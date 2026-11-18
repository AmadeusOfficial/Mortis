# 迁移和备份的做法改编自 Amane（https://github.com/sqzw-x/amane，GPL-3.0）。

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from alembic.util.exc import CommandError
from sqlalchemy import create_engine, event

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
BACKUPS_TO_KEEP = 3


def _alembic_config(db_path: Path | None = None) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS_DIR))
    if db_path is not None:
        config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return config


def _current_heads(db_path: Path) -> set[str]:
    engine = create_engine(f"sqlite:///{db_path}")
    try:
        with engine.connect() as conn:
            return set(MigrationContext.configure(conn).get_current_heads())
    finally:
        engine.dispose()


def _needs_upgrade(db_path: Path) -> bool:
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return True
    script = ScriptDirectory.from_config(_alembic_config())
    current = _current_heads(db_path)
    if current == set(script.get_heads()):
        return False
    unknown = []
    for revision in sorted(current):
        try:
            script.get_revision(revision)
        except CommandError:
            unknown.append(revision)
    if unknown:
        raise RuntimeError(
            f"数据库 {db_path} 的版本 {', '.join(unknown)} 不在当前程序的迁移脚本里，"
            "库版本高于当前代码；没有备份也没有升级。请升级程序，或从备份恢复。"
        )
    return True


def _backup(db_path: Path) -> Path | None:
    if not db_path.is_file() or db_path.stat().st_size == 0:
        return None
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    backup_path = db_path.with_name(f"{db_path.name}.pre-migrate-{stamp}.bak")
    # WAL 模式下只复制主文件会丢数据，必须用在线备份。
    source = sqlite3.connect(db_path)
    try:
        target = sqlite3.connect(backup_path)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    old = sorted(
        db_path.parent.glob(f"{db_path.name}.pre-migrate-*.bak"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    for stale in old[BACKUPS_TO_KEEP:]:
        stale.unlink(missing_ok=True)
    return backup_path


def upgrade_database(db_path: Path) -> Path | None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if not _needs_upgrade(db_path):
        return None
    backup_path = _backup(db_path)

    engine = create_engine(f"sqlite:///{db_path}")

    @event.listens_for(engine, "connect")
    def _transactional_ddl(dbapi_connection, _connection_record) -> None:
        # 一次迁移失败时，DDL 和版本号一起回滚。
        dbapi_connection.autocommit = False

    config = _alembic_config(db_path)
    try:
        with engine.connect() as conn:
            config.attributes["connection"] = conn
            command.upgrade(config, "head")
            conn.commit()
    finally:
        engine.dispose()
    return backup_path
