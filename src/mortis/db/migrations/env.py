import os
from pathlib import Path

from alembic import context
from sqlalchemy import engine_from_config, pool

from mortis.db.models import Base

config = context.config
target_metadata = Base.metadata


def _cli_database_url() -> str:
    data_dir = Path(os.environ.get("MORTIS_DATA_DIR") or ".dev-data")
    data_dir.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{data_dir / 'mortis.db'}"


def run_migrations() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            transactional_ddl=True,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()
        return

    # 只有命令行 alembic（生成新的迁移脚本）会走到这里。
    if not config.get_main_option("sqlalchemy.url"):
        config.set_main_option("sqlalchemy.url", _cli_database_url())
    engine = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    try:
        with engine.connect() as conn:
            context.configure(
                connection=conn,
                target_metadata=target_metadata,
                transactional_ddl=True,
                render_as_batch=True,
            )
            with context.begin_transaction():
                context.run_migrations()
            conn.commit()
    finally:
        engine.dispose()


run_migrations()
