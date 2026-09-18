"""User-facing handler for the main-menu «Розыгрыш» button (classic/default mode).

Cabinet mode opens ``/raffle`` as a WebApp button directly from the keyboard
builder; this handler covers callback fallbacks when ``MINIAPP_CUSTOM_URL`` is
not configured (or the classic menu layout emits ``menu_raffle``).
"""

from __future__ import annotations

import structlog
from aiogram import Dispatcher, F, types
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.localization.texts import get_texts
from app.utils.decorators import error_handler
from app.utils.miniapp_buttons import build_cabinet_url


logger = structlog.get_logger(__name__)


@error_handler
async def handle_menu_raffle(
    callback: types.CallbackQuery,
    db_user: User | None = None,
    db: AsyncSession | None = None,
) -> None:
    """Open raffle miniapp or show a sensible fallback when unavailable."""
    language = (
        getattr(db_user, 'language', None)
        or getattr(callback.from_user, 'language_code', None)
        or settings.DEFAULT_LANGUAGE
    )
    try:
        texts = get_texts(language)
    except Exception:
        texts = get_texts()

    if not settings.is_raffle_enabled():
        await callback.answer(
            texts.t('RAFFLE_DISABLED', '🎫 Розыгрыш сейчас недоступен.'),
            show_alert=True,
        )
        return

    raffle_url = build_cabinet_url('/raffle')
    if raffle_url:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=texts.t('RAFFLE_OPEN_BUTTON', '🎫 Открыть розыгрыш'),
                        web_app=types.WebAppInfo(url=raffle_url),
                    )
                ],
                [InlineKeyboardButton(text=texts.BACK, callback_data='back_to_menu')],
            ]
        )
        message_text = texts.t(
            'RAFFLE_OPEN_PROMPT',
            '🎫 Розыгрыш билетов за покупку подписки.\n\nНажмите кнопку ниже, чтобы открыть раздел.',
        )
        try:
            await callback.message.edit_text(message_text, reply_markup=keyboard)
        except Exception:
            await callback.message.answer(message_text, reply_markup=keyboard)
        await callback.answer()
        return

    await callback.answer(
        texts.t(
            'RAFFLE_MINIAPP_UNAVAILABLE',
            '🎫 Розыгрыш доступен в личном кабинете. Mini App URL не настроен.',
        ),
        show_alert=True,
    )


def register_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(handle_menu_raffle, F.data == 'menu_raffle')
