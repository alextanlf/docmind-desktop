from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from app.api.errors import DomainError
from app.schemas.ollama import validate_model_tag as _validate_model_tag


class Resolver(Protocol):
    async def resolve(self, host: str) -> Sequence[str]: ...


def validate_model_tag(value: str) -> str:
    """Preserve the Phase 2 validation import path for callers and tests."""
    return _validate_model_tag(value)


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

    # Only these two literal addresses are accepted.  In particular, do not
    # broaden the allow-list to every address for which ``is_loopback`` is
    # true (127.0.0.2, IPv4-mapped IPv6, etc.); the localhost name is the
    # separate case where DNS answers are checked below.
    literal_allowed = host in {"127.0.0.1", "::1"}
    if host == "localhost":
        try:
            answers = await resolver.resolve(host)
        except Exception as error:
            raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True) from error
        try:
            parsed_answers = [ipaddress.ip_address(str(answer)) for answer in answers]
        except ValueError as error:
            raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True) from error
        if not parsed_answers or any(not answer.is_loopback for answer in parsed_answers):
            raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True)
    elif not literal_allowed:
        raise ValueError("invalid Ollama URL")
    else:
        # A literal loopback address cannot be rebound through DNS.  Still
        # consult an injected resolver when it has an answer so tests and
        # production can perform the same preflight check; an empty answer is
        # allowed for the literal case.
        try:
            answers = await resolver.resolve(host)
        except OSError:
            answers = []
        if answers:
            try:
                parsed_answers = [ipaddress.ip_address(str(answer)) for answer in answers]
            except ValueError as error:
                raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True) from error
            if any(not answer.is_loopback for answer in parsed_answers):
                raise DomainError("OLLAMA_UNAVAILABLE", "Ollama 未运行或暂时无法连接", 503, True)
    rendered_host = f"[{host}]" if ":" in host else host
    return urlunsplit(("http", f"{rendered_host}:11434", "", "", ""))
