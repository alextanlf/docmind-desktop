from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import httpx
import pytest
import respx
from pydantic import SecretStr

from app.api.errors import DomainError
from app.config import AppSettings
from app.document.downloader import DocumentDownloader
from app.document.sources import SourceInspector, SourceValidator, StagedFileStore
from app.schemas.imports import SourceRef


@pytest.fixture
def document_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        session_token=SecretStr("document-test-token"), data_dir=tmp_path / "docmind", environment="test"
    )


@pytest.fixture
def source_validator() -> SourceValidator:
    return SourceValidator()


@pytest.fixture
def staged_store(document_settings: AppSettings) -> StagedFileStore:
    return StagedFileStore(document_settings)


@pytest.fixture
def downloader(document_settings: AppSettings) -> DocumentDownloader:
    return DocumentDownloader(document_settings)


@pytest.mark.parametrize(
    "value",
    [
        "file:///etc/passwd",
        "ftp://example.com/file.md",
        "https://user:pass@example.com/private",
        "https://127.0.0.1/private",
        "https://10.0.0.4/private",
        "https://100.64.0.4/private",
        "https://169.254.0.4/private",
        "https://172.16.0.4/private",
        "https://192.168.0.4/private",
        "https://[::1]/private",
        "https://[fc00::1]/private",
    ],
)
def test_validator_rejects_unsupported_or_nonpublic_source_url(
    source_validator: SourceValidator, value: str
) -> None:
    with pytest.raises(DomainError) as error:
        source_validator.validate_url(value)
    assert (error.value.code, error.value.retryable) == ("SOURCE_UNSUPPORTED", False)


@pytest.mark.parametrize(
    "value",
    ["https://8.8.8.8/guide.md", "https://[2606:4700:4700::1111]/guide.md", "https://docs.test/guide.md"],
)
def test_validator_allows_public_literal_ips_and_hostnames(
    source_validator: SourceValidator, value: str
) -> None:
    assert source_validator.validate_url(value) == value


def test_staged_source_requires_one_direct_uuid_named_file(staged_store: StagedFileStore) -> None:
    staged_id = str(uuid4())
    staged_path = staged_store.staging_dir / f"{staged_id}.md"
    staged_path.write_text("# Guide", encoding="utf-8")

    assert staged_store.resolve(staged_id) == staged_path.resolve()

    (staged_store.staging_dir / f"{staged_id}.txt").write_text("duplicate", encoding="utf-8")
    with pytest.raises(DomainError) as error:
        staged_store.resolve(staged_id)
    assert (error.value.code, error.value.retryable) == ("SOURCE_UNSUPPORTED", False)


@pytest.mark.parametrize("value", ["../../etc/passwd", "not-a-uuid", str(uuid4())])
def test_staged_source_rejects_escape_invalid_or_missing_id(staged_store: StagedFileStore, value: str) -> None:
    with pytest.raises(DomainError) as error:
        staged_store.resolve(value)
    assert error.value.code == "SOURCE_UNSUPPORTED"


def test_staged_source_rejects_symlink_that_resolves_outside_root(
    staged_store: StagedFileStore, tmp_path: Path
) -> None:
    staged_id = str(uuid4())
    outside = tmp_path / "outside.md"
    outside.write_text("outside", encoding="utf-8")
    (staged_store.staging_dir / f"{staged_id}.md").symlink_to(outside)

    with pytest.raises(DomainError) as error:
        staged_store.resolve(staged_id)
    assert error.value.code == "SOURCE_UNSUPPORTED"


@pytest.mark.parametrize(
    ("suffix", "content", "want"),
    [
        (".txt", b"not supported", "SOURCE_UNSUPPORTED"),
        (".html", b"<h1>not staged HTML</h1>", "SOURCE_UNSUPPORTED"),
        (".pdf", b"not a PDF", "SOURCE_UNSUPPORTED"),
        (".md", b"\xff", "SOURCE_UNSUPPORTED"),
    ],
)
def test_staged_source_rejects_bad_extension_signature_or_encoding(
    staged_store: StagedFileStore, suffix: str, content: bytes, want: str
) -> None:
    staged_id = str(uuid4())
    (staged_store.staging_dir / f"{staged_id}{suffix}").write_bytes(content)
    with pytest.raises(DomainError) as error:
        staged_store.load(staged_id)
    assert (error.value.code, error.value.retryable) == (want, False)


@pytest.mark.asyncio
async def test_downloader_follows_relative_redirect_only_after_validating_every_hop(
    downloader: DocumentDownloader,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://docs.test/start").mock(
            return_value=httpx.Response(302, headers={"location": "/guide.md"})
        )
        router.get("https://docs.test/guide.md").mock(
            return_value=httpx.Response(
                200, content=b"# Guide", headers={"content-type": "text/markdown; charset=utf-8"}
            )
        )
        document = await downloader.download_url("https://docs.test/start")

    assert document.source_url == "https://docs.test/guide.md"
    assert document.media_type == "text/markdown"
    assert document.raw_bytes == b"# Guide"


@pytest.mark.asyncio
async def test_downloader_follows_valid_absolute_redirect(downloader: DocumentDownloader) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://docs.test/start").mock(
            return_value=httpx.Response(302, headers={"location": "https://cdn.test/guide.md"})
        )
        router.get("https://cdn.test/guide.md").mock(
            return_value=httpx.Response(200, content=b"# Guide", headers={"content-type": "text/markdown"})
        )
        document = await downloader.download_url("https://docs.test/start")
    assert document.source_url == "https://cdn.test/guide.md"


@pytest.mark.asyncio
async def test_downloader_rejects_unsafe_absolute_redirect_before_requesting_it(
    downloader: DocumentDownloader,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://docs.test/start").mock(
            return_value=httpx.Response(302, headers={"location": "http://127.0.0.1/private"})
        )
        with pytest.raises(DomainError) as error:
            await downloader.download_url("https://docs.test/start")

    assert (error.value.code, error.value.retryable) == ("SOURCE_UNSUPPORTED", False)


@pytest.mark.asyncio
async def test_downloader_enforces_five_redirect_limit(downloader: DocumentDownloader) -> None:
    with respx.mock(assert_all_called=True) as router:
        for index in range(6):
            router.get(f"https://docs.test/{index}").mock(
                return_value=httpx.Response(302, headers={"location": f"/{index + 1}"})
            )
        with pytest.raises(DomainError) as error:
            await downloader.download_url("https://docs.test/0")
    assert (error.value.code, error.value.retryable) == ("SOURCE_REDIRECT_LIMIT", False)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("response", "want"),
    [
        (httpx.Response(404), ("SOURCE_DOWNLOAD_FAILED", False)),
        (httpx.Response(503), ("SOURCE_DOWNLOAD_FAILED", True)),
        (httpx.Response(200, content=b"not a document", headers={"content-type": "application/octet-stream"}), ("SOURCE_UNSUPPORTED", False)),
        (httpx.Response(200, content=b"not a PDF", headers={"content-type": "application/pdf"}), ("SOURCE_UNSUPPORTED", False)),
    ],
)
async def test_downloader_maps_status_and_type_failures_to_domain_errors(
    downloader: DocumentDownloader, response: httpx.Response, want: tuple[str, bool]
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://docs.test/file").mock(return_value=response)
        with pytest.raises(DomainError) as error:
            await downloader.download_url("https://docs.test/file")
    assert (error.value.code, error.value.retryable) == want


@pytest.mark.asyncio
async def test_downloader_stops_streaming_as_soon_as_size_limit_is_exceeded(
    downloader: DocumentDownloader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(downloader, "html_markdown_max_bytes", 10)

    async def body():
        yield b"1234567890"
        yield b"one byte too many"
        raise AssertionError("downloader must not buffer a later chunk")

    with respx.mock(assert_all_called=True) as router:
        router.get("https://docs.test/large.md").mock(
            return_value=httpx.Response(200, content=body(), headers={"content-type": "text/markdown"})
        )
        with pytest.raises(DomainError) as error:
            await downloader.download_url("https://docs.test/large.md")
    assert (error.value.code, error.value.retryable) == ("DOCUMENT_TOO_LARGE", False)


@pytest.mark.asyncio
async def test_downloader_rejects_oversized_content_length_before_reading_body(
    downloader: DocumentDownloader, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(downloader, "html_markdown_max_bytes", 10)
    with respx.mock(assert_all_called=True) as router:
        router.get("https://docs.test/declared-large.md").mock(
            return_value=httpx.Response(
                200,
                content=b"small",
                headers={"content-type": "text/markdown", "content-length": "11"},
            )
        )
        with pytest.raises(DomainError) as error:
            await downloader.download_url("https://docs.test/declared-large.md")
    assert (error.value.code, error.value.retryable) == ("DOCUMENT_TOO_LARGE", False)


@pytest.mark.asyncio
async def test_downloader_maps_transport_timeout_to_retryable_domain_error(
    downloader: DocumentDownloader,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://docs.test/slow.md").mock(side_effect=httpx.ReadTimeout("slow"))
        with pytest.raises(DomainError) as error:
            await downloader.download_url("https://docs.test/slow.md")
    assert (error.value.code, error.value.retryable) == ("SOURCE_TIMEOUT", True)


@pytest.mark.asyncio
async def test_downloader_maps_transport_failure_to_retryable_domain_error(
    downloader: DocumentDownloader,
) -> None:
    with respx.mock(assert_all_called=True) as router:
        router.get("https://docs.test/offline.md").mock(side_effect=httpx.ConnectError("offline"))
        with pytest.raises(DomainError) as error:
            await downloader.download_url("https://docs.test/offline.md")
    assert (error.value.code, error.value.retryable) == ("SOURCE_DOWNLOAD_FAILED", True)


@pytest.mark.asyncio
async def test_source_inspector_previews_staged_markdown(staged_store: StagedFileStore, document_settings: AppSettings) -> None:
    staged_id = str(uuid4())
    (staged_store.staging_dir / f"{staged_id}.md").write_text("# Local guide", encoding="utf-8")
    preview = await SourceInspector(document_settings).inspect(SourceRef(kind="staged_file", value=staged_id))
    assert (preview.title, preview.source_kind, preview.media_type, preview.size_bytes) == (
        "Local guide",
        "staged_file",
        "text/markdown",
        len(b"# Local guide"),
    )
