from __future__ import annotations

from fastapi import Request
from fastapi.exceptions import RequestValidationError
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
        *,
        auth_expired: bool = False,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.retryable = retryable
        self.action = action
        # True 表示「远程凭据已失效、需要用户重新登录」，供通用错误映射层
        # 区分可透传的错误与需要归一化的错误。平台专属的调用方（各 provider）
        # 在抛出点自行声明，通用层因此不需要认识任何平台错误码。
        self.auth_expired = auth_expired


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


async def request_validation_handler(_: Request, __: RequestValidationError) -> JSONResponse:
    error = DomainError("INVALID_REQUEST", "请求参数无效", 422)
    return await domain_error_handler(_, error)
