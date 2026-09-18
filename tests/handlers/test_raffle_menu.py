"""In-bot raffle main-menu handler tests."""

from __future__ import annotations

import inspect
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.handlers import raffle_menu


_handle = inspect.unwrap(raffle_menu.handle_menu_raffle)


def _make_callback() -> MagicMock:
    callback = MagicMock()
    callback.from_user = SimpleNamespace(language_code='ru')
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.message.answer = AsyncMock()
    callback.answer = AsyncMock()
    return callback


@pytest.mark.asyncio
async def test_disabled_shows_alert() -> None:
    callback = _make_callback()
    db_user = SimpleNamespace(id=1, language='ru')
    mock_settings = MagicMock()
    mock_settings.is_raffle_enabled.return_value = False
    mock_settings.DEFAULT_LANGUAGE = 'ru'

    with patch.object(raffle_menu, 'settings', mock_settings):
        await _handle(callback, db_user=db_user, db=MagicMock())

    callback.answer.assert_awaited()
    assert callback.answer.await_args.kwargs.get('show_alert') is True
    callback.message.edit_text.assert_not_awaited()


@pytest.mark.asyncio
async def test_no_campaign_shows_message_and_back() -> None:
    callback = _make_callback()
    db_user = SimpleNamespace(id=1, language='ru')
    db = MagicMock()
    mock_settings = MagicMock()
    mock_settings.is_raffle_enabled.return_value = True
    mock_settings.DEFAULT_LANGUAGE = 'ru'

    with (
        patch.object(raffle_menu, 'settings', mock_settings),
        patch.object(raffle_menu.raffle_crud, 'get_current_active_campaign', AsyncMock(return_value=None)),
    ):
        await _handle(callback, db_user=db_user, db=db)

    callback.message.edit_text.assert_awaited()
    text = callback.message.edit_text.await_args.args[0]
    assert 'розыгрыш' in text.lower()
    kb = callback.message.edit_text.await_args.kwargs['reply_markup']
    callbacks = [b.callback_data for row in kb.inline_keyboard for b in row]
    assert 'back_to_menu' in callbacks
    assert all(b.web_app is None for row in kb.inline_keyboard for b in row)


@pytest.mark.asyncio
async def test_active_campaign_shows_tickets() -> None:
    callback = _make_callback()
    db_user = SimpleNamespace(id=42, language='ru')
    db = MagicMock()
    mock_settings = MagicMock()
    mock_settings.is_raffle_enabled.return_value = True
    mock_settings.DEFAULT_LANGUAGE = 'ru'
    mock_settings.format_price = lambda x: f'{x / 100:.0f} ₽'

    campaign = SimpleNamespace(
        id=7,
        name='Summer Draw',
        description='Win stuff',
        prize_type='custom',
        prize_value=None,
        prize_text='iPhone',
        prize_slots=None,
        max_winners=1,
        tickets_per_purchase=2,
        tickets_by_tariff=None,
        skip_trial_purchases=True,
    )
    tickets = [
        SimpleNamespace(ticket_code='RAFFLE_AAAA'),
        SimpleNamespace(ticket_code='RAFFLE_BBBB'),
    ]

    with (
        patch.object(raffle_menu, 'settings', mock_settings),
        patch.object(raffle_menu.raffle_crud, 'get_current_active_campaign', AsyncMock(return_value=campaign)),
        patch.object(raffle_menu.raffle_crud, 'list_tickets_for_user', AsyncMock(return_value=tickets)),
    ):
        await _handle(callback, db_user=db_user, db=db)

    callback.message.edit_text.assert_awaited()
    text = callback.message.edit_text.await_args.args[0]
    assert 'Summer Draw' in text
    assert 'iPhone' in text
    assert 'RAFFLE_AAAA' in text
    assert 'RAFFLE_BBBB' in text
    kb = callback.message.edit_text.await_args.kwargs['reply_markup']
    assert all(b.web_app is None for row in kb.inline_keyboard for b in row)
    assert any(b.callback_data == 'back_to_menu' for row in kb.inline_keyboard for b in row)
