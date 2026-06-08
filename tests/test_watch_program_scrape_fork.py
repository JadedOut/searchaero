"""Layer 2 tests: program-aware freshness + program-forked watch refresh scrape.

NO real browsers/scrapers are launched. ``subprocess.run`` is patched where
``core.watchlist`` looks it up, capturing the argv that ``check_watches`` builds
so we can assert which scrape path each stale (program, route) was routed to:

  - United / unscoped (program None or "united") -> scripts/burn_in.py --headless
  - Aeroplan                                     -> cli.py search --program aeroplan
                                                    ... --max-reauths 1 (no --headless,
                                                    no --ephemeral, no --file)

Also covers the new program-scoped ``get_route_freshness`` (an Aeroplan-scoped
freshness check ignores United-only rows) and the new ``search --max-reauths``
flag threading into ``_scrape_route_aeroplan_live``.
"""

import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import db, watchlist


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def conn(tmp_path):
    c = db.get_connection(str(tmp_path / "watch_fork.db"))
    db.ensure_schema(c)
    yield c
    c.close()


def _iso(dt):
    return dt.isoformat()


def _seed_availability(conn, origin, dest, program, scraped_at, *, miles=50000,
                       date="2026-07-01", cabin="economy", award_type="saver"):
    """Insert one program-tagged availability row with an explicit scraped_at."""
    conn.execute(
        """
        INSERT INTO availability
            (origin, destination, date, cabin, award_type, miles, taxes_cents,
             scraped_at, program)
        VALUES
            (:origin, :destination, :date, :cabin, :award_type, :miles, 4500,
             :scraped_at, :program)
        """,
        {
            "origin": origin, "destination": dest, "date": date,
            "cabin": cabin, "award_type": award_type, "miles": miles,
            "scraped_at": scraped_at, "program": program,
        },
    )
    conn.commit()


# ---------------------------------------------------------------------------
# 1. Program-scoped freshness ignores other programs' rows.
# ---------------------------------------------------------------------------


def test_freshness_aeroplan_ignores_fresh_united_rows(conn):
    now = datetime.now(timezone.utc)
    # Fresh United row, but NO Aeroplan row at all.
    _seed_availability(conn, "YYZ", "LAX", "united", _iso(now))

    # Aeroplan-scoped freshness: no Aeroplan data -> stale (has_data False).
    aero = db.get_route_freshness(conn, "YYZ", "LAX", ttl_seconds=3600,
                                  program="aeroplan")
    assert aero["has_data"] is False
    assert aero["is_stale"] is True

    # United-scoped freshness: the fresh United row -> NOT stale.
    united = db.get_route_freshness(conn, "YYZ", "LAX", ttl_seconds=3600,
                                    program="united")
    assert united["has_data"] is True
    assert united["is_stale"] is False


def test_freshness_aeroplan_scoped_uses_only_aeroplan_scraped_at(conn):
    now = datetime.now(timezone.utc)
    # United row is FRESH; Aeroplan row is OLD (well past TTL).
    _seed_availability(conn, "YYZ", "LAX", "united", _iso(now))
    _seed_availability(conn, "YYZ", "LAX", "aeroplan",
                       _iso(now - timedelta(seconds=10000)))

    aero = db.get_route_freshness(conn, "YYZ", "LAX", ttl_seconds=3600,
                                  program="aeroplan")
    # Aeroplan-scoped sees only the OLD aeroplan row -> stale.
    assert aero["has_data"] is True
    assert aero["is_stale"] is True


def test_freshness_no_program_preserves_all_program_behavior(conn):
    now = datetime.now(timezone.utc)
    # Only an Aeroplan row exists, and it is FRESH.
    _seed_availability(conn, "YYZ", "LAX", "aeroplan", _iso(now))

    # No program arg -> all-program MAX(scraped_at) -> fresh, not stale.
    allp = db.get_route_freshness(conn, "YYZ", "LAX", ttl_seconds=3600)
    assert allp["has_data"] is True
    assert allp["is_stale"] is False

    # And an OLD all-program row is reported stale (unchanged behavior).
    conn.execute("DELETE FROM availability")
    conn.commit()
    _seed_availability(conn, "YYZ", "LAX", "united",
                       _iso(now - timedelta(seconds=10000)))
    allp_old = db.get_route_freshness(conn, "YYZ", "LAX", ttl_seconds=3600)
    assert allp_old["is_stale"] is True


# ---------------------------------------------------------------------------
# 2. search --max-reauths flag: argparse default + threading.
# ---------------------------------------------------------------------------


def test_search_max_reauths_default_is_4():
    """--max-reauths defaults to 4 (preserves manual-search behavior)."""
    import cli

    ns = _parse_via_main(cli, ["search", "YYZ", "LAX"])
    assert ns.max_reauths == 4

    ns2 = _parse_via_main(cli, ["search", "--program", "aeroplan",
                                "YYZ", "LAX", "--max-reauths", "2"])
    assert ns2.max_reauths == 2


def _parse_via_main(cli, argv):
    """Drive cli's argparse exactly as main() builds it, returning the parsed
    namespace WITHOUT executing the command.

    cli.py constructs its parser inside main(); we monkeypatch
    ``parser.parse_args`` indirectly by intercepting ``ArgumentParser.parse_args``
    on the search subparser. Simpler: call main with a patched dispatcher.
    """
    import argparse

    captured = {}
    real_parse_args = argparse.ArgumentParser.parse_args

    def spy_parse_args(self, args=None, namespace=None):
        ns = real_parse_args(self, args=args, namespace=namespace)
        if getattr(ns, "command", None) == "search":
            captured["ns"] = ns
            # Short-circuit: raise a sentinel so main() stops before scraping.
            raise _StopParse(ns)
        return ns

    with patch.object(argparse.ArgumentParser, "parse_args", spy_parse_args):
        try:
            cli.main(argv)
        except _StopParse as e:
            return e.ns
    return captured.get("ns")


class _StopParse(Exception):
    def __init__(self, ns):
        super().__init__("stop")
        self.ns = ns


def test_search_threads_max_reauths_into_aeroplan_live(conn, monkeypatch):
    """`search --program aeroplan ... --max-reauths N` calls
    _scrape_route_aeroplan_live(max_reauths=N)."""
    import cli

    captured = {}

    def fake_live(origin, dest, conn, **kwargs):
        captured.update(kwargs)
        return {"found": 0, "stored": 0, "rejected": 0, "errors": 0,
                "total_windows": 0, "expired": False, "error_messages": [],
                "reauths": 0, "batches": 1, "span_complete": True}

    monkeypatch.setattr(cli, "_scrape_route_aeroplan_live", fake_live)
    monkeypatch.setattr(cli.db, "get_connection", lambda *a, **k: conn)
    monkeypatch.setattr(cli.db, "ensure_schema", lambda c: None)

    # Build an args namespace the same way main() would for a single route.
    ns = _parse_via_main(cli, ["search", "--program", "aeroplan",
                               "YYZ", "LAX", "--max-reauths", "2", "--json"])
    assert ns.max_reauths == 2

    # main() computes _months_list after parsing and passes route as a 2-tuple;
    # mimic that minimal post-parse state for the in-proc single-route path.
    ns._months_list = None
    ns.route = ("YYZ", "LAX")
    cli._search_single_inproc(ns)
    assert captured.get("max_reauths") == 2


# ---------------------------------------------------------------------------
# 3. check_watches refresh-scrape fork (subprocess argv capture).
# ---------------------------------------------------------------------------


def _run_check_watches(conn):
    """Run check_watches with subprocess.run patched; return captured argv list."""
    captured = []

    def fake_run(cmd, *args, **kwargs):
        captured.append(list(cmd))
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result

    with patch.object(watchlist.subprocess, "run", side_effect=fake_run):
        # notify_enabled False + scrape True; matcher will find nothing notable.
        watchlist.check_watches(conn, scrape=True, notify_enabled=False)
    return captured


def test_stale_united_watch_routes_to_burn_in(conn):
    # A stale United watch (no fresh data at all).
    db.create_watch(conn, "YYZ", "LAX", 60000, program="united",
                    check_interval_minutes=60)

    captured = _run_check_watches(conn)

    burn = [c for c in captured if any("burn_in.py" in str(x) for x in c)]
    assert len(burn) == 1, f"expected one burn_in call, got {captured}"
    assert "--headless" in burn[0]
    # And it did NOT spawn an aeroplan search.
    assert not any("--program" in c and "aeroplan" in c for c in captured)


def test_stale_unscoped_watch_routes_to_burn_in(conn):
    # Backward compat: an unscoped (NULL program) watch still goes to burn_in.
    db.create_watch(conn, "YYZ", "LAX", 60000, program=None,
                    check_interval_minutes=60)

    captured = _run_check_watches(conn)

    burn = [c for c in captured if any("burn_in.py" in str(x) for x in c)]
    assert len(burn) == 1
    assert "--headless" in burn[0]


def test_stale_aeroplan_watch_routes_to_cli_search(conn):
    db.create_watch(conn, "YYZ", "LAX", 60000, program="aeroplan",
                    check_interval_minutes=60)

    captured = _run_check_watches(conn)

    # NOT through burn_in.py.
    assert not any(any("burn_in.py" in str(x) for x in c) for c in captured), captured

    aero = [c for c in captured
            if any("cli.py" in str(x) for x in c) and "--program" in c]
    assert len(aero) == 1, f"expected one aeroplan search call, got {captured}"
    cmd = aero[0]
    assert "search" in cmd
    assert "--program" in cmd and "aeroplan" in cmd
    assert "YYZ" in cmd and "LAX" in cmd
    assert "--mfa-file" in cmd
    assert "--mfa-method" in cmd and "email" in cmd
    # Capped re-auth for a bounded watch refresh.
    assert "--max-reauths" in cmd
    mr_idx = cmd.index("--max-reauths")
    assert cmd[mr_idx + 1] == "1"
    # These United/ephemeral/batch flags must be ABSENT.
    assert "--headless" not in cmd
    assert "--ephemeral" not in cmd
    assert "--file" not in cmd


def test_united_and_aeroplan_same_route_handled_independently(conn):
    """A United watch and an Aeroplan watch on the SAME route fork into two
    distinct scrape paths."""
    db.create_watch(conn, "YYZ", "LAX", 60000, program="united",
                    check_interval_minutes=60)
    db.create_watch(conn, "YYZ", "LAX", 60000, program="aeroplan",
                    check_interval_minutes=60)

    captured = _run_check_watches(conn)

    burn = [c for c in captured if any("burn_in.py" in str(x) for x in c)]
    aero = [c for c in captured
            if any("cli.py" in str(x) for x in c) and "--program" in c]
    assert len(burn) == 1, captured
    assert len(aero) == 1, captured
    assert "--headless" in burn[0]
    assert "aeroplan" in aero[0]


def test_fresh_aeroplan_watch_not_scraped(conn):
    """A watch whose program-scoped data is FRESH triggers no scrape."""
    now = datetime.now(timezone.utc)
    _seed_availability(conn, "YYZ", "LAX", "aeroplan", _iso(now))
    db.create_watch(conn, "YYZ", "LAX", 60000, program="aeroplan",
                    check_interval_minutes=60)

    captured = _run_check_watches(conn)
    assert captured == [], f"expected no scrape, got {captured}"
