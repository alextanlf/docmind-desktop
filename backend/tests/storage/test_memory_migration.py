from sqlalchemy import inspect


def test_memory_migration_has_authoritative_vector_ownership(database):
    columns = {c["name"] for c in inspect(database.engine).get_columns("memory_chunks")}
    assert {"vector_id", "repository_id", "summary_id", "distillation_id", "token_count"} <= columns
    assert {"session_summaries", "distillations", "memory_vector_cleanups"} <= set(inspect(database.engine).get_table_names())

