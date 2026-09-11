@echo off
setlocal enabledelayedexpansion

cd /d "%~dp0"

set "ACTION=%~1"

if /i "%ACTION%"=="setup" goto :run_setup
if /i "%ACTION%"=="scraper" goto :run_scraper
if /i "%ACTION%"=="scrape" goto :run_scraper
if /i "%ACTION%"=="crm" goto :run_crm
if /i "%ACTION%"=="dashboard" goto :run_crm
if /i "%ACTION%"=="app" goto :run_crm
if /i "%ACTION%"=="outreach" goto :run_scraper
if /i "%ACTION%"=="worker" goto :run_scraper
if /i "%ACTION%"=="install" goto :reinstall_deps
if /i "%ACTION%"=="reinstall" goto :reinstall_deps
if /i "%ACTION%"=="autostart" goto :autostart_on
if /i "%ACTION%"=="autostart-off" goto :autostart_off
if not "%ACTION%"=="" goto :usage

:menu
call :ensure_venv
if errorlevel 1 exit /b 1
echo.
echo ==================================================
echo      FB Marketplace Scraper ^& CRM Runner (Windows)
echo ==================================================
echo 1) Log in Facebook Accounts (setup_sessions.py)
echo 2) Run Scraper + Auto-Messaging, all accounts (continuous_scraper.py)
echo 3) Launch Streamlit CRM Dashboard (app.py)
echo 4) Turn ON auto-start at login (scraper starts by itself)
echo 5) Turn OFF auto-start
echo 6) Reinstall / Update Dependencies
echo 7) Exit
echo ==================================================
set /p "CHOICE=Select an option [1-7]: "

if "%CHOICE%"=="1" goto :run_setup
if "%CHOICE%"=="2" goto :run_scraper
if "%CHOICE%"=="3" goto :run_crm
if "%CHOICE%"=="4" goto :autostart_on
if "%CHOICE%"=="5" goto :autostart_off
if "%CHOICE%"=="6" goto :reinstall_deps
if "%CHOICE%"=="7" exit /b 0

echo Invalid choice. Please select between 1 and 7.
echo.
goto :menu

:ensure_venv
if not exist "venv\Scripts\activate.bat" (
    echo [INFO] Virtual environment not found or broken. Creating fresh venv...
    python -m venv venv 2>nul
    if errorlevel 1 (
        echo [INFO] 'python' command failed, trying 'py -3'...
        py -3 -m venv venv
        if errorlevel 1 (
            echo [ERROR] Python not found or failed to create venv. Please ensure Python 3.10-3.13 is installed and added to PATH.
            pause
            exit /b 1
        )
    )
    echo [INFO] Upgrading pip...
    call venv\Scripts\python.exe -m pip install --upgrade pip
)
rem Install requirements on first run and again whenever requirements.txt changes
fc /b requirements.txt venv\.requirements.installed >nul 2>&1
if errorlevel 1 (
    echo [INFO] Installing requirements from requirements.txt...
    call venv\Scripts\python.exe -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [ERROR] Failed to install requirements. Check your internet connection and try again.
        pause
        exit /b 1
    )
    echo [INFO] Installing Playwright Chromium browser...
    call venv\Scripts\python.exe -m playwright install chromium
    copy /y requirements.txt venv\.requirements.installed >nul
    echo [SUCCESS] Windows environment setup complete!
)
exit /b 0

:run_setup
call :ensure_venv
if errorlevel 1 exit /b 1
echo [INFO] Starting Facebook account login session setup...
call venv\Scripts\python.exe setup_sessions.py
pause
exit /b 0

:run_scraper
call :ensure_venv
if errorlevel 1 exit /b 1
echo [INFO] Starting scraper + auto-messaging. It restarts by itself after a crash - close this window to stop it.
:scraper_loop
call venv\Scripts\python.exe continuous_scraper.py
if errorlevel 1 (
    echo [WARN] Scraper crashed - restarting in 60 seconds...
    ping -n 61 127.0.0.1 >nul
    goto :scraper_loop
)
pause
exit /b 0

:autostart_on
set "STARTUP_FILE=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FB Deal Hunter.cmd"
> "%STARTUP_FILE%" echo @echo off
>> "%STARTUP_FILE%" echo ping -n 61 127.0.0.1 ^>nul
>> "%STARTUP_FILE%" echo start "FB Deal Hunter" "%~dp0run.bat" scraper
if exist "%STARTUP_FILE%" (
    echo [SUCCESS] Auto-start is ON: the scraper starts by itself 1 minute after you log in to Windows.
) else (
    echo [ERROR] Could not create the auto-start entry.
)
pause
exit /b 0

:autostart_off
set "STARTUP_FILE=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\FB Deal Hunter.cmd"
if exist "%STARTUP_FILE%" del "%STARTUP_FILE%"
echo [SUCCESS] Auto-start is OFF.
pause
exit /b 0

:run_crm
call :ensure_venv
if errorlevel 1 exit /b 1
echo [INFO] Launching Streamlit CRM Dashboard...
call venv\Scripts\python.exe -m streamlit run app.py
pause
exit /b 0

:reinstall_deps
echo [INFO] Removing existing venv and reinstalling dependencies...
if exist "venv" rmdir /s /q venv
call :ensure_venv
if errorlevel 1 exit /b 1
echo [SUCCESS] Reinstallation complete!
pause
exit /b 0

:usage
echo Usage: run.bat [setup^|scraper^|crm^|autostart^|autostart-off^|install]
pause
exit /b 1
