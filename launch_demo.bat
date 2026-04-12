@echo off
setlocal

cd /d "%~dp0"

echo ===============================================
echo  Satellite Turbine Connection - Demo Launcher
echo ===============================================
echo.

where py >nul 2>nul
if %ERRORLEVEL%==0 (
    set "PYTHON_CMD=py -3"
) else (
    set "PYTHON_CMD=python"
)

%PYTHON_CMD% --version >nul 2>nul
if not %ERRORLEVEL%==0 (
    echo Python 3 was not found. Please install Python 3 and try again.
    pause
    exit /b 1
)

set "SATELLITE_HOST=127.0.0.1"
set "FARM_TURBINES=TURBINE-01,TURBINE-02,TURBINE-03"

echo Starting satellite relay...
start "Satellite Relay" cmd /k "%PYTHON_CMD% satellite\satellite.py"
timeout /t 2 /nobreak >nul

echo Starting turbine nodes...
start "Turbine TURBINE-01" cmd /k "%PYTHON_CMD% turbine\turbine.py TURBINE-01"
timeout /t 1 /nobreak >nul
start "Turbine TURBINE-02" cmd /k "%PYTHON_CMD% turbine\turbine.py TURBINE-02"
timeout /t 1 /nobreak >nul
start "Turbine TURBINE-03" cmd /k "%PYTHON_CMD% turbine\turbine.py TURBINE-03"
timeout /t 2 /nobreak >nul

echo Starting ground station...
start "Ground Station" cmd /k "%PYTHON_CMD% ground_station\ground_station.py"

echo.
echo Demo launched.
echo Use the Ground Station window and type: help
echo Useful commands: discover, status, ping, yaw TURBINE-01 200, stopall, resumeall
echo Close the opened command windows to stop the demo.
echo.
pause
