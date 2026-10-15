"""Пауза (заморозка) подписки: расчёт остатка, допуск, панельная синхронизация."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import settings
from app.database.models import SubscriptionStatus
from app.services import subscription_pause_service as pause_service
from app.services.panel_sync.liveness import is_subscription_live


NOW = datetime(2026, 10, 15, 12, 0, tzinfo=UTC)


def _sub(**overrides):
    data = {
        'id': 7,
        'user_id': 3,
        'status': SubscriptionStatus.ACTIVE.value,
        'is_trial': False,
        'is_daily_paused': False,
        'grace_session_open': False,
        'tariff': SimpleNamespace(is_daily=False, external_squad_uuid=None),
        'end_date': NOW + timedelta(days=10),
        'is_paused': False,
        'paused_at': None,
        'pause_planned_until': None,
        'pause_remaining_seconds': None,
        'remnawave_id': 501,
    }
    data.update(overrides)
    return SimpleNamespace(**data)


def _user(**overrides):
    data = {'id': 3, 'status': 'active', 'remnawave_id': 501, 'telegram_id': 42, 'language': 'ru'}
    data.update(overrides)
    return SimpleNamespace(**data)


@pytest.fixture(autouse=True)
def _pause_settings(monkeypatch):
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_ENABLED', True)
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_MAX_DAYS', 30)
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_DAY_OPTIONS', '7,14,30')
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_MIN_REMAINING_DAYS', 1)
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_LIMIT_COUNT', 3)
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_LIMIT_WINDOW_DAYS', 30)
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_EXTERNAL_SQUAD_UUID', '')


# --- допуск -----------------------------------------------------------------


@pytest.mark.parametrize(
    ('overrides', 'code'),
    [
        ({}, None),
        ({'is_trial': True}, 'trial'),
        ({'status': SubscriptionStatus.TRIAL.value}, 'trial'),
        ({'tariff': SimpleNamespace(is_daily=True, external_squad_uuid=None)}, 'daily'),
        ({'is_daily_paused': True}, 'daily'),
        ({'status': SubscriptionStatus.EXPIRED.value}, 'not_active'),
        ({'status': SubscriptionStatus.DISABLED.value}, 'not_active'),
        ({'grace_session_open': True}, 'not_active'),
        ({'end_date': NOW - timedelta(minutes=1)}, 'not_active'),
        ({'end_date': NOW + timedelta(hours=12)}, 'too_little_remaining'),
        ({'is_paused': True}, 'already_paused'),
    ],
)
def test_static_eligibility(overrides, code):
    assert pause_service.check_static_eligibility(_user(), _sub(**overrides), now=NOW) == code


def test_static_eligibility_feature_off(monkeypatch):
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_ENABLED', False)
    assert pause_service.check_static_eligibility(_user(), _sub(), now=NOW) == 'disabled'


def test_blocked_user_cannot_pause():
    assert pause_service.check_static_eligibility(_user(status='blocked'), _sub(), now=NOW) == 'user_blocked'


@pytest.mark.parametrize('days', [0, 31, -1, 'x', None])
def test_validate_days_rejects(days):
    with pytest.raises(pause_service.SubscriptionPauseError) as exc:
        pause_service.validate_days(days)
    assert exc.value.code == 'invalid_days'


def test_day_options_are_capped_by_max(monkeypatch):
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_DAY_OPTIONS', '30, 7,14, 60, abc, 0')
    assert settings.get_subscription_pause_day_options() == [7, 14, 30]
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_DAY_OPTIONS', '')
    assert settings.get_subscription_pause_day_options() == [30]


async def test_limit_reached(monkeypatch):
    monkeypatch.setattr(pause_service, 'count_recent_pauses', AsyncMock(return_value=(3, NOW + timedelta(days=5))))
    result = await pause_service.get_eligibility(MagicMock(), _user(), _sub(), now=NOW)
    assert result.allowed is False
    assert result.code == 'limit_reached'
    assert result.next_available_at == NOW + timedelta(days=5)

    admin = await pause_service.get_eligibility(MagicMock(), _user(), _sub(), now=NOW, ignore_limit=True)
    assert admin.allowed is True


async def test_limit_zero_means_unlimited(monkeypatch):
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_LIMIT_COUNT', 0)
    monkeypatch.setattr(pause_service, 'count_recent_pauses', AsyncMock(return_value=(99, NOW)))
    result = await pause_service.get_eligibility(MagicMock(), _user(), _sub(), now=NOW)
    assert result.allowed is True


# --- расчёт срока -----------------------------------------------------------


def test_pause_freezes_remaining_and_end_date():
    sub = _sub(end_date=NOW + timedelta(days=10, hours=3))
    remaining = pause_service.apply_pause_state(sub, days=14, now=NOW)

    assert remaining == int(timedelta(days=10, hours=3).total_seconds())
    assert sub.is_paused is True
    assert sub.paused_at == NOW
    assert sub.pause_planned_until == NOW + timedelta(days=14)
    # Заморожено в будущем: до конца паузы и ещё на остаток.
    assert sub.end_date == NOW + timedelta(days=24, hours=3)
    assert pause_service.remaining_days(sub) == 11  # 10 д 3 ч — вверх


def test_early_resume_returns_all_unused_days():
    sub = _sub(end_date=NOW + timedelta(days=10))
    pause_service.apply_pause_state(sub, days=30, now=NOW)

    resume_at = NOW + timedelta(days=3)
    new_end = pause_service.apply_resume_state(sub, now=resume_at)

    assert new_end == resume_at + timedelta(days=10)
    assert sub.end_date == new_end
    assert sub.is_paused is False
    assert sub.pause_planned_until is None
    assert sub.pause_remaining_seconds is None


def test_auto_resume_at_planned_end():
    sub = _sub(end_date=NOW + timedelta(days=5))
    pause_service.apply_pause_state(sub, days=7, now=NOW)
    planned = sub.pause_planned_until
    new_end = pause_service.apply_resume_state(sub, now=planned)
    assert new_end == NOW + timedelta(days=12)


# --- панель -----------------------------------------------------------------


def test_paused_subscription_is_not_live_in_panel():
    sub = _sub(is_paused=True, end_date=NOW + timedelta(days=30))
    assert is_subscription_live(_user(), sub, now=NOW) is False
    assert is_subscription_live(_user(), _sub(), now=NOW) is True


def test_payload_uses_pause_external_squad_only_while_paused(monkeypatch):
    from app.services.panel_sync.payload import _external_squad_for

    tariff = SimpleNamespace(external_squad_uuid='tariff-squad')
    assert _external_squad_for(_sub(is_paused=True), tariff) == 'tariff-squad'
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_EXTERNAL_SQUAD_UUID', 'pause-squad')
    assert _external_squad_for(_sub(is_paused=True), tariff) == 'pause-squad'
    assert _external_squad_for(_sub(is_paused=False), tariff) == 'tariff-squad'


def test_projection_does_not_touch_paused_subscription():
    from app.services.panel_sync.projection import PanelSnapshot, project_onto_subscription

    sub = _sub(
        is_paused=True,
        end_date=NOW + timedelta(days=30),
        remnawave_short_uuid='abc',
        subscription_url='u',
        subscription_crypto_link=None,
        traffic_used_gb=0.0,
        traffic_limit_gb=0,
        device_limit=1,
        connected_squads=[],
        updated_at=None,
        last_webhook_update_at=None,
        grace_tail_expire_at=None,
        grace_overlay_expire_at=None,
    )
    fields = dict.fromkeys(PanelSnapshot.__dataclass_fields__)
    fields.update(status='DISABLED', expire_at=NOW + timedelta(days=1))
    snapshot = PanelSnapshot(**fields)
    project_onto_subscription(sub, snapshot, now=NOW)
    assert sub.status == SubscriptionStatus.ACTIVE.value
    assert sub.end_date == NOW + timedelta(days=30)


class _FakeApi:
    def __init__(self):
        self.calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def update_user(self, user_id, **kwargs):
        self.calls.append(('update', user_id, kwargs))

    async def disable_user(self, user_id):
        self.calls.append(('disable', user_id))

    async def enable_user(self, user_id):
        self.calls.append(('enable', user_id))


def _patch_panel(monkeypatch, api):
    import app.services.subscription_service as subscription_service_module

    class _Service:
        is_configured = True

        def get_api_client(self):
            return api

    monkeypatch.setattr(subscription_service_module, 'SubscriptionService', _Service)
    monkeypatch.setattr(settings, 'MULTI_TARIFF_ENABLED', False, raising=False)
    monkeypatch.setattr(type(settings), 'is_multi_tariff_enabled', lambda self: False)


async def test_panel_pause_then_resume_calls(monkeypatch):
    api = _FakeApi()
    _patch_panel(monkeypatch, api)
    monkeypatch.setattr(settings, 'SUBSCRIPTION_PAUSE_EXTERNAL_SQUAD_UUID', 'pause-squad')

    sub = _sub(tariff=SimpleNamespace(is_daily=False, external_squad_uuid=None))
    pause_service.apply_pause_state(sub, days=7, now=NOW)
    assert await pause_service._panel_apply(MagicMock(), _user(), sub, paused=True) is True
    pause_service.apply_resume_state(sub, now=NOW + timedelta(days=1))
    assert await pause_service._panel_apply(MagicMock(), _user(), sub, paused=False) is True

    (upd1, dis, upd2, ena) = api.calls
    assert upd1[2]['external_squad_uuid'] == 'pause-squad'
    assert dis == ('disable', 501)
    # Своего внешнего сквада у тарифа нет — сквад «Пауза» снимается явным null.
    assert upd2[2]['external_squad_uuid'] is None
    assert upd2[2]['expire_at'] == sub.end_date
    assert ena == ('enable', 501)


async def test_panel_without_pause_squad_does_not_touch_external_squad(monkeypatch):
    api = _FakeApi()
    _patch_panel(monkeypatch, api)
    sub = _sub()
    pause_service.apply_pause_state(sub, days=7, now=NOW)
    await pause_service._panel_apply(MagicMock(), _user(), sub, paused=True)
    assert 'external_squad_uuid' not in api.calls[0][2]


# --- сценарии сервиса ---------------------------------------------------------


def _db():
    db = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    db.flush = AsyncMock()
    db.add = MagicMock()
    return db


async def test_pause_subscription_records_history(monkeypatch):
    monkeypatch.setattr(pause_service, '_lock', AsyncMock())
    monkeypatch.setattr(pause_service, 'count_recent_pauses', AsyncMock(return_value=(0, None)))
    panel = AsyncMock(return_value=True)
    monkeypatch.setattr(pause_service, '_panel_apply', panel)
    db = _db()
    sub = _sub()

    record = await pause_service.pause_subscription(db, _user(), sub, 14, now=NOW)

    assert sub.is_paused is True
    assert record.remaining_seconds == 10 * 86400
    assert record.planned_until == NOW + timedelta(days=14)
    assert record.initiator == 'user'
    db.add.assert_called_once_with(record)
    db.commit.assert_awaited()
    panel.assert_awaited_once()
    assert panel.await_args.kwargs['paused'] is True


async def test_pause_subscription_refuses_trial(monkeypatch):
    monkeypatch.setattr(pause_service, '_lock', AsyncMock())
    monkeypatch.setattr(pause_service, 'count_recent_pauses', AsyncMock(return_value=(0, None)))
    with pytest.raises(pause_service.SubscriptionPauseError) as exc:
        await pause_service.pause_subscription(_db(), _user(), _sub(is_trial=True), 7, now=NOW)
    assert exc.value.code == 'trial'


async def test_resume_subscription_closes_history(monkeypatch):
    monkeypatch.setattr(pause_service, '_lock', AsyncMock())
    record = SimpleNamespace(resumed_at=None, resume_reason=None)
    monkeypatch.setattr(pause_service, '_open_history', AsyncMock(return_value=record))
    panel = AsyncMock(return_value=True)
    monkeypatch.setattr(pause_service, '_panel_apply', panel)
    sub = _sub(end_date=NOW + timedelta(days=8))
    pause_service.apply_pause_state(sub, days=30, now=NOW)

    later = NOW + timedelta(days=2)
    new_end = await pause_service.resume_subscription(_db(), _user(), sub, now=later)

    assert new_end == later + timedelta(days=8)
    assert record.resumed_at == later
    assert record.resume_reason == 'manual'
    assert panel.await_args.kwargs['paused'] is False


async def test_resume_not_paused_raises(monkeypatch):
    monkeypatch.setattr(pause_service, '_lock', AsyncMock())
    with pytest.raises(pause_service.SubscriptionPauseError) as exc:
        await pause_service.resume_subscription(_db(), _user(), _sub(), now=NOW)
    assert exc.value.code == 'not_paused'


async def test_resume_before_purchase_is_noop_when_not_paused():
    assert await pause_service.resume_before_purchase(_db(), _sub()) is False


def test_pause_state_dict_shape():
    sub = _sub()
    pause_service.apply_pause_state(sub, days=7, now=NOW)
    data = pause_service.pause_state_dict(sub)
    assert data['is_paused'] is True
    assert data['day_options'] == [7, 14, 30]
    assert data['remaining_days'] == 10
    assert data['planned_until'] == NOW + timedelta(days=7)
