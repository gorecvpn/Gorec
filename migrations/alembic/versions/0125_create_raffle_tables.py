"""create raffle_campaigns, raffle_tickets, raffle_winners

Revision ID: 0125
Revises: 0124
Create Date: 2026-09-17

MVP розыгрыша билетов за оплаченную подписку (отдельный модуль от Contest*).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0125'
down_revision: Union[str, None] = '0124'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'raffle_campaigns',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column('name', sa.String(255), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='draft'),
        sa.Column('starts_at', sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('ends_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('max_winners', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('prize_type', sa.String(20), nullable=False, server_default='custom'),
        sa.Column('prize_value', sa.Integer(), nullable=True),
        sa.Column('prize_text', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index('ix_raffle_campaigns_id', 'raffle_campaigns', ['id'])
    op.create_index('ix_raffle_campaigns_status', 'raffle_campaigns', ['status'])

    op.create_table(
        'raffle_tickets',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            'campaign_id',
            sa.Integer(),
            sa.ForeignKey('raffle_campaigns.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column('ticket_code', sa.String(64), nullable=False),
        sa.Column('source_transaction_id', sa.Integer(), nullable=False),
        sa.Column(
            'tariff_id',
            sa.Integer(),
            sa.ForeignKey('tariffs.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('campaign_id', 'source_transaction_id', name='uq_raffle_tickets_campaign_tx'),
        sa.UniqueConstraint('ticket_code', name='uq_raffle_tickets_ticket_code'),
    )
    op.create_index('ix_raffle_tickets_id', 'raffle_tickets', ['id'])
    op.create_index('ix_raffle_tickets_campaign_id', 'raffle_tickets', ['campaign_id'])
    op.create_index('ix_raffle_tickets_user_id', 'raffle_tickets', ['user_id'])
    op.create_index('ix_raffle_tickets_ticket_code', 'raffle_tickets', ['ticket_code'])
    op.create_index('ix_raffle_tickets_campaign_user', 'raffle_tickets', ['campaign_id', 'user_id'])

    op.create_table(
        'raffle_winners',
        sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            'campaign_id',
            sa.Integer(),
            sa.ForeignKey('raffle_campaigns.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'user_id',
            sa.Integer(),
            sa.ForeignKey('users.id', ondelete='CASCADE'),
            nullable=False,
        ),
        sa.Column(
            'ticket_id',
            sa.Integer(),
            sa.ForeignKey('raffle_tickets.id', ondelete='SET NULL'),
            nullable=True,
        ),
        sa.Column('ticket_code', sa.String(64), nullable=True),
        sa.Column('place', sa.Integer(), nullable=False, server_default='1'),
        sa.Column('prize_type', sa.String(20), nullable=True),
        sa.Column('prize_value', sa.Integer(), nullable=True),
        sa.Column('prize_text', sa.Text(), nullable=True),
        sa.Column('awarded', sa.Boolean(), nullable=False, server_default=sa.text('false')),
        sa.Column('awarded_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint('campaign_id', 'user_id', name='uq_raffle_winners_campaign_user'),
    )
    op.create_index('ix_raffle_winners_id', 'raffle_winners', ['id'])
    op.create_index('ix_raffle_winners_campaign_id', 'raffle_winners', ['campaign_id'])
    op.create_index('ix_raffle_winners_user_id', 'raffle_winners', ['user_id'])


def downgrade() -> None:
    op.drop_table('raffle_winners')
    op.drop_table('raffle_tickets')
    op.drop_table('raffle_campaigns')
