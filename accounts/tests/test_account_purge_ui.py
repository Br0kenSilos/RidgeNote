"""Permanent account-purge administrator UI and typed
username confirmation.

Covers `PurgeUserAccountForm`, the `user_purge` view/route, the
`user_purge_confirm.html` template, and the eligible-branch behavior of
`user_detail.html`. Permanent-purge locking, actor revalidation, cascade,
and audit correctness are covered by
`accounts/tests/test_account_purge.py` and are not duplicated
here. Scheduling/cancellation UI regression is covered lightly to confirm
that behavior is unaffected by permanent purge; its own full coverage remains in
`accounts/tests/test_account_deletion_ui.py`.
"""

import re
from datetime import timedelta

import pytest
from django.db import connection
from django.test import Client
from django.urls import reverse
from django.utils import timezone
from notes import documents
from notes.models import Folder, Note, Tag

from accounts import services
from accounts.forms import PurgeUserAccountForm
from accounts.models import AuditEvent, User
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


def _purge_panel(content: str) -> str:
    # Scopes assertions to the
    # purge-confirmation page's own panel rather than the whole response
    # body, since the site-wide Help panel (embedded on every page)
    # legitimately reuses "Permanently purge" wording elsewhere in the
    # same document.
    start = content.index('<section class="panel"')
    end = content.index("</section>", start)
    return content[start:end]


def _force_malformed_lifecycle(user):
    with connection.cursor() as cursor:
        cursor.execute(
            "ALTER TABLE accounts_user DROP CONSTRAINT "
            "accounts_user_deletion_restore_flag_paired_ck"
        )
        cursor.execute(
            "UPDATE accounts_user SET deletion_restore_is_active = NULL WHERE id = %s",
            [user.pk],
        )
        cursor.execute(
            "ALTER TABLE accounts_user ADD CONSTRAINT "
            "accounts_user_deletion_restore_flag_paired_ck CHECK ("
            "(deletion_scheduled_at IS NULL AND deletion_restore_is_active IS NULL) "
            "OR (deletion_scheduled_at IS NOT NULL AND deletion_restore_is_active IS NOT NULL)"
            ") NOT VALID"
        )


# -- Form -------------------------------------------------------------


@pytest.mark.django_db
def test_form_exact_match_succeeds():
    target = create_account("form-target")
    form = PurgeUserAccountForm({"confirm_username": "form-target"}, target=target)
    assert form.is_valid()
    assert form.cleaned_data["confirm_username"] == "form-target"


@pytest.mark.django_db
def test_form_mismatch_fails_with_exact_error():
    target = create_account("form-target-2")
    form = PurgeUserAccountForm({"confirm_username": "wrong-name"}, target=target)
    assert not form.is_valid()
    assert form.errors["confirm_username"] == [
        "The typed username did not match. Type the account's exact username to confirm."
    ]


@pytest.mark.django_db
def test_form_trims_surrounding_whitespace():
    target = create_account("form-target-3")
    form = PurgeUserAccountForm({"confirm_username": "  form-target-3  "}, target=target)
    assert form.is_valid()


@pytest.mark.django_db
def test_form_accepts_canonical_case_equivalent():
    target = create_account("form-target-4")
    form = PurgeUserAccountForm({"confirm_username": "FORM-TARGET-4"}, target=target)
    assert form.is_valid()


@pytest.mark.django_db
def test_form_rejects_empty_input():
    target = create_account("form-target-5")
    form = PurgeUserAccountForm({"confirm_username": ""}, target=target)
    assert not form.is_valid()
    assert "confirm_username" in form.errors


@pytest.mark.django_db
def test_form_exact_label_and_widget_attributes():
    target = create_account("form-target-6")
    form = PurgeUserAccountForm(target=target)
    field = form.fields["confirm_username"]
    assert field.label == "Type the username to confirm"
    assert field.widget.attrs["autocomplete"] == "off"
    assert field.widget.attrs["spellcheck"] == "false"
    assert field.widget.attrs["autocapitalize"] == "off"


@pytest.mark.django_db
def test_form_stores_target_for_validation_only():
    target = create_account("form-target-7")
    form = PurgeUserAccountForm(target=target)
    assert form.target is target


# -- Authorization and methods -----------------------------------------


@pytest.mark.django_db
def test_purge_get_requires_login():
    target = create_account("auth-target")
    response = Client().get(reverse("accounts:user_purge", args=[target.pk]))
    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_purge_post_requires_login():
    target = create_account("auth-target-post")
    response = Client().post(reverse("accounts:user_purge", args=[target.pk]))
    assert response.status_code == 302
    assert "/login/" in response.url


@pytest.mark.django_db
def test_purge_get_forbidden_for_non_admin():
    user = create_account("non-admin-user")
    target = create_account("non-admin-target")
    client = authenticated_client(user)
    response = client.get(reverse("accounts:user_purge", args=[target.pk]))
    assert response.status_code == 403


@pytest.mark.django_db
def test_purge_post_forbidden_for_non_admin():
    user = create_account("non-admin-user-2")
    target = create_account("non-admin-target-2")
    client = authenticated_client(user)
    response = client.post(reverse("accounts:user_purge", args=[target.pk]))
    assert response.status_code == 403


@pytest.mark.django_db
def test_purge_get_works_for_eligible_target():
    admin = create_admin("method-admin")
    target = create_account("method-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)
    response = client.get(reverse("accounts:user_purge", args=[target.pk]))
    assert response.status_code == 200


@pytest.mark.django_db
def test_purge_post_works_for_eligible_target():
    admin = create_admin("method-admin-2")
    target = create_account("method-target-2")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)
    response = client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "method-target-2"},
    )
    assert response.status_code == 302


@pytest.mark.django_db
def test_purge_rejects_unsupported_method():
    admin = create_admin("method-admin-3")
    target = create_account("method-target-3")
    client = authenticated_client(admin)
    response = client.delete(reverse("accounts:user_purge", args=[target.pk]))
    assert response.status_code == 405


@pytest.mark.django_db
def test_purge_route_reverses_correctly():
    assert reverse("accounts:user_purge", args=[42]) == "/admin/users/42/purge/"


@pytest.mark.django_db
def test_purge_get_missing_target_404():
    admin = create_admin("method-admin-4")
    client = authenticated_client(admin)
    response = client.get(reverse("accounts:user_purge", args=[999999]))
    assert response.status_code == 404


# -- Confirmation GET ----------------------------------------------------


@pytest.mark.django_db
def test_get_eligible_non_self_shows_form_and_submit_button():
    admin = create_admin("get-admin")
    target = create_account("get-eligible-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_purge", args=[target.pk]))
    content = response.content.decode()

    assert response.status_code == 200
    assert response.context["form"] is not None
    assert "confirm_username" in content
    assert "Permanently purge" in content


@pytest.mark.django_db
def test_get_pre_deadline_target_redirects_with_no_form():
    admin = create_admin("get-admin-2")
    target = create_account("get-pending-target")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_purge", args=[target.pk]), follow=True)

    assert response.redirect_chain[-1][0] == reverse("accounts:user_detail", args=[target.pk])
    messages = [str(m) for m in response.context["messages"]]
    assert any("not yet eligible" in m.lower() for m in messages)


@pytest.mark.django_db
def test_get_self_target_shows_explanatory_text_no_field_no_button(monkeypatch):
    # A purge-eligible account is, by construction, already `is_active=False`
    # (a purge precondition). That blocks this scenario at two separate
    # layers: `admin_required()` itself, and -- one layer deeper --
    # Django's own `ModelBackend.get_user()`, which silently refuses to
    # authenticate an inactive user at all (turning `request.user` into
    # `AnonymousUser` regardless of a valid session), the identical
    # unreachable-via-real-session contradiction already established for
    # other self-action edge cases. Bypassing both here tests the
    # view's own self-branch rendering in isolation.
    admin = create_admin("get-self-admin")
    create_admin("get-self-admin-bystander")
    _schedule_and_make_eligible(admin, actor=admin)
    monkeypatch.setattr("accounts.views.admin_required", lambda request: None)
    monkeypatch.setattr(
        "django.contrib.auth.backends.ModelBackend.user_can_authenticate",
        lambda self, user: True,
    )
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_purge", args=[admin.pk]))
    content = response.content.decode()

    assert response.status_code == 200
    assert response.context["form"] is None
    panel = _purge_panel(content)
    assert (
        "You cannot purge your own account. Another administrator must perform this action."
        in panel
    )
    assert "confirm_username" not in panel
    assert "Permanently purge<" not in panel
    assert '<button type="submit" class="button-link--danger">' not in panel


@pytest.mark.django_db
def test_get_shows_exact_five_counts():
    admin = create_admin("get-admin-3")
    target = create_account("get-counts-target")
    Note.objects.create(
        owner=target, title="n1", body_json=documents.EMPTY_DOCUMENT, body_plain_text=""
    )
    Note.objects.create(
        owner=target,
        title="n2",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="",
        trashed_at=timezone.now(),
    )
    Folder.objects.create(owner=target, name="f1")
    Tag.objects.create(owner=target, name="t1")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_purge", args=[target.pk]))

    counts = response.context["ownership_counts"]
    assert counts.active_notes == 1
    assert counts.trashed_notes == 1
    assert counts.active_folders == 1
    assert counts.trashed_folders == 0
    assert counts.tags == 1


@pytest.mark.django_db
def test_get_shows_scheduled_timestamp_and_deadline():
    admin = create_admin("get-admin-4")
    target = create_account("get-timestamps-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_purge", args=[target.pk]))
    content = response.content.decode()

    assert "Deletion scheduled" in content
    assert "Recovery deadline" in content


@pytest.mark.django_db
def test_get_shows_all_required_copy():
    admin = create_admin("get-admin-5")
    target = create_account("get-copy-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_purge", args=[target.pk]))
    content = response.content.decode()

    assert (
        "This action is permanent and cannot be undone. The account can no longer be cancelled "
        "or recovered after purge completes." in content
    )
    assert (
        "This will permanently delete the user account and all of its owned notes, folders, "
        "tags, and their associations." in content
    )
    assert (
        "The username will become available for reuse immediately after this account is purged."
        in content
    )
    assert (
        "Historical audit records will retain a text snapshot of the username after the account "
        "is removed." in content
    )
    assert (
        "Consider confirming that a recent backup exists before proceeding. This action cannot "
        "be undone." in content
    )


@pytest.mark.django_db
def test_get_has_no_hidden_count_inputs():
    admin = create_admin("get-admin-6")
    target = create_account("get-hidden-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_purge", args=[target.pk]))
    content = response.content.decode()

    # A CSRF hidden input is expected and safe; only ownership-count fields
    # must never be submitted as hidden inputs.
    for count_field_name in (
        "active_notes",
        "trashed_notes",
        "active_folders",
        "trashed_folders",
        "tags",
    ):
        assert f'name="{count_field_name}"' not in content


@pytest.mark.django_db
def test_get_privacy_excludes_recognizable_private_data():
    admin = create_admin("get-admin-7")
    target = create_account("get-privacy-target")
    Note.objects.create(
        owner=target,
        title="SecretNoteTitleXYZ",
        body_json=documents.EMPTY_DOCUMENT,
        body_plain_text="secret body content",
    )
    Folder.objects.create(owner=target, name="SecretFolderNameXYZ")
    Tag.objects.create(owner=target, name="SecretTagNameXYZ")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_purge", args=[target.pk]))
    content = response.content.decode()

    assert "SecretNoteTitleXYZ" not in content
    assert "secret body content" not in content
    assert "SecretFolderNameXYZ" not in content
    assert "SecretTagNameXYZ" not in content


# -- POST -----------------------------------------------------------------


@pytest.mark.django_db
def test_post_correct_username_purges_successfully():
    admin = create_admin("post-admin")
    target = create_account("post-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "post-target"},
        follow=True,
    )

    assert not User.objects.filter(pk=target.pk).exists()
    messages = [str(m) for m in response.context["messages"]]
    assert "Account permanently purged." in messages
    assert response.redirect_chain[-1][0] == reverse("accounts:user_list")


@pytest.mark.django_db
def test_post_mismatch_rerenders_and_does_not_purge():
    admin = create_admin("post-admin-2")
    target = create_account("post-target-2")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "wrong"},
    )

    assert response.status_code == 200
    assert User.objects.filter(pk=target.pk).exists()
    assert response.context["form"].errors


@pytest.mark.django_db
def test_post_whitespace_padded_username_succeeds():
    admin = create_admin("post-admin-3")
    target = create_account("post-target-3")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "  post-target-3  "},
    )

    assert not User.objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_post_canonical_case_equivalent_succeeds():
    admin = create_admin("post-admin-4")
    target = create_account("post-target-4")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "POST-TARGET-4"},
    )

    assert not User.objects.filter(pk=target.pk).exists()


@pytest.mark.django_db
def test_post_pre_deadline_rejection_maps_to_message_and_redirect():
    admin = create_admin("post-admin-5")
    target = create_account("post-target-5")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "post-target-5"},
        follow=True,
    )

    assert User.objects.filter(pk=target.pk).exists()
    assert response.redirect_chain[-1][0] == reverse("accounts:user_detail", args=[target.pk])
    messages = [str(m) for m in response.context["messages"]]
    assert any("has not yet elapsed" in m.lower() for m in messages)


@pytest.mark.django_db
def test_post_crafted_self_purge_maps_safely():
    # `SelfPurgeNotAllowedError` is only reachable while the actor is still
    # active: the service checks `locked_actor.pk == locked_target.pk`
    # before ever checking whether the target is pending deletion, so a
    # still-active administrator directly crafting a POST to their own
    # (not-yet-scheduled) purge route hits this exact rejection -- no
    # scheduling or bystander administrator is needed for this path.
    admin = create_admin("post-self-admin")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_purge", args=[admin.pk]),
        {"confirm_username": "post-self-admin"},
        follow=True,
    )

    assert User.objects.filter(pk=admin.pk).exists()
    assert response.redirect_chain[-1][0] == reverse("accounts:user_detail", args=[admin.pk])
    messages = [str(m) for m in response.context["messages"]]
    assert any("cannot purge their own account" in m.lower() for m in messages)


@pytest.mark.django_db
def test_post_not_pending_rejection_maps_safely():
    admin = create_admin("post-admin-6")
    target = create_account("post-target-6")
    client = authenticated_client(admin)

    response = client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "post-target-6"},
        follow=True,
    )

    assert User.objects.filter(pk=target.pk).exists()
    messages = [str(m) for m in response.context["messages"]]
    assert any("not pending" in m.lower() for m in messages)


@pytest.mark.django_db
def test_post_target_vanished_during_service_call_becomes_404(monkeypatch):
    admin = create_admin("post-admin-7")
    target = create_account("post-target-7")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    def _raise_does_not_exist(**kwargs):
        raise User.DoesNotExist

    monkeypatch.setattr(services, "purge_user_account", _raise_does_not_exist)

    response = client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "post-target-7"},
    )

    assert response.status_code == 404


@pytest.mark.django_db
def test_post_actor_unauthorized_maps_safely(monkeypatch):
    # `admin_required()` itself would already block a request from an actor
    # who is inactive/demoted by the time Django loads `request.user` for
    # that request -- the only way `PurgeActorNotAuthorizedError` reaches
    # `user_purge()` at all is a genuine mid-request race (the actor's own
    # row changing between `admin_required()`'s check and the service's own
    # locked re-fetch), which is exactly what
    # `accounts/tests/test_account_purge.py`'s real two-connection
    # concurrency tests exercise. Bypassing `admin_required()` here
    # while leaving the actor genuinely inactive at the database level lets
    # this test verify the view's exception-to-message mapping in isolation,
    # without re-implementing that concurrency machinery.
    admin = create_admin("post-admin-8")
    other_admin = create_admin("post-admin-8-authorizer")
    target = create_account("post-target-8")
    _schedule_and_make_eligible(target, actor=other_admin)
    client = authenticated_client(admin)

    admin.is_active = False
    admin.save(update_fields=["is_active"])
    monkeypatch.setattr("accounts.views.admin_required", lambda request: None)

    response = client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "post-target-8"},
        follow=True,
    )

    assert User.objects.filter(pk=target.pk).exists()
    messages = [str(m) for m in response.context["messages"]]
    assert any("no longer authorized" in m.lower() for m in messages)


@pytest.mark.django_db
def test_post_malformed_lifecycle_propagates_as_server_error():
    admin = create_admin("post-admin-9")
    target = create_account("post-target-9")
    _schedule_and_make_eligible(target, actor=admin)
    _force_malformed_lifecycle(target)
    client = authenticated_client(admin)
    client.raise_request_exception = False

    response = client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "post-target-9"},
    )

    assert response.status_code == 500
    assert User.objects.filter(pk=target.pk).exists()
    assert AuditEvent.objects.filter(event_type=AuditEvent.EVENT_ACCOUNT_PURGE_FAILED).exists()


@pytest.mark.django_db
def test_post_ignores_forged_count_fields():
    admin = create_admin("post-admin-10")
    target = create_account("post-target-10")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    client.post(
        reverse("accounts:user_purge", args=[target.pk]),
        {"confirm_username": "post-target-10", "active_notes": "999", "tags": "999"},
    )

    assert not User.objects.filter(pk=target.pk).exists()
    event = AuditEvent.objects.get(event_type=AuditEvent.EVENT_ACCOUNT_PERMANENTLY_PURGED)
    assert event.details["notes_active"] == 0
    assert event.details["tags"] == 0


# -- User detail ----------------------------------------------------------


@pytest.mark.django_db
def test_user_detail_shows_purge_link_for_eligible_non_self():
    admin = create_admin("detail-admin")
    target = create_account("detail-eligible-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))
    content = response.content.decode()

    expected_url = reverse("accounts:user_purge", args=[target.pk])
    assert expected_url in content
    assert "button-link--danger" in content
    assert "Permanently purge" in content


@pytest.mark.django_db
def test_user_detail_shows_no_purge_link_pre_deadline():
    admin = create_admin("detail-admin-2")
    target = create_account("detail-pending-target")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[target.pk]))
    content = response.content.decode()

    assert "accounts:user_purge" not in content
    assert reverse("accounts:user_purge", args=[target.pk]) not in content


@pytest.mark.django_db
def test_user_detail_self_eligible_shows_no_purge_link(monkeypatch):
    # Same reachability note as the purge-confirmation self-target GET test:
    # a purge-eligible account is already inactive, blocked both by
    # `admin_required()` and by Django's own `ModelBackend.get_user()` --
    # both are bypassed here to isolate `user_detail`'s own self-branch
    # presentation.
    admin = create_admin("detail-self-admin")
    create_admin("detail-self-admin-bystander")
    _schedule_and_make_eligible(admin, actor=admin)
    monkeypatch.setattr("accounts.views.admin_required", lambda request: None)
    monkeypatch.setattr(
        "django.contrib.auth.backends.ModelBackend.user_can_authenticate",
        lambda self, user: True,
    )
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_detail", args=[admin.pk]))
    content = response.content.decode()

    assert reverse("accounts:user_purge", args=[admin.pk]) not in content
    assert (
        "You cannot purge your own account. Another administrator must perform this action."
        in content
    )


# -- User list / scheduling-cancellation regression -------------------------


@pytest.mark.django_db
def test_user_list_remains_action_free():
    admin = create_admin("list-admin")
    target = create_account("list-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))
    content = response.content.decode()

    # The "Pending -- purge eligible" status text is expected; only an
    # actionable purge link/route must be absent.
    assert reverse("accounts:user_purge", args=[target.pk]) not in content
    assert "schedule-deletion" not in content
    assert "cancel-deletion" not in content


@pytest.mark.django_db
def test_scheduling_still_works():
    admin = create_admin("regress-admin")
    target = create_account("regress-target")
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_schedule_deletion", args=[target.pk]))

    target.refresh_from_db()
    assert response.status_code == 302
    assert target.deletion_scheduled_at is not None


@pytest.mark.django_db
def test_cancellation_still_works():
    admin = create_admin("regress-admin-2")
    target = create_account("regress-target-2")
    services.schedule_user_deletion(target=target, actor=admin)
    client = authenticated_client(admin)

    response = client.post(reverse("accounts:user_cancel_deletion", args=[target.pk]))

    target.refresh_from_db()
    assert response.status_code == 302
    assert target.deletion_scheduled_at is None


def _script_signatures(content):
    """Return a set identifying each `<script>` tag's origin.

    External scripts are identified by their `src`; inline scripts by
    their (static, request-independent) body text. This lets two pages
    sharing the same authenticated base shell be compared for exactly
    which scripts they carry, without depending on how many the shell
    happens to render today.
    """
    signatures = set()
    for tag in re.findall(r"<script\b[^>]*>.*?</script>", content, re.DOTALL):
        src_match = re.search(r'src="([^"]*)"', tag)
        if src_match:
            signatures.add(("src", src_match.group(1)))
        else:
            inline_body = re.sub(r"^<script\b[^>]*>|</script>$", "", tag, flags=re.DOTALL)
            signatures.add(("inline", inline_body.strip()))
    return signatures


@pytest.mark.django_db
def test_purge_confirm_template_adds_no_feature_specific_script():
    # The authenticated base shell (`core/templates/base.html`) already
    # renders its own scripts on every authenticated page -- the bundled
    # app.js module, plus a session-theme bootstrap script for signed-in
    # users. This asserts the purge-confirmation page contributes exactly
    # those inherited scripts and nothing purge-specific, by comparing its
    # script signatures against another authenticated admin page that
    # shares the same base shell -- rather than asserting a fixed total
    # count, which broke the first time an unrelated, legitimate shell
    # script (the theme bootstrap) was added.
    admin = create_admin("no-js-admin")
    target = create_account("no-js-target")
    _schedule_and_make_eligible(target, actor=admin)
    client = authenticated_client(admin)

    baseline_response = client.get(reverse("accounts:user_list"))
    baseline_signatures = _script_signatures(baseline_response.content.decode())

    response = client.get(reverse("accounts:user_purge", args=[target.pk]))
    content = response.content.decode()
    purge_signatures = _script_signatures(content)

    assert purge_signatures, "expected the inherited base-shell scripts to be present"
    assert purge_signatures == baseline_signatures, (
        "purge-confirmation page must not add or omit any script relative to "
        "another authenticated page sharing the same base shell"
    )
