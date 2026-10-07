"""Document formats: the seam between "a file arrived" and "we can read it".

A *format* turns a byte blob into a :class:`ParsedDocument`. The formats the
application ships with are registered in :mod:`app.document.builtin_formats`; a
plugin may register more (TeX, Pages, ...) without the parser learning their
names.

Before this existed, three questions were answered by hardcoded sets in three
different modules: which suffixes may be staged, what media type a suffix maps
to, and how a media type is parsed. Adding one format meant finding and editing
all three, and the copies could disagree — the directory scanner accepted
``.html`` while the single-file allow-list did not, which was true but only
discoverable by reading both. They are now one declaration each.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from app.schemas.imports import DownloadedDocument, ParsedDocument


@dataclass(frozen=True)
class DocumentFormat:
    """One importable document format.

    ``media_type`` is the identity key carried on every ``DownloadedDocument``,
    so it — not the name — is what the parser dispatches on.

    ``single_file`` says whether a user may pick this format's file directly.
    It is not derivable from the media type: HTML can be parsed and can appear
    inside an imported directory, but a lone ``.html`` is not offered as a
    document to pick. Declaring it keeps that distinction in one place instead
    of in a separate allow-list that has to be kept in sync.

    ``binary_payload`` selects which size ceiling applies. The ceilings
    themselves stay in configuration — a format names its *kind*, not a number.
    """

    name: str
    media_type: str
    extensions: tuple[str, ...]
    parse: Callable[[DownloadedDocument], ParsedDocument]
    #: Display name for the file picker and the import dialog ("Word 文档").
    #: Declared with the format rather than mapped in the client, so a plugin's
    #: format is labelled correctly without the renderer learning its name.
    label: str = ""
    #: True for formats the application always ships. A plugin cannot replace
    #: one: the user relies on `.pdf` meaning what it means today.
    builtin: bool = True
    single_file: bool = True
    binary_payload: bool = False
    #: Returns ``None`` when this format can be used on this machine, or a short
    #: reason it cannot. A format that needs something outside the application —
    #: an operating system, an installed editor to convert with — has no other
    #: way to say so, and the client builds its file picker out of what is
    #: offered. Leaving it out would mean offering a button that always fails.
    #:
    #: This is a *capability* declaration, not a probe: it must stay cheap and
    #: side-effect free, because the picker asks for it every time the import
    #: dialog opens.
    availability: Callable[[], str | None] | None = None
    #: Additional HTTP content types that resolve to this format. The media
    #: type itself always resolves; this is for the aliases a server may send
    #: instead (``text/x-markdown`` for markdown, ``application/xhtml+xml`` for
    #: HTML).
    content_types: tuple[str, ...] = ()
    #: Leading bytes that identify a binary payload, used to deny a stream whose
    #: signature contradicts its declared content type. ``None`` for formats
    #: with no magic number.
    magic: bytes | None = None
    #: Cheap pre-parse check on raw bytes, returning a message when the payload
    #: is not this format and ``None`` when it is. Declared here so the staging
    #: store, the directory scanner and the downloader all reject the same
    #: payloads for the same reason instead of each testing for ``%PDF-``.
    validate: Callable[[bytes], str | None] | None = None

    def __post_init__(self) -> None:
        if not self.extensions:
            raise ValueError(f"format {self.name!r} declares no extensions")
        for extension in self.extensions:
            if not extension.startswith(".") or extension != extension.lower():
                raise ValueError(
                    f"format {self.name!r} has a malformed extension {extension!r}; "
                    "extensions are lowercase and leading-dot ('.docx')"
                )

    def unavailable_reason(self) -> str | None:
        """Why this format cannot be used here, or ``None`` when it can."""
        return None if self.availability is None else self.availability()

    def is_pickable(self) -> bool:
        """Whether a user may hand over one of this format's files, here and now.

        Both halves matter and they are not the same question: ``single_file`` is
        about the format (HTML parses but is not offered alone), availability is
        about the machine (a `.pages` on Linux).
        """
        return self.single_file and self.unavailable_reason() is None


class FormatConflictError(ValueError):
    """A registration would shadow a format that is already available.

    Raised rather than ignored on purpose. A plugin that claims ``.pdf`` is
    broken, and the loader reports broken plugins in the settings page — the
    alternative is a format that silently does nothing, which the user cannot
    tell apart from a plugin that was never installed.
    """


@dataclass
class FormatRegistry:
    """Suffix and media-type lookup over every registered format.

    Registration order decides nothing: a conflict is an error, never a
    last-one-wins. That is what stops a plugin from hijacking a suffix the
    user's existing documents depend on.
    """

    _formats: list[DocumentFormat] = field(default_factory=list)
    _by_extension: dict[str, DocumentFormat] = field(default_factory=dict)
    _by_media_type: dict[str, DocumentFormat] = field(default_factory=dict)
    _by_content_type_alias: dict[str, DocumentFormat] = field(default_factory=dict)

    def register(self, format: DocumentFormat) -> None:
        if format.media_type in self._by_media_type:
            owner = self._by_media_type[format.media_type]
            raise FormatConflictError(
                f"媒体类型 {format.media_type} 已由 {owner.name} 注册，{format.name} 不能覆盖它"
            )
        for extension in format.extensions:
            existing = self._by_extension.get(extension)
            if existing is not None:
                raise FormatConflictError(
                    f"扩展名 {extension} 已由 {existing.name} 注册，{format.name} 不能覆盖它"
                )
        for alias in format.content_types:
            existing = self._by_content_type_alias.get(alias)
            if existing is not None:
                raise FormatConflictError(
                    f"内容类型别名 {alias} 已由 {existing.name} 注册，{format.name} 不能覆盖它"
                )
        self._formats.append(format)
        self._by_media_type[format.media_type] = format
        for extension in format.extensions:
            self._by_extension[extension] = format
        for alias in format.content_types:
            self._by_content_type_alias[alias] = format

    def for_extension(self, extension: str) -> DocumentFormat | None:
        return self._by_extension.get(extension.lower())

    def for_pickable_extension(self, extension: str) -> DocumentFormat | None:
        """Like :meth:`for_extension`, but only formats a user may pick alone.

        The staging store resolves one file the user chose by hand, so a format
        that is parsable but not offered as a standalone document (HTML) must
        not be accepted here, and neither must one this machine cannot use. Two
        lookups rather than a flag on the result, so a caller cannot forget to
        check it.
        """
        format = self._by_extension.get(extension.lower())
        return format if format is not None and format.is_pickable() else None

    def for_media_type(self, media_type: str) -> DocumentFormat | None:
        return self._by_media_type.get(media_type)

    def for_content_type(self, content_type: str) -> DocumentFormat | None:
        """Resolve an HTTP ``Content-Type`` header to a format.

        Parameters and charset are dropped first — a server is free to send
        ``text/markdown; charset=utf-8`` and that must not stop the lookup.
        Declared aliases are checked after the canonical media types, so a
        format that registers its own media type always wins over an alias
        another format claims.
        """
        bare = content_type.split(";", 1)[0].strip().lower()
        if not bare:
            return None
        direct = self._by_media_type.get(bare)
        if direct is not None:
            return direct
        return self._by_content_type_alias.get(bare)

    def binary_formats(self) -> tuple[DocumentFormat, ...]:
        """Formats carrying a magic number, longest signature first.

        Ordered so a longer signature is tested before a shorter one that
        prefixes it — otherwise a format whose magic is a prefix of another's
        could never be recognised.
        """
        with_magic = [format for format in self._formats if format.magic]
        return tuple(sorted(with_magic, key=lambda format: len(format.magic or b""), reverse=True))

    def formats(self) -> tuple[DocumentFormat, ...]:
        return tuple(self._formats)

    def pickable_formats(self) -> tuple[DocumentFormat, ...]:
        """Formats a user may hand over as a file, in registry order.

        The single answer to "what does the file picker offer". It used to be
        two: this method's sibling and a filter inside the endpoint that served
        the picker, which is the shape where one of them gets updated.
        """
        return tuple(format for format in self._formats if format.is_pickable())

    def media_types(self) -> tuple[str, ...]:
        return tuple(self._by_media_type)

    def pickable_extensions(self) -> tuple[str, ...]:
        """Suffixes a user may hand over as a single file, in registry order.

        The file picker and the staging allow-list both derive from this, so
        installing a format plugin widens what can be imported without either
        side being edited.
        """
        return tuple(
            extension for extension, format in self._by_extension.items() if format.is_pickable()
        )


def size_limit_for(
    format: DocumentFormat, *, binary_max_bytes: int, text_max_bytes: int
) -> int:
    """The byte ceiling for one format, from the configured ceilings.

    Callers hold the two numbers (they come from settings and tests override
    them in place), so this only maps a format's declared kind onto one of
    them. Kept as a function rather than a field so a format never carries a
    number that settings cannot override.
    """
    return binary_max_bytes if format.binary_payload else text_max_bytes
