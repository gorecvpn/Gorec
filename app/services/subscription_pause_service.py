"""Пауза (заморозка) подписки пользователем.

Отдельное состояние, не суточная пауза (``is_daily_paused``):

* Пауза: остаток срока (``end_date - now``) сохраняется в секундах, подписка
  помечается ``is_paused``, ``end_date`` замораживается как
  ``pause_planned_until + остаток`` — так ни один проверяльщик истечения не
  погасит подписку во время паузы. Аккаунт в панели отключается (DISABLED с
  будущей датой) и, если задан ``SUBSCRIPTION_PAUSE_EXTERNAL_SQUAD_UUID``,
  переводится во внешний сквад «Пауза» — Happ/INCY показывают его remarks и
  announce («подписка на паузе»).
* Выход из паузы (вручную в любой момент, автоматически в ``pause_planned_until``,
  перед покупкой/продлением/сменой тарифа или админом): новый срок
  ``now + остаток`` — неиспользованные дни возвращаются полностью. В панели
  сначала ``expireAt`` и внешний сквад тарифа, затем enable.

Трафик и HWID-устройства пауза не трогает. Пауза бесплатна.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import structlog
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.models import Subscription, SubscriptionPause, SubscriptionStatus, User, UserStatus
from app.services.panel_sync.writer import patch_panel_pause


logger = structlog.get_logger(__name__)

RESUME_MANUAL = 'manual'
RESUME_AUTO = 'auto'
RESUME_PURCHASE = 'purchase'
RESUME_ADMIN = 'admin'

INITIATOR_USER = 'user'
INITIATOR_ADMIN = 'admin'


class SubscriptionPauseError(Exception):
    """Пауза/возобновление невозможны. ``code`` — машинный код для API и локализации."""

    def __init__(self, code: str, message: str = '', **details) -> None:
        super().__init__(message or code)
        self.code = code
        self.details = details


@dataclass(frozen=True)
class PauseEligibility:
    allowed: bool
    code: str | None = None
    pauses_used: int = 0
    pauses_limit: int = 0
    next_available_at: datetime | None = None


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


def _now() -> datetime:
    return datetime.now(UTC)


def remaining_seconds(subscription, *, now: datetime | None = None) -> int:
    """Неиспользованный остаток подписки в секундах (на паузе — сохранённый)."""
    if getattr(subscription, 'is_paused', False):
        return max(0, int(getattr(subscription, 'pause_remaining_seconds', 0) or 0))
    end = _aware(getattr(subscription, 'end_date', None))
    if end is None:
        return 0
    return max(0, int((end - (now or _now())).total_seconds()))


def remaining_days(subscription, *, now: datetime | None = None) -> int:
    """Остаток в целых днях, округление вверх (23 ч → 1 день)."""
    seconds = remaining_seconds(subscription, now=now)
    return (seconds + 86399) // 86400


def _is_daily(subscription) -> bool:
    tariff = getattr(subscription, 'tariff', None)
    return bool(tariff is not None and getattr(tariff, 'is_daily', False))


def check_static_eligibility(user, subscription, *, now: datetime | None = None) -> str | None:
    """Проверки без базы. ``None`` — можно, иначе код причины."""
    moment = now or _now()
    if not settings.is_subscription_pause_enabled():
        return 'disabled'
    if subscription is None:
        return 'no_subscription'
    if getattr(subscription, 'is_paused', False) is True:
        return 'already_paused'
    if user is not None and getattr(user, 'status', UserStatus.ACTIVE.value) != UserStatus.ACTIVE.value:
        return 'user_blocked'
    status = getattr(subscription, 'status', None)
    if getattr(subscription, 'is_trial', False) or status == SubscriptionStatus.TRIAL.value:
        return 'trial'
    if _is_daily(subscription) or getattr(subscription, 'is_daily_paused', False):
        return 'daily'
    if status != SubscriptionStatus.ACTIVE.value:
        return 'not_active'
    if getattr(subscription, 'grace_session_open', False):
        return 'not_active'
    end = _aware(getattr(subscription, 'end_date', None))
    if end is None or end <= moment:
        return 'not_active'
    min_days = max(0, int(getattr(settings, 'SUBSCRIPTION_PAUSE_MIN_REMAINING_DAYS', 1) or 0))
    if (end - moment) < timedelta(days=min_days):
        return 'too_little_remaining'
    return None


async def count_recent_pauses(
    db: AsyncSession, user_id: int, *, now: datetime | None = None
) -> tuple[int, datetime | None]:
    """Сколько пауз пользователь начал за окно лимита, и когда освободится слот."""
    moment = now or _now()
    window_days = max(1, int(getattr(settings, 'SUBSCRIPTION_PAUSE_LIMIT_WINDOW_DAYS', 30) or 30))
    since = moment - timedelta(days=window_days)
    result = await db.execute(
        select(SubscriptionPause.paused_at)
        .where(SubscriptionPause.user_id == user_id, SubscriptionPause.paused_at >= since)
        .order_by(SubscriptionPause.paused_at.asc())
    )
    started = [_aware(row) for row in result.scalars().all()]
    next_slot = started[0] + timedelta(days=window_days) if started else None
    return len(started), next_slot


async def get_eligibility(
    db: AsyncSession, user, subscription, *, now: datetime | None = None, ignore_limit: bool = False
) -> PauseEligibility:
    moment = now or _now()
    limit = max(0, int(getattr(settings, 'SUBSCRIPTION_PAUSE_LIMIT_COUNT', 3) or 0))
    used, next_slot = (0, None)
    if user is not None:
        used, next_slot = await count_recent_pauses(db, user.id, now=moment)
    code = check_static_eligibility(user, subscription, now=moment)
    if code is None and not ignore_limit and limit and used >= limit:
        code = 'limit_reached'
    return PauseEligibility(
        allowed=code is None,
        code=code,
        pauses_used=used,
        pauses_limit=limit,
        next_available_at=next_slot if code == 'limit_reached' else None,
    )


def validate_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        raise SubscriptionPauseError('invalid_days') from None
    if value < 1 or value > settings.get_subscription_pause_max_days():
        raise SubscriptionPauseError('invalid_days', max_days=settings.get_subscription_pause_max_days())
    return value


async def _lock(db: AsyncSession, subscription: Subscription) -> None:
    await db.execute(select(Subscription.id).where(Subscription.id == subscription.id).with_for_update())
    await db.refresh(subscription)


async def _open_history(db: AsyncSession, subscription_id: int) -> SubscriptionPause | None:
    result = await db.execute(
        select(SubscriptionPause)
        .where(SubscriptionPause.subscription_id == subscription_id, SubscriptionPause.resumed_at.is_(None))
        .order_by(SubscriptionPause.paused_at.desc())
        .limit(1)
    )
    return result.scalars().first()


def apply_pause_state(subscription, *, days: int, now: datetime) -> int:
    """Перевести объект подписки в паузу. Возвращает сохранённый остаток (сек)."""
    remaining = remaining_seconds(subscription, now=now)
    planned_until = now + timedelta(days=days)
    subscription.is_paused = True
    subscription.paused_at = now
    subscription.pause_planned_until = planned_until
    subscription.pause_remaining_seconds = remaining
    # Заморозка даты: до конца паузы и ещё на остаток. Любой проверяльщик
    # истечения видит будущую дату и подписку не гасит.
    subscription.end_date = planned_until + timedelta(seconds=remaining)
    return remaining


def apply_resume_state(subscription, *, now: datetime) -> datetime:
    """Снять паузу с объекта подписки. Возвращает новый ``end_date``."""
    remaining = max(0, int(subscription.pause_remaining_seconds or 0))
    new_end = now + timedelta(seconds=remaining)
    subscription.is_paused = False
    subscription.paused_at = None
    subscription.pause_planned_until = None
    subscription.pause_remaining_seconds = None
    subscription.end_date = new_end
    return new_end


def _panel_user_id(user, subscription) -> int | None:
    if settings.is_multi_tariff_enabled():
        return getattr(subscription, 'remnawave_id', None)
    return getattr(user, 'remnawave_id', None) if user is not None else None


async def _panel_apply(db: AsyncSession, user, subscription, *, paused: bool) -> bool:
    """Отразить паузу в панели. Ошибка панели не откатывает состояние в боте:
    следующая синхронизация отправит тот же статус (liveness учитывает паузу)."""
    from app.services.subscription_service import SubscriptionService

    panel_id = _panel_user_id(user, subscription)
    if not panel_id:
        logger.warning('Пауза: у подписки нет панельного id, панель не обновлена', subscription_id=subscription.id)
        return False
    service = SubscriptionService()
    if not service.is_configured:
        return False
    pause_squad = settings.get_subscription_pause_external_squad_uuid()
    try:
        async with service.get_api_client() as api:
            external_squad = ...
            if pause_squad:
                if paused:
                    external_squad = pause_squad
                else:
                    tariff = getattr(subscription, 'tariff', None)
                    # None снимает внешний сквад «Пауза», если у тарифа своего нет.
                    external_squad = getattr(tariff, 'external_squad_uuid', None) or None
            await patch_panel_pause(
                api,
                user_id=panel_id,
                paused=paused,
                expire_at=subscription.end_date,
                external_squad_uuid=external_squad,
            )
        return True
    except Exception as exc:
        logger.error(
            'Пауза: не удалось обновить аккаунт в панели',
            subscription_id=subscription.id,
            paused=paused,
            error=exc,
        )
        return False


async def pause_subscription(
    db: AsyncSession,
    user: User,
    subscription: Subscription,
    days: int,
    *,
    initiator: str = INITIATOR_USER,
    now: datetime | None = None,
    sync_panel: bool = True,
) -> SubscriptionPause:
    """Поставить подписку на паузу на ``days`` дней."""
    moment = now or _now()
    days = validate_days(days)
    await _lock(db, subscription)
    eligibility = await get_eligibility(db, user, subscription, now=moment, ignore_limit=initiator == INITIATOR_ADMIN)
    if not eligibility.allowed:
        raise SubscriptionPauseError(eligibility.code or 'not_allowed', next_available_at=eligibility.next_available_at)

    remaining = apply_pause_state(subscription, days=days, now=moment)
    record = SubscriptionPause(
        subscription_id=subscription.id,
        user_id=subscription.user_id,
        paused_at=moment,
        planned_until=subscription.pause_planned_until,
        remaining_seconds=remaining,
        initiator=initiator,
    )
    db.add(record)
    await db.commit()
    await db.refresh(subscription)
    logger.info(
        '⏸ Подписка поставлена на паузу',
        subscription_id=subscription.id,
        user_id=subscription.user_id,
        days=days,
        remaining_seconds=remaining,
        initiator=initiator,
    )
    if sync_panel:
        await _panel_apply(db, user, subscription, paused=True)
    return record


async def resume_subscription(
    db: AsyncSession,
    user: User | None,
    subscription: Subscription,
    *,
    reason: str = RESUME_MANUAL,
    now: datetime | None = None,
    commit: bool = True,
    sync_panel: bool = True,
) -> datetime:
    """Выйти из паузы: новый срок = сейчас + сохранённый остаток."""
    moment = now or _now()
    if commit:
        await _lock(db, subscription)
    if not getattr(subscription, 'is_paused', False):
        raise SubscriptionPauseError('not_paused')

    new_end = apply_resume_state(subscription, now=moment)
    record = await _open_history(db, subscription.id)
    if record is not None:
        record.resumed_at = moment
        record.resume_reason = reason
    if commit:
        await db.commit()
        await db.refresh(subscription)
    else:
        await db.flush()
    logger.info(
        '▶️ Подписка снята с паузы',
        subscription_id=subscription.id,
        user_id=subscription.user_id,
        reason=reason,
        new_end_date=new_end,
    )
    if sync_panel:
        await _panel_apply(db, user, subscription, paused=False)
    return new_end


async def resume_before_purchase(db: AsyncSession, subscription: Subscription) -> bool:
    """Покупка/продление/смена тарифа на паузе: сначала выйти из паузы.

    Вызывается внутри транзакции покупки (без commit): состояние подписки
    возвращается к «живой» с честным остатком, дальше покупка считает срок от
    него, а её же синхронизация с панелью включает аккаунт. Внешний сквад
    «Пауза» снимается отдельным запросом (best effort).
    """
    if not getattr(subscription, 'is_paused', False):
        return False
    from app.database.crud.user import get_user_by_id

    user = await get_user_by_id(db, subscription.user_id)
    await resume_subscription(db, user, subscription, reason=RESUME_PURCHASE, commit=False, sync_panel=False)
    if settings.get_subscription_pause_external_squad_uuid() and user is not None:
        await _panel_apply(db, user, subscription, paused=False)
    return True


# --------------------------------------------------------------------------
# Планировщик: авто-возобновление и напоминания
# --------------------------------------------------------------------------


async def _notify(user: User, kind: str, **context) -> None:
    """Сообщение пользователю (Telegram / email / кабинет)."""
    from app.localization.texts import get_texts
    from app.services.notification_delivery_service import notification_delivery_service
    from app.services.notification_types import NotificationType

    texts = get_texts(getattr(user, 'language', None) or settings.DEFAULT_LANGUAGE)
    templates = {
        'paused': (
            NotificationType.SUBSCRIPTION_PAUSED,
            'SUBSCRIPTION_PAUSE_NOTIFY_PAUSED',
            '⏸ Подписка поставлена на паузу до {until}.\nОстаток {days} дн. сохранён и вернётся при выходе из паузы.',
        ),
        'ending': (
            NotificationType.SUBSCRIPTION_PAUSE_ENDING,
            'SUBSCRIPTION_PAUSE_NOTIFY_ENDING',
            '⏰ Пауза подписки закончится {until} — подписка возобновится автоматически.',
        ),
        'resumed': (
            NotificationType.SUBSCRIPTION_RESUMED,
            'SUBSCRIPTION_PAUSE_NOTIFY_RESUMED',
            '▶️ Подписка снова активна до {until}.',
        ),
    }
    notification_type, key, default = templates[kind]
    try:
        text = texts.t(key, default).format(**context)
    except (KeyError, IndexError):
        text = default.format(**context)

    bot = None
    try:
        if getattr(user, 'telegram_id', None):
            from app.bot_factory import create_bot

            bot = create_bot()
        await notification_delivery_service.send_notification(
            user=user,
            notification_type=notification_type,
            context=context,
            bot=bot,
            telegram_message=text,
        )
    except Exception as exc:
        logger.debug('Пауза: уведомление не доставлено', user_id=getattr(user, 'id', None), kind=kind, error=exc)
    finally:
        if bot is not None:
            await bot.session.close()


def format_until(value: datetime | None) -> str:
    from app.utils.timezone import format_local_datetime

    return format_local_datetime(value, '%d.%m.%Y %H:%M') if value else ''


async def notify_paused(user: User, subscription: Subscription) -> None:
    await _notify(
        user,
        'paused',
        until=format_until(subscription.pause_planned_until),
        days=remaining_days(subscription),
    )


async def notify_resumed(user: User, subscription: Subscription) -> None:
    await _notify(user, 'resumed', until=format_until(subscription.end_date))


async def process_due_pauses(db: AsyncSession, *, now: datetime | None = None, limit: int = 200) -> dict[str, int]:
    """Авто-возобновление по окончании паузы и напоминания за N часов."""
    from app.database.crud.user import get_user_by_id

    moment = now or _now()
    stats = {'resumed': 0, 'reminded': 0, 'errors': 0}

    due = await db.execute(
        select(Subscription.id)
        .where(Subscription.is_paused.is_(True), Subscription.pause_planned_until <= moment)
        .order_by(Subscription.pause_planned_until.asc())
        .limit(limit)
    )
    for subscription_id in due.scalars().all():
        try:
            subscription = await db.get(Subscription, subscription_id)
            if subscription is None or not subscription.is_paused:
                continue
            user = await get_user_by_id(db, subscription.user_id)
            await resume_subscription(db, user, subscription, reason=RESUME_AUTO, now=moment)
            stats['resumed'] += 1
            if user is not None:
                await notify_resumed(user, subscription)
        except Exception as exc:
            stats['errors'] += 1
            await db.rollback()
            logger.error('Пауза: ошибка авто-возобновления', subscription_id=subscription_id, error=exc)

    hours = max(1, int(getattr(settings, 'SUBSCRIPTION_PAUSE_REMINDER_HOURS_BEFORE', 24) or 24))
    reminders = await db.execute(
        select(SubscriptionPause)
        .join(Subscription, Subscription.id == SubscriptionPause.subscription_id)
        .where(
            Subscription.is_paused.is_(True),
            SubscriptionPause.resumed_at.is_(None),
            SubscriptionPause.reminder_sent_at.is_(None),
            SubscriptionPause.planned_until > moment,
            SubscriptionPause.planned_until <= moment + timedelta(hours=hours),
            # Пауза короче окна напоминания — не напоминаем сразу после старта.
            SubscriptionPause.planned_until - SubscriptionPause.paused_at > timedelta(hours=hours),
        )
        .limit(limit)
    )
    for record in reminders.scalars().all():
        try:
            record.reminder_sent_at = moment
            await db.commit()
            user = await get_user_by_id(db, record.user_id)
            if user is not None:
                await _notify(user, 'ending', until=format_until(record.planned_until))
            stats['reminded'] += 1
        except Exception as exc:
            stats['errors'] += 1
            await db.rollback()
            logger.error('Пауза: ошибка напоминания', pause_id=record.id, error=exc)
    return stats


async def count_paused_subscriptions(db: AsyncSession) -> int:
    result = await db.execute(select(func.count(Subscription.id)).where(Subscription.is_paused.is_(True)))
    return int(result.scalar() or 0)


def pause_state_dict(subscription, *, eligibility: PauseEligibility | None = None) -> dict:
    """Состояние паузы для API кабинета/miniapp."""
    data = {
        'enabled': settings.is_subscription_pause_enabled(),
        'is_paused': bool(getattr(subscription, 'is_paused', False)),
        'paused_at': _aware(getattr(subscription, 'paused_at', None)),
        'planned_until': _aware(getattr(subscription, 'pause_planned_until', None)),
        'remaining_days': remaining_days(subscription) if subscription is not None else 0,
        'remaining_seconds': remaining_seconds(subscription) if subscription is not None else 0,
        'day_options': settings.get_subscription_pause_day_options(),
        'max_days': settings.get_subscription_pause_max_days(),
        'can_pause': False,
        'reason': None,
        'pauses_used': 0,
        'pauses_limit': int(getattr(settings, 'SUBSCRIPTION_PAUSE_LIMIT_COUNT', 0) or 0),
        'next_available_at': None,
    }
    if eligibility is not None:
        data.update(
            can_pause=eligibility.allowed,
            reason=eligibility.code,
            pauses_used=eligibility.pauses_used,
            pauses_limit=eligibility.pauses_limit,
            next_available_at=eligibility.next_available_at,
        )
    return data
