"""Cabinet contract: tickets_by_tariff response keys are decimal strings."""

from __future__ import annotations

from app.services.raffle.service import tickets_by_tariff_for_api


def test_tickets_by_tariff_for_api_stringifies_keys():
    assert tickets_by_tariff_for_api({1: 2, '3': 4}) == {'1': 2, '3': 4}


def test_tickets_by_tariff_for_api_empty_and_invalid():
    assert tickets_by_tariff_for_api(None) is None
    assert tickets_by_tariff_for_api({}) is None
    assert tickets_by_tariff_for_api('nope') is None
    assert tickets_by_tariff_for_api({0: 1, -1: 2, 'x': 3}) is None
