from __future__ import annotations

from app.storage.database import Database
from app.storage.repositories import VersionStore


def test_version_snapshot_assigns_monotonic_number_and_trims(database: Database) -> None:
    store = VersionStore(database)

    for index in range(25):
        store.snapshot("doc-1", f"Title {index}", f"# Content {index}")

    versions = store.list("doc-1")
    assert [version.version_no for version in versions] == list(range(6, 26))
    assert versions[-1].title == "Title 24"
