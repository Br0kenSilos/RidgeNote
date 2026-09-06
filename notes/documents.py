from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any
from urllib.parse import urlsplit

from django.core.exceptions import ValidationError
from django.utils import timezone
from django.utils.html import format_html, format_html_join
from django.utils.safestring import mark_safe

DOCUMENT_IDENTIFIER = "ridgenote-note"
EDITOR_SCHEMA_VERSION = 1
EXPORT_FORMAT_IDENTIFIER = "ridgenote-note"
EXPORT_FORMAT_VERSION = 1
EMPTY_DOCUMENT = {
    "type": "doc",
    "content": [
        {
            "type": "paragraph",
        }
    ],
}

ALLOWED_LINK_SCHEMES = {"http", "https", "mailto"}
ALLOWED_NODE_TYPES = {
    "doc",
    "paragraph",
    "heading",
    "bulletList",
    "orderedList",
    "listItem",
    "text",
    "hardBreak",
}
ALLOWED_MARK_TYPES = {"bold", "italic", "underline", "link"}
INLINE_CONTAINER_TYPES = {"paragraph", "heading"}
ALLOWED_INLINE_NODE_TYPES = {"text", "hardBreak"}


def canonical_empty_document() -> dict[str, Any]:
    return deepcopy(EMPTY_DOCUMENT)


def generated_title_for_timestamp(value) -> str:
    localized = timezone.localtime(value)
    return localized.strftime("Untitled - %Y-%m-%d %H-%M")


def is_supported_schema_version(version: int) -> bool:
    return version == EDITOR_SCHEMA_VERSION


def validate_link_href(raw_href: Any) -> str:
    if not isinstance(raw_href, str):
        raise ValidationError("Links must use a string URL.")

    href = raw_href.strip()
    if not href:
        raise ValidationError("Links cannot be blank.")
    if href.startswith("//"):
        raise ValidationError("Protocol-relative links are not allowed.")

    try:
        parsed = urlsplit(href)
    except ValueError:
        raise ValidationError("Links must use a valid URL.") from None
    scheme = parsed.scheme.lower()
    if scheme not in ALLOWED_LINK_SCHEMES:
        raise ValidationError("Links must use http, https, or mailto.")
    if scheme in {"http", "https"} and not parsed.netloc:
        raise ValidationError("HTTP and HTTPS links must include a host.")
    if scheme == "mailto" and not parsed.path:
        raise ValidationError("Mailto links must include an address.")
    return href


def validate_canonical_document(document: Any) -> dict[str, Any]:
    return _validate_node(document, parent_type=None)


def _validate_node(node: Any, *, parent_type: str | None) -> dict[str, Any]:
    if not isinstance(node, dict):
        raise ValidationError("Document nodes must be objects.")

    node_type = node.get("type")
    if node_type not in ALLOWED_NODE_TYPES:
        raise ValidationError(f"Unsupported node type: {node_type!r}.")

    allowed_keys = {"type", "content", "text", "marks", "attrs"}
    extra_keys = set(node) - allowed_keys
    if extra_keys:
        raise ValidationError(f"Unsupported node keys: {sorted(extra_keys)!r}.")

    if parent_type == "doc" and node_type not in {
        "paragraph",
        "heading",
        "bulletList",
        "orderedList",
    }:
        raise ValidationError("Top-level content must be paragraphs, headings, or lists.")
    if parent_type in INLINE_CONTAINER_TYPES and node_type not in ALLOWED_INLINE_NODE_TYPES:
        raise ValidationError("Paragraphs and headings may contain only text and line-break nodes.")
    if parent_type in {"bulletList", "orderedList"} and node_type != "listItem":
        raise ValidationError("Lists may contain only list items.")
    if parent_type == "listItem" and node_type not in {
        "paragraph",
        "bulletList",
        "orderedList",
    }:
        raise ValidationError("List items may contain paragraphs or nested lists only.")

    cleaned: dict[str, Any] = {"type": node_type}
    attrs = node.get("attrs")
    content = node.get("content")
    text = node.get("text")
    marks = node.get("marks")

    if node_type == "doc":
        cleaned["content"] = _validate_content(content, parent_type=node_type, allow_empty=False)
        return cleaned

    if node_type == "paragraph":
        if content is not None:
            cleaned["content"] = _validate_content(content, parent_type=node_type, allow_empty=True)
        return cleaned

    if node_type == "heading":
        if not isinstance(attrs, dict):
            raise ValidationError("Headings must define attrs.level.")
        level = attrs.get("level")
        if level not in {1, 2, 3}:
            raise ValidationError("Heading level must be 1, 2, or 3.")
        cleaned["attrs"] = {"level": level}
        cleaned["content"] = _validate_content(content, parent_type=node_type, allow_empty=False)
        return cleaned

    if node_type in {"bulletList", "orderedList"}:
        cleaned["content"] = _validate_content(content, parent_type=node_type, allow_empty=False)
        return cleaned

    if node_type == "listItem":
        cleaned["content"] = _validate_content(content, parent_type=node_type, allow_empty=False)
        return cleaned

    if node_type == "hardBreak":
        if any(value is not None for value in (attrs, content, text, marks)):
            raise ValidationError(
                "Hard breaks do not support attrs, text, child content, or marks."
            )
        return cleaned

    if not isinstance(text, str):
        raise ValidationError("Text nodes must include a string value.")
    cleaned["text"] = text
    if attrs is not None:
        raise ValidationError("Text nodes do not support attrs.")
    if content is not None:
        raise ValidationError("Text nodes do not support child content.")
    if marks is not None:
        cleaned["marks"] = _validate_marks(marks)
    return cleaned


def _validate_content(content: Any, *, parent_type: str, allow_empty: bool) -> list[dict[str, Any]]:
    if not isinstance(content, list):
        raise ValidationError("Document content must be a list.")
    if not content and not allow_empty:
        raise ValidationError("This document structure requires at least one child node.")
    return [_validate_node(child, parent_type=parent_type) for child in content]


def _validate_marks(marks: Any) -> list[dict[str, Any]]:
    if not isinstance(marks, list):
        raise ValidationError("Text marks must be a list.")

    cleaned_marks: list[dict[str, Any]] = []
    for mark in marks:
        if not isinstance(mark, dict):
            raise ValidationError("Marks must be objects.")
        mark_type = mark.get("type")
        if mark_type not in ALLOWED_MARK_TYPES:
            raise ValidationError(f"Unsupported mark type: {mark_type!r}.")
        extra_keys = set(mark) - {"type", "attrs"}
        if extra_keys:
            raise ValidationError(f"Unsupported mark keys: {sorted(extra_keys)!r}.")

        cleaned_mark: dict[str, Any] = {"type": mark_type}
        attrs = mark.get("attrs")
        if mark_type == "link":
            if not isinstance(attrs, dict):
                raise ValidationError("Links must define attrs.href.")
            cleaned_mark["attrs"] = {"href": validate_link_href(attrs.get("href"))}
        elif attrs is not None:
            raise ValidationError(f"{mark_type!r} does not support attrs.")
        cleaned_marks.append(cleaned_mark)

    return cleaned_marks


def derive_plain_text(document: dict[str, Any]) -> str:
    blocks = [block for block in _plain_text_blocks(document) if block]
    return "\n\n".join(blocks)


def _plain_text_blocks(node: dict[str, Any]) -> list[str]:
    node_type = node["type"]
    if node_type == "doc":
        blocks: list[str] = []
        for child in node["content"]:
            blocks.extend(_plain_text_blocks(child))
        return blocks

    if node_type in {"paragraph", "heading"}:
        return [_plain_text_inline(node.get("content", []))]

    if node_type in {"bulletList", "orderedList"}:
        blocks: list[str] = []
        for child in node["content"]:
            blocks.extend(_plain_text_blocks(child))
        return blocks

    if node_type == "listItem":
        lines = []
        for child in node["content"]:
            lines.extend(_plain_text_blocks(child))
        return ["\n".join(line for line in lines if line)]

    return [node.get("text", "")]


def _plain_text_inline(children: Iterable[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for child in children:
        if child["type"] == "hardBreak":
            chunks.append("\n")
        else:
            chunks.append(child.get("text", ""))
    return "".join(chunks)


EXPORT_LIST_INDENT = "  "


def derive_export_plain_text(document: dict[str, Any]) -> str:
    """Render ``document`` (a note's current, validated ``body_json``) as
    plain text for on-demand text-file download -- preserving ordered/
    unordered list markers, per-level nested-list indentation, and link
    URLs, none of which `derive_plain_text()` above (still used to
    populate the stored, search-indexed `body_plain_text` field) tries
    to preserve. Deliberately a separate function, not a change to
    `derive_plain_text()`'s output: `body_plain_text` feeds full-text
    search (see `services.search_notes_global()`) and existing notes'
    stored copies are not backfilled, so changing what that function
    produces would silently desynchronize search from what's actually
    stored without a migration to fix it. This function instead always
    recomputes straight from `body_json` at download time, so every
    note -- including ones saved before this existed -- benefits
    immediately with no edit or migration required."""
    blocks = [block for block in _export_plain_text_blocks(document) if block]
    return "\n\n".join(blocks)


def _export_plain_text_blocks(node: dict[str, Any]) -> list[str]:
    node_type = node["type"]
    if node_type == "doc":
        blocks: list[str] = []
        for child in node.get("content", []):
            blocks.extend(_export_plain_text_blocks(child))
        return blocks

    if node_type in {"paragraph", "heading"}:
        return [_export_plain_text_inline(node.get("content", []))]

    if node_type in {"bulletList", "orderedList"}:
        return ["\n".join(_export_list_lines(node, depth=0))]

    return [node.get("text", "")]


def _export_plain_text_inline(children: Iterable[dict[str, Any]]) -> str:
    chunks: list[str] = []
    for child in children:
        if child["type"] == "hardBreak":
            chunks.append("\n")
            continue
        text = child.get("text", "")
        href = _export_link_href(child.get("marks") or [])
        if href and text != href:
            chunks.append(f"{text} ({href})")
        else:
            chunks.append(text)
    return "".join(chunks)


def _export_link_href(marks: Iterable[dict[str, Any]]) -> str | None:
    for mark in marks:
        if mark.get("type") == "link":
            return mark["attrs"]["href"]
    return None


def _export_list_lines(list_node: dict[str, Any], *, depth: int) -> list[str]:
    # Each list -- top-level or nested -- numbers/marks and indents
    # independently: a nested list restarts at "1." and indents one
    # level deeper than its parent, and two separate top-level lists
    # each restart at "1." too, since neither carries state from the
    # other.
    ordered = list_node["type"] == "orderedList"
    indent = EXPORT_LIST_INDENT * depth
    lines: list[str] = []
    for index, item in enumerate(list_node.get("content", []), start=1):
        marker = f"{index}. " if ordered else "- "
        lines.extend(_export_list_item_lines(item, indent=indent, marker=marker, depth=depth))
    return lines


def _export_list_item_lines(
    item: dict[str, Any], *, indent: str, marker: str, depth: int
) -> list[str]:
    lines: list[str] = []
    continuation_indent = indent + " " * len(marker)
    wrote_marker = False
    for child in item.get("content", []):
        if child["type"] in {"bulletList", "orderedList"}:
            if not wrote_marker:
                lines.append((indent + marker).rstrip())
                wrote_marker = True
            lines.extend(_export_list_lines(child, depth=depth + 1))
            continue

        text = _export_plain_text_inline(child.get("content", []))
        for text_line in text.split("\n") if text else [""]:
            if not wrote_marker:
                line = f"{indent}{marker}{text_line}"
                wrote_marker = True
            else:
                line = f"{continuation_indent}{text_line}"
            lines.append(line.rstrip() if not text_line else line)
    if not wrote_marker:
        lines.append((indent + marker).rstrip())
    return lines


def render_document_html(document: dict[str, Any]):
    return _render_node(document)


def _render_node(node: dict[str, Any]):
    node_type = node["type"]
    if node_type == "doc":
        return format_html_join("", "{}", ((_render_node(child),) for child in node["content"]))
    if node_type == "paragraph":
        return format_html("<p>{}</p>", _render_inline_children(node.get("content", [])))
    if node_type == "heading":
        level = node["attrs"]["level"]
        return format_html("<h{0}>{1}</h{0}>", level, _render_inline_children(node["content"]))
    if node_type == "bulletList":
        return format_html("<ul>{}</ul>", _render_block_children(node["content"]))
    if node_type == "orderedList":
        return format_html("<ol>{}</ol>", _render_block_children(node["content"]))
    if node_type == "listItem":
        return format_html("<li>{}</li>", _render_block_children(node["content"]))
    return _render_text_node(node)


def _render_block_children(children: Iterable[dict[str, Any]]):
    return format_html_join("", "{}", ((_render_node(child),) for child in children))


def _render_inline_children(children: Iterable[dict[str, Any]]):
    return format_html_join("", "{}", ((_render_inline_node(child),) for child in children))


def _render_inline_node(node: dict[str, Any]):
    if node["type"] == "hardBreak":
        return mark_safe("<br>")
    return _render_text_node(node)


def _render_text_node(node: dict[str, Any]):
    rendered = node.get("text", "")
    for mark in node.get("marks", []):
        mark_type = mark["type"]
        if mark_type == "bold":
            rendered = format_html("<strong>{}</strong>", rendered)
        elif mark_type == "italic":
            rendered = format_html("<em>{}</em>", rendered)
        elif mark_type == "underline":
            rendered = format_html("<u>{}</u>", rendered)
        else:
            rendered = format_html(
                '<a href="{}" rel="noopener noreferrer nofollow" target="_blank">{}</a>',
                mark["attrs"]["href"],
                rendered,
            )
    return rendered
