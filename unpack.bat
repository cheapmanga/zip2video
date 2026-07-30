@echo off
REM ============================================================
REM  unpack.bat  -  Extract the .zip contained in a .mkv or a .mp4
REM
REM  Usage: drag a .mkv or .mp4 file onto this .bat icon.
REM  The container is detected automatically.
REM  The .zip is recreated next to the video.
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
    echo Drag a .mkv or .mp4 file onto this icon to unpack it.
    echo.
    set /p "MKV=Or type the path to the video here: "
) else (
    set "MKV=%~1"
)

if "%MKV%"=="" (
    echo No file provided.
    echo.
    pause
    exit /b 1
)

echo.
python "%~dp0zip2mkv.py" unpack "%MKV%"
echo.
pause
endlocal
