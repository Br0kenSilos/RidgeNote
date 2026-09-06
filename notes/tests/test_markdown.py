"""Canonical body_json -> Markdown conversion.

Covers `notes.markdown`'s public `note_body_to_markdown()`/
`note_to_markdown()` and, for unknown-node/mark fallback behavior that
canonical validation makes unreachable through the public API, a small
number of narrowly scoped internal-renderer tests.
"""

import pytest

from notes import documents
from notes.markdown import (
    MarkdownConversionError,
    _render_document,
    _render_unknown_node,
    note_body_to_markdown,
    note_to_markdown,
)


def _doc(*content):
    return {"type": "doc", "content": list(content)}


def _paragraph(*content):
    return {"type": "paragraph", "content": list(content)}


def _text(value, marks=None):
    node = {"type": "text", "text": value}
    if marks:
        node["marks"] = marks
    return node


def _heading(level, *content):
    return {"type": "heading", "attrs": {"level": level}, "content": list(content)}


# ---------------------------------------------------------------------------
# Document root / paragraphs
# ---------------------------------------------------------------------------


def test_empty_document_renders_to_empty_string():
    assert note_body_to_markdown(documents.canonical_empty_document()) == ""


def test_single_paragraph():
    body = _doc(_paragraph(_text("Hello world")))
    assert note_body_to_markdown(body) == "Hello world\n"


def test_multiple_paragraphs_separated_by_blank_line():
    body = _doc(_paragraph(_text("First")), _paragraph(_text("Second")))
    assert note_body_to_markdown(body) == "First\n\nSecond\n"


def test_meaningful_empty_paragraph_between_two_paragraphs_is_preserved():
    body = _doc(_paragraph(_text("First")), _paragraph(), _paragraph(_text("Second")))
    rendered = note_body_to_markdown(body)
    # The empty paragraph must be distinguishable from an ordinary
    # single-blank-line paragraph gap, not silently dropped.
    assert rendered == "First\n\n\n\nSecond\n"


def test_output_ends_with_exactly_one_trailing_newline():
    body = _doc(_paragraph(_text("Text")))
    rendered = note_body_to_markdown(body)
    assert rendered.endswith("\n")
    assert not rendered.endswith("\n\n")


def test_conversion_does_not_mutate_input():
    body = _doc(_paragraph(_text("Text", marks=[{"type": "bold"}])))
    import copy

    original = copy.deepcopy(body)
    note_body_to_markdown(body)
    assert body == original


def test_repeated_calls_are_deterministic():
    body = _doc(_paragraph(_text("Same input")))
    assert note_body_to_markdown(body) == note_body_to_markdown(body)


# ---------------------------------------------------------------------------
# Headings
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("level,marker", [(1, "#"), (2, "##"), (3, "###")])
def test_heading_levels(level, marker):
    body = _doc(_heading(level, _text("Title")))
    assert note_body_to_markdown(body) == f"{marker} Title\n"


# ---------------------------------------------------------------------------
# Marks
# ---------------------------------------------------------------------------


def test_bold():
    body = _doc(_paragraph(_text("Bold", marks=[{"type": "bold"}])))
    assert note_body_to_markdown(body) == "**Bold**\n"


def test_italic():
    body = _doc(_paragraph(_text("Italic", marks=[{"type": "italic"}])))
    assert note_body_to_markdown(body) == "_Italic_\n"


def test_bold_and_italic():
    body = _doc(_paragraph(_text("Both", marks=[{"type": "bold"}, {"type": "italic"}])))
    assert note_body_to_markdown(body) == "**_Both_**\n"


def test_underline_renders_as_raw_html():
    body = _doc(_paragraph(_text("Underlined", marks=[{"type": "underline"}])))
    assert note_body_to_markdown(body) == "<u>Underlined</u>\n"


def test_underline_combined_with_other_marks():
    body = _doc(
        _paragraph(
            _text("All three", marks=[{"type": "bold"}, {"type": "italic"}, {"type": "underline"}])
        )
    )
    assert note_body_to_markdown(body) == "**_<u>All three</u>_**\n"


def test_mark_order_is_deterministic_regardless_of_stored_order():
    forward = _doc(_paragraph(_text("X", marks=[{"type": "bold"}, {"type": "italic"}])))
    reverse = _doc(_paragraph(_text("X", marks=[{"type": "italic"}, {"type": "bold"}])))
    assert note_body_to_markdown(forward) == note_body_to_markdown(reverse)


def test_duplicate_marks_do_not_double_wrap():
    body = _doc(_paragraph(_text("X", marks=[{"type": "bold"}, {"type": "bold"}])))
    assert note_body_to_markdown(body) == "**X**\n"


# ---------------------------------------------------------------------------
# Links
# ---------------------------------------------------------------------------


def test_link_with_visible_label():
    body = _doc(
        _paragraph(
            _text("RidgeNote", marks=[{"type": "link", "attrs": {"href": "https://example.com"}}])
        )
    )
    assert note_body_to_markdown(body) == "[RidgeNote](https://example.com)\n"


def test_link_where_label_equals_url_collapses_to_autolink():
    href = "https://example.com/page"
    body = _doc(_paragraph(_text(href, marks=[{"type": "link", "attrs": {"href": href}}])))
    assert note_body_to_markdown(body) == f"<{href}>\n"


def test_link_with_formatting_does_not_collapse_to_autolink():
    href = "https://example.com/page"
    body = _doc(
        _paragraph(
            _text(
                href,
                marks=[{"type": "bold"}, {"type": "link", "attrs": {"href": href}}],
            )
        )
    )
    assert note_body_to_markdown(body) == f"[**{href}**]({href})\n"


# ---------------------------------------------------------------------------
# Lists
# ---------------------------------------------------------------------------


def _bullet_list(*items):
    return {"type": "bulletList", "content": list(items)}


def _ordered_list(*items):
    return {"type": "orderedList", "content": list(items)}


def _list_item(*content):
    return {"type": "listItem", "content": list(content)}


def test_bullet_list():
    body = _doc(
        _bullet_list(
            _list_item(_paragraph(_text("One"))),
            _list_item(_paragraph(_text("Two"))),
        )
    )
    assert note_body_to_markdown(body) == "- One\n- Two\n"


def test_ordered_list_always_starts_at_one():
    body = _doc(
        _ordered_list(
            _list_item(_paragraph(_text("First"))),
            _list_item(_paragraph(_text("Second"))),
            _list_item(_paragraph(_text("Third"))),
        )
    )
    assert note_body_to_markdown(body) == "1. First\n2. Second\n3. Third\n"


def test_nested_lists_restart_numbering_and_indent():
    body = _doc(
        _ordered_list(
            _list_item(
                _paragraph(_text("Outer")),
                _bullet_list(_list_item(_paragraph(_text("Inner")))),
            )
        )
    )
    assert note_body_to_markdown(body) == "1. Outer\n  - Inner\n"


def test_multiple_paragraphs_in_one_list_item():
    body = _doc(
        _bullet_list(_list_item(_paragraph(_text("First para")), _paragraph(_text("Second para"))))
    )
    rendered = note_body_to_markdown(body)
    assert rendered.splitlines()[0] == "- First para"
    assert rendered.splitlines()[1] == "  Second para"


def test_empty_list_item():
    body = _doc(_bullet_list(_list_item(_paragraph())))
    assert note_body_to_markdown(body) == "-\n"


def test_hard_break_in_paragraph():
    body = _doc(_paragraph(_text("Line one"), {"type": "hardBreak"}, _text("Line two")))
    assert note_body_to_markdown(body) == "Line one  \nLine two\n"


def test_hard_break_inside_list_item():
    body = _doc(
        _bullet_list(
            _list_item(_paragraph(_text("Line one"), {"type": "hardBreak"}, _text("Line two")))
        )
    )
    assert note_body_to_markdown(body) == "- Line one  \n  Line two\n"


# ---------------------------------------------------------------------------
# Escaping
# ---------------------------------------------------------------------------


def test_unicode_text_preserved():
    body = _doc(_paragraph(_text("Café — 日本語 — emoji 🎉")))
    assert note_body_to_markdown(body) == "Café — 日本語 — emoji 🎉\n"


def test_metacharacters_escaped_mid_text():
    body = _doc(_paragraph(_text("5 * 3 = 15 and a_b and `code` and [x]")))
    rendered = note_body_to_markdown(body)
    assert rendered == "5 \\* 3 = 15 and a\\_b and \\`code\\` and \\[x\\]\n"


def test_backslash_itself_is_escaped():
    body = _doc(_paragraph(_text("C:\\path\\to\\file")))
    assert note_body_to_markdown(body) == "C:\\\\path\\\\to\\\\file\n"


def test_angle_brackets_escaped_to_prevent_html_injection():
    body = _doc(_paragraph(_text("<script>alert(1)</script>")))
    rendered = note_body_to_markdown(body)
    assert "<script>" not in rendered
    assert rendered == "\\<script\\>alert(1)\\</script\\>\n"


def test_heading_like_text_escaped_only_at_line_start():
    body = _doc(_paragraph(_text("# not a heading")))
    assert note_body_to_markdown(body) == "\\# not a heading\n"


def test_hash_mid_line_not_escaped():
    body = _doc(_paragraph(_text("This costs #5 today")))
    assert note_body_to_markdown(body) == "This costs #5 today\n"


def test_bullet_like_text_escaped_only_at_line_start():
    body = _doc(_paragraph(_text("- not a bullet")))
    assert note_body_to_markdown(body) == "\\- not a bullet\n"


def test_ordered_prefix_like_text_escaped_at_line_start():
    body = _doc(_paragraph(_text("1. not a list")))
    assert note_body_to_markdown(body) == "1\\. not a list\n"


def test_horizontal_rule_like_text_escaped_at_line_start():
    body = _doc(_paragraph(_text("--- not a rule")))
    assert note_body_to_markdown(body) == "\\--- not a rule\n"


def test_blockquote_like_text_escaped_at_line_start_via_angle_bracket_rule():
    body = _doc(_paragraph(_text("> not a quote")))
    assert note_body_to_markdown(body) == "\\> not a quote\n"


def test_line_start_marker_not_escaped_when_marked_text_precedes_it():
    # A marked (bold/italic/etc.) first run already adds a structural
    # prefix, so a *second*, unmarked run beginning with a
    # line-start-significant character is genuinely mid-line and must
    # not be escaped as if it were still at the true start of the line.
    body = _doc(_paragraph(_text("Bold", marks=[{"type": "bold"}]), _text(" # not escaped")))
    assert note_body_to_markdown(body) == "**Bold** # not escaped\n"


# ---------------------------------------------------------------------------
# Title / complete note output
# ---------------------------------------------------------------------------


def test_note_to_markdown_title_only():
    body = documents.canonical_empty_document()
    assert note_to_markdown("My Title", body) == "# My Title\n"


def test_note_to_markdown_title_and_body():
    body = _doc(_paragraph(_text("Body text")))
    assert note_to_markdown("My Title", body) == "# My Title\n\nBody text\n"


def test_note_to_markdown_escapes_title():
    body = documents.canonical_empty_document()
    assert note_to_markdown("Title * with _ chars", body) == "# Title \\* with \\_ chars\n"


# ---------------------------------------------------------------------------
# Invalid input
# ---------------------------------------------------------------------------


def test_invalid_canonical_document_raises():
    with pytest.raises(MarkdownConversionError):
        note_body_to_markdown({"type": "doc", "content": [{"type": "blockquote"}]})


def test_note_to_markdown_also_validates_body():
    with pytest.raises(MarkdownConversionError):
        note_to_markdown("Title", {"type": "doc", "content": [{"type": "codeBlock"}]})


# ---------------------------------------------------------------------------
# Unknown node / mark fallback (internal-renderer only -- canonical
# validation makes this unreachable through the public API)
# ---------------------------------------------------------------------------


def test_unknown_node_renders_marker_with_descendant_text():
    unknown = {
        "type": "codeBlock",
        "content": [{"type": "text", "text": "print('hi')"}],
    }
    rendered = _render_unknown_node(unknown)
    assert rendered == "[Unsupported RidgeNote content: codeBlock] print('hi')"


def test_unknown_node_with_no_descendant_text_renders_bare_marker():
    unknown = {"type": "image"}
    assert _render_unknown_node(unknown) == "[Unsupported RidgeNote content: image]"


def test_unknown_node_inside_document_is_rendered_via_internal_entry_point():
    doc = _doc({"type": "horizontalRule"})
    rendered = _render_document(doc)
    assert rendered == "[Unsupported RidgeNote content: horizontalRule]\n"


def test_unknown_mark_is_silently_omitted_not_marked():
    doc = _doc(_paragraph(_text("Struck", marks=[{"type": "strike"}])))
    assert _render_document(doc) == "Struck\n"
