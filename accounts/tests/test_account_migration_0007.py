"""Onboarding Lifecycle Foundation.

Covers `accounts.migrations.0007_user_setup_completed_at` in isolation,
following `test_account_migration_0002.py`/`test_account_migration_0006.py`'s
established pattern: downgrade to the migration immediately prior,
create rows with the shape the migration must handle, migrate forward,
and assert the result -- then restore the shared test database's
migration state in a fixture teardown so later tests are never left on
a downgraded schema.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.utils import timezone

TARGET = ("accounts", "0007_user_setup_completed_at")
PREVIOUS = [("accounts", "0006_user_email_normalization_and_uniqueness")]


def migrate_to(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _accounts_leaf_targets():
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor.loader.graph.leaf_nodes(app="accounts")


@pytest.fixture(autouse=True)
def _restore_accounts_migration_state():
    yield
    migrate_to(_accounts_leaf_targets())


@pytest.mark.django_db(transaction=True)
def test_accounts_0007_existing_user_backfilled_from_date_joined():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    joined_at = timezone.now() - timedelta(days=30)
    User.objects.create(
        username="backfill-user", password="!", role="user", email="", date_joined=joined_at
    )

    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")

    user = User.objects.get(username="backfill-user")
    assert user.setup_completed_at == joined_at


@pytest.mark.django_db(transaction=True)
def test_accounts_0007_multiple_existing_users_all_backfilled():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(username="multi-one", password="!", role="user", email="")
    User.objects.create(username="multi-two", password="!", role="user", email="")
    User.objects.create(username="multi-three", password="!", role="user", email="")

    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")

    assert User.objects.filter(setup_completed_at__isnull=True).count() == 0
    for username in ("multi-one", "multi-two", "multi-three"):
        user = User.objects.get(username=username)
        assert user.setup_completed_at == user.date_joined


@pytest.mark.django_db(transaction=True)
def test_accounts_0007_preserves_active_state():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(username="active-user", password="!", role="user", email="", is_active=True)
    User.objects.create(
        username="inactive-user", password="!", role="user", email="", is_active=False
    )

    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")

    assert User.objects.get(username="active-user").is_active is True
    assert User.objects.get(username="inactive-user").is_active is False


@pytest.mark.django_db(transaction=True)
def test_accounts_0007_preserves_must_change_password():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(
        username="must-change-user", password="!", role="user", email="", must_change_password=True
    )
    User.objects.create(
        username="no-change-user", password="!", role="user", email="", must_change_password=False
    )

    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")

    assert User.objects.get(username="must-change-user").must_change_password is True
    assert User.objects.get(username="no-change-user").must_change_password is False


@pytest.mark.django_db(transaction=True)
def test_accounts_0007_preserves_password_and_session_generation():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(
        username="password-user",
        password="a-hash-value",
        role="user",
        email="",
        session_generation=3,
    )

    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")

    user = User.objects.get(username="password-user")
    assert user.password == "a-hash-value"
    assert user.session_generation == 3
