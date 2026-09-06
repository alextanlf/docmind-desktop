from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.storage.database import Database


def test_memory_migration_has_authoritative_vector_ownership(database):
    columns = {c["name"] for c in inspect(database.engine).get_columns("memory_chunks")}
    assert {"vector_id", "repository_id", "summary_id", "distillation_id", "token_count"} <= columns
    assert {"session_summaries", "distillations", "memory_vector_cleanups"} <= set(inspect(database.engine).get_table_names())


def test_existing_0008_database_receives_forward_memory_contract_migration(tmp_path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'legacy.db'}")
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database.url)
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0008_phase2_memory")
    assert "vector_id" not in {column["name"] for column in inspect(database.engine).get_columns("memory_chunks")}
    database.upgrade()
    columns = {column["name"] for column in inspect(database.engine).get_columns("memory_chunks")}
    assert {"vector_id", "indexed", "token_count"} <= columns
