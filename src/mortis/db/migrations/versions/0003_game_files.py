"""media_files 允许游戏条目

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16 12:40:00.000000
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 约束名不带前缀：batch 操作会按命名约定补成 ck_media_files_file_kind。
_CHECK_NAME = "file_kind"
_OLD_CHECK = "file_kind in ('video', 'subtitle', 'trailer', 'nfo', 'image')"
_NEW_CHECK = "file_kind in ('video', 'subtitle', 'trailer', 'nfo', 'image', 'game')"


def _replace_check(expression: str) -> None:
    # SQLite 改不了已有 check，只能按新定义重建表；batch 反射会带上索引、path 唯一约束和外键。
    with op.batch_alter_table("media_files", recreate="always") as batch_op:
        batch_op.drop_constraint(_CHECK_NAME, type_="check")
        batch_op.create_check_constraint(_CHECK_NAME, expression)


def upgrade() -> None:
    _replace_check(_NEW_CHECK)


def downgrade() -> None:
    op.execute("delete from media_files where file_kind = 'game'")
    _replace_check(_OLD_CHECK)
