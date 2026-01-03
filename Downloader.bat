@echo off
setlocal
title Music Downloader Launcher
color 0B

REM =====================================================
REM      MUSIC DOWNLOADER - AUTO INSTALLER
REM =====================================================
cls
echo.
echo  Step 1 of 5 - Checking System

REM 1. CHECK PYTHON
python --version >nul 2>&1
if errorlevel 1 (
    color 0C
    echo.
    echo  CRITICAL ERROR: Python is NOT installed
    echo.
    echo  1. Go to https://www.python.org/downloads/
    echo  2. Download Python
    echo  3. CHECK "Add Python to PATH" during install
    echo.
    pause
    exit /b
)

REM 2. SETUP VIRTUAL ENVIRONMENT
if not exist ".venv" (
    echo.
    echo  Step 2 of 5 - Creating Virtual Environment
    python -m venv .venv
)

REM ACTIVATE VENV
call ".venv\Scripts\activate.bat"

REM 3. GENERATE REQUIREMENTS
if not exist "requirements.txt" (
    echo.
    echo  Step 3 of 5 - Generating requirements.txt
    echo yt-dlp>requirements.txt
    echo requests>>requirements.txt
    echo spotipy>>requirements.txt
    echo mutagen>>requirements.txt
    echo rich>>requirements.txt
    echo selenium>>requirements.txt
    echo webdriver-manager>>requirements.txt
)

REM 4. INSTALL DEPENDENCIES
echo.
echo  Step 4 of 5 - Installing dependencies
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet --disable-pip-version-check

REM 5. CHECK FFMPEG
echo.
echo  Step 5 of 5 - Checking FFmpeg
ffmpeg -version >nul 2>&1
if errorlevel 1 (
    echo.
    echo  WARNING: FFmpeg not found
    echo  Attempting auto-install via Winget
    echo.
    winget install Gyan.FFmpeg

    if errorlevel 1 (
        color 0C
        echo.
        echo  ERROR: FFmpeg auto-install failed
        echo  Download manually from:
        echo  https://www.gyan.dev/ffmpeg/builds/
        echo.
        pause
        exit /b
    ) else (
        echo.
        echo  FFmpeg installed successfully
        echo  PLEASE restart this script once
        pause
        exit /b
    )
)

REM LAUNCH PROGRAM
cls
echo.
echo  Starting Music Downloader...
echo.
python main.py

if errorlevel 1 (
    color 0C
    echo.
    echo  APPLICATION CRASHED
    echo  Check error output above
    pause
)

pause
