"""Email Normalization and Uniqueness.

Covers `accounts.migrations.0006_user_email_normalization_and_uniqueness`
in isolation, following `test_account_migration_0002.py`'s established
pattern exactly: downgrade to the migration immediately prior, create
rows with the shape the migration must handle, migrate forward, and
assert the result -- then restore the shared test database's migration
state in a fixture teardown so later tests are never left on a
downgraded schema.
"""

from __future__ import annotations

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.utils import IntegrityError

TARGET = ("accounts", "0006_user_email_normalization_and_uniqueness")
PREVIOUS = [("accounts", "0005_user_tag_preferences")]


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
def test_accounts_0006_clean_data_migrates_successfully():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(username="clean-user", password="!", role="user", email="")

    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")

    user = User.objects.get(username="clean-user")
    assert user.email == ""


@pytest.mark.django_db(transaction=True)
def test_accounts_0006_multiple_blanks_migrate_successfully():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(username="blank-one", password="!", role="user", email="")
    User.objects.create(username="blank-two", password="!", role="user", email="")
    User.objects.create(username="blank-three", password="!", role="user", email="")

    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")

    assert list(
        User.objects.filter(email="").values_list("username", flat=True).order_by("username")
    ) == ["blank-one", "blank-three", "blank-two"]


@pytest.mark.django_db(transaction=True)
def test_accounts_0006_whitespace_legacy_value_becomes_trimmed():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(
        username="whitespace-user", password="!", role="user", email="  padded@example.com  "
    )

    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")

    assert User.objects.get(username="whitespace-user").email == "padded@example.com"


@pytest.mark.django_db(transaction=True)
def test_accounts_0006_mixed_case_legacy_value_becomes_lowercase():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(
        username="mixed-case-user", password="!", role="user", email="User@Example.COM"
    )

    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")

    assert User.objects.get(username="mixed-case-user").email == "user@example.com"


@pytest.mark.django_db(transaction=True)
def test_accounts_0006_fails_safely_on_canonical_email_collision():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(
        username="collision-one", password="!", role="user", email="Same@Example.com"
    )
    User.objects.create(
        username="collision-two", password="!", role="user", email=" same@example.com "
    )

    with pytest.raises(RuntimeError, match="canonical email"):
        migrate_to([TARGET])

    User.objects.all().delete()
    migrate_to([TARGET])


@pytest.mark.django_db(transaction=True)
def test_accounts_0006_collision_error_reports_user_ids_not_email_addresses():
    old_apps = migrate_to(PREVIOUS)
    User = old_apps.get_model("accounts", "User")
    User.objects.create(
        username="privacy-one", password="!", role="user", email="Secret@Example.com"
    )
    User.objects.create(
        username="privacy-two", password="!", role="user", email="secret@example.com"
    )

    with pytest.raises(RuntimeError) as excinfo:
        migrate_to([TARGET])

    message = str(excinfo.value)
    assert "user ids" in message
    assert "secret@example.com" not in message.lower()

    User.objects.all().delete()
    migrate_to([TARGET])


@pytest.mark.django_db(transaction=True)
def test_accounts_0006_constraint_rejects_duplicate_nonblank_email_after_migration():
    new_apps = migrate_to([TARGET])
    User = new_apps.get_model("accounts", "User")
    User.objects.create(
        username="post-migration-one", password="!", role="user", email="taken@example.com"
    )

    with pytest.raises(IntegrityError):
        User.objects.create(
            username="post-migration-two",
            password="!",
            role="user",
            email="taken@example.com",
        )
