import ev3_dc as ev3

print("Testing EV3 USB connection...")

try:
    with ev3.EV3(protocol=ev3.USB) as brick:
        print("Successfully connected!")
        print(brick)

except Exception as exc:
    print("EV3 connection failed:")
    print(type(exc).__name__, exc)