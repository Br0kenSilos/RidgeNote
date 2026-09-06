"""Email Normalization and Uniqueness.

Covers `accounts.identifiers.normalize_email()`, `SetupForm`/
`UserCreateForm`'s `clean_email()` behavior, and the DB-level
`accounts_user_email_canonical_ck`/`accounts_user_email_nonblank_uq`
constraints added by
`accounts.migrations.0006_user_email_normalization_and_uniqueness`.
Migration-specific coverage (canonicalization of legacy data, collision
preflight, ID-only error reporting) lives in
`test_account_migration_0006.py`; this file covers ordinary
application-level behavior on the already-migrated schema.
"""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.utils import timezone

from accounts.forms import SetupForm, UserCreateForm
from accounts.identifiers import normalize_email
from accounts.models import User

PASSWORD = "LongUniquePassword123!"


def password_payload(password=PASSWORD):
    return {"password1": password, "password2": password}


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# normalize_email()
# ---------------------------------------------------------------------------


def test_normalize_email_blank_stays_blank():
    assert normalize_email("") == ""


def test_normalize_email_whitespace_only_becomes_blank():
    assert normalize_email("   ") == ""


def test_normalize_email_already_lowercase_unchanged():
    assert normalize_email("user@example.com") == "user@example.com"


def test_normalize_email_surrounding_whitespace_trimmed():
    assert normalize_email("  user@example.com  ") == "user@example.com"


def test_normalize_email_uppercase_local_part_lowercased():
    assert normalize_email("User@example.com") == "user@example.com"


def test_normalize_email_uppercase_domain_lowercased():
    assert normalize_email("user@EXAMPLE.COM") == "user@example.com"


def test_normalize_email_mixed_case_full_address_fully_lowercased():
    assert normalize_email("  User@Example.COM  ") == "user@example.com"


# ---------------------------------------------------------------------------
# SetupForm
# ---------------------------------------------------------------------------


def _setup_payload(**overrides):
    payload = {"username": "setup-admin", "email": "", **password_payload()}
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_setup_form_blank_email_accepted():
    form = SetupForm(_setup_payload(email=""))
    assert form.is_valid()
    assert form.cleaned_data["email"] == ""


@pytest.mark.django_db
def test_setup_form_whitespace_only_email_becomes_blank():
    form = SetupForm(_setup_payload(email="   "))
    assert form.is_valid()
    assert form.cleaned_data["email"] == ""


@pytest.mark.django_db
def test_setup_form_normal_email_accepted():
    form = SetupForm(_setup_payload(email="user@example.com"))
    assert form.is_valid()
    assert form.cleaned_data["email"] == "user@example.com"


@pytest.mark.django_db
def test_setup_form_email_whitespace_trimmed():
    form = SetupForm(_setup_payload(email="  user@example.com  "))
    assert form.is_valid()
    assert form.cleaned_data["email"] == "user@example.com"


@pytest.mark.django_db
def test_setup_form_email_casing_canonicalized_to_lowercase():
    form = SetupForm(_setup_payload(email="User@Example.COM"))
    assert form.is_valid()
    assert form.cleaned_data["email"] == "user@example.com"


@pytest.mark.django_db
def test_setup_form_rejects_exact_duplicate_email():
    create_account("existing-user", email="taken@example.com")
    form = SetupForm(_setup_payload(username="new-admin", email="taken@example.com"))
    assert not form.is_valid()
    assert "A user with that email already exists." in form.errors["email"]


@pytest.mark.django_db
def test_setup_form_rejects_case_equivalent_duplicate_email():
    create_account("existing-user", email="taken@example.com")
    form = SetupForm(_setup_payload(username="new-admin", email="Taken@Example.COM"))
    assert not form.is_valid()
    assert "A user with that email already exists." in form.errors["email"]


@pytest.mark.django_db
def test_setup_form_rejects_whitespace_equivalent_duplicate_email():
    create_account("existing-user", email="taken@example.com")
    form = SetupForm(_setup_payload(username="new-admin", email="  taken@example.com  "))
    assert not form.is_valid()
    assert "A user with that email already exists." in form.errors["email"]


# ---------------------------------------------------------------------------
# UserCreateForm
# ---------------------------------------------------------------------------


def _create_payload(**overrides):
    payload = {
        "setup_method": "temporary_password",
        "username": "created-user",
        "display_name": "Created User",
        "email": "",
        "role": User.ROLE_USER,
        "is_active": "on",
        **password_payload(),
    }
    payload.update(overrides)
    return payload


@pytest.mark.django_db
def test_user_create_form_blank_email_accepted():
    form = UserCreateForm(_create_payload(email=""))
    assert form.is_valid()
    assert form.cleaned_data["email"] == ""


@pytest.mark.django_db
def test_user_create_form_whitespace_only_email_becomes_blank():
    form = UserCreateForm(_create_payload(email="   "))
    assert form.is_valid()
    assert form.cleaned_data["email"] == ""


@pytest.mark.django_db
def test_user_create_form_normal_email_accepted():
    form = UserCreateForm(_create_payload(email="user@example.com"))
    assert form.is_valid()
    assert form.cleaned_data["email"] == "user@example.com"


@pytest.mark.django_db
def test_user_create_form_email_whitespace_trimmed():
    form = UserCreateForm(_create_payload(email="  user@example.com  "))
    assert form.is_valid()
    assert form.cleaned_data["email"] == "user@example.com"


@pytest.mark.django_db
def test_user_create_form_email_casing_canonicalized_to_lowercase():
    form = UserCreateForm(_create_payload(email="User@Example.COM"))
    assert form.is_valid()
    assert form.cleaned_data["email"] == "user@example.com"


@pytest.mark.django_db
def test_user_create_form_rejects_exact_duplicate_email():
    create_account("existing-user", email="taken@example.com")
    form = UserCreateForm(_create_payload(username="new-user", email="taken@example.com"))
    assert not form.is_valid()
    assert "A user with that email already exists." in form.errors["email"]


@pytest.mark.django_db
def test_user_create_form_rejects_case_equivalent_duplicate_email():
    create_account("existing-user", email="taken@example.com")
    form = UserCreateForm(_create_payload(username="new-user", email="Taken@Example.COM"))
    assert not form.is_valid()
    assert "A user with that email already exists." in form.errors["email"]


@pytest.mark.django_db
def test_user_create_form_rejects_whitespace_equivalent_duplicate_email():
    create_account("existing-user", email="taken@example.com")
    form = UserCreateForm(_create_payload(username="new-user", email="  taken@example.com  "))
    assert not form.is_valid()
    assert "A user with that email already exists." in form.errors["email"]


# ---------------------------------------------------------------------------
# Database integrity
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_multiple_blank_emails_are_allowed():
    create_account("blank-one", email="")
    create_account("blank-two", email="")
    create_account("blank-three", email="")
    assert User.objects.filter(email="").count() >= 3


@pytest.mark.django_db
def test_distinct_nonblank_emails_are_allowed():
    create_account("owner-one", email="one@example.com")
    create_account("owner-two", email="two@example.com")
    assert User.objects.filter(email="one@example.com").exists()
    assert User.objects.filter(email="two@example.com").exists()


@pytest.mark.django_db
def test_exact_duplicate_nonblank_email_blocked_by_db():
    create_account("owner-one", email="dup@example.com")
    with pytest.raises(IntegrityError), transaction.atomic():
        get_user_model().objects.create_user(
            username="owner-two",
            password=PASSWORD,
            role=User.ROLE_USER,
            email="dup@example.com",
        )


@pytest.mark.django_db
def test_noncanonical_manual_email_rejected_by_canonical_check_constraint():
    # `.update()` bypasses `User.save()`'s own normalization -- this
    # proves the DB-level CheckConstraint is real defense-in-depth, not
    # merely relying on application code always going through save().
    user = create_account("manual-user", email="")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(email="Manual@Example.COM")


@pytest.mark.django_db
def test_whitespace_manual_email_rejected_by_canonical_check_constraint():
    user = create_account("manual-whitespace-user", email="")
    with pytest.raises(IntegrityError), transaction.atomic():
        User.objects.filter(pk=user.pk).update(email=" whitespace@example.com ")


@pytest.mark.django_db
def test_uniqueness_constraint_applies_only_to_nonblank_values():
    create_account("blank-a", email="")
    create_account("blank-b", email="")
    # No IntegrityError -- proves the partial constraint's condition excludes "".
    assert User.objects.filter(email="").count() >= 2


# ---------------------------------------------------------------------------
# Regression
# ---------------------------------------------------------------------------


@pytest.mark.django_db
def test_username_normalization_unchanged():
    user = create_account("  Regression-User  ")
    user.refresh_from_db()
    assert user.username == "regression-user"


@pytest.mark.django_db
def test_email_remains_optional_on_direct_creation():
    user = create_account("optional-email-user")
    assert user.email == ""
