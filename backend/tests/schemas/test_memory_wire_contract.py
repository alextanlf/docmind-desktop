import pytest
from pydantic import ValidationError

from app.schemas.memory import DistillationTarget, MemoryListInput


def test_yuque_target_requires_repository_id():
    with pytest.raises(ValidationError):
        DistillationTarget(target="yuque")


def test_memory_list_requires_nonempty_scope():
    with pytest.raises(ValidationError):
        MemoryListInput(repository_ids=[])


def test_local_target_rejects_repository_id():
    with pytest.raises(ValidationError):
        DistillationTarget(target="local", repositoryId="00000000-0000-0000-0000-000000000001")
