"""User List expired-lockout display accuracy.

`accounts.services.is_user_locked()` is the authoritative lockout-state
semantic (`locked_until > timezone.now()`), matching what login
enforcement actually checks. The User List's Lockout column and
attention-row styling must key off that same semantic rather than bare
truthy presence of `locked_until` -- otherwise an *expired* lockout
timestamp would still display as "Locked" and still trigger
attention-row styling even though authentication no longer treats the
account as locked. This file is display-only regression coverage; it
does not touch login enforcement, failed-login counters, or lockout
duration.
"""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.test import Client
from django.urls import reverse
from django.utils import timezone

from accounts import services
from accounts.models import User

PASSWORD = "LongUniquePassword123!"
ATTENTION_CLASS = "user-list-page__row--attention"


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


def create_admin(username="admin", *, password=PASSWORD, **kwargs):
    return create_account(username, role=User.ROLE_ADMIN, password=password, **kwargs)


def authenticated_client(user):
    client = Client()
    client.force_login(user)
    session = client.session
    session[services.SESSION_GENERATION_KEY] = user.session_generation
    session[services.LAST_ACTIVITY_KEY] = timezone.now().timestamp()
    session.save()
    return client


def row_for(content: str, username: str) -> str:
    """Extracts the single <tr>...</tr> row containing `username` from
    the rendered User List page, for row-scoped assertions."""
    marker = f">{username}<"
    row_start = content.rindex("<tr", 0, content.index(marker))
    row_end = content.index("</tr>", row_start) + len("</tr>")
    return content[row_start:row_end]


@pytest.mark.django_db
def test_active_lockout_shows_locked_and_attention_row():
    admin = create_admin("lockout-admin-active")
    target = create_account(
        "lockout-user-active",
        locked_until=timezone.now() + timedelta(minutes=15),
    )
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))
    content = response.content.decode()
    row = row_for(content, target.username)

    assert response.status_code == 200
    assert "Locked until" in row
    assert ATTENTION_CLASS in row


@pytest.mark.django_db
def test_expired_lockout_does_not_show_locked_or_attention_row():
    admin = create_admin("lockout-admin-expired")
    target = create_account(
        "lockout-user-expired",
        locked_until=timezone.now() - timedelta(minutes=15),
    )
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))
    content = response.content.decode()
    row = row_for(content, target.username)

    assert response.status_code == 200
    assert "Not locked" in row
    assert "Locked until" not in row
    assert ATTENTION_CLASS not in row


@pytest.mark.django_db
def test_no_lockout_shows_not_locked_and_no_attention_row():
    admin = create_admin("lockout-admin-none")
    target = create_account("lockout-user-none", locked_until=None)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))
    content = response.content.decode()
    row = row_for(content, target.username)

    assert response.status_code == 200
    assert "Not locked" in row
    assert ATTENTION_CLASS not in row


@pytest.mark.django_db
def test_disabled_user_still_gets_attention_row_with_no_lockout():
    admin = create_admin("lockout-admin-disabled")
    target = create_account("lockout-user-disabled", is_active=False, locked_until=None)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))
    content = response.content.decode()
    row = row_for(content, target.username)

    assert response.status_code == 200
    assert ATTENTION_CLASS in row


@pytest.mark.django_db
def test_pending_deletion_user_still_gets_attention_row_with_expired_lockout():
    admin = create_admin("lockout-admin-pending")
    target = create_account(
        "lockout-user-pending",
        locked_until=timezone.now() - timedelta(minutes=15),
    )
    services.schedule_user_deletion(target=target, actor=admin, request=None)
    client = authenticated_client(admin)

    response = client.get(reverse("accounts:user_list"))
    content = response.content.decode()
    row = row_for(content, target.username)

    assert response.status_code == 200
    assert "Not locked" in row
    assert ATTENTION_CLASS in row
