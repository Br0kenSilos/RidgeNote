"""Automatic purge scheduler integration.

`run_purge_scheduler` periodically calls the independently shipped
`notes.purge.run_purge_cycle()` -- no purge logic lives in this
module. Tests below cover: the two strict environment parsers (pure
functions, no Django database needed); disabled/enabled scheduler
behavior via direct calls to `Command()._run_cycle()`; every non-fatal
failure outcome; genuine signal-driven shutdown behavior (real
background threads sending real OS signals, since `signal.signal()` only
works from the main thread); database-connection hygiene; log privacy;
and one genuine PostgreSQL concurrency test proving the scheduler and the
manual `purge_expired_trash` command share the same advisory lock.
"""

import logging
import os
import signal
import threading
import time
from datetime import timedelta

import pytest
from django.core.management import call_command
from django.db import OperationalError
from django.utils import timezone
from notes import services
from notes.models import Note
from notes.purge import PurgeAuditWriteError, PurgeCycleResult

import core.management.commands.run_purge_scheduler as scheduler_module

PASSWORD = "LongUniquePassword123!"


@pytest.fixture(autouse=True)
def use_simple_staticfiles_storage(settings):
    settings.STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }


def create_account(username="user", **kwargs):
    from django.contrib.auth import get_user_model

    kwargs.setdefault("display_name", username)
    kwargs.setdefault("setup_completed_at", timezone.now())
    return get_user_model().objects.create_user(username=username, password=PASSWORD, **kwargs)


def _make_result(**overrides) -> PurgeCycleResult:
    fields = {
        "run_id": "test-run-id",
        "cutoff": timezone.now() - timedelta(days=90),
        "dry_run": False,
        "lock_acquired": True,
        "notes_eligible": 0,
        "notes_purged": 0,
        "notes_failed": 0,
        "folders_eligible": 0,
        "folders_purged": 0,
        "folders_blocked": 0,
        "folders_failed": 0,
        "duration_seconds": 0.01,
    }
    fields.update(overrides)
    return PurgeCycleResult(**fields)


@pytest.fixture
def restore_signal_handlers():
    original_sigterm = signal.getsignal(signal.SIGTERM)
    original_sigint = signal.getsignal(signal.SIGINT)
    yield
    signal.signal(signal.SIGTERM, original_sigterm)
    signal.signal(signal.SIGINT, original_sigint)


# -- strict enablement parser -------------------------------------------------


def test_purge_enabled_absent_is_false():
    assert scheduler_module.parse_purge_enabled(None) is False


@pytest.mark.parametrize("raw", ["true", "TRUE", "True", "  true  ", " TrUe "])
def test_purge_enabled_true_variants(raw):
    assert scheduler_module.parse_purge_enabled(raw) is True


@pytest.mark.parametrize("raw", ["false", "FALSE", "False", "  false  ", " FaLsE "])
def test_purge_enabled_false_variants(raw):
    assert scheduler_module.parse_purge_enabled(raw) is False


@pytest.mark.parametrize("raw", ["", "   ", "1", "0", "yes", "no", "on", "off", "garbage"])
def test_purge_enabled_invalid_values_raise(raw):
    with pytest.raises(ValueError, match="RIDGENOTE_PURGE_ENABLED"):
        scheduler_module.parse_purge_enabled(raw)


# -- interval parser -----------------------------------------------------------


def test_purge_interval_absent_defaults_to_86400():
    assert scheduler_module.parse_purge_interval_seconds(None) == 86400


@pytest.mark.parametrize("raw", ["1", "60", "86400", "  120  "])
def test_purge_interval_valid_positive_integers(raw):
    assert scheduler_module.parse_purge_interval_seconds(raw) == int(raw.strip())


@pytest.mark.parametrize("raw", ["0", "-1", "", "   ", "not-a-number", "12.5"])
def test_purge_interval_invalid_values_raise(raw):
    with pytest.raises(ValueError, match="RIDGENOTE_PURGE_INTERVAL_SECONDS"):
        scheduler_module.parse_purge_interval_seconds(raw)


# -- disabled scheduler ---------------------------------------------------------


def test_disabled_scheduler_never_calls_run_purge_cycle(monkeypatch, restore_signal_handlers):
    monkeypatch.setenv("RIDGENOTE_PURGE_ENABLED", "false")
    monkeypatch.setattr(scheduler_module, "SHUTDOWN_POLL_INTERVAL_SECONDS", 0.02)

    call_count = {"n": 0}

    def fake_run_purge_cycle(**kwargs):
        call_count["n"] += 1
        return _make_result()

    monkeypatch.setattr(scheduler_module, "run_purge_cycle", fake_run_purge_cycle)

    def send_sigterm_soon():
        time.sleep(0.15)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=send_sigterm_soon).start()

    scheduler_module.Command().handle()

    assert call_count["n"] == 0


def test_disabled_scheduler_logs_once_not_repeated(monkeypatch, restore_signal_handlers):
    monkeypatch.setenv("RIDGENOTE_PURGE_ENABLED", "false")
    monkeypatch.setattr(scheduler_module, "SHUTDOWN_POLL_INTERVAL_SECONDS", 0.02)

    logged = []
    original_info = scheduler_module.logger.info

    def tracking_info(msg, *args, **kwargs):
        logged.append(msg % args if args else msg)
        return original_info(msg, *args, **kwargs)

    monkeypatch.setattr(scheduler_module.logger, "info", tracking_info)

    def send_sigterm_soon():
        time.sleep(0.15)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=send_sigterm_soon).start()

    command = scheduler_module.Command()
    command.handle()

    disabled_messages = [m for m in logged if "disabled" in m.lower()]
    assert len(disabled_messages) == 1


# -- enabled scheduler: single-cycle behavior via _run_cycle() ----------------


@pytest.mark.django_db
def test_run_cycle_passes_exact_default_batch_sizes_and_dry_run_false(monkeypatch):
    captured = {}

    def fake_run_purge_cycle(*, dry_run, note_batch_size, folder_batch_size):
        captured["dry_run"] = dry_run
        captured["note_batch_size"] = note_batch_size
        captured["folder_batch_size"] = folder_batch_size
        return _make_result()

    monkeypatch.setattr(scheduler_module, "run_purge_cycle", fake_run_purge_cycle)

    scheduler_module.Command()._run_cycle()

    assert captured == {"dry_run": False, "note_batch_size": 200, "folder_batch_size": 200}


@pytest.mark.django_db
def test_run_cycle_calls_close_old_connections_before_attempt(monkeypatch):
    call_order = []

    monkeypatch.setattr(
        scheduler_module,
        "close_old_connections",
        lambda: call_order.append("close_old_connections"),
    )

    def fake_run_purge_cycle(**kwargs):
        call_order.append("run_purge_cycle")
        return _make_result()

    monkeypatch.setattr(scheduler_module, "run_purge_cycle", fake_run_purge_cycle)

    scheduler_module.Command()._run_cycle()

    assert call_order == ["close_old_connections", "run_purge_cycle"]


# -- enabled scheduler: full loop via handle() ---------------------------------


def test_enabled_scheduler_runs_immediately_then_one_cycle_per_interval(
    monkeypatch, restore_signal_handlers
):
    monkeypatch.setenv("RIDGENOTE_PURGE_ENABLED", "true")
    monkeypatch.setenv("RIDGENOTE_PURGE_INTERVAL_SECONDS", "3600")
    monkeypatch.setattr(scheduler_module, "SHUTDOWN_POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(scheduler_module, "close_old_connections", lambda: None)

    call_count = {"n": 0}

    def fake_run_purge_cycle(**kwargs):
        call_count["n"] += 1
        return _make_result()

    monkeypatch.setattr(scheduler_module, "run_purge_cycle", fake_run_purge_cycle)

    def send_sigterm_soon():
        time.sleep(0.2)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=send_sigterm_soon).start()

    command = scheduler_module.Command()
    command.handle()

    # One immediate cycle ran; the 3600s interval never elapsed before
    # SIGTERM arrived during the interruptible wait, so no second cycle ran.
    assert call_count["n"] == 1


def test_shutdown_signal_interrupts_a_long_interval_promptly(monkeypatch, restore_signal_handlers):
    monkeypatch.setenv("RIDGENOTE_PURGE_ENABLED", "true")
    monkeypatch.setenv("RIDGENOTE_PURGE_INTERVAL_SECONDS", "3600")
    monkeypatch.setattr(scheduler_module, "SHUTDOWN_POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(scheduler_module, "close_old_connections", lambda: None)
    monkeypatch.setattr(scheduler_module, "run_purge_cycle", lambda **kwargs: _make_result())

    def send_sigterm_soon():
        time.sleep(0.15)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=send_sigterm_soon).start()

    started_at = time.monotonic()
    scheduler_module.Command().handle()
    elapsed = time.monotonic() - started_at

    assert elapsed < 2.0, "shutdown was not interruptible within the expected tick bound"


def test_in_progress_cycle_is_allowed_to_finish_before_shutdown(
    monkeypatch, restore_signal_handlers
):
    monkeypatch.setenv("RIDGENOTE_PURGE_ENABLED", "true")
    monkeypatch.setenv("RIDGENOTE_PURGE_INTERVAL_SECONDS", "3600")
    monkeypatch.setattr(scheduler_module, "SHUTDOWN_POLL_INTERVAL_SECONDS", 0.02)
    monkeypatch.setattr(scheduler_module, "close_old_connections", lambda: None)

    cycle_started = threading.Event()
    cycle_finished = threading.Event()

    def slow_run_purge_cycle(**kwargs):
        cycle_started.set()
        # A busy-wait (not `time.sleep`) so a signal arriving mid-cycle
        # cannot cause this simulated cycle to return early -- proving the
        # scheduler genuinely waits for the real call to return on its own,
        # rather than merely happening to survive an interruptible sleep.
        end_at = time.monotonic() + 0.3
        while time.monotonic() < end_at:
            pass
        cycle_finished.set()
        return _make_result()

    monkeypatch.setattr(scheduler_module, "run_purge_cycle", slow_run_purge_cycle)

    def send_sigterm_during_cycle():
        assert cycle_started.wait(timeout=2), "cycle never started"
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=send_sigterm_during_cycle).start()

    scheduler_module.Command().handle()

    assert cycle_finished.is_set()


# -- failure handling -----------------------------------------------------------


def test_lock_contention_is_nonfatal(monkeypatch):
    monkeypatch.setattr(scheduler_module, "close_old_connections", lambda: None)
    monkeypatch.setattr(
        scheduler_module, "run_purge_cycle", lambda **kwargs: _make_result(lock_acquired=False)
    )

    scheduler_module.Command()._run_cycle()  # must not raise


def test_blocked_folders_are_nonfatal(monkeypatch, caplog):
    monkeypatch.setattr(scheduler_module, "close_old_connections", lambda: None)
    monkeypatch.setattr(
        scheduler_module, "run_purge_cycle", lambda **kwargs: _make_result(folders_blocked=3)
    )

    with caplog.at_level(logging.INFO, logger=scheduler_module.logger.name):
        scheduler_module.Command()._run_cycle()  # must not raise

    assert any("folders_blocked" in r.message for r in caplog.records)


def test_row_failures_logged_as_aggregate_warning(monkeypatch, caplog):
    monkeypatch.setattr(scheduler_module, "close_old_connections", lambda: None)
    monkeypatch.setattr(
        scheduler_module,
        "run_purge_cycle",
        lambda **kwargs: _make_result(notes_failed=2, folders_failed=1),
    )

    with caplog.at_level(logging.WARNING, logger=scheduler_module.logger.name):
        scheduler_module.Command()._run_cycle()

    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "notes_failed=2" in warnings[0].message
    assert "folders_failed=1" in warnings[0].message


def test_audit_write_failure_is_caught_and_logged(monkeypatch, caplog):
    monkeypatch.setattr(scheduler_module, "close_old_connections", lambda: None)

    def raising_run_purge_cycle(**kwargs):
        raise PurgeAuditWriteError(result=_make_result(notes_purged=1))

    monkeypatch.setattr(scheduler_module, "run_purge_cycle", raising_run_purge_cycle)

    with caplog.at_level(logging.CRITICAL, logger=scheduler_module.logger.name):
        scheduler_module.Command()._run_cycle()  # must not raise, scheduler stays alive

    assert any(r.levelno == logging.CRITICAL for r in caplog.records)


def test_database_error_is_caught_logged_and_connections_recycled(monkeypatch, caplog):
    close_calls = {"n": 0}

    def fake_close_old_connections():
        close_calls["n"] += 1

    monkeypatch.setattr(scheduler_module, "close_old_connections", fake_close_old_connections)

    def raising_run_purge_cycle(**kwargs):
        raise OperationalError("connection lost")

    monkeypatch.setattr(scheduler_module, "run_purge_cycle", raising_run_purge_cycle)

    with caplog.at_level(logging.ERROR, logger=scheduler_module.logger.name):
        scheduler_module.Command()._run_cycle()  # must not raise

    assert close_calls["n"] == 2  # once before the attempt, once after the failure
    assert any(r.levelno == logging.ERROR for r in caplog.records)


def test_unexpected_exception_is_caught_and_logged(monkeypatch, caplog):
    monkeypatch.setattr(scheduler_module, "close_old_connections", lambda: None)

    def raising_run_purge_cycle(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(scheduler_module, "run_purge_cycle", raising_run_purge_cycle)

    with caplog.at_level(logging.ERROR, logger=scheduler_module.logger.name):
        scheduler_module.Command()._run_cycle()  # must not raise

    assert any(r.levelno == logging.ERROR for r in caplog.records)


# -- privacy --------------------------------------------------------------------


@pytest.mark.django_db
def test_scheduler_logs_contain_no_seeded_identifying_content(monkeypatch, caplog):
    # `close_old_connections()` is stubbed out here (and dedicated-tested
    # separately in `test_run_cycle_calls_close_old_connections_before_attempt`)
    # because calling the real one while already inside this test's own
    # `pytest.mark.django_db` transaction closes that connection out from
    # under the test (autocommit-state mismatch) -- a test-harness
    # artifact with no bearing on the real, non-transactional scheduler
    # process this test is otherwise exercising faithfully.
    monkeypatch.setattr(scheduler_module, "close_old_connections", lambda: None)

    owner = create_account("scheduler-privacy-owner")
    note = services.create_note(owner=owner)
    note.title = "Scheduler Privacy Secret Title"
    note.save(update_fields=["title"])
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=100))

    with caplog.at_level(logging.DEBUG):
        scheduler_module.Command()._run_cycle()

    log_text = "\n".join(r.message for r in caplog.records)
    assert "Scheduler Privacy Secret Title" not in log_text
    assert owner.username not in log_text


# -- advisory-lock wiring: genuine PostgreSQL concurrency ----------------------


@pytest.mark.django_db(transaction=True)
def test_scheduler_and_manual_command_share_the_same_advisory_lock(monkeypatch):
    """Confirms the scheduler code path contends for the exact same
    session advisory lock as the manual `purge_expired_trash` command --
    a confidence check on this wiring, not a re-proof of the
    underlying lock mechanics already covered by
    `notes/tests/test_purge_race.py`."""
    from django.db import connections
    from notes import purge as purge_module

    owner = create_account("scheduler-lock-owner")
    note = services.create_note(owner=owner)
    services.rename_note(note=note, title="Scheduler Lock Note")
    services.move_note_to_trash(note=note)
    Note.objects.filter(pk=note.pk).update(trashed_at=timezone.now() - timedelta(days=100))

    lock_acquired = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    original = purge_module._is_purge_eligible

    def pausing(*, trashed_at, cutoff):
        call_count["n"] += 1
        if call_count["n"] == 1:
            lock_acquired.set()
            assert proceed.wait(timeout=5), "manual command thread never signaled proceed"
        return original(trashed_at=trashed_at, cutoff=cutoff)

    monkeypatch.setattr(purge_module, "_is_purge_eligible", pausing)

    scheduler_outcome = {}

    def run_scheduler_cycle():
        try:
            scheduler_outcome["result"] = purge_module.run_purge_cycle(
                dry_run=False, note_batch_size=200, folder_batch_size=200
            )
        except Exception as exc:  # pragma: no cover
            scheduler_outcome["error"] = exc
        finally:
            connections.close_all()

    scheduler_thread = threading.Thread(target=run_scheduler_cycle)
    scheduler_thread.start()
    assert lock_acquired.wait(timeout=5), (
        "scheduler cycle did not reach the locked eligibility check"
    )

    manual_outcome = {}

    def run_manual_command():
        from io import StringIO

        try:
            stdout = StringIO()
            call_command("purge_expired_trash", "--execute", stdout=stdout)
            manual_outcome["output"] = stdout.getvalue()
        except Exception as exc:  # pragma: no cover
            manual_outcome["error"] = exc
        finally:
            connections.close_all()

    manual_thread = threading.Thread(target=run_manual_command)
    manual_thread.start()
    time.sleep(0.3)
    proceed.set()
    scheduler_thread.join(timeout=5)
    manual_thread.join(timeout=5)

    assert "error" not in scheduler_outcome, scheduler_outcome.get("error")
    assert "error" not in manual_outcome, manual_outcome.get("error")

    # The scheduler-style call held the lock first, so the manual command
    # must have safely skipped -- no duplicate purge occurred either way.
    assert (
        "Skipped: another purge cycle already holds the advisory lock." in manual_outcome["output"]
    )
    assert scheduler_outcome["result"].lock_acquired is True
    assert scheduler_outcome["result"].notes_purged == 1
    assert not Note.objects.filter(pk=note.pk).exists()
