from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse

from app.schemas.common import ErrorBody, ErrorEnvelope


class DomainError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = 400,
        retryable: bool = False,
        action: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.action = action


async def domain_error_handler(_: Request, error: DomainError) -> JSONResponse:
    envelope = ErrorEnvelope(
        error=ErrorBody(
            code=error.code,
            message=error.message,
            retryable=error.retryable,
            action=error.action,
        )
    )
    return JSONResponse(status_code=error.status_code, content=envelope.model_dump(by_alias=True))
