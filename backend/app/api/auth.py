from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import Depends, Header

from app.api.errors import DomainError
from app.config import AppSettings, get_settings


async def require_runtime_token(
    x_docmind_token: Annotated[str | None, Header()] = None,
    settings: AppSettings = Depends(get_settings),  # noqa: B008
) -> None:
    if not x_docmind_token or not secrets.compare_digest(
        x_docmind_token, settings.session_token.get_secret_value()
    ):
        raise DomainError("AUTH_REQUIRED", "缺少或无效的本地会话令牌", 401)
