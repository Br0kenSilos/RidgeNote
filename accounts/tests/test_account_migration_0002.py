from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

TARGET = ("accounts", "0002_user_identity_canonicalization")
PREVIOUS = [("accounts", "0001_initial")]


def migrate_to(targets):
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    executor.migrate(targets)
    return executor.loader.project_state(targets).apps


def _accounts_leaf_targets():
    """Resolves `accounts`' current latest migration(s) from the migration
    graph itself, never a hard-coded name -- so restoration below always
    targets whatever is actually latest, including migrations added after
    this test file was written."""
    executor = MigrationExecutor(connection)
    executor.loader.build_graph()
    return executor.loader.graph.leaf_nodes(app="accounts")


@pytest.fixture(autouse=True)
def _restore_accounts_migration_state():
    """Both tests below intentionally downgrade the shared test database to
    `accounts.0001_initial` and forward only to `accounts.0002_...` to
    exercise that one migration in isolation. Without an explicit restore,
    the schema would remain downgraded for the rest of the pytest session --
    no `conftest.py` exists in this repository to compensate -- risking
    spurious missing-column failures in unrelated later tests. Restoring in
    a fixture's teardown (rather than inline in each test body) covers both
    the success path and any unexpected exception, not only the one
    `pytest.raises` path the second test already expects."""
    yield
    migrate_to(_accounts_leaf_targets())


@pytest.mark.django_db(transaction=True)
def test_accounts_0002_normalizes_usernames_and_populates_display_name():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(username=" Steve ", password="!", role="user", email="")
    User.objects.create(username="ADMIN", password="!", role="admin", email="")

    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")

    steve = User.objects.get(username="steve")
    admin = User.objects.get(username="admin")

    assert steve.display_name == "Steve"
    assert admin.display_name == "ADMIN"


@pytest.mark.django_db(transaction=True)
def test_accounts_0002_fails_safely_on_normalized_username_collision():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(username="Steve", password="!", role="user", email="")
    User.objects.create(username=" steve ", password="!", role="user", email="")

    with pytest.raises(RuntimeError, match="normalized username collisions"):
        migrate_to([TARGET])

    User.objects.all().delete()
    migrate_to([TARGET])
