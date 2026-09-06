import json

import pytest
from accounts import services as account_services
from accounts.models import User
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from notes import services
from notes.models import Tag

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


# -- search_tags_for_owner() (service) -------------------------------------


@pytest.mark.django_db
def test_search_tags_for_owner_blank_query_returns_nothing():
    owner = create_account("suggest-blank-owner")
    services.get_or_create_tag(owner=owner, name="RED", color="red")

    assert services.search_tags_for_owner(owner=owner, query="") == []
    assert services.search_tags_for_owner(owner=owner, query="   ") == []


@pytest.mark.django_db
def test_search_tags_for_owner_exact_match_ranks_first():
    owner = create_account("suggest-rank-owner")
    prefix_tag = services.get_or_create_tag(owner=owner, name="REDDIT", color="slate")
    exact_tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    substring_tag = services.get_or_create_tag(owner=owner, name="BORED", color="slate")

    results = services.search_tags_for_owner(owner=owner, query="RED")

    assert results[0] == exact_tag
    assert results[1] == prefix_tag
    assert results[2] == substring_tag


@pytest.mark.django_db
def test_search_tags_for_owner_alphabetical_within_rank_group():
    owner = create_account("suggest-alpha-owner")
    services.get_or_create_tag(owner=owner, name="REDWOOD", color="slate")
    services.get_or_create_tag(owner=owner, name="REDACTED", color="slate")

    results = services.search_tags_for_owner(owner=owner, query="RED")

    assert [tag.name for tag in results] == ["REDACTED", "REDWOOD"]


@pytest.mark.django_db
def test_search_tags_for_owner_case_insensitive():
    owner = create_account("suggest-ci-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    assert services.search_tags_for_owner(owner=owner, query="red") == [tag]
    assert services.search_tags_for_owner(owner=owner, query="rEd") == [tag]


@pytest.mark.django_db
def test_search_tags_for_owner_never_returns_another_owners_tags():
    owner = create_account("suggest-scope-owner")
    other = create_account("suggest-scope-other")
    services.get_or_create_tag(owner=other, name="RED", color="red")

    assert services.search_tags_for_owner(owner=owner, query="RED") == []


@pytest.mark.django_db
def test_search_tags_for_owner_excludes_ids():
    owner = create_account("suggest-exclude-owner")
    keep = services.get_or_create_tag(owner=owner, name="REDWOOD", color="slate")
    excluded = services.get_or_create_tag(owner=owner, name="RED", color="red")

    results = services.search_tags_for_owner(owner=owner, query="RED", exclude_ids=[excluded.pk])

    assert results == [keep]


@pytest.mark.django_db
def test_search_tags_for_owner_respects_result_cap():
    owner = create_account("suggest-cap-owner")
    for i in range(12):
        services.get_or_create_tag(owner=owner, name=f"RED{i:02d}", color="slate")

    results = services.search_tags_for_owner(owner=owner, query="RED", limit=8)

    assert len(results) == 8


# -- note_tag_suggestions view -----------------------------------------------
#
# The suggestion
# panel/endpoint is existing-tag-only -- there is no create-candidate
# preview, no `color` query parameter, and no `create` response field.
# Normal new-tag creation is entirely unaffected: it is the
# ordinary `note_tag_assign()` POST flow, never this endpoint.


@pytest.mark.django_db
def test_note_tag_suggestions_requires_login():
    owner = create_account("suggest-login-owner")
    note = services.create_note(owner=owner)

    response = Client().get(reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "red"})

    assert response.status_code in (302, 401, 403)


@pytest.mark.django_db
def test_note_tag_suggestions_404s_for_another_owners_note():
    owner = create_account("suggest-owner-a")
    other = create_account("suggest-owner-b")
    other_note = services.create_note(owner=other)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[other_note.id]), {"q": "red"}
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_note_tag_suggestions_never_leaks_another_owners_tags():
    owner = create_account("suggest-leak-owner")
    other = create_account("suggest-leak-other")
    services.get_or_create_tag(owner=other, name="SHARED", color="violet")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "shared"}
    )

    payload = json.loads(response.content)
    assert payload["existing"] == []


@pytest.mark.django_db
def test_note_tag_suggestions_blank_query_returns_empty():
    owner = create_account("suggest-view-blank-owner")
    services.get_or_create_tag(owner=owner, name="RED", color="red")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": ""}
    )

    payload = json.loads(response.content)
    assert payload["ok"] is True
    assert payload["existing"] == []
    assert "create" not in payload


@pytest.mark.django_db
def test_note_tag_suggestions_no_match_returns_empty_existing_no_create_metadata():
    owner = create_account("suggest-view-nomatch-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "greenbar"}
    )

    payload = json.loads(response.content)
    assert payload["existing"] == []
    assert "create" not in payload


@pytest.mark.django_db
def test_note_tag_suggestions_exact_match():
    owner = create_account("suggest-view-exact-owner")
    services.get_or_create_tag(owner=owner, name="GREEN", color="green")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "GREEN"}
    )

    payload = json.loads(response.content)
    assert [row["name"] for row in payload["existing"]] == ["GREEN"]


@pytest.mark.django_db
def test_note_tag_suggestions_prefix_match():
    owner = create_account("suggest-view-prefix-owner")
    services.get_or_create_tag(owner=owner, name="GREENLIGHT", color="green")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "GRE"}
    )

    payload = json.loads(response.content)
    assert [row["name"] for row in payload["existing"]] == ["GREENLIGHT"]


@pytest.mark.django_db
def test_note_tag_suggestions_substring_match():
    owner = create_account("suggest-view-substring-owner")
    services.get_or_create_tag(owner=owner, name="EVERGREEN", color="green")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "GREEN"}
    )

    payload = json.loads(response.content)
    assert [row["name"] for row in payload["existing"]] == ["EVERGREEN"]


@pytest.mark.django_db
def test_note_tag_suggestions_returns_stored_color_not_semantic_implied_color():
    owner = create_account("suggest-view-color-owner")
    services.get_or_create_tag(owner=owner, name="RED", color="blue")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "red"}
    )

    payload = json.loads(response.content)
    assert payload["existing"][0]["name"] == "RED"
    assert payload["existing"][0]["color"] == "blue"


@pytest.mark.django_db
def test_note_tag_suggestions_excludes_already_attached_tags():
    owner = create_account("suggest-view-attached-owner")
    note = services.create_note(owner=owner)
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "red"}
    )

    payload = json.loads(response.content)
    assert payload["existing"] == []


@pytest.mark.django_db
def test_note_tag_suggestions_color_query_param_is_ignored_if_sent():
    # The endpoint does not read or
    # need a `color` parameter -- confirmed harmless if a stale
    # client still sends one.
    owner = create_account("suggest-view-ignored-color-owner")
    services.get_or_create_tag(owner=owner, name="RED", color="blue")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "red", "color": "violet"}
    )

    payload = json.loads(response.content)
    assert payload["existing"][0]["color"] == "blue"
    assert "create" not in payload


@pytest.mark.django_db
def test_note_tag_suggestions_at_tag_limit_omits_all_suggestions():
    owner = create_account("suggest-view-limit-owner")
    note = services.create_note(owner=owner)
    for i in range(20):
        tag = services.get_or_create_tag(owner=owner, name=f"TAG{i:02d}", color="slate")
        services.assign_tag_to_note(note=note, tag=tag)
    services.get_or_create_tag(owner=owner, name="NEWMATCH", color="slate")

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "new"}
    )

    payload = json.loads(response.content)
    assert payload["at_tag_limit"] is True
    assert payload["existing"] == []


@pytest.mark.django_db
def test_note_tag_suggestions_not_at_tag_limit_below_20():
    owner = create_account("suggest-view-below-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "new"}
    )

    payload = json.loads(response.content)
    assert payload["at_tag_limit"] is False


@pytest.mark.django_db
def test_note_tag_suggestions_ranking_over_http():
    owner = create_account("suggest-view-rank-owner")
    services.get_or_create_tag(owner=owner, name="REDDIT", color="slate")
    services.get_or_create_tag(owner=owner, name="RED", color="red")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "red"}
    )

    payload = json.loads(response.content)
    names = [row["name"] for row in payload["existing"]]
    assert names == ["RED", "REDDIT"]


@pytest.mark.django_db
def test_note_tag_suggestions_endpoint_is_read_only_causes_no_mutation():
    owner = create_account("suggest-view-readonly-owner")
    note = services.create_note(owner=owner)
    tags_before = set(Tag.objects.filter(owner=owner).values_list("pk", flat=True))
    note_tags_before = list(note.tags.values_list("pk", flat=True))

    authenticated_client(owner).get(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "brand-new-candidate"}
    )

    tags_after = set(Tag.objects.filter(owner=owner).values_list("pk", flat=True))
    note.refresh_from_db()
    assert tags_after == tags_before
    assert list(note.tags.values_list("pk", flat=True)) == note_tags_before


@pytest.mark.django_db
def test_note_tag_suggestions_rejects_post():
    owner = create_account("suggest-view-post-owner")
    note = services.create_note(owner=owner)

    response = authenticated_client(owner).post(
        reverse("notes:note_tag_suggestions", args=[note.id]), {"q": "red"}
    )

    assert response.status_code == 405
