from core.tag_colors import TAG_COLOR_CHOICES
from core.themes import THEME_CHOICES
from django import forms
from django.contrib.auth import authenticate, get_user_model
from django.contrib.auth.forms import UsernameField
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError

from accounts.identifiers import normalize_display_name, normalize_email, normalize_username
from accounts.models import User
from accounts.services import is_valid_timezone_name


def username_conflicts(username: str, *, exclude_pk: int | None = None) -> bool:
    normalized_username = normalize_username(username)
    queryset = get_user_model().objects.filter(username=normalized_username)
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.exists()


def email_conflicts(email: str, *, exclude_pk: int | None = None) -> bool:
    normalized_email = normalize_email(email)
    if not normalized_email:
        return False
    queryset = get_user_model().objects.filter(email=normalized_email)
    if exclude_pk is not None:
        queryset = queryset.exclude(pk=exclude_pk)
    return queryset.exists()


class PasswordConfirmationMixin:
    def clean_password2(self) -> str:
        password1 = self.cleaned_data.get("password1")
        password2 = self.cleaned_data.get("password2")
        if password1 and password2 and password1 != password2:
            raise ValidationError("The two password fields did not match.")
        return password2

    def validate_password_for_user(self, user: User | None = None) -> None:
        password = self.cleaned_data.get("password2")
        if password:
            validate_password(password, user=user)


class SetupForm(PasswordConfirmationMixin, forms.ModelForm):
    password1 = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", strip=False, widget=forms.PasswordInput)

    class Meta:
        model = User
        fields = ["username", "display_name", "email"]
        field_classes = {"username": UsernameField}

    def clean_username(self) -> str:
        username = normalize_username(self.cleaned_data["username"])
        if username_conflicts(username):
            raise ValidationError("A user with that username already exists.")
        return username

    def clean_display_name(self) -> str:
        return normalize_display_name(self.cleaned_data.get("display_name", ""))

    def clean_email(self) -> str:
        email = normalize_email(self.cleaned_data.get("email", ""))
        if email and email_conflicts(email):
            raise ValidationError("A user with that email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        user = User(
            username=cleaned.get("username", ""),
            display_name=cleaned.get("display_name", ""),
        )
        self.validate_password_for_user(user)
        return cleaned

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        user.set_password(self.cleaned_data["password2"])
        if commit:
            user.save()
        return user


class LoginForm(forms.Form):
    username = UsernameField()
    password = forms.CharField(strip=False, widget=forms.PasswordInput)

    error_messages = {
        "invalid_login": "The username or password is incorrect, or the account cannot sign in.",
    }

    def __init__(self, request=None, *args, **kwargs):
        self.request = request
        self.user_cache = None
        super().__init__(*args, **kwargs)

    def clean_username(self) -> str:
        return normalize_username(self.cleaned_data["username"])

    def clean(self):
        cleaned = super().clean()
        username = cleaned.get("username")
        password = cleaned.get("password")
        if username and password:
            self.user_cache = authenticate(self.request, username=username, password=password)
            if self.user_cache is None:
                raise ValidationError(self.error_messages["invalid_login"], code="invalid_login")
        return cleaned

    def get_user(self):
        return self.user_cache


# Dual-mode Admin Create User. Form-state only,
# never persisted on User: an already-onboarded account's history
# (setup_completed_at / UserInvitation rows) already fully answers "how
# was this account originally onboarded" whenever that matters.
SETUP_METHOD_INVITATION = "invitation"
# The internal value is form-state only, never persisted, never exposed
# to the user -- only the human-facing label reflects current
# terminology (see the "Set password" `require_password_change`
# handling below).
SETUP_METHOD_SET_PASSWORD = "temporary_password"
SETUP_METHOD_CHOICES = (
    (SETUP_METHOD_INVITATION, "Invitation"),
    (SETUP_METHOD_SET_PASSWORD, "Set password"),
)


class UserCreateForm(PasswordConfirmationMixin, forms.ModelForm):
    setup_method = forms.ChoiceField(
        choices=SETUP_METHOD_CHOICES,
        initial=SETUP_METHOD_INVITATION,
        widget=forms.RadioSelect,
        help_text=(
            "Invitation: User chooses their own password via an invitation "
            "link. Set password: you choose the password and provide it to "
            "the user directly. You can require the user to change it at "
            "first sign-in if you wish."
        ),
    )
    password1 = forms.CharField(
        label="Password", strip=False, widget=forms.PasswordInput, required=False
    )
    password2 = forms.CharField(
        label="Confirm password", strip=False, widget=forms.PasswordInput, required=False
    )
    # Scoped to Set password only -- see `UserCreateForm.save()`'s own
    # comment for why this is not unconditional. Distinct from, and
    # unrelated to, `UserPasswordResetForm`'s own same-named field, which
    # governs resetting an already setup-complete account's password.
    require_password_change = forms.BooleanField(
        required=False,
        initial=True,
        label="Require password change on first sign-in",
    )
    # Present on the form unconditionally, exactly
    # like `require_password_change` above -- the *template* only renders
    # it visibly-and-enabled when SMTP delivery is actually configured,
    # and the *view* independently re-derives real eligibility (Invitation
    # mode, SMTP configured, nonblank email) before ever attempting to
    # send. A stale/scripted `send_invitation=on` submitted outside those
    # conditions simply has no effect -- never a validation error, same
    # "unknown/inapplicable POST keys are not a product contract"
    # convention already established for `require_password_change` under
    # Invitation mode.
    send_invitation = forms.BooleanField(
        required=False,
        initial=True,
        label="Send invitation by email",
    )

    class Meta:
        model = User
        fields = ["username", "display_name", "email", "role", "is_active"]
        field_classes = {"username": UsernameField}

    def clean_username(self) -> str:
        username = normalize_username(self.cleaned_data["username"])
        if username_conflicts(username):
            raise ValidationError("A user with that username already exists.")
        return username

    def clean_display_name(self) -> str:
        return normalize_display_name(self.cleaned_data.get("display_name", ""))

    def clean_email(self) -> str:
        email = normalize_email(self.cleaned_data.get("email", ""))
        if email and email_conflicts(email):
            raise ValidationError("A user with that email already exists.")
        return email

    def clean(self):
        cleaned = super().clean()
        setup_method = cleaned.get("setup_method")
        password1 = cleaned.get("password1")
        password2 = cleaned.get("password2")
        if setup_method == SETUP_METHOD_SET_PASSWORD:
            if not password1 or not password2:
                raise ValidationError(
                    "A password and confirmation are required for the Set password setup method."
                )
            user = User(
                username=cleaned.get("username", ""),
                display_name=cleaned.get("display_name", ""),
                email=cleaned.get("email", ""),
            )
            self.validate_password_for_user(user)
        elif setup_method == SETUP_METHOD_INVITATION and (password1 or password2):
            # Rejected rather than silently ignored -- an administrator must
            # never be led to believe a password they entered was actually
            # set on an invitation-mode account.
            raise ValidationError(
                "Password fields are not used for invitation-based setup. "
                "Leave them blank, or choose Set password instead."
            )
        # `require_password_change` is never validated/rejected for
        # Invitation mode -- a stale/scripted submission of that field is
        # simply unused (see `save()`), matching the existing "unknown
        # POST keys are not a product contract" convention rather than
        # adding a second, redundant rejection path.
        return cleaned

    def is_invitation_mode(self) -> bool:
        return self.cleaned_data.get("setup_method") == SETUP_METHOD_INVITATION

    def save(self, commit: bool = True) -> User:
        user = super().save(commit=False)
        if self.cleaned_data.get("setup_method") == SETUP_METHOD_SET_PASSWORD:
            user.set_password(self.cleaned_data["password2"])
            # Making this unconditional ("a temporary password is temporary
            # by definition") would create
            # a semantic contradiction -- if first-login change is meant
            # to be administrator-discretionary, the password isn't
            # inherently "temporary" at all. Set password is an
            # ordinary administrator-chosen password whose forced-change
            # behavior is this checkbox's explicit choice (default
            # checked). Deliberately different from
            # UserPasswordResetForm's own, separate, similarly-discretionary
            # require_password_change control, which governs resetting an
            # already setup-complete account's password, not initial
            # onboarding.
            user.must_change_password = self.cleaned_data.get("require_password_change", True)
        user.is_staff = False
        user.is_superuser = False
        if commit:
            user.save()
        return user


class UserRoleForm(forms.Form):
    role = forms.ChoiceField(choices=User.ROLE_CHOICES)


class UserActiveForm(forms.Form):
    is_active = forms.BooleanField(required=False)


class UserEmailForm(forms.Form):
    email = forms.EmailField(required=False)

    def __init__(self, *args, target: User | None = None, **kwargs):
        self.target = target
        super().__init__(*args, **kwargs)

    def clean_email(self) -> str:
        email = normalize_email(self.cleaned_data.get("email", ""))
        exclude_pk = self.target.pk if self.target is not None else None
        if email and email_conflicts(email, exclude_pk=exclude_pk):
            raise ValidationError("A user with that email already exists.")
        return email


class UserPasswordResetForm(PasswordConfirmationMixin, forms.Form):
    password1 = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", strip=False, widget=forms.PasswordInput)
    require_password_change = forms.BooleanField(required=False, initial=True)

    def __init__(self, *args, user: User | None = None, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        self.validate_password_for_user(self.user)
        return cleaned


class PurgeUserAccountForm(forms.Form):
    confirm_username = forms.CharField(
        label="Type the username to confirm",
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "spellcheck": "false",
                "autocapitalize": "off",
            }
        ),
    )

    def __init__(self, *args, target: User, **kwargs):
        self.target = target
        super().__init__(*args, **kwargs)

    def clean_confirm_username(self) -> str:
        entered = normalize_username(self.cleaned_data["confirm_username"])
        expected = normalize_username(self.target.username)
        if entered != expected:
            raise ValidationError(
                "The typed username did not match. Type the account's exact username to confirm."
            )
        return entered


class ForcedPasswordChangeForm(PasswordConfirmationMixin, forms.Form):
    password1 = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", strip=False, widget=forms.PasswordInput)

    def __init__(self, *args, user: User, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        self.validate_password_for_user(self.user)
        return cleaned


class InvitationAcceptanceForm(PasswordConfirmationMixin, forms.Form):
    """Mirrors `ForcedPasswordChangeForm` exactly --
    password-only, no username/email/invitation fields. The invitation
    itself is session-backed (`pending_invitation_id`), never submitted
    by the client."""

    password1 = forms.CharField(label="Password", strip=False, widget=forms.PasswordInput)
    password2 = forms.CharField(label="Confirm password", strip=False, widget=forms.PasswordInput)

    def __init__(self, *args, user: User, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean(self):
        cleaned = super().clean()
        self.validate_password_for_user(self.user)
        return cleaned


class AccountPasswordChangeForm(PasswordConfirmationMixin, forms.Form):
    """The Account page's self-service password
    change -- the one addition `ForcedPasswordChangeForm` deliberately
    doesn't need, since that flow only ever runs for a user already mid
    lockout-recovery, never proving anything about a password they already
    know. Reuses `PasswordConfirmationMixin` (confirmation matching, new-
    password validation) unchanged."""

    current_password = forms.CharField(
        label="Current password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )
    password1 = forms.CharField(
        label="New password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )
    password2 = forms.CharField(
        label="Confirm new password",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "new-password"}),
    )

    def __init__(self, *args, user: User, **kwargs):
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_current_password(self) -> str:
        current_password = self.cleaned_data.get("current_password", "")
        if not self.user.check_password(current_password):
            raise ValidationError("Your current password was incorrect.")
        return current_password

    def clean(self):
        cleaned = super().clean()
        self.validate_password_for_user(self.user)
        return cleaned


class AccountDisplayNameForm(forms.Form):
    """Cosmetic self-service Display Name edit on
    the Account page, scoped to `request.user` only. Reuses the model's
    own `normalize_display_name()` on `clean()` -- the same
    belt-and-suspenders convention `User.save()` already applies, and the
    same helper `SetupForm`/`UserCreateForm` already use. Blank is valid;
    the existing `display_label` property (`display_name or username`)
    remains the sole fallback, not reimplemented here.

    `max_length=40` here is a self-service-form-only product/UI limit,
    deliberately smaller than the model's own `max_length=150`: 40
    characters is sufficient for a human-readable display name and keeps
    compact account/navigation UI sane, while the model stays at 150 for
    backward/data compatibility (an existing stored value longer than 40
    is never truncated or rejected on read -- only a *new* self-service
    submission over 40 characters fails validation)."""

    display_name = forms.CharField(
        required=False,
        max_length=40,
        label="Display name",
        help_text="40 characters maximum.",
    )

    def clean_display_name(self) -> str:
        return normalize_display_name(self.cleaned_data.get("display_name", ""))


class PreferencesTimezoneForm(forms.Form):
    """The user's self-service timezone preference, part of the
    Preferences page. An
    empty string is explicitly valid and means "unset" -- RidgeNote then
    falls back to `settings.TIME_ZONE` via `resolve_display_timezone()`.
    A non-empty value must be a real, currently-recognized IANA
    identifier (aliases such as `US/Eastern` are accepted as-is, never
    rewritten to a canonical name)."""

    timezone_name = forms.CharField(
        required=False,
        max_length=64,
        label="Timezone",
        widget=forms.TextInput(
            attrs={
                "list": "timezone-options",
                "autocomplete": "off",
                "spellcheck": "false",
                "data-preferences-timezone-input": "true",
                "placeholder": "e.g. America/New_York",
            }
        ),
    )

    def clean_timezone_name(self) -> str:
        value = self.cleaned_data.get("timezone_name", "").strip()
        if not value:
            return ""
        if not is_valid_timezone_name(value):
            raise ValidationError(
                "That is not a recognized timezone. "
                "Choose a value such as America/New_York or Europe/London."
            )
        return value


class PreferencesTagsForm(forms.Form):
    """The three per-user Tag-creation preferences.
    A plain, independent `forms.Form` -- not tied to `User` via
    `ModelForm` -- matching `PreferencesTimezoneForm`'s own established
    shape on this page. `tag_default_color`'s `ChoiceField` already
    rejects any value outside `TAG_COLOR_CHOICES` at the form layer;
    `accounts_user_tag_default_color_valid_ck` on `User` is the
    matching DB-level guarantee, so an invalid value cannot persist
    even via a path that bypasses this form entirely."""

    tag_uppercase_enabled = forms.BooleanField(required=False, label="Uppercase tag names")
    tag_semantic_color_enabled = forms.BooleanField(required=False, label="Use semantic tag colors")
    tag_default_color = forms.ChoiceField(choices=TAG_COLOR_CHOICES, label="Default tag color")


class PreferencesAppearanceForm(forms.Form):
    """The per-user theme preference,
    matching `PreferencesTagsForm`'s/`PreferencesTimezoneForm`'s own
    established shape on this page. `theme`'s `ChoiceField` already
    rejects any value outside `THEME_CHOICES` at the form layer;
    `accounts_user_theme_valid_ck` on `User` is the matching DB-level
    guarantee, so an invalid value cannot persist even via a path that
    bypasses this form entirely -- the same pattern already used for
    `tag_default_color` above."""

    theme = forms.ChoiceField(choices=THEME_CHOICES, label="Theme")
