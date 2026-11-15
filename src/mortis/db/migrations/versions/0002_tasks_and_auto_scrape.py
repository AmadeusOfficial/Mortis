"""任务的优先级和父子关系，库的自动刮削开关

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15 20:45:02.909371
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("libraries", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("is_auto_scrape", sa.Integer(), server_default=sa.text("1"), nullable=False)
        )

    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("priority", sa.Integer(), server_default=sa.text("0"), nullable=False)
        )
        batch_op.add_column(sa.Column("parent_task_id", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("root_task_id", sa.Integer(), nullable=True))
        batch_op.create_index("ix_tasks_parent", ["parent_task_id"], unique=False)
        batch_op.create_index("ix_tasks_root", ["root_task_id"], unique=False)
        batch_op.create_foreign_key(
            batch_op.f("fk_tasks_parent_task_id_tasks"),
            "tasks",
            ["parent_task_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("tasks", schema=None) as batch_op:
        batch_op.drop_constraint(batch_op.f("fk_tasks_parent_task_id_tasks"), type_="foreignkey")
        batch_op.drop_index("ix_tasks_root")
        batch_op.drop_index("ix_tasks_parent")
        batch_op.drop_column("root_task_id")
        batch_op.drop_column("parent_task_id")
        batch_op.drop_column("priority")

    with op.batch_alter_table("libraries", schema=None) as batch_op:
        batch_op.drop_column("is_auto_scrape")
