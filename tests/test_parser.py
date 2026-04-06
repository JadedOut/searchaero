"""Tests for united_api.parse_calendar_solutions — parser logic with synthetic data."""

import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts", "experiments"))

from united_api import parse_calendar_solutions, CABIN_TYPE_MAP


def _make_day(date_value, solutions):
    """Build a synthetic Day object matching the API schema."""
    return {
        "DateValue": date_value,
        "DayNotInThisMonth": False,
        "Solutions": solutions,
    }


def _make_solution(cabin_type, award_type, miles, taxes_usd):
    return {
        "CabinType": cabin_type,
        "AwardType": award_type,
        "Prices": [
            {"Currency": "MILES", "Amount": miles},
            {"Currency": "USD", "Amount": taxes_usd},
        ],
    }


def _wrap_calendar(days):
    """Wrap days into the full calendar response structure."""
    return {
        "data": {
            "Calendar": {
                "Months": [
                    {
                        "Weeks": [
                            {"Days": days}
                        ]
                    }
                ]
            }
        }
    }


class TestParseCalendarSolutions:
    def test_single_day_single_solution(self):
        response = _wrap_calendar([
            _make_day("04/15/2026", [
                _make_solution("MIN-ECONOMY-SURP-OR-DISP", "Saver", 13000.0, 68.51),
            ]),
        ])
        results = parse_calendar_solutions(response)
        assert len(results) == 1
        assert results[0]["date"] == "04/15/2026"
        assert results[0]["cabin"] == "economy"
        assert results[0]["award_type"] == "Saver"
        assert results[0]["miles"] == 13000.0
        assert results[0]["taxes_usd"] == 68.51

    def test_multiple_cabins_per_day(self):
        response = _wrap_calendar([
            _make_day("04/15/2026", [
                _make_solution("MIN-ECONOMY-SURP-OR-DISP", "Saver", 13000.0, 68.51),
                _make_solution("MIN-BUSINESS-SURP-OR-DISP", "Saver", 30000.0, 68.51),
                _make_solution("MIN-FIRST-SURP-OR-DISP", "Saver", 75000.0, 68.51),
            ]),
        ])
        results = parse_calendar_solutions(response)
        assert len(results) == 3
        cabins = {r["cabin"] for r in results}
        assert cabins == {"economy", "business", "first"}

    def test_saver_and_standard(self):
        response = _wrap_calendar([
            _make_day("04/15/2026", [
                _make_solution("MIN-ECONOMY-SURP-OR-DISP", "Saver", 13000.0, 68.51),
                _make_solution("MIN-ECONOMY-SURP-OR-DISP", "Standard", 22500.0, 68.51),
            ]),
        ])
        results = parse_calendar_solutions(response)
        assert len(results) == 2
        types = {r["award_type"] for r in results}
        assert types == {"Saver", "Standard"}

    def test_skip_padding_days(self):
        days = [
            {"DateValue": "", "DayNotInThisMonth": True, "Solutions": []},
            _make_day("04/01/2026", [
                _make_solution("MIN-ECONOMY-SURP-OR-DISP", "Saver", 13000.0, 68.51),
            ]),
        ]
        response = _wrap_calendar(days)
        results = parse_calendar_solutions(response)
        assert len(results) == 1

    def test_empty_calendar(self):
        response = {"data": {"Calendar": {"Months": []}}}
        results = parse_calendar_solutions(response)
        assert results == []

    def test_cabin_type_mapping(self):
        for raw_type, expected_name in CABIN_TYPE_MAP.items():
            response = _wrap_calendar([
                _make_day("04/15/2026", [
                    _make_solution(raw_type, "Saver", 10000.0, 50.0),
                ]),
            ])
            results = parse_calendar_solutions(response)
            assert results[0]["cabin"] == expected_name, f"{raw_type} should map to {expected_name}"

    def test_unknown_cabin_type_preserved(self):
        response = _wrap_calendar([
            _make_day("04/15/2026", [
                _make_solution("UNKNOWN-CABIN-TYPE", "Saver", 10000.0, 50.0),
            ]),
        ])
        results = parse_calendar_solutions(response)
        assert results[0]["cabin"] == "UNKNOWN-CABIN-TYPE"

    def test_multiple_days(self):
        response = _wrap_calendar([
            _make_day("04/15/2026", [
                _make_solution("MIN-ECONOMY-SURP-OR-DISP", "Saver", 13000.0, 68.51),
            ]),
            _make_day("04/16/2026", [
                _make_solution("MIN-ECONOMY-SURP-OR-DISP", "Saver", 16700.0, 68.51),
            ]),
        ])
        results = parse_calendar_solutions(response)
        assert len(results) == 2
        dates = {r["date"] for r in results}
        assert dates == {"04/15/2026", "04/16/2026"}
