"""raffle: tickets per month of the purchased subscription period

Revision ID: 0129
Revises: 0128
Create Date: 2026-09-25
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = '0129'
down_revision: Union[str, None] = '0128'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ON for new and existing campaigns: 1 month = 1 ticket, 3 = 3, 6 = 6, 12 = 12
    # (× per-tariff / default tickets count, which becomes "tickets per month").
    op.add_column(
        'raffle_campaigns',
        sa.Column('tickets_per_month', sa.Boolean(), nullable=False, server_default=sa.text('true')),
    )


def downgrade() -> None:
    op.drop_column('raffle_campaigns', 'tickets_per_month')
