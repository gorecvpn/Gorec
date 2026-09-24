"""Raffle: tickets = months in the purchased period (round(days / 30), min 1)."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import RaffleCampaignStatus
from app.services.raffle import service as raffle_service


@pytest.mark.parametrize(
    ('days', 'months'),
    [
        (30, 1),
        (31, 1),
        (90, 3),
        (180, 6),
        (365, 12),
        (360, 12),
        (60, 2),
        (45, 2),
        (14, 1),
        (1, 1),
        (0, 1),
        (None, 1),
        (-5, 1),
    ],
)
def test_months_in_period(days, months):
    assert raffle_service.months_in_period(days) == months


@pytest.mark.parametrize(
    ('description', 'days'),
    [
        ('Подписка на 90 дней (3 мес)', 90),
        ("Покупка тарифа 'Premium' на 180 дней", 180),
        ('Продление тарифа Pro на 30 дней', 30),
        ('Автопродление истёкшей подписки на 365 дней', 365),
        ('Покупка тарифа Pro на 1 день', 1),
        ('Покупка подписки через лендинг (Pro 3, 90 дн.)', 90),
        ("Переход с суточного на тариф 'Pro' (30 дней)", 30),
        ('Renewal for 60 days', 60),
        ('Изменение устройств с 1 до 3 за 200 дн.', None),
        ('Переключение трафика с 50GB на 100GB за 120 дн.', None),
        ('Добавление стран к подписке: DE за 90 дн.', None),
        ("Переход на тариф 'Pro' (доплата за 120 дней)", None),
        ('Суточная оплата тарифа «Pro»', None),
        ('Автопродление Lava', None),
        ('', None),
        (None, None),
    ],
)
def test_period_days_from_description(description, days):
    assert raffle_service.period_days_from_description(description) == days


def _campaign(**overrides):
    data = {
        'id': 7,
        'name': 'Test raffle',
        'status': RaffleCampaignStatus.ACTIVE.value,
        'max_winners': 1,
        'prize_type': 'custom',
        'prize_value': None,
        'prize_text': 'Prize',
        'prize_slots': None,
        'tickets_per_purchase': 1,
        'tickets_by_tariff': None,
        'skip_trial_purchases': True,
        'tickets_per_month': True,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.mark.parametrize(('days', 'expected'), [(30, 1), (31, 1), (90, 3), (180, 6), (365, 12), (None, 1)])
def test_tickets_for_period_default(days, expected):
    assert raffle_service.tickets_for_period(_campaign(), None, days) == (expected, expected)


def test_tickets_for_period_per_tariff_is_per_month():
    campaign = _campaign(tickets_by_tariff={'3': 2}, tickets_per_purchase=1)
    assert raffle_service.tickets_for_period(campaign, 3, 180) == (12, 6)
    assert raffle_service.tickets_for_period(campaign, 4, 180) == (6, 6)


def test_tickets_for_period_toggle_off_keeps_flat_count():
    campaign = _campaign(tickets_per_month=False, tickets_by_tariff={'3': 2})
    assert raffle_service.tickets_for_period(campaign, 3, 365) == (2, 0)
    assert raffle_service.tickets_for_period(campaign, None, 365) == (1, 0)


def test_tickets_for_period_hard_ceiling():
    campaign = _campaign(tickets_per_purchase=50)
    tickets, months = raffle_service.tickets_for_period(campaign, None, 3650)
    assert months == 122
    assert tickets == raffle_service.MAX_TICKETS_PER_PURCHASE_BATCH


def test_ticket_grant_headline_plurals():
    assert raffle_service.ticket_grant_headline(6, 6) == 'Вы получили <b>6</b> билетов за подписку на 6 месяцев'
    assert raffle_service.ticket_grant_headline(1, 1) == 'Вы получили <b>1</b> билет за подписку на 1 месяц'
    assert raffle_service.ticket_grant_headline(3, 3) == 'Вы получили <b>3</b> билета за подписку на 3 месяца'
    assert raffle_service.ticket_grant_headline(12, 12) == 'Вы получили <b>12</b> билетов за подписку на 12 месяцев'


def _stub_db(tx=None) -> SimpleNamespace:
    db = SimpleNamespace()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.flush = AsyncMock()
    db.refresh = AsyncMock()
    db.rollback = AsyncMock()
    db.get = AsyncMock(return_value=tx)

    @asynccontextmanager
    async def _begin_nested():
        yield

    db.begin_nested = _begin_nested
    return db


def _wire(monkeypatch, campaign, *, per_user_cap=0, per_payment_cap=0, user_have=0):
    monkeypatch.setattr(
        raffle_service,
        'settings',
        SimpleNamespace(
            is_raffle_enabled=lambda: True,
            get_raffle_max_tickets_per_user=lambda: per_user_cap,
            get_raffle_max_tickets_per_payment=lambda: per_payment_cap,
        ),
    )
    created = []

    async def _create(db, **kwargs):
        ticket = SimpleNamespace(id=len(created) + 1, **kwargs)
        created.append(ticket)
        return ticket

    monkeypatch.setattr(raffle_service.raffle_crud, 'get_current_active_campaign', AsyncMock(return_value=campaign))
    monkeypatch.setattr(raffle_service.raffle_crud, 'list_tickets_by_campaign_tx', AsyncMock(return_value=[]))
    monkeypatch.setattr(raffle_service.raffle_crud, 'count_tickets_for_user', AsyncMock(return_value=user_have))
    monkeypatch.setattr(raffle_service.raffle_crud, 'create_ticket', AsyncMock(side_effect=_create))
    notify = AsyncMock(return_value=None)
    monkeypatch.setattr(raffle_service, '_notify_user_ticket', notify)
    return notify


def _paid_tx(description: str) -> SimpleNamespace:
    return SimpleNamespace(amount_kopeks=-59900, description=description, external_id=None, payment_method='balance')


@pytest.mark.parametrize(('days', 'expected'), [(30, 1), (90, 3), (180, 6), (365, 12)])
async def test_issue_for_purchase_grants_months_from_description(monkeypatch, days, expected):
    notify = _wire(monkeypatch, _campaign())
    db = _stub_db(_paid_tx(f'Подписка на {days} дней'))
    result = await raffle_service.issue_for_purchase(db, user_id=42, transaction_id=500 + days, tariff_id=1)
    assert len(result) == expected
    assert notify.await_args.kwargs == {'tickets_count': expected, 'months': expected}


async def test_issue_for_purchase_explicit_period_days_wins(monkeypatch):
    _wire(monkeypatch, _campaign())
    db = _stub_db(_paid_tx('Автопродление Lava'))
    result = await raffle_service.issue_for_purchase(db, user_id=42, transaction_id=901, period_days=180)
    assert len(result) == 6


async def test_issue_for_purchase_addon_gets_base_count(monkeypatch):
    _wire(monkeypatch, _campaign())
    db = _stub_db(_paid_tx('Изменение устройств с 1 до 3 за 300 дн.'))
    result = await raffle_service.issue_for_purchase(db, user_id=42, transaction_id=902)
    assert len(result) == 1


async def test_issue_for_purchase_respects_caps(monkeypatch):
    _wire(monkeypatch, _campaign(), per_payment_cap=5)
    db = _stub_db(_paid_tx('Подписка на 365 дней'))
    assert len(await raffle_service.issue_for_purchase(db, user_id=42, transaction_id=903)) == 5

    _wire(monkeypatch, _campaign(), per_user_cap=10, user_have=7)
    db = _stub_db(_paid_tx('Подписка на 180 дней'))
    assert len(await raffle_service.issue_for_purchase(db, user_id=42, transaction_id=904)) == 3


async def test_issue_for_purchase_toggle_off_is_flat(monkeypatch):
    notify = _wire(monkeypatch, _campaign(tickets_per_month=False))
    db = _stub_db(_paid_tx('Подписка на 365 дней'))
    result = await raffle_service.issue_for_purchase(db, user_id=42, transaction_id=905)
    assert len(result) == 1
    assert notify.await_args.kwargs == {'tickets_count': 1, 'months': 0}
