"""The format registry: one declaration per format, no second list.

What is locked here:

* a conflict is an error, not a silent overwrite — the user relies on ``.pdf``
  meaning what it means today, so a plugin cannot take it;
* ``single_file`` is enforced on the lookup the staging store uses, because
  HTML is parsable but must not be pickable on its own;
* the built-in set is what the parser, the staging store and the API report,
  so those three can never disagree about which formats exist.
"""
from __future__ import annotations

import pytest

from app.document.builtin_formats import (
    DOCX_MEDIA_TYPE,
    HTML_MEDIA_TYPE,
    MARKDOWN_MEDIA_TYPE,
    PDF_MEDIA_TYPE,
    builtin_registry,
)
from app.document.formats import (
    DocumentFormat,
    FormatConflictError,
    FormatRegistry,
    size_limit_for,
)


def _format(
    name: str,
    *,
    media_type: str,
    extensions: tuple[str, ...],
    **kwargs: object,
) -> DocumentFormat:
    return DocumentFormat(
        name=name,
        media_type=media_type,
        extensions=extensions,
        parse=lambda document: document,  # type: ignore[arg-type,return-value]
        **kwargs,  # type: ignore[arg-type]
    )


def test_builtin_registry_covers_every_shipped_format() -> None:
    registry = builtin_registry()

    assert [format.name for format in registry.formats()] == ["pdf", "markdown", "docx", "html"]
    assert registry.for_extension(".pdf").media_type == PDF_MEDIA_TYPE  # type: ignore[union-attr]
    assert registry.for_extension(".DOCX").media_type == DOCX_MEDIA_TYPE  # type: ignore[union-attr]
    assert registry.for_media_type(MARKDOWN_MEDIA_TYPE) is not None
    assert registry.for_media_type(HTML_MEDIA_TYPE) is not None


def test_html_is_parsable_but_not_pickable_as_a_single_file() -> None:
    registry = builtin_registry()

    # It parses and it can appear inside an imported directory...
    assert registry.for_media_type(HTML_MEDIA_TYPE) is not None
    assert registry.for_extension(".html") is not None
    # ...but a lone .html is not offered to the user as a document.
    assert registry.for_pickable_extension(".html") is None
    assert ".html" not in registry.pickable_extensions()
    assert ".htm" not in registry.pickable_extensions()


def test_pickable_extensions_are_the_single_file_formats() -> None:
    registry = builtin_registry()

    assert registry.pickable_extensions() == (".pdf", ".md", ".markdown", ".docx")


def test_registering_a_taken_extension_raises_instead_of_overwriting() -> None:
    registry = builtin_registry()

    with pytest.raises(FormatConflictError) as error:
        registry.register(_format("evil", media_type="text/x-evil", extensions=(".pdf",)))

    assert ".pdf" in str(error.value)
    # And the original still resolves — the failure did not half-apply.
    assert registry.for_extension(".pdf").name == "pdf"  # type: ignore[union-attr]


def test_registering_a_taken_media_type_raises() -> None:
    registry = builtin_registry()

    with pytest.raises(FormatConflictError):
        registry.register(_format("clone", media_type=PDF_MEDIA_TYPE, extensions=(".clone",)))

    assert registry.for_extension(".clone") is None


def test_registering_a_taken_alias_raises() -> None:
    registry = builtin_registry()

    with pytest.raises(FormatConflictError):
        registry.register(
            _format(
                "alias-thief",
                media_type="text/x-alias",
                extensions=(".alias",),
                content_types=("text/x-markdown",),
            )
        )


def test_a_new_format_is_immediately_visible_to_every_lookup() -> None:
    registry = builtin_registry()

    registry.register(
        _format(
            "tex",
            media_type="text/x-tex",
            extensions=(".tex", ".latex"),
            content_types=("application/x-tex",),
            binary_payload=False,
        )
    )

    assert registry.for_extension(".tex").name == "tex"  # type: ignore[union-attr]
    assert registry.for_extension(".LATEX").name == "tex"  # type: ignore[union-attr]
    assert registry.for_content_type("application/x-tex").name == "tex"  # type: ignore[union-attr]
    # A plugin format widens the picker without any client-side edit.
    assert ".tex" in registry.pickable_extensions()
    assert registry.pickable_extensions()[-1] == ".latex"


def test_content_type_lookup_strips_parameters_and_matches_aliases() -> None:
    registry = builtin_registry()

    assert registry.for_content_type("text/markdown; charset=utf-8").name == "markdown"  # type: ignore[union-attr]
    assert registry.for_content_type("TEXT/MARKDOWN").name == "markdown"  # type: ignore[union-attr]
    assert registry.for_content_type("text/x-markdown").name == "markdown"  # type: ignore[union-attr]
    assert registry.for_content_type("text/plain").name == "markdown"  # type: ignore[union-attr]
    assert registry.for_content_type("application/xhtml+xml").name == "html"  # type: ignore[union-attr]
    assert registry.for_content_type("") is None
    assert registry.for_content_type("application/x-nope") is None


def test_binary_formats_are_ordered_longest_signature_first() -> None:
    registry = builtin_registry()

    magics = [format.magic for format in registry.binary_formats()]

    # Ordered so a shorter signature that prefixes a longer one is never
    # matched first — otherwise the longer format could never be recognised.
    assert all(magic for magic in magics)
    assert magics == sorted(magics, key=len, reverse=True)
    assert {format.name for format in registry.binary_formats()} == {"pdf", "docx"}


def test_malformed_extensions_are_rejected_at_construction() -> None:
    # Dirty input on purpose: no dot, uppercase, and empty are all wrong, and
    # each would silently fail to match a real filename.
    for extension in ("pdf", ".PDF", "docx"):
        with pytest.raises(ValueError):
            _format("bad", media_type="text/x-bad", extensions=(extension,))

    with pytest.raises(ValueError):
        _format("empty", media_type="text/x-empty", extensions=())


def test_size_limit_follows_the_declared_kind_not_the_media_type() -> None:
    registry = builtin_registry()
    limits = {"binary_max_bytes": 100, "text_max_bytes": 20}

    assert size_limit_for(registry.for_media_type(PDF_MEDIA_TYPE), **limits) == 100  # type: ignore[arg-type]
    assert size_limit_for(registry.for_media_type(DOCX_MEDIA_TYPE), **limits) == 100  # type: ignore[arg-type]
    assert size_limit_for(registry.for_media_type(MARKDOWN_MEDIA_TYPE), **limits) == 20  # type: ignore[arg-type]
    assert size_limit_for(registry.for_media_type(HTML_MEDIA_TYPE), **limits) == 20  # type: ignore[arg-type]


def test_a_fresh_registry_is_not_shared_between_callers() -> None:
    first = builtin_registry()
    first.register(_format("tex", media_type="text/x-tex", extensions=(".tex",)))

    # A standalone parser must not inherit formats registered elsewhere.
    assert builtin_registry().for_extension(".tex") is None


def test_empty_registry_answers_every_lookup_with_none() -> None:
    registry = FormatRegistry()

    assert registry.for_extension(".pdf") is None
    assert registry.for_media_type(PDF_MEDIA_TYPE) is None
    assert registry.for_content_type("application/pdf") is None
    assert registry.pickable_extensions() == ()
    assert registry.binary_formats() == ()
