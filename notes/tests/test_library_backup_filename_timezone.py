"""Backup ZIP filename timezone localization.

Covers `_archive_filename()`/`write_library_backup_archive()`'s new
`display_timezone` parameter: the DST/multi-zone matrix, per-user
isolation for the same UTC instant, and the invariant that only the
filename (and its README echo) changes -- `manifest.json`'s
`exported_at` and the README's own `Generated ... UTC` line never do.
"""

import io
import json
import zipfile
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest
from accounts.models import User
from django.contrib.auth import get_user_model
from django.utils import timezone

from notes import library_backup_archive

PASSWORD = "LongUniquePassword123!"


def create_account(username="user", *, role=User.ROLE_USER, password=PASSWORD, **kwargs):
    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(
        username=username,
        password=password,
        role=role,
        **kwargs,
    )


def _filename(exported_at, *, display_timezone):
    owner = create_account(f"tz-filename-owner-{display_timezone}-{exported_at.isoformat()}")
    destination = io.BytesIO()
    metadata = library_backup_archive.write_library_backup_archive(
        owner, destination, exported_at=exported_at, display_timezone=display_timezone
    )
    return metadata.filename


# ---------------------------------------------------------------------------
# DST / multi-zone matrix
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_new_york_winter_offset():
    exported_at = datetime(2026, 1, 15, 17, 0, 0, tzinfo=UTC)

    filename = _filename(exported_at, display_timezone=ZoneInfo("America/New_York"))

    assert filename == "ridgenote-library-backup-2026-01-15_120000.zip"


@pytest.mark.django_db(transaction=True)
def test_new_york_summer_offset():
    exported_at = datetime(2026, 7, 15, 16, 0, 0, tzinfo=UTC)

    filename = _filename(exported_at, display_timezone=ZoneInfo("America/New_York"))

    assert filename == "ridgenote-library-backup-2026-07-15_120000.zip"


@pytest.mark.django_db(transaction=True)
def test_los_angeles_offset():
    exported_at = datetime(2026, 8, 8, 19, 8, 50, tzinfo=UTC)

    filename = _filename(exported_at, display_timezone=ZoneInfo("America/Los_Angeles"))

    assert filename == "ridgenote-library-backup-2026-08-08_120850.zip"


@pytest.mark.django_db(transaction=True)
def test_london_offset_ahead_of_utc_in_summer():
    # Europe/London is a useful negative control against the US
    # convention: BST is *ahead* of UTC (+1), not behind it.
    exported_at = datetime(2026, 7, 15, 16, 0, 0, tzinfo=UTC)

    filename = _filename(exported_at, display_timezone=ZoneInfo("Europe/London"))

    assert filename == "ridgenote-library-backup-2026-07-15_170000.zip"


@pytest.mark.django_db(transaction=True)
def test_tokyo_no_dst():
    exported_at = datetime(2026, 1, 15, 17, 0, 0, tzinfo=UTC)

    filename = _filename(exported_at, display_timezone=ZoneInfo("Asia/Tokyo"))

    assert filename == "ridgenote-library-backup-2026-01-16_020000.zip"


# ---------------------------------------------------------------------------
# Multi-user isolation
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_two_users_same_utc_instant_produce_different_filenames():
    exported_at = datetime(2026, 8, 8, 19, 8, 50, tzinfo=UTC)
    user_a = create_account("tz-isolation-user-a", timezone_name="America/New_York")
    user_b = create_account("tz-isolation-user-b", timezone_name="America/Los_Angeles")

    metadata_a = library_backup_archive.write_library_backup_archive(
        user_a, io.BytesIO(), exported_at=exported_at
    )
    metadata_b = library_backup_archive.write_library_backup_archive(
        user_b, io.BytesIO(), exported_at=exported_at
    )

    assert metadata_a.filename == "ridgenote-library-backup-2026-08-08_150850.zip"
    assert metadata_b.filename == "ridgenote-library-backup-2026-08-08_120850.zip"
    assert metadata_a.filename != metadata_b.filename


@pytest.mark.django_db(transaction=True)
def test_default_display_timezone_resolves_from_owner_when_not_explicit():
    exported_at = datetime(2026, 8, 8, 19, 8, 50, tzinfo=UTC)
    owner = create_account("tz-default-resolution-owner", timezone_name="America/New_York")

    metadata = library_backup_archive.write_library_backup_archive(
        owner, io.BytesIO(), exported_at=exported_at
    )

    assert metadata.filename == "ridgenote-library-backup-2026-08-08_150850.zip"


@pytest.mark.django_db(transaction=True)
def test_changing_owner_timezone_does_not_affect_other_owner_filename():
    exported_at = datetime(2026, 8, 8, 19, 8, 50, tzinfo=UTC)
    owner = create_account("tz-noninterference-owner", timezone_name="America/New_York")
    other = create_account("tz-noninterference-other", timezone_name="America/New_York")

    owner.timezone_name = "Asia/Tokyo"
    owner.save(update_fields=["timezone_name"])

    metadata_other = library_backup_archive.write_library_backup_archive(
        other, io.BytesIO(), exported_at=exported_at
    )

    assert metadata_other.filename == "ridgenote-library-backup-2026-08-08_150850.zip"


# ---------------------------------------------------------------------------
# UTC invariants
# ---------------------------------------------------------------------------


@pytest.mark.django_db(transaction=True)
def test_manifest_exported_at_unaffected_by_display_timezone():
    exported_at = datetime(2026, 8, 8, 19, 8, 50, tzinfo=UTC)
    owner = create_account("tz-manifest-invariant-owner")
    destination = io.BytesIO()

    library_backup_archive.write_library_backup_archive(
        owner, destination, exported_at=exported_at, display_timezone=ZoneInfo("Asia/Tokyo")
    )

    with zipfile.ZipFile(destination) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    assert manifest["exported_at"] == "2026-08-08T19:08:50+00:00"


@pytest.mark.django_db(transaction=True)
def test_readme_generated_line_stays_utc_regardless_of_display_timezone():
    exported_at = datetime(2026, 8, 8, 19, 8, 50, tzinfo=UTC)
    owner = create_account("tz-readme-invariant-owner")
    destination = io.BytesIO()

    library_backup_archive.write_library_backup_archive(
        owner, destination, exported_at=exported_at, display_timezone=ZoneInfo("Asia/Tokyo")
    )

    with zipfile.ZipFile(destination) as archive:
        readme = archive.read("README.txt").decode()
    assert "Generated: 2026-08-08 19:08:50 UTC" in readme
    # The README's own "Archive:" line echoes the *actual* (localized)
    # filename, so it stays consistent with what the user actually
    # downloaded -- this is the one line inside README.txt permitted to
    # reflect the display timezone.
    assert "Archive: ridgenote-library-backup-2026-08-09_040850.zip" in readme
