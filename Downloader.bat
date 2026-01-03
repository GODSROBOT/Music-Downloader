@echo off
setlocal
title Music Downloader Launcher
color 0B

:: =====================================================
::      MUSIC DOWNLOADER - AUTO INSTALLER
:: =====================================================
cls
echo.
echo  [1/5] Checking System...

:: 1. CHECK PYTHON
python --version >nul 2>&1
if %errorlevel% neq 0 (
    color 0C
    echo.
    echo  [CRITICAL ERROR] Python is NOT installed!
    echo.
    echo  1. Go to https://www.python.org/downloads/
    echo  2. Download Python.
    echo  3. IMPORTANT: Check the box "Add Python to PATH" during install.
    echo.
    pause
    exit
)

:: 2. SETUP VIRTUAL ENVIRONMENT (Isolates installations)
if not exist ".venv" (
    echo  [2/5] Creating Virtual Environment (First run only)...
    python -m venv .venv
)

:: Activate the environment
call .venv\Scripts\activate

:: 3. GENERATE REQUIREMENTS (If missing)
if not exist "requirements.txt" (
    echo  [3/5] Generating requirements.txt...
    (
        echo yt-dlp
        echo requests
        echo spotipy
        echo mutagen
        echo rich
        echo selenium
        echo webdriver-manager
    ) > requirements.txt
)

:: 4. INSTALL DEPENDENCIES
echo  [4/5] Updating Libraries (This may take a moment)...
python -m pip install --upgrade pip --quiet
pip install -r requirements.txt --quiet --disable-pip-version-check

:: 5. CHECK & INSTALL FFMPEG
echo  [5/5] Checking FFmpeg...
ffmpeg -version >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo  [WARNING] FFmpeg is missing. Attempting Auto-Install via Winget...
    echo.
    winget install Gyan.FFmpeg
    
    if %errorlevel% neq 0 (
        color 0C
        echo.
        echo  [ERROR] Auto-install failed. You must install FFmpeg manually.
        echo  Download: https://www.gyan.dev/ffmpeg/builds/ffmpeg-git-essentials.7z
        echo  Extract and add 'bin' folder to System PATH.
        pause
        exit
    ) else (
        echo.
        echo  [SUCCESS] FFmpeg installed! Please RESTART this script to apply changes.
        pause
        exit
    )
)

:: 6. LAUNCH SCRIPT
cls
echo.
echo  Starting Music Downloader...
echo.
python main.py

:: Keep window open if crash
if %errorlevel% neq 0 (
    color 0C
    echo.
    echo  [CRASH] The script crashed. See error above.
    pause
)
pause