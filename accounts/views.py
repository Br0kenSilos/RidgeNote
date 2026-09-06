from __future__ import annotations

from core.palette_lab import (
    PALETTE_LAB_CANDIDATES,
    PALETTE_LAB_CATEGORIES,
    PALETTE_LAB_SHELL_COMPARE_IDS,
)
from core.permissions import admin_required
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login, logout
from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_GET, require_http_methods, require_POST
from notes import services as note_services
from notes.forms import NoteTagAssignForm
from notes.models import TAG_COLOR_CHOICES

from accounts import mail, services
from accounts.forms import (
    AccountDisplayNameForm,
    AccountPasswordChangeForm,
    ForcedPasswordChangeForm,
    InvitationAcceptanceForm,
    LoginForm,
    PreferencesAppearanceForm,
    PreferencesTagsForm,
    PreferencesTimezoneForm,
    PurgeUserAccountForm,
    SetupForm,
    UserActiveForm,
    UserCreateForm,
    UserEmailForm,
    UserPasswordResetForm,
    UserRoleForm,
)
from accounts.identifiers import normalize_username
from accounts.models import AuditEvent, User, UserInvitation

PENDING_INVITATION_SESSION_KEY = "pending_invitation_id"


def safe_next_url(request: HttpRequest) -> str:
    next_url = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(
        next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        return next_url
    return reverse("home")


@require_http_methods(["GET", "POST"])
def setup(request: HttpRequest) -> HttpResponse:
    if services.admin_exists():
        return redirect("accounts:login")
    if request.method == "POST":
        form = SetupForm(request.POST)
        if form.is_valid():
            try:
                user = services.create_initial_admin(form, request=request)
            except services.BootstrapClosedError:
                messages.error(request, "Initial setup is already complete.")
                return redirect("accounts:login")
            login(request, user)
            services.record_successful_login(user, request)
            return redirect("home")
    else:
        form = SetupForm()
    return render(request, "accounts/setup.html", {"form": form})


@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if not services.admin_exists():
        return redirect("accounts:setup")
    if request.user.is_authenticated:
        return redirect("home")
    if request.method == "POST":
        form = LoginForm(request, data=request.POST)
        username = normalize_username(request.POST.get("username", ""))
        if form.is_valid():
            user = form.get_user()
            if user is None or not user.is_active or services.is_user_locked(user):
                services.record_failed_login(username, request)
                form.add_error(None, LoginForm.error_messages["invalid_login"])
            else:
                login(request, user)
                services.record_successful_login(user, request)
                if user.must_change_password:
                    return redirect("accounts:forced_password_change")
                return redirect(safe_next_url(request))
        else:
            services.record_failed_login(username, request)
    else:
        form = LoginForm(request)
    return render(request, "accounts/login.html", {"form": form, "next": safe_next_url(request)})


def _no_store_response(
    response: HttpResponse, *, referrer_policy: str = "no-referrer"
) -> HttpResponse:
    """`no-referrer` is the default and is correct for every response that
    renders no POST form (redirects, the unavailable page): it leaks
    nothing at all. Responses that DO render a POST form must instead use
    `strict-origin` -- Firefox sends `Origin: null` on a same-origin form
    POST from a page whose own
    `Referrer-Policy` is `no-referrer`, which Django's CSRF middleware
    correctly rejects (`null` can never be a trusted origin). `strict-origin`
    still never reveals the raw-token path/query string -- only the bare
    origin (scheme+host+port) is ever sent -- while leaving the browser's
    normal `Origin` header derivation for CSRF alone."""
    response["Cache-Control"] = "no-store"
    response["Referrer-Policy"] = referrer_policy
    return response


def _invitation_unavailable(request: HttpRequest) -> HttpResponse:
    return _no_store_response(render(request, "accounts/invitation_unavailable.html"))


def _clear_pending_invitation(request: HttpRequest) -> None:
    request.session.pop(PENDING_INVITATION_SESSION_KEY, None)


def _eligible_pending_invitation(request: HttpRequest) -> tuple[UserInvitation, User] | None:
    """Advisory/UX-only re-check for GET display and
    for deciding whether a POST retry should even reach the form -- never
    authoritative. `accept_user_invitation()` re-derives and re-validates
    everything itself, under lock, at POST time."""
    invitation_id = request.session.get(PENDING_INVITATION_SESSION_KEY)
    if invitation_id is None:
        return None
    invitation = UserInvitation.objects.filter(pk=invitation_id).select_related("user").first()
    if invitation is None or not invitation.is_valid:
        return None
    target = invitation.user
    if target.setup_completed_at is not None or not target.is_active:
        return None
    return invitation, target


@require_GET
def invite_entry(request: HttpRequest, raw_token: str) -> HttpResponse:
    if request.user.is_authenticated:
        response = render(
            request,
            "accounts/invitation_already_signed_in.html",
            {"display_label": request.user.display_label},
        )
        # This page renders a Sign Out POST form -- strict-origin, not
        # no-referrer, so Firefox does not derive Origin: null on submit.
        return _no_store_response(response, referrer_policy="strict-origin")

    invitation = services.invitation_for_raw_token(raw_token)
    if invitation is None:
        _clear_pending_invitation(request)
        return _invitation_unavailable(request)

    request.session[PENDING_INVITATION_SESSION_KEY] = invitation.pk
    return _no_store_response(redirect("accounts:invite_setup"))


@require_http_methods(["GET", "POST"])
def invite_setup(request: HttpRequest) -> HttpResponse:
    pending = _eligible_pending_invitation(request)
    if pending is None:
        _clear_pending_invitation(request)
        return _invitation_unavailable(request)
    invitation, target = pending

    if request.method == "POST":
        form = InvitationAcceptanceForm(request.POST, user=target)
        if form.is_valid():
            try:
                user = services.accept_user_invitation(
                    invitation_id=invitation.pk,
                    password=form.cleaned_data["password2"],
                    request=request,
                )
            except services.InvitationNoLongerEligibleError:
                _clear_pending_invitation(request)
                return _invitation_unavailable(request)
            _clear_pending_invitation(request)
            login(request, user)
            services.record_successful_login(user, request)
            return _no_store_response(redirect("home"))
    else:
        form = InvitationAcceptanceForm(user=target)

    response = render(request, "accounts/invitation_setup.html", {"form": form})
    # This page renders the Set Password POST form -- strict-origin, for
    # the same Firefox Origin-derivation reason as the interstitial above.
    return _no_store_response(response, referrer_policy="strict-origin")


@require_POST
def logout_view(request: HttpRequest) -> HttpResponse:
    actor = request.user if request.user.is_authenticated else None
    if actor:
        services.record_audit_event(
            AuditEvent.EVENT_LOGOUT,
            actor=actor,
            target_user=actor,
            request=request,
        )
    logout(request)
    return redirect("accounts:login")


@require_GET
def session_status(request: HttpRequest) -> JsonResponse:
    if not request.user.is_authenticated:
        return JsonResponse({"authenticated": False}, status=401)
    return JsonResponse(
        {
            "authenticated": True,
            "idle_timeout_seconds": settings.RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS,
            "warning_seconds": settings.RIDGENOTE_SESSION_WARNING_SECONDS,
        }
    )


@login_required
@require_http_methods(["GET", "POST"])
def forced_password_change(request: HttpRequest) -> HttpResponse:
    if not request.user.must_change_password:
        return redirect("home")
    if request.method == "POST":
        form = ForcedPasswordChangeForm(request.POST, user=request.user)
        if form.is_valid():
            services.complete_forced_password_change(
                user=request.user,
                new_password=form.cleaned_data["password2"],
                request=request,
            )
            messages.success(request, "Your password has been changed.")
            return redirect("home")
    else:
        form = ForcedPasswordChangeForm(user=request.user)
    return render(request, "accounts/forced_password_change.html", {"form": form})


@login_required
@require_http_methods(["GET", "POST"])
def account(request: HttpRequest) -> HttpResponse:
    # always operates on `request.user` -- never
    # accepts a target user ID, so there is no cross-owner path. A user
    # still under a forced password-change requirement never reaches this
    # view in practice: `SessionSecurityMiddleware` already redirects any
    # authenticated request with `must_change_password` set (this path
    # included) to `accounts:forced_password_change` before this view
    # runs, so no second, competing gate is added here.
    #
    # gained a second, independent section
    # (Display Name), so this view now uses an explicit `account_action`
    # POST discriminator -- each section's own `<form>` posts back here
    # with its own hidden `account_action` value, and only the matching
    # form is validated/saved; the other section's form is simply
    # re-rendered with its current values, untouched. Mirrors
    # `preferences`'s own `preferences_action` pattern exactly.
    if request.method == "POST" and request.POST.get("account_action") == "display_name":
        display_name_form = AccountDisplayNameForm(request.POST)
        if display_name_form.is_valid():
            request.user.display_name = display_name_form.cleaned_data["display_name"]
            request.user.save(update_fields=["display_name"])
            messages.success(request, "Your display name has been saved.")
            return redirect("accounts:account")
        for field_errors in display_name_form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        form = AccountPasswordChangeForm(user=request.user)
        email_form = UserEmailForm(initial={"email": request.user.email}, target=request.user)
    elif request.method == "POST" and request.POST.get("account_action") == "email":
        # reuses `UserEmailForm`/
        # `services.set_user_email()` completely unchanged -- neither
        # contains any admin-specific check, so `target=request.user,
        # actor=request.user` is the entire self-service boundary. Never
        # accepts a target user ID from the client, exactly like the
        # Display Name/password sections above.
        email_form = UserEmailForm(request.POST, target=request.user)
        if email_form.is_valid():
            services.set_user_email(
                target=request.user,
                email=email_form.cleaned_data["email"],
                actor=request.user,
                request=request,
            )
            messages.success(request, "Your email has been saved.")
            return redirect("accounts:account")
        display_name_form = AccountDisplayNameForm(
            initial={"display_name": request.user.display_name}
        )
        form = AccountPasswordChangeForm(user=request.user)
    elif request.method == "POST" and request.POST.get("account_action") == "password":
        form = AccountPasswordChangeForm(request.POST, user=request.user)
        if form.is_valid():
            services.complete_account_password_change(
                user=request.user,
                new_password=form.cleaned_data["password2"],
                request=request,
            )
            messages.success(request, "Your password has been changed.")
            return redirect("accounts:account")
        else:
            # Surface
            # every validation failure (incorrect current password,
            # mismatched confirmation, weak new password) through the
            # existing site-wide messages banner -- the same
            # `.messages__item--error` component already used everywhere
            # else in this app -- rather than relying solely on the
            # per-field `{{ field.errors }}` list `_form_field.html`
            # already renders. `form.errors` covers both field-specific
            # errors (keyed by field name, e.g. `current_password`) and
            # non-field errors (keyed `__all__`, e.g. mismatched
            # confirmation or a rejected new password from
            # `validate_password_for_user()`) in one pass.
            for field_errors in form.errors.values():
                for error in field_errors:
                    messages.error(request, error)
        display_name_form = AccountDisplayNameForm(
            initial={"display_name": request.user.display_name}
        )
        email_form = UserEmailForm(initial={"email": request.user.email}, target=request.user)
    else:
        display_name_form = AccountDisplayNameForm(
            initial={"display_name": request.user.display_name}
        )
        form = AccountPasswordChangeForm(user=request.user)
        email_form = UserEmailForm(initial={"email": request.user.email}, target=request.user)
    return render(
        request,
        "accounts/account.html",
        {"form": form, "display_name_form": display_name_form, "email_form": email_form},
    )


@login_required
@require_http_methods(["GET", "POST"])
def preferences(request: HttpRequest) -> HttpResponse:
    """The user-preference home,
    separate from Account (identity/security/library backup).

    Preferences gained a second, independent section (Tags),
    so this view now uses an explicit `preferences_action` POST
    discriminator -- each section's own `<form>` posts back here with
    its own hidden `preferences_action` value, and only the matching
    form is validated/saved; the other section's form is simply
    re-rendered with its current values, untouched. This is the first
    use of this pattern on this page (previously exactly one form, no
    discriminator needed); it is expected to be reused by future
    Preferences sections rather than redesigned each time.

    A third, independent section
    (Appearance) follows the exact same discriminator pattern."""
    if request.method == "POST" and request.POST.get("preferences_action") == "timezone":
        form = PreferencesTimezoneForm(request.POST)
        if form.is_valid():
            request.user.timezone_name = form.cleaned_data["timezone_name"]
            request.user.save(update_fields=["timezone_name"])
            messages.success(request, "Your timezone has been saved.")
            return redirect("accounts:preferences")
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        tags_form = PreferencesTagsForm(
            initial={
                "tag_uppercase_enabled": request.user.tag_uppercase_enabled,
                "tag_semantic_color_enabled": request.user.tag_semantic_color_enabled,
                "tag_default_color": request.user.tag_default_color,
            }
        )
        appearance_form = PreferencesAppearanceForm(initial={"theme": request.user.theme})
    elif request.method == "POST" and request.POST.get("preferences_action") == "tags":
        tags_form = PreferencesTagsForm(request.POST)
        if tags_form.is_valid():
            request.user.tag_uppercase_enabled = tags_form.cleaned_data["tag_uppercase_enabled"]
            request.user.tag_semantic_color_enabled = tags_form.cleaned_data[
                "tag_semantic_color_enabled"
            ]
            request.user.tag_default_color = tags_form.cleaned_data["tag_default_color"]
            request.user.save(
                update_fields=[
                    "tag_uppercase_enabled",
                    "tag_semantic_color_enabled",
                    "tag_default_color",
                ]
            )
            messages.success(request, "Your tag preferences have been saved.")
            return redirect("accounts:preferences")
        for field_errors in tags_form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        form = PreferencesTimezoneForm(initial={"timezone_name": request.user.timezone_name})
        appearance_form = PreferencesAppearanceForm(initial={"theme": request.user.theme})
    elif request.method == "POST" and request.POST.get("preferences_action") == "appearance":
        appearance_form = PreferencesAppearanceForm(request.POST)
        if appearance_form.is_valid():
            request.user.theme = appearance_form.cleaned_data["theme"]
            request.user.save(update_fields=["theme"])
            messages.success(request, "Your appearance preference has been saved.")
            return redirect("accounts:preferences")
        for field_errors in appearance_form.errors.values():
            for error in field_errors:
                messages.error(request, error)
        form = PreferencesTimezoneForm(initial={"timezone_name": request.user.timezone_name})
        tags_form = PreferencesTagsForm(
            initial={
                "tag_uppercase_enabled": request.user.tag_uppercase_enabled,
                "tag_semantic_color_enabled": request.user.tag_semantic_color_enabled,
                "tag_default_color": request.user.tag_default_color,
            }
        )
    else:
        form = PreferencesTimezoneForm(initial={"timezone_name": request.user.timezone_name})
        tags_form = PreferencesTagsForm(
            initial={
                "tag_uppercase_enabled": request.user.tag_uppercase_enabled,
                "tag_semantic_color_enabled": request.user.tag_semantic_color_enabled,
                "tag_default_color": request.user.tag_default_color,
            }
        )
        appearance_form = PreferencesAppearanceForm(initial={"theme": request.user.theme})

    return render(
        request,
        "accounts/preferences.html",
        {
            "timezone_form": form,
            "timezone_options": sorted(services.available_timezone_names()),
            "effective_timezone": str(services.resolve_display_timezone(request.user)),
            "tags_form": tags_form,
            "appearance_form": appearance_form,
        },
    )


@login_required
@require_POST
def quick_set_theme(request: HttpRequest) -> HttpResponse:
    """The top-bar quick theme-selector's
    server-side target. A real, no-JS-compatible authenticated POST
    endpoint -- `core/static/core/src/theme.ts`'s JS enhancement
    intercepts the same form's submit and applies the theme immediately
    via `fetch`, reconciling on failure; without JS, this view's normal
    redirect-back-to-`next` response is the entire mechanism. Reuses
    `PreferencesAppearanceForm` (identical single-field shape) rather
    than a second, duplicate form class. Deliberately separate from the
    `preferences` view above: this is reachable from every authenticated
    page's header, not just Preferences, and must redirect back to
    wherever the user actually was, not always to Preferences."""
    form = PreferencesAppearanceForm(request.POST)
    if form.is_valid():
        request.user.theme = form.cleaned_data["theme"]
        request.user.save(update_fields=["theme"])
    else:
        messages.error(request, "That theme isn't available.")
    return redirect(safe_next_url(request))


def _tag_delete_consequence(*, usage_count: int) -> str:
    if usage_count == 0:
        return "Deleting this tag removes it from your tag list."
    noun = "note" if usage_count == 1 else "notes"
    return (
        f"Deleting this tag removes it from {usage_count} {noun}. "
        "The notes themselves will not be deleted."
    )


def _usage_label(*, usage_count: int) -> str:
    noun = "note" if usage_count == 1 else "notes"
    return f"{usage_count} {noun}"


# A presentation-
# only reordering of the same 11 `(value, label)` pairs, alphabetical by
# label, for Tag manager's own New tag/Recolor `<select>`s only. Never
# touches `notes.models.TAG_COLOR_CHOICES` itself -- its declared order
# remains the stored enum's own order, unchanged, and every other
# consumer (note-detail's own Add Tag color select builds its own,
# separate `tag_color_choices` context value in `notes/views.py` and is
# completely unaffected by this). Computed once at import time since the
# 11-entry palette is static.
TAG_COLOR_CHOICES_ALPHABETICAL = tuple(sorted(TAG_COLOR_CHOICES, key=lambda pair: pair[1]))


@login_required
@require_GET
def tag_management(request: HttpRequest) -> HttpResponse:
    """The Tag Management surface -- owner-scoped
    administration of the persistent `Tag` vocabulary itself, not
    individual-note tag editing (that remains the unchanged Add Tag
    form on note detail). `sort`/`q` are the only accepted query
    parameters; `sort` is validated against a small closed set
    (`TAG_MANAGEMENT_SORTS`) rather than trusted as a raw order
    expression."""
    query = request.GET.get("q", "")
    sort = request.GET.get("sort", note_services.TAG_MANAGEMENT_DEFAULT_SORT)
    if sort not in note_services.TAG_MANAGEMENT_SORTS:
        sort = note_services.TAG_MANAGEMENT_DEFAULT_SORT

    color_labels = dict(TAG_COLOR_CHOICES)
    tags = note_services.list_tags_for_owner_with_usage(owner=request.user, query=query, sort=sort)
    for tag in tags:
        tag.delete_consequence = _tag_delete_consequence(usage_count=tag.usage_count)
        tag.usage_label = _usage_label(usage_count=tag.usage_count)
        tag.color_label = color_labels.get(tag.color, tag.color)
        tag.note_ids_csv = ",".join(str(note.pk) for note in tag.notes.all())

    return render(
        request,
        "accounts/tag_management.html",
        {
            "tags": tags,
            "query": query,
            "sort": sort,
            "tag_color_choices": TAG_COLOR_CHOICES_ALPHABETICAL,
            "current_path": request.get_full_path(),
            # the New tag editor's initial preview
            # pill reflects the owner's actual configured default color,
            # not a hardcoded "Slate" -- computed here so the template
            # never needs its own color-label lookup logic.
            "default_color_label": color_labels.get(
                request.user.tag_default_color, request.user.tag_default_color
            ),
        },
    )


@login_required
@require_POST
def tag_create(request: HttpRequest) -> HttpResponse:
    """Creates a
    persistent, zero-use Tag directly from Tag manager -- the vocabulary
    object only, never attached to any Note. Reuses `NoteTagAssignForm`
    verbatim -- the exact same name/color validation note-detail's own
    Add Tag form already applies -- rather than duplicating those rules
    here. Owner comes exclusively from `request.user`; no owner ID is
    ever accepted from form data."""
    form = NoteTagAssignForm(request.POST, user=request.user)
    if form.is_valid():
        try:
            note_services.create_tag_for_owner(
                owner=request.user,
                name=form.cleaned_data["name"],
                color=form.cleaned_data["color"],
            )
        except note_services.TagNameConflictError as exc:
            messages.error(request, str(exc))
        else:
            messages.success(request, "Tag created.")
    else:
        for field_errors in form.errors.values():
            for error in field_errors:
                messages.error(request, error)
    return redirect(safe_next_url(request))


@login_required
@require_POST
def tag_rename(request: HttpRequest, tag_id: int) -> HttpResponse:
    tag = note_services.tag_for_owner_or_404(tag_id=tag_id, owner=request.user)
    new_name = request.POST.get("name", "")
    try:
        note_services.rename_tag(tag=tag, new_name=new_name)
    except note_services.TagNameValidationError as exc:
        messages.error(request, str(exc))
    except note_services.TagNameConflictError as exc:
        messages.error(request, str(exc))
    else:
        messages.success(request, "Tag renamed.")
    return redirect(safe_next_url(request))


@login_required
@require_POST
def tag_recolor(request: HttpRequest, tag_id: int) -> HttpResponse:
    tag = note_services.tag_for_owner_or_404(tag_id=tag_id, owner=request.user)
    color = request.POST.get("color", "")
    try:
        note_services.recolor_tag(tag=tag, color=color)
    except ValueError:
        messages.error(request, "Choose a valid tag color.")
    else:
        messages.success(request, "Tag recolored.")
    return redirect(safe_next_url(request))


@login_required
@require_POST
def tag_delete(request: HttpRequest, tag_id: int) -> HttpResponse:
    tag = note_services.tag_for_owner_or_404(tag_id=tag_id, owner=request.user)
    note_services.delete_tag(tag=tag)
    messages.success(request, "Tag deleted.")
    return redirect(safe_next_url(request))


@login_required
@require_POST
def tag_bulk_delete(request: HttpRequest) -> HttpResponse:
    raw_ids = request.POST.getlist("tag_ids")
    tag_ids: list[int] = []
    for raw_id in raw_ids:
        try:
            tag_ids.append(int(raw_id))
        except ValueError:
            continue
    deleted_count = note_services.bulk_delete_tags(owner=request.user, tag_ids=tag_ids)
    if deleted_count:
        noun = "tag" if deleted_count == 1 else "tags"
        messages.success(request, f"Deleted {deleted_count} {noun}.")
    else:
        messages.error(request, "No tags were selected.")
    return redirect(safe_next_url(request))


@require_GET
def user_list(request: HttpRequest) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    users = list(User.objects.order_by("username"))
    for user in users:
        user.deletion_status = services.user_deletion_status(target=user)
        user.is_locked = services.is_user_locked(user)
    return render(request, "accounts/user_list.html", {"users": users})


@require_GET
def theme_calibration(request: HttpRequest) -> HttpResponse:
    """An internal, admin-only reference page
    for reviewing production theme/color tokens (all 11 tag colors, button/
    link/status/form primitives, and non-functional future shell-candidate
    swatches) side by side across warm-light and dark. Not an end-user
    feature -- not linked from any primary navigation. `tag_color_choices`
    is passed through unmodified so the fixture always reflects the real
    palette/order, never a hand-copied duplicate.

    This page also includes the Palette Lab section:
    `palette_lab_candidates`/`palette_lab_categories` are
    passed through unmodified from `core.palette_lab`, the single
    source of truth for the internal design-candidate registry --
    these candidates are never production themes (see that module's
    docstring).

    `palette_lab_shell_compare_candidates` holds
    (id, display_name) pairs for
    the three selected Dim themes with the approved light-shell
    direction (`PALETTE_LAB_SHELL_COMPARE_IDS`), derived from the same
    registry rather than duplicating names by hand. The displayed
    name for each candidate is its approved `approved_name` (e.g. "Blue
    Dusk") where one exists, falling back to the internal `label`
    for any id that has none."""
    denied = admin_required(request)
    if denied:
        return denied
    candidate_display_names = {
        candidate["id"]: candidate["approved_name"] or candidate["label"]
        for candidate in PALETTE_LAB_CANDIDATES
    }
    shell_compare_candidates = [
        (candidate_id, candidate_display_names[candidate_id])
        for candidate_id in PALETTE_LAB_SHELL_COMPARE_IDS
    ]
    return render(
        request,
        "accounts/theme_calibration.html",
        {
            "tag_color_choices": TAG_COLOR_CHOICES,
            "palette_lab_candidates": PALETTE_LAB_CANDIDATES,
            "palette_lab_categories": PALETTE_LAB_CATEGORIES,
            "palette_lab_shell_compare_candidates": shell_compare_candidates,
        },
    )


def _invitation_entry_url(request: HttpRequest, raw_token: str) -> str:
    return request.build_absolute_uri(
        reverse("accounts:invite_entry", kwargs={"raw_token": raw_token})
    )


def _user_detail_context(
    request: HttpRequest,
    target: User,
    *,
    new_invitation_url: str | None = None,
    email_form: UserEmailForm | None = None,
) -> dict:
    latest_invitation = None
    if not target.has_completed_setup:
        latest_invitation = services.latest_invitation_for_user(target)
    context = {
        "target": target,
        "deletion_status": services.user_deletion_status(target=target),
        "latest_invitation": latest_invitation,
        "email_form": email_form or UserEmailForm(initial={"email": target.email}, target=target),
    }
    if new_invitation_url is not None:
        context["new_invitation_url"] = new_invitation_url
    return context


def _maybe_send_invitation_email(
    request: HttpRequest,
    *,
    wants_send: bool,
    target: User,
    invitation: UserInvitation,
    raw_token: str,
) -> None:
    """Called only *after* the triggering
    invitation mutation's own transaction has already committed --
    a send attempt here can never roll back the User/invitation it
    is reporting on. Re-derives
    real eligibility itself (SMTP configured, target has an email) rather
    than trusting the caller or the submitted checkbox alone; a no-op
    here is always silent, never an error, since automatic email is
    optional by design. The one-time manual link is always rendered by
    the caller regardless of what happens in this function. `raw_token`
    is passed explicitly, never read off `invitation` -- the raw token is
    never persisted on that model at all."""
    if not wants_send or not mail.smtp_configured() or not target.email:
        return
    result = mail.send_invitation_email(
        user=target, raw_token=raw_token, expires_at=invitation.expires_at
    )
    if result.sent:
        services.record_invitation_sent(
            invitation=invitation,
            target=target,
            actor=request.user,
            recipient_email=target.email,
            request=request,
        )
        messages.success(request, f"Invitation email sent to {target.email}.")
    else:
        messages.warning(request, mail.MAIL_FAILURE_MESSAGE)


@require_http_methods(["GET", "POST"])
def user_create(request: HttpRequest) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    if request.method == "POST":
        form = UserCreateForm(request.POST)
        if form.is_valid():
            if form.is_invitation_mode():
                result = services.create_invited_user(
                    form=form, actor=request.user, request=request
                )
                new_invitation_url = _invitation_entry_url(request, result.raw_token)
                _maybe_send_invitation_email(
                    request,
                    wants_send=form.cleaned_data.get("send_invitation", False),
                    target=result.user,
                    invitation=result.invitation,
                    raw_token=result.raw_token,
                )
                return render(
                    request,
                    "accounts/user_detail.html",
                    _user_detail_context(
                        request, result.user, new_invitation_url=new_invitation_url
                    ),
                )
            user = services.create_user(form=form, actor=request.user, request=request)
            messages.success(request, f"Created user {user.display_label}.")
            return redirect("accounts:user_list")
    else:
        form = UserCreateForm(initial={"is_active": True, "role": User.ROLE_USER})
    return render(
        request,
        "accounts/user_form.html",
        {"form": form, "smtp_configured": mail.smtp_configured()},
    )


@require_GET
def user_detail(request: HttpRequest, user_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    target = get_object_or_404(User, pk=user_id)
    return render(request, "accounts/user_detail.html", _user_detail_context(request, target))


@require_http_methods(["GET", "POST"])
def user_invitation_reissue(request: HttpRequest, user_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    target = get_object_or_404(User, pk=user_id)
    if target.has_completed_setup:
        messages.error(request, "This account has already completed setup.")
        return redirect("accounts:user_detail", user_id=user_id)
    if request.method == "POST":
        try:
            issued = services.issue_user_invitation(
                target=target, actor=request.user, request=request
            )
        except services.InvitationTargetAlreadySetUpError:
            messages.error(request, "This account has already completed setup.")
            return redirect("accounts:user_detail", user_id=user_id)
        new_invitation_url = _invitation_entry_url(request, issued.raw_token)
        _maybe_send_invitation_email(
            request,
            wants_send="send_invitation" in request.POST,
            target=target,
            invitation=issued.invitation,
            raw_token=issued.raw_token,
        )
        return render(
            request,
            "accounts/user_detail.html",
            _user_detail_context(request, target, new_invitation_url=new_invitation_url),
        )
    latest_invitation = services.latest_invitation_for_user(target)
    return render(
        request,
        "accounts/user_invitation_reissue_confirm.html",
        {
            "target": target,
            "latest_invitation": latest_invitation,
            "smtp_configured": mail.smtp_configured(),
        },
    )


@require_http_methods(["GET", "POST"])
def user_invitation_revoke(request: HttpRequest, user_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    target = get_object_or_404(User, pk=user_id)
    latest_invitation = services.latest_invitation_for_user(target)
    if target.has_completed_setup or latest_invitation is None or not latest_invitation.is_valid:
        messages.error(request, "There is no live invitation to revoke for this account.")
        return redirect("accounts:user_detail", user_id=user_id)
    if request.method == "POST":
        services.revoke_user_invitation(
            invitation=latest_invitation, actor=request.user, request=request
        )
        messages.success(request, "Invitation revoked.")
        return redirect("accounts:user_detail", user_id=user_id)
    return render(
        request,
        "accounts/user_invitation_revoke_confirm.html",
        {"target": target, "latest_invitation": latest_invitation},
    )


@require_POST
def user_role(request: HttpRequest, user_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    target = get_object_or_404(User, pk=user_id)
    form = UserRoleForm(request.POST)
    if form.is_valid():
        try:
            services.set_user_role(
                target=target,
                role=form.cleaned_data["role"],
                actor=request.user,
                request=request,
            )
            messages.success(request, "User role updated.")
        except services.LastActiveAdminError as exc:
            messages.error(request, str(exc))
    return redirect("accounts:user_detail", user_id=user_id)


@require_POST
def user_active(request: HttpRequest, user_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    target = get_object_or_404(User, pk=user_id)
    form = UserActiveForm(request.POST)
    if form.is_valid():
        try:
            services.set_user_active(
                target=target,
                active=form.cleaned_data["is_active"],
                actor=request.user,
                request=request,
            )
            messages.success(request, "User status updated.")
        except services.LastActiveAdminError as exc:
            messages.error(request, str(exc))
    return redirect("accounts:user_detail", user_id=user_id)


@require_POST
def user_email(request: HttpRequest, user_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    target = get_object_or_404(User, pk=user_id)
    form = UserEmailForm(request.POST, target=target)
    if form.is_valid():
        services.set_user_email(
            target=target,
            email=form.cleaned_data["email"],
            actor=request.user,
            request=request,
        )
        messages.success(request, "Email updated.")
        return redirect("accounts:user_detail", user_id=user_id)
    return render(
        request,
        "accounts/user_detail.html",
        _user_detail_context(request, target, email_form=form),
    )


@require_http_methods(["GET", "POST"])
def user_schedule_deletion(request: HttpRequest, user_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    target = get_object_or_404(User, pk=user_id)
    if request.method == "POST":
        try:
            services.schedule_user_deletion(target=target, actor=request.user, request=request)
            messages.success(
                request,
                "Deletion scheduled. The account is now disabled and recoverable for seven days.",
            )
        except (services.AlreadyPendingDeletionError, services.LastActiveAdminError) as exc:
            messages.error(request, str(exc))
        return redirect("accounts:user_detail", user_id=user_id)
    ownership_counts = services.count_owned_content(target=target)
    return render(
        request,
        "accounts/user_schedule_deletion_confirm.html",
        {"target": target, "ownership_counts": ownership_counts},
    )


@require_POST
def user_cancel_deletion(request: HttpRequest, user_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    target = get_object_or_404(User, pk=user_id)
    try:
        cancelled = services.cancel_user_deletion(
            target=target, actor=request.user, request=request
        )
        if cancelled.is_active:
            messages.success(request, "Deletion cancelled. The account has been reactivated.")
        else:
            messages.success(request, "Deletion cancelled. The account remains disabled.")
    except services.NotPendingDeletionError as exc:
        messages.error(request, str(exc))
    return redirect("accounts:user_detail", user_id=user_id)


@require_http_methods(["GET", "POST"])
def user_purge(request: HttpRequest, user_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    target = get_object_or_404(User, pk=user_id)
    is_self = request.user.pk == target.pk
    deletion_status = services.user_deletion_status(target=target)

    if request.method == "POST":
        form = PurgeUserAccountForm(request.POST, target=target)
        if form.is_valid():
            try:
                services.purge_user_account(target=target, actor=request.user, request=request)
            except (
                services.PurgeDeadlineNotElapsedError,
                services.SelfPurgeNotAllowedError,
                services.PurgeActorNotAuthorizedError,
                services.NotPendingDeletionError,
            ) as exc:
                messages.error(request, str(exc))
                return redirect("accounts:user_detail", user_id=user_id)
            except User.DoesNotExist as exc:
                raise Http404() from exc
            messages.success(request, "Account permanently purged.")
            return redirect("accounts:user_list")
        ownership_counts = services.count_owned_content(target=target)
        return render(
            request,
            "accounts/user_purge_confirm.html",
            {
                "target": target,
                "form": form,
                "ownership_counts": ownership_counts,
                "deletion_status": deletion_status,
                "is_self": is_self,
            },
        )

    if not deletion_status.is_purge_eligible:
        messages.error(request, "This account is not yet eligible for permanent purge.")
        return redirect("accounts:user_detail", user_id=user_id)

    ownership_counts = services.count_owned_content(target=target)
    form = None if is_self else PurgeUserAccountForm(target=target)
    return render(
        request,
        "accounts/user_purge_confirm.html",
        {
            "target": target,
            "form": form,
            "ownership_counts": ownership_counts,
            "deletion_status": deletion_status,
            "is_self": is_self,
        },
    )


@require_http_methods(["GET", "POST"])
def user_password_reset(request: HttpRequest, user_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    target = get_object_or_404(User, pk=user_id)
    if not target.has_completed_setup:
        messages.error(
            request,
            "This account has not completed setup and cannot have its password "
            "reset by an administrator. Manage its invitation instead.",
        )
        return redirect("accounts:user_detail", user_id=user_id)
    if request.method == "POST":
        form = UserPasswordResetForm(request.POST, user=target)
        if form.is_valid():
            try:
                services.reset_user_password(
                    target=target,
                    new_password=form.cleaned_data["password2"],
                    must_change_password=form.cleaned_data["require_password_change"],
                    actor=request.user,
                    request=request,
                )
            except services.TargetSetupIncompleteError as exc:
                messages.error(request, str(exc))
                return redirect("accounts:user_detail", user_id=user_id)
            messages.success(request, "Password reset.")
            return redirect("accounts:user_detail", user_id=user_id)
    else:
        form = UserPasswordResetForm(user=target)
    return render(request, "accounts/password_reset.html", {"form": form, "target": target})
