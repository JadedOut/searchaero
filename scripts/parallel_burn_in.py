"""Parallel burn-in runner — launches N concurrent burn_in.py workers.

Splits routes round-robin across workers, staggers their startup, monitors
progress, handles graceful Ctrl+C shutdown, and prints an aggregated summary.

Usage:
    python scripts/parallel_burn_in.py --routes-file routes/canada_test.txt --workers 3 --duration 60
    python scripts/parallel_burn_in.py --setup-profiles --workers 3
"""

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime

# Path setup for imports
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "experiments"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def build_parser():
    parser = argparse.ArgumentParser(
        description="Parallel burn-in runner — launches N concurrent burn_in.py workers."
    )
    parser.add_argument("--routes-file", type=str, help="Path to routes file (required unless --setup-profiles)")
    parser.add_argument("--workers", type=int, default=3, help="Number of parallel workers (default: 3)")
    parser.add_argument("--duration", type=int, default=60, help="Max run duration in minutes (default: 60)")
    parser.add_argument("--delay", type=float, default=1.0, help="Delay between API calls per worker (default: 1.0)")
    parser.add_argument("--refresh-interval", type=int, default=3, help="Refresh cookies every N calls (default: 3)")
    parser.add_argument("--session-budget", type=int, default=9999, help="Session budget per worker (default: 9999)")
    parser.add_argument("--route-delay", type=int, default=5, help="Seconds between routes per worker (default: 5)")
    parser.add_argument("--stagger", type=int, default=10, help="Seconds between worker launches (default: 10)")
    parser.add_argument("--setup-profiles", action="store_true", help="Set up browser profiles for MFA login (sequential, no scraping)")
    parser.add_argument("--create-schema", action="store_true", help="Create/update DB schema before starting")
    parser.add_argument("--headless", action="store_true", help="Run browsers in headless mode")
    parser.add_argument("--log-dir", type=str, default="logs/", help="Directory for log files (default: logs/)")
    return parser


def load_routes(routes_file):
    """Load routes from file, return list of 'ORIG DEST' strings."""
    routes = []
    with open(routes_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                routes.append(line)
    return routes


def split_routes_round_robin(routes, n_workers):
    """Distribute routes round-robin across N workers.

    Round-robin (not contiguous chunks) ensures each worker gets a mix
    of airports, spreading the load across different Akamai origin fingerprints.

    Returns list of N lists.
    """
    chunks = [[] for _ in range(n_workers)]
    for i, route in enumerate(routes):
        chunks[i % n_workers].append(route)
    return chunks


def write_chunk_files(chunks, routes_dir="routes"):
    """Write each chunk to routes/_chunk_{worker_id}.txt. Returns list of file paths."""
    os.makedirs(routes_dir, exist_ok=True)
    paths = []
    for i, chunk in enumerate(chunks):
        path = os.path.join(routes_dir, f"_chunk_{i + 1}.txt")
        with open(path, "w") as f:
            for route in chunk:
                f.write(route + "\n")
        paths.append(path)
    return paths


def cleanup_chunk_files(chunk_paths):
    """Remove temp route chunk files."""
    for path in chunk_paths:
        try:
            os.remove(path)
        except OSError:
            pass


def launch_worker(worker_id, routes_chunk_file, args):
    """Launch a burn_in.py subprocess with worker-specific settings.

    Returns (subprocess.Popen, stdout_log_file_handle, stdout_log_path).
    """
    os.makedirs(args.log_dir, exist_ok=True)
    stdout_log = os.path.join(args.log_dir, f"worker_{worker_id}_stdout.log")

    cmd = [
        sys.executable, "scripts/burn_in.py",
        "--routes-file", routes_chunk_file,
        "--worker-id", str(worker_id),
        "--duration", str(args.duration),
        "--delay", str(args.delay),
        "--refresh-interval", str(args.refresh_interval),
        "--session-budget", str(args.session_budget),
        "--route-delay", str(args.route_delay),
        "--log-dir", args.log_dir,
    ]

    if args.headless:
        cmd.append("--headless")

    # Only first worker creates schema
    if worker_id == 1 and args.create_schema:
        cmd.append("--create-schema")

    log_fh = open(stdout_log, "w")
    proc = subprocess.Popen(
        cmd,
        stdout=log_fh,
        stderr=subprocess.STDOUT,
        # On Windows, use CREATE_NEW_PROCESS_GROUP for signal handling
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0,
    )
    return proc, log_fh, stdout_log


def find_worker_jsonl(log_dir, worker_id, run_start_time):
    """Find the JSONL log file for a specific worker from this run.

    Looks for burn_in_w{worker_id}_*.jsonl files created after run_start_time.
    """
    pattern = os.path.join(log_dir, f"burn_in_w{worker_id}_*.jsonl")
    matches = glob.glob(pattern)
    # Filter to files created after our run started
    recent = [f for f in matches if os.path.getmtime(f) >= run_start_time - 5]
    if recent:
        return max(recent, key=os.path.getmtime)
    return None


def count_jsonl_records(filepath):
    """Count records and aggregate stats from a JSONL file."""
    if not filepath or not os.path.exists(filepath):
        return {"routes": 0, "windows_ok": 0, "windows_failed": 0, "errors": 0, "found": 0, "stored": 0}

    stats = {"routes": 0, "windows_ok": 0, "windows_failed": 0, "errors": 0, "found": 0, "stored": 0}
    try:
        with open(filepath) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    stats["routes"] += 1
                    stats["windows_ok"] += rec.get("windows_ok", 0)
                    stats["windows_failed"] += rec.get("windows_failed", 0)
                    stats["errors"] += len(rec.get("errors", []))
                    stats["found"] += rec.get("solutions_found", 0)
                    stats["stored"] += rec.get("solutions_stored", 0)
                except json.JSONDecodeError:
                    pass
    except OSError:
        pass
    return stats


def monitor_workers(workers, args, run_start_time):
    """Poll worker processes every 5 seconds, print live status.

    workers: list of (worker_id, proc, log_fh, stdout_log)
    """
    while True:
        all_done = True
        lines = []

        for worker_id, proc, log_fh, stdout_log in workers:
            retcode = proc.poll()
            status = "DONE" if retcode is not None else "RUNNING"
            if retcode is None:
                all_done = False

            # Find and parse JSONL for this worker
            jsonl_path = find_worker_jsonl(args.log_dir, worker_id, run_start_time)
            stats = count_jsonl_records(jsonl_path)

            lines.append(
                f"  Worker {worker_id}: {status:7s} | "
                f"{stats['routes']} routes | "
                f"{stats['windows_ok']}/{stats['windows_ok'] + stats['windows_failed']} windows OK | "
                f"{stats['found']} found | "
                f"{stats['errors']} errors"
            )

        # Print status
        print(f"\n--- Status ({datetime.now().strftime('%H:%M:%S')}) ---")
        for line in lines:
            print(line)

        if all_done:
            break

        time.sleep(5)


def aggregate_results(workers, args, run_start_time, wall_clock_seconds):
    """Parse all JSONL logs from this run and print combined summary."""
    total_routes = 0
    total_windows_ok = 0
    total_windows_failed = 0
    total_found = 0
    total_stored = 0
    total_errors = 0
    worker_summaries = []

    for worker_id, proc, log_fh, stdout_log in workers:
        jsonl_path = find_worker_jsonl(args.log_dir, worker_id, run_start_time)
        stats = count_jsonl_records(jsonl_path)

        total_routes += stats["routes"]
        total_windows_ok += stats["windows_ok"]
        total_windows_failed += stats["windows_failed"]
        total_found += stats["found"]
        total_stored += stats["stored"]
        total_errors += stats["errors"]

        worker_summaries.append((worker_id, stats, jsonl_path))

    total_windows = total_windows_ok + total_windows_failed
    success_pct = (total_windows_ok / total_windows * 100) if total_windows > 0 else 0.0
    wall_min = wall_clock_seconds / 60
    throughput = (total_routes / wall_min) if wall_min > 0 else 0.0

    print()
    print("=" * 60)
    print(f"Parallel Burn-In Complete ({args.workers} workers)")
    print("=" * 60)
    print(f"  Total routes scraped:  {total_routes}")
    print(f"  Total windows:         {total_windows_ok}/{total_windows} ({success_pct:.1f}%)")
    print(f"  Solutions found:       {total_found:,}")
    print(f"  Solutions stored:      {total_stored:,}")
    print(f"  Total errors:          {total_errors}")
    print(f"  Wall-clock time:       {wall_min:.1f} minutes")
    print(f"  Throughput:            {throughput:.2f} routes/min")
    print(f"  Log files:")
    for worker_id, stats, jsonl_path in worker_summaries:
        display_path = jsonl_path or "(no log found)"
        print(f"    Worker {worker_id}: {display_path} ({stats['routes']} routes)")
    print("=" * 60)


def graceful_shutdown(workers):
    """Send interrupt signal to all workers, wait for graceful shutdown."""
    print("\nShutting down workers...")

    for worker_id, proc, log_fh, stdout_log in workers:
        if proc.poll() is None:
            try:
                if sys.platform == "win32":
                    # On Windows, send CTRL_BREAK_EVENT to the process group
                    os.kill(proc.pid, signal.CTRL_BREAK_EVENT)
                else:
                    proc.send_signal(signal.SIGINT)
            except OSError:
                pass

    # Wait up to 30s for graceful shutdown
    deadline = time.time() + 30
    for worker_id, proc, log_fh, stdout_log in workers:
        remaining = max(0, deadline - time.time())
        try:
            proc.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            print(f"  Worker {worker_id}: force-killing (timeout)...")
            proc.kill()
            proc.wait()
        finally:
            log_fh.close()

        print(f"  Worker {worker_id}: stopped (exit code {proc.returncode})")


def setup_profiles(args):
    """Set up browser profiles sequentially for MFA login.

    Launches each browser profile one at a time (not parallel) so MFA
    emails arrive one at a time. Only needs to run once — after that,
    "Already logged in" kicks in for each profile.
    """
    from cookie_farm import CookieFarm

    n_workers = args.workers
    print("=" * 60)
    print("Browser Profile Setup")
    print(f"Setting up {n_workers} browser profiles for parallel scraping.")
    print("Each browser will open for login — complete MFA if prompted.")
    print("=" * 60)

    for i in range(1, n_workers + 1):
        profile_dir = os.path.join(
            os.path.dirname(__file__), "experiments",
            f".browser-profile-{i}"
        )
        print(f"\n--- Worker {i}/{n_workers} ---")
        print(f"Profile: {profile_dir}")

        try:
            farm = CookieFarm(user_data_dir=profile_dir, headless=args.headless)
            farm.start()
            farm.ensure_logged_in()
            print(f"Worker {i} profile ready.")
            farm.stop()
        except Exception as exc:
            print(f"ERROR setting up worker {i}: {exc}")
            print("Fix the issue and re-run --setup-profiles.")
            sys.exit(1)

    print("\n" + "=" * 60)
    print(f"All {n_workers} profiles set up successfully!")
    print("Run without --setup-profiles to start scraping.")
    print("=" * 60)


def main():
    parser = build_parser()
    args = parser.parse_args()

    # Handle --setup-profiles mode
    if args.setup_profiles:
        setup_profiles(args)
        return

    # Validate args
    if not args.routes_file:
        parser.error("--routes-file is required (unless using --setup-profiles)")

    # Load and split routes
    routes = load_routes(args.routes_file)
    if not routes:
        print(f"ERROR: No routes found in {args.routes_file}")
        sys.exit(1)

    n_workers = min(args.workers, len(routes))  # Don't create more workers than routes
    if n_workers < args.workers:
        print(f"NOTE: Only {len(routes)} routes — using {n_workers} workers instead of {args.workers}")

    chunks = split_routes_round_robin(routes, n_workers)

    # Banner
    print("=" * 60)
    print("Seataero Parallel Burn-In Runner")
    print(f"Time:              {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"Routes file:       {args.routes_file}")
    print(f"Total routes:      {len(routes)}")
    print(f"Workers:           {n_workers}")
    for i, chunk in enumerate(chunks):
        print(f"  Worker {i+1}:        {len(chunk)} routes")
    print(f"Duration:          {args.duration} minutes")
    print(f"Stagger:           {args.stagger}s between worker launches")
    print(f"Create schema:     {args.create_schema}")
    print("=" * 60)

    # Write chunk files
    chunk_paths = write_chunk_files(chunks)

    run_start_time = time.time()
    workers = []  # list of (worker_id, proc, log_fh, stdout_log)
    shutdown_requested = False

    def handle_signal(signum, frame):
        nonlocal shutdown_requested
        if not shutdown_requested:
            shutdown_requested = True
            print("\n\nCtrl+C received — shutting down all workers...")
            graceful_shutdown(workers)

    # Set up signal handler
    signal.signal(signal.SIGINT, handle_signal)
    if sys.platform == "win32":
        signal.signal(signal.SIGBREAK, handle_signal)

    try:
        # Launch workers with staggered start
        print(f"\nLaunching {n_workers} workers...")
        for i in range(n_workers):
            worker_id = i + 1
            print(f"  Starting worker {worker_id}...")
            proc, log_fh, stdout_log = launch_worker(worker_id, chunk_paths[i], args)
            workers.append((worker_id, proc, log_fh, stdout_log))

            # Stagger (except after last worker)
            if i < n_workers - 1:
                print(f"  Waiting {args.stagger}s before next worker...")
                time.sleep(args.stagger)

        print(f"\nAll {n_workers} workers launched. Monitoring...\n")

        # Monitor until all done
        if not shutdown_requested:
            monitor_workers(workers, args, run_start_time)

        # Close log file handles
        for worker_id, proc, log_fh, stdout_log in workers:
            try:
                log_fh.close()
            except Exception:
                pass

        # Aggregated summary
        wall_clock = time.time() - run_start_time
        aggregate_results(workers, args, run_start_time, wall_clock)

    finally:
        # Cleanup temp chunk files
        print("\nCleaning up temp route files...")
        cleanup_chunk_files(chunk_paths)
        print("Done.")


if __name__ == "__main__":
    main()
