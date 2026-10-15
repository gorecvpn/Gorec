"""Пауза (заморозка) подписки пользователем.

GET  /subscription/freeze    — состояние паузы и доступность
POST /subscription/freeze    — поставить на паузу на N дней
POST /subscription/unfreeze  — выйти из паузы (в любой момент)

Не путать с POST /subscription/pause — это суточная пауза (daily.py).
"""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query as QueryParam, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User
from app.services import subscription_pause_service as pause_service

from ...dependencies import get_cabinet_db, get_current_cabinet_user
from .helpers import resolve_subscription


logger = structlog.get_logger(__name__)

router = APIRouter()


class FreezeRequest(BaseModel):
    days: int = Field(..., ge=1, le=365)


def _error(exc: pause_service.SubscriptionPauseError) -> HTTPException:
    detail: dict[str, Any] = {'code': exc.code, 'message': str(exc)}
    next_at = exc.details.get('next_available_at')
    if next_at is not None:
        detail['next_available_at'] = next_at.isoformat()
    if exc.code == 'disabled':
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=detail)
    if exc.code in ('limit_reached', 'already_paused', 'not_paused'):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


async def _state(db: AsyncSession, user: User, subscription) -> dict[str, Any]:
    eligibility = None
    if subscription is not None and not getattr(subscription, 'is_paused', False):
        eligibility = await pause_service.get_eligibility(db, user, subscription)
    data = pause_service.pause_state_dict(subscription, eligibility=eligibility)
    data['subscription_id'] = getattr(subscription, 'id', None)
    return data


@router.get('/freeze')
async def get_freeze_state(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
    subscription_id: int | None = QueryParam(None, description='Subscription ID for multi-tariff'),
) -> dict[str, Any]:
    subscription = await resolve_subscription(db, user, subscription_id)
    return await _state(db, user, subscription)


@router.post('/freeze')
async def freeze_subscription(
    request: FreezeRequest,
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
    subscription_id: int | None = QueryParam(None, description='Subscription ID for multi-tariff'),
) -> dict[str, Any]:
    subscription = await resolve_subscription(db, user, subscription_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No subscription found')
    try:
        await pause_service.pause_subscription(db, user, subscription, request.days)
    except pause_service.SubscriptionPauseError as exc:
        raise _error(exc) from exc
    await pause_service.notify_paused(user, subscription)
    return await _state(db, user, subscription)


@router.post('/unfreeze')
async def unfreeze_subscription(
    user: User = Depends(get_current_cabinet_user),
    db: AsyncSession = Depends(get_cabinet_db),
    subscription_id: int | None = QueryParam(None, description='Subscription ID for multi-tariff'),
) -> dict[str, Any]:
    subscription = await resolve_subscription(db, user, subscription_id)
    if subscription is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='No subscription found')
    try:
        await pause_service.resume_subscription(db, user, subscription, reason=pause_service.RESUME_MANUAL)
    except pause_service.SubscriptionPauseError as exc:
        raise _error(exc) from exc
    await pause_service.notify_resumed(user, subscription)
    return await _state(db, user, subscription)
