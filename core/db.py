"""Database operations for seataero award availability."""

import os
from datetime import datetime, timezone

import psycopg
from psycopg.rows import dict_row


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

DEFAULT_DATABASE_URL = "postgresql://seataero:seataero_dev@localhost:5432/seataero"


def get_connection(database_url: str = None) -> psycopg.Connection:
    """Get a PostgreSQL connection.

    Args:
        database_url: Connection string. Falls back to DATABASE_URL env var,
                      then to the default local dev connection.

    Returns:
        psycopg.Connection with autocommit=False.
    """
    url = database_url or os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)
    return psycopg.connect(url, autocommit=False)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def create_schema(conn: psycopg.Connection):
    """Create tables and indexes if they don't exist.

    Safe to call repeatedly (uses IF NOT EXISTS throughout).
    """
    with conn.cursor() as cur:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS availability (
                id SERIAL PRIMARY KEY,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                date DATE NOT NULL,
                cabin TEXT NOT NULL,
                award_type TEXT NOT NULL,
                miles INTEGER NOT NULL,
                taxes_cents INTEGER,
                scraped_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                seats INTEGER,
                direct BOOLEAN,
                flights JSONB,
                UNIQUE(origin, destination, date, cabin, award_type)
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_route_date_cabin
            ON availability(origin, destination, date, cabin)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_scraped
            ON availability(scraped_at)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_alert_match
            ON availability(origin, destination, cabin, miles)
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS scrape_jobs (
                id SERIAL PRIMARY KEY,
                origin TEXT NOT NULL,
                destination TEXT NOT NULL,
                month_start DATE NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                started_at TIMESTAMPTZ,
                completed_at TIMESTAMPTZ,
                solutions_found INTEGER DEFAULT 0,
                solutions_stored INTEGER DEFAULT 0,
                solutions_rejected INTEGER DEFAULT 0,
                error TEXT,
                UNIQUE(origin, destination, month_start, started_at)
            )
        """)

    conn.commit()


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


def upsert_availability(conn: psycopg.Connection, results: list) -> int:
    """Upsert validated AwardResult objects into the availability table.

    Args:
        conn: Database connection.
        results: List of AwardResult objects (from core.models).

    Returns:
        Number of rows upserted.
    """
    if not results:
        return 0

    sql = """
        INSERT INTO availability (origin, destination, date, cabin, award_type, miles, taxes_cents, scraped_at)
        VALUES (%(origin)s, %(destination)s, %(date)s, %(cabin)s, %(award_type)s, %(miles)s, %(taxes_cents)s, %(scraped_at)s)
        ON CONFLICT (origin, destination, date, cabin, award_type)
        DO UPDATE SET
            miles = EXCLUDED.miles,
            taxes_cents = EXCLUDED.taxes_cents,
            scraped_at = EXCLUDED.scraped_at
    """

    params = [
        {
            "origin": r.origin,
            "destination": r.destination,
            "date": r.date,
            "cabin": r.cabin,
            "award_type": r.award_type,
            "miles": r.miles,
            "taxes_cents": r.taxes_cents,
            "scraped_at": r.scraped_at,
        }
        for r in results
    ]

    with conn.cursor() as cur:
        cur.executemany(sql, params)

    conn.commit()
    return len(results)


# ---------------------------------------------------------------------------
# Job tracking
# ---------------------------------------------------------------------------


def record_scrape_job(conn: psycopg.Connection, origin: str, destination: str,
                      month_start, status: str, solutions_found: int = 0,
                      solutions_stored: int = 0, solutions_rejected: int = 0,
                      error: str = None):
    """Record a scrape job in the scrape_jobs table.

    Args:
        conn: Database connection.
        origin: 3-letter IATA origin code.
        destination: 3-letter IATA destination code.
        month_start: Date of the month window start.
        status: Job status (e.g., 'completed', 'failed').
        solutions_found: Number of solutions parsed from API response.
        solutions_stored: Number of solutions that passed validation and were stored.
        solutions_rejected: Number of solutions that failed validation.
        error: Error message if the job failed.
    """
    now = datetime.now(timezone.utc)
    sql = """
        INSERT INTO scrape_jobs (origin, destination, month_start, status,
                                 started_at, completed_at, solutions_found,
                                 solutions_stored, solutions_rejected, error)
        VALUES (%(origin)s, %(destination)s, %(month_start)s, %(status)s,
                %(started_at)s, %(completed_at)s, %(solutions_found)s,
                %(solutions_stored)s, %(solutions_rejected)s, %(error)s)
    """
    with conn.cursor() as cur:
        cur.execute(sql, {
            "origin": origin,
            "destination": destination,
            "month_start": month_start,
            "status": status,
            "started_at": now,
            "completed_at": now if status in ("completed", "failed") else None,
            "solutions_found": solutions_found,
            "solutions_stored": solutions_stored,
            "solutions_rejected": solutions_rejected,
            "error": error,
        })
    conn.commit()


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def get_route_summary(conn: psycopg.Connection, origin: str, destination: str) -> list:
    """Get all availability records for a route.

    Returns:
        List of dicts with keys: date, cabin, award_type, miles, taxes_cents, scraped_at.
    """
    sql = """
        SELECT date, cabin, award_type, miles, taxes_cents, scraped_at
        FROM availability
        WHERE origin = %(origin)s AND destination = %(destination)s
        ORDER BY date, cabin, award_type
    """
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(sql, {"origin": origin, "destination": destination})
        return cur.fetchall()


def get_scrape_stats(conn: psycopg.Connection) -> dict:
    """Get aggregate scraping statistics.

    Returns:
        Dict with keys: total_rows, routes_covered, latest_scrape, date_range_start, date_range_end.
    """
    stats = {}

    with conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM availability")
        stats["total_rows"] = cur.fetchone()[0]

        cur.execute("SELECT COUNT(DISTINCT (origin, destination)) FROM availability")
        stats["routes_covered"] = cur.fetchone()[0]

        cur.execute("SELECT MAX(scraped_at) FROM availability")
        stats["latest_scrape"] = cur.fetchone()[0]

        cur.execute("SELECT MIN(date), MAX(date) FROM availability")
        row = cur.fetchone()
        stats["date_range_start"] = row[0]
        stats["date_range_end"] = row[1]

    return stats


def get_scanned_routes_today(conn: psycopg.Connection) -> set[tuple[str, str]]:
    """Return set of (origin, destination) pairs that have at least one
    completed scrape_job with started_at today (UTC).

    Used by the orchestrator to skip routes already scanned in the current sweep.
    """
    sql = """
        SELECT DISTINCT origin, destination
        FROM scrape_jobs
        WHERE status = 'completed'
          AND started_at >= CURRENT_DATE
    """
    with conn.cursor() as cur:
        cur.execute(sql)
        return {(row[0], row[1]) for row in cur.fetchall()}
