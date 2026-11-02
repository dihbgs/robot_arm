@echo off
setlocal
cd /d "%~dp0"

title LEGO Robot Arm Setup

echo ========================
echo LEGO ROBOT ARM INSTALLER
echo ========================

echo.
echo Checking Python...

where py >nul 2>&1

if errorlevel 1 (
    echo Python is missing.
    echo Installing Python 3.14...

    winget install --id Python.Python.3.14 -e --scope user --accept-package-agreements --accept-source-agreements

    if errorlevel 1 (
        echo Python installation failed.
        echo Please install Python manually.
        pause
        exit /b 1
    )
)

echo.
echo Creating virtual environment...

if not exist ".venv" (
    py -3.14 -m venv .venv
)

if errorlevel 1 (
    echo Could not create virtual environment.
    pause
    exit /b 1
)

echo.
echo Installing dependencies...

".venv\Scripts\python.exe" -m pip install --upgrade pip
".venv\Scripts\python.exe" -m pip install -r requirements.txt

if errorlevel 1 (
    echo Dependency installation failed.
    pause
    exit /b 1
)

echo.
echo Verifying libusb backend...

".venv\Scripts\python.exe" -c "import usb.core; import usb.backend.libusb1; import libusb_package; backend = usb.backend.libusb1.get_backend(find_library=libusb_package.find_library); print('SUCCESS: libusb backend loaded!' if backend else 'ERROR: libusb backend not found.'); exit(0 if backend else 1)"

if errorlevel 1 (
    echo.
    echo Installation verification failed.
    pause
    exit /b 1
)

echo.
echo Installation complete!
echo Stating controller...

".venv\Scripts\python.exe" server.py

pause