from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import inspect

from app.storage.database import Database


def test_phase3a_migration_creates_ollama_pulls_after_phase2(tmp_path: Path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'db.sqlite'}")
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "migrations"))
    config.set_main_option("sqlalchemy.url", database.url)
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0010_phase3a_ollama_pulls")
    columns = {column["name"] for column in inspect(database.engine).get_columns("ollama_pulls")}
    assert {"id", "model_name", "base_url", "state", "progress", "error_code", "last_event_sequence"} <= columns


def test_phase3a_migration_has_state_guard_and_active_address_index(tmp_path: Path):
    database = Database(f"sqlite+pysqlite:///{tmp_path / 'db.sqlite'}")
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    config.set_main_option("script_location", str(Path(__file__).parents[2] / "migrations"))
    config.set_main_option("sqlalchemy.url", database.url)
    with database.engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0010_phase3a_ollama_pulls")

    table = inspect(database.engine).get_table_names()
    assert "ollama_pulls" in table
    indexes = inspect(database.engine).get_indexes("ollama_pulls")
    assert any(index.get("unique") for index in indexes)
