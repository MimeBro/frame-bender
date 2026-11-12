@echo off
rem Starts the skin editor in your browser. Nothing is installed or downloaded by this file.
cd /d "%~dp0"
python -c "import flask, PIL" 2>nul
if errorlevel 1 (
  echo.
  echo Missing Python packages: flask and/or Pillow.
  echo Install them once with:
  echo     python -m pip install -r requirements.txt
  echo then run this file again.
  echo.
  pause
  exit /b 1
)
python launcher.py --browser
pause
