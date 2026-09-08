from __future__ import annotations

from app.schemas.common import WireModel


class LoginStatus(WireModel):
    logged_in: bool
    account_label: str | None = None
    requires_login: bool


class LoginResult(LoginStatus):
    pass


class BrowserInstallResult(WireModel):
    installed: bool
    message: str


class YuqueRepository(WireModel):
    yuque_id: str
    name: str
    url: str | None = None


class YuqueDocument(WireModel):
    yuque_id: str
    repository_id: str
    title: str
    url: str | None = None


class YuqueDocumentContent(YuqueDocument):
    content: str


class CreateRepositoryRequest(WireModel):
    name: str


class CreateYuqueDocumentRequest(WireModel):
    repository_id: str
    title: str
    content: str


class UpdateYuqueDocumentRequest(WireModel):
    document_id: str
    title: str
    content: str
