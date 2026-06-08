"""Tests for the alert/watch `program` discriminator (Layer 1).

Covers schema, idempotent migration, in-place migration of a pre-column DB,
writer round-trips, CLI `--program` wiring, program-scoped matching, the
watchlist passthrough, the program-sensitive dedup hash, and the list views'
Program column.

FULLY OFFLINE — no browser, no network. Availability rows are seeded directly
into the DB; the CLI is exercised via cli.main().
"""

import datetime
from datetime import timezone

import cli
import core.db as db
import core.models
from core.matching import compute_match_hash


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _columns(conn, table):
    cur = conn.execute(f"PRAGMA table_info({table})")
    return {row[1] for row in cur.fetchall()}


def _col_notnull(conn, table, column):
    """Return the notnull flag (PRAGMA column 3) for `column`, or None."""
    for row in conn.execute(f"PRAGMA table_info({table})").fetchall():
        if row[1] == column:
            return row[3]
    return None


def _award(origin, dest, date, cabin, award_type, miles, taxes_cents, program):
    return core.models.AwardResult(
        origin=origin,
        destination=dest,
        date=date,
        cabin=cabin,
        award_type=award_type,
        miles=miles,
        taxes_cents=taxes_cents,
        program=program,
        scraped_at=datetime.datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc),
    )


def _seed_mixed_availability(conn):
    """Seed a United + Aeroplan row sharing the same route/date/cabin."""
    d = datetime.date(2026, 7, 1)
    db.upsert_availability(conn, [
        _award("YYZ", "LAX", d, "economy", "Saver", 12500, 560, "united"),
        _award("YYZ", "LAX", d, "economy", "Saver", 11000, 700, "aeroplan"),
    ])


# ---------------------------------------------------------------------------
# 1-2. Schema: nullable program column on alerts + watches
# ---------------------------------------------------------------------------


def test_alerts_has_nullable_program_column(tmp_path):
    conn = db.get_connection(str(tmp_path / "new.db"))
    db.create_schema(conn)
    assert "program" in _columns(conn, "alerts")
    # notnull flag == 0 means nullable.
    assert _col_notnull(conn, "alerts", "program") == 0
    conn.close()


def test_watches_has_nullable_program_column(tmp_path):
    conn = db.get_connection(str(tmp_path / "new.db"))
    db.create_schema(conn)
    assert "program" in _columns(conn, "watches")
    assert _col_notnull(conn, "watches", "program") == 0
    conn.close()


# ---------------------------------------------------------------------------
# 3. Migration idempotency on a fresh DB
# ---------------------------------------------------------------------------


def test_ensure_alert_watch_program_columns_idempotent(tmp_path):
    conn = db.get_connection(str(tmp_path / "idem.db"))
    db.create_schema(conn)
    # Call twice — no error, no duplicate column.
    db.ensure_alert_watch_program_columns(conn)
    db.ensure_alert_watch_program_columns(conn)
    for table in ("alerts", "watches"):
        cur = conn.execute(f"PRAGMA table_info({table})")
        prog_cols = [r for r in cur.fetchall() if r[1] == "program"]
        assert len(prog_cols) == 1
    conn.close()


# ---------------------------------------------------------------------------
# 4. In-place migration of a pre-column DB
# ---------------------------------------------------------------------------


def _build_pre_column_alert_watch(conn):
    """Build alerts + watches WITHOUT a program column, each with one row."""
    conn.execute("""
        CREATE TABLE alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            cabin TEXT,
            max_miles INTEGER NOT NULL,
            date_from TEXT,
            date_to TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_notified_at TEXT,
            last_notified_hash TEXT,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute("""
        CREATE TABLE watches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            origin TEXT NOT NULL,
            destination TEXT NOT NULL,
            cabin TEXT,
            max_miles INTEGER NOT NULL,
            date_from TEXT,
            date_to TEXT,
            check_interval_minutes INTEGER NOT NULL DEFAULT 720,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            last_checked_at TEXT,
            last_notified_at TEXT,
            last_notified_hash TEXT,
            active INTEGER NOT NULL DEFAULT 1
        )
    """)
    conn.execute(
        "INSERT INTO alerts (origin, destination, cabin, max_miles) "
        "VALUES ('YYZ','LAX','economy',50000)"
    )
    conn.execute(
        "INSERT INTO watches (origin, destination, cabin, max_miles) "
        "VALUES ('YVR','SFO','business',90000)"
    )
    conn.commit()


def test_pre_column_db_migrates_in_place(tmp_path):
    conn = db.get_connection(str(tmp_path / "legacy.db"))
    _build_pre_column_alert_watch(conn)

    assert "program" not in _columns(conn, "alerts")
    assert "program" not in _columns(conn, "watches")

    db.ensure_alert_watch_program_columns(conn)

    assert "program" in _columns(conn, "alerts")
    assert "program" in _columns(conn, "watches")

    # Rows intact and program is NULL.
    arow = conn.execute("SELECT origin, destination, max_miles, program FROM alerts").fetchone()
    assert (arow["origin"], arow["destination"], arow["max_miles"]) == ("YYZ", "LAX", 50000)
    assert arow["program"] is None

    wrow = conn.execute("SELECT origin, destination, max_miles, program FROM watches").fetchone()
    assert (wrow["origin"], wrow["destination"], wrow["max_miles"]) == ("YVR", "SFO", 90000)
    assert wrow["program"] is None

    conn.close()


# ---------------------------------------------------------------------------
# 5. Writer round-trips via list_alerts / list_watches
# ---------------------------------------------------------------------------


def test_create_alert_program_round_trips(tmp_path):
    conn = db.get_connection(str(tmp_path / "a.db"))
    db.create_schema(conn)

    db.create_alert(conn, "YYZ", "LAX", 50000, cabin="economy", program="aeroplan")
    db.create_alert(conn, "YVR", "SFO", 60000)  # no program -> NULL

    alerts = {a["origin"]: a for a in db.list_alerts(conn)}
    assert alerts["YYZ"]["program"] == "aeroplan"
    assert alerts["YVR"]["program"] is None
    conn.close()


def test_create_watch_program_round_trips(tmp_path):
    conn = db.get_connection(str(tmp_path / "w.db"))
    db.create_schema(conn)

    db.create_watch(conn, "YYZ", "LAX", 50000, cabin="economy", program="aeroplan")
    db.create_watch(conn, "YVR", "SFO", 60000)  # no program -> NULL

    watches = {w["origin"]: w for w in db.list_watches(conn)}
    assert watches["YYZ"]["program"] == "aeroplan"
    assert watches["YVR"]["program"] is None
    conn.close()


# ---------------------------------------------------------------------------
# 6-7. CLI `--program` wiring stores the right value
# ---------------------------------------------------------------------------


def _init_db(db_path):
    """Create a fresh schema at db_path (mirrors `searchaero setup`)."""
    conn = db.get_connection(db_path)
    db.create_schema(conn)
    conn.close()


def test_cli_alert_add_program_stored(tmp_path):
    db_path = str(tmp_path / "cli.db")
    _init_db(db_path)
    rc = cli.main(["alert", "add", "--program", "aeroplan", "YYZ", "LAX",
                   "--max-miles", "50000", "--db-path", db_path])
    assert rc == 0
    conn = db.get_connection(db_path)
    row = conn.execute("SELECT program FROM alerts WHERE origin='YYZ'").fetchone()
    assert row["program"] == "aeroplan"
    conn.close()


def test_cli_alert_add_program_omitted_is_null(tmp_path):
    db_path = str(tmp_path / "cli.db")
    _init_db(db_path)
    rc = cli.main(["alert", "add", "YYZ", "LAX",
                   "--max-miles", "50000", "--db-path", db_path])
    assert rc == 0
    conn = db.get_connection(db_path)
    row = conn.execute("SELECT program FROM alerts WHERE origin='YYZ'").fetchone()
    assert row["program"] is None
    conn.close()


def test_cli_watch_add_program_stored(tmp_path):
    db_path = str(tmp_path / "cli.db")
    _init_db(db_path)
    rc = cli.main(["watch", "add", "--program", "aeroplan", "YYZ", "LAX",
                   "--max-miles", "50000", "--db-path", db_path])
    assert rc == 0
    conn = db.get_connection(db_path)
    row = conn.execute("SELECT program FROM watches WHERE origin='YYZ'").fetchone()
    assert row["program"] == "aeroplan"
    conn.close()


def test_cli_watch_add_program_omitted_is_null(tmp_path):
    db_path = str(tmp_path / "cli.db")
    _init_db(db_path)
    rc = cli.main(["watch", "add", "YYZ", "LAX",
                   "--max-miles", "50000", "--db-path", db_path])
    assert rc == 0
    conn = db.get_connection(db_path)
    row = conn.execute("SELECT program FROM watches WHERE origin='YYZ'").fetchone()
    assert row["program"] is None
    conn.close()


# ---------------------------------------------------------------------------
# 8. Program-scoped vs unscoped matching (check_alert_matches + _alert_check)
# ---------------------------------------------------------------------------


def test_check_alert_matches_scoped_to_program(tmp_path):
    conn = db.get_connection(str(tmp_path / "match.db"))
    db.create_schema(conn)
    _seed_mixed_availability(conn)

    aeroplan_only = db.check_alert_matches(
        conn, "YYZ", "LAX", 50000, program="aeroplan")
    assert len(aeroplan_only) == 1
    assert aeroplan_only[0]["program"] == "aeroplan"

    united_only = db.check_alert_matches(
        conn, "YYZ", "LAX", 50000, program="united")
    assert len(united_only) == 1
    assert united_only[0]["program"] == "united"

    unscoped = db.check_alert_matches(conn, "YYZ", "LAX", 50000)
    assert len(unscoped) == 2
    assert {m["program"] for m in unscoped} == {"united", "aeroplan"}

    conn.close()


def test_alert_check_respects_program_scope(tmp_path, capsys):
    db_path = str(tmp_path / "ac.db")
    conn = db.get_connection(db_path)
    db.create_schema(conn)
    _seed_mixed_availability(conn)
    # Scoped alert: only Aeroplan rows should trigger it.
    db.create_alert(conn, "YYZ", "LAX", 50000, program="aeroplan")
    conn.close()

    rc = cli.main(["alert", "check", "--db-path", db_path, "--json"])
    assert rc == 0
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["alerts_triggered"] == 1
    matches = out["results"][0]["matches"]
    assert all(m["program"] == "aeroplan" for m in matches)
    assert len(matches) == 1


def test_alert_check_unscoped_matches_all_programs(tmp_path, capsys):
    db_path = str(tmp_path / "ac2.db")
    conn = db.get_connection(db_path)
    db.create_schema(conn)
    _seed_mixed_availability(conn)
    db.create_alert(conn, "YYZ", "LAX", 50000)  # unscoped
    conn.close()

    rc = cli.main(["alert", "check", "--db-path", db_path, "--json"])
    assert rc == 0
    import json
    out = json.loads(capsys.readouterr().out)
    assert out["alerts_triggered"] == 1
    matches = out["results"][0]["matches"]
    assert {m["program"] for m in matches} == {"united", "aeroplan"}


# ---------------------------------------------------------------------------
# 9. check_watches passes the watch's program into the matcher
# ---------------------------------------------------------------------------


def test_check_watches_passes_program(monkeypatch, tmp_path):
    conn = db.get_connection(str(tmp_path / "cw.db"))
    db.create_schema(conn)
    _seed_mixed_availability(conn)
    db.create_watch(conn, "YYZ", "LAX", 50000, program="aeroplan")

    captured = {}
    real = db.check_alert_matches

    def _spy(c, origin, dest, max_miles, **kwargs):
        captured["program"] = kwargs.get("program", "MISSING")
        return real(c, origin, dest, max_miles, **kwargs)

    from core import watchlist
    monkeypatch.setattr(watchlist.db, "check_alert_matches", _spy)

    # scrape=False / notify_enabled=False: pure evaluation, no subprocess/network.
    watchlist.check_watches(conn, scrape=False, notify_enabled=False)
    assert captured["program"] == "aeroplan"
    conn.close()


def test_check_watches_unscoped_passes_none(monkeypatch, tmp_path):
    conn = db.get_connection(str(tmp_path / "cw2.db"))
    db.create_schema(conn)
    _seed_mixed_availability(conn)
    db.create_watch(conn, "YYZ", "LAX", 50000)  # unscoped -> program NULL

    captured = {}
    real = db.check_alert_matches

    def _spy(c, origin, dest, max_miles, **kwargs):
        captured["program"] = kwargs.get("program", "MISSING")
        return real(c, origin, dest, max_miles, **kwargs)

    from core import watchlist
    monkeypatch.setattr(watchlist.db, "check_alert_matches", _spy)

    watchlist.check_watches(conn, scrape=False, notify_enabled=False)
    assert captured["program"] is None
    conn.close()


# ---------------------------------------------------------------------------
# 10. compute_match_hash is program-sensitive
# ---------------------------------------------------------------------------


def test_compute_match_hash_is_program_sensitive():
    base = {"date": "2026-07-01", "cabin": "economy",
            "award_type": "Saver", "miles": 12500}
    united = dict(base, program="united")
    aeroplan = dict(base, program="aeroplan")

    assert compute_match_hash([united]) != compute_match_hash([aeroplan])
    # Same program -> same hash (sanity).
    assert compute_match_hash([united]) == compute_match_hash([dict(base, program="united")])


# ---------------------------------------------------------------------------
# 11. List views include a Program column
# ---------------------------------------------------------------------------


def test_alert_list_includes_program_column(tmp_path, capsys):
    db_path = str(tmp_path / "al.db")
    conn = db.get_connection(db_path)
    db.create_schema(conn)
    db.create_alert(conn, "YYZ", "LAX", 50000, program="aeroplan")
    db.create_alert(conn, "YVR", "SFO", 60000)  # NULL -> "any"
    conn.close()

    rc = cli.main(["alert", "list", "--db-path", db_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Program" in out
    assert "aeroplan" in out
    assert "any" in out


def test_watch_list_includes_program_column(tmp_path, capsys):
    db_path = str(tmp_path / "wl.db")
    conn = db.get_connection(db_path)
    db.create_schema(conn)
    db.create_watch(conn, "YYZ", "LAX", 50000, program="aeroplan")
    db.create_watch(conn, "YVR", "SFO", 60000)  # NULL -> "any"
    conn.close()

    rc = cli.main(["watch", "list", "--db-path", db_path])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Program" in out
    assert "aeroplan" in out
    assert "any" in out
