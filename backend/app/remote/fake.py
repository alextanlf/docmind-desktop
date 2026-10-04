from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from app.api.errors import DomainError
from app.remote.markers import strip_mutation_marker
from app.remote.provider import ProviderCapabilities, ProviderIdentity
from app.schemas.remote import (
    BrowserInstallResult,
    CreateRemoteDocumentRequest,
    CreateRemoteRepositoryRequest,
    LoginResult,
    LoginStatus,
    RemoteDocument,
    RemoteDocumentContent,
    RemoteRepository,
    UpdateRemoteDocumentRequest,
)


class FakeRemoteProvider:
    """Deterministic in-memory remote provider for API and end-to-end tests."""

    def __init__(
        self,
        data_dir: str | os.PathLike[str] | None = None,
        *,
        name: str = "yuque",
        label: str = "语雀",
    ) -> None:
        self.identity = ProviderIdentity(
            name=name,
            label=label,
            capabilities=ProviderCapabilities(browser_install=True, marker_lookup=True),
        )
        self._lock = asyncio.Lock()
        self._repositories: dict[str, RemoteRepository] = {}
        self._documents: dict[str, RemoteDocumentContent] = {}
        self._login_marker = (
            os.path.join(data_dir, "e2e", "remote-logged-in") if data_dir else None
        )
        self._state_path = (
            os.path.join(data_dir, "e2e", "remote-state.json") if data_dir else None
        )
        self._logged_in = bool(self._login_marker and os.path.exists(self._login_marker))
        if self._state_path:
            import json
            try:
                state = json.loads(Path(self._state_path).read_text(encoding="utf-8"))
                self._repositories = {
                    item["remoteId"]: RemoteRepository.model_validate(item)
                    for item in state.get("repositories", [])
                }
                self._documents = {
                    item["remoteId"]: RemoteDocumentContent.model_validate(item)
                    for item in state.get("documents", [])
                }
            except (FileNotFoundError, json.JSONDecodeError, KeyError, ValueError):
                pass
        self._active_contexts = 0
        self.max_concurrent_contexts = 0
        self.read_calls: list[str] = []
        self.write_calls: list[str] = []

    async def login_status(self) -> LoginStatus:
        async with self._serialized():
            return LoginStatus(
                logged_in=self._logged_in,
                account_label="f***e" if self._logged_in else None,
                requires_login=not self._logged_in,
            )

    async def begin_login(self) -> LoginResult:
        async with self._serialized():
            self._logged_in = True
            if self._login_marker:
                os.makedirs(os.path.dirname(self._login_marker), exist_ok=True)
                Path(self._login_marker).touch()
            return LoginResult(logged_in=True, account_label="f***e", requires_login=False)

    async def install_browser(self) -> BrowserInstallResult:
        return BrowserInstallResult(installed=True, message="测试环境无需安装浏览器")

    async def list_repositories(self) -> list[RemoteRepository]:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            return list(self._repositories.values())

    async def create_repository(self, request: CreateRemoteRepositoryRequest) -> RemoteRepository:
        self.write_calls.append("create_repository")
        async with self._serialized():
            self._require_login(allow_first_use=True)
            repository_id = f"repo-{len(self._repositories) + 1}"
            repository = RemoteRepository(
                remote_id=repository_id,
                name=request.name,
                url=f"https://remote.local/{repository_id}",
            )
            self._repositories[repository_id] = repository
            self._persist_state()
            return repository

    async def list_documents(self, repository_id: str) -> list[RemoteDocument]:
        self.read_calls.append("list_documents")
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._repository(repository_id)
            return [
                _document_summary(document)
                for document in self._documents.values()
                if document.repository_id == repository_id
            ]

    async def create_document(self, request: CreateRemoteDocumentRequest) -> RemoteDocument:
        self.write_calls.append("create_document")
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._repository(request.repository_id)
            document_id = f"doc-{len(self._documents) + 1}"
            document = RemoteDocumentContent(
                remote_id=document_id,
                repository_id=request.repository_id,
                title=request.title,
                content=request.content,
                url=f"https://remote.local/{document_id}",
            )
            self._documents[document_id] = document
            self._persist_state()
            return _document_summary(document)

    async def find_document_by_marker(
        self, repository_id: str, marker: str
    ) -> RemoteDocument | None:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._repository(repository_id)
            return next(
                (
                    _document_summary(document)
                    for document in self._documents.values()
                    if document.repository_id == repository_id and marker in document.content
                ),
                None,
            )

    async def document_exists(self, repository_id: str, document_id: str) -> bool:
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._repository(repository_id)
            document = self._documents.get(document_id)
            return document is not None and document.repository_id == repository_id

    async def read_document(self, document_id: str) -> RemoteDocumentContent:
        self.read_calls.append("read_document")
        async with self._serialized():
            self._require_login(allow_first_use=True)
            document = self._document(document_id)
            return document.model_copy(
                update={"content": strip_mutation_marker(document.content)}
            )

    async def update_document(self, request: UpdateRemoteDocumentRequest) -> RemoteDocument:
        self.write_calls.append("update_document")
        async with self._serialized():
            self._require_login(allow_first_use=True)
            document = self._document(request.document_id).model_copy(
                update={"title": request.title, "content": request.content}
            )
            self._documents[request.document_id] = document
            self._persist_state()
            return _document_summary(document)

    async def delete_document(self, document_id: str, repository_id: str) -> None:
        self.write_calls.append("delete_document")
        async with self._serialized():
            self._require_login(allow_first_use=True)
            self._repository(repository_id)
            self._document(document_id)
            del self._documents[document_id]
            self._persist_state()

    async def close(self) -> None:
            return None

    def _persist_state(self) -> None:
        if not self._state_path:
            return
        import json
        os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
        Path(self._state_path).write_text(
            json.dumps({
                "repositories": [item.model_dump() for item in self._repositories.values()],
                "documents": [item.model_dump() for item in self._documents.values()],
            }),
            encoding="utf-8",
        )

    def seed_repository(self, remote_id: str, name: str) -> RemoteRepository:
        repository = RemoteRepository(
            remote_id=remote_id, name=name, url=f"https://remote.local/{remote_id}"
        )
        self._repositories[remote_id] = repository
        return repository

    @asynccontextmanager
    async def _serialized(self) -> AsyncIterator[None]:
        async with self._lock:
            self._active_contexts += 1
            self.max_concurrent_contexts = max(self.max_concurrent_contexts, self._active_contexts)
            try:
                await asyncio.sleep(0)
                yield
            finally:
                self._active_contexts -= 1

    def _require_login(self, allow_first_use: bool = False) -> None:
        if not self._logged_in and not allow_first_use:
            raise DomainError(
                "REMOTE_LOGIN_REQUIRED",
                "请先登录远程知识库",
                401,
                False,
                "重新登录",
                auth_expired=True,
            )

    def _repository(self, repository_id: str) -> RemoteRepository:
        try:
            return self._repositories[repository_id]
        except KeyError:
            raise DomainError(
                "REMOTE_NOT_FOUND", "远程知识库不存在或已不可用", 404
            ) from None

    def _document(self, document_id: str) -> RemoteDocumentContent:
        try:
            return self._documents[document_id]
        except KeyError:
            raise DomainError(
                "REMOTE_NOT_FOUND", "远程文档不存在或已不可用", 404
            ) from None


def _document_summary(document: RemoteDocumentContent) -> RemoteDocument:
    return RemoteDocument.model_validate(document.model_dump(exclude={"content"}))
