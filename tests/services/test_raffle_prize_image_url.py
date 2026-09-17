"""Prize slot image_url normalization."""

from __future__ import annotations

import pytest

from app.services.raffle.service import _normalize_image_url, _normalize_prize_slots


def test_normalize_image_url_https_and_path():
    assert _normalize_image_url('https://cdn.example.com/p.jpg') == 'https://cdn.example.com/p.jpg'
    assert _normalize_image_url('/media/prizes/ford.png') == '/media/prizes/ford.png'
    assert _normalize_image_url('  ') is None
    assert _normalize_image_url(None) is None


def test_normalize_image_url_rejects_http_and_protocol_relative():
    assert _normalize_image_url('http://evil.example/x') is None
    assert _normalize_image_url('//evil.example/x') is None
    with pytest.raises(ValueError):
        _normalize_image_url('http://evil.example/x', strict=True)


def test_normalize_prize_slots_keeps_image_url():
    slots = _normalize_prize_slots(
        [
            {
                'place': 2,
                'prize_type': 'custom',
                'prize_text': 'Деньги',
                'image_url': 'https://cdn.example.com/money.png',
            },
            {'place': 1, 'prize_type': 'custom', 'prize_text': 'Форд', 'image_url': '/img/ford.jpg'},
        ]
    )
    assert [s['place'] for s in slots] == [1, 2]
    assert slots[0]['image_url'] == '/img/ford.jpg'
    assert slots[1]['image_url'] == 'https://cdn.example.com/money.png'
