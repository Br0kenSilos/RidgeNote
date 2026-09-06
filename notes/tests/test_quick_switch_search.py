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


# -- search_notes_for_quick_switch (service) ---------------------------------


@pytest.mark.django_db
def test_search_notes_for_quick_switch_matches_title():
    owner = create_account("qs-title-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Weekly Sync Notes")
    services.create_note(owner=owner)

    results = services.search_notes_for_quick_switch(owner=owner, query="weekly")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_for_quick_switch_matches_folder_name():
    owner = create_account("qs-folder-owner")
    folder = services.create_folder(owner=owner, name="Project Alpha")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.create_note(owner=owner)

    results = services.search_notes_for_quick_switch(owner=owner, query="alpha")

    assert results == [note]


@pytest.mark.django_db
def test_search_notes_for_quick_switch_matches_unfiled_label():
    owner = create_account("qs-unfiled-owner")
    unfiled_note = services.create_note(owner=owner)
    folder = services.create_folder(owner=owner, name="Somewhere")
    filed_note = services.create_note(owner=owner)
    services.assign_note_folder(note=filed_note, folder=folder)

    results = services.search_notes_for_quick_switch(owner=owner, query="unfiled")

    assert unfiled_note in results
    assert filed_note not in results


@pytest.mark.django_db
def test_search_notes_for_quick_switch_never_matches_body_text():
    owner = create_account("qs-body-owner")
    note = services.create_note(owner=owner)
    note.body_plain_text = "this note is all about wombats"
    note.save(update_fields=["body_plain_text"])

    results = services.search_notes_for_quick_switch(owner=owner, query="wombat")

    assert results == []


@pytest.mark.django_db
def test_search_notes_for_quick_switch_excludes_given_note():
    owner = create_account("qs-exclude-owner")
    current = services.create_note(owner=owner)
    services.rename_note(note=current, title="Duplicate Title")
    other = services.create_note(owner=owner)
    services.rename_note(note=other, title="Duplicate Title Two")

    results = services.search_notes_for_quick_switch(
        owner=owner, query="duplicate", exclude_note_id=current.id
    )

    assert current not in results
    assert other in results


@pytest.mark.django_db
def test_search_notes_for_quick_switch_does_not_include_other_owners_notes():
    owner = create_account("qs-scope-owner")
    other = create_account("qs-scope-other")
    services.rename_note(note=services.create_note(owner=owner), title="Shared Word")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Shared Word Two")

    results = services.search_notes_for_quick_switch(owner=owner, query="shared")

    assert other_note not in results


@pytest.mark.django_db
def test_search_notes_for_quick_switch_caps_at_eight():
    owner = create_account("qs-cap-owner")
    for index in range(10):
        note = services.create_note(owner=owner)
        services.rename_note(note=note, title=f"Matching Note {index}")

    results = services.search_notes_for_quick_switch(owner=owner, query="matching")

    assert len(results) == 8


@pytest.mark.django_db
def test_search_notes_for_quick_switch_returns_empty_list_for_blank_query():
    owner = create_account("qs-blank-owner")
    services.create_note(owner=owner)

    results = services.search_notes_for_quick_switch(owner=owner, query="   ")

    assert results == []


# -- quick_switch_search view -------------------------------------------------


@pytest.mark.django_db
def test_quick_switch_search_returns_matching_note_json():
    owner = create_account("qs-view-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Roadmap Review")

    response = authenticated_client(owner).get(
        reverse("notes:quick_switch_search"), {"q": "roadmap"}
    )

    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["ok"] is True
    assert len(payload["results"]) == 1
    result = payload["results"][0]
    assert result["id"] == note.id
    assert result["title"] == "Roadmap Review"
    assert result["folder_label"] == "Unfiled"
    assert result["url"] == reverse("notes:detail", args=[note.id])


@pytest.mark.django_db
def test_quick_switch_search_uses_folder_name_as_label():
    owner = create_account("qs-view-folder-owner")
    folder = services.create_folder(owner=owner, name="Personal")
    note = services.create_note(owner=owner)
    services.assign_note_folder(note=note, folder=folder)
    services.rename_note(note=note, title="Personal Note")

    response = authenticated_client(owner).get(
        reverse("notes:quick_switch_search"), {"q": "personal"}
    )

    payload = json.loads(response.content)
    result = payload["results"][0]
    assert result["folder_label"] == "Personal"


@pytest.mark.django_db
def test_quick_switch_search_excludes_current_note_when_requested():
    owner = create_account("qs-view-exclude-owner")
    current = services.create_note(owner=owner)
    services.rename_note(note=current, title="Same Title")
    other = services.create_note(owner=owner)
    services.rename_note(note=other, title="Same Title Two")

    response = authenticated_client(owner).get(
        reverse("notes:quick_switch_search"),
        {"q": "same", "exclude": str(current.id)},
    )

    payload = json.loads(response.content)
    result_ids = [result["id"] for result in payload["results"]]
    assert current.id not in result_ids
    assert other.id in result_ids


@pytest.mark.django_db
def test_quick_switch_search_does_not_leak_cross_owner_notes():
    owner = create_account("qs-view-scope-owner")
    other = create_account("qs-view-scope-other")
    other_note = services.create_note(owner=other)
    services.rename_note(note=other_note, title="Shared Keyword")

    response = authenticated_client(owner).get(
        reverse("notes:quick_switch_search"), {"q": "shared"}
    )

    payload = json.loads(response.content)
    assert payload["results"] == []


@pytest.mark.django_db
def test_quick_switch_search_never_matches_body_text():
    owner = create_account("qs-view-body-owner")
    note = services.create_note(owner=owner)
    note.body_plain_text = "a paragraph mentioning giraffes"
    note.save(update_fields=["body_plain_text"])

    response = authenticated_client(owner).get(
        reverse("notes:quick_switch_search"), {"q": "giraffe"}
    )

    payload = json.loads(response.content)
    assert payload["results"] == []


@pytest.mark.django_db
def test_quick_switch_search_requires_authentication():
    response = Client().get(reverse("notes:quick_switch_search"), {"q": "anything"})

    assert response.status_code == 302


@pytest.mark.django_db
def test_quick_switch_search_returns_empty_results_for_blank_query():
    owner = create_account("qs-view-blank-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("notes:quick_switch_search"))

    payload = json.loads(response.content)
    assert payload == {"ok": True, "results": []}


@pytest.mark.django_db
def test_quick_switch_search_ignores_invalid_exclude_parameter():
    owner = create_account("qs-view-bad-exclude-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Findable Note")

    response = authenticated_client(owner).get(
        reverse("notes:quick_switch_search"),
        {"q": "findable", "exclude": "not-an-int"},
    )

    assert response.status_code == 200
    payload = json.loads(response.content)
    assert len(payload["results"]) == 1
    assert payload["results"][0]["id"] == note.id
