@echo off
REM ============================================================
REM  launch.bat  –  Windows RC Sender Launcher
REM
REM  Starts pc_rc_sender.py on Windows to read Flysky iBUS
REM  over a COM port and forward it via UDP to the Raspberry Pi.
REM
REM  USAGE (from this folder in Command Prompt or PowerShell):
REM    launch.bat
REM  or with explicit port:
REM    launch.bat COM3
REM
REM  REQUIREMENTS:
REM    python --version   (Python 3.9+)
REM    pip install -r requirements.txt
REM ============================================================

setlocal

REM ── Configuration ────────────────────────────────────────────
set PI_IP=192.168.50.2
set PI_PORT=5000
set HZ=50
set PRINT_EVERY=20

REM Allow overriding serial port as first argument
set SERIAL_PORT=%~1

REM ── Check Python ─────────────────────────────────────────────
python --version >nul 2>&1
if errorlevel 1 (
    echo [FAIL] Python not found. Install Python 3.9+ from https://www.python.org/downloads/
    pause
    exit /b 1
)

REM ── Install dependencies if needed ───────────────────────────
echo [INFO] Checking Python dependencies...
python -c "import flask, serial, cv2" >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing required packages...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [FAIL] pip install failed. Run manually: pip install -r requirements.txt
        pause
        exit /b 1
    )
)
echo [OK]   Dependencies ready.

REM ── Start RC Sender ──────────────────────────────────────────
echo.
echo ============================================================
echo   ROVER RC SENDER – Windows Launcher
echo ============================================================
echo   Pi target : %PI_IP%:%PI_PORT%
echo   Send rate : %HZ% Hz
if not "%SERIAL_PORT%"=="" (
    echo   Serial    : %SERIAL_PORT%  (explicit)
) else (
    echo   Serial    : auto-detect
)
echo ============================================================
echo   Press Ctrl+C to stop.
echo.

if not "%SERIAL_PORT%"=="" (
    python pc_rc_sender.py ^
        --serial-port %SERIAL_PORT% ^
        --pi-ip %PI_IP% ^
        --pi-port %PI_PORT% ^
        --hz %HZ% ^
        --print-every %PRINT_EVERY%
) else (
    python pc_rc_sender.py ^
        --pi-ip %PI_IP% ^
        --pi-port %PI_PORT% ^
        --hz %HZ% ^
        --print-every %PRINT_EVERY%
)

if errorlevel 1 (
    echo.
    echo [FAIL] RC sender exited with an error.
    echo        Common fixes:
    echo          - Check CP2102 USB adapter is plugged in
    echo          - Install CP2102 driver from Silicon Labs website
    echo          - Run:  python pc_rc_sender.py --serial-port COM3 --pi-ip %PI_IP%
    pause
)

endlocal
