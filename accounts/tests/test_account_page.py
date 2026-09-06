"""Account page foundation.

Covers `AccountPasswordChangeForm`, `services.complete_account_password_change()`,
and the `/account/` view: a read-only identity summary plus a self-service
password-change form that reuses `complete_forced_password_change()`'s
locked-update/session-hash/audit mechanics via the shared
`_lock_and_set_password()` helper. The account-menu link itself (all three
responsive copies) is covered in `notes/tests/test_notes.py`, alongside the
rest of that menu's existing coverage.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.forms import AccountPasswordChangeForm
from accounts.models import AuditEvent, User

PASSWORD = "LongUniquePassword123!"
OTHER_PASSWORD = "AnotherLongPassword123!"


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
    session[services.SESSION_GENERATION_KEY] = user.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def password_payload(current=PASSWORD, new=OTHER_PASSWORD):
    return {
        "account_action": "password",
        "current_password": current,
        "password1": new,
        "password2": new,
    }


# ---------------------------------------------------------------------------
# Form
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_account_password_change_form_valid_with_correct_current_password():
    user = create_account("form-valid-user")

    form = AccountPasswordChangeForm(password_payload(), user=user)

    assert form.is_valid()


@pytest.mark.django_db
def test_account_password_change_form_rejects_incorrect_current_password():
    user = create_account("form-wrong-current-user")

    form = AccountPasswordChangeForm(password_payload(current="not-the-real-password"), user=user)

    assert not form.is_valid()
    assert "Your current password was incorrect." in form.errors["current_password"]


@pytest.mark.django_db
def test_account_password_change_form_rejects_weak_new_password():
    user = create_account("form-weak-new-user")

    form = AccountPasswordChangeForm(password_payload(new="123"), user=user)

    assert not form.is_valid()
    # Reuses `PasswordConfirmationMixin.
    # validate_password_for_user()` unchanged from `ForcedPasswordChangeForm`
    # -- Django's password validators raise from `clean()`, so their
    # errors land as non-field errors, not attached to `password2`.
    assert form.non_field_errors()


@pytest.mark.django_db
def test_account_password_change_form_rejects_mismatched_confirmation():
    user = create_account("form-mismatch-user")

    form = AccountPasswordChangeForm(
        {
            "current_password": PASSWORD,
            "password1": OTHER_PASSWORD,
            "password2": "SomethingElse123!",
        },
        user=user,
    )

    assert not form.is_valid()
    assert "The two password fields did not match." in form.errors["password2"]


@pytest.mark.django_db
def test_account_password_change_form_allows_same_as_current_password():
    # No configured AUTH_PASSWORD_VALIDATORS rejects reusing the current
    # password -- confirming actual configured behavior, not an assumption.
    user = create_account("form-same-as-current-user")

    form = AccountPasswordChangeForm(password_payload(current=PASSWORD, new=PASSWORD), user=user)

    assert form.is_valid()


@pytest.mark.django_db
def test_account_password_change_form_uses_password_manager_autocomplete_hints():
    user = create_account("form-autocomplete-user")

    form = AccountPasswordChangeForm(user=user)

    assert form.fields["current_password"].widget.attrs["autocomplete"] == "current-password"
    assert form.fields["password1"].widget.attrs["autocomplete"] == "new-password"
    assert form.fields["password2"].widget.attrs["autocomplete"] == "new-password"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_complete_account_password_change_updates_password():
    from django.test import RequestFactory

    user = create_account("service-update-user")
    request = RequestFactory().post("/account/")
    request.session = authenticated_client(user).session
    request.user = user

    services.complete_account_password_change(
        user=user, new_password=OTHER_PASSWORD, request=request
    )

    user.refresh_from_db()
    assert check_password(OTHER_PASSWORD, user.password)


@pytest.mark.django_db
def test_complete_account_password_change_does_not_touch_must_change_password():
    from django.test import RequestFactory

    user = create_account("service-preserves-flag-user", must_change_password=False)
    request = RequestFactory().post("/account/")
    request.session = authenticated_client(user).session
    request.user = user

    services.complete_account_password_change(
        user=user, new_password=OTHER_PASSWORD, request=request
    )

    user.refresh_from_db()
    assert user.must_change_password is False


@pytest.mark.django_db
def test_complete_account_password_change_creates_expected_audit_event():
    from django.test import RequestFactory

    user = create_account("service-audit-user")
    request = RequestFactory().post("/account/")
    request.session = authenticated_client(user).session
    request.user = user

    services.complete_account_password_change(
        user=user, new_password=OTHER_PASSWORD, request=request
    )

    event = AuditEvent.objects.get(
        event_type=AuditEvent.EVENT_ACCOUNT_PASSWORD_CHANGE_COMPLETED,
    )
    assert event.actor_id == user.id
    assert event.target_user_id == user.id
    # No password material of any kind in the audit payload.
    assert "password" not in str(event.details).lower()
    assert OTHER_PASSWORD not in str(event.details)


@pytest.mark.django_db
def test_forced_password_change_still_clears_must_change_password_flag():
    # Regression guard: the shared `_lock_and_set_password()` extraction
    # must not have changed complete_forced_password_change()'s own
    # contract.
    from django.test import RequestFactory

    user = create_account("service-forced-still-works-user", must_change_password=True)
    request = RequestFactory().post("/password/change-required/")
    request.session = authenticated_client(user).session
    request.user = user

    services.complete_forced_password_change(
        user=user, new_password=OTHER_PASSWORD, request=request
    )

    user.refresh_from_db()
    assert user.must_change_password is False
    assert check_password(OTHER_PASSWORD, user.password)
    assert AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_FORCED_PASSWORD_CHANGE_COMPLETED,
        target_user=user,
    ).exists()


# ---------------------------------------------------------------------------
# View
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_account_page_redirects_anonymous_users():
    response = Client().get(reverse("accounts:account"))

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url


@pytest.mark.django_db
def test_account_page_get_shows_identity_summary():
    user = create_account("view-identity-user", display_name="Identity User")

    response = authenticated_client(user).get(reverse("accounts:account"))
    content = response.content.decode()

    assert response.status_code == 200
    assert "view-identity-user" in content
    assert "Identity User" in content
    assert "User" in content  # role display


@pytest.mark.django_db
def test_account_page_never_renders_a_raw_template_comment():
    # Regression guard: `{# ... #}` Django comments do not support
    # multi-line content -- a multi-line one previously rendered as
    # literal text (delimiters included) instead of being parsed away.
    # Covers both the clean GET render and a failed POST, since the
    # comment sat directly above the password fields in both.
    user = create_account("view-no-raw-comment-user")
    client = authenticated_client(user)

    get_content = client.get(reverse("accounts:account")).content.decode()
    post_content = client.post(
        reverse("accounts:account"), password_payload(current="not-the-real-password")
    ).content.decode()

    for content in (get_content, post_content):
        assert "{#" not in content
        assert "#}" not in content

    # The actual fix this comment used to describe must still work.
    assert 'class="messages__item messages__item--error"' in post_content
    assert '<div class="visually-hidden" id="id_current_password_error">' in post_content


@pytest.mark.django_db
def test_account_page_shows_empty_display_name_field_when_not_set():
    # The summary `<dl>` does not conditionally
    # show a Display name row -- Display Name is always rendered as
    # an editable field (blank when unset), so this only needs to prove
    # the input starts empty, not that the whole row disappears.
    user = create_account("view-no-display-name-user", display_name="")

    response = authenticated_client(user).get(reverse("accounts:account"))
    content = response.content.decode()

    assert "Display name" in content
    assert 'id="id_display_name"' in content
    assert 'name="display_name" value="view-no-display-name-user"' not in content


@pytest.mark.django_db
def test_account_page_shows_never_for_absent_last_login():
    user = create_account("view-never-login-user")
    assert user.last_successful_login_at is None

    response = authenticated_client(user).get(reverse("accounts:account"))
    content = response.content.decode()

    assert "Never" in content


@pytest.mark.django_db
def test_account_page_shows_admin_role_for_administrators():
    admin = create_account("view-admin-role-user", role=User.ROLE_ADMIN)

    response = authenticated_client(admin).get(reverse("accounts:account"))
    content = response.content.decode()

    assert "Admin" in content


@pytest.mark.django_db
def test_account_page_password_change_success_redirects_and_preserves_session():
    user = create_account("view-success-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), password_payload())

    assert response.status_code == 302
    assert response.url == reverse("accounts:account")
    user.refresh_from_db()
    assert check_password(OTHER_PASSWORD, user.password)
    # Session remains authenticated -- no forced re-login.
    assert client.get(reverse("accounts:session_status")).status_code == 200


@pytest.mark.django_db
def test_account_page_password_change_success_shows_message():
    user = create_account("view-success-message-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), password_payload(), follow=True)

    assert b"Your password has been changed." in response.content


@pytest.mark.django_db
def test_account_page_old_password_rejected_and_new_password_accepted_after_success():
    # An admin must exist for `accounts:login` to render the login form at
    # all -- otherwise `login_view` redirects everyone to `/setup/`,
    # unrelated to the credentials under test here.
    create_account("view-login-swap-admin", role=User.ROLE_ADMIN)
    user = create_account("view-login-swap-user")
    authenticated_client(user).post(reverse("accounts:account"), password_payload())

    old_login = Client().post(
        reverse("accounts:login"), {"username": "view-login-swap-user", "password": PASSWORD}
    )
    new_login = Client().post(
        reverse("accounts:login"),
        {"username": "view-login-swap-user", "password": OTHER_PASSWORD},
    )

    assert old_login.status_code == 200  # re-renders the login form, no redirect
    assert new_login.status_code == 302


@pytest.mark.django_db
def test_account_page_incorrect_current_password_does_not_change_password_or_session():
    user = create_account("view-wrong-current-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:account"),
        password_payload(current="not-the-real-password"),
        follow=True,
    )
    content = response.content.decode()

    assert response.status_code == 200
    user.refresh_from_db()
    assert check_password(PASSWORD, user.password)
    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ACCOUNT_PASSWORD_CHANGE_COMPLETED,
    ).exists()
    # The failure
    # surfaces through the existing site-wide error-messages banner (same
    # `.messages__item--error` component used everywhere else in this
    # app) exactly once, plus one screen-reader-only echo associated with
    # the field via `aria-describedby` (so that attribute, which Django
    # adds automatically whenever a field has errors, never references a
    # nonexistent element) -- never a second *visible* copy.
    assert 'class="messages__item messages__item--error"' in content
    assert content.count("Your current password was incorrect.") == 2
    assert '<div class="visually-hidden" id="id_current_password_error">' in content
    assert 'id="id_current_password"' in content
    assert 'aria-invalid="true"' in content
    assert 'aria-describedby="id_current_password_error"' in content


@pytest.mark.django_db
def test_account_page_weak_new_password_does_not_change_password():
    user = create_account("view-weak-password-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), password_payload(new="123"), follow=True)
    content = response.content.decode()

    assert response.status_code == 200
    user.refresh_from_db()
    assert check_password(PASSWORD, user.password)
    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ACCOUNT_PASSWORD_CHANGE_COMPLETED,
    ).exists()
    assert 'class="messages__item messages__item--error"' in content
    # Weak-password
    # messages are non-field errors (raised from `clean()`, not attached
    # to `password2`) -- `form.non_field_errors` is no longer rendered on
    # this page at all, so each message now appears exactly once, in the
    # top banner, not also as an inline "errorlist nonfield" bullet list.
    assert content.count("too short") == 1
    assert content.count("too common") == 1
    assert content.count("entirely numeric") == 1
    assert "errorlist nonfield" not in content


@pytest.mark.django_db
def test_account_page_mismatched_confirmation_does_not_change_password():
    user = create_account("view-mismatch-password-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:account"),
        {
            "account_action": "password",
            "current_password": PASSWORD,
            "password1": OTHER_PASSWORD,
            "password2": "SomethingElse123!",
        },
        follow=True,
    )
    content = response.content.decode()

    assert response.status_code == 200
    user.refresh_from_db()
    assert check_password(PASSWORD, user.password)
    assert 'class="messages__item messages__item--error"' in content
    # Once in the banner, once in the screen-reader-only echo associated
    # with password2 via aria-describedby -- never a third, visible copy.
    assert content.count("The two password fields did not match.") == 2
    assert '<div class="visually-hidden" id="id_password2_error">' in content
    assert 'aria-describedby="id_password2_error"' in content


@pytest.mark.django_db
def test_account_page_error_banner_supports_multiple_simultaneous_errors():
    # Incorrect current password *and* a weak new password at once --
    # both must appear, each as its own list item, distinct from the
    # green success banner's class.
    user = create_account("view-multi-error-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:account"),
        {
            "account_action": "password",
            "current_password": "not-the-real-password",
            "password1": "123",
            "password2": "123",
        },
        follow=True,
    )
    content = response.content.decode()

    assert response.content.count(b'class="messages__item messages__item--error"') >= 2
    assert "Your current password was incorrect." in content
    assert "too short" in content
    assert "messages__item--success" not in content


@pytest.mark.django_db
def test_account_page_does_not_repopulate_password_fields_on_error():
    user = create_account("view-no-repopulate-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:account"), password_payload(current="not-the-real-password")
    )
    content = response.content.decode()

    assert "not-the-real-password" not in content
    assert OTHER_PASSWORD not in content


@pytest.mark.django_db
def test_account_page_ignores_any_user_id_submitted_in_post():
    owner = create_account("view-owner-user")
    other = create_account("view-other-user")
    client = authenticated_client(owner)

    payload = password_payload()
    payload["user_id"] = other.pk
    payload["user"] = other.pk
    response = client.post(reverse("accounts:account"), payload)

    assert response.status_code == 302
    owner.refresh_from_db()
    other.refresh_from_db()
    assert check_password(OTHER_PASSWORD, owner.password)
    assert check_password(PASSWORD, other.password)  # unaffected


@pytest.mark.django_db
def test_account_page_does_not_bypass_forced_password_change_requirement():
    user = create_account("view-forced-gate-user", must_change_password=True)
    client = authenticated_client(user)

    response = client.get(reverse("accounts:account"))

    assert response.status_code == 302
    assert response.url == reverse("accounts:forced_password_change")


@pytest.mark.django_db
def test_forced_password_change_page_still_shows_visible_inline_errors():
    # Regression guard: `_form_field.html`'s new `hide_visible_errors`
    # option is opt-in -- every other caller, including this one (which
    # reuses the same `PasswordConfirmationMixin` validation), must keep
    # its existing visible inline `errorlist` behavior unchanged.
    user = create_account("forced-still-visible-errors-user", must_change_password=True)
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:forced_password_change"),
        {"password1": "123", "password2": "123"},
    )
    content = response.content.decode()

    assert response.status_code == 200
    assert "errorlist" in content
    assert "too short" in content
