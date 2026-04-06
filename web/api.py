"""FastAPI backend for the seataero web UI."""

import re

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from psycopg.rows import dict_row

from core.db import get_connection, get_scrape_stats

app = FastAPI(title="seataero")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

IATA_RE = re.compile(r"^[A-Z]{3}$")

CABIN_DISPLAY = {
    "economy": "Economy",
    "premium_economy": "Premium Economy",
    "business": "Business",
    "business_pure": "Business (pure)",
    "first": "First",
    "first_pure": "First (pure)",
}


def _validate_iata(code: str, field: str) -> str:
    """Validate that a string is a 3-letter uppercase IATA code."""
    if not IATA_RE.match(code):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {field}: must be exactly 3 uppercase letters (e.g. YYZ)",
        )
    return code


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.get("/api/stats")
def stats():
    with get_connection() as conn:
        raw = get_scrape_stats(conn)

    return {
        "total_rows": raw["total_rows"],
        "routes_covered": raw["routes_covered"],
        "latest_scrape": raw["latest_scrape"].isoformat() if raw["latest_scrape"] else None,
        "date_range_start": raw["date_range_start"].isoformat() if raw["date_range_start"] else None,
        "date_range_end": raw["date_range_end"].isoformat() if raw["date_range_end"] else None,
    }


@app.get("/api/search")
def search(
    origin: str = Query(..., description="3-letter IATA origin code"),
    destination: str = Query(..., description="3-letter IATA destination code"),
):
    _validate_iata(origin, "origin")
    _validate_iata(destination, "destination")

    sql = """
        SELECT date,
               MAX(scraped_at) as last_seen,
               MIN(CASE WHEN cabin IN ('economy') THEN miles END) as economy_miles,
               MIN(CASE WHEN cabin IN ('economy') THEN taxes_cents END) as economy_taxes,
               BOOL_OR(CASE WHEN cabin IN ('economy') THEN direct END) as economy_direct,
               MIN(CASE WHEN cabin IN ('premium_economy') THEN miles END) as premium_miles,
               MIN(CASE WHEN cabin IN ('premium_economy') THEN taxes_cents END) as premium_taxes,
               BOOL_OR(CASE WHEN cabin IN ('premium_economy') THEN direct END) as premium_direct,
               MIN(CASE WHEN cabin IN ('business', 'business_pure') THEN miles END) as business_miles,
               MIN(CASE WHEN cabin IN ('business', 'business_pure') THEN taxes_cents END) as business_taxes,
               BOOL_OR(CASE WHEN cabin IN ('business', 'business_pure') THEN direct END) as business_direct,
               MIN(CASE WHEN cabin IN ('first', 'first_pure') THEN miles END) as first_miles,
               MIN(CASE WHEN cabin IN ('first', 'first_pure') THEN taxes_cents END) as first_taxes,
               BOOL_OR(CASE WHEN cabin IN ('first', 'first_pure') THEN direct END) as first_direct
        FROM availability
        WHERE origin = %(origin)s AND destination = %(destination)s
          AND date >= CURRENT_DATE
        GROUP BY date
        ORDER BY date ASC
    """

    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, {"origin": origin, "destination": destination})
            rows = cur.fetchall()

    results = []
    for row in rows:
        def _cabin_obj(miles_key: str, taxes_key: str, direct_key: str):
            miles = row[miles_key]
            if miles is None:
                return None
            return {
                "miles": miles,
                "taxes_cents": row[taxes_key],
                "direct": row[direct_key],
            }

        results.append({
            "date": row["date"].isoformat(),
            "last_seen": row["last_seen"].isoformat() if row["last_seen"] else None,
            "program": "United",
            "origin": origin,
            "destination": destination,
            "economy": _cabin_obj("economy_miles", "economy_taxes", "economy_direct"),
            "premium": _cabin_obj("premium_miles", "premium_taxes", "premium_direct"),
            "business": _cabin_obj("business_miles", "business_taxes", "business_direct"),
            "first": _cabin_obj("first_miles", "first_taxes", "first_direct"),
        })

    return {
        "origin": origin,
        "destination": destination,
        "results": results,
    }


@app.get("/api/search/detail")
def search_detail(
    origin: str = Query(..., description="3-letter IATA origin code"),
    destination: str = Query(..., description="3-letter IATA destination code"),
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
):
    _validate_iata(origin, "origin")
    _validate_iata(destination, "destination")

    sql = """
        SELECT cabin, award_type, miles, taxes_cents, scraped_at, direct
        FROM availability
        WHERE origin = %(origin)s AND destination = %(destination)s AND date = %(date)s
        ORDER BY
          CASE cabin
            WHEN 'economy' THEN 1
            WHEN 'premium_economy' THEN 2
            WHEN 'business' THEN 3
            WHEN 'business_pure' THEN 4
            WHEN 'first' THEN 5
            WHEN 'first_pure' THEN 6
          END,
          miles ASC
    """

    with get_connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(sql, {"origin": origin, "destination": destination, "date": date})
            rows = cur.fetchall()

    offerings = []
    for row in rows:
        offerings.append({
            "cabin": CABIN_DISPLAY.get(row["cabin"], row["cabin"]),
            "award_type": row["award_type"],
            "miles": row["miles"],
            "taxes_cents": row["taxes_cents"],
            "scraped_at": row["scraped_at"].isoformat() if row["scraped_at"] else None,
            "direct": row["direct"],
        })

    return {
        "date": date,
        "origin": origin,
        "destination": destination,
        "offerings": offerings,
    }
