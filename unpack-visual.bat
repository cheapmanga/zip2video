@echo off
REM ============================================================
REM  unpack-visual.bat  -  Recover a file from the picture of a video
REM
REM  Usage: drag the video onto this .bat icon. It works on the copy
REM  you downloaded back from the host, even after re-encoding.
REM  The block grid is detected automatically.
REM  (Must stay in the same folder as zipvisual.py)
REM ============================================================
setlocal

cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python is not installed or not in PATH.
    echo Install it from the Microsoft Store or python.org
    echo and tick "Add Python to PATH".
    echo.
    pause
    exit /b 1
)

python -c "import numpy" >nul 2>nul
if errorlevel 1 (
    echo [ERROR] numpy is missing. Install the requirements with:
    echo.
    echo     pip install numpy imageio-ffmpeg
    echo.
    pause
    exit /b 1
)
python -c "import shutil,sys; sys.exit(0 if shutil.which('ffmpeg') else 1)" >nul 2>nul
if errorlevel 1 (
    python -c "import imageio_ffmpeg" >nul 2>nul
    if errorlevel 1 (
        echo [ERROR] ffmpeg not found. Easiest fix:
        echo.
        echo     pip install imageio-ffmpeg
        echo.
        pause
        exit /b 1
    )
)

if "%~1"=="" (
    echo Drag the video onto this icon to recover the file from it.
    echo.
    set /p "VID=Or type the path to the video here: "
) else (
    set "VID=%~1"
)

if "%VID%"=="" (
    echo No file provided.
    echo.
    pause
    exit /b 1
)

echo.
python "%~dp0zipvisual.py" unpack "%VID%"
echo.
pause
endlocal
