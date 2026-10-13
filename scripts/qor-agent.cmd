@echo off
REM qor-receipt capture agent launcher (Windows).
REM Usage: qor-agent.cmd REFEREE_URL [extra args]   e.g. qor-agent.cmd http://192.168.1.20:8000 --window
REM Capture card: DirectShow "USB3.0 Video" (640x360 scorebug reads). DualSense Edge: USB to this PC
REM (and Bluetooth to the PS5). First run creates a venv in %~dp0..\.venv-agent.
setlocal
set "ROOT=%~dp0.."
set "VENV=%ROOT%\.venv-agent"
if "%~1"=="" (
  echo usage: %~nx0 REFEREE_URL [--window] [--obs-text C:\path\prompt.txt] [--role home^|away]
  exit /b 2
)
if not exist "%VENV%\Scripts\python.exe" (
  py -3 -m venv "%VENV%" || exit /b 1
  "%VENV%\Scripts\python.exe" -m pip install -q --upgrade pip
  "%VENV%\Scripts\python.exe" -m pip install -q -e "%ROOT%[agent]" || exit /b 1
)
set "QOR_HID_ANALOG_HZ=125"
set "REF=%~1"
shift
"%VENV%\Scripts\qor-receipt.exe" agent --device "USB3.0 Video" --referee "%REF%" %1 %2 %3 %4 %5 %6 %7 %8
exit /b %ERRORLEVEL%
