"""Пауза (заморозка) подписки в боте: «⏸ Пауза» и «▶️ Выйти из паузы».

Callback-и несут id подписки последним сегментом (``spz_menu:<sub_id>``,
``spz_do:<days>:<sub_id>``, ``spz_resume:<sub_id>``) и разбираются здесь же —
через общий резолвер их не пускаем (см. FOOTGUN в common.py).
"""

from __future__ import annotations

from aiogram import Dispatcher, F, types
from aiogram.types import InaccessibleMessage
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import User
from app.localization.texts import get_texts
from app.services import subscription_pause_service as pause_service


PAUSE_ERROR_DEFAULTS = {
    'disabled': '❌ Пауза подписки сейчас недоступна.',
    'no_subscription': '❌ У вас нет активной подписки.',
    'already_paused': 'ℹ️ Подписка уже на паузе.',
    'user_blocked': '❌ Аккаунт заблокирован.',
    'trial': '❌ Тестовую подписку нельзя поставить на паузу.',
    'daily': '❌ Суточный тариф ставится на паузу своей кнопкой.',
    'not_active': '❌ Поставить на паузу можно только активную подписку.',
    'too_little_remaining': '❌ До конца подписки осталось слишком мало времени для паузы.',
    'limit_reached': '❌ Лимит пауз исчерпан. Следующая пауза доступна с {date}.',
    'invalid_days': '❌ Недопустимая длительность паузы.',
    'not_paused': 'ℹ️ Подписка не на паузе.',
}


def pause_error_text(texts, code: str | None, *, next_available_at=None) -> str:
    code = code or 'not_active'
    default = PAUSE_ERROR_DEFAULTS.get(code, PAUSE_ERROR_DEFAULTS['not_active'])
    template = texts.t(f'SUBSCRIPTION_PAUSE_ERROR_{code.upper()}', default)
    date = pause_service.format_until(next_available_at) if next_available_at else '—'
    try:
        return template.format(date=date)
    except (KeyError, IndexError):
        return default.format(date=date)


def _back_callback(sub_id: int) -> str:
    return f'sm:{sub_id}' if settings.is_multi_tariff_enabled() else 'menu_subscription'


def pause_keyboard_rows(texts, subscription) -> list[list[types.InlineKeyboardButton]]:
    """Ряды кнопок паузы для меню подписки: «Выйти из паузы» или «Пауза»."""
    sub_id = getattr(subscription, 'id', None)
    if subscription is None or not isinstance(sub_id, int):
        return []
    if not settings.is_subscription_pause_enabled() and not _is_paused(subscription):
        return []
    if _is_paused(subscription):
        return [
            [
                types.InlineKeyboardButton(
                    text=texts.t('SUBSCRIPTION_RESUME_BUTTON', '▶️ Выйти из паузы'),
                    callback_data=f'spz_resume:{sub_id}',
                )
            ]
        ]
    if pause_service.check_static_eligibility(None, subscription) is None:
        return [
            [
                types.InlineKeyboardButton(
                    text=texts.t('SUBSCRIPTION_PAUSE_BUTTON', '⏸ Пауза'),
                    callback_data=f'spz_menu:{sub_id}',
                )
            ]
        ]
    return []


def _is_paused(subscription) -> bool:
    return getattr(subscription, 'is_paused', False) is True


def pause_status_block(texts, subscription) -> str:
    """Абзац «Подписка на паузе до …» для экрана подписки (пусто, если не на паузе)."""
    if not _is_paused(subscription):
        return ''
    template = texts.t(
        'SUBSCRIPTION_PAUSE_STATUS_BLOCK',
        '⏸ <b>Подписка на паузе</b> до {until}\nСохранённый остаток: {days} дн. Выйти из паузы можно в любой момент.',
    )
    return template.format(
        until=pause_service.format_until(subscription.pause_planned_until),
        days=pause_service.remaining_days(subscription),
    )


async def _load_subscription(callback: types.CallbackQuery, db_user: User, db: AsyncSession):
    from app.database.crud.subscription import get_subscription_by_id_for_user

    try:
        sub_id = int((callback.data or '').rsplit(':', 1)[-1])
    except ValueError:
        return None
    return await get_subscription_by_id_for_user(db, sub_id, db_user.id)


def _back_markup(texts, sub_id: int) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(
        inline_keyboard=[[types.InlineKeyboardButton(text=texts.BACK, callback_data=_back_callback(sub_id))]]
    )


async def handle_pause_menu(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return
    texts = get_texts(db_user.language)
    subscription = await _load_subscription(callback, db_user, db)
    if subscription is None:
        await callback.answer(pause_error_text(texts, 'no_subscription'), show_alert=True)
        return
    eligibility = await pause_service.get_eligibility(db, db_user, subscription)
    if not eligibility.allowed:
        await callback.answer(
            pause_error_text(texts, eligibility.code, next_available_at=eligibility.next_available_at),
            show_alert=True,
        )
        return

    text = texts.t(
        'SUBSCRIPTION_PAUSE_CHOOSE',
        '⏸ <b>Пауза подписки</b>\n\n'
        'VPN-доступ будет приостановлен. Остаток подписки ({days} дн.) сохранится и вернётся полностью '
        'при выходе из паузы. Выйти из паузы можно в любой момент, по окончании срока паузы подписка '
        'возобновится автоматически. Пауза бесплатна.\n\nНа сколько дней поставить паузу?',
    ).format(days=pause_service.remaining_days(subscription))
    if eligibility.pauses_limit:
        text += '\n\n' + texts.t(
            'SUBSCRIPTION_PAUSE_LIMIT_LINE', 'Пауз использовано: {used} из {limit} за {window} дн.'
        ).format(
            used=eligibility.pauses_used,
            limit=eligibility.pauses_limit,
            window=int(getattr(settings, 'SUBSCRIPTION_PAUSE_LIMIT_WINDOW_DAYS', 30) or 30),
        )

    option_label = texts.t('SUBSCRIPTION_PAUSE_DAYS_OPTION', '{days} дн.')
    buttons = [
        types.InlineKeyboardButton(
            text=f'⏸ {option_label.format(days=days)}', callback_data=f'spz_do:{days}:{subscription.id}'
        )
        for days in settings.get_subscription_pause_day_options()
    ]
    rows = [buttons[i : i + 3] for i in range(0, len(buttons), 3)]
    rows.append([types.InlineKeyboardButton(text=texts.BACK, callback_data=_back_callback(subscription.id))])
    await callback.message.edit_text(
        text, reply_markup=types.InlineKeyboardMarkup(inline_keyboard=rows), parse_mode='HTML'
    )
    await callback.answer()


async def handle_pause_do(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return
    texts = get_texts(db_user.language)
    parts = (callback.data or '').split(':')
    if len(parts) != 3 or not parts[1].isdigit():
        await callback.answer(pause_error_text(texts, 'invalid_days'), show_alert=True)
        return
    days = int(parts[1])
    subscription = await _load_subscription(callback, db_user, db)
    if subscription is None:
        await callback.answer(pause_error_text(texts, 'no_subscription'), show_alert=True)
        return
    try:
        await pause_service.pause_subscription(db, db_user, subscription, days)
    except pause_service.SubscriptionPauseError as exc:
        await callback.answer(
            pause_error_text(texts, exc.code, next_available_at=exc.details.get('next_available_at')),
            show_alert=True,
        )
        return
    text = texts.t(
        'SUBSCRIPTION_PAUSE_DONE',
        '⏸ Подписка на паузе до {until}.\nСохранённый остаток: {days} дн.',
    ).format(
        until=pause_service.format_until(subscription.pause_planned_until),
        days=pause_service.remaining_days(subscription),
    )
    await callback.message.edit_text(text, reply_markup=_back_markup(texts, subscription.id), parse_mode='HTML')
    await callback.answer()


async def handle_pause_resume(callback: types.CallbackQuery, db_user: User, db: AsyncSession) -> None:
    if isinstance(callback.message, InaccessibleMessage):
        await callback.answer()
        return
    texts = get_texts(db_user.language)
    subscription = await _load_subscription(callback, db_user, db)
    if subscription is None:
        await callback.answer(pause_error_text(texts, 'no_subscription'), show_alert=True)
        return
    try:
        await pause_service.resume_subscription(db, db_user, subscription)
    except pause_service.SubscriptionPauseError as exc:
        await callback.answer(pause_error_text(texts, exc.code), show_alert=True)
        return
    text = texts.t('SUBSCRIPTION_RESUME_DONE', '▶️ Пауза снята. Подписка активна до {until}.').format(
        until=pause_service.format_until(subscription.end_date)
    )
    await callback.message.edit_text(text, reply_markup=_back_markup(texts, subscription.id), parse_mode='HTML')
    await callback.answer()


def register_pause_handlers(dp: Dispatcher) -> None:
    dp.callback_query.register(handle_pause_menu, F.data.startswith('spz_menu:'))
    dp.callback_query.register(handle_pause_do, F.data.startswith('spz_do:'))
    dp.callback_query.register(handle_pause_resume, F.data.startswith('spz_resume:'))
