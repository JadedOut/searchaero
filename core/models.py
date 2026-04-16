"""Data models and validation for searchaero award availability."""

import datetime
import re
from dataclasses import dataclass, field
from datetime import timezone

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

VALID_CABINS = {"economy", "premium_economy", "business", "business_pure", "first", "first_pure"}

VALID_AWARD_TYPES = {"Saver", "Standard"}

CANADIAN_AIRPORTS = ["YYZ", "YVR", "YUL", "YYC", "YOW", "YEG", "YWG", "YHZ", "YQB"]

_IATA_RE = re.compile(r"^[A-Z]{3}$")


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class AwardResult:
    """A single validated award availability record."""
    origin: str
    destination: str
    date: datetime.date
    cabin: str
    award_type: str
    miles: int
    taxes_cents: int
    scraped_at: datetime.datetime = field(default_factory=lambda: datetime.datetime.now(timezone.utc))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_iata_code(code: str) -> bool:
    """Check if a string is a valid 3-letter uppercase IATA code."""
    return bool(_IATA_RE.match(code))


def validate_solution(raw: dict, origin: str, destination: str) -> tuple:
    """Validate a single parsed solution dict and convert to AwardResult.

    Args:
        raw: Dict from united_api.parse_calendar_solutions() with keys:
             date, cabin, cabin_raw, award_type, miles, taxes_usd
        origin: 3-letter IATA origin code
        destination: 3-letter IATA destination code

    Returns:
        (AwardResult, None) on success, (None, "reason string") on rejection.
    """
    # Validate IATA codes
    if not validate_iata_code(origin):
        return (None, f"Invalid origin IATA code: {origin}")
    if not validate_iata_code(destination):
        return (None, f"Invalid destination IATA code: {destination}")

    # Parse date from MM/DD/YYYY
    date_str = raw.get("date", "")
    try:
        date = datetime.datetime.strptime(date_str, "%m/%d/%Y").date()
    except (ValueError, TypeError):
        return (None, f"Invalid date format: {date_str}")

    # Validate date range
    today = datetime.date.today()
    if date < today:
        return (None, f"Date in the past: {date}")
    if (date - today).days > 337:
        return (None, f"Date beyond booking window (>337 days): {date}")

    # Validate cabin
    cabin = raw.get("cabin", "")
    if cabin not in VALID_CABINS:
        return (None, f"Unknown cabin type: {cabin}")

    # Validate award type
    award_type = raw.get("award_type", "")
    if award_type not in VALID_AWARD_TYPES:
        return (None, f"Unknown award type: {award_type}")

    # Convert and validate miles
    try:
        miles = int(float(raw.get("miles", 0)))
    except (ValueError, TypeError):
        return (None, f"Invalid miles value: {raw.get('miles')}")

    if miles <= 0:
        return (None, f"Miles must be positive, got: {miles}")
    if miles > 500_000:
        return (None, f"Miles exceeds maximum (500,000): {miles}")

    # Convert and validate taxes
    try:
        taxes_usd = float(raw.get("taxes_usd", 0))
        taxes_cents = round(taxes_usd * 100)
    except (ValueError, TypeError):
        return (None, f"Invalid taxes value: {raw.get('taxes_usd')}")

    if taxes_cents < 0:
        return (None, f"Negative taxes: ${taxes_usd}")

    return (
        AwardResult(
            origin=origin,
            destination=destination,
            date=date,
            cabin=cabin,
            award_type=award_type,
            miles=miles,
            taxes_cents=taxes_cents,
        ),
        None,
    )
