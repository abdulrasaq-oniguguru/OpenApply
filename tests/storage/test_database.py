from pathlib import Path

from openapply.storage.database import LATEST_SCHEMA, Database


def test_initializes_all_migrations_once(tmp_path: Path) -> None:
    database = Database(tmp_path / "agent.db")
    database.initialize()
    database.initialize()

    with database.read() as connection:
        versions = connection.execute(
            "SELECT version FROM schema_migrations ORDER BY version"
        ).fetchall()
        tables = {
            str(row["name"])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }

    assert [int(row["version"]) for row in versions] == list(range(1, LATEST_SCHEMA + 1))
    assert {"interview_sessions", "tasks", "applications", "channel_bindings"} <= tables
