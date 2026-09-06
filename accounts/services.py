from __future__ import annotations

import hashlib
import secrets
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from zoneinfo import available_timezones as _available_timezones

from django.conf import settings
from django.contrib.auth import get_user_model, update_session_auth_hash
from django.db import connection, transaction
from django.db.models import F
from django.http import HttpRequest
from django.utils import timezone

from accounts.identifiers import normalize_email, normalize_username
from accounts.models import AuditEvent, User, UserInvitation

ADMIN_OPERATION_LOCK_ID = 502_001_001
SESSION_GENERATION_KEY = "ridgenote_session_generation"
LAST_ACTIVITY_KEY = "ridgenote_last_activity"

# account-deletion recovery period. Dedicated,
# account-specific, and fixed for Version One: never reused, aliased, or
# derived from any note/folder Trash-retention constant (see
# `notes.services.TRASH_VISIBLE_MAX_AGE`/`TRASH_RECOVERABLE_MAX_AGE`, which
# protect against a different risk on a different object entirely).
ACCOUNT_DELETION_RECOVERY_PERIOD = timedelta(days=7)

SENSITIVE_DETAIL_KEYS = {
    "password",
    "password1",
    "password2",
    "new_password",
    "new_password1",
    "new_password2",
    "old_password",
    "secret",
    "token",
    "hash",
    "password_hash",
}


class LastActiveAdminError(ValueError):
    pass


class BootstrapClosedError(ValueError):
    pass


class AlreadyPendingDeletionError(ValueError):
    pass


class NotPendingDeletionError(ValueError):
    pass


class PurgeDeadlineNotElapsedError(ValueError):
    pass


class SelfPurgeNotAllowedError(ValueError):
    pass


class InvalidAccountDeletionStateError(ValueError):
    pass


class PurgeActorNotAuthorizedError(ValueError):
    pass


class InvitationActorNotAuthorizedError(ValueError):
    pass


class InvitationTargetAlreadySetUpError(ValueError):
    pass


class InvitationAlreadyAcceptedError(ValueError):
    pass


class InvitationNoLongerEligibleError(ValueError):
    pass


class TargetSetupIncompleteError(ValueError):
    pass


def admin_exists() -> bool:
    return get_user_model().objects.filter(role=User.ROLE_ADMIN).exists()


def active_admin_count() -> int:
    return get_user_model().objects.filter(role=User.ROLE_ADMIN, is_active=True).count()


def acquire_admin_operation_lock() -> None:
    with connection.cursor() as cursor:
        cursor.execute("select pg_advisory_xact_lock(%s)", [ADMIN_OPERATION_LOCK_ID])


def client_ip(request: HttpRequest | None) -> str:
    if request is None:
        return ""
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",", maxsplit=1)[0].strip()
    return request.META.get("REMOTE_ADDR", "")


def sanitize_detail_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return sanitize_details(value)
    if isinstance(value, (list, tuple)):
        return [sanitize_detail_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def sanitize_details(details: Mapping[str, Any] | None) -> dict[str, Any]:
    clean: dict[str, Any] = {}
    for key, value in (details or {}).items():
        if key.lower() in SENSITIVE_DETAIL_KEYS:
            continue
        clean[key] = sanitize_detail_value(value)
    return clean


def record_audit_event(
    event_type: str,
    *,
    actor: User | None = None,
    target_user: User | None = None,
    request: HttpRequest | None = None,
    source: str = AuditEvent.SOURCE_WEB,
    details: Mapping[str, Any] | None = None,
) -> AuditEvent:
    ip = client_ip(request) or None
    user_agent = request.META.get("HTTP_USER_AGENT", "") if request else ""
    return AuditEvent.objects.create(
        event_type=event_type,
        actor=actor,
        target_user=target_user,
        actor_username_snapshot=actor.get_username() if actor else "",
        target_username_snapshot=target_user.get_username() if target_user else "",
        source=source,
        ip_address=ip,
        user_agent=user_agent,
        details=sanitize_details(details),
    )


@transaction.atomic
def create_initial_admin(form, request: HttpRequest | None = None) -> User:
    acquire_admin_operation_lock()
    if admin_exists():
        raise BootstrapClosedError("Initial setup is closed.")
    user = form.save(commit=False)
    user.role = User.ROLE_ADMIN
    user.is_active = True
    user.is_staff = False
    user.is_superuser = False
    user.must_change_password = False
    user.setup_completed_at = timezone.now()
    user.save()
    record_audit_event(
        AuditEvent.EVENT_INITIAL_ADMIN_CREATED,
        actor=user,
        target_user=user,
        request=request,
        details={"username": user.get_username()},
    )
    return user


def ensure_not_last_active_admin(
    user: User,
    *,
    changing_role_to: str | None = None,
    disabling: bool = False,
    scheduling_deletion: bool = False,
) -> None:
    would_stop_being_active_admin = (
        user.role == User.ROLE_ADMIN
        and user.is_active
        and (changing_role_to == User.ROLE_USER or disabling or scheduling_deletion)
    )
    if not would_stop_being_active_admin:
        return
    remaining = (
        get_user_model()
        .objects.filter(role=User.ROLE_ADMIN, is_active=True)
        .exclude(pk=user.pk)
        .count()
    )
    if remaining == 0:
        raise LastActiveAdminError("The last active administrator cannot be changed this way.")


@transaction.atomic
def create_user(*, form, actor: User, request: HttpRequest | None = None) -> User:
    user = form.save(commit=False)
    user.is_staff = False
    user.is_superuser = False
    user.setup_completed_at = timezone.now()
    user.save()
    record_audit_event(
        AuditEvent.EVENT_USER_CREATED,
        actor=actor,
        target_user=user,
        request=request,
        details={"role": user.role, "is_active": user.is_active},
    )
    return user


@dataclass(frozen=True)
class CreatedInvitedUser:
    user: User
    invitation: UserInvitation
    raw_token: str


@transaction.atomic
def create_invited_user(
    *, form, actor: User, request: HttpRequest | None = None
) -> CreatedInvitedUser:
    """Creates the User and issues its initial
    invitation as one coherent operation -- `issue_user_invitation()` is
    unmodified and already `@transaction.atomic`; nested inside this
    function's own atomic block it composes as a savepoint, so if
    issuance raises for any reason the User row (and its
    EVENT_USER_CREATED audit event) rolls back too. No orphaned
    setup-incomplete account with no invitation can result."""
    user = form.save(commit=False)
    user.is_staff = False
    user.is_superuser = False
    user.must_change_password = False
    user.set_unusable_password()
    user.save()
    record_audit_event(
        AuditEvent.EVENT_USER_CREATED,
        actor=actor,
        target_user=user,
        request=request,
        details={"role": user.role, "is_active": user.is_active},
    )
    issued = issue_user_invitation(target=user, actor=actor, request=request)
    return CreatedInvitedUser(user=user, invitation=issued.invitation, raw_token=issued.raw_token)


@transaction.atomic
def set_user_role(
    *,
    target: User,
    role: str,
    actor: User,
    request: HttpRequest | None = None,
) -> User:
    acquire_admin_operation_lock()
    target = get_user_model().objects.select_for_update().get(pk=target.pk)
    previous_role = target.role
    ensure_not_last_active_admin(target, changing_role_to=role)
    target.role = role
    target.is_staff = False
    target.is_superuser = False
    target.save(update_fields=["role", "is_staff", "is_superuser"])
    if previous_role != role:
        record_audit_event(
            AuditEvent.EVENT_ROLE_CHANGED,
            actor=actor,
            target_user=target,
            request=request,
            details={"old_role": previous_role, "new_role": role},
        )
    return target


@transaction.atomic
def set_user_active(
    *,
    target: User,
    active: bool,
    actor: User,
    request: HttpRequest | None = None,
) -> User:
    acquire_admin_operation_lock()
    target = get_user_model().objects.select_for_update().get(pk=target.pk)
    if not active:
        ensure_not_last_active_admin(target, disabling=True)
    was_active = target.is_active
    target.is_active = active
    target.disabled_at = None if active else timezone.now()
    fields = ["is_active", "disabled_at"]
    if not active:
        target.session_generation = F("session_generation") + 1
        fields.append("session_generation")
    target.save(update_fields=fields)
    target.refresh_from_db()
    if was_active != active:
        record_audit_event(
            AuditEvent.EVENT_USER_ENABLED if active else AuditEvent.EVENT_USER_DISABLED,
            actor=actor,
            target_user=target,
            request=request,
        )
    return target


@transaction.atomic
def set_user_email(
    *,
    target: User,
    email: str,
    actor: User,
    request: HttpRequest | None = None,
) -> User:
    """Admin correction of a User's email.

    Touches only `User.email` -- never invitation, setup, active, or
    deletion state, role, password, or sessions. A future Issue/Reissue
    already reads `User.email` live at send time, so a corrected email
    is picked up automatically with no further action here. Uniqueness
    is expected to have already been validated by the calling form
    (`accounts.forms.email_conflicts()`); the DB's own canonical/unique
    constraints remain the final authority regardless.
    """
    acquire_admin_operation_lock()
    target = get_user_model().objects.select_for_update().get(pk=target.pk)
    normalized_email = normalize_email(email)
    previous_email = target.email
    target.email = normalized_email
    target.save(update_fields=["email"])
    if previous_email != normalized_email:
        record_audit_event(
            AuditEvent.EVENT_EMAIL_CHANGED,
            actor=actor,
            target_user=target,
            request=request,
            details={"old_email": previous_email, "new_email": normalized_email},
        )
    return target


@transaction.atomic
def schedule_user_deletion(
    *,
    target: User,
    actor: User,
    request: HttpRequest | None = None,
    source: str = AuditEvent.SOURCE_WEB,
) -> User:
    acquire_admin_operation_lock()
    target = get_user_model().objects.select_for_update().get(pk=target.pk)
    if target.deletion_scheduled_at is not None:
        raise AlreadyPendingDeletionError("Account deletion is already pending for this user.")
    ensure_not_last_active_admin(target, scheduling_deletion=True)

    now = timezone.now()
    was_active = target.is_active

    target.deletion_restore_is_active = was_active
    target.deletion_scheduled_at = now
    target.deletion_recovery_deadline = now + ACCOUNT_DELETION_RECOVERY_PERIOD
    target.is_active = False
    fields = [
        "deletion_restore_is_active",
        "deletion_scheduled_at",
        "deletion_recovery_deadline",
        "is_active",
    ]
    if was_active:
        # Matches `set_user_active(active=False)`'s exact disable semantics:
        # a fresh disable timestamp and a session-generation bump, since
        # this is a genuine active-to-disabled transition with live
        # sessions to revoke. An already-disabled account has no live
        # sessions to revoke and keeps its own, earlier `disabled_at`
        # untouched rather than overwriting it with a new one here.
        target.disabled_at = now
        target.session_generation = F("session_generation") + 1
        fields.extend(["disabled_at", "session_generation"])
    target.save(update_fields=fields)
    target.refresh_from_db()

    record_audit_event(
        AuditEvent.EVENT_ACCOUNT_DELETION_SCHEDULED,
        actor=actor,
        target_user=target,
        request=request,
        source=source,
        details={
            "deletion_scheduled_at": target.deletion_scheduled_at.isoformat(),
            "deletion_recovery_deadline": target.deletion_recovery_deadline.isoformat(),
            "was_active": was_active,
        },
    )
    return target


@transaction.atomic
def cancel_user_deletion(
    *,
    target: User,
    actor: User,
    request: HttpRequest | None = None,
    source: str = AuditEvent.SOURCE_WEB,
) -> User:
    acquire_admin_operation_lock()
    target = get_user_model().objects.select_for_update().get(pk=target.pk)
    if target.deletion_scheduled_at is None:
        raise NotPendingDeletionError("Account deletion is not pending for this user.")

    restore_active = target.deletion_restore_is_active
    target.is_active = restore_active
    if restore_active:
        # Matches `set_user_active(active=True)`'s exact reactivation
        # semantics. Restoring to the ordinarily-disabled state instead
        # leaves `disabled_at` completely untouched -- it was never
        # modified by `schedule_user_deletion()` for an already-disabled
        # account, so it still holds its own original, pre-scheduling
        # value here.
        target.disabled_at = None
    target.deletion_scheduled_at = None
    target.deletion_recovery_deadline = None
    target.deletion_restore_is_active = None
    target.save(
        update_fields=[
            "is_active",
            "disabled_at",
            "deletion_scheduled_at",
            "deletion_recovery_deadline",
            "deletion_restore_is_active",
        ]
    )
    target.refresh_from_db()

    record_audit_event(
        AuditEvent.EVENT_ACCOUNT_DELETION_CANCELLED,
        actor=actor,
        target_user=target,
        request=request,
        source=source,
        details={"restored_active": restore_active},
    )
    return target


@dataclass(frozen=True)
class UserDeletionStatus:
    is_pending: bool
    scheduled_at: datetime | None
    recovery_deadline: datetime | None
    is_purge_eligible: bool
    restore_is_active: bool | None


@dataclass(frozen=True)
class AccountOwnershipCounts:
    active_notes: int
    trashed_notes: int
    active_folders: int
    trashed_folders: int
    tags: int


def count_owned_content(*, target: User) -> AccountOwnershipCounts:
    from notes.models import Folder, Note, Tag

    return AccountOwnershipCounts(
        active_notes=Note.objects.filter(owner=target, trashed_at__isnull=True).count(),
        trashed_notes=Note.objects.filter(owner=target, trashed_at__isnull=False).count(),
        active_folders=Folder.objects.filter(owner=target, trashed_at__isnull=True).count(),
        trashed_folders=Folder.objects.filter(owner=target, trashed_at__isnull=False).count(),
        tags=Tag.objects.filter(owner=target).count(),
    )


def user_deletion_status(*, target: User) -> UserDeletionStatus:
    is_pending = target.deletion_scheduled_at is not None
    is_purge_eligible = bool(
        is_pending
        and target.deletion_recovery_deadline is not None
        and target.deletion_recovery_deadline <= timezone.now()
    )
    return UserDeletionStatus(
        is_pending=is_pending,
        scheduled_at=target.deletion_scheduled_at,
        recovery_deadline=target.deletion_recovery_deadline,
        is_purge_eligible=is_purge_eligible,
        restore_is_active=target.deletion_restore_is_active,
    )


@dataclass(frozen=True)
class PurgeAccountResult:
    username_snapshot: str
    notes_active: int
    notes_trashed: int
    folders_active: int
    folders_trashed: int
    tags: int


def _purge_rejection_audit(
    event_type: str,
    *,
    actor: User | None,
    target: User | None,
    request: HttpRequest | None,
    source: str,
    details: Mapping[str, Any] | None = None,
) -> None:
    with transaction.atomic():
        record_audit_event(
            event_type,
            actor=actor,
            target_user=target,
            request=request,
            source=source,
            details=details,
        )


def purge_user_account(
    *,
    target: User,
    actor: User,
    request: HttpRequest | None = None,
    source: str = AuditEvent.SOURCE_WEB,
) -> PurgeAccountResult:
    from notes.models import Folder, Note, Tag

    UserModel = get_user_model()

    live_actor: User | None = None
    locked_target: User | None = None

    try:
        with transaction.atomic():
            acquire_admin_operation_lock()

            live_actor = UserModel.objects.filter(pk=actor.pk).first()
            actor_is_authorized = (
                live_actor is not None
                and live_actor.is_active
                and live_actor.role == User.ROLE_ADMIN
            )
            if not actor_is_authorized:
                live_actor = None
                raise PurgeActorNotAuthorizedError(
                    "The acting administrator is no longer authorized to purge accounts."
                )

            locked_target = UserModel.objects.select_for_update().get(pk=target.pk)

            if live_actor.pk == locked_target.pk:
                raise SelfPurgeNotAllowedError("An administrator cannot purge their own account.")

            if locked_target.deletion_scheduled_at is None:
                raise NotPendingDeletionError("Account deletion is not pending for this user.")

            now = timezone.now()
            lifecycle_is_consistent = (
                locked_target.deletion_recovery_deadline is not None
                and locked_target.deletion_restore_is_active is not None
                and locked_target.is_active is False
            )
            if not lifecycle_is_consistent:
                raise InvalidAccountDeletionStateError(
                    "Account deletion lifecycle state is malformed for this user."
                )

            if locked_target.deletion_recovery_deadline > now:
                raise PurgeDeadlineNotElapsedError(
                    "The recovery deadline for this account has not yet elapsed."
                )

            username_snapshot = locked_target.get_username()
            notes_active = Note.objects.filter(owner=locked_target, trashed_at__isnull=True).count()
            notes_trashed = Note.objects.filter(
                owner=locked_target, trashed_at__isnull=False
            ).count()
            folders_active = Folder.objects.filter(
                owner=locked_target, trashed_at__isnull=True
            ).count()
            folders_trashed = Folder.objects.filter(
                owner=locked_target, trashed_at__isnull=False
            ).count()
            tags = Tag.objects.filter(owner=locked_target).count()

            record_audit_event(
                AuditEvent.EVENT_ACCOUNT_PERMANENTLY_PURGED,
                actor=live_actor,
                target_user=locked_target,
                request=request,
                source=source,
                details={
                    "username_snapshot": username_snapshot,
                    "notes_active": notes_active,
                    "notes_trashed": notes_trashed,
                    "folders_active": folders_active,
                    "folders_trashed": folders_trashed,
                    "tags": tags,
                },
            )

            locked_target.delete()

            return PurgeAccountResult(
                username_snapshot=username_snapshot,
                notes_active=notes_active,
                notes_trashed=notes_trashed,
                folders_active=folders_active,
                folders_trashed=folders_trashed,
                tags=tags,
            )
    except PurgeActorNotAuthorizedError:
        _purge_rejection_audit(
            AuditEvent.EVENT_ACCOUNT_PURGE_REJECTED_ACTOR_UNAUTHORIZED,
            actor=None,
            target=locked_target or target,
            request=request,
            source=source,
        )
        raise
    except SelfPurgeNotAllowedError:
        _purge_rejection_audit(
            AuditEvent.EVENT_ACCOUNT_PURGE_REJECTED_SELF,
            actor=live_actor,
            target=locked_target or target,
            request=request,
            source=source,
        )
        raise
    except PurgeDeadlineNotElapsedError:
        recovery_deadline = locked_target.deletion_recovery_deadline if locked_target else None
        _purge_rejection_audit(
            AuditEvent.EVENT_ACCOUNT_PURGE_REJECTED_DEADLINE,
            actor=live_actor,
            target=locked_target or target,
            request=request,
            source=source,
            details={
                "reason": "deadline_not_elapsed",
                "deletion_recovery_deadline": (
                    recovery_deadline.isoformat() if recovery_deadline else None
                ),
            },
        )
        raise
    except NotPendingDeletionError:
        raise
    except UserModel.DoesNotExist:
        # Target already gone (e.g. a losing side of a repeated/racing
        # purge attempt). No safe, still-existing target row remains to
        # attribute an audit event to -- propagate without one, matching
        # "target-missing"/"repeated-purge-after-success" exclusions.
        raise
    except InvalidAccountDeletionStateError as exc:
        _purge_rejection_audit(
            AuditEvent.EVENT_ACCOUNT_PURGE_FAILED,
            actor=live_actor,
            target=locked_target or target,
            request=request,
            source=source,
            details={"exception": type(exc).__name__, "reason": str(exc)},
        )
        raise
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: unexpected-failure audit path
        _purge_rejection_audit(
            AuditEvent.EVENT_ACCOUNT_PURGE_FAILED,
            actor=live_actor,
            target=locked_target or target,
            request=request,
            source=source,
            details={"exception": type(exc).__name__, "reason": str(exc)},
        )
        raise


@transaction.atomic
def reset_user_password(
    *,
    target: User,
    new_password: str,
    must_change_password: bool,
    actor: User,
    request: HttpRequest | None = None,
    event_type: str = AuditEvent.EVENT_ADMIN_PASSWORD_RESET,
    source: str = AuditEvent.SOURCE_WEB,
) -> User:
    target = get_user_model().objects.select_for_update().get(pk=target.pk)
    if not target.has_completed_setup:
        raise TargetSetupIncompleteError(
            "This account has not completed setup and cannot have its password "
            "reset by an administrator. Manage its invitation instead."
        )
    target.set_password(new_password)
    target.must_change_password = must_change_password
    target.session_generation = F("session_generation") + 1
    target.save(update_fields=["password", "must_change_password", "session_generation"])
    target.refresh_from_db()
    record_audit_event(
        event_type,
        actor=actor,
        target_user=target,
        request=request,
        source=source,
        details={"must_change_password": must_change_password},
    )
    return target


def _lock_and_set_password(*, user: User, new_password: str) -> User:
    """Locks ``user``'s row and applies the new password hash, returning the
    locked, now-current instance. Shared by the forced and self-service
    password-change flows below -- the only genuinely duplicated step
    between them; each caller still sets its own remaining fields and
    calls `save()` explicitly, since forced-change also clears
    `must_change_password` and self-service does not, and the two flows'
    audit events are deliberately distinct."""
    locked = get_user_model().objects.select_for_update().get(pk=user.pk)
    locked.set_password(new_password)
    return locked


@transaction.atomic
def complete_forced_password_change(*, user: User, new_password: str, request: HttpRequest) -> User:
    user = _lock_and_set_password(user=user, new_password=new_password)
    user.must_change_password = False
    user.save(update_fields=["password", "must_change_password"])
    update_session_auth_hash(request, user)
    request.session[SESSION_GENERATION_KEY] = user.session_generation
    mark_session_activity(request)
    record_audit_event(
        AuditEvent.EVENT_FORCED_PASSWORD_CHANGE_COMPLETED,
        actor=user,
        target_user=user,
        request=request,
    )
    return user


@transaction.atomic
def complete_account_password_change(
    *, user: User, new_password: str, request: HttpRequest
) -> User:
    """The Account page's self-service password
    change. Unlike `complete_forced_password_change()`, this never touches
    `must_change_password` -- a user reaching `/account/` is not inside
    the forced-change flow (the existing `SessionSecurityMiddleware` gate
    already redirects anyone with that flag set away from every other
    page, `/account/` included), so there is nothing to clear. Current-
    password verification happens earlier, in `AccountPasswordChangeForm`,
    before this function is ever called."""
    user = _lock_and_set_password(user=user, new_password=new_password)
    user.save(update_fields=["password"])
    update_session_auth_hash(request, user)
    request.session[SESSION_GENERATION_KEY] = user.session_generation
    mark_session_activity(request)
    record_audit_event(
        AuditEvent.EVENT_ACCOUNT_PASSWORD_CHANGE_COMPLETED,
        actor=user,
        target_user=user,
        request=request,
    )
    return user


@lru_cache(maxsize=1)
def available_timezone_names() -> frozenset[str]:
    """`zoneinfo.available_timezones` scans the
    system tzdata directory on every call -- cached for the life of the
    process, since the container's baked-in tzdata cannot change without a
    restart. Shared by `is_valid_timezone_name()`, the Account page's
    timezone `<datalist>` options, and any future caller needing the full
    IANA set."""
    return _available_timezones()


def is_valid_timezone_name(timezone_name: str) -> bool:
    """The sole authoritative IANA-membership check,
    reused by both `PreferencesTimezoneForm.clean_timezone_name()` (persistence
    gate) and `resolve_display_timezone()` (defensive read-time re-check).
    Deliberately membership-based (`available_timezone_names()`), not a
    bare `ZoneInfo()` construction attempt -- a clear, explicit
    accept/reject check rather than exception-based control flow."""
    return timezone_name in available_timezone_names()


def resolve_display_timezone(user: User | None) -> ZoneInfo:
    """Effective-timezone resolution --
    never calls `timezone.activate()`, never consults browser-provided
    data, and is independent of any request. Precedence: (1) the user's
    own explicit, currently-valid `timezone_name`; (2) `settings.TIME_ZONE`;
    (3) UTC, only if `settings.TIME_ZONE` itself is somehow invalid. A
    stored value that was valid when saved but no longer resolves (a
    future tzdata change) degrades safely to (2)/(3) rather than raising,
    so a corrupted/stale preference can never break an unrelated request
    such as a backup download."""
    stored = getattr(user, "timezone_name", "") or ""
    if stored and is_valid_timezone_name(stored):
        try:
            return ZoneInfo(stored)
        except ZoneInfoNotFoundError:
            pass
    try:
        return ZoneInfo(settings.TIME_ZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo("UTC")


def reset_failed_login_state(user: User) -> None:
    user.failed_login_count = 0
    user.failed_login_window_started_at = None
    user.locked_until = None
    user.last_successful_login_at = timezone.now()
    user.save(
        update_fields=[
            "failed_login_count",
            "failed_login_window_started_at",
            "locked_until",
            "last_successful_login_at",
        ]
    )


@transaction.atomic
def record_successful_login(user: User, request: HttpRequest) -> None:
    locked_before = bool(user.locked_until)
    locked_until = user.locked_until
    user = get_user_model().objects.select_for_update().get(pk=user.pk)
    reset_failed_login_state(user)
    request.session[SESSION_GENERATION_KEY] = user.session_generation
    mark_session_activity(request)
    if locked_before and locked_until and locked_until <= timezone.now():
        record_audit_event(
            AuditEvent.EVENT_ACCOUNT_UNLOCKED,
            actor=user,
            target_user=user,
            request=request,
        )
    record_audit_event(
        AuditEvent.EVENT_LOGIN_SUCCESS,
        actor=user,
        target_user=user,
        request=request,
    )


@transaction.atomic
def record_failed_login(username: str, request: HttpRequest | None = None) -> None:
    UserModel = get_user_model()
    now = timezone.now()
    normalized_username = normalize_username(username)
    try:
        user = UserModel.objects.select_for_update().get(username=normalized_username)
    except UserModel.DoesNotExist:
        record_audit_event(
            AuditEvent.EVENT_LOGIN_FAILED,
            request=request,
            details={"username": normalized_username, "account_found": False},
        )
        return

    if not user.is_active:
        record_audit_event(
            AuditEvent.EVENT_LOGIN_FAILED,
            target_user=user,
            request=request,
            details={"account_inactive": True},
        )
        return

    if not user.has_completed_setup:
        record_audit_event(
            AuditEvent.EVENT_LOGIN_FAILED,
            target_user=user,
            request=request,
            details={"setup_incomplete": True},
        )
        return

    if user.locked_until and user.locked_until > now:
        record_audit_event(
            AuditEvent.EVENT_LOGIN_FAILED,
            target_user=user,
            request=request,
            details={"locked": True},
        )
        return

    if user.locked_until and user.locked_until <= now:
        user.failed_login_count = 0
        user.failed_login_window_started_at = None
        user.locked_until = None
        record_audit_event(AuditEvent.EVENT_ACCOUNT_UNLOCKED, target_user=user, request=request)

    window = timedelta(seconds=settings.RIDGENOTE_LOGIN_LOCKOUT_WINDOW_SECONDS)
    if (
        not user.failed_login_window_started_at
        or now - user.failed_login_window_started_at > window
    ):
        user.failed_login_window_started_at = now
        user.failed_login_count = 1
    else:
        user.failed_login_count += 1

    locked = user.failed_login_count >= settings.RIDGENOTE_LOGIN_LOCKOUT_THRESHOLD
    if locked:
        user.locked_until = now + timedelta(
            seconds=settings.RIDGENOTE_LOGIN_LOCKOUT_DURATION_SECONDS,
        )
    user.save(
        update_fields=[
            "failed_login_count",
            "failed_login_window_started_at",
            "locked_until",
        ]
    )
    record_audit_event(AuditEvent.EVENT_LOGIN_FAILED, target_user=user, request=request)
    if locked:
        record_audit_event(AuditEvent.EVENT_ACCOUNT_LOCKED, target_user=user, request=request)


def is_user_locked(user: User) -> bool:
    return bool(user.locked_until and user.locked_until > timezone.now())


def mark_session_activity(request: HttpRequest) -> None:
    request.session[LAST_ACTIVITY_KEY] = timezone.now().timestamp()


def session_is_expired(request: HttpRequest) -> bool:
    last_activity = request.session.get(LAST_ACTIVITY_KEY)
    if last_activity is None:
        return False
    expires_at = float(last_activity) + settings.RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS
    return timezone.now().timestamp() > expires_at


def static_url_path_prefix() -> str:
    configured_path = urlsplit(settings.STATIC_URL).path or settings.STATIC_URL
    prefix = f"/{configured_path.lstrip('/')}"
    if not prefix.endswith("/"):
        prefix = f"{prefix}/"
    return prefix


def should_refresh_session_activity(request: HttpRequest) -> bool:
    if not request.user.is_authenticated:
        return False
    if request.path == "/health/" or request.path.startswith(static_url_path_prefix()):
        return False
    if request.path == "/session/status/":
        return False
    return True


# -- secure invitation-token foundation ---------


@dataclass(frozen=True)
class IssuedInvitation:
    invitation: UserInvitation
    raw_token: str


def _require_authorized_invitation_actor(actor: User) -> User:
    # Defense-in-depth, mirroring `purge_user_account()`'s own live-actor
    # re-fetch: ships no view/URL, so no `admin_required(request)`
    # gate exists yet for these two functions to lean on -- the service
    # itself must not trust the passed-in `actor` object.
    live_actor = get_user_model().objects.filter(pk=actor.pk).first()
    if live_actor is None or not live_actor.is_active or live_actor.role != User.ROLE_ADMIN:
        raise InvitationActorNotAuthorizedError(
            "The acting administrator is not currently authorized to manage invitations."
        )
    return live_actor


def _generate_invitation_token() -> tuple[str, str]:
    raw_token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    return raw_token, token_hash


def _lock_invitation_for_update(invitation_pk) -> UserInvitation:
    return UserInvitation.objects.select_for_update().get(pk=invitation_pk)


@transaction.atomic
def issue_user_invitation(
    *,
    target: User,
    actor: User,
    request: HttpRequest | None = None,
) -> IssuedInvitation:
    live_actor = _require_authorized_invitation_actor(actor)
    target = get_user_model().objects.select_for_update().get(pk=target.pk)
    if target.has_completed_setup:
        raise InvitationTargetAlreadySetUpError(
            "This user has already completed initial account setup."
        )

    now = timezone.now()
    # The target-user row lock above already serializes concurrent
    # issue/reissue calls for this same user -- including the case of zero
    # prior invitation rows -- so this bulk supersession update needs no
    # locking of its own.
    UserInvitation.objects.filter(
        user=target,
        accepted_at__isnull=True,
        revoked_at__isnull=True,
    ).update(revoked_at=now)

    raw_token, token_hash = _generate_invitation_token()
    expires_at = now + timedelta(minutes=settings.RIDGENOTE_INVITATION_EXPIRY_MINUTES)
    invitation = UserInvitation.objects.create(
        user=target,
        token_hash=token_hash,
        expires_at=expires_at,
        created_by=live_actor,
    )
    record_audit_event(
        AuditEvent.EVENT_INVITATION_ISSUED,
        actor=live_actor,
        target_user=target,
        request=request,
        details={
            "invitation_id": invitation.pk,
            "expires_at": expires_at.isoformat(),
        },
    )
    return IssuedInvitation(invitation=invitation, raw_token=raw_token)


@transaction.atomic
def revoke_user_invitation(
    *,
    invitation: UserInvitation,
    actor: User,
    request: HttpRequest | None = None,
) -> UserInvitation:
    live_actor = _require_authorized_invitation_actor(actor)
    # Lock the target user row before the invitation row, in the same
    # order `issue_user_invitation()` uses (user row, then invitation
    # row). Both functions reference the target user via the audit-event
    # foreign key from within the same transaction; a consistent lock
    # order across the two functions avoids a Postgres deadlock between a
    # concurrent revoke and reissue for the same user.
    get_user_model().objects.select_for_update().get(pk=invitation.user_id)
    invitation = _lock_invitation_for_update(invitation.pk)

    if invitation.accepted_at is not None:
        raise InvitationAlreadyAcceptedError(
            "This invitation has already been accepted and cannot be revoked."
        )
    if invitation.revoked_at is not None:
        return invitation
    if invitation.expires_at <= timezone.now():
        return invitation

    invitation.revoked_at = timezone.now()
    invitation.save(update_fields=["revoked_at"])
    record_audit_event(
        AuditEvent.EVENT_INVITATION_REVOKED,
        actor=live_actor,
        target_user=invitation.user,
        request=request,
        details={"invitation_id": invitation.pk},
    )
    return invitation


def record_invitation_sent(
    *,
    invitation: UserInvitation,
    target: User,
    actor: User,
    recipient_email: str,
    request: HttpRequest | None = None,
) -> AuditEvent:
    """Called by a view only after
    `accounts.mail.send_invitation_email()` has already reported a
    successful send -- never on failure (a mail-transport failure is
    operational telemetry, not a durable domain event). Kept in
    `accounts/services.py`, not
    `accounts/mail.py` or a view, preserving the existing convention that
    every `AuditEvent` write in this codebase lives here. Deliberately no
    `@transaction.atomic` -- this always runs strictly after the
    triggering invitation mutation's own transaction has already
    committed, and is a single insert with nothing else to coordinate."""
    return record_audit_event(
        AuditEvent.EVENT_INVITATION_SENT,
        actor=actor,
        target_user=target,
        request=request,
        details={
            "invitation_id": invitation.pk,
            "recipient_email": recipient_email,
            "delivery": "smtp",
        },
    )


def invitation_for_raw_token(raw_token: str) -> UserInvitation | None:
    if not raw_token or not isinstance(raw_token, str):
        return None
    token_hash = hashlib.sha256(raw_token.encode("utf-8")).hexdigest()
    invitation = UserInvitation.objects.filter(token_hash=token_hash).first()
    if invitation is None or not invitation.is_valid:
        return None
    return invitation


def latest_invitation_for_user(target: User) -> UserInvitation | None:
    """The single most recent invitation row for
    `target`, or `None` -- deliberately not a history query. Automatic
    supersession on reissue always creates a newer row than
    whatever it superseded, so "most recent by creation time" is always
    the currently-relevant one, never an arbitrary older live/expired
    row."""
    return target.invitations.order_by("-created_at", "-id").first()


@transaction.atomic
def accept_user_invitation(
    *,
    invitation_id: int,
    password: str,
    request: HttpRequest | None = None,
) -> User:
    """Never accepts a raw token -- the caller has
    already reduced the invitation to a session-held PK. Preserves the
    `user row -> invitation row` lock order -- an
    initial unlocked lookup is used only to learn the target's `user_id`,
    then both rows are re-fetched under lock and every eligibility
    condition is re-checked from scratch, trusting nothing the caller
    determined earlier (e.g. at the setup GET)."""
    unlocked_invitation = UserInvitation.objects.filter(pk=invitation_id).first()
    if unlocked_invitation is None:
        raise InvitationNoLongerEligibleError("This invitation is no longer available.")

    target = get_user_model().objects.select_for_update().get(pk=unlocked_invitation.user_id)
    invitation = _lock_invitation_for_update(invitation_id)

    eligible = (
        invitation.accepted_at is None
        and invitation.revoked_at is None
        and invitation.expires_at > timezone.now()
        and target.setup_completed_at is None
        and target.is_active
    )
    if not eligible:
        raise InvitationNoLongerEligibleError("This invitation is no longer available.")

    now = timezone.now()
    target.set_password(password)
    target.setup_completed_at = now
    target.must_change_password = False
    target.save(update_fields=["password", "setup_completed_at", "must_change_password"])

    invitation.accepted_at = now
    invitation.save(update_fields=["accepted_at"])

    record_audit_event(
        AuditEvent.EVENT_INVITATION_ACCEPTED,
        actor=target,
        target_user=target,
        request=request,
        details={"invitation_id": invitation.pk},
    )
    return target
