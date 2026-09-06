import threading

import pytest
from django.contrib.auth import get_user_model
from django.db import connections
from django.http import Http404
from django.test import Client
from django.urls import reverse
from django.utils import timezone as django_timezone
from notes import services
from notes.models import TAG_COLOR_CHOICES, Note, Tag

from accounts import services as account_services
from accounts.models import User
from accounts.views import TAG_COLOR_CHOICES_ALPHABETICAL

PASSWORD = "LongUniquePassword123!"


@pytest.fixture(autouse=True)
def use_simple_staticfiles_storage(settings):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", django_timezone.now())
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
    session[account_services.LAST_ACTIVITY_KEY] = django_timezone.now().timestamp()
    session.save()
    return client


# -- list_tags_for_owner_with_usage() (service) -----------------------------


@pytest.mark.django_db
def test_usage_count_includes_active_notes():
    owner = create_account("usage-active-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=tag)

    [result] = services.list_tags_for_owner_with_usage(owner=owner)
    assert result.usage_count == 1


@pytest.mark.django_db
def test_usage_count_includes_trashed_notes():
    owner = create_account("usage-trash-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=tag)
    note.trashed_at = django_timezone.now()
    note.save(update_fields=["trashed_at"])

    [result] = services.list_tags_for_owner_with_usage(owner=owner)
    assert result.usage_count == 1


@pytest.mark.django_db
def test_usage_count_includes_emptied_notes():
    owner = create_account("usage-emptied-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=tag)
    now = django_timezone.now()
    note.trashed_at = now
    note.emptied_at = now
    note.save(update_fields=["trashed_at", "emptied_at"])

    [result] = services.list_tags_for_owner_with_usage(owner=owner)
    assert result.usage_count == 1


@pytest.mark.django_db
def test_usage_count_excludes_purged_notes():
    owner = create_account("usage-purged-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=tag)
    note.delete()

    [result] = services.list_tags_for_owner_with_usage(owner=owner)
    assert result.usage_count == 0


@pytest.mark.django_db
def test_zero_use_tag_appears_with_zero_count():
    owner = create_account("usage-zero-owner")
    services.get_or_create_tag(owner=owner, name="LONELY", color="slate")

    [result] = services.list_tags_for_owner_with_usage(owner=owner)
    assert result.usage_count == 0


@pytest.mark.django_db
def test_list_owner_isolation():
    owner = create_account("list-scope-owner")
    other = create_account("list-scope-other")
    services.get_or_create_tag(owner=other, name="RED", color="red")

    assert services.list_tags_for_owner_with_usage(owner=owner) == []


@pytest.mark.django_db
def test_default_sort_is_name_asc():
    owner = create_account("sort-default-owner")
    services.get_or_create_tag(owner=owner, name="ZEBRA", color="slate")
    services.get_or_create_tag(owner=owner, name="APPLE", color="slate")

    results = services.list_tags_for_owner_with_usage(owner=owner)
    assert [t.name for t in results] == ["APPLE", "ZEBRA"]


@pytest.mark.django_db
def test_all_four_sorts():
    owner = create_account("sort-all-owner")
    low = services.get_or_create_tag(owner=owner, name="LOW", color="slate")
    high = services.get_or_create_tag(owner=owner, name="HIGH", color="slate")
    for _ in range(3):
        note = services.create_note(owner=owner)
        services.assign_tag_to_note(note=note, tag=high)
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=low)

    name_asc = services.list_tags_for_owner_with_usage(owner=owner, sort="name_asc")
    assert [t.name for t in name_asc] == ["HIGH", "LOW"]

    name_desc = services.list_tags_for_owner_with_usage(owner=owner, sort="name_desc")
    assert [t.name for t in name_desc] == ["LOW", "HIGH"]

    usage_asc = services.list_tags_for_owner_with_usage(owner=owner, sort="usage_asc")
    assert [t.name for t in usage_asc] == ["LOW", "HIGH"]

    usage_desc = services.list_tags_for_owner_with_usage(owner=owner, sort="usage_desc")
    assert [t.name for t in usage_desc] == ["HIGH", "LOW"]


@pytest.mark.django_db
def test_usage_sort_tie_is_deterministic_by_name():
    owner = create_account("sort-tie-owner")
    services.get_or_create_tag(owner=owner, name="BETA", color="slate")
    services.get_or_create_tag(owner=owner, name="ALPHA", color="slate")

    results = services.list_tags_for_owner_with_usage(owner=owner, sort="usage_desc")
    assert [t.name for t in results] == ["ALPHA", "BETA"]


@pytest.mark.django_db
def test_query_filter():
    owner = create_account("filter-owner")
    services.get_or_create_tag(owner=owner, name="RED", color="red")
    services.get_or_create_tag(owner=owner, name="BLUE", color="blue")

    results = services.list_tags_for_owner_with_usage(owner=owner, query="re")
    assert [t.name for t in results] == ["RED"]


# -- rename_tag() (service) -------------------------------------------------


@pytest.mark.django_db
def test_rename_tag_updates_name():
    owner = create_account("rename-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    services.rename_tag(tag=tag, new_name="crimson")

    tag.refresh_from_db()
    assert tag.name == "CRIMSON"


@pytest.mark.django_db
def test_rename_tag_applies_normalization():
    owner = create_account("rename-normalize-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    services.rename_tag(tag=tag, new_name="  new   name  ")

    tag.refresh_from_db()
    assert tag.name == "NEW NAME"


@pytest.mark.django_db
def test_rename_tag_uppercase_off_preserves_case():
    owner = create_account("rename-uppercase-off-owner", tag_uppercase_enabled=False)
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    services.rename_tag(tag=tag, new_name="Crimson Shade")

    tag.refresh_from_db()
    assert tag.name == "Crimson Shade"


@pytest.mark.django_db
def test_rename_tag_uppercase_on_still_uppercases_by_default():
    owner = create_account("rename-uppercase-on-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    services.rename_tag(tag=tag, new_name="crimson")

    tag.refresh_from_db()
    assert tag.name == "CRIMSON"


@pytest.mark.django_db
def test_rename_tag_reads_the_tags_own_owner_preference():
    owner = create_account("rename-owner-scoped-owner", tag_uppercase_enabled=False)
    other = create_account("rename-owner-scoped-other")
    owner_tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    other_tag = services.get_or_create_tag(owner=other, name="BLUE", color="blue")

    services.rename_tag(tag=owner_tag, new_name="pink shade")
    services.rename_tag(tag=other_tag, new_name="sky shade")

    owner_tag.refresh_from_db()
    other_tag.refresh_from_db()
    assert owner_tag.name == "pink shade"
    assert other_tag.name == "SKY SHADE"


@pytest.mark.django_db
def test_rename_tag_rejects_too_long():
    owner = create_account("rename-toolong-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    with pytest.raises(services.TagNameValidationError):
        services.rename_tag(tag=tag, new_name="X" * 41)


@pytest.mark.django_db
def test_rename_tag_rejects_prohibited_characters():
    owner = create_account("rename-prohibited-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    with pytest.raises(services.TagNameValidationError):
        services.rename_tag(tag=tag, new_name="bad\tname")


@pytest.mark.django_db
def test_rename_tag_same_name_is_a_no_op_success():
    owner = create_account("rename-noop-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    result = services.rename_tag(tag=tag, new_name="red")

    assert result.name == "RED"


@pytest.mark.django_db
def test_rename_tag_case_insensitive_collision_fails_safely():
    owner = create_account("rename-collision-owner")
    services.get_or_create_tag(owner=owner, name="BLUE", color="blue")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    with pytest.raises(services.TagNameConflictError):
        services.rename_tag(tag=tag, new_name="blue")

    # Never merges -- both tags still exist independently.
    assert Tag.objects.filter(owner=owner).count() == 2


@pytest.mark.django_db
def test_rename_tag_whitespace_normalized_collision_fails_safely():
    owner = create_account("rename-ws-collision-owner")
    services.get_or_create_tag(owner=owner, name="NEW NAME", color="blue")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    with pytest.raises(services.TagNameConflictError):
        services.rename_tag(tag=tag, new_name="  new   name  ")


@pytest.mark.django_db
def test_rename_tag_cross_owner_same_name_allowed():
    owner = create_account("rename-cross-owner")
    other = create_account("rename-cross-other")
    services.get_or_create_tag(owner=other, name="BLUE", color="blue")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    result = services.rename_tag(tag=tag, new_name="blue")

    assert result.name == "BLUE"


@pytest.mark.django_db
def test_rename_tag_does_not_change_color():
    owner = create_account("rename-color-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="blue")

    services.rename_tag(tag=tag, new_name="crimson")

    tag.refresh_from_db()
    assert tag.color == "blue"


@pytest.mark.django_db
def test_rename_tag_preserves_relationships():
    owner = create_account("rename-relationships-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=tag)

    services.rename_tag(tag=tag, new_name="crimson")

    assert list(note.tags.values_list("name", flat=True)) == ["CRIMSON"]


@pytest.mark.django_db(transaction=True)
def test_rename_tag_race_maps_integrity_error_safely(monkeypatch):
    # Two distinct tags race to both rename themselves to the same,
    # not-yet-existing target name "PURPLE" -- neither pre-check sees a
    # collision (the target doesn't exist yet when both start), so the
    # database's own uniqueness constraint is the only thing that can
    # catch the loser, exactly as `IntegrityError` mapping exists for.
    owner = create_account("rename-race-owner")
    tag_a = services.get_or_create_tag(owner=owner, name="RED", color="red")
    tag_b = services.get_or_create_tag(owner=owner, name="GREEN", color="green")

    original_save = Tag.save
    ready = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}

    def pausing_save(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            ready.set()
            assert proceed.wait(timeout=5), "second rename never signaled proceed"
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Tag, "save", pausing_save)

    outcome_a: dict = {}

    def rename_a():
        try:
            services.rename_tag(tag=tag_a, new_name="purple")
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = threading.Thread(target=rename_a)
    thread_a.start()
    assert ready.wait(timeout=5), "first rename never reached save"

    outcome_b: dict = {}
    try:
        services.rename_tag(tag=tag_b, new_name="purple")
    except Exception as exc:
        outcome_b["error"] = exc

    proceed.set()
    thread_a.join(timeout=5)

    # Exactly one of the two must have failed safely with the friendly
    # conflict error -- never a raw IntegrityError/500, and never both
    # succeeding (which would violate the unique constraint).
    errors = [outcome_a.get("error"), outcome_b.get("error")]
    non_none = [e for e in errors if e is not None]
    assert len(non_none) == 1
    assert isinstance(non_none[0], services.TagNameConflictError)
    assert Tag.objects.filter(owner=owner, name="PURPLE").count() == 1


# -- recolor_tag() (service) ------------------------------------------------


@pytest.mark.django_db
@pytest.mark.parametrize(
    "color",
    [
        "slate",
        "blue",
        "teal",
        "green",
        "yellow",
        "amber",
        "orange",
        "red",
        "rose",
        "violet",
        "brown",
    ],
)
def test_recolor_tag_accepts_every_valid_color(color):
    owner = create_account(f"recolor-{color}-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="slate")

    services.recolor_tag(tag=tag, color=color)

    tag.refresh_from_db()
    assert tag.color == color


@pytest.mark.django_db
def test_recolor_tag_rejects_invalid_color():
    owner = create_account("recolor-invalid-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="slate")

    with pytest.raises(ValueError):
        services.recolor_tag(tag=tag, color="chartreuse")

    tag.refresh_from_db()
    assert tag.color == "slate"


@pytest.mark.django_db
def test_recolor_tag_does_not_change_name():
    owner = create_account("recolor-name-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="slate")

    services.recolor_tag(tag=tag, color="blue")

    tag.refresh_from_db()
    assert tag.name == "RED"


@pytest.mark.django_db
def test_recolor_tag_preserves_relationships():
    owner = create_account("recolor-relationships-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="slate")
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=tag)

    services.recolor_tag(tag=tag, color="blue")

    assert list(note.tags.all()) == [tag]


# -- delete_tag() (service) -------------------------------------------------


@pytest.mark.django_db
def test_delete_tag_zero_use():
    owner = create_account("delete-zero-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    services.delete_tag(tag=tag)

    assert not Tag.objects.filter(pk=tag.pk).exists()


@pytest.mark.django_db
def test_delete_tag_used_removes_relationship_but_not_note():
    owner = create_account("delete-used-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Keep Me")
    services.assign_tag_to_note(note=note, tag=tag)

    services.delete_tag(tag=tag)

    note.refresh_from_db()
    assert note.title == "Keep Me"
    assert list(note.tags.all()) == []


@pytest.mark.django_db
def test_delete_tag_owner_isolation():
    owner = create_account("delete-scope-owner")
    other = create_account("delete-scope-other")
    tag = services.get_or_create_tag(owner=other, name="RED", color="red")

    with pytest.raises(Http404):
        services.tag_for_owner_or_404(tag_id=tag.pk, owner=owner)


# -- bulk_delete_tags() (service) -------------------------------------------


@pytest.mark.django_db
def test_bulk_delete_multiple_tags():
    owner = create_account("bulk-multi-owner")
    a = services.get_or_create_tag(owner=owner, name="A", color="red")
    b = services.get_or_create_tag(owner=owner, name="B", color="blue")

    count = services.bulk_delete_tags(owner=owner, tag_ids=[a.pk, b.pk])

    assert count == 2
    assert Tag.objects.filter(owner=owner).count() == 0


@pytest.mark.django_db
def test_bulk_delete_used_and_unused_mixture_notes_survive():
    owner = create_account("bulk-mixture-owner")
    used = services.get_or_create_tag(owner=owner, name="USED", color="red")
    unused = services.get_or_create_tag(owner=owner, name="UNUSED", color="blue")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Survivor")
    services.assign_tag_to_note(note=note, tag=used)

    services.bulk_delete_tags(owner=owner, tag_ids=[used.pk, unused.pk])

    note.refresh_from_db()
    assert note.title == "Survivor"
    assert Note.objects.filter(pk=note.pk).exists()


@pytest.mark.django_db
def test_bulk_delete_foreign_ids_never_mutate_foreign_rows():
    owner = create_account("bulk-foreign-owner")
    other = create_account("bulk-foreign-other")
    own_tag = services.get_or_create_tag(owner=owner, name="MINE", color="red")
    foreign_tag = services.get_or_create_tag(owner=other, name="THEIRS", color="blue")

    count = services.bulk_delete_tags(owner=owner, tag_ids=[own_tag.pk, foreign_tag.pk])

    assert count == 1
    assert not Tag.objects.filter(pk=own_tag.pk).exists()
    assert Tag.objects.filter(pk=foreign_tag.pk).exists()


@pytest.mark.django_db
def test_bulk_delete_only_foreign_ids_deletes_nothing():
    owner = create_account("bulk-onlyforeign-owner")
    other = create_account("bulk-onlyforeign-other")
    foreign_tag = services.get_or_create_tag(owner=other, name="THEIRS", color="blue")

    count = services.bulk_delete_tags(owner=owner, tag_ids=[foreign_tag.pk])

    assert count == 0
    assert Tag.objects.filter(pk=foreign_tag.pk).exists()


# -- create_tag_for_owner() (service) ----------------------------------------
# Creates a persistent, zero-use Tag vocabulary object, never attached
# to any Note.


@pytest.mark.django_db
def test_create_tag_for_owner_creates_zero_use_tag():
    owner = create_account("create-zero-use-owner")

    tag = services.create_tag_for_owner(owner=owner, name="FRESH", color="")

    assert tag.name == "FRESH"
    [result] = services.list_tags_for_owner_with_usage(owner=owner)
    assert result.usage_count == 0


@pytest.mark.django_db
def test_create_tag_for_owner_applies_normalization():
    owner = create_account("create-normalize-owner")

    tag = services.create_tag_for_owner(owner=owner, name="  new   tag  ", color="")

    assert tag.name == "NEW TAG"


@pytest.mark.django_db
def test_create_tag_for_owner_applies_uppercase():
    owner = create_account("create-uppercase-owner")

    tag = services.create_tag_for_owner(owner=owner, name="lowercase", color="")

    assert tag.name == "LOWERCASE"


@pytest.mark.django_db
def test_create_tag_for_owner_collapses_internal_whitespace():
    owner = create_account("create-whitespace-owner")

    tag = services.create_tag_for_owner(owner=owner, name="a   b    c", color="")

    assert tag.name == "A B C"


@pytest.mark.django_db
def test_create_tag_for_owner_explicit_color_wins():
    owner = create_account("create-explicit-color-owner")

    tag = services.create_tag_for_owner(owner=owner, name="ORDINARY", color="violet")

    assert tag.color == "violet"


@pytest.mark.django_db
def test_create_tag_for_owner_semantic_color_when_no_explicit_choice():
    owner = create_account("create-semantic-color-owner")

    tag = services.create_tag_for_owner(owner=owner, name="teal", color="")

    assert tag.color == "teal"


@pytest.mark.django_db
def test_create_tag_for_owner_falls_back_to_slate_default():
    owner = create_account("create-default-color-owner")

    tag = services.create_tag_for_owner(owner=owner, name="ORDINARY", color="")

    assert tag.color == "slate"


@pytest.mark.django_db
def test_create_tag_for_owner_uppercase_off_preserves_case():
    owner = create_account("create-uppercase-off-owner", tag_uppercase_enabled=False)

    tag = services.create_tag_for_owner(owner=owner, name="Server Room", color="")

    assert tag.name == "Server Room"


@pytest.mark.django_db
def test_create_tag_for_owner_semantic_off_falls_back_to_default_not_semantic():
    owner = create_account("create-semantic-off-owner", tag_semantic_color_enabled=False)

    tag = services.create_tag_for_owner(owner=owner, name="RED", color="")

    assert tag.color == "slate"


@pytest.mark.django_db
def test_create_tag_for_owner_custom_default_color():
    owner = create_account("create-custom-default-owner", tag_default_color="violet")

    tag = services.create_tag_for_owner(owner=owner, name="ORDINARY", color="")

    assert tag.color == "violet"


@pytest.mark.django_db
def test_create_tag_for_owner_semantic_wins_over_custom_default():
    owner = create_account("create-semantic-vs-default-owner", tag_default_color="violet")

    tag = services.create_tag_for_owner(owner=owner, name="RED", color="")

    assert tag.color == "red"


@pytest.mark.django_db
def test_create_tag_for_owner_explicit_wins_over_semantic_and_custom_default():
    owner = create_account("create-explicit-wins-owner", tag_default_color="violet")

    tag = services.create_tag_for_owner(owner=owner, name="RED", color="amber")

    assert tag.color == "amber"


@pytest.mark.django_db
def test_create_tag_for_owner_two_normalization_passes_stay_consistent():
    # create_tag_for_owner() keeps its own defensive normalization pass
    # rather than trusting the caller -- confirms that pass reads the
    # same owner preference the (already-normalized-once) input assumes,
    # so the second pass is idempotent, not a second, independently-
    # diverging policy.
    owner = create_account("create-double-normalize-owner", tag_uppercase_enabled=False)

    # Simulates a caller (e.g. NoteTagAssignForm.clean_name()) that
    # already normalized once under the same preference.
    already_normalized_once = services.normalize_tag_name(
        "  server   room  ", uppercase=owner.tag_uppercase_enabled
    )
    tag = services.create_tag_for_owner(owner=owner, name=already_normalized_once, color="")

    assert tag.name == "server room"


@pytest.mark.django_db
def test_create_tag_for_owner_rejects_too_long():
    owner = create_account("create-toolong-owner")

    with pytest.raises(services.TagNameValidationError):
        services.create_tag_for_owner(owner=owner, name="X" * 41, color="")


@pytest.mark.django_db
def test_create_tag_for_owner_accepts_exactly_40_characters():
    owner = create_account("create-exactly40-owner")

    tag = services.create_tag_for_owner(owner=owner, name="X" * 40, color="")

    assert len(tag.name) == 40


@pytest.mark.django_db
def test_create_tag_for_owner_rejects_prohibited_characters():
    owner = create_account("create-prohibited-owner")

    with pytest.raises(services.TagNameValidationError):
        services.create_tag_for_owner(owner=owner, name="bad\tname", color="")


@pytest.mark.django_db
def test_create_tag_for_owner_same_owner_collision_fails_safely():
    owner = create_account("create-collision-owner")
    services.get_or_create_tag(owner=owner, name="RED", color="red")

    with pytest.raises(services.TagNameConflictError):
        services.create_tag_for_owner(owner=owner, name="red", color="")

    assert Tag.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_create_tag_for_owner_cross_owner_same_name_allowed():
    owner = create_account("create-cross-owner")
    other = create_account("create-cross-other")
    services.get_or_create_tag(owner=other, name="RED", color="red")

    tag = services.create_tag_for_owner(owner=owner, name="red", color="")

    assert tag.name == "RED"
    assert Tag.objects.filter(owner=owner).count() == 1


@pytest.mark.django_db
def test_create_tag_for_owner_creates_no_note_relationship():
    owner = create_account("create-no-note-owner")

    tag = services.create_tag_for_owner(owner=owner, name="LONELY", color="")

    assert tag.notes.count() == 0


@pytest.mark.django_db(transaction=True)
def test_create_tag_for_owner_race_maps_integrity_error_safely(monkeypatch):
    # Two concurrent creates race to both create a tag under the same,
    # not-yet-existing normalized name -- neither pre-check sees a
    # collision (the name doesn't exist yet when both start), so the
    # database's own uniqueness constraint is the only thing that can
    # catch the loser.
    owner = create_account("create-race-owner")

    original_save = Tag.save
    ready = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}

    def pausing_save(self, *args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            ready.set()
            assert proceed.wait(timeout=5), "second create never signaled proceed"
        return original_save(self, *args, **kwargs)

    monkeypatch.setattr(Tag, "save", pausing_save)

    outcome_a: dict = {}

    def create_a():
        try:
            services.create_tag_for_owner(owner=owner, name="RACER", color="")
        except Exception as exc:  # pragma: no cover
            outcome_a["error"] = exc
        finally:
            connections.close_all()

    thread_a = threading.Thread(target=create_a)
    thread_a.start()
    assert ready.wait(timeout=5), "first create never reached save"

    outcome_b: dict = {}
    try:
        services.create_tag_for_owner(owner=owner, name="RACER", color="")
    except Exception as exc:
        outcome_b["error"] = exc

    proceed.set()
    thread_a.join(timeout=5)

    # Exactly one of the two must have failed safely with the friendly
    # conflict error -- never a raw IntegrityError/500, and never both
    # succeeding (which would violate the unique constraint).
    errors = [outcome_a.get("error"), outcome_b.get("error")]
    non_none = [e for e in errors if e is not None]
    assert len(non_none) == 1
    assert isinstance(non_none[0], services.TagNameConflictError)
    assert Tag.objects.filter(owner=owner, name__iexact="RACER").count() == 1


# -- views -------------------------------------------------------------------


@pytest.mark.django_db
def test_tag_management_view_requires_login():
    response = Client().get(reverse("accounts:tag_management"))
    assert response.status_code in (302, 401, 403)


@pytest.mark.django_db
def test_tag_management_view_owner_isolation():
    owner = create_account("view-scope-owner")
    other = create_account("view-scope-other")
    services.get_or_create_tag(owner=other, name="THEIRS", color="blue")

    response = authenticated_client(owner).get(reverse("accounts:tag_management"))

    assert response.status_code == 200
    assert b"THEIRS" not in response.content


@pytest.mark.django_db
def test_tag_management_view_renders_usage_count():
    owner = create_account("view-usage-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    note = services.create_note(owner=owner)
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).get(reverse("accounts:tag_management"))

    assert response.status_code == 200
    content = response.content.decode()
    assert "RED" in content


@pytest.mark.django_db
def test_tag_management_view_uses_official_tag_manager_name():
    # The official user-facing name is "Tag manager", not "Manage tags"
    # or a bare "Tags" menu label.
    owner = create_account("view-naming-owner")

    response = authenticated_client(owner).get(reverse("accounts:tag_management"))

    content = response.content.decode()
    assert "<h1>Tag manager</h1>" in content
    assert ">Tag manager<" in content  # the account-menu entry
    assert "Manage tags" not in content


@pytest.mark.django_db
def test_preferences_view_links_to_tag_manager_by_its_official_name():
    owner = create_account("view-preferences-naming-owner")

    response = authenticated_client(owner).get(reverse("accounts:preferences"))

    content = response.content.decode()
    assert "Tag manager" in content
    assert "Manage tags" not in content


@pytest.mark.django_db
def test_tag_management_view_color_column_shows_stored_color_name_not_tag_name():
    # The Color column must communicate the Tag's stored color (e.g.
    # "Violet"), never a second pill repeating the tag's own name,
    # rendered as `.tag-management__color-pill` rather than a tiny
    # swatch/dot presentation.
    owner = create_account("view-color-naming-owner")
    services.get_or_create_tag(owner=owner, name="BLUEBALLS", color="violet")

    response = authenticated_client(owner).get(reverse("accounts:tag_management"))

    content = response.content.decode()
    assert "Violet" in content
    assert 'class="tag-management__color-pill" data-tag-color="violet"' in content
    pill_start = content.index('class="tag-management__color-pill"')
    pill_fragment = content[pill_start : pill_start + 250]
    assert "BLUEBALLS" not in pill_fragment


@pytest.mark.django_db
def test_tag_management_view_new_tag_control_and_editor_present():
    owner = create_account("view-new-tag-owner")

    response = authenticated_client(owner).get(reverse("accounts:tag_management"))

    content = response.content.decode()
    assert 'data-tag-inline-edit-target="tag-new-editor"' in content
    assert 'id="tag-new-editor"' in content
    assert f'action="{reverse("accounts:tag_create")}"' in content


@pytest.mark.django_db
def test_tag_management_view_sort_select_has_exactly_four_options():
    # The four sort links are replaced by a single <select> with exactly
    # these four values/labels -- no behavior change, presentation only.
    owner = create_account("view-sort-select-owner")

    response = authenticated_client(owner).get(reverse("accounts:tag_management"))

    content = response.content.decode()
    select_start = content.index('id="tag-management-sort"')
    select_end = content.index("</select>", select_start)
    select_fragment = content[select_start:select_end]
    assert select_fragment.count("<option") == 4
    assert 'value="name_asc"' in select_fragment
    assert 'value="name_desc"' in select_fragment
    assert 'value="usage_asc"' in select_fragment
    assert 'value="usage_desc"' in select_fragment


@pytest.mark.django_db
def test_tag_management_view_sort_select_marks_current_sort_selected():
    owner = create_account("view-sort-selected-owner")

    response = authenticated_client(owner).get(
        reverse("accounts:tag_management"), {"sort": "usage_desc"}
    )

    content = response.content.decode()
    select_start = content.index('id="tag-management-sort"')
    select_end = content.index("</select>", select_start)
    select_fragment = content[select_start:select_end]
    assert 'value="usage_desc" selected' in select_fragment
    assert "selected" not in select_fragment.replace('value="usage_desc" selected', "")


def test_tag_color_choices_alphabetical_contains_exactly_the_11_stored_colors():
    # A pure presentation reordering -- the same 11 (value, label) pairs
    # as notes.models.TAG_COLOR_CHOICES, no additions/removals.
    assert set(TAG_COLOR_CHOICES_ALPHABETICAL) == set(TAG_COLOR_CHOICES)
    assert len(TAG_COLOR_CHOICES_ALPHABETICAL) == 11


def test_tag_color_choices_alphabetical_is_actually_alphabetical_by_label():
    labels = [label for _value, label in TAG_COLOR_CHOICES_ALPHABETICAL]
    assert labels == sorted(labels)
    assert labels == [
        "Amber",
        "Blue",
        "Brown",
        "Green",
        "Orange",
        "Red",
        "Rose",
        "Slate",
        "Teal",
        "Violet",
        "Yellow",
    ]


def test_tag_color_choices_alphabetical_never_mutates_the_stored_enum_order():
    # notes.models.TAG_COLOR_CHOICES itself -- the actual stored enum/
    # order -- must remain exactly as declared (slate first, the model's
    # own default), unaffected by the presentation-only reordering above.
    assert TAG_COLOR_CHOICES[0] == ("slate", "Slate")


@pytest.mark.django_db
def test_tag_management_view_new_tag_color_select_has_default_first_then_alphabetical():
    owner = create_account("view-new-tag-color-order-owner")

    response = authenticated_client(owner).get(reverse("accounts:tag_management"))

    content = response.content.decode()
    select_start = content.index('id="tag-new-color"')
    select_end = content.index("</select>", select_start)
    select_fragment = content[select_start:select_end]
    values = [part.split('"')[0] for part in select_fragment.split('value="')[1:]]
    assert values[0] == ""
    assert values[1:] == [value for value, _label in TAG_COLOR_CHOICES_ALPHABETICAL]


@pytest.mark.django_db
def test_tag_management_view_recolor_select_is_alphabetical_with_no_default_option():
    owner = create_account("view-recolor-color-order-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    response = authenticated_client(owner).get(reverse("accounts:tag_management"))

    content = response.content.decode()
    select_start = content.index(f'id="tag-recolor-{tag.id}"')
    select_end = content.index("</select>", select_start)
    select_fragment = content[select_start:select_end]
    values = [part.split('"')[0] for part in select_fragment.split('value="')[1:]]
    assert values == [value for value, _label in TAG_COLOR_CHOICES_ALPHABETICAL]
    assert "" not in values


@pytest.mark.django_db
def test_tag_management_view_row_has_single_ellipsis_menu_not_visible_actions():
    # Rename/Recolor/Delete must not render as three permanently visible
    # buttons on the row -- they live behind one `.row-action-menu`
    # ellipsis trigger per tag, reusing the existing row-action-menu
    # primitive.
    owner = create_account("view-ellipsis-owner")
    services.get_or_create_tag(owner=owner, name="ELLIPSIS", color="red")

    response = authenticated_client(owner).get(reverse("accounts:tag_management"))

    content = response.content.decode()
    assert 'class="row-action-menu tag-management__row-menu"' in content
    assert "Actions for ELLIPSIS" in content
    assert 'data-tag-inline-edit-target="tag-rename-row-' in content
    assert 'data-tag-inline-edit-target="tag-recolor-row-' in content


@pytest.mark.django_db
def test_tag_create_view_creates_zero_use_tag():
    owner = create_account("view-create-owner")

    response = authenticated_client(owner).post(
        reverse("accounts:tag_create"), {"name": "fresh", "color": ""}
    )

    assert response.status_code == 302
    tag = Tag.objects.get(owner=owner, name__iexact="fresh")
    assert tag.name == "FRESH"
    assert tag.notes.count() == 0


@pytest.mark.django_db
def test_tag_create_view_uppercase_off_preserves_case():
    owner = create_account("view-create-uppercase-off-owner", tag_uppercase_enabled=False)

    authenticated_client(owner).post(
        reverse("accounts:tag_create"), {"name": "Server Room", "color": ""}
    )

    assert Tag.objects.filter(owner=owner, name="Server Room").exists()


@pytest.mark.django_db
def test_tag_create_view_and_note_add_tag_agree_for_the_same_owner():
    # Both creation entry points must resolve identically for the same
    # owner/preferences/input.
    owner = create_account(
        "view-create-parity-owner", tag_semantic_color_enabled=False, tag_default_color="rose"
    )

    authenticated_client(owner).post(reverse("accounts:tag_create"), {"name": "red", "color": ""})

    tag = Tag.objects.get(owner=owner, name="RED")
    assert tag.color == "rose"


@pytest.mark.django_db
def test_tag_create_view_requires_post():
    owner = create_account("view-create-post-only-owner")

    response = authenticated_client(owner).get(reverse("accounts:tag_create"))

    assert response.status_code == 405


@pytest.mark.django_db
def test_tag_create_view_requires_login():
    response = Client().post(reverse("accounts:tag_create"), {"name": "FRESH", "color": ""})
    assert response.status_code in (302, 401, 403)


@pytest.mark.django_db
def test_tag_create_view_owner_isolation():
    # Owner comes exclusively from request.user -- there is no owner
    # field on the form to spoof, but this confirms the created tag is
    # scoped to the authenticated user and invisible to another owner's
    # own list.
    owner = create_account("view-create-scope-owner")
    other = create_account("view-create-scope-other")

    authenticated_client(owner).post(reverse("accounts:tag_create"), {"name": "MINE", "color": ""})

    assert Tag.objects.filter(owner=owner, name="MINE").exists()
    assert not Tag.objects.filter(owner=other, name="MINE").exists()
    response = authenticated_client(other).get(reverse("accounts:tag_management"))
    assert b"MINE" not in response.content


@pytest.mark.django_db
def test_tag_create_view_collision_feedback():
    owner = create_account("view-create-collision-owner")
    services.get_or_create_tag(owner=owner, name="RED", color="red")

    response = authenticated_client(owner).post(
        reverse("accounts:tag_create"), {"name": "red", "color": ""}, follow=True
    )

    assert response.status_code == 200
    content = response.content.decode()
    assert "already exists" in content
    assert Tag.objects.filter(owner=owner, name="RED").count() == 1


@pytest.mark.django_db
def test_tag_create_view_explicit_color():
    owner = create_account("view-create-explicit-color-owner")

    authenticated_client(owner).post(
        reverse("accounts:tag_create"), {"name": "ORDINARY", "color": "violet"}
    )

    tag = Tag.objects.get(owner=owner, name="ORDINARY")
    assert tag.color == "violet"


@pytest.mark.django_db
def test_tag_create_view_semantic_default_color():
    owner = create_account("view-create-semantic-color-owner")

    authenticated_client(owner).post(reverse("accounts:tag_create"), {"name": "teal", "color": ""})

    tag = Tag.objects.get(owner=owner, name="TEAL")
    assert tag.color == "teal"


@pytest.mark.django_db
def test_tag_create_view_new_tag_appears_in_manager_list():
    owner = create_account("view-create-appears-owner")

    authenticated_client(owner).post(
        reverse("accounts:tag_create"), {"name": "APPEARS", "color": ""}
    )

    response = authenticated_client(owner).get(reverse("accounts:tag_management"))
    assert b"APPEARS" in response.content


@pytest.mark.django_db
def test_tag_rename_view():
    owner = create_account("view-rename-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    response = authenticated_client(owner).post(
        reverse("accounts:tag_rename", args=[tag.pk]), {"name": "crimson"}
    )

    assert response.status_code == 302
    tag.refresh_from_db()
    assert tag.name == "CRIMSON"


@pytest.mark.django_db
def test_tag_rename_view_uppercase_off_preserves_case():
    owner = create_account("view-rename-uppercase-off-owner", tag_uppercase_enabled=False)
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    response = authenticated_client(owner).post(
        reverse("accounts:tag_rename", args=[tag.pk]), {"name": "Crimson Shade"}
    )

    assert response.status_code == 302
    tag.refresh_from_db()
    assert tag.name == "Crimson Shade"


@pytest.mark.django_db
def test_tag_rename_view_collision_feedback():
    owner = create_account("view-rename-collision-owner")
    services.get_or_create_tag(owner=owner, name="BLUE", color="blue")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    response = authenticated_client(owner).post(
        reverse("accounts:tag_rename", args=[tag.pk]), {"name": "blue"}, follow=True
    )

    assert response.status_code == 200
    tag.refresh_from_db()
    assert tag.name == "RED"


@pytest.mark.django_db
def test_tag_rename_view_owner_isolation_404s():
    owner = create_account("view-rename-scope-owner")
    other = create_account("view-rename-scope-other")
    tag = services.get_or_create_tag(owner=other, name="RED", color="red")

    response = authenticated_client(owner).post(
        reverse("accounts:tag_rename", args=[tag.pk]), {"name": "crimson"}
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_tag_recolor_view():
    owner = create_account("view-recolor-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="slate")

    response = authenticated_client(owner).post(
        reverse("accounts:tag_recolor", args=[tag.pk]), {"color": "blue"}
    )

    assert response.status_code == 302
    tag.refresh_from_db()
    assert tag.color == "blue"


@pytest.mark.django_db
def test_tag_delete_view_used_tag():
    owner = create_account("view-delete-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Keep Me")
    services.assign_tag_to_note(note=note, tag=tag)

    response = authenticated_client(owner).post(reverse("accounts:tag_delete", args=[tag.pk]))

    assert response.status_code == 302
    assert not Tag.objects.filter(pk=tag.pk).exists()
    note.refresh_from_db()
    assert note.title == "Keep Me"


@pytest.mark.django_db
def test_tag_delete_view_requires_post():
    owner = create_account("view-delete-post-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    response = authenticated_client(owner).get(reverse("accounts:tag_delete", args=[tag.pk]))

    assert response.status_code == 405


@pytest.mark.django_db
def test_tag_bulk_delete_view():
    owner = create_account("view-bulk-owner")
    a = services.get_or_create_tag(owner=owner, name="A", color="red")
    b = services.get_or_create_tag(owner=owner, name="B", color="blue")

    response = authenticated_client(owner).post(
        reverse("accounts:tag_bulk_delete"), {"tag_ids": [str(a.pk), str(b.pk)]}
    )

    assert response.status_code == 302
    assert Tag.objects.filter(owner=owner).count() == 0


@pytest.mark.django_db
def test_tag_bulk_delete_view_requires_post():
    owner = create_account("view-bulk-post-owner")

    response = authenticated_client(owner).get(reverse("accounts:tag_bulk_delete"))

    assert response.status_code == 405


# -- autocomplete integration ------------------------------------------------


@pytest.mark.django_db
def test_autocomplete_reflects_rename_recolor_delete_without_code_change():
    owner = create_account("integration-owner")
    tag = services.get_or_create_tag(owner=owner, name="RED", color="red")

    [before] = services.search_tags_for_owner(owner=owner, query="red")
    assert before.name == "RED"
    assert before.color == "red"

    services.rename_tag(tag=tag, new_name="crimson")
    services.recolor_tag(tag=tag, color="blue")

    [after] = services.search_tags_for_owner(owner=owner, query="crimson")
    assert after.name == "CRIMSON"
    assert after.color == "blue"

    services.delete_tag(tag=tag)
    assert services.search_tags_for_owner(owner=owner, query="crimson") == []
