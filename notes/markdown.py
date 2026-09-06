"""Canonical ``body_json`` -> Markdown conversion.

This module owns rendering a note's canonical, validated ``body_json``
document as a human-readable, portable Markdown copy. It is
deliberately independent of `notes/library_backup.py` -- it has no
knowledge of archives, manifests, or filenames -- so it can be reused
directly by a future individual-note Markdown download and a future
"Switch to Markdown" view, not only by the library-backup feature.

The native ``body_json`` remains authoritative; Markdown produced here
is a best-effort, human-readable convenience copy only, never intended
as a lossless or re-importable representation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError

from notes import documents

MARKDOWN_LIST_INDENT = documents.EXPORT_LIST_INDENT

_INLINE_ESCAPE_CHARS = ("`", "*", "_", "[", "]", "<", ">")

_ORDERED_PREFIX_RE = re.compile(r"^(\d+)([.)])(\s|$)")


class MarkdownConversionError(Exception):
    """Raised when `note_body_to_markdown()`/`note_to_markdown()` is
    given a document that fails canonical validation. Wraps the
    underlying `django.core.exceptions.ValidationError` message, which
    never includes note content -- only node/key/type names -- so it
    remains safe to surface directly."""


def note_body_to_markdown(body_json: dict[str, Any]) -> str:
    """Renders a note's canonical, validated ``body_json`` as Markdown.
    Requires canonical-valid input -- reuses
    `notes.documents.validate_canonical_document()` and raises
    `MarkdownConversionError` (never attempting broad best-effort
    recovery) if validation fails. Never mutates the supplied
    document. Deterministic: identical input always produces identical
    output."""
    try:
        document = documents.validate_canonical_document(body_json)
    except DjangoValidationError as exc:
        raise MarkdownConversionError(str(exc)) from exc
    return _render_document(document)


def note_to_markdown(title: str, body_json: dict[str, Any]) -> str:
    """Renders a complete, portable note file: the title as a leading
    H1, then one blank line, then the rendered body. `note_body_to_markdown()`
    remains title-independent so it can be reused where a surrounding
    UI already displays the title separately. Contains no filename or
    path logic -- that is `library_backup.build_backup_markdown_paths()`'s
    concern."""
    body = note_body_to_markdown(body_json)
    heading = f"# {_escape_inline_text(title)}"
    if body:
        return f"{heading}\n\n{body}"
    return f"{heading}\n"


# ---------------------------------------------------------------------------
# Block-level rendering
# ---------------------------------------------------------------------------


def _render_document(document: dict[str, Any]) -> str:
    blocks = [_render_block(child) for child in document.get("content", []) or []]
    text = "\n\n".join(blocks)
    if not text:
        return ""
    return text + "\n"


def _render_block(node: dict[str, Any]) -> str:
    node_type = node.get("type")
    if node_type == "paragraph":
        return _render_inline(node.get("content", []) or [], at_line_start=True)
    if node_type == "heading":
        level = node["attrs"]["level"]
        heading_text = _render_inline(node.get("content", []) or [], at_line_start=False)
        return f"{'#' * level} {heading_text}"
    if node_type in {"bulletList", "orderedList"}:
        return "\n".join(_render_list_lines(node, depth=0))
    return _render_unknown_node(node)


def _render_list_lines(list_node: dict[str, Any], *, depth: int) -> list[str]:
    # Mirrors notes/documents.py's own `_export_list_lines()`: every
    # list, top-level or nested, numbers/marks and indents
    # independently -- a nested list restarts at "1." and indents one
    # level deeper than its parent.
    ordered = list_node.get("type") == "orderedList"
    indent = MARKDOWN_LIST_INDENT * depth
    lines: list[str] = []
    for index, item in enumerate(list_node.get("content", []) or [], start=1):
        marker = f"{index}. " if ordered else "- "
        lines.extend(_render_list_item_lines(item, indent=indent, marker=marker, depth=depth))
    return lines


def _render_list_item_lines(
    item: dict[str, Any], *, indent: str, marker: str, depth: int
) -> list[str]:
    lines: list[str] = []
    continuation_indent = indent + " " * len(marker)
    wrote_marker = False
    for child in item.get("content", []) or []:
        child_type = child.get("type")
        if child_type in {"bulletList", "orderedList"}:
            if not wrote_marker:
                lines.append((indent + marker).rstrip())
                wrote_marker = True
            lines.extend(_render_list_lines(child, depth=depth + 1))
            continue

        text = _render_inline(child.get("content", []) or [], at_line_start=False)
        text_lines = text.split("\n") if text else [""]
        for text_line in text_lines:
            if not wrote_marker:
                line = f"{indent}{marker}{text_line}"
                wrote_marker = True
            else:
                line = f"{continuation_indent}{text_line}"
            lines.append(line if text_line else line.rstrip())
    if not wrote_marker:
        lines.append((indent + marker).rstrip())
    return lines


def _render_unknown_node(node: dict[str, Any]) -> str:
    # Defensive, forward-compatible rendering only: `note_body_to_markdown()`
    # always validates first, so this path is not reachable through the
    # public API today. It exists so a future schema addition that
    # reaches this renderer before the renderer itself is updated fails
    # softly (visible marker, preserved descendant text) rather than
    # losing the whole note.
    node_type = node.get("type", "unknown")
    descendant_text = _render_descendant_text(node)
    marker = f"[Unsupported RidgeNote content: {node_type}]"
    if descendant_text:
        return f"{marker} {descendant_text}"
    return marker


def _render_descendant_text(node: dict[str, Any]) -> str:
    if "text" in node:
        return _apply_marks(
            _escape_inline_text(node.get("text", "")),
            node.get("marks") or [],
            raw_text=node.get("text", ""),
        )
    content = node.get("content") or []
    parts = [_render_descendant_text(child) for child in content]
    return " ".join(part for part in parts if part)


# ---------------------------------------------------------------------------
# Inline rendering
# ---------------------------------------------------------------------------


def _render_inline(children: Iterable[dict[str, Any]], *, at_line_start: bool) -> str:
    chunks: list[str] = []
    line_start = at_line_start
    for child in children:
        node_type = child.get("type")
        if node_type == "hardBreak":
            chunks.append("  \n")
            line_start = True
            continue
        if node_type == "text":
            chunks.append(_render_text_node(child, at_line_start=line_start))
        else:
            chunks.append(_render_unknown_node(child))
        line_start = False
    return "".join(chunks)


def _render_text_node(node: dict[str, Any], *, at_line_start: bool) -> str:
    raw_text = node.get("text", "")
    marks = node.get("marks") or []
    escaped = _escape_inline_text(raw_text)
    if at_line_start and not marks:
        escaped = _escape_line_start(escaped)
    return _apply_marks(escaped, marks, raw_text=raw_text)


# Canonical, deterministic nesting order applied regardless of stored
# mark order: link (outermost) -> bold -> italic -> underline
# (innermost, nearest the text).
def _apply_marks(text: str, marks: list[dict[str, Any]], *, raw_text: str) -> str:
    present = {mark.get("type") for mark in marks if isinstance(mark, dict)}
    rendered = text
    if "underline" in present:
        rendered = f"<u>{rendered}</u>"
    if "italic" in present:
        rendered = f"_{rendered}_"
    if "bold" in present:
        rendered = f"**{rendered}**"
    if "link" in present:
        href = _link_href(marks)
        if href is None:
            return rendered
        other_marks_present = bool(present & {"bold", "italic", "underline"})
        if not other_marks_present and raw_text == href:
            return f"<{href}>"
        return f"[{rendered}]({href})"
    return rendered


def _link_href(marks: Iterable[dict[str, Any]]) -> str | None:
    for mark in marks:
        if isinstance(mark, dict) and mark.get("type") == "link":
            attrs = mark.get("attrs")
            if isinstance(attrs, dict):
                href = attrs.get("href")
                if isinstance(href, str):
                    return href
    return None


def _escape_inline_text(text: str) -> str:
    escaped = text.replace("\\", "\\\\")
    for char in _INLINE_ESCAPE_CHARS:
        escaped = escaped.replace(char, f"\\{char}")
    return escaped


def _escape_line_start(text: str) -> str:
    # Only reached for an unmarked text node at the true start of a
    # rendered paragraph/heading line (marks already add a prefix --
    # **, _, <u>, [ -- that prevents any of these structural
    # misreadings on their own). `*`, `_`, backtick, and brackets are
    # already escaped everywhere by `_escape_inline_text()`, so only
    # the characters *not* in that always-escaped set need handling
    # here: `#`, `-`, `+`, and a leading `<digits>.`/`<digits>)`
    # ordered-list-looking prefix.
    if not text:
        return text
    first = text[0]
    if first == "#":
        return "\\" + text
    if first == "-" and (len(text) == 1 or text[1] in (" ", "-")):
        return "\\" + text
    if first == "+" and (len(text) == 1 or text[1] == " "):
        return "\\" + text
    match = _ORDERED_PREFIX_RE.match(text)
    if match:
        digits, punct = match.group(1), match.group(2)
        return f"{digits}\\{punct}{text[match.end(2) :]}"
    return text
