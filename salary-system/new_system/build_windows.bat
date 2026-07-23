@echo off
REM ===================================================================
REM  One-click Windows build for APEX Payroll (Admin + Operator).
REM
REM  Prerequisite (one time, on THIS build machine only):
REM    Install Python 3.11 or newer from https://www.python.org/downloads/
REM    -> on the first installer screen, TICK "Add python.exe to PATH".
REM
REM  Then just double-click this file. The two finished .exe apps appear
REM  in the "dist" folder. The laptops you copy them to need NOTHING
REM  installed.
REM ===================================================================
setlocal
cd /d "%~dp0"

echo.
echo === [1/3] Creating an isolated build environment ===
py -3 -m venv build-venv 2>nul || python -m venv build-venv || goto :fail
call "build-venv\Scripts\activate.bat" || goto :fail

echo.
echo === [2/3] Installing build dependencies (first run downloads a bit) ===
python -m pip install --upgrade pip || goto :fail
pip install -r requirements-build.txt || goto :fail

echo.
echo === [3/3] Building the apps (a few minutes) ===
pyinstaller --noconfirm apex_payroll.spec || goto :fail

echo.
echo ============================================================
echo  DONE. Your apps are in the "dist" folder:
echo     dist\APEX Payroll (Admin).exe       ^<- give to the CEO
echo     dist\APEX Payroll (Operator).exe    ^<- give to the operator
echo ============================================================
echo.
pause
exit /b 0

:fail
echo.
echo *** BUILD FAILED — read the messages above. ***
echo.
pause
exit /b 1
