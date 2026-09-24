from enum import Enum
from threading import Event, Lock, Thread
from time import monotonic, sleep

import ev3_dc as ev3
from flask import Flask, request, send_from_directory


app = Flask(__name__)


class State(Enum):
    UP = 1
    DOWN = 2
    LEFT = 3
    RIGHT = 4
    IDLE = 5


class Robot:
    def __init__(self):

        self.is_hand_open: bool = False

        self.reflection_upper_limit = 20
        self.reflection_lower_limit = 10
        self.calibration_delay = 1

        self.up_down_speed = 30
        self.speed = 30

        self.angle_limit = 320

        self.close_angle = 0
        self.open_angle = 30
        self.hand_speed = 65
        self.hand_angle = 6

        self.position_kp = 2.0
        self.position_max_speed = 50
        self.position_tolerance = 2

        self.control_period = 0.05

        self.state: State = State.IDLE
        self._last_state: State = State.IDLE

        self.up_down_target: float | None = None

        self.lock = Lock()
        self.stop_event = Event()

        self._left_right_command = None
        self._up_down_command = None

        self.main = ev3.EV3(protocol=ev3.USB)

        self.hand = ev3.Motor(
            ev3.PORT_D,
            ev3_obj=self.main,
        )

        self.touch = ev3.Touch(
            ev3.PORT_1,
            ev3_obj=self.main,
        )

        self.up_down = ev3.Motor(
            ev3.PORT_A,
            ev3_obj=self.main,
        )

        self.left_right = ev3.Motor(
            ev3.PORT_C,
            ev3_obj=self.main,
        )

        self.color = ev3.Color(
            ev3.PORT_3,
            ev3_obj=self.main,
        )

        self.controller_thread = Thread(
            target=self._control_loop,
            name="ev3-controller",
            daemon=True,
        )

    def start(self):
        self.stop_event.clear()

        if not self.controller_thread.is_alive():
            self.controller_thread = Thread(
                target=self._control_loop,
                name="ev3-controller",
                daemon=True,
            )

            self.controller_thread.start()

    def shutdown(self):
        self.stop_event.set()

        if self.controller_thread.is_alive():
            self.controller_thread.join(timeout=1.0)

        try:
            self._stop_left_right(brake=True)
        except Exception as exc:
            print(f"Failed to stop left/right motor: {exc}")

        try:
            self._stop_up_down(brake=True)
        except Exception as exc:
            print(f"Failed to stop up/down motor: {exc}")

        try:
            self.hand.stop()
        except Exception as exc:
            print(f"Failed to stop hand motor: {exc}")

    def _control_loop(self):
        """
        Main controller loop.

        Only this thread controls the movement motors.
        Flask threads only change self.state.
        """

        while not self.stop_event.is_set():
            cycle_start = monotonic()

            try:
                self._update_controller()

            except Exception as exc:
                print(f"Controller error: {exc}")

                try:
                    self._stop_left_right(brake=True)
                except Exception as stop_exc:
                    print(f"Failed to stop left/right motor: {stop_exc}")

                try:
                    self._stop_up_down(brake=True)
                except Exception as stop_exc:
                    print(f"Failed to stop up/down motor: {stop_exc}")

                self.stop_event.wait(0.1)
                continue

            elapsed = monotonic() - cycle_start
            remaining = self.control_period - elapsed

            if remaining > 0:
                self.stop_event.wait(remaining)

    def _update_controller(self):
        with self.lock:
            state = self.state

        if state != self._last_state:
            self._handle_state_change(
                self._last_state,
                state,
            )

            self._last_state = state

        match state:
            case State.IDLE:
                self._stop_left_right(brake=True)
                self._hold_up_down_position()

            case State.RIGHT:
                self._stop_up_down(brake=True)
                self._move_left()

            case State.LEFT:
                self._stop_up_down(brake=True)
                self._move_right()

            case State.UP:
                self._stop_left_right(brake=True)
                self._move_up()

            case State.DOWN:
                self._stop_left_right(brake=True)
                self._move_down()

    def _handle_state_change(self, old_state, new_state):
        """
        Handle actions that should happen exactly once when changing
        between states.
        """

        if new_state == State.IDLE:
            self._stop_left_right(brake=True)

            if old_state in (State.UP, State.DOWN):
                self._stop_up_down(brake=True)
                self._capture_up_down_target()

        elif new_state in (State.LEFT, State.RIGHT):
            self._stop_up_down(brake=True)

        elif new_state in (State.UP, State.DOWN):
            self._stop_left_right(brake=True)

    def _move_left(self):
        if self.get_position() <= -self.angle_limit:
            with self.lock:
                self.state = State.IDLE

            self._stop_left_right(brake=True)
            return

        self._set_left_right(
            direction=-1,
            speed=self.speed,
        )

    def _move_right(self):
        if self.is_touching():
            with self.lock:
                self.state = State.IDLE

            self._stop_left_right(brake=True)
            return

        self._set_left_right(
            direction=1,
            speed=self.speed,
        )

    def _move_up(self):
        if self.get_reflection() >= self.reflection_upper_limit:
            with self.lock:
                self.state = State.IDLE

            self._stop_up_down(brake=True)
            self._capture_up_down_target()

            return

        self._set_up_down(
            direction=-1,
            speed=self.up_down_speed,
        )

    def _move_down(self):
        self._set_up_down(
            direction=1,
            speed=self.up_down_speed,
        )

    def _set_left_right(self, direction, speed):
        """
        Start left/right movement only when the command changes.
        """

        command = (direction, speed)

        if command == self._left_right_command:
            return

        self.left_right.start_move(
            speed=speed,
            direction=direction,
        )

        self._left_right_command = command

    def _stop_left_right(self, brake=False):
        """
        Stop the left/right motor if it is currently commanded to move.
        """

        if self._left_right_command is None:
            return

        self.left_right.stop(brake=brake)
        self._left_right_command = None

    def _set_up_down(self, direction, speed):
        """
        Start up/down movement only when the command changes.
        """

        speed = int(round(speed))

        speed = max(1, min(100, speed))

        command = (direction, speed)

        if command == self._up_down_command:
            return

        self.up_down.start_move(
            speed=speed,
            direction=direction,
        )

        self._up_down_command = command

    def _stop_up_down(self, brake=False):
        """
        Stop the up/down motor if it is currently commanded to move.
        """

        if self._up_down_command is None:
            return

        self.up_down.stop(brake=brake)
        self._up_down_command = None

    def _capture_up_down_target(self):
        """
        Capture the current position as the position that the arm
        should hold.
        """

        self.up_down_target = self.up_down.position

        print(f"Up/down target position: {self.up_down_target}")

    def _hold_up_down_position(self):
        """
        Hold the up/down arm at the last captured position using
        a proportional controller.
        """

        if self.up_down_target is None:
            self._stop_up_down(brake=True)
            return

        current_position = self.up_down.position

        error = self.up_down_target - current_position

        command = self.position_kp * error

        command = max(
            -self.position_max_speed,
            min(
                self.position_max_speed,
                command,
            ),
        )

        if abs(error) <= self.position_tolerance:
            command = 0

        if command == 0:
            self._stop_up_down(brake=True)
            return

        direction = 1 if command > 0 else -1

        speed = int(round(abs(command)))

        speed = max(
            1,
            min(
                self.position_max_speed,
                speed,
            ),
        )

        self._set_up_down(
            direction=direction,
            speed=speed,
        )

    def set_state(self, state: State):
        """
        Change the requested state.

        This function does not directly control the motors.
        The controller thread handles the actual motor commands.
        """

        with self.lock:
            self.state = state

    def use_hand(self):
        if self.is_hand_open:
            self.hand.move_to(
                self.close_angle,
                speed=self.hand_speed,
            ).start()
        else:
            self.hand.move_to(
                self.open_angle,
                speed=self.hand_speed,
            ).start()

        self.is_hand_open = not self.is_hand_open

    def calibrate(self):
        """
        Move left/right until the touch sensor is pressed, then
        establish that position as zero.
        """

        print("Calibrating...")

        self.left_right.start_move(
            speed=self.speed,
            direction=1,
        )

        try:
            while not self.touch.touched:
                sleep(0.01)

        finally:
            self.left_right.stop(brake=True)

        sleep(self.calibration_delay)

        self.left_right.position = 0

        self._left_right_command = None

        print("Calibration complete.")

    def is_touching(self):
        return self.touch.touched

    def get_position(self):
        return self.left_right.position

    def get_reflection(self):
        return self.color.reflected

    def stop(self):
        """
        Immediately stop the movement motors and return to IDLE.
        """

        self._stop_left_right(brake=True)
        self._stop_up_down(brake=True)

        with self.lock:
            self.state = State.IDLE

    def debug(self):
        print(f"Current battery: {self.main.battery}%")

        print(f"Left/right position: {self.left_right.position}")

        print(f"Left/right speed: {self.left_right.speed}%")

        print(f"Up/down position: {self.up_down.position}")

        print(f"Up/down speed: {self.up_down.speed}%")

        print(f"Up/down target: {self.up_down_target}")

        print(f"Intensity: {self.color.reflected}%")

        with self.lock:
            print(f"State: {self.state}")


@app.route("/")
def index():
    return send_from_directory(".", "index.html")


@app.route("/key", methods=["POST"])
def key_event():
    data = request.get_json()

    key = data.get("key")
    pressed = data.get("pressed")

    if pressed:
        match key:
            case "p":
                ROBOT.set_state(State.IDLE)
                ROBOT.debug()

            case "ArrowLeft":
                ROBOT.set_state(State.LEFT)

            case "ArrowRight":
                ROBOT.set_state(State.RIGHT)

            case "ArrowUp":
                ROBOT.set_state(State.UP)

            case "ArrowDown":
                ROBOT.set_state(State.DOWN)

            case " ":
                ROBOT.use_hand()

    else:
        ROBOT.set_state(State.IDLE)

    return {"ok": True}


ROBOT = Robot()


if __name__ == "__main__":
    try:
        ROBOT.calibrate()

        ROBOT.start()

        app.run(
            host="127.0.0.1",
            port=3000,
            threaded=True,
        )

    finally:
        ROBOT.shutdown()
