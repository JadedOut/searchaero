"""Windows Task Scheduler management for searchaero scheduled scrapes.

Provides functions to:
- Manage a local schedules registry (~/.searchaero/schedules/schedules.json)
- Generate .bat launcher scripts for scheduled_scrape.py
- Register/delete/query Windows Task Scheduler tasks via schtasks
- Check and configure wake timers via powercfg
"""

import json
import math
import os
import subprocess
import sys
import tempfile
from datetime import datetime
from xml.etree import ElementTree

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

SCHEDULES_DIR = os.path.join(os.path.expanduser("~"), ".searchaero", "schedules")
SCHEDULES_FILE = os.path.join(SCHEDULES_DIR, "schedules.json")
MIN_INTERVAL_MINUTES = 60
MAX_ROUTES = 10
BUFFER_MINUTES = 45
LOGIN_OVERHEAD_MINUTES = 3
PER_ROUTE_MINUTES = 2

# ---------------------------------------------------------------------------
# XML namespace for schtasks export
# ---------------------------------------------------------------------------

_TASK_NS = "http://schemas.microsoft.com/windows/2004/02/mit/task"
ElementTree.register_namespace("", _TASK_NS)


# ---------------------------------------------------------------------------
# Platform guard
# ---------------------------------------------------------------------------

def require_windows():
    """Raise RuntimeError if not on Windows."""
    if sys.platform != "win32":
        raise RuntimeError(
            "Schedule management requires Windows (Task Scheduler). "
            "Not supported on this platform."
        )


# ---------------------------------------------------------------------------
# Route counting and timing estimation
# ---------------------------------------------------------------------------

def count_routes_in_file(routes_file: str) -> int:
    """Count non-empty, non-comment lines in a routes file.

    Lines starting with # are treated as comments.

    Args:
        routes_file: Absolute path to a routes file.

    Returns:
        int: Number of route lines.
    """
    count = 0
    with open(routes_file, "r", encoding="utf-8") as f:
        for line in f:
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                count += 1
    return count


def count_routes_in_files(routes_files: list[str]) -> int:
    """Count total routes across multiple route files.

    Args:
        routes_files: List of absolute paths to route files.

    Returns:
        int: Sum of route counts across all files.
    """
    return sum(count_routes_in_file(rf) for rf in routes_files)


def estimate_scrape_minutes(route_count: int, delay: float = 3.0) -> int:
    """Estimate how many minutes a scrape run will take.

    Formula: LOGIN_OVERHEAD_MINUTES + ceil(route_count * PER_ROUTE_MINUTES).

    Args:
        route_count: Number of routes to scrape.
        delay: Per-route delay in seconds (reserved for future use).

    Returns:
        int: Estimated minutes.
    """
    return LOGIN_OVERHEAD_MINUTES + math.ceil(route_count * PER_ROUTE_MINUTES)


def compute_min_interval(route_count: int, delay: float = 3.0,
                         buffer: int = BUFFER_MINUTES) -> int:
    """Compute the minimum schedule interval for a given route count.

    Returns estimate_scrape_minutes + buffer, rounded UP to the nearest
    15-minute increment. For example: 68 -> 75, 60 -> 60, 61 -> 75.

    Args:
        route_count: Number of routes to scrape.
        delay: Per-route delay in seconds (reserved for future use).
        buffer: Buffer minutes to add after estimated scrape time.

    Returns:
        int: Minimum interval in minutes (multiple of 15).
    """
    raw = estimate_scrape_minutes(route_count, delay) + buffer
    return math.ceil(raw / 15) * 15


# ---------------------------------------------------------------------------
# Schedules registry
# ---------------------------------------------------------------------------

def load_schedules():
    """Load schedules from the JSON registry.

    Returns:
        list[dict]: Schedule entries, or [] if file is missing/empty.
    """
    if not os.path.isfile(SCHEDULES_FILE):
        return []
    try:
        with open(SCHEDULES_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return data
        return []
    except (json.JSONDecodeError, OSError):
        return []


def _write_schedules(schedules):
    """Write the full schedules list to disk."""
    os.makedirs(SCHEDULES_DIR, exist_ok=True)
    with open(SCHEDULES_FILE, "w", encoding="utf-8") as f:
        json.dump(schedules, f, indent=2)


def save_schedule(entry):
    """Add or update a schedule entry (keyed by 'name').

    Args:
        entry: dict with at least a 'name' key.
    """
    schedules = load_schedules()
    # Replace existing entry with the same name, or append
    updated = False
    for i, s in enumerate(schedules):
        if s.get("name") == entry["name"]:
            schedules[i] = entry
            updated = True
            break
    if not updated:
        schedules.append(entry)
    _write_schedules(schedules)


def remove_schedule(name):
    """Remove a schedule entry by name.

    Args:
        name: Schedule name to remove.

    Returns:
        bool: True if an entry was found and removed.
    """
    schedules = load_schedules()
    original_len = len(schedules)
    schedules = [s for s in schedules if s.get("name") != name]
    if len(schedules) < original_len:
        _write_schedules(schedules)
        return True
    return False


# ---------------------------------------------------------------------------
# Route group helpers (consolidated schedule data model)
# ---------------------------------------------------------------------------

def add_route_group(schedule: dict, group: dict) -> dict:
    """Append a route group to a schedule's route_groups list.

    Args:
        schedule: Schedule dict (must have a 'route_groups' key).
        group: Route group dict with at least 'name' and 'routes_file'.

    Returns:
        dict: The updated schedule.
    """
    if "route_groups" not in schedule:
        schedule["route_groups"] = []
    schedule["route_groups"].append(group)
    return schedule


def remove_route_group(schedule: dict, group_name: str) -> dict:
    """Remove a route group by name from a schedule.

    Args:
        schedule: Schedule dict with a 'route_groups' list.
        group_name: Name of the group to remove.

    Returns:
        dict: The updated schedule.
    """
    schedule["route_groups"] = [
        g for g in schedule.get("route_groups", [])
        if g.get("name") != group_name
    ]
    return schedule


def get_all_routes_files(schedule: dict) -> list[str]:
    """Get all routes_file paths from a schedule's route groups.

    Args:
        schedule: Schedule dict with a 'route_groups' list.

    Returns:
        list[str]: List of routes_file paths.
    """
    return [
        g["routes_file"]
        for g in schedule.get("route_groups", [])
        if "routes_file" in g
    ]


def get_total_route_count(schedule: dict) -> int:
    """Count total routes across all groups in a schedule.

    Args:
        schedule: Schedule dict with a 'route_groups' list.

    Returns:
        int: Total number of routes.
    """
    return count_routes_in_files(get_all_routes_files(schedule))


def is_old_format(entry: dict) -> bool:
    """Detect whether a schedule entry uses the old (pre-consolidation) format.

    Old format has a top-level 'routes_file' key but no 'route_groups' key.

    Args:
        entry: A schedule entry dict.

    Returns:
        bool: True if the entry is in the old format.
    """
    return "routes_file" in entry and "route_groups" not in entry


# ---------------------------------------------------------------------------
# BAT generator
# ---------------------------------------------------------------------------

def generate_bat(schedule_name, project_dir, python_exe,
                 eval=False, env_file=None):
    """Generate a .bat launcher script for scheduled_scrape.py.

    The generated .bat invokes scheduled_scrape.py with --schedule-name,
    which reads route groups from the schedules registry at runtime.

    Args:
        schedule_name: Schedule name (used in log messages and filename).
        project_dir: Absolute path to the project root.
        python_exe: Absolute path to the Python interpreter.
        eval: If True, enable Claude eval step; if False, pass --no-eval.
        env_file: Path to .env file for mfa_responder, or None.

    Returns:
        str: Absolute path to the generated .bat file.
    """
    # Build the scheduled_scrape.py argument line
    scrape_args = f"--schedule-name {schedule_name}"
    if not eval:
        scrape_args += " --no-eval"
    if env_file:
        scrape_args += f" --env-file {env_file}"

    description = schedule_name

    bat_content = f"""@echo off
REM Auto-generated by searchaero schedule add -- do not edit manually
REM Schedule: {description}

if not exist "%USERPROFILE%\\.searchaero\\logs" mkdir "%USERPROFILE%\\.searchaero\\logs"

set LOGFILE=%USERPROFILE%\\.searchaero\\logs\\task_scheduler.log
set PYTHON={python_exe}
set PROJECT={project_dir}

cd /d %PROJECT%

echo [%date% %time%] === {schedule_name} scrape starting === >> "%LOGFILE%"

%PYTHON% scripts\\scheduled_scrape.py {scrape_args} >> "%LOGFILE%" 2>&1
set SCRAPE_EXIT=%ERRORLEVEL%

if %SCRAPE_EXIT% NEQ 0 (
    echo [%date% %time%] {schedule_name} scrape failed with exit code %SCRAPE_EXIT% >> "%LOGFILE%"
) else (
    echo [%date% %time%] {schedule_name} scrape completed successfully >> "%LOGFILE%"
)
"""

    # Write the .bat file
    os.makedirs(SCHEDULES_DIR, exist_ok=True)
    bat_path = os.path.join(SCHEDULES_DIR, f"{schedule_name}.bat")
    with open(bat_path, "w", encoding="utf-8") as f:
        f.write(bat_content)

    return bat_path


# ---------------------------------------------------------------------------
# schtasks wrapper
# ---------------------------------------------------------------------------

def register_task(name, bat_path, interval_minutes):
    """Register a Windows Task Scheduler task with wake-to-run settings.

    Steps:
    1. Create task with schtasks /create
    2. Export XML, patch Settings for wake/battery/start-when-available
    3. Delete and re-create from patched XML

    Args:
        name: Schedule name (task will be named "searchaero-{name}").
        bat_path: Absolute path to the .bat launcher.
        interval_minutes: Minutes between runs.

    Returns:
        tuple[bool, str]: (success, message).
    """
    if interval_minutes < MIN_INTERVAL_MINUTES:
        return False, f"Interval must be at least {MIN_INTERVAL_MINUTES} minutes"

    task_name = f"searchaero-{name}"

    # Step 1: Create the basic task
    result = subprocess.run(
        ["schtasks", "/create",
         "/tn", task_name,
         "/tr", bat_path,
         "/sc", "minute",
         "/mo", str(interval_minutes),
         "/f"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, f"Failed to create task: {result.stderr.strip()}"

    # Step 2: Export XML
    result = subprocess.run(
        ["schtasks", "/query", "/tn", task_name, "/xml"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, f"Failed to export task XML: {result.stderr.strip()}"

    xml_text = result.stdout

    # Step 3: Parse and patch XML
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as e:
        return False, f"Failed to parse task XML: {e}"

    settings_tag = f"{{{_TASK_NS}}}Settings"
    settings = root.find(settings_tag)
    if settings is None:
        settings = ElementTree.SubElement(root, settings_tag)

    # Desired settings to ensure
    desired = {
        "StartWhenAvailable": "true",
        "WakeToRun": "true",
        "DisallowStartIfOnBatteries": "false",
        "AllowStartOnDemand": "true",
    }
    for tag_name, value in desired.items():
        full_tag = f"{{{_TASK_NS}}}{tag_name}"
        elem = settings.find(full_tag)
        if elem is None:
            elem = ElementTree.SubElement(settings, full_tag)
        elem.text = value

    # Step 4: Write modified XML to temp file
    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".xml", prefix="searchaero_task_")
    try:
        os.close(tmp_fd)
        tree = ElementTree.ElementTree(root)
        tree.write(tmp_path, xml_declaration=True, encoding="unicode")

        # Step 5: Delete and re-create from XML
        subprocess.run(
            ["schtasks", "/delete", "/tn", task_name, "/f"],
            capture_output=True, text=True,
        )

        result = subprocess.run(
            ["schtasks", "/create", "/tn", task_name, "/xml", tmp_path, "/f"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False, f"Failed to re-create task from XML: {result.stderr.strip()}"
    finally:
        # Step 6: Clean up temp file
        try:
            os.unlink(tmp_path)
        except OSError:
            pass

    return True, f"Task '{task_name}' registered (every {interval_minutes} min, wake-to-run enabled)"


def delete_task(name):
    """Delete a Windows Task Scheduler task.

    Args:
        name: Schedule name (task is named "searchaero-{name}").

    Returns:
        tuple[bool, str]: (success, message).
    """
    task_name = f"searchaero-{name}"
    result = subprocess.run(
        ["schtasks", "/delete", "/tn", task_name, "/f"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return False, f"Failed to delete task: {result.stderr.strip()}"
    return True, f"Task '{task_name}' deleted"


def query_task(name):
    """Query the status of a Windows Task Scheduler task.

    Args:
        name: Schedule name (task is named "searchaero-{name}").

    Returns:
        dict | None: {task_name, next_run, status, last_result} or None if not found.
    """
    task_name = f"searchaero-{name}"
    result = subprocess.run(
        ["schtasks", "/query", "/tn", task_name, "/fo", "CSV", "/nh"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return None

    line = result.stdout.strip()
    if not line:
        return None

    # CSV fields: "TaskName","Next Run Time","Status"
    # Use csv module for robust parsing
    import csv
    import io
    reader = csv.reader(io.StringIO(line))
    for row in reader:
        if len(row) >= 3:
            return {
                "task_name": row[0],
                "next_run": row[1],
                "status": row[2],
                "last_result": row[3] if len(row) > 3 else "N/A",
            }

    return None


# ---------------------------------------------------------------------------
# powercfg checker
# ---------------------------------------------------------------------------

_WAKE_VALUE_MAP = {
    "0x00000000": "disabled",
    "0x00000001": "enabled",
    "0x00000002": "important_only",
}


def check_wake_timers():
    """Check the current state of wake timers via powercfg.

    Returns:
        dict: {"ac": str, "dc": str, "raw": str} where ac/dc are one of
              "disabled", "enabled", "important_only", or the raw hex value.
    """
    result = subprocess.run(
        ["powercfg", "/query", "SCHEME_CURRENT", "SUB_SLEEP", "RTCWAKE"],
        capture_output=True, text=True,
    )

    raw = result.stdout
    ac_value = "unknown"
    dc_value = "unknown"

    for line in raw.splitlines():
        stripped = line.strip()
        if "Current AC Power Setting Index" in stripped:
            # e.g., "Current AC Power Setting Index: 0x00000001"
            parts = stripped.split(":")
            if len(parts) >= 2:
                hex_val = parts[-1].strip()
                ac_value = _WAKE_VALUE_MAP.get(hex_val, hex_val)
        elif "Current DC Power Setting Index" in stripped:
            parts = stripped.split(":")
            if len(parts) >= 2:
                hex_val = parts[-1].strip()
                dc_value = _WAKE_VALUE_MAP.get(hex_val, hex_val)

    return {"ac": ac_value, "dc": dc_value, "raw": raw}


def enable_wake_timers(ac=True):
    """Enable wake timers for AC power (and optionally DC).

    Args:
        ac: If True, enable AC wake timers (default).

    Returns:
        tuple[bool, str]: (success, message).
    """
    if ac:
        result = subprocess.run(
            ["powercfg", "/setacvalueindex", "SCHEME_CURRENT",
             "SUB_SLEEP", "RTCWAKE", "1"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False, (
                "Failed to enable wake timers. Run manually as admin:\n"
                "  powercfg /setacvalueindex SCHEME_CURRENT SUB_SLEEP RTCWAKE 1\n"
                "  powercfg /setactive SCHEME_CURRENT"
            )

        # Apply the change
        result = subprocess.run(
            ["powercfg", "/setactive", "SCHEME_CURRENT"],
            capture_output=True, text=True,
        )
        if result.returncode != 0:
            return False, (
                "Wake timer value set but failed to activate. Run manually:\n"
                "  powercfg /setactive SCHEME_CURRENT"
            )

    return True, "Wake timers enabled (AC)"
