@echo off
REM ============================================================
REM Scheduled Scrape v2 — Scrape + Claude CLI Eval
REM ============================================================
REM
REM Step 1: Deterministic scrape (no LLM, ~18 min)
REM Step 2: Claude CLI eval session (~30 sec, ~$0.05-0.20)
REM Fallback: If claude fails, send a notification email
REM
REM IMPORTANT: PC must be on AC power for wake-from-sleep.
REM Task Scheduler -> Conditions -> "Wake the computer to run this task"
REM ============================================================

REM Ensure log directory exists
if not exist "%USERPROFILE%\.searchaero\logs" mkdir "%USERPROFILE%\.searchaero\logs"

set LOGFILE=%USERPROFILE%\.searchaero\logs\task_scheduler.log
set PYTHON=C:\Users\jiami\local_workspace\searchaero\scripts\experiments\.venv\Scripts\python.exe
set PROJECT=C:\Users\jiami\local_workspace\seataero-src
set CLAUDE=C:\Users\jiami\AppData\Roaming\npm\claude.cmd

cd /d %PROJECT%

echo [%date% %time%] === Scheduled scrape v2 starting === >> "%LOGFILE%"

REM --- Step 1: Deterministic scrape with --no-eval ---
echo [%date% %time%] Step 1: Running scrape (--no-eval) >> "%LOGFILE%"
%PYTHON% scripts\scheduled_scrape.py --no-eval >> "%LOGFILE%" 2>&1
set SCRAPE_EXIT=%ERRORLEVEL%

if %SCRAPE_EXIT% NEQ 0 (
    echo [%date% %time%] Scrape failed with exit code %SCRAPE_EXIT% >> "%LOGFILE%"
    goto :EOF
)

echo [%date% %time%] Scrape completed successfully >> "%LOGFILE%"

REM --- Step 2: Claude CLI eval ---
REM Check if claude is available
where /q "%CLAUDE%" >nul 2>&1
if %ERRORLEVEL% NEQ 0 (
    echo [%date% %time%] claude not found, sending fallback notification >> "%LOGFILE%"
    goto :EVAL_FAILED
)

REM Check if watches.yaml exists
if not exist "%USERPROFILE%\.searchaero\watches.yaml" (
    echo [%date% %time%] No watches.yaml found, skipping eval >> "%LOGFILE%"
    goto :EOF
)

echo [%date% %time%] Step 2: Running claude -p eval >> "%LOGFILE%"

REM Read the prompt from file
set "PROMPT_FILE=%PROJECT%\scripts\eval_prompt.txt"

REM Run claude with the prompt file piped in
%CLAUDE% -p --verbose 0 < "%PROMPT_FILE%" >> "%LOGFILE%" 2>&1
set CLAUDE_EXIT=%ERRORLEVEL%

if %CLAUDE_EXIT% NEQ 0 (
    echo [%date% %time%] claude exited %CLAUDE_EXIT%, sending fallback notification >> "%LOGFILE%"
    goto :EVAL_FAILED
)

echo [%date% %time%] Claude eval completed successfully >> "%LOGFILE%"

REM Append eval_method to the latest log entry
%PYTHON% -c "import json, os; f=os.path.join(os.path.expanduser('~'),'.searchaero','logs','scheduled_scrape.jsonl'); lines=open(f).readlines(); last=json.loads(lines[-1]); last['eval_method']='claude_cli'; lines[-1]=json.dumps(last,default=str)+'\n'; open(f,'w').writelines(lines)" >> "%LOGFILE%" 2>&1

goto :EOF

:EVAL_FAILED
echo [%date% %time%] Sending eval-failed Discord notification >> "%LOGFILE%"
%PYTHON% -c "import sys; sys.path.insert(0, r'C:\Users\jiami\local_workspace\seataero-src'); from core.notify import load_notify_config, send_discord; c = load_notify_config(); send_discord(c.get('discord_webhook_url',''), content='**[searchaero] Claude eval session failed**\nThe scheduled scrape completed successfully, but the Claude CLI eval session failed or was unavailable.\n\nData has been saved to the database. Check watches manually.')" >> "%LOGFILE%" 2>&1

REM Update JSONL log with eval_method
%PYTHON% -c "import json, os; f=os.path.join(os.path.expanduser('~'),'.searchaero','logs','scheduled_scrape.jsonl'); lines=open(f).readlines(); last=json.loads(lines[-1]); last['eval_method']='failed'; lines[-1]=json.dumps(last,default=str)+'\n'; open(f,'w').writelines(lines)" >> "%LOGFILE%" 2>&1

echo [%date% %time%] Fallback Discord notification sent >> "%LOGFILE%"
goto :EOF
