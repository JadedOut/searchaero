"""Aeroplan air-calendars transport/parse module (Phase 2).

Pure functions lifted VERBATIM from the proven recon spike
(`scripts/experiments/aeroplan_calendar_recon.py`):

  - build_availability_url — build the outbound-availability URL (with optional
    flexibility injection).
  - parse_calendar_response — parse an air-calendars response body into per-date
    cheapest-economy fares.
  - _cheapest_economy — min miles over a single data entry's unitPrices.
  - extract_sent_flexibility — read the SPA's actual request-body flexibility.
  - redact_card_numbers — defensively mask card/member/userId fields.

Plus a NEW window-stepping helper:

  - window_dates — generate 5-day window-start dates with range/month filtering,
    mirroring scrape.py::scrape_route's window generation semantics.

This module is PURE: it imports with ZERO side effects (no browser, no I/O at
import time) and never imports Playwright.
"""

import json
import logging
from datetime import date, datetime, timedelta
from urllib.parse import urlencode

log = logging.getLogger(__name__)

# ----------------------------------------------------------------------------
# Constants
# ----------------------------------------------------------------------------

# Availability URL host + path family (recon §3 / transport spike).
AVAILABILITY_HOST = "https://www.aircanada.com"
AVAILABILITY_PATH = "/aeroplan/redeem/availability/outbound"

# Date format used by the URL builder and all window arithmetic.
DATE_FMT = "%Y-%m-%d"


# ----------------------------------------------------------------------------
# Availability URL builder (from recon path family) + flexibility injection
# ----------------------------------------------------------------------------

def build_availability_url(org: str, dest: str, date: str,
                           flexibility: int = None) -> str:
    """Build the Aeroplan outbound-availability URL from org/dest/date.

    Standard params (recon §3 / transport spike):
      /aeroplan/redeem/availability/outbound
        ?org0=YYZ&dest0=LAX&departureDate0=2026-08-15&lang=en-CA&tripType=O
         &ADT=1&YTH=0&CHD=0&INF=0&INS=0&marketCode=DOM

    When `flexibility` is set, INJECT three candidate param names so we can test
    empirically whether the SPA honors any of them: `flexibility`, `flex`,
    `calendarFlexibility`. The SPA may ignore all three (its real flexibility
    lives in the POST body, which we capture separately) — that null result is
    itself the answer.
    """
    params = {
        "org0": org,
        "dest0": dest,
        "departureDate0": date,
        "lang": "en-CA",
        "tripType": "O",
        "ADT": "1",
        "YTH": "0",
        "CHD": "0",
        "INF": "0",
        "INS": "0",
        "marketCode": "DOM",
    }
    if flexibility is not None:
        # Candidate injection — three plausible param spellings at once.
        params["flexibility"] = str(flexibility)
        params["flex"] = str(flexibility)
        params["calendarFlexibility"] = str(flexibility)
    return f"{AVAILABILITY_HOST}{AVAILABILITY_PATH}?{urlencode(params)}"


# ----------------------------------------------------------------------------
# air-calendars RESPONSE parsing
# ----------------------------------------------------------------------------

def parse_calendar_response(body) -> dict:
    """Parse an air-calendars RESPONSE body into per-date cheapest-economy fares.

    Response shape (recon §3): `{ data[], meta, dictionaries }`, one `data` entry
    per `departureDate`. The cheapest economy fare for a date is the MIN over
    `prices.unitPrices[].milesConversion.convertedMiles.base`, carrying along its
    sibling `totalTaxes` (cents CAD).

    Defensive: any missing entry/path yields a null fare for that date rather
    than raising. Returns:
        {
          "returned_dates": [sorted date strings],
          "day_count": int,
          "window_min": str|None,
          "window_max": str|None,
          "fares": { date: {"miles": int|None, "taxes_cents": int|None} },
        }
    """
    fares = {}
    try:
        data = body.get("data") if isinstance(body, dict) else None
    except Exception:
        data = None
    if not isinstance(data, list):
        data = []

    for entry in data:
        if not isinstance(entry, dict):
            continue
        date = entry.get("departureDate")
        if not isinstance(date, str):
            continue
        miles, taxes = _cheapest_economy(entry)
        # If a date appears twice, keep the cheaper miles.
        existing = fares.get(date)
        if existing is None:
            fares[date] = {"miles": miles, "taxes_cents": taxes}
        elif miles is not None and (
                existing["miles"] is None or miles < existing["miles"]):
            fares[date] = {"miles": miles, "taxes_cents": taxes}

    returned_dates = sorted(fares.keys())
    return {
        "returned_dates": returned_dates,
        "day_count": len(returned_dates),
        "window_min": returned_dates[0] if returned_dates else None,
        "window_max": returned_dates[-1] if returned_dates else None,
        "fares": fares,
    }


def _cheapest_economy(entry: dict):
    """Min miles over prices.unitPrices[].milesConversion.convertedMiles.base for
    one data entry, with the sibling totalTaxes. Returns (miles, taxes_cents),
    either of which may be None if the paths are missing/malformed.
    """
    best_miles = None
    best_taxes = None
    try:
        prices = entry.get("prices") if isinstance(entry, dict) else None
        unit_prices = prices.get("unitPrices") if isinstance(prices, dict) else None
        if not isinstance(unit_prices, list):
            return (None, None)
        for up in unit_prices:
            if not isinstance(up, dict):
                continue
            mc = up.get("milesConversion")
            if not isinstance(mc, dict):
                continue
            cm = mc.get("convertedMiles")
            if not isinstance(cm, dict):
                continue
            base = cm.get("base")
            if not isinstance(base, (int, float)):
                continue
            taxes = cm.get("totalTaxes")
            if best_miles is None or base < best_miles:
                best_miles = base
                best_taxes = taxes if isinstance(taxes, (int, float)) else None
    except Exception:
        log.debug("cheapest-economy parse failed", exc_info=True)
        return (None, None)
    return (best_miles, best_taxes)


# ----------------------------------------------------------------------------
# air-calendars REQUEST parsing (read the SPA's actual flexibility)
# ----------------------------------------------------------------------------

def extract_sent_flexibility(request) -> dict:
    """Read the SPA's ACTUAL air-calendars REQUEST body to find the flexibility
    it sent (recon §3: `itineraries[0].flexibility`).

    Tries `request.post_data_json` first, then falls back to `post_data` parsed
    via json.loads. Returns:
        { "sent_flexibility": int|None, "request_body": dict|None }
    The raw (UN-redacted) body is returned for the caller to redact before any
    on-disk persistence. Best-effort; never raises.
    """
    body = None
    try:
        body = request.post_data_json
    except Exception:
        body = None
    if body is None:
        try:
            raw = request.post_data
            if raw:
                body = json.loads(raw)
        except Exception:
            body = None

    sent = None
    try:
        if isinstance(body, dict):
            itins = body.get("itineraries")
            if isinstance(itins, list) and itins and isinstance(itins[0], dict):
                flex = itins[0].get("flexibility")
                if isinstance(flex, (int, float)):
                    sent = flex
    except Exception:
        log.debug("flexibility extraction failed", exc_info=True)
    return {"sent_flexibility": sent, "request_body": body if isinstance(body, dict) else None}


# ----------------------------------------------------------------------------
# Redaction
# ----------------------------------------------------------------------------

def redact_card_numbers(obj):
    """Defensively mask the Aeroplan card / member number in a captured payload.

    Masks any value under a `frequentFlyer.cardNumber` key, and any key that
    LOOKS like a card-number / member-number field (cardNumber, aeroplanNumber,
    memberNumber, userId, etc.) anywhere in the tree. A live run leaked
    `"userId": "523781508"` (the Aeroplan number), so `userId` is explicitly
    covered. Recurses through dicts/lists; returns a NEW structure.
    """
    card_key_markers = ("cardnumber", "card_number", "aeroplannumber",
                        "membernumber", "membershipnumber", "ffp", "loyaltynumber",
                        "userid")

    def _looks_like_card_key(key: str) -> bool:
        k = key.lower().replace(" ", "").replace("_", "")
        return any(marker.replace("_", "") in k for marker in card_key_markers)

    def _walk(node, parent_key=None):
        if isinstance(node, dict):
            out = {}
            for k, v in node.items():
                if isinstance(k, str) and _looks_like_card_key(k) and not isinstance(v, (dict, list)):
                    out[k] = "***REDACTED***"
                elif parent_key == "frequentFlyer" and k == "cardNumber":
                    out[k] = "***REDACTED***"
                else:
                    out[k] = _walk(v, parent_key=k)
            return out
        if isinstance(node, list):
            return [_walk(item, parent_key=parent_key) for item in node]
        return node

    return _walk(obj)


# ----------------------------------------------------------------------------
# Window stepping (5-day windows with range/month filtering)
# ----------------------------------------------------------------------------

def window_dates(base_date=None, *, from_date=None, to_date=None, months=None,
                 step_days=5, max_windows=12):
    """Generate 5-day window-start dates with range/month filtering.

    Mirrors the window generation/filtering of scrape.py::scrape_route, but with
    `step_days`-day windows (each window covers `step_days` days starting at the
    window's start date) instead of 30-day windows.

    Args:
        base_date: Base/start date (YYYY-MM-DD ISO string). If None, defaults to
            today (date.today()).
        from_date: Optional start date (YYYY-MM-DD) — keep windows that OVERLAP
            [from_date, ...], i.e. window_start + step_days >= from_date.
        to_date: Optional end date (YYYY-MM-DD) — keep windows where
            window_start <= to_date.
        months: Optional list of ints (1-12) — keep only windows whose start-date
            month is in this list.
        step_days: Days between window starts AND days each window covers.
        max_windows: Max number of window-start dates to generate before filters.

    Returns:
        Filtered list of ISO date strings (YYYY-MM-DD); may be empty.
    """
    if base_date is None:
        base = date.today()
    else:
        base = date.fromisoformat(base_date)

    depart_dates = [(base + timedelta(days=step_days * i)).strftime(DATE_FMT)
                    for i in range(max_windows)]

    # Filter by month numbers (e.g., [6, 7, 12] for June, July, December).
    if months:
        depart_dates = [d for d in depart_dates
                        if date.fromisoformat(d).month in months]

    # Filter by date range — keep windows that overlap with [from_date, to_date].
    # Each window covers `step_days` days starting from the window start date.
    if from_date:
        to_cutoff = date.fromisoformat(from_date)
        depart_dates = [d for d in depart_dates
                        if date.fromisoformat(d) + timedelta(days=step_days) >= to_cutoff]
    if to_date:
        from_cutoff = date.fromisoformat(to_date)
        depart_dates = [d for d in depart_dates
                        if date.fromisoformat(d) <= from_cutoff]

    return depart_dates
