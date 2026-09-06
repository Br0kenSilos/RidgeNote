"""Administrator account-deletion status, scheduling,
and cancellation UI.

Covers the two views (`user_schedule_deletion`, `user_cancel_deletion`),
the `user_detail`/`user_list` templates, and the read-only
`count_owned_content()` ownership-count helper. Permanent-purge UI and typed
username confirmation are covered separately -- see
`accounts/tests/test_account_purge_ui.py`. Service-layer locking,
audit, cascade, and concurrency behavior is covered by
`accounts/tests/test_account_deletion.py` and
`accounts/tests/test_account_purge.py` and is not duplicated here.
"""

from datetime import timedelta

import pytest
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from notes import documents
from notes.models import Folder, Note, Tag

from accounts import services
from accounts.models import User
from accounts.tests.test_accounts import authenticated_client, create_account, create_admin


@pytest.fixture(autouse=True)
def use_simple_staticfiles_storage(settings):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def _schedule_and_make_eligible(target, *, actor=None):
    actor = actor or target
    services.schedule_user_deletion(target=target, actor=actor)
    target.refresh_from_db()
    target.deletion_recovery_deadline = timezone.now() - timedelta(seconds=1)
    target.save(update_fields=["deletion_recovery_deadline"])
    target.refresh_from_db()
    return target


def _account_actions_panel(content: bytes) -> bytes:
    # Scopes assertions to the
    # user-detail account-actions panel rather than the whole response
    # body, since the site-wide Help panel (embedded on every page)
    # legitimately reuses "Schedule deletion"/"Cancel deletion" wording
    # elsewhere in the same document.
    start = content.index(b'class="panel panel--account-actions"')
    end = content.index(b"</section>", start)
    return content[start:end]


def _deletion_cell(content: bytes) -> bytes:
    # Same rationale as `_account_actions_panel`, scoped to the one
    # deletion-status table cell instead of the whole response body.
    start = content.index(b'class="user-list-page__cell--deletion"')
    end = content.index(b"</td>", start)
    return content[start:end]


# -- Authorization and methods -------------------------------------------


@pytest.mark.django_db
def test_schedule_deletion_get_requires_login():
    create_admin("auth-admin")
    target = create_account("auth-target")

    response = Client().get(reverse("accounts:user_schedule_deletion", args=[target.pk]))
    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_schedule_deletion_post_requires_login():
    target = create_account("auth-target-post")
    response = Client().post(reverse("accounts:user_schedule_deletion", args=[target.pk]))
    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_schedule_deletion_forbidden_for_non_admin():
    user = create_account("non-admin-user")
    target = create_account("non-admin-target")
    client = authenticated_client(user)

    get_response = client.get(reverse("accounts:user_schedule_deletion", args=[target.pk]))
    post_response = client.post(reverse("accounts:user_schedule_deletion", args=[target.pk]))

    assert get_response.status_code == 403
    assert post_response.status_code == 403


@pytest.mark.django_db
def test_cancel_deletion_requires_login():
    target = create_account("cancel-auth-target")
    response = Client().post(reverse("accounts:user_cancel_deletion", args=[target.pk]))
    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_cancel_deletion_forbidden_for_non_admin():
    user = create_account("non-admin-canceler")
    target = create_account("non-admin-cancel-target")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:user_cancel_deletion", args=[target.pk]))

    assert response.status_code == 403


@pytest.mark.django_db
def test_cancel_deletion_rejects_get():
    admin = create_admin("method-admin")
    target = create_account("method-target")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_cancel_deletion", args=[target.pk]))

    assert response.status_code == 405


@pytest.mark.django_db
def test_schedule_deletion_allows_only_get_and_post():
    admin = create_admin("method-admin-2")
    target = create_account("method-target-2")
    client = authenticated_client(admin)

    response = client.delete(reverse("accounts:user_schedule_deletion", args=[target.pk]))

    assert response.status_code == 405


@pytest.mark.django_db
def test_route_names_reverse_correctly():
    target_id = 42
    assert (
        reverse("accounts:user_schedule_deletion", args=[target_id])
        == f"/admin/users/{target_id}/schedule-deletion/"
    )
    assert (
        reverse("accounts:user_cancel_deletion", args=[target_id])
        == f"/admin/users/{target_id}/cancel-deletion/"
    )


# -- User detail -----------------------------------------------------------


@pytest.mark.django_db
def test_user_detail_shows_active_status():
    admin = create_admin("detail-admin")
    target = create_account("detail-active-target")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))

    assert response.status_code == 200
    assert b"Active" in response.content
    actions_panel = _account_actions_panel(response.content)
    assert b"Schedule deletion" in actions_panel
    assert b"Cancel deletion" not in actions_panel


@pytest.mark.django_db
def test_user_detail_shows_disabled_status():
    admin = create_admin("detail-admin-2")
    target = create_account("detail-disabled-target", is_active=False)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))

    assert response.status_code == 200
    assert b"Disabled" in response.content
    assert b"Schedule deletion" in response.content


@pytest.mark.django_db
def test_user_detail_shows_recoverable_pending_status():
    admin = create_admin("detail-admin-3")
    target = create_account("detail-recoverable-target")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))

    assert response.status_code == 200
    assert b"Pending deletion \xe2\x80\x94 recoverable until" in response.content
    assert b"Deletion scheduled" in response.content
    assert b"Recovery deadline" in response.content
    actions_panel = _account_actions_panel(response.content)
    assert b"Schedule deletion" not in actions_panel
    assert b"Cancel deletion" in actions_panel


@pytest.mark.django_db
def test_user_detail_shows_purge_eligible_pending_status():
    admin = create_admin("detail-admin-4")
    target = create_account("detail-eligible-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))

    # The eligible branch shows an actionable purge
    # link for non-self targets; the purge
    # link's own presentation is fully covered by
    # `accounts/tests/test_account_purge_ui.py` and not re-asserted here.
    assert response.status_code == 200
    assert b"Pending deletion \xe2\x80\x94 eligible for permanent purge" in response.content
    assert b"Cancel deletion" in response.content


@pytest.mark.django_db
def test_user_detail_shows_restoration_to_active_text():
    admin = create_admin("detail-admin-5")
    target = create_account("detail-restore-active-target")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))

    assert b"Cancelling will restore this account to Active." in response.content


@pytest.mark.django_db
def test_user_detail_shows_restoration_to_disabled_text():
    admin = create_admin("detail-admin-6")
    target = create_account("detail-restore-disabled-target", is_active=False)
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))

    assert (
        b"Cancelling will restore this account to Disabled because it was already disabled"
        in response.content
    )


# User detail has a purge action
# (eligible, non-self targets).
# The purge link's presence, privacy boundary, and self-purge omission are
# covered by `accounts/tests/test_account_purge_ui.py`.


# -- Schedule confirmation GET ---------------------------------------------


@pytest.mark.django_db
def test_schedule_confirmation_shows_active_target_copy():
    admin = create_admin("sched-admin")
    target = create_account("sched-active-target")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_schedule_deletion", args=[target.pk]))

    assert response.status_code == 200
    assert b"This will disable the account immediately" in response.content


@pytest.mark.django_db
def test_schedule_confirmation_shows_disabled_target_copy():
    admin = create_admin("sched-admin-2")
    target = create_account("sched-disabled-target", is_active=False)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_schedule_deletion", args=[target.pk]))

    assert response.status_code == 200
    assert b"This account is already disabled." in response.content


@pytest.mark.django_db
def test_schedule_confirmation_shows_exact_ownership_counts():
    admin = create_admin("sched-admin-3")
    target = create_account("sched-counts-target")
    Note.objects.create(
        owner=target,
        title="active-note-should-not-appear",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
    )
    Note.objects.create(
        owner=target,
        title="trashed-note-should-not-appear",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
        trashed_at=timezone.now(),
    )
    Folder.objects.create(owner=target, name="folder-should-not-appear")
    Tag.objects.create(owner=target, name="tag-should-not-appear")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_schedule_deletion", args=[target.pk]))
    content = response.content.decode()

    assert response.context["ownership_counts"].active_notes == 1
    assert response.context["ownership_counts"].trashed_notes == 1
    assert response.context["ownership_counts"].active_folders == 1
    assert response.context["ownership_counts"].trashed_folders == 0
    assert response.context["ownership_counts"].tags == 1
    assert "active-note-should-not-appear" not in content
    assert "trashed-note-should-not-appear" not in content
    assert "folder-should-not-appear" not in content
    assert "tag-should-not-appear" not in content


@pytest.mark.django_db
def test_schedule_confirmation_counts_are_fresh_not_cached():
    admin = create_admin("sched-admin-4")
    target = create_account("sched-fresh-target")
    client = authenticated_client(admin)

    first = client.get(reverse("accounts:user_schedule_deletion", args=[target.pk]))
    assert first.context["ownership_counts"].active_notes == 0

    Note.objects.create(
        owner=target, title="added", body_json=documents.EMPTY_DOCUMENT, body_plain_text=""
    )

    second = client.get(reverse("accounts:user_schedule_deletion", args=[target.pk]))
    assert second.context["ownership_counts"].active_notes == 1


@pytest.mark.django_db
def test_schedule_confirmation_cancel_link_returns_to_user_detail():
    admin = create_admin("sched-admin-5")
    target = create_account("sched-cancel-link-target")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_schedule_deletion", args=[target.pk]))

    expected_url = reverse("accounts:user_detail", args=[target.pk])
    assert expected_url.encode() in response.content


# -- Schedule POST -----------------------------------------------------------


@pytest.mark.django_db
def test_schedule_post_active_target_becomes_pending_and_inactive():
    admin = create_admin("post-admin")
    target = create_account("post-active-target")
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_schedule_deletion", args=[target.pk]))

    target.refresh_from_db()
    assert response.status_code == 302
    assert response.url == reverse("accounts:user_detail", args=[target.pk])
    assert target.deletion_scheduled_at is not None
    assert target.is_active is False


@pytest.mark.django_db
def test_schedule_post_disabled_target_becomes_pending_and_remains_inactive():
    admin = create_admin("post-admin-2")
    target = create_account("post-disabled-target", is_active=False)
    client = authenticated_client(admin)

    client.post(reverse("accounts:user_schedule_deletion", args=[target.pk]))

    target.refresh_from_db()
    assert target.deletion_scheduled_at is not None
    assert target.is_active is False
    assert target.deletion_restore_is_active is False


@pytest.mark.django_db
def test_schedule_post_success_message():
    admin = create_admin("post-admin-3")
    target = create_account("post-message-target")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_schedule_deletion", args=[target.pk]), follow=True
    )

    messages = [str(m) for m in response.context["messages"]]
    assert (
        "Deletion scheduled. The account is now disabled and recoverable for seven days."
        in messages
    )


@pytest.mark.django_db
def test_schedule_post_rejects_already_pending():
    admin = create_admin("post-admin-4")
    target = create_account("post-already-pending-target")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_schedule_deletion", args=[target.pk]), follow=True
    )

    messages = [str(m) for m in response.context["messages"]]
    assert any("already pending" in m for m in messages)


@pytest.mark.django_db
def test_schedule_post_rejects_last_active_admin_self_scheduling():
    admin = create_admin("lone-post-admin")
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_schedule_deletion", args=[admin.pk]), follow=True)

    admin.refresh_from_db()
    messages = [str(m) for m in response.context["messages"]]
    assert admin.deletion_scheduled_at is None
    assert any("last active administrator" in m.lower() for m in messages)


# -- Cancellation POST --------------------------------------------------


@pytest.mark.django_db
def test_cancel_reactivates_previously_active_target():
    admin = create_admin("cancel-admin")
    target = create_account("cancel-active-target")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_cancel_deletion", args=[target.pk]), follow=True)

    target.refresh_from_db()
    assert target.is_active is True
    assert target.deletion_scheduled_at is None
    messages = [str(m) for m in response.context["messages"]]
    assert "Deletion cancelled. The account has been reactivated." in messages


@pytest.mark.django_db
def test_cancel_leaves_previously_disabled_target_disabled():
    admin = create_admin("cancel-admin-2")
    target = create_account("cancel-disabled-target", is_active=False)
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_cancel_deletion", args=[target.pk]), follow=True)

    target.refresh_from_db()
    assert target.is_active is False
    assert target.deletion_scheduled_at is None
    messages = [str(m) for m in response.context["messages"]]
    assert "Deletion cancelled. The account remains disabled." in messages


@pytest.mark.django_db
def test_cancel_succeeds_after_deadline_has_elapsed():
    admin = create_admin("cancel-admin-3")
    target = create_account("cancel-after-deadline-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_cancel_deletion", args=[target.pk]))

    target.refresh_from_db()
    assert response.status_code == 302
    assert target.deletion_scheduled_at is None
    assert target.is_active is True


@pytest.mark.django_db
def test_cancel_rejects_non_pending_target():
    admin = create_admin("cancel-admin-4")
    target = create_account("cancel-not-pending-target")
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_cancel_deletion", args=[target.pk]), follow=True)

    messages = [str(m) for m in response.context["messages"]]
    assert any("not pending" in m.lower() for m in messages)


@pytest.mark.django_db
def test_cancel_redirects_to_user_detail():
    admin = create_admin("cancel-admin-5")
    target = create_account("cancel-redirect-target")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_cancel_deletion", args=[target.pk]))

    assert response.url == reverse("accounts:user_detail", args=[target.pk])


# -- User list ---------------------------------------------------------


@pytest.mark.django_db
def test_user_list_blank_deletion_cell_for_ordinary_account():
    admin = create_admin("list-admin")
    create_account("list-ordinary-target")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))

    assert response.status_code == 200
    assert b"Pending" not in _deletion_cell(response.content)


@pytest.mark.django_db
def test_user_list_shows_pending_recoverable():
    admin = create_admin("list-admin-2")
    target = create_account("list-recoverable-target")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))

    assert b"Pending \xe2\x80\x94 recoverable" in response.content


@pytest.mark.django_db
def test_user_list_shows_pending_purge_eligible():
    admin = create_admin("list-admin-3")
    target = create_account("list-eligible-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))

    assert b"Pending \xe2\x80\x94 purge eligible" in response.content


@pytest.mark.django_db
def test_user_list_has_no_quick_action_links_for_deletion():
    admin = create_admin("list-admin-4")
    target = create_account("list-no-links-target")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))
    content = response.content.decode()

    assert "schedule-deletion" not in content
    assert "cancel-deletion" not in content


# -- Regression ----------------------------------------------------------


@pytest.mark.django_db
def test_existing_role_action_still_works():
    admin = create_admin("regress-admin")
    target = create_account("regress-role-target")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_role", args=[target.pk]), {"role": User.ROLE_ADMIN}
    )

    target.refresh_from_db()
    assert response.status_code == 302
    assert target.role == User.ROLE_ADMIN


@pytest.mark.django_db
def test_existing_active_action_still_works():
    admin = create_admin("regress-admin-2")
    target = create_account("regress-active-target")
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_active", args=[target.pk]), {"is_active": ""})

    target.refresh_from_db()
    assert response.status_code == 302
    assert target.is_active is False


@pytest.mark.django_db
def test_password_reset_route_still_works():
    admin = create_admin("regress-admin-3")
    target = create_account("regress-reset-target")
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_password_reset", args=[target.pk]))

    assert response.status_code == 200


# `accounts:user_purge` and
# `PurgeUserAccountForm` coverage lives in
# `accounts/tests/test_account_purge_ui.py`.
