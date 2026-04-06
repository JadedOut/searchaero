"""Tests for core.models — AwardResult dataclass and validation logic."""

import datetime
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from core.models import (
    AwardResult,
    validate_solution,
    validate_iata_code,
    VALID_CABINS,
    VALID_AWARD_TYPES,
)


# ---------------------------------------------------------------------------
# validate_iata_code
# ---------------------------------------------------------------------------


class TestValidateIataCode:
    def test_valid_codes(self):
        assert validate_iata_code("YYZ")
        assert validate_iata_code("LAX")
        assert validate_iata_code("SFO")

    def test_lowercase_rejected(self):
        assert not validate_iata_code("yyz")

    def test_mixed_case_rejected(self):
        assert not validate_iata_code("Yyz")

    def test_too_short(self):
        assert not validate_iata_code("YY")

    def test_too_long(self):
        assert not validate_iata_code("YYZZ")

    def test_digits_rejected(self):
        assert not validate_iata_code("Y1Z")

    def test_empty_string(self):
        assert not validate_iata_code("")


# ---------------------------------------------------------------------------
# validate_solution — valid inputs
# ---------------------------------------------------------------------------


def _make_raw(overrides=None):
    """Build a valid raw solution dict for testing."""
    today = datetime.date.today()
    future_date = today + datetime.timedelta(days=30)
    base = {
        "date": future_date.strftime("%m/%d/%Y"),
        "cabin": "economy",
        "award_type": "Saver",
        "miles": 13000.0,
        "taxes_usd": 68.51,
    }
    if overrides:
        base.update(overrides)
    return base


class TestValidateSolutionValid:
    def test_valid_economy_saver(self):
        result, reason = validate_solution(_make_raw(), "YYZ", "LAX")
        assert result is not None
        assert reason is None
        assert isinstance(result, AwardResult)
        assert result.origin == "YYZ"
        assert result.destination == "LAX"
        assert result.cabin == "economy"
        assert result.award_type == "Saver"
        assert result.miles == 13000
        assert result.taxes_cents == 6851

    def test_valid_business_standard(self):
        raw = _make_raw({"cabin": "business", "award_type": "Standard", "miles": 65000.0})
        result, reason = validate_solution(raw, "YVR", "SFO")
        assert result is not None
        assert result.cabin == "business"
        assert result.award_type == "Standard"
        assert result.miles == 65000

    def test_all_valid_cabins(self):
        for cabin in VALID_CABINS:
            raw = _make_raw({"cabin": cabin})
            result, reason = validate_solution(raw, "YYZ", "LAX")
            assert result is not None, f"Cabin {cabin} should be valid"

    def test_all_valid_award_types(self):
        for award_type in VALID_AWARD_TYPES:
            raw = _make_raw({"award_type": award_type})
            result, reason = validate_solution(raw, "YYZ", "LAX")
            assert result is not None, f"Award type {award_type} should be valid"

    def test_taxes_zero(self):
        raw = _make_raw({"taxes_usd": 0.0})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is not None
        assert result.taxes_cents == 0

    def test_miles_boundary_low(self):
        raw = _make_raw({"miles": 1.0})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is not None
        assert result.miles == 1

    def test_miles_boundary_high(self):
        raw = _make_raw({"miles": 500000.0})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is not None
        assert result.miles == 500000

    def test_date_today(self):
        today = datetime.date.today()
        raw = _make_raw({"date": today.strftime("%m/%d/%Y")})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is not None

    def test_date_max_window(self):
        max_date = datetime.date.today() + datetime.timedelta(days=337)
        raw = _make_raw({"date": max_date.strftime("%m/%d/%Y")})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is not None


# ---------------------------------------------------------------------------
# validate_solution — rejections
# ---------------------------------------------------------------------------


class TestValidateSolutionRejections:
    def test_reject_invalid_origin(self):
        result, reason = validate_solution(_make_raw(), "yyz", "LAX")
        assert result is None
        assert "origin" in reason.lower() or "IATA" in reason

    def test_reject_invalid_destination(self):
        result, reason = validate_solution(_make_raw(), "YYZ", "la")
        assert result is None
        assert "destination" in reason.lower() or "IATA" in reason

    def test_reject_bad_date_format(self):
        raw = _make_raw({"date": "2026-04-15"})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None
        assert "date" in reason.lower()

    def test_reject_past_date(self):
        yesterday = datetime.date.today() - datetime.timedelta(days=1)
        raw = _make_raw({"date": yesterday.strftime("%m/%d/%Y")})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None
        assert "past" in reason.lower()

    def test_reject_date_beyond_window(self):
        far_future = datetime.date.today() + datetime.timedelta(days=338)
        raw = _make_raw({"date": far_future.strftime("%m/%d/%Y")})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None
        assert "337" in reason or "beyond" in reason.lower()

    def test_reject_unknown_cabin(self):
        raw = _make_raw({"cabin": "ultra_first"})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None
        assert "cabin" in reason.lower()

    def test_reject_unknown_award_type(self):
        raw = _make_raw({"award_type": "Premium"})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None
        assert "award" in reason.lower()

    def test_reject_zero_miles(self):
        raw = _make_raw({"miles": 0})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None
        assert "miles" in reason.lower() or "positive" in reason.lower()

    def test_reject_negative_miles(self):
        raw = _make_raw({"miles": -100})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None

    def test_reject_miles_over_max(self):
        raw = _make_raw({"miles": 500001})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None
        assert "500,000" in reason or "500000" in reason

    def test_reject_negative_taxes(self):
        raw = _make_raw({"taxes_usd": -1.0})
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None
        assert "tax" in reason.lower() or "negative" in reason.lower()

    def test_reject_missing_date(self):
        raw = _make_raw()
        del raw["date"]
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None

    def test_reject_missing_miles(self):
        raw = _make_raw()
        del raw["miles"]
        result, reason = validate_solution(raw, "YYZ", "LAX")
        assert result is None


# ---------------------------------------------------------------------------
# AwardResult dataclass
# ---------------------------------------------------------------------------


class TestAwardResult:
    def test_default_scraped_at(self):
        today = datetime.date.today() + datetime.timedelta(days=30)
        r = AwardResult(
            origin="YYZ", destination="LAX", date=today,
            cabin="economy", award_type="Saver", miles=13000, taxes_cents=6851,
        )
        assert r.scraped_at is not None
        assert r.scraped_at.tzinfo is not None  # timezone-aware

    def test_fields_stored(self):
        today = datetime.date.today() + datetime.timedelta(days=30)
        r = AwardResult(
            origin="YVR", destination="SFO", date=today,
            cabin="business", award_type="Standard", miles=30000, taxes_cents=5000,
        )
        assert r.origin == "YVR"
        assert r.destination == "SFO"
        assert r.miles == 30000
