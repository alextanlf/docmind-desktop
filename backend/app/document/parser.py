"""Dispatching a downloaded payload to the format that can read it.

The parser owns no knowledge of any particular format. It looks the payload's
media type up in a :class:`~app.document.formats.FormatRegistry` and delegates.
Adding a format — including one installed by a plugin — needs no change here,
which is the whole point: the previous version was an ``if media_type == ...``
chain that every new format had to be threaded through.
"""
from __future__ import annotations

from app.api.errors import DomainError
from app.document.builtin_formats import builtin_registry
from app.document.formats import FormatRegistry
from app.schemas.imports import DownloadedDocument, ParsedDocument


class DocumentParser:
    """Dispatches on media type via a format registry.

    The registry is resolved through a property rather than a plain attribute
    so that a subclass replacing ``__init__`` still parses. That is not
    hypothetical: the test suite has such a subclass, and a caller wrapping the
    parser for instrumentation is the obvious next one — both would otherwise
    die on an attribute the base class never got to set.
    """

    _formats: FormatRegistry | None = None

    def __init__(self, formats: FormatRegistry | None = None) -> None:
        self._formats = formats

    @property
    def formats(self) -> FormatRegistry:
        """Built-in formats until something supplies a wider registry.

        Cached on the instance, so the fallback is built once and one parser
        can never leak formats into another.
        """
        if self._formats is None:
            self._formats = builtin_registry()
        return self._formats

    @formats.setter
    def formats(self, value: FormatRegistry) -> None:
        self._formats = value

    def parse(self, document: DownloadedDocument) -> ParsedDocument:
        format = self.formats.for_media_type(document.media_type)
        if format is None:
            raise DomainError("SOURCE_UNSUPPORTED", "不支持的文档类型", 400, False)
        # The format's own payload check runs here too, not only at staging
        # time: this parser is also reached from the remote gateways and the
        # directory scanner, which hand it bytes that were never staged.
        if format.validate is not None:
            problem = format.validate(document.raw_bytes)
            if problem is not None:
                raise DomainError("SOURCE_UNSUPPORTED", problem, 400, False)
        return format.parse(document)
