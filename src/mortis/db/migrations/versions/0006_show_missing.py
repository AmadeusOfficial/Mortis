"""媒体库和单部作品的缺失集开关

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-19 19:20:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("libraries", schema=None) as batch_op:
        batch_op.add_column(sa.Column("show_missing_episodes", sa.Integer(), nullable=True))
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("show_missing_episodes", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_column("show_missing_episodes")
    with op.batch_alter_table("libraries", schema=None) as batch_op:
        batch_op.drop_column("show_missing_episodes")
