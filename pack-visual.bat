@echo off
REM ============================================================
REM  pack-visual.bat  -  Draw a file INTO the picture of a video
REM
REM  Use this one when the video will go through a host that
REM  re-encodes (vidmoly and friends). Slower and much bigger than
REM  pack.bat / pack-mp4.bat, but the payload survives transcoding.
REM
REM  Usage: drag any file onto this .bat icon.
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

REM This tool needs numpy, and ffmpeg to build the video.
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
    echo Drag any file onto this icon to hide it inside a video.
    echo.
    set /p "SRC=Or type the path to the file here: "
) else (
    set "SRC=%~1"
)

if "%SRC%"=="" (
    echo No file provided.
    echo.
    pause
    exit /b 1
)

echo.
echo This can take a while, and the video will be roughly 10x the
echo size of your file. Leave the window open until it says [OK].
echo.
python "%~dp0zipvisual.py" pack "%SRC%"
echo.
pause
endlocal
