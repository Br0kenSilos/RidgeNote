"""Full Search/Filter.

Covers `notes.services.validate_full_search_text()`/`search_notes_full()`
at the service level, and the `notes:full_search` view's request
parsing/validation, pagination, active-filter chips, and rendering.
Global Search (`search_notes_global()`) and Quick Switch are untouched
and are not re-tested here -- see `test_global_search.py`
and `test_quick_switch_search.py` for their own coverage.
"""

from datetime import timedelta

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.models import Note

PASSWORD = "LongUniquePassword123!"


@pytest.fixture(autouse=True)
def use_simple_staticfiles_storage(settings):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[account_services.SESSION_GENERATION_KEY] = user.session_generation
    session[account_services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def _set_body_plain_text(note, text):
    note.body_plain_text = text
    note.save(update_fields=["body_plain_text"])


def _make_note(owner, title="Untitled", *, folder=None, body=""):
    note = services.create_note(owner=owner, folder=folder)
    services.rename_note(note=note, title=title)
    if body:
        _set_body_plain_text(note, body)
    return note


def _make_tag(owner, name, color="slate"):
    return services.get_or_create_tag(owner=owner, name=name, color=color)


def _trash_note(note, *, days_ago=1):
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=days_ago))


def _empty_trash_note(note):
    # Administrator-only recoverable: `emptied_at` set, well within the
    # 90-day recoverable window.
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(emptied_at=timezone.now())


def _main_content(response) -> str:
    """Every Full Search response also renders the shared narrow-drawer
    navigation tree (`_nav_drawer.html`), which lists *every* owned note
    and folder regardless of the current search/filter state -- exactly
    like every other page that includes it (All Notes, Tag Manager,
    note-detail). Assertions about what the *search results themselves*
    do/don't contain must be scoped to the content before that drawer
    markup, or a note title that's correctly excluded from the results
    could still be found via the always-present navigation tree."""
    content = response.content.decode()
    return content.split('id="workspace-drawer"')[0]


def _search(owner, **kwargs):
    kwargs.setdefault("query", "")
    kwargs.setdefault("search_in", "both")
    kwargs.setdefault("tag_ids", [])
    kwargs.setdefault("tag_mode", "any")
    kwargs.setdefault("tag_status", "any")
    kwargs.setdefault("folder_id", None)
    kwargs.setdefault("folder_is_unfiled", False)
    kwargs.setdefault("include_trash", False)
    return list(services.search_notes_full(owner=owner, **kwargs))


# ---------------------------------------------------------------------------
# validate_full_search_text()
# ---------------------------------------------------------------------------


def test_validate_full_search_text_blank_is_valid():
    assert services.validate_full_search_text("") is None
    assert services.validate_full_search_text("   ") is None


@pytest.mark.django_db
def test_validate_full_search_text_ordinary_query_is_valid():
    assert services.validate_full_search_text("roadmap review") is None


@pytest.mark.django_db
def test_validate_full_search_text_quoted_phrase_is_valid():
    assert services.validate_full_search_text('"quarterly roadmap"') is None


@pytest.mark.django_db
def test_validate_full_search_text_unmatched_quote_is_valid():
    assert services.validate_full_search_text('roadmap "unclosed') is None


@pytest.mark.django_db
def test_validate_full_search_text_stop_word_only_is_invalid():
    assert services.validate_full_search_text("the a an") == "query_has_no_searchable_terms"


@pytest.mark.django_db
def test_validate_full_search_text_punctuation_only_is_invalid():
    assert services.validate_full_search_text("!!! ---") == "query_has_no_searchable_terms"


def test_validate_full_search_text_overlong_is_invalid():
    overlong = "a" * (services.GLOBAL_SEARCH_MAX_QUERY_LENGTH + 1)
    assert services.validate_full_search_text(overlong) == "query_too_long"


@pytest.mark.django_db
def test_validate_full_search_text_whitespace_padded_valid_query_is_valid():
    assert services.validate_full_search_text("   roadmap   ") is None


# ---------------------------------------------------------------------------
# search_notes_full() -- text
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_search_notes_full_matches_title():
    owner = create_account("fs-title-owner")
    note = _make_note(owner, "Quarterly Roadmap Review")

    assert note in _search(owner, query="roadmap")


@pytest.mark.django_db
def test_search_notes_full_matches_body():
    owner = create_account("fs-body-owner")
    note = _make_note(owner, "Untitled", body="a note mentioning giraffes specifically")

    assert note in _search(owner, query="giraffe")


@pytest.mark.django_db
def test_search_notes_full_title_ranks_above_body():
    owner = create_account("fs-rank-owner")
    title_match = _make_note(owner, "Giraffe Roadmap")
    body_match = _make_note(owner, "Other Note", body="mentions a giraffe in passing")

    results = _search(owner, query="giraffe")

    assert results.index(title_match) < results.index(body_match)


@pytest.mark.django_db
def test_search_notes_full_blank_text_orders_by_modified_desc():
    owner = create_account("fs-blank-order-owner")
    older = _make_note(owner, "Older")
    newer = _make_note(owner, "Newer")

    results = _search(owner, query="")

    assert results.index(newer) < results.index(older)


@pytest.mark.django_db
def test_search_notes_full_nonblank_unusable_text_returns_zero_results_not_broader():
    owner = create_account("fs-unusable-owner")
    _make_note(owner, "Ordinary Note")

    # Blank text would return the note above; nonblank-but-unusable text
    # must not silently fall back to that broader behavior.
    results = _search(owner, query="the a an")

    assert results == []


@pytest.mark.django_db
def test_search_notes_full_overlong_text_returns_zero_results():
    owner = create_account("fs-overlong-owner")
    _make_note(owner, "Ordinary Note")
    overlong = "a" * (services.GLOBAL_SEARCH_MAX_QUERY_LENGTH + 1)

    assert _search(owner, query=overlong) == []


@pytest.mark.django_db
def test_search_notes_full_hostile_content_survives_as_literal_text_in_segments():
    owner = create_account("fs-hostile-owner")
    _make_note(owner, "<script>alert(1)</script> roadmap")

    results = list(
        services.search_notes_full(
            owner=owner,
            query="roadmap",
            search_in="both",
            tag_ids=[],
            tag_mode="any",
            tag_status="any",
            folder_id=None,
            folder_is_unfiled=False,
            include_trash=False,
        )
    )
    matched = results[0]
    segments = services.build_segments_from_headline(matched.title_headline)
    combined = "".join(segment["text"] for segment in segments)

    assert "<script>" in combined
    assert all(isinstance(segment["highlight"], bool) for segment in segments)


# ---------------------------------------------------------------------------
# search_notes_full() -- Search in
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_search_notes_full_search_in_both_matches_title_and_body():
    owner = create_account("fs-search-in-both-owner")
    title_match = _make_note(owner, "Roadmap Review")
    body_match = _make_note(owner, "Untitled", body="mentions the roadmap in passing")

    results = _search(owner, query="roadmap", search_in="both")

    assert title_match in results
    assert body_match in results


@pytest.mark.django_db
def test_search_notes_full_search_in_title_matches_title_not_body_only():
    owner = create_account("fs-search-in-title-owner")
    title_match = _make_note(owner, "Roadmap Review")
    body_only = _make_note(owner, "Untitled", body="mentions the roadmap in passing")

    results = _search(owner, query="roadmap", search_in="title")

    assert title_match in results
    assert body_only not in results


@pytest.mark.django_db
def test_search_notes_full_search_in_body_matches_body_not_title_only():
    owner = create_account("fs-search-in-body-owner")
    title_only = _make_note(owner, "Roadmap Review")
    body_match = _make_note(owner, "Untitled", body="mentions the roadmap in passing")

    results = _search(owner, query="roadmap", search_in="body")

    assert body_match in results
    assert title_only not in results


@pytest.mark.django_db
def test_search_notes_full_search_in_both_preserves_title_over_body_weighting():
    owner = create_account("fs-search-in-weight-owner")
    title_match = _make_note(owner, "Giraffe Roadmap")
    body_match = _make_note(owner, "Other Note", body="mentions a giraffe in passing")

    results = _search(owner, query="giraffe", search_in="both")

    assert results.index(title_match) < results.index(body_match)


@pytest.mark.django_db
def test_search_notes_full_search_in_title_highlights_title_only():
    owner = create_account("fs-search-in-title-highlight-owner")
    note = _make_note(owner, "Roadmap Review", body="an unrelated body about giraffes")

    results = list(
        services.search_notes_full(
            owner=owner,
            query="roadmap",
            search_in="title",
            tag_ids=[],
            tag_mode="any",
            tag_status="any",
            folder_id=None,
            folder_is_unfiled=False,
            include_trash=False,
        )
    )

    assert len(results) == 1
    matched = results[0]
    assert matched.pk == note.pk
    assert matched.title_headline
    assert not hasattr(matched, "body_headline")


@pytest.mark.django_db
def test_search_notes_full_search_in_body_highlights_body_only():
    owner = create_account("fs-search-in-body-highlight-owner")
    note = _make_note(owner, "Unrelated Title", body="a giraffe wandered through the roadmap")

    results = list(
        services.search_notes_full(
            owner=owner,
            query="roadmap",
            search_in="body",
            tag_ids=[],
            tag_mode="any",
            tag_status="any",
            folder_id=None,
            folder_is_unfiled=False,
            include_trash=False,
        )
    )

    assert len(results) == 1
    matched = results[0]
    assert matched.pk == note.pk
    assert matched.body_headline
    assert not hasattr(matched, "title_headline")


# ---------------------------------------------------------------------------
# search_notes_full() -- Tags (Any/All)
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_search_notes_full_zero_tags_has_no_effect():
    owner = create_account("fs-zero-tags-owner")
    note = _make_note(owner, "Note")

    assert note in _search(owner, tag_ids=[])


@pytest.mark.django_db
def test_search_notes_full_one_tag():
    owner = create_account("fs-one-tag-owner")
    tag = _make_tag(owner, "SERVER")
    matching = _make_note(owner, "Matching")
    services.assign_tag_to_note(note=matching, tag=tag)
    other = _make_note(owner, "Other")

    results = _search(owner, tag_ids=[tag.id], tag_mode="any")

    assert matching in results
    assert other not in results


@pytest.mark.django_db
def test_search_notes_full_any_mode_matches_at_least_one_selected_tag():
    owner = create_account("fs-any-owner")
    server_tag = _make_tag(owner, "SERVER")
    network_tag = _make_tag(owner, "NETWORK")
    server_only = _make_note(owner, "Server only")
    services.assign_tag_to_note(note=server_only, tag=server_tag)
    network_only = _make_note(owner, "Network only")
    services.assign_tag_to_note(note=network_only, tag=network_tag)
    neither = _make_note(owner, "Neither")

    results = _search(owner, tag_ids=[server_tag.id, network_tag.id], tag_mode="any")

    assert server_only in results
    assert network_only in results
    assert neither not in results


@pytest.mark.django_db
def test_search_notes_full_all_mode_requires_every_selected_tag():
    owner = create_account("fs-all-owner")
    server_tag = _make_tag(owner, "SERVER")
    network_tag = _make_tag(owner, "NETWORK")
    both = _make_note(owner, "Both")
    services.assign_tag_to_note(note=both, tag=server_tag)
    services.assign_tag_to_note(note=both, tag=network_tag)
    server_only = _make_note(owner, "Server only")
    services.assign_tag_to_note(note=server_only, tag=server_tag)

    results = _search(owner, tag_ids=[server_tag.id, network_tag.id], tag_mode="all")

    assert both in results
    assert server_only not in results


@pytest.mark.django_db
def test_search_notes_full_any_mode_no_duplicate_rows():
    owner = create_account("fs-any-dup-owner")
    tag_a = _make_tag(owner, "ALPHA")
    tag_b = _make_tag(owner, "BETA")
    note = _make_note(owner, "Both tags")
    services.assign_tag_to_note(note=note, tag=tag_a)
    services.assign_tag_to_note(note=note, tag=tag_b)

    results = _search(owner, tag_ids=[tag_a.id, tag_b.id], tag_mode="any")

    assert results.count(note) == 1


@pytest.mark.django_db
def test_search_notes_full_all_mode_no_duplicate_rows():
    owner = create_account("fs-all-dup-owner")
    tag_a = _make_tag(owner, "ALPHA")
    tag_b = _make_tag(owner, "BETA")
    note = _make_note(owner, "Both tags")
    services.assign_tag_to_note(note=note, tag=tag_a)
    services.assign_tag_to_note(note=note, tag=tag_b)

    results = _search(owner, tag_ids=[tag_a.id, tag_b.id], tag_mode="all")

    assert results.count(note) == 1


@pytest.mark.django_db
def test_search_notes_full_zero_use_tag_matches_nothing():
    owner = create_account("fs-zero-use-owner")
    zero_use_tag = _make_tag(owner, "UNUSED")
    _make_note(owner, "Some note")

    results = _search(owner, tag_ids=[zero_use_tag.id], tag_mode="any")

    assert results == []


# ---------------------------------------------------------------------------
# search_notes_full() -- Tag status
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_search_notes_full_tag_status_any_has_no_restriction():
    owner = create_account("fs-status-any-owner")
    tagged = _make_note(owner, "Tagged")
    services.assign_tag_to_note(note=tagged, tag=_make_tag(owner, "A"))
    untagged = _make_note(owner, "Untagged")

    results = _search(owner, tag_status="any")

    assert tagged in results
    assert untagged in results


@pytest.mark.django_db
def test_search_notes_full_tag_status_tagged_matches_only_notes_with_tags():
    owner = create_account("fs-status-tagged-owner")
    tagged = _make_note(owner, "Tagged")
    services.assign_tag_to_note(note=tagged, tag=_make_tag(owner, "A"))
    untagged = _make_note(owner, "Untagged")

    results = _search(owner, tag_status="tagged")

    assert tagged in results
    assert untagged not in results


@pytest.mark.django_db
def test_search_notes_full_tag_status_untagged_matches_only_notes_without_tags():
    owner = create_account("fs-status-untagged-owner")
    tagged = _make_note(owner, "Tagged")
    services.assign_tag_to_note(note=tagged, tag=_make_tag(owner, "A"))
    untagged = _make_note(owner, "Untagged")

    results = _search(owner, tag_status="untagged")

    assert untagged in results
    assert tagged not in results


@pytest.mark.django_db
def test_search_notes_full_specific_tags_with_tagged_status_is_valid_and_redundant():
    owner = create_account("fs-status-redundant-owner")
    tag = _make_tag(owner, "SERVER")
    note = _make_note(owner, "Tagged note")
    services.assign_tag_to_note(note=note, tag=tag)

    plain_results = _search(owner, tag_ids=[tag.id], tag_mode="any", tag_status="any")
    redundant_results = _search(owner, tag_ids=[tag.id], tag_mode="any", tag_status="tagged")

    assert list(plain_results) == list(redundant_results)


@pytest.mark.django_db
def test_search_notes_full_tag_status_no_duplicate_rows():
    owner = create_account("fs-status-dup-owner")
    note = _make_note(owner, "Multi-tag")
    services.assign_tag_to_note(note=note, tag=_make_tag(owner, "A"))
    services.assign_tag_to_note(note=note, tag=_make_tag(owner, "B"))
    services.assign_tag_to_note(note=note, tag=_make_tag(owner, "C"))

    results = _search(owner, tag_status="tagged")

    assert results.count(note) == 1


# ---------------------------------------------------------------------------
# search_notes_full() -- Folder
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_search_notes_full_no_folder_is_global():
    owner = create_account("fs-global-owner")
    folder = services.create_folder(owner=owner, name="Infrastructure")
    in_folder = _make_note(owner, "In folder", folder=folder)
    unfiled = _make_note(owner, "Unfiled")

    results = _search(owner)

    assert in_folder in results
    assert unfiled in results


@pytest.mark.django_db
def test_search_notes_full_selected_folder_scopes_to_direct_notes():
    owner = create_account("fs-folder-owner")
    folder = services.create_folder(owner=owner, name="Infrastructure")
    other_folder = services.create_folder(owner=owner, name="Other")
    in_folder = _make_note(owner, "In folder", folder=folder)
    in_other = _make_note(owner, "In other", folder=other_folder)

    results = _search(owner, folder_id=folder.id)

    assert in_folder in results
    assert in_other not in results


@pytest.mark.django_db
def test_search_notes_full_unfiled_scopes_to_notes_without_folder():
    owner = create_account("fs-unfiled-owner")
    folder = services.create_folder(owner=owner, name="Infrastructure")
    in_folder = _make_note(owner, "In folder", folder=folder)
    unfiled = _make_note(owner, "Unfiled")

    results = _search(owner, folder_is_unfiled=True)

    assert unfiled in results
    assert in_folder not in results


# ---------------------------------------------------------------------------
# search_notes_full() -- Include Trash
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_search_notes_full_include_trash_false_excludes_trashed():
    owner = create_account("fs-no-trash-owner")
    active = _make_note(owner, "Active")
    trashed = _make_note(owner, "Trashed")
    _trash_note(trashed)

    results = _search(owner, include_trash=False)

    assert active in results
    assert trashed not in results


@pytest.mark.django_db
def test_search_notes_full_include_trash_true_includes_active_and_trash():
    owner = create_account("fs-include-trash-owner")
    active = _make_note(owner, "Active")
    trashed = _make_note(owner, "Trashed")
    _trash_note(trashed)

    results = _search(owner, include_trash=True)

    assert active in results
    assert trashed in results


@pytest.mark.django_db
def test_search_notes_full_never_includes_administrator_recoverable_notes():
    owner = create_account("fs-admin-excl-owner")
    hidden = _make_note(owner, "Hidden Recoverable")
    _empty_trash_note(hidden)

    for include_trash in (False, True):
        assert hidden not in _search(owner, include_trash=include_trash)


@pytest.mark.django_db
def test_search_notes_full_administrator_recoverable_excluded_even_with_matching_text_and_tag():
    owner = create_account("fs-admin-excl-combo-owner")
    tag = _make_tag(owner, "SERVER")
    hidden = _make_note(owner, "Server Roadmap", body="giraffe content")
    services.assign_tag_to_note(note=hidden, tag=tag)
    _empty_trash_note(hidden)

    results = _search(
        owner, query="roadmap", tag_ids=[tag.id], tag_mode="any", include_trash=True
    )

    assert hidden not in results


@pytest.mark.django_db
def test_search_notes_full_old_trash_beyond_visible_window_excluded_from_include_trash():
    owner = create_account("fs-old-trash-owner")
    old = _make_note(owner, "Old trash")
    _trash_note(old, days_ago=45)

    assert old not in _search(owner, include_trash=True)


# ---------------------------------------------------------------------------
# search_notes_full() -- combined filters
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_search_notes_full_text_and_tags_combined():
    owner = create_account("fs-combo-text-tags-owner")
    tag = _make_tag(owner, "SERVER")
    matching = _make_note(owner, "Roadmap")
    services.assign_tag_to_note(note=matching, tag=tag)
    text_only = _make_note(owner, "Roadmap without tag")
    tag_only = _make_note(owner, "Untitled")
    services.assign_tag_to_note(note=tag_only, tag=tag)

    results = _search(owner, query="roadmap", tag_ids=[tag.id], tag_mode="any")

    assert matching in results
    assert text_only not in results
    assert tag_only not in results


@pytest.mark.django_db
def test_search_notes_full_text_and_folder_combined():
    owner = create_account("fs-combo-text-folder-owner")
    folder = services.create_folder(owner=owner, name="Infrastructure")
    matching = _make_note(owner, "Roadmap", folder=folder)
    wrong_folder = _make_note(owner, "Roadmap Elsewhere")

    results = _search(owner, query="roadmap", folder_id=folder.id)

    assert matching in results
    assert wrong_folder not in results


@pytest.mark.django_db
def test_search_notes_full_text_and_include_trash_combined():
    owner = create_account("fs-combo-text-include-trash-owner")
    trashed_match = _make_note(owner, "Trashed Roadmap")
    _trash_note(trashed_match)

    assert trashed_match not in _search(owner, query="roadmap", include_trash=False)
    assert trashed_match in _search(owner, query="roadmap", include_trash=True)


@pytest.mark.django_db
def test_search_notes_full_search_in_and_tags_combined():
    owner = create_account("fs-combo-search-in-tags-owner")
    tag = _make_tag(owner, "SERVER")
    matching = _make_note(owner, "Roadmap", body="unrelated body")
    services.assign_tag_to_note(note=matching, tag=tag)
    body_only_same_tag = _make_note(owner, "Unrelated Title", body="roadmap mentioned here")
    services.assign_tag_to_note(note=body_only_same_tag, tag=tag)

    results = _search(owner, query="roadmap", search_in="title", tag_ids=[tag.id], tag_mode="any")

    assert matching in results
    assert body_only_same_tag not in results


@pytest.mark.django_db
def test_search_notes_full_search_in_and_folder_combined():
    owner = create_account("fs-combo-search-in-folder-owner")
    folder = services.create_folder(owner=owner, name="Infrastructure")
    matching = _make_note(owner, "Roadmap", folder=folder)
    wrong_folder = _make_note(owner, "Roadmap Elsewhere")

    results = _search(owner, query="roadmap", search_in="title", folder_id=folder.id)

    assert matching in results
    assert wrong_folder not in results


@pytest.mark.django_db
def test_search_notes_full_search_in_and_include_trash_combined():
    owner = create_account("fs-combo-search-in-trash-owner")
    trashed_match = _make_note(owner, "Roadmap", body="unrelated")
    _trash_note(trashed_match)

    assert trashed_match not in _search(owner, query="roadmap", search_in="title")
    assert trashed_match in _search(
        owner, query="roadmap", search_in="title", include_trash=True
    )


@pytest.mark.django_db
def test_search_notes_full_search_in_and_tag_status_combined():
    owner = create_account("fs-combo-search-in-status-owner")
    tagged = _make_note(owner, "Roadmap", body="unrelated")
    services.assign_tag_to_note(note=tagged, tag=_make_tag(owner, "A"))
    untagged = _make_note(owner, "Roadmap Two", body="unrelated")

    results = _search(owner, query="roadmap", search_in="title", tag_status="tagged")

    assert tagged in results
    assert untagged not in results


@pytest.mark.django_db
def test_search_notes_full_tags_folder_include_trash_combined():
    owner = create_account("fs-combo-full-owner")
    tag = _make_tag(owner, "SERVER")
    folder = services.create_folder(owner=owner, name="Infrastructure")
    matching = _make_note(owner, "Matches everything", folder=folder)
    services.assign_tag_to_note(note=matching, tag=tag)
    _trash_note(matching)

    wrong_folder = _make_note(owner, "Wrong folder")
    services.assign_tag_to_note(note=wrong_folder, tag=tag)
    _trash_note(wrong_folder)

    results = _search(
        owner, tag_ids=[tag.id], tag_mode="any", folder_id=folder.id, include_trash=True
    )

    assert matching in results
    assert wrong_folder not in results


@pytest.mark.django_db
def test_search_notes_full_full_combination_of_every_filter_type():
    owner = create_account("fs-combo-everything-owner")
    tag = _make_tag(owner, "SERVER")
    folder = services.create_folder(owner=owner, name="Infrastructure")
    matching = _make_note(owner, "Server Roadmap", folder=folder, body="giraffe notes")
    services.assign_tag_to_note(note=matching, tag=tag)

    results = _search(
        owner,
        query="roadmap",
        search_in="both",
        tag_ids=[tag.id],
        tag_mode="all",
        tag_status="tagged",
        folder_id=folder.id,
        include_trash=False,
    )

    assert matching in results


# ---------------------------------------------------------------------------
# search_notes_full() -- owner isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_search_notes_full_owner_isolation():
    owner = create_account("fs-owner-iso-owner")
    other = create_account("fs-owner-iso-other")
    other_note = _make_note(other, "Roadmap")
    other_tag = _make_tag(other, "SERVER")
    services.assign_tag_to_note(note=other_note, tag=other_tag)

    assert other_note not in _search(owner, query="roadmap")
    assert other_note not in _search(owner, tag_ids=[other_tag.id])
    assert other_note not in _search(owner, include_trash=True)


# ===========================================================================
# View-level tests
# ===========================================================================


@pytest.mark.django_db
def test_full_search_view_requires_login():
    client = Client()

    response = client.get(reverse("notes:full_search"))

    assert response.status_code in (302, 401, 403)


@pytest.mark.django_db
def test_full_search_view_renders_matching_note():
    owner = create_account("fsv-basic-owner")
    _make_note(owner, "Quarterly Roadmap")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"q": "roadmap"})
    content = response.content.decode()

    assert response.status_code == 200
    assert "Roadmap" in content


@pytest.mark.django_db
def test_full_search_view_unusable_text_shows_validation_message_and_no_results():
    owner = create_account("fsv-invalid-text-owner")
    _make_note(owner, "Ordinary Note")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"q": "the a an"})
    content = _main_content(response)

    assert response.status_code == 200
    assert "Ordinary Note" not in content
    assert "full-search-validation" in content


@pytest.mark.django_db
def test_full_search_view_overlong_text_shows_validation_message():
    owner = create_account("fsv-overlong-owner")
    client = authenticated_client(owner)
    overlong = "a" * (services.GLOBAL_SEARCH_MAX_QUERY_LENGTH + 1)

    response = client.get(reverse("notes:full_search"), {"q": overlong})
    content = response.content.decode()

    assert response.status_code == 200
    assert "full-search-validation" in content


@pytest.mark.django_db
def test_full_search_view_bare_get_is_idle_shows_no_results():
    owner = create_account("fsv-bare-idle-owner")
    _make_note(owner, "Whatever Title")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"))
    content = _main_content(response)

    assert response.status_code == 200
    assert "Whatever Title" not in content
    assert "full-search-validation" not in content
    assert "Enter search text or choose one or more filters." in content


@pytest.mark.django_db
def test_full_search_view_explicit_all_defaults_is_idle_shows_no_results():
    owner = create_account("fsv-explicit-idle-owner")
    _make_note(owner, "Whatever Title")
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"),
        {"q": "", "tag_mode": "any", "tag_status": "any", "search_in": "both"},
    )
    content = _main_content(response)

    assert response.status_code == 200
    assert "Whatever Title" not in content
    assert "Enter search text or choose one or more filters." in content


@pytest.mark.django_db
def test_full_search_view_text_criterion_activates_search():
    owner = create_account("fsv-idle-text-owner")
    _make_note(owner, "Roadmap Review")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"q": "roadmap"})
    content = response.content.decode()

    assert "Roadmap Review" in content
    assert "Enter search text or choose one or more filters." not in content


@pytest.mark.django_db
def test_full_search_view_specific_tag_activates_search():
    owner = create_account("fsv-idle-tag-owner")
    tag = _make_tag(owner, "SERVER")
    matching = _make_note(owner, "Tagged note")
    services.assign_tag_to_note(note=matching, tag=tag)
    _make_note(owner, "Other note")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"tag": str(tag.id)})
    content = _main_content(response)

    assert "Tagged note" in content
    assert "Other note" not in content
    assert "Enter search text or choose one or more filters." not in response.content.decode()


@pytest.mark.django_db
def test_full_search_view_tagged_status_activates_search():
    owner = create_account("fsv-idle-tagged-owner")
    tagged = _make_note(owner, "Tagged note")
    services.assign_tag_to_note(note=tagged, tag=_make_tag(owner, "A"))
    _make_note(owner, "Untagged note")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"tag_status": "tagged"})
    content = _main_content(response)

    assert "Tagged note" in content
    assert "Untagged note" not in content
    assert "Enter search text or choose one or more filters." not in response.content.decode()


@pytest.mark.django_db
def test_full_search_view_untagged_status_activates_search():
    owner = create_account("fsv-idle-untagged-owner")
    tagged = _make_note(owner, "Tagged note")
    services.assign_tag_to_note(note=tagged, tag=_make_tag(owner, "A"))
    _make_note(owner, "Untagged note")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"tag_status": "untagged"})
    content = _main_content(response)

    assert "Untagged note" in content
    assert "Tagged note" not in content
    assert "Enter search text or choose one or more filters." not in response.content.decode()


@pytest.mark.django_db
def test_full_search_view_folder_scope_activates_search():
    owner = create_account("fsv-idle-folder-owner")
    folder = services.create_folder(owner=owner, name="Infrastructure")
    _make_note(owner, "In folder", folder=folder)
    _make_note(owner, "Unfiled note")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"folder": str(folder.id)})
    content = _main_content(response)

    assert "In folder" in content
    assert "Unfiled note" not in content
    assert "Enter search text or choose one or more filters." not in response.content.decode()


@pytest.mark.django_db
def test_full_search_view_unfiled_scope_activates_search():
    owner = create_account("fsv-idle-unfiled-owner")
    folder = services.create_folder(owner=owner, name="Infrastructure")
    _make_note(owner, "In folder", folder=folder)
    _make_note(owner, "Unfiled note")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"folder": "unfiled"})
    content = _main_content(response)

    assert "Unfiled note" in content
    assert "In folder" not in content
    assert "Enter search text or choose one or more filters." not in response.content.decode()


@pytest.mark.django_db
def test_full_search_view_include_trash_activates_search():
    owner = create_account("fsv-idle-include-trash-owner")
    _make_note(owner, "Active note")
    trashed = _make_note(owner, "Trashed note")
    _trash_note(trashed)
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"include_trash": "1"})
    content = _main_content(response)

    assert "Active note" in content
    assert "Trashed note" in content
    assert "Enter search text or choose one or more filters." not in response.content.decode()


@pytest.mark.django_db
def test_full_search_view_search_in_alone_does_not_activate_search():
    owner = create_account("fsv-idle-search-in-owner")
    _make_note(owner, "Whatever Title")
    client = authenticated_client(owner)

    for search_in in ("title", "body"):
        response = client.get(reverse("notes:full_search"), {"search_in": search_in})
        content = _main_content(response)

        assert "Whatever Title" not in content
        assert "Enter search text or choose one or more filters." in content


@pytest.mark.django_db
def test_full_search_view_invalid_state_is_not_treated_as_idle():
    owner = create_account("fsv-idle-invalid-owner")
    _make_note(owner, "Some note")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"q": "the a an"})
    content = response.content.decode()

    assert "full-search-validation" in content
    assert "Enter search text or choose one or more filters." not in content


@pytest.mark.django_db
def test_full_search_view_exactly_20_tags_accepted():
    owner = create_account("fsv-20-tags-owner")
    tags = [_make_tag(owner, f"TAG{i}") for i in range(20)]
    note = _make_note(owner, "Tagged note")
    for tag in tags:
        services.assign_tag_to_note(note=note, tag=tag)
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"),
        {"tag": [str(tag.id) for tag in tags], "tag_mode": "any"},
    )
    content = response.content.decode()

    assert "Tagged note" in content
    assert "full-search-validation" not in content


@pytest.mark.django_db
def test_full_search_view_more_than_20_tags_rejected_not_truncated():
    owner = create_account("fsv-21-tags-owner")
    # 21 legitimate, owned Tag IDs -- deliberately not attached to any
    # note (a single note can hold at most 20 Tags, an unrelated limit).
    # The rejection under test is purely about the *selection* count
    # submitted in the request, independent of what any note has.
    tags = [_make_tag(owner, f"TAG{i}") for i in range(21)]
    note = _make_note(owner, "Tagged note")
    services.assign_tag_to_note(note=note, tag=tags[0])
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"),
        {"tag": [str(tag.id) for tag in tags], "tag_mode": "any"},
    )
    content = _main_content(response)

    assert response.status_code == 200
    assert "Tagged note" not in content
    assert "full-search-validation" in response.content.decode()


@pytest.mark.django_db
def test_full_search_view_malformed_tag_ids_silently_ignored():
    owner = create_account("fsv-malformed-tag-owner")
    _make_note(owner, "Some note")
    client = authenticated_client(owner)

    # `include_trash=1` supplies a meaningful criterion so the search
    # actually runs; the malformed `tag` value under test must still be
    # silently discarded rather than erroring or excluding.
    response = client.get(
        reverse("notes:full_search"), {"tag": "not-a-number", "include_trash": "1"}
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "full-search-validation" not in content
    assert "Some note" in content


@pytest.mark.django_db
def test_full_search_view_foreign_tag_id_silently_ignored_no_disclosure():
    owner = create_account("fsv-foreign-tag-owner")
    other = create_account("fsv-foreign-tag-other")
    foreign_tag = _make_tag(other, "SECRET")
    _make_note(owner, "Owner note")
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"),
        {"tag": str(foreign_tag.id), "include_trash": "1"},
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "full-search-validation" not in content
    assert "SECRET" not in content
    assert "Owner note" in content


@pytest.mark.django_db
def test_full_search_view_specific_tags_with_untagged_status_rejected():
    owner = create_account("fsv-conflict-owner")
    tag = _make_tag(owner, "SERVER")
    note = _make_note(owner, "Tagged note")
    services.assign_tag_to_note(note=note, tag=tag)
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"), {"tag": str(tag.id), "tag_status": "untagged"}
    )
    content = _main_content(response)

    assert response.status_code == 200
    assert "Tagged note" not in content
    assert "full-search-validation" in response.content.decode()


@pytest.mark.django_db
def test_full_search_view_specific_tags_with_tagged_status_accepted():
    owner = create_account("fsv-redundant-owner")
    tag = _make_tag(owner, "SERVER")
    note = _make_note(owner, "Tagged note")
    services.assign_tag_to_note(note=note, tag=tag)
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"), {"tag": str(tag.id), "tag_status": "tagged"}
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "full-search-validation" not in content
    assert "Tagged note" in content


@pytest.mark.django_db
def test_full_search_view_invalid_folder_falls_back_to_global_no_disclosure():
    owner = create_account("fsv-invalid-folder-owner")
    _make_note(owner, "Owner note")
    client = authenticated_client(owner)

    # `include_trash=1` supplies a meaningful criterion so the search
    # actually runs; the invalid `folder` value under test must still
    # fall back to global scope, not silently narrow/hide results.
    response = client.get(
        reverse("notes:full_search"), {"folder": "999999", "include_trash": "1"}
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "Owner note" in content


@pytest.mark.django_db
def test_full_search_view_unfiled_folder_param():
    owner = create_account("fsv-unfiled-owner")
    folder = services.create_folder(owner=owner, name="Infrastructure")
    _make_note(owner, "In folder", folder=folder)
    _make_note(owner, "Unfiled note")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"folder": "unfiled"})
    content = _main_content(response)

    assert "Unfiled note" in content
    assert "In folder" not in content


@pytest.mark.django_db
def test_full_search_view_include_trash_garbage_value_treated_as_unchecked():
    owner = create_account("fsv-garbage-include-trash-owner")
    trashed = _make_note(owner, "Trashed note")
    _trash_note(trashed)
    client = authenticated_client(owner)

    # Any value other than the exact `"1"` is treated as unchecked, not
    # a validation error -- a closed two-value vocabulary, not a general
    # boolean parser. Paired with `q=` so the request stays meaningful
    # (an idle bare `include_trash=yes` would be indistinguishable from
    # a bare idle visit).
    response = client.get(
        reverse("notes:full_search"), {"q": "trashed", "include_trash": "yes"}
    )
    content = _main_content(response)

    assert response.status_code == 200
    assert "Trashed note" not in content


@pytest.mark.django_db
def test_full_search_view_invalid_search_in_falls_back_to_both_no_error():
    owner = create_account("fsv-invalid-search-in-owner")
    note = _make_note(owner, "Roadmap Review")
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"), {"q": "roadmap", "search_in": "not-a-real-scope"}
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "full-search-validation" not in content
    assert "Roadmap Review" in content
    assert str(note.id) in content


@pytest.mark.django_db
def test_full_search_view_search_in_default_both():
    owner = create_account("fsv-search-in-default-owner")
    body_match = _make_note(owner, "Body Only Note", body="mentions the roadmap in passing")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"q": "roadmap"})
    content = _main_content(response)

    assert body_match.title in content


@pytest.mark.django_db
def test_full_search_view_search_in_titles_only_excludes_body_only_match():
    owner = create_account("fsv-search-in-titles-owner")
    title_match = _make_note(owner, "Roadmap Review")
    body_only = _make_note(owner, "Untitled", body="mentions the roadmap in passing")
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"), {"q": "roadmap", "search_in": "title"}
    )
    content = _main_content(response)

    # The matched title is rendered with highlight segments, so the raw
    # "Roadmap Review" string is split around a `<mark>` -- its own
    # note-detail link is a reliable, format-independent presence check.
    assert f"/notes/{title_match.id}/" in content
    assert body_only.title not in content


@pytest.mark.django_db
def test_full_search_view_search_in_notes_only_excludes_title_only_match():
    owner = create_account("fsv-search-in-notes-owner")
    title_only = _make_note(owner, "Roadmap Review")
    body_match = _make_note(owner, "Untitled Note", body="mentions the roadmap in passing")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"q": "roadmap", "search_in": "body"})
    content = _main_content(response)

    assert body_match.title in content
    assert title_only.title not in content
    # The Note title is still shown as plain navigation/context even
    # though the title itself was never part of the search scope.
    assert "Untitled Note" in content


@pytest.mark.django_db
def test_full_search_view_deep_link_pre_scoped_to_one_tag():
    owner = create_account("fsv-deep-link-owner")
    tag = _make_tag(owner, "SERVER")
    matching = _make_note(owner, "Matching note")
    services.assign_tag_to_note(note=matching, tag=tag)
    _make_note(owner, "Other note")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"tag": str(tag.id)})
    content = _main_content(response)

    assert "Matching note" in content
    assert "Other note" not in content


@pytest.mark.django_db
def test_full_search_view_repeated_tag_params_round_trip():
    owner = create_account("fsv-repeated-tag-owner")
    tag_a = _make_tag(owner, "ALPHA")
    tag_b = _make_tag(owner, "BETA")
    note = _make_note(owner, "Both tags")
    services.assign_tag_to_note(note=note, tag=tag_a)
    services.assign_tag_to_note(note=note, tag=tag_b)
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"), {"tag": [str(tag_a.id), str(tag_b.id)], "tag_mode": "all"}
    )
    content = response.content.decode()

    assert "Both tags" in content


@pytest.mark.django_db
def test_full_search_view_include_trash_appears_as_active_filter_chip():
    owner = create_account("fsv-include-trash-chip-owner")
    trashed = _make_note(owner, "Trashed note")
    _trash_note(trashed)
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"include_trash": "1"})
    content = response.content.decode()

    assert "Include Trash" in content


@pytest.mark.django_db
def test_full_search_view_search_in_both_is_not_an_active_filter_chip():
    owner = create_account("fsv-search-in-both-chip-owner")
    _make_note(owner, "Roadmap")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"q": "roadmap", "search_in": "both"})
    content = response.content.decode()

    assert "Search in:" not in content


@pytest.mark.django_db
def test_full_search_view_search_in_titles_only_appears_as_active_filter_chip():
    owner = create_account("fsv-search-in-titles-chip-owner")
    _make_note(owner, "Roadmap")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"q": "roadmap", "search_in": "title"})
    content = response.content.decode()

    assert "Titles only" in content


@pytest.mark.django_db
def test_full_search_view_active_filter_chip_removal_updates_results():
    owner = create_account("fsv-chip-removal-owner")
    server_tag = _make_tag(owner, "SERVER")
    network_tag = _make_tag(owner, "NETWORK")
    both = _make_note(owner, "Both tags")
    services.assign_tag_to_note(note=both, tag=server_tag)
    services.assign_tag_to_note(note=both, tag=network_tag)
    server_only = _make_note(owner, "Server only")
    services.assign_tag_to_note(note=server_only, tag=server_tag)
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"),
        {"tag": [str(server_tag.id), str(network_tag.id)], "tag_mode": "all"},
    )
    assert "Server only" not in _main_content(response)

    # Simulate following the NETWORK chip's own remove_url: same route,
    # with only the SERVER tag remaining -- still an active (non-idle)
    # search, now broadened to Any-equivalent single-tag matching.
    response_after_removal = client.get(reverse("notes:full_search"), {"tag": str(server_tag.id)})
    content = _main_content(response_after_removal)

    assert "Both tags" in content
    assert "Server only" in content


@pytest.mark.django_db
def test_full_search_view_removing_the_only_active_filter_returns_to_idle():
    owner = create_account("fsv-chip-removal-idle-owner")
    tag = _make_tag(owner, "SERVER")
    tagged = _make_note(owner, "Tagged note")
    services.assign_tag_to_note(note=tagged, tag=tag)
    _make_note(owner, "Untagged note")
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"tag": str(tag.id)})
    assert "Untagged note" not in _main_content(response)

    # Following the chip's own remove_url when it was the *only* active
    # filter lands back on the bare route -- idle, not "show everything."
    response_after_removal = client.get(reverse("notes:full_search"), {})
    content = _main_content(response_after_removal)

    assert "Tagged note" not in content
    assert "Untagged note" not in content
    full_content = response_after_removal.content.decode()
    assert "Enter search text or choose one or more filters." in full_content


@pytest.mark.django_db
def test_full_search_view_clear_all_url_is_bare_route():
    owner = create_account("fsv-clear-all-owner")
    client = authenticated_client(owner)
    bare_url = reverse("notes:full_search")

    response = client.get(
        reverse("notes:full_search"),
        {"q": "roadmap", "tag_status": "tagged", "include_trash": "1"},
    )
    content = response.content.decode()

    assert f'href="{bare_url}">Clear all</a>' in content


@pytest.mark.django_db
def test_full_search_view_chip_remove_url_matches_state_without_that_filter():
    owner = create_account("fsv-chip-url-owner")
    tag = _make_tag(owner, "SERVER")
    note = _make_note(owner, "Tagged note")
    services.assign_tag_to_note(note=note, tag=tag)
    client = authenticated_client(owner)
    bare_url = reverse("notes:full_search")

    response = client.get(reverse("notes:full_search"), {"tag": str(tag.id)})
    content = response.content.decode()

    # The one active filter (this single Tag) removed leaves an empty
    # state -- its own chip's remove_url must be the bare route.
    assert f'href="{bare_url}"' in content


@pytest.mark.django_db
def test_full_search_view_pagination_50_per_page():
    owner = create_account("fsv-pagination-owner")
    for i in range(55):
        _make_note(owner, f"Note {i:03d}")
    client = authenticated_client(owner)

    # `include_trash=1` is a meaningful (non-default) criterion that
    # activates the search without narrowing this owner's 55 all-active
    # notes (there is no Trash content to add or exclude).
    first_page = client.get(reverse("notes:full_search"), {"include_trash": "1"})
    second_page = client.get(
        reverse("notes:full_search"), {"include_trash": "1", "page": "2"}
    )

    assert first_page.status_code == 200
    assert second_page.status_code == 200
    assert b"Page 1 of 2" in first_page.content
    assert b"Page 2 of 2" in second_page.content


@pytest.mark.django_db
def test_full_search_view_pagination_preserves_non_default_search_in():
    owner = create_account("fsv-pagination-search-in-owner")
    for i in range(55):
        _make_note(owner, f"Roadmap Note {i:03d}")
    client = authenticated_client(owner)

    first_page = client.get(
        reverse("notes:full_search"), {"q": "roadmap", "search_in": "title"}
    )
    content = first_page.content.decode()

    assert first_page.status_code == 200
    assert b"Page 1 of 2" in first_page.content
    assert "search_in=title" in content


@pytest.mark.django_db
def test_full_search_view_stale_page_number_clamps_to_last_page():
    owner = create_account("fsv-stale-page-owner")
    _make_note(owner, "Only note")
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"), {"include_trash": "1", "page": "999"}
    )

    assert response.status_code == 200
    assert b"Only note" in response.content


@pytest.mark.django_db
def test_full_search_view_malformed_page_number_falls_back_to_page_one():
    owner = create_account("fsv-malformed-page-owner")
    _make_note(owner, "Only note")
    client = authenticated_client(owner)

    response = client.get(
        reverse("notes:full_search"), {"include_trash": "1", "page": "not-a-number"}
    )

    assert response.status_code == 200
    assert b"Only note" in response.content


@pytest.mark.django_db
def test_full_search_view_owner_isolation_across_tags_folders_notes():
    owner = create_account("fsv-owner-iso-owner")
    other = create_account("fsv-owner-iso-other")
    other_folder = services.create_folder(owner=other, name="Secret Folder")
    other_note = _make_note(other, "Secret Note", folder=other_folder)
    other_tag = _make_tag(other, "SECRETTAG")
    services.assign_tag_to_note(note=other_note, tag=other_tag)
    client = authenticated_client(owner)

    response = client.get(reverse("notes:full_search"), {"include_trash": "1"})
    content = response.content.decode()

    assert "Secret Note" not in content
    assert "Secret Folder" not in content
    assert "SECRETTAG" not in content
