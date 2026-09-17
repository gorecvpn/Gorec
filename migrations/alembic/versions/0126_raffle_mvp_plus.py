"""raffle MVP+: prize slots, issue rules, fairness, multi-ticket, per-tariff

Revision ID: 0126
Revises: 0125
Create Date: 2026-09-18
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0126'
down_revision: Union[str, None] = '0125'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('raffle_campaigns', sa.Column('prize_slots', sa.JSON(), nullable=True))
    op.add_column(
        'raffle_campaigns',
        sa.Column('tickets_per_purchase', sa.Integer(), nullable=False, server_default='1'),
    )
    op.add_column('raffle_campaigns', sa.Column('tickets_by_tariff', sa.JSON(), nullable=True))
    op.add_column(
        'raffle_campaigns',
        sa.Column('skip_trial_purchases', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )
    op.add_column('raffle_campaigns', sa.Column('draw_seed', sa.String(64), nullable=True))
    op.add_column('raffle_campaigns', sa.Column('draw_algorithm', sa.String(64), nullable=True))
    op.add_column('raffle_campaigns', sa.Column('drawn_at', sa.DateTime(timezone=True), nullable=True))

    op.add_column(
        'raffle_tickets',
        sa.Column('ticket_index', sa.Integer(), nullable=False, server_default='0'),
    )
    op.drop_constraint('uq_raffle_tickets_campaign_tx', 'raffle_tickets', type_='unique')
    op.create_unique_constraint(
        'uq_raffle_tickets_campaign_tx_idx',
        'raffle_tickets',
        ['campaign_id', 'source_transaction_id', 'ticket_index'],
    )


def downgrade() -> None:
    op.drop_constraint('uq_raffle_tickets_campaign_tx_idx', 'raffle_tickets', type_='unique')
    op.drop_column('raffle_tickets', 'ticket_index')
    op.create_unique_constraint(
        'uq_raffle_tickets_campaign_tx',
        'raffle_tickets',
        ['campaign_id', 'source_transaction_id'],
    )
    op.drop_column('raffle_campaigns', 'drawn_at')
    op.drop_column('raffle_campaigns', 'draw_algorithm')
    op.drop_column('raffle_campaigns', 'draw_seed')
    op.drop_column('raffle_campaigns', 'skip_trial_purchases')
    op.drop_column('raffle_campaigns', 'tickets_by_tariff')
    op.drop_column('raffle_campaigns', 'tickets_per_purchase')
    op.drop_column('raffle_campaigns', 'prize_slots')
