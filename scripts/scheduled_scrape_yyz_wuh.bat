@echo off
REM ============================================================
REM YYZ-WUH Scheduled Scrape — June, July, December only
REM Every 45 minutes — 3 API calls per run
REM ============================================================

if not exist "%USERPROFILE%\.searchaero\logs" mkdir "%USERPROFILE%\.searchaero\logs"

set LOGFILE=%USERPROFILE%\.searchaero\logs\task_scheduler.log
set PYTHON=C:\Users\jiami\local_workspace\seataero-src\.venv\Scripts\python.exe
set PROJECT=C:\Users\jiami\local_workspace\seataero-src

cd /d %PROJECT%

echo [%date% %time%] === YYZ-WUH scrape starting (months 6,7,12) === >> "%LOGFILE%"

%PYTHON% scripts\scheduled_scrape.py --routes routes\yyz_wuh.txt --no-eval --months 6,7,12 >> "%LOGFILE%" 2>&1
set SCRAPE_EXIT=%ERRORLEVEL%

if %SCRAPE_EXIT% NEQ 0 (
    echo [%date% %time%] YYZ-WUH scrape failed with exit code %SCRAPE_EXIT% >> "%LOGFILE%"
) else (
    echo [%date% %time%] YYZ-WUH scrape completed successfully >> "%LOGFILE%"
)
