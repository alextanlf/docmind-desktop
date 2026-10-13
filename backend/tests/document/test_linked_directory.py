"""链接型目录批次：清单指向用户的目录，字节一份都不复制。

这一组守的是两条同时成立的性质：

1. **不复制**：`collections/<id>/` 下只有一份清单，没有 items。
2. **一致性不降级**：清单里记的 sha256 在读取时**重新核对**，文件在"用户确认"之后
   被改过，那一条就要以 `BATCH_SOURCE_CHANGED` 失败 —— 与今天拷贝型完全一样。
   改成就地读最容易悄悄丢掉的就是这一条。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from app.api.errors import DomainError
from app.config import AppSettings
from app.document.discovery import DirectoryDiscovery
from app.document.path_tokens import DIRECTORY_DOMAIN, path_token
from app.document.sources import CollectionCacheRequest, CollectionCacheStore
from app.document.staging_manifest import (
    StagingManifest,
    entry_path,
    linked_directory_root,
    load_manifest,
    validate_manifest,
)
from app.schemas.batches import CachedSourceRef, DiscoveryRequest

SECRET = "linked-directory-test-secret"

#: 集合标识在真实链路里是主进程生成的 UUID：`CollectionCacheStore` 会校验这一点。
#: 夹具用真 UUID，否则"发现"能过、"导入"却会在校验那一步炸 —— 那是夹具不真实，
#: 不是被测代码的问题。
COLLECTION_ID = "11111111-1111-1111-1111-111111111111"


def _write_manifest(staging_root: Path, root: Path, entries: list[dict], *, token: str | None) -> Path:
    collection = staging_root / "collections" / COLLECTION_ID
    collection.mkdir(parents=True, exist_ok=True)
    manifest = {
        "rootId": "r1",
        "rootPath": str(root),
        "rootToken": token if token is not None else path_token(SECRET, DIRECTORY_DOMAIN, str(root)),
        "files": entries,
    }
    path = collection / "manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _entry(root: Path, relative: str, media_type: str = "text/markdown") -> dict:
    payload = (root / relative).read_bytes()
    return {
        "relativePath": relative,
        "stagedId": str(uuid4()),
        "mediaType": media_type,
        "sizeBytes": len(payload),
        "sha256": hashlib.sha256(payload).hexdigest(),
    }


@pytest.fixture
def linked(tmp_path: Path) -> tuple[Path, Path]:
    """一个用户的目录 + 一份指向它的清单。"""
    root = tmp_path / "资料"
    (root / "docs").mkdir(parents=True)
    (root / "docs" / "a.md").write_text("# Alpha\n", encoding="utf-8")
    (root / "b.md").write_text("# Beta\n", encoding="utf-8")
    staging_root = tmp_path / "staging"
    _write_manifest(
        staging_root,
        root,
        [_entry(root, "docs/a.md"), _entry(root, "b.md")],
        token=None,
    )
    return staging_root, root


def _discover(staging_root: Path, secret: str | None = SECRET):
    request = DiscoveryRequest(
        batch_id=UUID(int=1),
        source_kind="staged_directory",
        source_descriptor={"collectionId": COLLECTION_ID},
        repository_id=UUID(int=2),
    )
    return DirectoryDiscovery(staging_root, secret=secret).discover(request, lambda _: None)


@pytest.mark.asyncio
async def test_a_linked_directory_is_discovered_without_copying_anything(
    linked: tuple[Path, Path]
) -> None:
    staging_root, root = linked

    result = await _discover(staging_root)

    assert [source.display_path for source in result.sources] == ["b.md", "docs/a.md"]
    # 引用里只有相对路径 —— 用户目录的绝对路径不进 chunk，也不进引用。
    assert all(str(root) not in source.display_path for source in result.sources)
    assert all(str(root) not in source.source_identity for source in result.sources)
    # 暂存里只有那份清单。
    assert sorted(path.name for path in (staging_root / "collections" / COLLECTION_ID).iterdir()) == [
        "manifest.json"
    ]


@pytest.mark.asyncio
async def test_a_file_changed_after_confirmation_is_caught(
    linked: tuple[Path, Path]
) -> None:
    """一致性保证不降级：这是改成就地读最容易悄悄丢掉的那一条。

    **长度必须保持不变**（`# Beta\n` → `# Beto\n`，都是 7 字节）：否则大小检查就先把它
    拦下了，这条用例会在"摘要核对被整个删掉"时依然通过 —— 那是假的绿。
    """
    staging_root, root = linked
    (root / "b.md").write_bytes(b"# Beto\n")

    with pytest.raises(DomainError) as error:
        await _discover(staging_root)

    assert (error.value.code, error.value.retryable) == ("BATCH_SOURCE_CHANGED", False)


@pytest.mark.asyncio
async def test_a_directory_that_moved_away_says_so(linked: tuple[Path, Path]) -> None:
    """外接盘拔了、目录被挪走 —— 说清楚，别退化成 500。"""
    staging_root, root = linked
    for entry in sorted(root.rglob("*"), reverse=True):
        entry.unlink() if entry.is_file() else entry.rmdir()
    root.rmdir()

    with pytest.raises(DomainError) as error:
        await _discover(staging_root)

    assert error.value.code == "BATCH_SOURCE_CHANGED"


@pytest.mark.asyncio
async def test_a_forged_root_token_is_refused(linked: tuple[Path, Path]) -> None:
    """目录授权就是签名本身：换掉它，这个目录就不再被信任。"""
    staging_root, root = linked
    _write_manifest(
        staging_root,
        root,
        [_entry(root, "b.md")],
        token=path_token("another-secret", DIRECTORY_DOMAIN, str(root)),
    )

    with pytest.raises(DomainError) as error:
        await _discover(staging_root)

    assert error.value.code == "BATCH_SOURCE_CHANGED"


@pytest.mark.asyncio
async def test_a_linked_manifest_is_refused_when_there_is_no_key_to_check_it(
    linked: tuple[Path, Path]
) -> None:
    """没有密钥就判断不了"这个目录被授权过吗" —— 拒绝，而不是默认信任。"""
    staging_root, _ = linked

    with pytest.raises(DomainError):
        await _discover(staging_root, secret=None)


@pytest.mark.asyncio
async def test_a_symlinked_subdirectory_cannot_escape_the_granted_root(
    tmp_path: Path
) -> None:
    """`..` 挡在字符串层面，符号链接挡不住 —— 所以解析之后必须再查一次包含性。

    这是就地读相对拷贝型**新增**的攻击面：拷贝型读的是我们自己放好的文件，
    而这里读的是用户目录里的路径，中间任何一段都可能是链接。
    """
    outside = tmp_path / "外面"
    outside.mkdir()
    (outside / "secret.md").write_text("# 不该被读到\n", encoding="utf-8")
    root = tmp_path / "资料"
    root.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    staging_root = tmp_path / "staging"
    # 清单里记的是"合法"的相对路径，逃逸发生在磁盘上的链接里。
    _write_manifest(
        staging_root,
        root,
        [
            {
                "relativePath": "escape/secret.md",
                "stagedId": str(uuid4()),
                "mediaType": "text/markdown",
                "sizeBytes": (outside / "secret.md").stat().st_size,
                "sha256": hashlib.sha256((outside / "secret.md").read_bytes()).hexdigest(),
            }
        ],
        token=None,
    )

    with pytest.raises(DomainError) as error:
        await _discover(staging_root)

    assert error.value.code == "BATCH_SOURCE_CHANGED"


def test_entry_path_answers_the_same_question_for_both_forms(
    linked: tuple[Path, Path]
) -> None:
    """两种形态的"字节在哪"只有一个答案来源 —— 三处各拼一次路径迟早会漏掉检查。"""
    staging_root, root = linked
    manifest = load_manifest(staging_root / "collections" / COLLECTION_ID / "manifest.json")
    entries = validate_manifest(
        manifest, staging_root / "collections" / COLLECTION_ID, secret=SECRET
    )
    linked_root = linked_directory_root(manifest, SECRET)

    paths = {
        entry_path(manifest, staging_root / "collections" / COLLECTION_ID, entry, linked_root=linked_root)
        for entry in entries
    }

    assert paths == {root / "b.md", root / "docs" / "a.md"}
    assert all(path.is_file() for path in paths)


def test_a_manifest_without_a_root_still_resolves_to_staged_items() -> None:
    """拷贝型没有被这次改动影响：仍然在 collections/<id>/items/ 下。"""
    manifest = StagingManifest.model_validate(
        {
            "rootId": "r1",
            "files": [
                {
                    "relativePath": "a.md",
                    "stagedId": "00000000-0000-0000-0000-000000000001",
                    "mediaType": "text/markdown",
                    "sizeBytes": 1,
                    "sha256": "a" * 64,
                }
            ],
        }
    )

    path = entry_path(manifest, Path("/staging/collections/c1"), manifest.files[0])

    assert path == Path("/staging/collections/c1/items/00000000-0000-0000-0000-000000000001")
    assert linked_directory_root(manifest, None) is None


def _manifest_at(staging_root: Path) -> StagingManifest:
    return load_manifest(staging_root / "collections" / COLLECTION_ID / "manifest.json")


def test_validate_manifest_itself_rejects_an_escaping_entry(tmp_path: Path) -> None:
    """直接测校验层，而不是穿过 discover。

    discover 里还有第二道包含性检查（`_candidate`），它会把逃逸挡在前面 ——
    于是"删掉校验层的那一道检查"这个变异**测不出来**。两道检查是好事，
    但测试必须有一处直接钉住其中一道，否则哪一道坏了都没人知道。
    """
    outside = tmp_path / "外面"
    outside.mkdir()
    (outside / "secret.md").write_text("# 不该被读到\n", encoding="utf-8")
    root = tmp_path / "资料"
    root.mkdir()
    (root / "escape").symlink_to(outside, target_is_directory=True)
    staging_root = tmp_path / "staging"
    _write_manifest(
        staging_root,
        root,
        [
            {
                "relativePath": "escape/secret.md",
                "stagedId": str(uuid4()),
                "mediaType": "text/markdown",
                "sizeBytes": (outside / "secret.md").stat().st_size,
                "sha256": hashlib.sha256((outside / "secret.md").read_bytes()).hexdigest(),
            }
        ],
        token=None,
    )

    with pytest.raises(DomainError) as error:
        validate_manifest(
            _manifest_at(staging_root),
            staging_root / "collections" / COLLECTION_ID,
            secret=SECRET,
        )

    assert error.value.code == "BATCH_SOURCE_CHANGED"


def test_validate_manifest_itself_rechecks_the_digest(linked: tuple[Path, Path]) -> None:
    """同样地，摘要核对要在校验层直接可见：清单是"用户确认时"的快照，不是免检通行证。

    同样保持长度不变，只改内容 —— 让这条用例只能由摘要核对来发现。
    """
    staging_root, root = linked
    (root / "b.md").write_bytes(b"# Beto\n")

    with pytest.raises(DomainError) as error:
        validate_manifest(
            _manifest_at(staging_root),
            staging_root / "collections" / COLLECTION_ID,
            secret=SECRET,
        )

    assert error.value.code == "BATCH_SOURCE_CHANGED"


def _collection_settings(tmp_path: Path) -> AppSettings:
    """一个 data_dir，使 `settings.staging_dir` 正好是这些用例写清单的地方。

    `CollectionCacheStore` 从 settings 推出暂存目录，而发现那条路是直接收一个
    staging_root —— 两者必须指向同一个位置，否则测试会在"清单不存在"上失败，
    而那与用例要验的东西无关。
    """
    return AppSettings(session_token=SECRET, data_dir=tmp_path, environment="test")


def test_the_loader_hands_over_the_users_path_not_a_copy(tmp_path: Path) -> None:
    """链接型集合加载出来的文档必须带 `source_path`。

    这一行是整个目录批次的**归属**所在：少了它，导入那边会把字节当成"我们自己拷进来的
    副本"，于是给 PDF 再写一份 `raw.bin`、并且永远显示不出"原件被改过"。
    不报错、不崩，只是静默多存一份 —— 所以必须有一条用例盯着它。
    """
    settings = _collection_settings(tmp_path)
    root = tmp_path / "资料"
    root.mkdir()
    (root / "beta.md").write_bytes(b"# Beta\n")
    staging_root = settings.staging_dir
    _write_manifest(
        staging_root,
        root,
        [_entry(root, "beta.md")],
        token=None,
    )
    manifest = _manifest_at(staging_root)
    entry = manifest.files[0]
    request = CollectionCacheRequest(
        key="item-1",
        cache_ref=CachedSourceRef(
            cache_id=UUID(entry.staged_id),
            media_type=entry.media_type,
            byte_size=entry.size_bytes,
            sha256=entry.sha256,
        ),
        source_identity=f"folder:{manifest.root_id}:{entry.relative_path}",
        source_revision=entry.sha256,
        display_path=entry.relative_path,
        title="Beta",
    )

    loaded = CollectionCacheStore(settings).load(COLLECTION_ID, request)

    assert loaded.source_path == root / entry.relative_path
    assert loaded.raw_bytes == b"# Beta\n"


def test_a_staged_collection_still_reports_no_source_path(tmp_path: Path) -> None:
    """拷贝型（`staging/remote/` 那类远程缓存）没有被顺手改掉：它没有用户路径可交。"""
    settings = _collection_settings(tmp_path)
    collection = settings.staging_dir / "collections" / COLLECTION_ID
    (collection / "items").mkdir(parents=True)
    payload = b"# Remote\n"
    staged_id = "00000000-0000-0000-0000-0000000000aa"
    (collection / "items" / staged_id).write_bytes(payload)
    sha = hashlib.sha256(payload).hexdigest()
    (collection / "manifest.json").write_text(
        json.dumps(
            {
                "rootId": "r9",
                "files": [
                    {
                        "relativePath": "remote.md",
                        "stagedId": staged_id,
                        "mediaType": "text/markdown",
                        "sizeBytes": len(payload),
                        "sha256": sha,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    request = CollectionCacheRequest(
        key="item-1",
        cache_ref=CachedSourceRef(
            cache_id=UUID(staged_id),
            media_type="text/markdown",
            byte_size=len(payload),
            sha256=sha,
        ),
        source_identity="folder:r9:remote.md",
        source_revision=sha,
        display_path="remote.md",
        title="Remote",
    )

    loaded = CollectionCacheStore(settings).load(COLLECTION_ID, request)

    assert loaded.source_path is None
