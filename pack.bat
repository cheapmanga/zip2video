@echo off
REM ============================================================
REM  pack.bat  -  Wrap a .zip file into a .mkv
REM
REM  Usage: drag a .zip file onto this .bat icon.
REM  The .mkv is created next to the .zip, with the same name.
REM  (Must stay in the same folder as zip2mkv.py)
REM ============================================================
setlocal

REM Move into the folder of this .bat (where zip2mkv.py lives)
cd /d "%~dp0"

REM Check that Python is available
where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Install it from the Microsoft Store or python.org
    echo and tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

REM Was a file dropped onto the icon?
if "%~1"=="" (
    echo Drag a .zip file onto this icon to pack it.
    echo.
    set /p "ZIP=Or type the path to the .zip here: "
) else (
    set "ZIP=%~1"
)

if "%ZIP%"=="" (
    echo No file provided.
    echo.
    pause
    exit /b 1
)

echo.
python "%~dp0zip2mkv.py" pack "%ZIP%"
echo.
pause
endlocal
