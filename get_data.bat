@echo off
rem Windows: double-click this file to get data without typing commands. It asks the
rem same questions as "python get_data.py" (see README.md). The first time, it installs
rem the Python packages the code needs into a folder named .venv beside it, which takes
rem a minute or two. Python 3.12 or later must be installed: https://www.python.org
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" goto run

echo Setting up the Python packages for the first run. This takes a minute or two ...
py -3 -m venv .venv 2>nul || python -m venv .venv 2>nul
if not exist ".venv\Scripts\python.exe" goto nopython
".venv\Scripts\python.exe" -m pip install --quiet --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto nopackages

:run
".venv\Scripts\python.exe" get_data.py
echo.
pause
exit /b

:nopython
echo.
echo Python was not found. Install Python 3.12 or later from https://www.python.org/downloads/
echo and double-click this file again.
pause
exit /b 1

:nopackages
rmdir /s /q .venv
echo.
echo The Python packages could not be installed. They need Python 3.12 or later and the
echo internet. Fix that and double-click this file again.
pause
exit /b 1
