"""plain-text export.

`documents.derive_export_plain_text()` renders a note's current
`body_json` on demand, preserving ordered/unordered list markers,
per-level nested-list indentation, and link URLs -- unlike the
unchanged `derive_plain_text()` that still populates the stored,
search-indexed `body_plain_text` field. See notes/documents.py.
"""

from notes import documents


def _doc(*content):
    return {"type": "doc", "content": list(content)}


def _paragraph(*inline):
    return {"type": "paragraph", "content": list(inline)}


def _heading(level, *inline):
    return {"type": "heading", "attrs": {"level": level}, "content": list(inline)}


def _text(value, marks=None):
    node = {"type": "text", "text": value}
    if marks is not None:
        node["marks"] = marks
    return node


def _link(href):
    return [{"type": "link", "attrs": {"href": href}}]


def _list_item(*content):
    return {"type": "listItem", "content": list(content)}


def _bullet_list(*items):
    return {"type": "bulletList", "content": list(items)}


def _ordered_list(*items):
    return {"type": "orderedList", "content": list(items)}


def test_ordered_list_numbers_items():
    document = _doc(
        _ordered_list(
            _list_item(_paragraph(_text("First"))),
            _list_item(_paragraph(_text("Second"))),
            _list_item(_paragraph(_text("Third"))),
        )
    )

    assert documents.derive_export_plain_text(document) == "1. First\n2. Second\n3. Third"


def test_unordered_list_uses_hyphen_bullets():
    document = _doc(
        _bullet_list(
            _list_item(_paragraph(_text("First"))),
            _list_item(_paragraph(_text("Second"))),
        )
    )

    assert documents.derive_export_plain_text(document) == "- First\n- Second"


def test_nested_ordered_and_unordered_combination_indents_by_level():
    document = _doc(
        _ordered_list(
            _list_item(
                _paragraph(_text("Top item one")),
                _bullet_list(
                    _list_item(_paragraph(_text("Nested bullet one"))),
                    _list_item(_paragraph(_text("Nested bullet two"))),
                ),
            ),
            _list_item(_paragraph(_text("Top item two"))),
        )
    )

    assert documents.derive_export_plain_text(document) == (
        "1. Top item one\n"
        "  - Nested bullet one\n"
        "  - Nested bullet two\n"
        "2. Top item two"
    )


def test_numbering_resets_for_separate_lists():
    document = _doc(
        _ordered_list(
            _list_item(_paragraph(_text("First list, item one"))),
            _list_item(_paragraph(_text("First list, item two"))),
        ),
        _paragraph(_text("Separator paragraph")),
        _ordered_list(
            _list_item(_paragraph(_text("Second list, item one"))),
        ),
    )

    result = documents.derive_export_plain_text(document)

    assert "1. First list, item one" in result
    assert "2. First list, item two" in result
    assert "1. Second list, item one" in result
    # The second list must not continue the first list's count.
    assert "2. Second list, item one" not in result


def test_link_with_visible_text_appends_url():
    document = _doc(_paragraph(_text("Visit our site", marks=_link("https://example.test"))))

    assert (
        documents.derive_export_plain_text(document)
        == "Visit our site (https://example.test)"
    )


def test_url_only_link_is_not_duplicated():
    document = _doc(
        _paragraph(_text("https://example.test", marks=_link("https://example.test")))
    )

    assert documents.derive_export_plain_text(document) == "https://example.test"


def test_headings_use_plain_text_with_blank_line_separation_no_markdown_markers():
    document = _doc(
        _heading(1, _text("Title")),
        _paragraph(_text("Body text")),
    )

    result = documents.derive_export_plain_text(document)

    assert result == "Title\n\nBody text"
    assert "#" not in result


def test_paragraphs_are_separated_by_a_blank_line():
    document = _doc(_paragraph(_text("First paragraph")), _paragraph(_text("Second paragraph")))

    assert documents.derive_export_plain_text(document) == (
        "First paragraph\n\nSecond paragraph"
    )


def test_empty_document_produces_calm_empty_output():
    assert documents.derive_export_plain_text(documents.canonical_empty_document()) == ""


def test_hostile_text_remains_plain():
    hostile = '<script>alert(1)</script> & "quotes" \'apostrophes\''
    document = _doc(_paragraph(_text(hostile)))

    assert documents.derive_export_plain_text(document) == hostile


def test_list_item_paragraph_hard_break_indents_continuation_line():
    document = _doc(
        _ordered_list(
            _list_item(
                _paragraph(
                    _text("First line"),
                    {"type": "hardBreak"},
                    _text("continuation"),
                )
            )
        )
    )

    assert documents.derive_export_plain_text(document) == "1. First line\n   continuation"
