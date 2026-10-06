"""Loopback enforcement for local inference servers.

Local model servers must stay on the loopback interface. That is a security
boundary, not a vendor constraint: Ollama on 11434, LM Studio on 1234 and
llama.cpp on 8080 are all equally acceptable, while anything reachable from the
network is not. The port is therefore unconstrained.

The DNS-rebinding defence from the previous Ollama-only implementation is kept
verbatim in spirit: a hostname is only trusted when *every* address it resolves
to is a loopback address, so a name that points outward is rejected before any
HTTP request is made.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Sequence
from typing import Protocol
from urllib.parse import urlsplit, urlunsplit

from app.api.errors import DomainError
from app.schemas.local_model import validate_model_tag as _validate_model_tag

UNAVAILABLE_CODE = "LOCAL_MODEL_UNAVAILABLE"
UNAVAILABLE_MESSAGE = "本地模型服务未运行或暂时无法连接"

# Only these literals are trusted outright. Deliberately narrower than
# "everything `is_loopback` accepts": 127.0.0.2 and IPv4-mapped IPv6 are loopback
# but unusual enough that naming them explicitly is cheaper than reasoning about
# which of them a user could have meant.
_TRUSTED_LITERALS = frozenset({"127.0.0.1", "::1"})


class Resolver(Protocol):
    async def resolve(self, host: str) -> Sequence[str]: ...


def validate_model_tag(value: str) -> str:
    return _validate_model_tag(value)


def _unavailable(cause: BaseException | None = None) -> DomainError:
    error = DomainError(UNAVAILABLE_CODE, UNAVAILABLE_MESSAGE, 503, True)
    if cause is not None:
        error.__cause__ = cause
    return error


async def _assert_all_answers_are_loopback(host: str, resolver: Resolver) -> None:
    try:
        answers = await resolver.resolve(host)
    except Exception as error:
        raise _unavailable(error) from error
    try:
        parsed_answers = [ipaddress.ip_address(str(answer)) for answer in answers]
    except ValueError as error:
        raise _unavailable(error) from error
    if not parsed_answers or any(not answer.is_loopback for answer in parsed_answers):
        raise _unavailable()


async def normalize_loopback_base_url(value: str, resolver: Resolver) -> str:
    """Validate a local server URL and return it in canonical form.

    The returned URL keeps the caller's port — that is the whole point of
    supporting more than one server. Only the scheme and trailing slash are
    normalized.
    """
    if (
        not isinstance(value, str)
        or value != value.strip()
        or any(char.isspace() for char in value)
    ):
        raise ValueError("invalid local model URL")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError as error:
        raise ValueError("invalid local model URL") from error
    if (
        parsed.scheme.lower() != "http"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path.rstrip("/")
    ):
        raise ValueError("invalid local model URL")
    host = parsed.hostname.lower()

    if host == "localhost":
        # A name can be rebound between validation and use, so every answer it
        # currently produces has to be loopback.
        await _assert_all_answers_are_loopback(host, resolver)
    elif host in _TRUSTED_LITERALS:
        # A literal cannot be rebound through DNS, but consult the resolver
        # anyway so tests and production run the same preflight. An empty
        # answer is fine for the literal case.
        try:
            answers = await resolver.resolve(host)
        except OSError:
            answers = []
        if answers:
            await _assert_all_answers_are_loopback(host, resolver)
    else:
        raise ValueError("invalid local model URL")

    if port is not None and not (0 < port < 65536):
        raise ValueError("invalid local model URL")
    rendered_host = f"[{host}]" if ":" in host else host
    netloc = rendered_host if port is None else f"{rendered_host}:{port}"
    return urlunsplit(("http", netloc, "", "", ""))
