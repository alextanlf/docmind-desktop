from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.storage.models import Base

config = context.config

if config.config_file_name is not None:
    # 🔴 必须显式 `disable_existing_loggers=False`。
    # `fileConfig` 的默认值是 **True**，它会把**所有在调用前已存在的 logger** 设成
    # `disabled = True`（实测：`app.search.tavily` 从 False 变 True，`isEnabledFor(WARNING)`
    # 随之变 False，之后 `logger.warning(...)` 直接返回、记录根本不产生）。
    #
    # 而 `app/storage/database.py` 是在**应用启动时进程内**跑 `command.upgrade(...)` 的，
    # 那时 `app.*` 的所有 logger 都已在导入期创建完毕。所以用默认值等于：**应用一启动就
    # 静默关掉自己的全部日志**，而且不报错、不提示 —— 排查线上问题时表现为"日志里什么都没有"。
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    configuration = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
