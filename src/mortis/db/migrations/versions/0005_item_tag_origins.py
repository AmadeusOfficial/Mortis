"""标签按来源记录

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-19 15:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 字段被锁定、或字段来源记着 manual 的标签算手动的；其余无法判断，记为刮削。
# 一条标签只对应一个字段（按类别、条目层级和数据类型匹配），多个字段指向同一类别时取手动优先。
_BACKFILL = """
insert into item_tag_origins (item_id, tag_id, origin)
select it.item_id, it.tag_id,
       case when coalesce(max(
           case
               when f.field_key is null then 0
               when exists (
                   select 1 from json_each(i.locked_fields) lk where lk.value = f.field_key
               ) then 1
               when exists (
                   select 1 from json_each(i.field_sources) fs
                   where fs.key = f.field_key and fs.value = 'manual'
               ) then 1
               else 0
           end
       ), 0) = 1 then 'manual' else 'scrape' end
from item_tags it
join items i on i.id = it.item_id
join tags t on t.id = it.tag_id
left join type_fields f
  on f.data_type_id = i.data_type_id
 and f.applies_to = i.kind
 and f.stored_in = 'tags'
 and coalesce(f.tag_kind, f.field_key) = t.tag_kind
group by it.item_id, it.tag_id
"""


def upgrade() -> None:
    op.create_table(
        "item_tag_origins",
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.Column("origin", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["tags.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id", "tag_id", "origin"),
    )
    op.create_index("ix_item_tag_origins_tag", "item_tag_origins", ["tag_id"])
    op.execute(sa.text(_BACKFILL))
    connection = op.get_bind()
    expected = connection.execute(sa.text("select count(*) from item_tags")).scalar_one()
    actual = connection.execute(sa.text("select count(*) from item_tag_origins")).scalar_one()
    if actual != expected:
        raise RuntimeError(
            f"标签来源回填数量不对：item_tags 有 {expected} 条，只回填了 {actual} 条；"
            "升级已回滚，数据库没有改动。"
        )


def downgrade() -> None:
    # 只删来源表；item_tags 就是各来源标签的并集，展示和 NFO 不受影响。
    op.drop_index("ix_item_tag_origins_tag", table_name="item_tag_origins")
    op.drop_table("item_tag_origins")
