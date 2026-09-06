import json

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services

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


def _segment_text(segments):
    return "".join(segment["text"] for segment in segments)


def _highlighted_text(segments):
    return "".join(segment["text"] for segment in segments if segment["highlight"])


# -- search_notes_global (service): matching, ranking, scoping ---------------


@pytest.mark.django_db
def test_search_notes_global_matches_title():
    owner = create_account("gs-title-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Quarterly Roadmap Review")

    results = services.search_notes_global(owner=owner, query="roadmap")

    assert note in results


@pytest.mark.django_db
def test_search_notes_global_matches_body_text():
    owner = create_account("gs-body-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Untitled Thoughts")
    _set_body_plain_text(note, "a long paragraph mentioning giraffes and other animals")

    results = services.search_notes_global(owner=owner, query="giraffe")

    assert note in results


@pytest.mark.django_db
def test_search_notes_global_stemmed_term_matches_inflected_form():
    owner = create_account("gs-stem-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Release Notes")
    _set_body_plain_text(note, "we completed the database migration last night without incident")

    results = services.search_notes_global(owner=owner, query="migrate")

    assert note in results


@pytest.mark.django_db
def test_search_notes_global_ranks_title_matches_above_body_matches():
    owner = create_account("gs-rank-owner")
    title_match = services.create_note(owner=owner)
    services.rename_note(note=title_match, title="Wombat Sighting Log")
    _set_body_plain_text(title_match, "unrelated body content here")

    body_match = services.create_note(owner=owner)
    services.rename_note(note=body_match, title="Unrelated Title")
    _set_body_plain_text(body_match, "a note that just happens to mention a wombat once")

    results = services.search_notes_global(owner=owner, query="wombat")

    assert results.index(title_match) < results.index(body_match)


@pytest.mark.django_db
def test_search_notes_global_stable_tie_break_is_recency_then_id():
    owner = create_account("gs-tiebreak-owner")
    older = services.create_note(owner=owner)
    services.rename_note(note=older, title="Duplicate Term Alpha")
    newer = services.create_note(owner=owner)
    services.rename_note(note=newer, title="Duplicate Term Beta")

    results = services.search_notes_global(owner=owner, query="duplicate term")

    # Both notes rank identically on relevance (same title-only match shape);
    # the newer note (higher id, later modified_at) must sort first.
    assert results.index(newer) < results.index(older)


@pytest.mark.django_db
def test_search_notes_global_body_only_match_appears():
    owner = create_account("gs-body-only-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Completely Unrelated Title")
    _set_body_plain_text(note, "this paragraph is about narwhals specifically")

    results = services.search_notes_global(owner=owner, query="narwhal")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_does_not_include_other_owners_notes():
    owner = create_account("gs-scope-owner")
    other = create_account("gs-scope-other")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Shared Keyword Note")

    results = services.search_notes_global(owner=owner, query="shared")

    assert other_note not in results


@pytest.mark.django_db
def test_search_notes_global_excludes_trashed_notes():
    owner = create_account("gs-trash-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Trashed Keyword Note")
    services.move_note_to_trash(note=note)

    results = services.search_notes_global(owner=owner, query="trashed")

    assert results == []


@pytest.mark.django_db
def test_search_notes_global_caps_at_ten():
    owner = create_account("gs-cap-owner")
    for index in range(12):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Matching Note {index}")

    results = services.search_notes_global(owner=owner, query="matching")

    assert len(results) == 10


@pytest.mark.django_db
def test_search_notes_global_returns_empty_list_for_blank_query():
    owner = create_account("gs-blank-owner")
    services.create_note(owner=owner)

    results = services.search_notes_global(owner=owner, query="   ")

    assert results == []


@pytest.mark.django_db
def test_search_notes_global_returns_empty_list_for_empty_query():
    owner = create_account("gs-empty-owner")
    services.create_note(owner=owner)

    assert services.search_notes_global(owner=owner, query="") == []


@pytest.mark.django_db
def test_search_notes_global_no_match_returns_empty_list():
    owner = create_account("gs-nomatch-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Ordinary Note")

    results = services.search_notes_global(owner=owner, query="zzznonexistentqueryterm")

    assert results == []


# -- multi-word AND and quoted-phrase semantics -------------------------------


@pytest.mark.django_db
def test_search_notes_global_two_word_query_requires_both_terms():
    owner = create_account("gs-and-owner")
    both = services.create_note(owner=owner)
    services.rename_note(note=both, title="Server Maintenance Window")
    only_one = services.create_note(owner=owner)
    services.rename_note(note=only_one, title="Server Room Access")

    results = services.search_notes_global(owner=owner, query="server maintenance")

    assert results == [both]


@pytest.mark.django_db
def test_search_notes_global_one_term_only_does_not_match_two_word_query():
    owner = create_account("gs-and-partial-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Server Room Access")

    results = services.search_notes_global(owner=owner, query="server maintenance")

    assert results == []


@pytest.mark.django_db
def test_search_notes_global_quoted_phrase_matches_adjacent_terms():
    owner = create_account("gs-phrase-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Notes")
    _set_body_plain_text(note, "please review the server maintenance schedule this week")

    results = services.search_notes_global(owner=owner, query='"server maintenance"')

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_quoted_phrase_does_not_match_scattered_terms():
    owner = create_account("gs-phrase-nonmatch-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Notes")
    _set_body_plain_text(note, "the maintenance team will reboot the server next week")

    results = services.search_notes_global(owner=owner, query='"server maintenance"')

    assert results == []


@pytest.mark.django_db
def test_search_notes_global_unclosed_quote_does_not_error():
    owner = create_account("gs-malformed-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Notes")
    _set_body_plain_text(note, "please review the server maintenance schedule this week")

    results = services.search_notes_global(owner=owner, query='"server maintenance')

    assert results == [note]


# -- ineffective/degenerate query handling ------------------------------------


@pytest.mark.django_db
def test_search_notes_global_stop_word_only_query_returns_no_results():
    owner = create_account("gs-stopword-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="The And To For")

    results = services.search_notes_global(owner=owner, query="the and to")

    assert results == []


@pytest.mark.django_db
def test_search_notes_global_punctuation_only_query_returns_no_results():
    owner = create_account("gs-punct-owner")
    services.create_note(owner=owner)

    results = services.search_notes_global(owner=owner, query="...")

    assert results == []


@pytest.mark.django_db
def test_search_notes_global_overlong_query_returns_no_results_without_error():
    owner = create_account("gs-overlong-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Roadmap")

    overlong_query = "roadmap " + ("x" * services.GLOBAL_SEARCH_MAX_QUERY_LENGTH)
    results = services.search_notes_global(owner=owner, query=overlong_query)

    assert results == []


@pytest.mark.django_db
def test_search_notes_global_query_at_the_length_boundary_still_executes():
    owner = create_account("gs-boundary-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Roadmap")

    boundary_query = "roadmap".ljust(services.GLOBAL_SEARCH_MAX_QUERY_LENGTH, "x")
    # Not a realistic query, but must not raise -- length is the only bound
    # enforced here; a nonsense long string simply matches nothing.
    services.search_notes_global(owner=owner, query=boundary_query)


# -- technical-token matrix (empirically verified against real PostgreSQL) ---


@pytest.mark.django_db
def test_search_notes_global_matches_ip_address():
    owner = create_account("gs-ip-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Server reference")
    _set_body_plain_text(note, "reachable at 192.0.2.10 on the LAN")
    other = services.create_note(owner=owner)
    services.rename_note(note=other, title="Unrelated")
    _set_body_plain_text(other, "reachable at 10.0.0.1 elsewhere")

    results = services.search_notes_global(owner=owner, query="192.0.2.10")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_matches_hyphenated_hostname():
    owner = create_account("gs-hostname-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Deployment host")
    _set_body_plain_text(note, "the deployment target is deployment-test-host on the LAN")

    results = services.search_notes_global(owner=owner, query="deployment-test-host")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_matches_domain():
    owner = create_account("gs-domain-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Registry reference")
    _set_body_plain_text(note, "pulled from registry.example.com without issue")

    results = services.search_notes_global(owner=owner, query="registry.example.com")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_matches_image_tag():
    owner = create_account("gs-imagetag-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Image reference")
    _set_body_plain_text(note, "running ridgenote:v1.0.0 in production now")

    results = services.search_notes_global(owner=owner, query="ridgenote:v1.0.0")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_matches_file_name():
    owner = create_account("gs-filename-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Compose reference")
    _set_body_plain_text(note, "edit docker-compose.yml before restarting the stack")

    results = services.search_notes_global(owner=owner, query="docker-compose.yml")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_matches_path():
    owner = create_account("gs-path-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Deploy path")
    _set_body_plain_text(note, "the stack lives at /opt/ridgenote today")

    results = services.search_notes_global(owner=owner, query="/opt/ridgenote")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_matches_email_like_token():
    owner = create_account("gs-email-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Contact reference")
    _set_body_plain_text(note, "notify user@example.com when this completes")

    results = services.search_notes_global(owner=owner, query="user@example.com")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_matches_possessive_apostrophe_word():
    owner = create_account("gs-apostrophe-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Ownership note")
    _set_body_plain_text(note, "this is the user's own note about preferences")

    results = services.search_notes_global(owner=owner, query="user's notes")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_matches_slash_separated_text():
    owner = create_account("gs-slash-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Test layout")
    _set_body_plain_text(note, "coverage lives under notes/tests in this repository")

    results = services.search_notes_global(owner=owner, query="notes/tests")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_global_matches_dotted_identifier():
    owner = create_account("gs-dotted-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Module reference")
    _set_body_plain_text(note, "the search logic lives in notes.services now")

    results = services.search_notes_global(owner=owner, query="notes.services")

    assert results == [note]


# -- Technical-token limitations --
#
# These three behaviors are bounded PostgreSQL full-text-search limitations,
# not defects: there is no hybrid substring fallback and no prominent
# user-facing warning for any of them. Pinned here so a future
# PostgreSQL/Django upgrade that silently changes this behavior is caught.


@pytest.mark.django_db
def test_search_notes_global_partial_ip_prefix_does_not_match():
    # Approved limitation 1: full-text search matches whole lexemes only.
    # PostgreSQL tokenizes a complete IP as one atomic "host" lexeme
    # (`192.0.2.10`) and an incomplete trailing octet as a *different*
    # atomic lexeme (`192.0.2`) -- never a prefix relationship.
    owner = create_account("gs-partial-ip-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Server reference")
    _set_body_plain_text(note, "reachable at 192.0.2.10 on the LAN")

    results = services.search_notes_global(owner=owner, query="192.0.2.")

    assert results == []


@pytest.mark.django_db
def test_search_notes_global_tag_embedded_in_slash_path_may_not_self_match():
    # Approved limitation 2: a punctuation-heavy compound (registry/org/
    # image:tag) embedded in a longer slash path is absorbed into large
    # compound URL tokens by `to_tsvector` at index time, while
    # `websearch_to_tsquery` tokenizes the *same* string differently at
    # query time -- even an exact full-string search of such a compound is
    # not guaranteed to self-match. Searching a useful standalone component
    # instead (see the image-tag/hostname/filename tests above) is the
    # supported path; no hybrid substring fallback is authorized.
    owner = create_account("gs-embedded-path-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Pull command")
    _set_body_plain_text(
        note, "pulled registry.example.com/example/ridgenote:v1.0.0 without issue"
    )

    results = services.search_notes_global(
        owner=owner, query="registry.example.com/example/ridgenote:v1.0.0"
    )

    assert results == []


@pytest.mark.django_db
def test_search_notes_global_dont_contraction_may_have_no_searchable_lexemes():
    # Approved limitation 3: PostgreSQL's English dictionary reduces the
    # contraction "don't" to zero lexemes (both "don" and "t" are filtered),
    # unlike an ordinary possessive such as "user's" (which keeps "user").
    # This is a silent non-match, never an error and never a false broad
    # match -- confirmed safe and accepted as-is.
    owner = create_account("gs-dont-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Reminder")
    _set_body_plain_text(note, "don't forget to rotate the credentials next week")

    results = services.search_notes_global(owner=owner, query="don't")

    assert results == []


# -- build_segments_from_headline (safety + shared parsing) ------------------


def test_build_segments_from_headline_splits_and_marks_the_match():
    headline = "the \x01quick\x02 brown fox"

    segments = services.build_segments_from_headline(headline)

    assert segments == [
        {"text": "the ", "highlight": False},
        {"text": "quick", "highlight": True},
        {"text": " brown fox", "highlight": False},
    ]


def test_build_segments_from_headline_returns_plain_text_when_no_sentinel():
    segments = services.build_segments_from_headline("no markers here")

    assert segments == [{"text": "no markers here", "highlight": False}]


def test_build_segments_from_headline_handles_multiple_matches():
    headline = "\x01docker\x02-\x01test\x02-\x01box1\x02"

    segments = services.build_segments_from_headline(headline)

    assert segments == [
        {"text": "docker", "highlight": True},
        {"text": "-", "highlight": False},
        {"text": "test", "highlight": True},
        {"text": "-", "highlight": False},
        {"text": "box1", "highlight": True},
    ]


def test_build_segments_from_headline_never_embeds_sentinel_bytes():
    headline = "the \x01quick\x02 brown fox"

    segments = services.build_segments_from_headline(headline)

    combined = "".join(segment["text"] for segment in segments)
    assert "\x01" not in combined
    assert "\x02" not in combined


def test_build_segments_from_headline_never_embeds_html_markup():
    # PostgreSQL's own ts_headline output never contains HTML here (custom
    # sentinel delimiters, not <mark> tags) -- confirm hostile note content
    # survives unescaped-but-inert as plain segment text.
    headline = "<script>alert(1)</script> \x01quick\x02 fox"

    segments = services.build_segments_from_headline(headline)

    combined = "".join(segment["text"] for segment in segments)
    assert combined == "<script>alert(1)</script> quick fox"
    assert all("<mark" not in segment["text"] for segment in segments)


# -- title/body highlighting, produced by search_notes_global itself ---------


@pytest.mark.django_db
def test_search_notes_global_title_headline_highlights_stemmed_match():
    owner = create_account("gs-title-highlight-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Database Migration Notes")

    results = services.search_notes_global(owner=owner, query="migrate")
    segments = services.build_segments_from_headline(results[0].title_headline)

    assert _highlighted_text(segments).lower() == "migration"
    assert _segment_text(segments) == "Database Migration Notes"


@pytest.mark.django_db
def test_search_notes_global_body_headline_highlights_stemmed_match():
    owner = create_account("gs-body-highlight-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Release Notes")
    _set_body_plain_text(note, "we completed the database migration last night without incident")

    results = services.search_notes_global(owner=owner, query="migrate")
    segments = services.build_segments_from_headline(results[0].body_headline)

    assert _highlighted_text(segments).lower() == "migration"


@pytest.mark.django_db
def test_search_notes_global_title_only_match_has_no_misleading_body_highlight():
    owner = create_account("gs-title-only-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Migration Notes")
    _set_body_plain_text(note, "nothing in this paragraph relates to the query at all")

    results = services.search_notes_global(owner=owner, query="migrate")
    title_segments = services.build_segments_from_headline(results[0].title_headline)
    body_segments = services.build_segments_from_headline(results[0].body_headline)

    assert any(segment["highlight"] for segment in title_segments)
    assert not any(segment["highlight"] for segment in body_segments)


# -- global_search view --------------------------------------------------------


@pytest.mark.django_db
def test_global_search_returns_matching_note_json():
    owner = create_account("gs-view-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Launch Plan")

    response = authenticated_client(owner).get(reverse("notes:global_search"), {"q": "launch"})

    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["ok"] is True
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["id"] == note.id
    assert _segment_text(result["title_segments"]) == "Launch Plan"
    assert result["folder_label"] == "Unfiled"
    assert result["url"] == reverse("notes:detail", args=[note.id])
    assert "body_segments" in result


@pytest.mark.django_db
def test_global_search_uses_folder_name_as_label():
    owner = create_account("gs-view-folder-owner")
    folder = services.create_folder(owner=owner, name="Research")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.rename_note(note=note, title="Research Findings")

    response = authenticated_client(owner).get(reverse("notes:global_search"), {"q": "research"})

    payload = json.loads(response.content)
    assert payload["results"][0]["folder_label"] == "Research"


@pytest.mark.django_db
def test_global_search_unfiled_note_uses_unfiled_label():
    owner = create_account("gs-view-unfiled-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Loose Findings")

    response = authenticated_client(owner).get(reverse("notes:global_search"), {"q": "loose"})

    payload = json.loads(response.content)
    assert payload["results"][0]["folder_label"] == "Unfiled"


@pytest.mark.django_db
def test_global_search_body_segments_highlight_body_match():
    owner = create_account("gs-view-excerpt-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Field Notes")
    _set_body_plain_text(note, "spotted a rare pangolin during the trip")

    response = authenticated_client(owner).get(reverse("notes:global_search"), {"q": "pangolin"})

    payload = json.loads(response.content)
    segments = payload["results"][0]["body_segments"]
    assert any(
        segment["highlight"] and segment["text"].lower() == "pangolin" for segment in segments
    )


@pytest.mark.django_db
def test_global_search_title_segments_highlight_title_match():
    owner = create_account("gs-view-title-highlight-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Pangolin Sighting")

    response = authenticated_client(owner).get(reverse("notes:global_search"), {"q": "pangolin"})

    payload = json.loads(response.content)
    segments = payload["results"][0]["title_segments"]
    assert any(
        segment["highlight"] and segment["text"].lower() == "pangolin" for segment in segments
    )


@pytest.mark.django_db
def test_global_search_response_never_contains_raw_highlight_markup():
    owner = create_account("gs-view-raw-markup-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Pangolin Sighting")
    _set_body_plain_text(note, "spotted a rare pangolin during the trip")

    response = authenticated_client(owner).get(reverse("notes:global_search"), {"q": "pangolin"})

    assert b"\x01" not in response.content
    assert b"\x02" not in response.content
    assert b"<mark" not in response.content


@pytest.mark.django_db
def test_global_search_hostile_note_content_survives_as_literal_text():
    # This endpoint returns JSON, never HTML -- it cannot itself execute
    # anything. What matters here is that hostile content is preserved
    # exactly (never partially stripped/mangled by headline parsing) and
    # that the segment structure contains no HTML markup of its own; the
    # frontend renderer (global-search.test.ts) separately proves this exact
    # text becomes an inert DOM text node, never executable markup.
    owner = create_account("gs-view-hostile-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Pangolin <script>alert(1)</script> Sighting")

    response = authenticated_client(owner).get(reverse("notes:global_search"), {"q": "pangolin"})

    payload = json.loads(response.content)
    combined_title = _segment_text(payload["results"][0]["title_segments"])
    assert combined_title == "Pangolin <script>alert(1)</script> Sighting"
    assert all(
        "<mark" not in segment["text"] for segment in payload["results"][0]["title_segments"]
    )


@pytest.mark.django_db
def test_global_search_does_not_leak_cross_owner_notes():
    owner = create_account("gs-view-scope-owner")
    other = create_account("gs-view-scope-other")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Confidential Keyword")

    response = authenticated_client(owner).get(
        reverse("notes:global_search"), {"q": "confidential"}
    )

    payload = json.loads(response.content)
    assert payload["results"] == []


@pytest.mark.django_db
def test_global_search_result_limit_is_ten():
    owner = create_account("gs-view-cap-owner")
    for index in range(12):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Capped Result {index}")

    response = authenticated_client(owner).get(reverse("notes:global_search"), {"q": "capped"})

    payload = json.loads(response.content)
    assert len(payload["results"]) == 10


@pytest.mark.django_db
def test_global_search_empty_query_returns_empty_results():
    owner = create_account("gs-view-blank-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:global_search"))

    payload = json.loads(response.content)
    assert payload == {"ok": True, "results": []}


@pytest.mark.django_db
def test_global_search_no_match_returns_empty_results():
    owner = create_account("gs-view-nomatch-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Ordinary Note")

    response = authenticated_client(owner).get(
        reverse("notes:global_search"), {"q": "zzznonexistentqueryterm"}
    )

    payload = json.loads(response.content)
    assert payload["results"] == []


@pytest.mark.django_db
def test_global_search_stop_word_only_query_returns_empty_results():
    owner = create_account("gs-view-stopword-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:global_search"), {"q": "the and to"})

    payload = json.loads(response.content)
    assert payload == {"ok": True, "results": []}


@pytest.mark.django_db
def test_global_search_requires_authentication():
    response = Client().get(reverse("notes:global_search"), {"q": "anything"})

    assert response.status_code == 302
