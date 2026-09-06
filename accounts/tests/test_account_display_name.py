"""Account Display Name Edit.

Covers `AccountDisplayNameForm` and the `/account/` view's
`account_action` discriminator that lets the Display Name and
password-change sections save independently. Reuses the existing
`display_name` field, `normalize_display_name()`, and `display_label`
fallback unchanged -- no separate field, migration, or fallback logic
exists. General Account-page/password-change
coverage (redirects, message text, error rendering, forced-password
-change gating) lives in `test_account_page.py`; this file is scoped to
the Display Name section itself and its non-interference with the
password section.
"""

import pytest
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.forms import AccountDisplayNameForm
from accounts.models import AuditEvent, User

PASSWORD = "LongUniquePassword123!"
OTHER_PASSWORD = "AnotherLongPassword123!"


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


def display_name_payload(display_name):
    return {"account_action": "display_name", "display_name": display_name}


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
def test_display_name_form_accepts_a_normal_value():
    form = AccountDisplayNameForm({"display_name": "Casey User"})

    assert form.is_valid()
    assert form.cleaned_data["display_name"] == "Casey User"


def test_display_name_form_trims_leading_and_trailing_whitespace():
    form = AccountDisplayNameForm({"display_name": "  Casey User  "})

    assert form.is_valid()
    assert form.cleaned_data["display_name"] == "Casey User"


def test_display_name_form_preserves_case():
    form = AccountDisplayNameForm({"display_name": "CaSeY UsEr"})

    assert form.is_valid()
    assert form.cleaned_data["display_name"] == "CaSeY UsEr"


def test_display_name_form_allows_blank():
    form = AccountDisplayNameForm({"display_name": ""})

    assert form.is_valid()
    assert form.cleaned_data["display_name"] == ""


def test_display_name_form_omitted_field_is_also_valid():
    form = AccountDisplayNameForm({})

    assert form.is_valid()
    assert form.cleaned_data["display_name"] == ""


def test_display_name_form_accepts_exactly_max_length():
    # The self-service form's own limit
    # is 40 characters, deliberately smaller than the model's own
    # `max_length=150` -- see `AccountDisplayNameForm`'s docstring.
    form = AccountDisplayNameForm({"display_name": "x" * 40})

    assert form.is_valid()


def test_display_name_form_rejects_over_max_length():
    form = AccountDisplayNameForm({"display_name": "x" * 41})

    assert not form.is_valid()
    assert "display_name" in form.errors


# ---------------------------------------------------------------------------
# View: GET
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_account_page_get_renders_maxlength_attribute():
    # The browser-enforced `maxlength`
    # stops the user from ever typing a 41st character, so the inline
    # validation error is normally unreachable in ordinary use -- this
    # help text is what explains the limit up front instead.
    user = create_account("view-dn-maxlength-attr-user")

    response = authenticated_client(user).get(reverse("accounts:account"))
    content = response.content.decode()

    assert 'name="display_name"' in content
    assert 'maxlength="40"' in content


@pytest.mark.django_db
def test_account_page_get_shows_character_limit_help_text():
    # The caption's
    # `id="id_display_name_helptext"` matches Django 5.2's own
    # automatically-added `aria-describedby` on the input (confirmed via
    # direct form rendering, not assumed) -- it is revealed on
    # desktop only when Display Name has focus, rather than permanently
    # visible, so this asserts the markup is present in the response at
    # all, not that it is unconditionally displayed.
    user = create_account("view-dn-help-text-user")

    response = authenticated_client(user).get(reverse("accounts:account"))
    content = response.content.decode()

    helptext = '<p class="help-text" id="id_display_name_helptext">40 characters maximum.</p>'
    assert helptext in content


@pytest.mark.django_db
def test_account_page_get_renders_current_display_name():
    user = create_account("view-dn-get-user", display_name="Existing Name")

    response = authenticated_client(user).get(reverse("accounts:account"))
    content = response.content.decode()

    assert response.status_code == 200
    assert 'value="Existing Name"' in content


@pytest.mark.django_db
def test_account_page_get_still_shows_username_read_only():
    user = create_account("view-dn-username-user", display_name="Something Else")

    response = authenticated_client(user).get(reverse("accounts:account"))
    content = response.content.decode()

    assert "view-dn-username-user" in content
    # Username is rendered as plain text, never inside an editable input.
    assert 'name="username"' not in content


# ---------------------------------------------------------------------------
# View: successful save
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_setting_display_name_persists_the_new_value():
    user = create_account("view-dn-set-user", display_name="")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload("New Name"))

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.display_name == "New Name"


@pytest.mark.django_db
def test_setting_display_name_trims_whitespace_through_existing_normalization():
    user = create_account("view-dn-trim-user", display_name="")
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), display_name_payload("  Padded Name  "))

    user.refresh_from_db()
    assert user.display_name == "Padded Name"


@pytest.mark.django_db
def test_blank_display_name_is_accepted_and_saved():
    user = create_account("view-dn-blank-user", display_name="Old Name")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload(""))

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.display_name == ""


@pytest.mark.django_db
def test_blank_display_name_falls_back_to_username_via_display_label():
    user = create_account("view-dn-fallback-user", display_name="Old Name")
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), display_name_payload(""))

    user.refresh_from_db()
    assert user.display_label == "view-dn-fallback-user"


@pytest.mark.django_db
def test_clearing_display_name_restores_username_fallback_in_rendered_navigation():
    user = create_account("view-dn-nav-fallback-user", display_name="Old Name")
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), display_name_payload(""))

    content = client.get(reverse("home")).content.decode()
    assert "Signed in as view-dn-nav-fallback-user" in content
    assert "Signed in as Old Name" not in content


@pytest.mark.django_db
def test_display_name_save_changes_only_display_name_field():
    user = create_account("view-dn-scoped-user", display_name="Old Name", email="old@example.com")
    original_password_hash = user.password
    original_session_generation = user.session_generation
    original_role = user.role
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), display_name_payload("New Name"))

    user.refresh_from_db()
    assert user.display_name == "New Name"
    assert user.username == "view-dn-scoped-user"
    assert user.email == "old@example.com"
    assert user.session_generation == original_session_generation
    assert user.password == original_password_hash
    assert user.role == original_role


@pytest.mark.django_db
def test_display_name_save_creates_no_audit_event():
    user = create_account("view-dn-no-audit-user", display_name="")
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), display_name_payload("New Name"))

    assert not AuditEvent.objects.exists()


@pytest.mark.django_db
def test_display_name_save_success_follows_prg():
    user = create_account("view-dn-prg-user", display_name="")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload("New Name"))

    assert response.status_code == 302
    assert response.url == reverse("accounts:account")


@pytest.mark.django_db
def test_display_name_save_shows_success_message():
    user = create_account("view-dn-message-user", display_name="")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:account"), display_name_payload("New Name"), follow=True
    )

    assert b"Your display name has been saved." in response.content


@pytest.mark.django_db
def test_setting_display_name_identical_to_current_value_still_redirects_safely():
    user = create_account("view-dn-noop-user", display_name="Same Name")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload("Same Name"))

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.display_name == "Same Name"


# ---------------------------------------------------------------------------
# View: validation failure
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_over_max_length_display_name_does_not_change_persisted_value():
    user = create_account("view-dn-too-long-user", display_name="Original Name")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload("x" * 41), follow=True)
    content = response.content.decode()

    assert response.status_code == 200
    user.refresh_from_db()
    assert user.display_name == "Original Name"
    assert 'class="messages__item messages__item--error"' in content


@pytest.mark.django_db
def test_over_max_length_display_name_shows_a_visible_error_next_to_the_field():
    # The field-level error must be
    # visibly rendered directly under the Display Name input, not only
    # hidden inside a `visually-hidden` echo (the password form's own
    # convention, which relies on the page-level banner instead).
    user = create_account("view-dn-visible-error-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload("x" * 41))
    content = response.content.decode()

    assert 'class="visually-hidden" id="id_display_name_error"' not in content
    assert (
        '<ul class="errorlist" id="id_display_name_error">'
        "<li>Ensure this value has at most 40 characters (it has 41).</li></ul>"
    ) in content


@pytest.mark.django_db
def test_over_max_length_error_is_rendered_within_the_display_name_section():
    # The visible errorlist must sit between the Display Name input and
    # that section's own Save button -- not merely somewhere on the page.
    user = create_account("view-dn-error-placement-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload("x" * 41))
    content = response.content.decode()

    field_pos = content.index('id="id_display_name"')
    error_pos = content.index('<ul class="errorlist" id="id_display_name_error">')
    save_button_pos = content.index("Save display name")
    assert field_pos < error_pos < save_button_pos


@pytest.mark.django_db
def test_over_max_length_display_name_does_not_show_password_errors():
    user = create_account("view-dn-no-password-error-side-effect-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload("x" * 41))
    content = response.content.decode()

    assert "id_current_password_error" not in content
    assert "Your current password was incorrect." not in content


@pytest.mark.django_db
def test_valid_max_length_display_name_save_succeeds():
    user = create_account("view-dn-valid-max-length-user", display_name="")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload("x" * 40))

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.display_name == "x" * 40


@pytest.mark.django_db
def test_existing_stored_value_longer_than_form_limit_still_renders_on_get():
    # The 40-character cap is a
    # self-service-form-only limit -- the model remains `max_length=150`
    # and any pre-existing longer value must render, not be truncated.
    user = create_account("view-dn-legacy-long-user", display_name="x" * 90)

    response = authenticated_client(user).get(reverse("accounts:account"))
    content = response.content.decode()

    assert response.status_code == 200
    assert f'value="{"x" * 90}"' in content


@pytest.mark.django_db
def test_existing_stored_value_longer_than_form_limit_can_still_be_shortened():
    user = create_account("view-dn-legacy-long-shorten-user", display_name="x" * 90)
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload("Short Name"))

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.display_name == "Short Name"


@pytest.mark.django_db
def test_invalid_display_name_post_does_not_trigger_password_validation_errors():
    user = create_account("view-dn-no-password-crosstalk-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), display_name_payload("x" * 41), follow=True)
    content = response.content.decode()

    assert "Your current password was incorrect." not in content
    assert "The two password fields did not match." not in content


@pytest.mark.django_db
def test_display_name_post_cannot_modify_another_user():
    owner = create_account("view-dn-owner-user", display_name="Owner Name")
    other = create_account("view-dn-other-user", display_name="Other Name")
    client = authenticated_client(owner)

    payload = display_name_payload("Hijacked Name")
    payload["user_id"] = other.pk
    payload["user"] = other.pk
    client.post(reverse("accounts:account"), payload)

    owner.refresh_from_db()
    other.refresh_from_db()
    assert owner.display_name == "Hijacked Name"
    assert other.display_name == "Other Name"  # unaffected


# ---------------------------------------------------------------------------
# Cross-form non-interference
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_display_name_post_does_not_trigger_password_change_behavior():
    user = create_account("view-dn-no-password-mutation-user")
    original_password_hash = user.password
    client = authenticated_client(user)

    client.post(reverse("accounts:account"), display_name_payload("New Name"))

    user.refresh_from_db()
    assert user.password == original_password_hash
    assert not AuditEvent.objects.filter(
        event_type=AuditEvent.EVENT_ACCOUNT_PASSWORD_CHANGE_COMPLETED
    ).exists()


@pytest.mark.django_db
def test_password_post_does_not_validate_or_mutate_display_name():
    user = create_account("view-pw-no-display-name-mutation-user", display_name="Untouched Name")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), password_payload())

    assert response.status_code == 302
    user.refresh_from_db()
    assert user.display_name == "Untouched Name"
    assert check_password(OTHER_PASSWORD, user.password)


@pytest.mark.django_db
def test_password_change_still_succeeds_alongside_the_new_display_name_section():
    # Regression guard: introducing `account_action` must not have broken
    # the pre-existing password-change success path.
    user = create_account("view-pw-still-works-user")
    client = authenticated_client(user)

    response = client.post(reverse("accounts:account"), password_payload())

    assert response.status_code == 302
    assert response.url == reverse("accounts:account")
    user.refresh_from_db()
    assert check_password(OTHER_PASSWORD, user.password)


@pytest.mark.django_db
def test_password_change_failure_still_behaves_as_before():
    # Regression guard: same failure path, same message, unaffected by
    # the new discriminator.
    user = create_account("view-pw-still-fails-user")
    client = authenticated_client(user)

    response = client.post(
        reverse("accounts:account"), password_payload(current="not-the-real-password")
    )
    content = response.content.decode()

    assert response.status_code == 200
    user.refresh_from_db()
    assert check_password(PASSWORD, user.password)
    assert "Your current password was incorrect." in content


@pytest.mark.django_db
def test_account_authorization_requirement_unchanged():
    response = Client().get(reverse("accounts:account"))

    assert response.status_code == 302
    assert reverse("accounts:login") in response.url
