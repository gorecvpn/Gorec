"""Focused tests for the built-in Telegram main-menu «Розыгрыш» button."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from aiogram.types import InlineKeyboardButton

from app.services.menu_layout.constants import BUILTIN_BUTTONS_INFO, DEFAULT_MENU_CONFIG
from app.services.menu_layout.context import MenuContext
from app.services.menu_layout.service import MenuLayoutService
from app.utils import button_styles_cache, menu_layout_cache
from app.utils.button_styles_cache import CALLBACK_TO_SECTION, DEFAULT_BUTTON_STYLES
from app.utils.menu_layout_cache import BUILTIN_SECTIONS, DEFAULT_MENU_LAYOUT
from app.utils.miniapp_buttons import CALLBACK_TO_CABINET_PATH, CALLBACK_TO_CABINET_STYLE


def test_raffle_builtin_in_classic_defaults() -> None:
    assert 'raffle' in DEFAULT_MENU_CONFIG['buttons']
    raffle_btn = DEFAULT_MENU_CONFIG['buttons']['raffle']
    assert raffle_btn['action'] == 'menu_raffle'
    assert raffle_btn['text']['ru'] == '🎫 Розыгрыш'
    assert raffle_btn['conditions'] == {'raffle_visible': True}

    row_ids = [row['id'] for row in DEFAULT_MENU_CONFIG['rows']]
    assert 'raffle_row' in row_ids
    raffle_row = next(r for r in DEFAULT_MENU_CONFIG['rows'] if r['id'] == 'raffle_row')
    assert raffle_row['conditions'] == {'raffle_visible': True}

    info = next(b for b in BUILTIN_BUTTONS_INFO if b['id'] == 'raffle')
    assert info['callback_data'] == 'menu_raffle'
    assert info['default_conditions'] == {'raffle_visible': True}


def test_raffle_not_confused_with_contests() -> None:
    contests = DEFAULT_MENU_CONFIG['buttons']['contests']
    raffle = DEFAULT_MENU_CONFIG['buttons']['raffle']
    assert contests['action'] == 'contests_menu'
    assert raffle['action'] == 'menu_raffle'
    assert contests['action'] != raffle['action']


def test_cabinet_builtins_include_raffle() -> None:
    assert 'raffle' in BUILTIN_SECTIONS
    assert 'raffle' in DEFAULT_BUTTON_STYLES
    assert DEFAULT_BUTTON_STYLES['raffle']['enabled'] is True

    all_default_btns = [
        b for row in DEFAULT_MENU_LAYOUT.values() if isinstance(row, dict) for b in row.get('buttons', [])
    ]
    assert 'raffle' in all_default_btns


def test_miniapp_maps_menu_raffle_to_path() -> None:
    assert CALLBACK_TO_CABINET_PATH['menu_raffle'] == '/raffle'
    assert CALLBACK_TO_CABINET_STYLE['menu_raffle'] == 'primary'
    assert CALLBACK_TO_SECTION['menu_raffle'] == 'raffle'


@pytest.mark.parametrize(
    ('enabled', 'expected'),
    [
        (True, True),
        (False, False),
    ],
)
def test_evaluate_raffle_visible_condition(monkeypatch: pytest.MonkeyPatch, enabled: bool, expected: bool) -> None:
    monkeypatch.setattr(
        'app.services.menu_layout.service.settings',
        SimpleNamespace(is_raffle_enabled=lambda: enabled),
        raising=False,
    )
    # Patch the settings object methods used by evaluate — only raffle matters here
    with patch('app.services.menu_layout.service.settings') as mock_settings:
        mock_settings.is_raffle_enabled.return_value = enabled
        ctx = MenuContext(language='ru')
        assert MenuLayoutService._evaluate_conditions({'raffle_visible': True}, ctx) is expected


def test_build_button_raffle_opens_webapp_when_miniapp_configured() -> None:
    button_config = {
        'type': 'builtin',
        'builtin_id': 'raffle',
        'text': {'ru': '🎫 Розыгрыш'},
        'action': 'menu_raffle',
        'enabled': True,
    }
    context = MenuContext(language='ru')
    texts = MagicMock()
    texts.t = lambda key, default: default

    with patch('app.utils.miniapp_buttons.build_cabinet_url', return_value='https://cab.example/raffle'):
        button = MenuLayoutService._build_button(button_config, context, texts, button_id='raffle')

    assert isinstance(button, InlineKeyboardButton)
    assert button.web_app is not None
    assert button.web_app.url == 'https://cab.example/raffle'
    assert button.callback_data is None


def test_build_button_raffle_falls_back_to_callback_without_miniapp() -> None:
    button_config = {
        'type': 'builtin',
        'builtin_id': 'raffle',
        'text': {'ru': '🎫 Розыгрыш'},
        'action': 'menu_raffle',
        'enabled': True,
    }
    context = MenuContext(language='ru')
    texts = MagicMock()
    texts.t = lambda key, default: default

    with patch('app.utils.miniapp_buttons.build_cabinet_url', return_value=''):
        button = MenuLayoutService._build_button(button_config, context, texts, button_id='raffle')

    assert isinstance(button, InlineKeyboardButton)
    assert button.callback_data == 'menu_raffle'
    assert button.web_app is None


def test_cabinet_keyboard_shows_raffle_when_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.keyboards.inline import _build_cabinet_main_menu_keyboard

    monkeypatch.setattr(menu_layout_cache, '_cached_layout', None)
    monkeypatch.setattr(button_styles_cache, '_cached_styles', None)

    texts = MagicMock()
    texts.t = lambda key, default: default
    texts.MENU_SUBSCRIPTION = 'Sub'
    texts.MENU_REFERRALS = 'Ref'
    texts.MENU_SUPPORT = 'Support'
    texts.MENU_LANGUAGE = 'Lang'
    texts.MENU_ADMIN = 'Admin'
    texts.format_price = lambda x: '0'

    with (
        patch('app.keyboards.inline.settings') as mock_settings,
        patch('app.utils.miniapp_buttons.build_cabinet_url', side_effect=lambda path: f'https://cab.example{path}'),
        patch('app.utils.miniapp_buttons.settings') as mini_settings,
    ):
        mock_settings.is_raffle_enabled.return_value = True
        mock_settings.is_referral_program_enabled.return_value = True
        mock_settings.is_language_selection_enabled.return_value = False
        mock_settings.is_multi_tariff_enabled.return_value = False
        mock_settings.CABINET_BUTTON_STYLE = ''
        mini_settings.MINIAPP_CUSTOM_URL = 'https://cab.example'
        mini_settings.is_cabinet_mode.return_value = True

        # Minimal layout without raffle → auto-append should add it
        monkeypatch.setattr(
            menu_layout_cache,
            '_cached_layout',
            {
                'row_1': {'id': 'row_1', 'buttons': ['home'], 'max_per_row': 1},
                'custom_buttons': {},
            },
        )
        monkeypatch.setattr(
            button_styles_cache,
            '_cached_styles',
            {**DEFAULT_BUTTON_STYLES},
        )

        kb = _build_cabinet_main_menu_keyboard('ru', texts, is_admin=False, is_moderator=False)
        urls = [
            btn.web_app.url
            for row in kb.inline_keyboard
            for btn in row
            if btn.web_app is not None
        ]
        assert any(u.endswith('/raffle') for u in urls)


def test_cabinet_keyboard_hides_raffle_when_feature_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.keyboards.inline import _build_cabinet_main_menu_keyboard

    texts = MagicMock()
    texts.t = lambda key, default: default
    texts.MENU_SUBSCRIPTION = 'Sub'
    texts.MENU_REFERRALS = 'Ref'
    texts.MENU_SUPPORT = 'Support'
    texts.MENU_LANGUAGE = 'Lang'
    texts.MENU_ADMIN = 'Admin'
    texts.format_price = lambda x: '0'

    with (
        patch('app.keyboards.inline.settings') as mock_settings,
        patch('app.utils.miniapp_buttons.build_cabinet_url', side_effect=lambda path: f'https://cab.example{path}'),
    ):
        mock_settings.is_raffle_enabled.return_value = False
        mock_settings.is_referral_program_enabled.return_value = False
        mock_settings.is_language_selection_enabled.return_value = False
        mock_settings.is_multi_tariff_enabled.return_value = False
        mock_settings.CABINET_BUTTON_STYLE = ''

        monkeypatch.setattr(
            menu_layout_cache,
            '_cached_layout',
            {
                'row_1': {'id': 'row_1', 'buttons': ['raffle', 'home'], 'max_per_row': 2},
                'custom_buttons': {},
            },
        )
        monkeypatch.setattr(button_styles_cache, '_cached_styles', {**DEFAULT_BUTTON_STYLES})

        kb = _build_cabinet_main_menu_keyboard('ru', texts, is_admin=False, is_moderator=False)
        urls = [
            btn.web_app.url
            for row in kb.inline_keyboard
            for btn in row
            if btn.web_app is not None
        ]
        callbacks = [btn.callback_data for row in kb.inline_keyboard for btn in row]
        assert not any(u and u.endswith('/raffle') for u in urls)
        assert 'menu_raffle' not in callbacks


def test_cabinet_keyboard_respects_section_enabled_false(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.keyboards.inline import _build_cabinet_main_menu_keyboard

    texts = MagicMock()
    texts.t = lambda key, default: default
    texts.MENU_SUBSCRIPTION = 'Sub'
    texts.MENU_REFERRALS = 'Ref'
    texts.MENU_SUPPORT = 'Support'
    texts.MENU_LANGUAGE = 'Lang'
    texts.MENU_ADMIN = 'Admin'
    texts.format_price = lambda x: '0'

    styles = {**DEFAULT_BUTTON_STYLES, 'raffle': {**DEFAULT_BUTTON_STYLES['raffle'], 'enabled': False}}

    with (
        patch('app.keyboards.inline.settings') as mock_settings,
        patch('app.utils.miniapp_buttons.build_cabinet_url', side_effect=lambda path: f'https://cab.example{path}'),
    ):
        mock_settings.is_raffle_enabled.return_value = True
        mock_settings.is_referral_program_enabled.return_value = False
        mock_settings.is_language_selection_enabled.return_value = False
        mock_settings.is_multi_tariff_enabled.return_value = False
        mock_settings.CABINET_BUTTON_STYLE = ''

        monkeypatch.setattr(
            menu_layout_cache,
            '_cached_layout',
            {
                'row_1': {'id': 'row_1', 'buttons': ['raffle'], 'max_per_row': 1},
                'custom_buttons': {},
            },
        )
        monkeypatch.setattr(button_styles_cache, '_cached_styles', styles)

        kb = _build_cabinet_main_menu_keyboard('ru', texts, is_admin=False, is_moderator=False)
        urls = [
            btn.web_app.url
            for row in kb.inline_keyboard
            for btn in row
            if btn.web_app is not None
        ]
        assert not any(u and u.endswith('/raffle') for u in urls)
