import re
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
from notes.views import (
    _ALL_NOTES_SORT_ORDER_BY,
    ALL_NOTES_SORT_CHOICES,
    ALL_NOTES_SORT_DEFAULT,
    ALL_NOTES_SORT_SESSION_KEY,
    _resolve_all_notes_sort,
)

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


def _seed_note(owner, *, title, created_hours_ago, modified_hours_ago, pinned=False):
    note = services.create_note(owner=owner)
    now = timezone.now()
    Note.objects.filter(pk=note.pk).update(
        title=title,
        created_at=now - timedelta(hours=created_hours_ago),
        modified_at=now - timedelta(hours=modified_hours_ago),
        pinned=pinned,
    )
    note.refresh_from_db()
    return note


@pytest.fixture
def four_notes(db):
    owner = create_account("sort-owner")
    # Deliberately mismatched title/date ordering so each sort mode produces
    # a visibly distinct arrangement.
    charlie = _seed_note(owner, title="charlie", created_hours_ago=4, modified_hours_ago=1)
    alpha = _seed_note(owner, title="Alpha", created_hours_ago=3, modified_hours_ago=4)
    delta = _seed_note(owner, title="Delta", created_hours_ago=2, modified_hours_ago=3)
    bravo = _seed_note(owner, title="bravo", created_hours_ago=1, modified_hours_ago=2)
    return owner, {"alpha": alpha, "bravo": bravo, "charlie": charlie, "delta": delta}


def _ordered_titles(owner, sort_value):
    # Home does not render any sorted output, so
    # the eight order-by mappings are exercised directly against the
    # queryset they produce -- the same real backend logic the
    # All Notes view actually calls (see
    # `notes/tests/test_all_notes.py` for full-route coverage) -- rather
    # than through Home's own rendered HTML, which never reflects sort
    # order at all.
    order_by = _ALL_NOTES_SORT_ORDER_BY[sort_value]
    return list(
        Note.objects.filter(owner=owner, trashed_at__isnull=True)
        .order_by(*order_by)
        .values_list("title", flat=True)
    )


# -- Ordering semantics for each of the eight choices (direct queryset) ------


@pytest.mark.django_db
def test_pinned_first_newest_modified_is_the_default(four_notes):
    owner, notes = four_notes
    services.set_note_pinned(note=notes["delta"], pinned=True)

    order = _ordered_titles(owner, ALL_NOTES_SORT_DEFAULT)

    # Delta is pinned -> first; remaining ordered by -modified_at (charlie=1h, bravo=2h, Alpha=4h)
    assert order == ["Delta", "charlie", "bravo", "Alpha"]


@pytest.mark.django_db
def test_pinned_first_oldest_modified(four_notes):
    owner, notes = four_notes
    services.set_note_pinned(note=notes["delta"], pinned=True)
    services.set_note_pinned(note=notes["bravo"], pinned=True)

    order = _ordered_titles(owner, "pinned_old")

    # Pinned: Delta (3h), bravo (2h) -> oldest modified first among pinned -> Delta, bravo
    # Unpinned: Alpha (4h), charlie (1h) -> oldest modified first -> Alpha, charlie
    assert order == ["Delta", "bravo", "Alpha", "charlie"]


@pytest.mark.django_db
def test_title_a_to_z_is_case_insensitive(four_notes):
    owner, _ = four_notes

    order = _ordered_titles(owner, "title_asc")

    assert order == ["Alpha", "bravo", "charlie", "Delta"]


@pytest.mark.django_db
def test_title_z_to_a_is_case_insensitive(four_notes):
    owner, _ = four_notes

    order = _ordered_titles(owner, "title_desc")

    assert order == ["Delta", "charlie", "bravo", "Alpha"]


@pytest.mark.django_db
def test_created_newest_first(four_notes):
    owner, _ = four_notes

    order = _ordered_titles(owner, "created_new")

    # created_hours_ago: charlie=4, Alpha=3, Delta=2, bravo=1 -> newest first
    # -> bravo, Delta, Alpha, charlie
    assert order == ["bravo", "Delta", "Alpha", "charlie"]


@pytest.mark.django_db
def test_created_oldest_first(four_notes):
    owner, _ = four_notes

    order = _ordered_titles(owner, "created_old")

    assert order == ["charlie", "Alpha", "Delta", "bravo"]


@pytest.mark.django_db
def test_modified_newest_first(four_notes):
    owner, _ = four_notes

    order = _ordered_titles(owner, "modified_new")

    # modified_hours_ago: charlie=1, bravo=2, Delta=3, Alpha=4 -> newest first
    assert order == ["charlie", "bravo", "Delta", "Alpha"]


@pytest.mark.django_db
def test_modified_oldest_first(four_notes):
    owner, _ = four_notes

    order = _ordered_titles(owner, "modified_old")

    assert order == ["Alpha", "Delta", "bravo", "charlie"]


@pytest.mark.django_db
def test_deterministic_tie_break_for_identical_modified_at(db):
    owner = create_account("tie-break-owner")
    now = timezone.now()
    first = services.create_note(owner=owner)
    second = services.create_note(owner=owner)
    Note.objects.filter(pk=first.pk).update(modified_at=now)
    Note.objects.filter(pk=second.pk).update(modified_at=now)
    assert second.pk > first.pk

    ordered_ids = list(
        Note.objects.filter(owner=owner)
        .order_by("-modified_at", "-id")
        .values_list("id", flat=True)
    )
    # Same modified_at for both -> the -id tie-break must put the higher id first.
    assert ordered_ids == [second.pk, first.pk]


# -- Pin/sort interaction (direct queryset) -----------------------------------


@pytest.mark.django_db
def test_explicit_title_sort_does_not_promote_pinned_notes(four_notes):
    owner, notes = four_notes
    services.set_note_pinned(note=notes["delta"], pinned=True)

    order = _ordered_titles(owner, "title_asc")

    # Pinned Delta is NOT promoted; pure alphabetical order applies
    assert order == ["Alpha", "bravo", "charlie", "Delta"]


@pytest.mark.django_db
def test_explicit_created_sort_does_not_promote_pinned_notes(four_notes):
    owner, notes = four_notes
    services.set_note_pinned(note=notes["alpha"], pinned=True)

    order = _ordered_titles(owner, "created_new")

    assert order == ["bravo", "Delta", "Alpha", "charlie"]


@pytest.mark.django_db
def test_sort_selection_does_not_change_pin_state(four_notes):
    # The `?sort=` query parameter is inert on Home (see
    # `test_home_no_longer_renders_sort_selector_or_apply_button` below),
    # but this still guards against a regression where an unrelated query
    # parameter accidentally affected pin state.
    owner, notes = four_notes
    services.set_note_pinned(note=notes["delta"], pinned=True)

    authenticated_client(owner).get(reverse("home"), {"sort": "title_asc"})

    notes["delta"].refresh_from_db()
    assert notes["delta"].pinned is True


# -- `_resolve_all_notes_sort` session/state handling (direct unit boundary) ------
#
# This resolver's real caller is the `all_notes` view (see
# `notes/tests/test_all_notes.py` for route-level coverage of that).
# Home's own view never calls it -- calling it would mutate the
# All Notes session key and parse a `?sort=` query parameter with no
# visible effect on anything Home renders -- see
# `test_home_dashboard_output_does_not_depend_on_sort_query_parameter`
# below. Exercised here directly against the function itself as a fast,
# focused unit boundary, independent of either route.


class _FakeSession(dict):
    """A plain-dict stand-in for `request.session`.

    `_resolve_all_notes_sort` only ever calls `.get(...)` and item
    assignment/membership on `request.session`, all of which a plain
    `dict` already provides -- no real Django session backend or
    database access is needed to exercise its actual branching logic.
    """


class _FakeRequest:
    def __init__(self, *, sort=None, session=None):
        self.GET = {"sort": sort} if sort is not None else {}
        self.session = session if session is not None else _FakeSession()


def test_resolve_all_notes_sort_valid_explicit_choice_is_saved_to_session():
    request = _FakeRequest(sort="title_asc")

    result = _resolve_all_notes_sort(request)

    assert result == "title_asc"
    assert request.session[ALL_NOTES_SORT_SESSION_KEY] == "title_asc"


def test_resolve_all_notes_sort_missing_explicit_choice_reuses_saved_session_value():
    session = _FakeSession()
    _resolve_all_notes_sort(_FakeRequest(sort="title_desc", session=session))

    result = _resolve_all_notes_sort(_FakeRequest(session=session))

    assert result == "title_desc"
    assert session[ALL_NOTES_SORT_SESSION_KEY] == "title_desc"


def test_resolve_all_notes_sort_unsupported_value_falls_back_to_default():
    result = _resolve_all_notes_sort(_FakeRequest(sort="not-a-real-choice"))

    assert result == ALL_NOTES_SORT_DEFAULT


def test_resolve_all_notes_sort_invalid_value_is_not_persisted_to_session():
    session = _FakeSession()

    _resolve_all_notes_sort(_FakeRequest(sort="not-a-real-choice", session=session))

    assert ALL_NOTES_SORT_SESSION_KEY not in session


def test_resolve_all_notes_sort_invalid_value_does_not_overwrite_existing_saved_session_value():
    session = _FakeSession()
    _resolve_all_notes_sort(_FakeRequest(sort="title_asc", session=session))

    _resolve_all_notes_sort(_FakeRequest(sort="bogus", session=session))

    assert session[ALL_NOTES_SORT_SESSION_KEY] == "title_asc"


def test_resolve_all_notes_sort_first_visit_with_no_session_value_uses_default():
    result = _resolve_all_notes_sort(_FakeRequest())

    assert result == ALL_NOTES_SORT_DEFAULT
    assert ALL_NOTES_SORT_DEFAULT == "pinned_new"


# -- Ownership and rendering --------------------------------------------------


@pytest.mark.django_db
def test_owner_scoping_remains_intact_with_sort_query_parameter_present(four_notes):
    # The `?sort=` parameter is inert on Home (see above), but Home must
    # still never leak another owner's notes regardless of what query
    # parameters are present on the request.
    owner, _ = four_notes
    other = create_account("sort-other-owner")
    other_note = services.create_note(owner=other)
    other_note.title = "Other Owner Note"
    other_note.save(update_fields=["title"])

    response = authenticated_client(owner).get(reverse("home"), {"sort": "title_asc"})
    content = response.content.decode()

    assert "Other Owner Note" not in content


@pytest.mark.django_db
def test_home_no_longer_renders_sort_selector_or_apply_button():
    owner = create_account("sort-removed-owner")
    services.create_note(owner=owner)

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    assert "home-sort-form" not in content
    assert "home-sort-select" not in content
    assert "Sort by" not in content


@pytest.mark.django_db
def test_home_dashboard_output_does_not_depend_on_sort_query_parameter():
    # An explicit `?sort=` value must have zero
    # effect on Home's rendered output -- not on the Recent module's
    # order, not on the session (Home never calls `_resolve_all_notes_sort`),
    # and not by raising an error for an unsupported value.
    owner = create_account("sort-param-inert-owner")
    older = services.create_note(owner=owner)
    services.rename_note(note=older, title="Older note")
    newer = services.create_note(owner=owner)
    services.rename_note(note=newer, title="Newer note")
    Note.objects.filter(pk=older.pk).update(modified_at=timezone.now() - timedelta(hours=2))
    Note.objects.filter(pk=newer.pk).update(modified_at=timezone.now() - timedelta(hours=1))

    def recent_section(response):
        # Excludes the CSRF token (regenerated per request, irrelevant
        # here) from the comparison; the Recent module's own content and
        # order is what must stay identical. Home
        # rows carry Pin/Move/rename forms of their own, each with a
        # real per-request CSRF token -- normalized here to a constant
        # placeholder so genuinely identical output still compares equal.
        content = response.content.decode()
        start = content.index('class="home-recent"')
        section = content[start : content.index('id="workspace-drawer"')]
        return re.sub(r'(name="csrfmiddlewaretoken" value=")[^"]*(")', r"\1CSRF\2", section)

    client = authenticated_client(owner)
    baseline = client.get(reverse("home"))
    with_sort = client.get(reverse("home"), {"sort": "title_asc"})
    with_bogus_sort = client.get(reverse("home"), {"sort": "not-a-real-choice"})

    assert baseline.status_code == with_sort.status_code == with_bogus_sort.status_code == 200
    assert recent_section(baseline) == recent_section(with_sort) == recent_section(with_bogus_sort)
    assert ALL_NOTES_SORT_SESSION_KEY not in client.session


@pytest.mark.django_db
def test_all_notes_sort_backend_constants_remain_defined():
    # Guards against accidental deletion/drift of the choices/order-by
    # mapping actively used by the `all_notes`
    # view -- every choice must have a matching order-by entry.
    assert len(ALL_NOTES_SORT_CHOICES) == 8
    assert set(value for value, _label in ALL_NOTES_SORT_CHOICES) == set(_ALL_NOTES_SORT_ORDER_BY)


@pytest.mark.django_db
def test_home_renders_no_duplicate_element_ids_with_sort_control(four_notes):
    owner, _ = four_notes

    response = authenticated_client(owner).get(reverse("home"))
    content = response.content.decode()

    id_pattern = re.compile(r'\sid="([^"]+)"')
    ids = id_pattern.findall(content)

    assert len(ids) == len(set(ids))


@pytest.mark.django_db
def test_tree_ordering_remains_unchanged_by_home_sort(four_notes):
    owner, notes = four_notes
    services.set_note_pinned(note=notes["delta"], pinned=True)

    response = authenticated_client(owner).get(reverse("home"), {"sort": "title_asc"})
    content = response.content.decode()

    tree_section = content[content.index('id="workspace-drawer"') :]
    # Tree/Unfiled ordering follows notes_grouped_for_tree's fixed
    # -pinned, Lower(title), -id order, distinct from
    # -pinned, -modified_at, -id, independent of the (dormant)
    # Home sort machinery -- Delta is pinned, so it is
    # promoted to the front regardless of the `?sort=` value passed here,
    # and the remaining unpinned notes are alphabetical rather than
    # recency-ordered.
    titles = ["Alpha", "bravo", "charlie", "Delta"]
    positions = {title: tree_section.index(f">{title}<") for title in titles}
    tree_order = sorted(titles, key=lambda title: positions[title])
    assert tree_order == ["Delta", "Alpha", "bravo", "charlie"]
    assert "note-list__pin-badge" not in tree_section
