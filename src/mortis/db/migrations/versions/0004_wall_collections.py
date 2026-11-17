"""库的海报墙怎么显示系列电影

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-17 20:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 约束名不带前缀：batch 操作会按命名约定补成 ck_libraries_wall_collections。
_CHECK_NAME = "wall_collections"


def upgrade() -> None:
    with op.batch_alter_table("libraries", recreate="always") as batch_op:
        batch_op.add_column(
            sa.Column(
                "wall_collections",
                sa.Text(),
                server_default=sa.text("'collapse'"),
                nullable=False,
            )
        )
        batch_op.create_check_constraint(
            _CHECK_NAME, "wall_collections in ('movies', 'both', 'collapse')"
        )


def downgrade() -> None:
    with op.batch_alter_table("libraries", recreate="always") as batch_op:
        batch_op.drop_constraint(_CHECK_NAME, type_="check")
        batch_op.drop_column("wall_collections")
