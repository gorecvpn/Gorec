"""Idempotency of raffle issue_for_purchase on (campaign_id, source_transaction_id)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import RaffleCampaignStatus
from app.services.raffle import service as raffle_service


def _stub_db() -> SimpleNamespace:
    db = SimpleNamespace()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()

    @asynccontextmanager
    async def _begin_nested():
        yield

    db.begin_nested = _begin_nested
    return db


def _patch_settings(monkeypatch, *, enabled: bool):
    monkeypatch.setattr(
        raffle_service,
        'settings',
        SimpleNamespace(is_raffle_enabled=lambda: enabled),
    )


@pytest.fixture
def raffle_enabled(monkeypatch):
    _patch_settings(monkeypatch, enabled=True)


@pytest.fixture
def active_campaign():
    return SimpleNamespace(
        id=7,
        name='Test raffle',
        status=RaffleCampaignStatus.ACTIVE.value,
        max_winners=1,
        prize_type='custom',
        prize_value=None,
        prize_text='Prize',
    )


async def test_issue_for_purchase_disabled_returns_none(monkeypatch):
    _patch_settings(monkeypatch, enabled=False)
    result = await raffle_service.issue_for_purchase(_stub_db(), user_id=1, transaction_id=100)
    assert result is None


async def test_issue_for_purchase_no_campaign_returns_none(raffle_enabled, monkeypatch):
    monkeypatch.setattr(
        raffle_service.raffle_crud,
        'get_current_active_campaign',
        AsyncMock(return_value=None),
    )
    result = await raffle_service.issue_for_purchase(_stub_db(), user_id=1, transaction_id=100)
    assert result is None


async def test_issue_for_purchase_idempotent(raffle_enabled, active_campaign, monkeypatch):
    existing = SimpleNamespace(
        id=1,
        campaign_id=7,
        user_id=42,
        ticket_code='RAFFLE_AAAA',
        source_transaction_id=555,
    )
    monkeypatch.setattr(
        raffle_service.raffle_crud,
        'get_current_active_campaign',
        AsyncMock(return_value=active_campaign),
    )
    get_ticket = AsyncMock(return_value=existing)
    monkeypatch.setattr(
        raffle_service.raffle_crud,
        'get_ticket_by_campaign_tx',
        get_ticket,
    )
    create_ticket = AsyncMock()
    monkeypatch.setattr(
        raffle_service.raffle_crud,
        'create_ticket',
        create_ticket,
    )

    first = await raffle_service.issue_for_purchase(_stub_db(), user_id=42, transaction_id=555)
    second = await raffle_service.issue_for_purchase(_stub_db(), user_id=42, transaction_id=555)

    assert first is existing
    assert second is existing
    create_ticket.assert_not_called()
    assert get_ticket.await_count == 2


async def test_issue_for_purchase_creates_once(raffle_enabled, active_campaign, monkeypatch):
    created = SimpleNamespace(
        id=9,
        campaign_id=7,
        user_id=42,
        ticket_code='RAFFLE_BBBB',
        source_transaction_id=777,
    )
    monkeypatch.setattr(
        raffle_service.raffle_crud,
        'get_current_active_campaign',
        AsyncMock(return_value=active_campaign),
    )
    monkeypatch.setattr(
        raffle_service.raffle_crud,
        'get_ticket_by_campaign_tx',
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        raffle_service.raffle_crud,
        'create_ticket',
        AsyncMock(return_value=created),
    )
    monkeypatch.setattr(
        raffle_service,
        '_notify_user_ticket',
        AsyncMock(return_value=None),
    )

    result = await raffle_service.issue_for_purchase(
        _stub_db(), user_id=42, transaction_id=777, tariff_id=3
    )
    assert result is created
