"""Tests for core.db — database operations against real PostgreSQL.

Requires: docker compose up -d (PostgreSQL running on localhost:5432)
"""

import datetime
import sys
import os
from datetime import timezone

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.db import get_connection, create_schema, upsert_availability, record_scrape_job, get_route_summary, get_scrape_stats
from core.models import AwardResult

DATABASE_URL = "postgresql://seataero:seataero_dev@localhost:5432/seataero"


@pytest.fixture
def conn():
    """Get a database connection and ensure schema exists."""
    c = get_connection(DATABASE_URL)
    create_schema(c)
    yield c
    c.close()


@pytest.fixture
def clean_test_route(conn):
    """Delete test data for a specific route before/after test."""
    origin, dest = "TST", "DBT"
    with conn.cursor() as cur:
        cur.execute("DELETE FROM availability WHERE origin = %s AND destination = %s", (origin, dest))
        cur.execute("DELETE FROM scrape_jobs WHERE origin = %s AND destination = %s", (origin, dest))
    conn.commit()
    yield origin, dest
    with conn.cursor() as cur:
        cur.execute("DELETE FROM availability WHERE origin = %s AND destination = %s", (origin, dest))
        cur.execute("DELETE FROM scrape_jobs WHERE origin = %s AND destination = %s", (origin, dest))
    conn.commit()


# ---------------------------------------------------------------------------
# Schema creation
# ---------------------------------------------------------------------------


class TestSchema:
    def test_create_schema_idempotent(self, conn):
        """create_schema can be called multiple times without error."""
        create_schema(conn)
        create_schema(conn)

    def test_availability_table_exists(self, conn):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'availability'
                ORDER BY ordinal_position
            """)
            columns = [row[0] for row in cur.fetchall()]
        assert "origin" in columns
        assert "destination" in columns
        assert "date" in columns
        assert "cabin" in columns
        assert "award_type" in columns
        assert "miles" in columns
        assert "taxes_cents" in columns
        assert "scraped_at" in columns

    def test_scrape_jobs_table_exists(self, conn):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT column_name FROM information_schema.columns
                WHERE table_name = 'scrape_jobs'
                ORDER BY ordinal_position
            """)
            columns = [row[0] for row in cur.fetchall()]
        assert "origin" in columns
        assert "destination" in columns
        assert "month_start" in columns
        assert "status" in columns
        assert "solutions_found" in columns
        assert "solutions_stored" in columns

    def test_unique_constraint_exists(self, conn):
        with conn.cursor() as cur:
            cur.execute("""
                SELECT constraint_name FROM information_schema.table_constraints
                WHERE table_name = 'availability' AND constraint_type = 'UNIQUE'
            """)
            constraints = cur.fetchall()
        assert len(constraints) >= 1


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------


class TestUpsert:
    def test_insert_new_rows(self, conn, clean_test_route):
        origin, dest = clean_test_route
        future = datetime.date.today() + datetime.timedelta(days=30)
        results = [
            AwardResult(origin=origin, destination=dest, date=future,
                        cabin="economy", award_type="Saver", miles=13000, taxes_cents=6851),
            AwardResult(origin=origin, destination=dest, date=future,
                        cabin="business", award_type="Saver", miles=30000, taxes_cents=6851),
        ]
        count = upsert_availability(conn, results)
        assert count == 2

    def test_upsert_updates_existing(self, conn, clean_test_route):
        origin, dest = clean_test_route
        future = datetime.date.today() + datetime.timedelta(days=30)

        # Insert first
        r1 = AwardResult(origin=origin, destination=dest, date=future,
                         cabin="economy", award_type="Saver", miles=13000, taxes_cents=6851)
        upsert_availability(conn, [r1])

        # Upsert with updated miles
        r2 = AwardResult(origin=origin, destination=dest, date=future,
                         cabin="economy", award_type="Saver", miles=15000, taxes_cents=7000)
        upsert_availability(conn, [r2])

        # Verify updated
        rows = get_route_summary(conn, origin, dest)
        econ_saver = [r for r in rows if r["cabin"] == "economy" and r["award_type"] == "Saver"]
        assert len(econ_saver) == 1
        assert econ_saver[0]["miles"] == 15000
        assert econ_saver[0]["taxes_cents"] == 7000

    def test_upsert_empty_list(self, conn):
        count = upsert_availability(conn, [])
        assert count == 0

    def test_different_award_types_not_conflicting(self, conn, clean_test_route):
        origin, dest = clean_test_route
        future = datetime.date.today() + datetime.timedelta(days=30)

        results = [
            AwardResult(origin=origin, destination=dest, date=future,
                        cabin="economy", award_type="Saver", miles=13000, taxes_cents=6851),
            AwardResult(origin=origin, destination=dest, date=future,
                        cabin="economy", award_type="Standard", miles=22500, taxes_cents=6851),
        ]
        upsert_availability(conn, results)

        rows = get_route_summary(conn, origin, dest)
        econ = [r for r in rows if r["cabin"] == "economy"]
        assert len(econ) == 2  # Saver and Standard are distinct


# ---------------------------------------------------------------------------
# Job tracking
# ---------------------------------------------------------------------------


class TestJobTracking:
    def test_record_completed_job(self, conn, clean_test_route):
        origin, dest = clean_test_route
        month_start = datetime.date.today()
        record_scrape_job(conn, origin, dest, month_start, "completed",
                          solutions_found=28, solutions_stored=26, solutions_rejected=2)

        with conn.cursor() as cur:
            cur.execute("SELECT status, solutions_found, solutions_stored, solutions_rejected "
                        "FROM scrape_jobs WHERE origin = %s AND destination = %s",
                        (origin, dest))
            row = cur.fetchone()
        assert row[0] == "completed"
        assert row[1] == 28
        assert row[2] == 26
        assert row[3] == 2

    def test_record_failed_job(self, conn, clean_test_route):
        origin, dest = clean_test_route
        month_start = datetime.date.today()
        record_scrape_job(conn, origin, dest, month_start, "failed",
                          error="HTTP 403 Cloudflare block")

        with conn.cursor() as cur:
            cur.execute("SELECT status, error FROM scrape_jobs WHERE origin = %s AND destination = %s",
                        (origin, dest))
            row = cur.fetchone()
        assert row[0] == "failed"
        assert "403" in row[1]


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


class TestQueries:
    def test_get_route_summary(self, conn, clean_test_route):
        origin, dest = clean_test_route
        future = datetime.date.today() + datetime.timedelta(days=30)
        results = [
            AwardResult(origin=origin, destination=dest, date=future,
                        cabin="economy", award_type="Saver", miles=13000, taxes_cents=6851),
        ]
        upsert_availability(conn, results)

        rows = get_route_summary(conn, origin, dest)
        assert len(rows) == 1
        assert rows[0]["miles"] == 13000
        assert rows[0]["cabin"] == "economy"

    def test_get_route_summary_empty(self, conn):
        rows = get_route_summary(conn, "ZZZ", "ZZZ")
        assert rows == []

    def test_get_scrape_stats(self, conn, clean_test_route):
        origin, dest = clean_test_route
        future = datetime.date.today() + datetime.timedelta(days=30)
        results = [
            AwardResult(origin=origin, destination=dest, date=future,
                        cabin="economy", award_type="Saver", miles=13000, taxes_cents=6851),
        ]
        upsert_availability(conn, results)

        stats = get_scrape_stats(conn)
        assert stats["total_rows"] >= 1
        assert stats["routes_covered"] >= 1


# ---------------------------------------------------------------------------
# Live data verification (existing 922 records from scrape run)
# ---------------------------------------------------------------------------


class TestLiveData:
    """Verify the data already stored from the scrape run."""

    def test_has_stored_data(self, conn):
        stats = get_scrape_stats(conn)
        assert stats["total_rows"] > 0, "Database should have records from scrape run"

    def test_date_range_reasonable(self, conn):
        stats = get_scrape_stats(conn)
        if stats["date_range_start"] and stats["date_range_end"]:
            start = stats["date_range_start"]
            end = stats["date_range_end"]
            span = (end - start).days
            assert span > 100, f"Date range should span months, got {span} days"

    def test_all_cabins_represented(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT cabin FROM availability")
            cabins = {row[0] for row in cur.fetchall()}
        # At minimum economy and business should be present
        assert "economy" in cabins, f"Economy missing, got {cabins}"
        assert "business" in cabins, f"Business missing, got {cabins}"

    def test_all_award_types_present(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT award_type FROM availability")
            types = {row[0] for row in cur.fetchall()}
        assert "Saver" in types
        assert "Standard" in types

    def test_no_zero_mile_records(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM availability WHERE miles <= 0")
            bad_count = cur.fetchone()[0]
        assert bad_count == 0, f"Found {bad_count} records with miles <= 0"

    def test_no_negative_taxes(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM availability WHERE taxes_cents < 0")
            bad_count = cur.fetchone()[0]
        assert bad_count == 0, f"Found {bad_count} records with negative taxes"

    def test_miles_in_reasonable_range(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT MIN(miles), MAX(miles) FROM availability")
            min_miles, max_miles = cur.fetchone()
        assert min_miles > 0, f"Min miles should be > 0, got {min_miles}"
        assert max_miles <= 500000, f"Max miles should be <= 500k, got {max_miles}"

    def test_unique_dates_coverage(self, conn):
        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(DISTINCT date) FROM availability")
            unique_dates = cur.fetchone()[0]
        assert unique_dates > 50, f"Should cover many dates, got {unique_dates}"
