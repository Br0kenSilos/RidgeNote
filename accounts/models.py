# Imported from the plain, Django-model-free
# `core.tag_colors` module -- not `notes.models` directly. INSTALLED_APPS
# loads `accounts` before `notes`; a bare `from notes.models import ...`
# here would force `notes`'s `Tag`/`Folder`/`Note` model classes to
# register with Django's app registry before `notes`'s own `AppConfig`
# is ready, a known circular-app-loading hazard. `core.tag_colors` has
# no such risk -- it defines no models, only constants.
from core.tag_colors import DEFAULT_TAG_COLOR, TAG_COLOR_CHOICES, TAG_COLOR_VALUES
from core.themes import DEFAULT_THEME, THEME_CHOICES, THEME_VALUES
from django.contrib.auth.models import AbstractUser
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from accounts.identifiers import normalize_display_name, normalize_email, normalize_username


class User(AbstractUser):
    ROLE_ADMIN = "admin"
    ROLE_USER = "user"
    ROLE_CHOICES = (
        (ROLE_ADMIN, "Admin"),
        (ROLE_USER, "User"),
    )

    role = models.CharField(max_length=5, choices=ROLE_CHOICES, default=ROLE_USER)
    display_name = models.CharField(max_length=150, blank=True, default="")
    email = models.EmailField(blank=True)
    must_change_password = models.BooleanField(default=False)
    failed_login_count = models.PositiveIntegerField(default=0)
    failed_login_window_started_at = models.DateTimeField(blank=True, null=True)
    locked_until = models.DateTimeField(blank=True, null=True)
    last_successful_login_at = models.DateTimeField(blank=True, null=True)
    session_generation = models.PositiveIntegerField(default=1)
    disabled_at = models.DateTimeField(blank=True, null=True)
    deletion_scheduled_at = models.DateTimeField(blank=True, null=True)
    deletion_recovery_deadline = models.DateTimeField(blank=True, null=True)
    deletion_restore_is_active = models.BooleanField(blank=True, null=True)
    # Tracks whether initial account/credential setup has completed,
    # independently of `is_active` (which remains solely the
    # administrator enable/disable control). NULL means not yet
    # completed; non-null means completed. No stored status enum.
    setup_completed_at = models.DateTimeField(blank=True, null=True)
    timezone_name = models.CharField(max_length=64, blank=True, default="")
    # Per-user Tag-creation preferences. Defaults preserve the
    # application's established default behavior (uppercase on,
    # semantic color on, Slate default) -- an existing user's
    # Tag-creation/rename behavior is unchanged until they explicitly
    # opt out via Preferences.
    tag_uppercase_enabled = models.BooleanField(default=True)
    tag_semantic_color_enabled = models.BooleanField(default=True)
    tag_default_color = models.CharField(
        max_length=20, choices=TAG_COLOR_CHOICES, default=DEFAULT_TAG_COLOR
    )
    # The authoritative per-user theme preference. Valid values come
    # from `core/themes.py`'s `THEME_CHOICES`; adding or changing a
    # theme is a `core/themes.py` change, not a change to this field's
    # shape.
    theme = models.CharField(max_length=20, choices=THEME_CHOICES, default=DEFAULT_THEME)

    class Meta(AbstractUser.Meta):
        constraints = [
            models.CheckConstraint(
                condition=models.Q(username=Lower(Trim("username"))),
                name="accounts_user_username_canonical_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        deletion_scheduled_at__isnull=True,
                        deletion_recovery_deadline__isnull=True,
                    )
                    | models.Q(
                        deletion_scheduled_at__isnull=False,
                        deletion_recovery_deadline__isnull=False,
                    )
                ),
                name="accounts_user_deletion_timestamps_paired_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(deletion_scheduled_at__isnull=True) | models.Q(is_active=False)
                ),
                name="accounts_user_pending_deletion_implies_inactive_ck",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        deletion_scheduled_at__isnull=True,
                        deletion_restore_is_active__isnull=True,
                    )
                    | models.Q(
                        deletion_scheduled_at__isnull=False,
                        deletion_restore_is_active__isnull=False,
                    )
                ),
                name="accounts_user_deletion_restore_flag_paired_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(tag_default_color__in=TAG_COLOR_VALUES),
                name="accounts_user_tag_default_color_valid_ck",
            ),
            models.CheckConstraint(
                condition=models.Q(theme__in=THEME_VALUES),
                name="accounts_user_theme_valid_ck",
            ),
            models.CheckConstraint(
                condition=(models.Q(email="") | models.Q(email=Lower(Trim("email")))),
                name="accounts_user_email_canonical_ck",
            ),
            models.UniqueConstraint(
                fields=["email"],
                condition=~models.Q(email=""),
                name="accounts_user_email_nonblank_uq",
            ),
        ]

    @classmethod
    def normalize_username(cls, username):
        return normalize_username(username)

    @property
    def is_ridgenote_admin(self) -> bool:
        return self.role == self.ROLE_ADMIN

    @property
    def display_label(self) -> str:
        return self.display_name or self.username

    @property
    def has_completed_setup(self) -> bool:
        return self.setup_completed_at is not None

    @property
    def account_status_label(self) -> str:
        """Derived, never persisted -- deliberately
        separate from invitation-credential state (see
        `services.latest_invitation_for_user()`)."""
        if not self.is_active:
            return "Disabled"
        return "Active" if self.has_completed_setup else "Invited"

    @property
    def account_status_detail(self) -> str:
        """Secondary qualifier only -- currently non-empty exactly for the
        disabled-and-setup-incomplete combination, so that state is never
        silently collapsed into a bare "Disabled" with no onboarding
        context."""
        if not self.is_active and not self.has_completed_setup:
            return "Invitation/setup incomplete"
        return ""

    def save(self, *args, **kwargs):
        self.username = normalize_username(self.username)
        self.display_name = normalize_display_name(self.display_name)
        self.email = normalize_email(self.email)
        super().save(*args, **kwargs)

    def validate_unique(self, exclude=None) -> None:
        super().validate_unique(exclude=exclude)
        if not self.username:
            return
        normalized_username = normalize_username(self.username)
        conflict_exists = (
            type(self).objects.filter(username=normalized_username).exclude(pk=self.pk).exists()
        )
        if conflict_exists:
            raise ValidationError({"username": "A user with that username already exists."})


class AuditEvent(models.Model):
    SOURCE_WEB = "web"
    SOURCE_MANAGEMENT_COMMAND = "management_command"

    EVENT_LOGIN_SUCCESS = "login_success"
    EVENT_LOGIN_FAILED = "login_failed"
    EVENT_ACCOUNT_LOCKED = "account_locked"
    EVENT_ACCOUNT_UNLOCKED = "account_unlocked"
    EVENT_LOGOUT = "logout"
    EVENT_INITIAL_ADMIN_CREATED = "initial_admin_created"
    EVENT_USER_CREATED = "user_created"
    EVENT_ROLE_CHANGED = "role_changed"
    EVENT_USER_ENABLED = "user_enabled"
    EVENT_USER_DISABLED = "user_disabled"
    EVENT_ADMIN_PASSWORD_RESET = "admin_password_reset"
    EVENT_FORCED_PASSWORD_CHANGE_COMPLETED = "forced_password_change_completed"
    EVENT_ACCOUNT_PASSWORD_CHANGE_COMPLETED = "account_password_change_completed"
    EVENT_CLI_ADMIN_PASSWORD_RECOVERED = "cli_admin_password_recovered"
    EVENT_NOTES_EMPTY_TRASH = "notes_empty_trash"
    EVENT_NOTE_PLACEHOLDER_DISCARDED = "note_placeholder_discarded"
    EVENT_NOTE_EMPTIED = "note_emptied"
    EVENT_ADMINISTRATOR_NOTE_RESTORE = "administrator_note_restore"
    EVENT_ADMINISTRATOR_FOLDER_RESTORE = "administrator_folder_restore"
    EVENT_TRASH_PURGE_COMPLETED = "trash_purge_completed"
    EVENT_ACCOUNT_DELETION_SCHEDULED = "account_deletion_scheduled"
    EVENT_ACCOUNT_DELETION_CANCELLED = "account_deletion_cancelled"
    EVENT_ACCOUNT_PERMANENTLY_PURGED = "account_permanently_purged"
    EVENT_ACCOUNT_PURGE_FAILED = "account_purge_failed"
    EVENT_ACCOUNT_PURGE_REJECTED_DEADLINE = "account_purge_rejected_deadline"
    EVENT_ACCOUNT_PURGE_REJECTED_SELF = "account_purge_rejected_self"
    EVENT_ACCOUNT_PURGE_REJECTED_ACTOR_UNAUTHORIZED = "account_purge_rejected_actor_unauthorized"
    EVENT_LIBRARY_BACKUP_DOWNLOADED = "library_backup_downloaded"
    EVENT_LIBRARY_BACKUP_RESTORED = "library_backup_restored"
    EVENT_INVITATION_ISSUED = "invitation_issued"
    EVENT_INVITATION_REVOKED = "invitation_revoked"
    EVENT_INVITATION_ACCEPTED = "invitation_accepted"
    EVENT_INVITATION_SENT = "invitation_sent"
    EVENT_EMAIL_CHANGED = "email_changed"

    event_type = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)
    actor = models.ForeignKey(
        User,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_events_as_actor",
    )
    target_user = models.ForeignKey(
        User,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="audit_events_as_target",
    )
    actor_username_snapshot = models.CharField(max_length=150, blank=True)
    target_username_snapshot = models.CharField(max_length=150, blank=True)
    source = models.CharField(max_length=40, default=SOURCE_WEB)
    ip_address = models.GenericIPAddressField(blank=True, null=True)
    user_agent = models.TextField(blank=True)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"{self.event_type} at {self.created_at}"


class UserInvitation(models.Model):
    """Secure invitation-token foundation.

    Only a SHA-256 digest of the issued token is ever persisted; the raw
    token exists solely in the immediate service-call response. `revoked_at`
    is reused for both an explicit administrator revoke and automatic
    supersession on reissue -- the two cases are distinguished at the audit
    -event layer, not by a second model field.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="invitations",
    )
    token_hash = models.CharField(max_length=64, unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    accepted_at = models.DateTimeField(blank=True, null=True)
    revoked_at = models.DateTimeField(blank=True, null=True)
    created_by = models.ForeignKey(
        User,
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name="invitations_issued",
    )

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self) -> str:
        return f"Invitation({self.pk}) for {self.user_id}"

    @property
    def is_valid(self) -> bool:
        return (
            self.accepted_at is None
            and self.revoked_at is None
            and self.expires_at > timezone.now()
        )
