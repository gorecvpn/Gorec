"""Фоновый цикл паузы подписок: авто-возобновление и напоминания."""

from __future__ import annotations

import asyncio

import structlog

from app.config import settings
from app.database.database import AsyncSessionLocal
from app.services.subscription_pause_service import process_due_pauses


logger = structlog.get_logger(__name__)


class SubscriptionPauseSchedulerService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._interval_seconds = 300

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self) -> None:
        await self.stop()
        # Цикл работает и при выключенной функции: уже замороженные подписки
        # обязаны выйти из паузы в срок.
        interval = int(getattr(settings, 'SUBSCRIPTION_PAUSE_CHECK_INTERVAL_SECONDS', 300) or 300)
        self._interval_seconds = max(30, interval)
        self._task = asyncio.create_task(self._loop())
        logger.info('Планировщик паузы подписок запущен', interval=self._interval_seconds)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def _loop(self) -> None:
        try:
            while True:
                try:
                    async with AsyncSessionLocal() as db:
                        stats = await process_due_pauses(db)
                    if stats.get('resumed') or stats.get('reminded') or stats.get('errors'):
                        logger.info('Пауза подписок: проход планировщика', **stats)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.error('Ошибка планировщика паузы подписок', error=exc)
                await asyncio.sleep(self._interval_seconds)
        except asyncio.CancelledError:
            logger.info('Планировщик паузы подписок остановлен')
            raise


subscription_pause_scheduler_service = SubscriptionPauseSchedulerService()
