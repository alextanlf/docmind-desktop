from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from app.api.errors import DomainError
from app.schemas.ollama import validate_model_tag


class Resolver(Protocol):
    async def resolve(self, host: str) -> Sequence[str]: ...


async def normalize_loopback_base_url(value: str, resolver: Resolver) -> str:
    if not isinstance(value, str) or value != value.strip() or any(char.isspace() for char in value):
        raise ValueError("invalid Ollama URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid Ollama URL") from error
    if (
        parsed.scheme.lower() != "http"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/")
        or port not in (None, 11434)
    ):
        raise ValueError("invalid Ollama URL")
    host = parsed.hostname.lower()
    try:
        answers = await resolver.resolve(host)
    except Exception as error:
        raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True) from error
    if not answers or any(not ipaddress.ip_address(answer).is_loopback for answer in answers):
        raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True)
    rendered_host = f"[{host}]" if ":" in host else host
    return urlunsplit(("http", f"{rendered_host}:11434", "", "", ""))
