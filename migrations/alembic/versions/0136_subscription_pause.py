"""subscription pause (freeze): state on subscriptions + subscription_pauses history

Revision ID: 0136
Revises: 0135
Create Date: 2026-10-15
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0136'
down_revision: Union[str, None] = '0135'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'subscriptions',
        sa.Column('is_paused', sa.Boolean(), nullable=False, server_default=sa.text('false')),
    )
    op.add_column('subscriptions', sa.Column('paused_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('subscriptions', sa.Column('pause_planned_until', sa.DateTime(timezone=True), nullable=True))
    op.add_column('subscriptions', sa.Column('pause_remaining_seconds', sa.BigInteger(), nullable=True))

    op.create_table(
        'subscription_pauses',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column(
            'subscription_id',
            sa.Integer(),
            sa.ForeignKey('subscriptions.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('users.id', ondelete='CASCADE'), nullable=False),
        sa.Column('paused_at', sa.DateTime(timezone=True), nullable=False),
        sa.Column('planned_until', sa.DateTime(timezone=True), nullable=False),
        sa.Column('remaining_seconds', sa.BigInteger(), nullable=False),
        sa.Column('resumed_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('resume_reason', sa.String(length=16), nullable=True),
        sa.Column('initiator', sa.String(length=16), nullable=False, server_default='user'),
        sa.Column('reminder_sent_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
    )
    op.create_index(
        'ix_subscription_pauses_subscription_paused_at', 'subscription_pauses', ['subscription_id', 'paused_at']
    )
    op.create_index('ix_subscription_pauses_user_paused_at', 'subscription_pauses', ['user_id', 'paused_at'])
    # Планировщик авто-возобновления выбирает только замороженные подписки.
    op.create_index(
        'ix_subscriptions_paused_planned_until',
        'subscriptions',
        ['pause_planned_until'],
        postgresql_where=sa.text('is_paused'),
    )


def downgrade() -> None:
    op.drop_index('ix_subscriptions_paused_planned_until', table_name='subscriptions')
    op.drop_index('ix_subscription_pauses_user_paused_at', table_name='subscription_pauses')
    op.drop_index('ix_subscription_pauses_subscription_paused_at', table_name='subscription_pauses')
    op.drop_table('subscription_pauses')
    op.drop_column('subscriptions', 'pause_remaining_seconds')
    op.drop_column('subscriptions', 'pause_planned_until')
    op.drop_column('subscriptions', 'paused_at')
    op.drop_column('subscriptions', 'is_paused')
