from __future__ import annotations

import json
import tempfile
from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlencode

from accounts import services as account_services
from accounts.models import AuditEvent
from core.permissions import admin_required
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.views import redirect_to_login
from django.core.paginator import Paginator
from django.db.models.functions import Lower
from django.http import FileResponse, HttpRequest, HttpResponse, JsonResponse
from django.http.multipartparser import MultiPartParserError, TooManyFilesSent
from django.shortcuts import redirect, render
from django.template.defaultfilters import slugify
from django.urls import reverse
from django.views.decorators.http import require_GET, require_http_methods, require_POST

from notes import documents, library_backup_archive, library_backup_upload, services
from notes import library_backup_restore as library_backup_restore_service
from notes.forms import (
    FolderCreateForm,
    FolderRenameForm,
    LibraryBackupRestoreForm,
    LibraryBackupUploadForm,
    NoteMoveForm,
    NotePinForm,
    NoteRenameForm,
    NoteTagAssignForm,
    NoteTagRemoveForm,
    NoteUpdateForm,
)
from notes.markdown import MarkdownConversionError, note_to_markdown
from notes.models import TAG_COLOR_CHOICES, Folder, Note, Tag

CONFLICT_MESSAGE = (
    "This note changed in another tab or session. Reload the latest version before saving again."
)

RESTORE_NOT_AVAILABLE_MESSAGE = (
    "This item is no longer available for self-service restore. "
    "Contact your RidgeNote administrator for help."
)

PERMANENT_DELETE_NOT_AVAILABLE_MESSAGE = (
    "This note is no longer available for self-service permanent delete. "
    "Contact your RidgeNote administrator for help."
)

NOTE_CONTENT_NOT_AVAILABLE_MESSAGE = (
    "This note's content is not available while it is in Trash. "
    "Contact your RidgeNote administrator for help."
)

TRASHED_ITEM_MESSAGE = "This item is in Trash and can no longer be modified."

# Home's own view never calls these (see `_home_context` below); the All
# Notes view is the actual, real caller, so the names describe what
# calls them. The session key's own dedicated name (`"notes_all_sort"`)
# means All Notes' sort state can never be silently read or overwritten by
# Home, which calls none of this.
ALL_NOTES_SORT_SESSION_KEY = "notes_all_sort"
ALL_NOTES_SORT_DEFAULT = "pinned_new"

ALL_NOTES_SORT_CHOICES = (
    (ALL_NOTES_SORT_DEFAULT, "Pinned first — newest modified"),
    ("pinned_old", "Pinned first — oldest modified"),
    ("title_asc", "Title — A to Z"),
    ("title_desc", "Title — Z to A"),
    ("created_new", "Created — newest first"),
    ("created_old", "Created — oldest first"),
    ("modified_new", "Modified — newest first"),
    ("modified_old", "Modified — oldest first"),
)

_ALL_NOTES_SORT_VALUES = frozenset(value for value, _label in ALL_NOTES_SORT_CHOICES)

_ALL_NOTES_SORT_ORDER_BY = {
    "pinned_new": ("-pinned", "-modified_at", "-id"),
    "pinned_old": ("-pinned", "modified_at", "id"),
    "title_asc": (Lower("title").asc(), "id"),
    "title_desc": (Lower("title").desc(), "-id"),
    "created_new": ("-created_at", "-id"),
    "created_old": ("created_at", "id"),
    "modified_new": ("-modified_at", "-id"),
    "modified_old": ("modified_at", "id"),
}


def _resolve_all_notes_sort(request: HttpRequest) -> str:
    requested_sort = request.GET.get("sort")
    if requested_sort is not None:
        if requested_sort in _ALL_NOTES_SORT_VALUES:
            request.session[ALL_NOTES_SORT_SESSION_KEY] = requested_sort
            return requested_sort
        return ALL_NOTES_SORT_DEFAULT

    saved_sort = request.session.get(ALL_NOTES_SORT_SESSION_KEY)
    if saved_sort in _ALL_NOTES_SORT_VALUES:
        return saved_sort
    return ALL_NOTES_SORT_DEFAULT


def _coerce_all_notes_page(value) -> int:
    try:
        page = int(value)
    except (TypeError, ValueError):
        return 1
    return page if page >= 1 else 1


def _coerce_all_notes_sort(value) -> str:
    return value if value in _ALL_NOTES_SORT_VALUES else ALL_NOTES_SORT_DEFAULT


def _all_notes_redirect(*, page, sort: str) -> HttpResponse:
    # The one shared destination every All-Notes-aware
    # action redirects through -- always the fixed `notes:all_notes` route
    # name, with only these two independently-validated scalar values
    # varying. Never an arbitrary caller-supplied URL, so there is no
    # open-redirect surface here regardless of what a POST body contains.
    query = urlencode({"page": _coerce_all_notes_page(page), "sort": _coerce_all_notes_sort(sort)})
    return redirect(f"{reverse('notes:all_notes')}?{query}")


HOME_DASHBOARD_RECENT_LIMIT = 10


def _home_context(request: HttpRequest) -> dict:
    # Home no longer renders a sorted full note
    # list, so `_resolve_all_notes_sort` is deliberately *not* called
    # here -- calling it would mutate the All Notes session key and
    # parse a `?sort=` query parameter with no visible effect on
    # anything Home renders. The `all_notes` view is
    # the real caller of `ALL_NOTES_SORT_CHOICES`/
    # `_ALL_NOTES_SORT_ORDER_BY`/`_resolve_all_notes_sort` -- Home
    # continues to read/write none of it, and the two pages' sort state
    # can never silently couple.
    folders, unfiled_notes = services.notes_grouped_for_tree(owner=request.user)
    recent_notes = services.recent_notes_for_owner(
        owner=request.user,
        limit=HOME_DASHBOARD_RECENT_LIMIT,
        with_tags=True,
        pinned_first=True,
    )
    # Distinguishes "zero active notes" (the expanded active-empty
    # introduction) from the defensive "notes exist but none are
    # recent" fallback -- unreachable given today's data model (recent
    # notes and active notes share the same underlying filter), but
    # implemented as its own explicit signal rather than assumed away.
    has_active_notes = (
        recent_notes or Note.objects.filter(owner=request.user, trashed_at__isnull=True).exists()
    )
    # Only computed for active-empty users (a single extra `.exists()`
    # query, never on the common "has active notes" path) -- distinguishes
    # a genuinely new user ("No notes yet") from one whose only notes are
    # trashed ("No active notes", with a Trash link).
    has_trashed_notes = (
        not has_active_notes
        and Note.objects.filter(owner=request.user, trashed_at__isnull=False).exists()
    )
    return {
        "folders": folders,
        "unfiled_notes": unfiled_notes,
        "note": None,
        "nav_drawer_available": True,
        "recent_notes": recent_notes,
        "home_has_trashed_notes": has_trashed_notes,
        "home_has_active_notes": has_active_notes,
        # An explicit, server-computed
        # state hook -- not a CSS selector inferring state from rendered
        # text -- for whether the dashboard shows real Recent rows
        # ("populated", which stretches to match the tree's height) or
        # one of the three empty states ("empty", which stays content-
        # driven). Deliberately just `bool(recent_notes)`, the same
        # condition the template already branches the Recent list on.
        "home_dashboard_populated": bool(recent_notes),
    }


@require_GET
def home(request: HttpRequest) -> HttpResponse:
    if not request.user.is_authenticated:
        return redirect_to_login(request.get_full_path())

    return render(request, "notes/home.html", _home_context(request))


ALL_NOTES_PAGE_SIZE = 50


@login_required
@require_GET
def all_notes(request: HttpRequest) -> HttpResponse:
    sort = _resolve_all_notes_sort(request)
    folders, unfiled_notes = services.notes_grouped_for_tree(owner=request.user)
    notes_queryset = services.all_active_notes_for_owner(
        owner=request.user, order_by=_ALL_NOTES_SORT_ORDER_BY[sort]
    )
    paginator = Paginator(notes_queryset, ALL_NOTES_PAGE_SIZE)
    # Malformed/negative/zero page values fall back
    # to page 1 (the same tolerant-fallback shape `_resolve_all_notes_sort`
    # already uses for an invalid `?sort=`); a page beyond the last page
    # clamps to the last valid page rather than raising `EmptyPage` --
    # `Paginator.num_pages` is always >= 1 even for zero results, so this
    # clamp alone is sufficient and `page_obj` is never fetched out of
    # range. Never a 404 for a merely-stale page number.
    requested_page = _coerce_all_notes_page(request.GET.get("page"))
    page_number = min(requested_page, paginator.num_pages)
    page_obj = paginator.page(page_number)
    has_active_notes = (
        page_obj.object_list
        or Note.objects.filter(owner=request.user, trashed_at__isnull=True).exists()
    )
    has_trashed_notes = (
        not has_active_notes
        and Note.objects.filter(owner=request.user, trashed_at__isnull=False).exists()
    )
    return render(
        request,
        "notes/all_notes.html",
        {
            "folders": folders,
            "unfiled_notes": unfiled_notes,
            "note": None,
            "nav_drawer_available": True,
            "page_obj": page_obj,
            "sort": sort,
            "sort_choices": ALL_NOTES_SORT_CHOICES,
            "all_notes_has_active_notes": has_active_notes,
            "all_notes_has_trashed_notes": has_trashed_notes,
        },
    )


LIBRARY_BACKUP_SPOOL_MAX_SIZE = 8 * 1024 * 1024

LIBRARY_BACKUP_FAILURE_MESSAGES = {
    "note_limit_exceeded": "The library contains too many notes to create a backup.",
    "folder_limit_exceeded": "The library contains too many folders to create a backup.",
    "entry_limit_exceeded": "The library contains too many backup entries.",
    "manifest_size_exceeded": "The library manifest is too large to create a backup.",
    "markdown_size_exceeded": "At least one note is too large to include in the backup.",
    "uncompressed_size_exceeded": "The library is too large to create a backup.",
    "compressed_size_exceeded": "The completed backup exceeds the supported download size.",
}
LIBRARY_BACKUP_DEFAULT_FAILURE_MESSAGE = (
    "RidgeNote could not create the library backup. Please try again."
)


@login_required
@require_GET
def library_backup_download(request: HttpRequest) -> HttpResponse:
    spool = tempfile.SpooledTemporaryFile(max_size=LIBRARY_BACKUP_SPOOL_MAX_SIZE, mode="w+b")
    try:
        metadata = library_backup_archive.write_library_backup_archive(request.user, spool)
    except library_backup_archive.LibraryBackupGenerationError as exc:
        spool.close()
        messages.error(
            request,
            LIBRARY_BACKUP_FAILURE_MESSAGES.get(exc.code, LIBRARY_BACKUP_DEFAULT_FAILURE_MESSAGE),
        )
        return redirect("accounts:account")
    except Exception:
        spool.close()
        raise

    spool.seek(0)

    try:
        account_services.record_audit_event(
            AuditEvent.EVENT_LIBRARY_BACKUP_DOWNLOADED,
            actor=request.user,
            target_user=request.user,
            request=request,
        )
    except Exception:
        spool.close()
        raise

    response = FileResponse(
        spool,
        content_type="application/zip",
        as_attachment=True,
        filename=metadata.filename,
    )
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


LIBRARY_BACKUP_REQUEST_TOO_LARGE_MESSAGE = (
    "This upload is too large. RidgeNote accepts library backup ZIP files up to 200 MiB."
)
LIBRARY_BACKUP_MALFORMED_UPLOAD_MESSAGE = (
    "RidgeNote could not read that upload. Please choose the backup file again."
)
LIBRARY_BACKUP_NO_FILE_MESSAGE = "Choose a RidgeNote library backup ZIP to continue."
LIBRARY_BACKUP_EXTRA_FILE_MESSAGE = "Select exactly one RidgeNote library backup ZIP."

LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE = "This file is not a valid RidgeNote library backup."
LIBRARY_BACKUP_UPLOAD_FAILURE_MESSAGES = {
    "empty_upload": LIBRARY_BACKUP_NO_FILE_MESSAGE,
    "upload_too_large": LIBRARY_BACKUP_REQUEST_TOO_LARGE_MESSAGE,
    "invalid_zip": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "too_many_entries": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "duplicate_member": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "encrypted_member": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "unsupported_compression": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "directory_member": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "unsafe_member_path": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "special_member": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "extra_member": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "missing_manifest": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "missing_readme": LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE,
    "declared_size_limit_exceeded": "The backup exceeds RidgeNote's supported archive limits.",
    "actual_size_limit_exceeded": "The backup exceeds RidgeNote's supported archive limits.",
    "manifest_too_large": "The backup exceeds RidgeNote's supported archive limits.",
    "readme_too_large": "The backup exceeds RidgeNote's supported archive limits.",
    "markdown_too_large": "The backup exceeds RidgeNote's supported archive limits.",
    "invalid_utf8": "The backup manifest is invalid.",
    "manifest_bom": "The backup manifest is invalid.",
    "invalid_json": "The backup manifest is invalid.",
    "duplicate_json_key": "The backup manifest is invalid.",
    "manifest_validation_failed": "The backup manifest is invalid.",
    "missing_markdown_member": "The backup archive is damaged or incomplete.",
    "orphan_markdown_member": "The backup archive is damaged or incomplete.",
    "decompression_failed": "The backup archive is damaged or incomplete.",
}


def _current_library_counts(owner) -> dict[str, int]:
    # Mirrors the exact lifecycle/pinned/Unfiled definitions
    # notes.library_backup uses for the backup side (no lifecycle
    # restriction on pinned/Unfiled counts), so the two sides of the
    # preview's comparison are genuinely comparable.
    notes_qs = Note.objects.filter(owner=owner)
    return {
        "folder_count": Folder.objects.filter(owner=owner).count(),
        "tag_count": Tag.objects.filter(owner=owner).count(),
        "note_count": notes_qs.count(),
        "active_note_count": notes_qs.filter(trashed_at__isnull=True).count(),
        "trash_note_count": notes_qs.filter(
            trashed_at__isnull=False, emptied_at__isnull=True
        ).count(),
        "recovery_note_count": notes_qs.filter(emptied_at__isnull=False).count(),
        "pinned_note_count": notes_qs.filter(pinned=True).count(),
        "unfiled_note_count": notes_qs.filter(folder__isnull=True).count(),
    }


@login_required
def library_backup_restore_preview(request: HttpRequest) -> HttpResponse:
    form = LibraryBackupUploadForm()
    context: dict = {"form": form}

    if request.method == "POST":
        guard = getattr(request, "library_backup_upload_guard", None)

        error_message = None
        preview = None

        if guard is None:
            error_message = LIBRARY_BACKUP_MALFORMED_UPLOAD_MESSAGE
        else:
            try:
                uploaded_files = request.FILES.getlist("backup_file")
                other_fields = [key for key in request.FILES if key != "backup_file"]
            except (MultiPartParserError, TooManyFilesSent):
                error_message = LIBRARY_BACKUP_MALFORMED_UPLOAD_MESSAGE
            else:
                if guard.request_too_large:
                    error_message = LIBRARY_BACKUP_REQUEST_TOO_LARGE_MESSAGE
                elif len(uploaded_files) > 1 or other_fields:
                    error_message = LIBRARY_BACKUP_EXTRA_FILE_MESSAGE
                elif not uploaded_files:
                    error_message = LIBRARY_BACKUP_NO_FILE_MESSAGE
                else:
                    uploaded_file = uploaded_files[0]
                    if uploaded_file.size <= 0:
                        error_message = LIBRARY_BACKUP_NO_FILE_MESSAGE
                    elif uploaded_file.size > library_backup_upload.MAX_LIBRARY_BACKUP_UPLOAD_BYTES:
                        error_message = LIBRARY_BACKUP_REQUEST_TOO_LARGE_MESSAGE
                    else:
                        try:
                            preview = library_backup_upload.validate_library_backup_upload(
                                uploaded_file, compressed_size=uploaded_file.size
                            )
                        except library_backup_upload.LibraryBackupUploadValidationError as exc:
                            error_message = LIBRARY_BACKUP_UPLOAD_FAILURE_MESSAGES.get(
                                exc.code, LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE
                            )

        if preview is not None:
            context["preview"] = preview
            context["current"] = _current_library_counts(request.user)
        elif error_message is not None:
            # Both the messages-framework banner (the no-JavaScript
            # fallback path: an ordinary full-page form submission) and
            # the inline `error_message` context value (consumed by
            # `library-restore.ts`'s async validation, which extracts
            # only `[data-library-restore-result-region]` from this same
            # response and never sees the messages banner at all) carry
            # the identical safe text -- one string, two independent
            # render paths, never duplicated logic.
            messages.error(request, error_message)
            context["error_message"] = error_message

    response = render(request, "notes/library_backup_restore_preview.html", context)
    response["Cache-Control"] = "private, no-store"
    return response


LIBRARY_BACKUP_RESTORE_SEMANTIC_FAILURE_MESSAGES = {
    "duplicate_active_folder_name": "The backup contains two active folders with the same name.",
    "duplicate_tag_name": "The backup contains two tags with the same name.",
    "multiple_recovery_markers": "The backup contains more than one recovery-folder marker.",
}
LIBRARY_BACKUP_RESTORE_SUCCESS_MESSAGE = "Library restored successfully."


@login_required
@require_POST
def library_backup_restore(request: HttpRequest) -> HttpResponse:
    # POST-only: the standalone
    # GET-rendered restore page is retired -- the corrected flow submits
    # this destructive endpoint only from the confirmation dialog on
    # `library_backup_restore_preview`, using the same browser-held
    # File selection validated there. A GET here now correctly 405s via
    # @require_POST, matching this project's existing
    # @require_GET/@require_POST convention (e.g. `library_backup_download`).
    #
    # JSON vs redirect:
    # reuses `_wants_json_response()` verbatim -- the same content
    # negotiation `folder_create` already uses -- rather than inventing a
    # second convention. The JS-enhanced dialog sends `Accept:
    # application/json` and gets a small safe JSON envelope so it can
    # transform itself into an in-place success state instead of
    # navigating away; a plain non-JS form POST is entirely unaffected
    # and keeps the original messages+redirect behavior below. Every
    # validation/transaction/audit code path is identical either way --
    # only the final response shape differs, and only at the two exit
    # points below. An *unexpected* exception anywhere above those two
    # exit points is never caught here, so it always propagates as an
    # ordinary 500 regardless of which response shape was requested.
    wants_json = _wants_json_response(request)

    guard = getattr(request, "library_backup_upload_guard", None)

    error_message = None

    if guard is None:
        error_message = LIBRARY_BACKUP_MALFORMED_UPLOAD_MESSAGE
    else:
        try:
            uploaded_files = request.FILES.getlist("backup_file")
            other_fields = [key for key in request.FILES if key != "backup_file"]
        except (MultiPartParserError, TooManyFilesSent):
            error_message = LIBRARY_BACKUP_MALFORMED_UPLOAD_MESSAGE
        else:
            if guard.request_too_large:
                error_message = LIBRARY_BACKUP_REQUEST_TOO_LARGE_MESSAGE
            elif len(uploaded_files) > 1 or other_fields:
                error_message = LIBRARY_BACKUP_EXTRA_FILE_MESSAGE
            elif not uploaded_files:
                error_message = LIBRARY_BACKUP_NO_FILE_MESSAGE
            else:
                uploaded_file = uploaded_files[0]
                if uploaded_file.size <= 0:
                    error_message = LIBRARY_BACKUP_NO_FILE_MESSAGE
                elif uploaded_file.size > library_backup_upload.MAX_LIBRARY_BACKUP_UPLOAD_BYTES:
                    error_message = LIBRARY_BACKUP_REQUEST_TOO_LARGE_MESSAGE
                else:
                    try:
                        validated = library_backup_upload.load_library_backup_for_restore(
                            uploaded_file, compressed_size=uploaded_file.size
                        )
                    except library_backup_upload.LibraryBackupUploadValidationError as exc:
                        error_message = LIBRARY_BACKUP_UPLOAD_FAILURE_MESSAGES.get(
                            exc.code, LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE
                        )
                    else:
                        try:
                            library_backup_restore_service.validate_library_backup_for_restore(
                                validated.manifest
                            )
                        except (
                            library_backup_restore_service.LibraryBackupRestoreValidationError
                        ) as exc:
                            error_message = LIBRARY_BACKUP_RESTORE_SEMANTIC_FAILURE_MESSAGES.get(
                                exc.code, LIBRARY_BACKUP_UPLOAD_DEFAULT_FAILURE_MESSAGE
                            )
                        else:
                            bound_form = LibraryBackupRestoreForm(request.POST, request.FILES)
                            bound_form.is_valid()
                            if "confirmation" in bound_form.errors:
                                error_message = bound_form.errors["confirmation"][0]
                            else:
                                library_backup_restore_service.restore_library_backup_for_owner(
                                    request.user, validated.manifest, request=request
                                )
                                if wants_json:
                                    return JsonResponse(
                                        {"ok": True, "redirect_url": reverse("home")}
                                    )
                                messages.success(request, LIBRARY_BACKUP_RESTORE_SUCCESS_MESSAGE)
                                return redirect("home")

    if wants_json:
        return JsonResponse({"ok": False, "error": error_message}, status=400)
    messages.error(request, error_message)
    return redirect("notes:library_backup_restore_preview")


@login_required
@require_POST
def note_create(request: HttpRequest) -> HttpResponse:
    note = services.create_note(owner=request.user)
    detail_url = reverse("notes:detail", args=[note.id])
    return redirect(f"{detail_url}?{urlencode({'new': '1'})}")


@login_required
@require_POST
def note_create_sibling(request: HttpRequest, note_id: int) -> HttpResponse:
    """Create a new note with a user-chosen destination, defaulting to
    ``note_id``'s own folder (or Unfiled).

    Backs the active-note overflow's New note action. Reuses
    `NoteMoveForm` unchanged -- the same owner-scoped,
    trashed-folder-excluding queryset the Move UI already validates
    against -- rather than duplicating destination-eligibility rules; a
    tampered cross-owner or trashed folder ID simply fails validation
    the same way an invalid Move submission does.
    """
    current_note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if current_note.trashed_at is not None:
        messages.error(request, TRASHED_ITEM_MESSAGE)
        return redirect("notes:detail", note_id=current_note.id)

    form = NoteMoveForm(request.POST, owner=request.user)
    if not form.is_valid():
        messages.error(request, "Could not create the note. Please try again.")
        return redirect("notes:detail", note_id=current_note.id)

    try:
        note = services.create_note(owner=request.user, folder=form.cleaned_data["folder"])
    except services.TrashedItemMutationError:
        messages.error(request, TRASHED_ITEM_MESSAGE)
        return redirect("notes:detail", note_id=current_note.id)
    detail_url = reverse("notes:detail", args=[note.id])
    return redirect(f"{detail_url}?{urlencode({'new': '1'})}")


def _folder_note_create(
    request: HttpRequest, folder_id: int, *, fallback_redirect: str
) -> HttpResponse:
    folder = services.folder_for_owner_or_404(folder_id=folder_id, owner=request.user)
    try:
        note = services.create_note(owner=request.user, folder=folder)
    except services.TrashedItemMutationError:
        messages.error(request, TRASHED_ITEM_MESSAGE)
        return redirect(fallback_redirect)
    detail_url = reverse("notes:detail", args=[note.id])
    return redirect(f"{detail_url}?{urlencode({'new': '1'})}")


@login_required
@require_POST
def folder_note_create(request: HttpRequest, note_id: int, folder_id: int) -> HttpResponse:
    current_note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    return _folder_note_create(
        request,
        folder_id,
        fallback_redirect=reverse("notes:detail", args=[current_note.id]),
    )


@login_required
@require_POST
def folder_note_create_home(request: HttpRequest, folder_id: int) -> HttpResponse:
    return _folder_note_create(request, folder_id, fallback_redirect=reverse("home"))


@login_required
@require_GET
def quick_switch_search(request: HttpRequest) -> JsonResponse:
    query = request.GET.get("q", "")

    exclude_note_id: int | None = None
    exclude_param = request.GET.get("exclude")
    if exclude_param is not None:
        try:
            exclude_note_id = int(exclude_param)
        except ValueError:
            exclude_note_id = None

    notes = services.search_notes_for_quick_switch(
        owner=request.user,
        query=query,
        exclude_note_id=exclude_note_id,
    )
    results = [
        {
            "id": note.id,
            "title": note.title,
            "folder_label": note.folder.name if note.folder_id else services.UNFILED_FOLDER_LABEL,
            "url": reverse("notes:detail", args=[note.id]),
        }
        for note in notes
    ]
    return JsonResponse({"ok": True, "results": results})


@login_required
@require_GET
def global_search(request: HttpRequest) -> JsonResponse:
    query = request.GET.get("q", "")
    notes = services.search_notes_global(owner=request.user, query=query)
    results = [
        {
            "id": note.id,
            "folder_label": note.folder.name if note.folder_id else services.UNFILED_FOLDER_LABEL,
            "url": reverse("notes:detail", args=[note.id]),
            "title_segments": services.build_segments_from_headline(note.title_headline),
            "body_segments": services.build_segments_from_headline(note.body_headline),
        }
        for note in notes
    ]
    return JsonResponse({"ok": True, "results": results})


# Full Search/Filter. A dedicated, paginated,
# bookmarkable destination page -- genuinely separate from the Global
# Search dialog/route above, which stays completely untouched.
FULL_SEARCH_PAGE_SIZE = 50
FULL_SEARCH_UNFILED_VALUE = "unfiled"

FULL_SEARCH_TAG_MODE_DEFAULT = "any"
FULL_SEARCH_TAG_MODE_CHOICES = (
    (FULL_SEARCH_TAG_MODE_DEFAULT, "Any"),
    ("all", "All"),
)
_FULL_SEARCH_TAG_MODE_VALUES = frozenset(value for value, _label in FULL_SEARCH_TAG_MODE_CHOICES)

FULL_SEARCH_TAG_STATUS_DEFAULT = "any"
FULL_SEARCH_TAG_STATUS_CHOICES = (
    (FULL_SEARCH_TAG_STATUS_DEFAULT, "Any"),
    ("tagged", "Has tags"),
    ("untagged", "Without tags"),
)
_FULL_SEARCH_TAG_STATUS_VALUES = frozenset(
    value for value, _label in FULL_SEARCH_TAG_STATUS_CHOICES
)

FULL_SEARCH_SEARCH_IN_DEFAULT = "both"
FULL_SEARCH_SEARCH_IN_CHOICES = (
    (FULL_SEARCH_SEARCH_IN_DEFAULT, "Both"),
    ("title", "Titles only"),
    ("body", "Notes only"),
)
_FULL_SEARCH_SEARCH_IN_VALUES = frozenset(value for value, _label in FULL_SEARCH_SEARCH_IN_CHOICES)

# Full Search's Lifecycle control is a single "Include
# Trash" checkbox -- there is no user-facing Trash-only mode in Full
# Search. `"1"` is the only value that activates it; anything else
# (absent, malformed, any other string) is `False` -- a closed,
# unambiguous two-value vocabulary, not a general boolean parser.
FULL_SEARCH_INCLUDE_TRASH_VALUE = "1"

# Aligned with the existing `MAX_TAGS_PER_NOTE` limit.
MAX_FULL_SEARCH_TAGS = services.MAX_TAGS_PER_NOTE

FULL_SEARCH_TEXT_ERROR_MESSAGES = {
    "query_too_long": (
        f"Your search text is too long (over {services.GLOBAL_SEARCH_MAX_QUERY_LENGTH} "
        "characters). Shorten it and search again."
    ),
    "query_has_no_searchable_terms": (
        "Your search text didn't contain any words that can be searched. Try different words."
    ),
}

FULL_SEARCH_TAGS_ERROR_MESSAGES = {
    "too_many_tags": f"Select at most {MAX_FULL_SEARCH_TAGS} tags, then search again.",
    "tags_conflict_with_untagged": (
        'Specific tags can\'t be combined with "Without tags." '
        "Remove your tag selections or choose a different tag status."
    ),
}


@dataclass
class FullSearchState:
    q: str
    search_in: str
    tag_ids: list[int]
    tag_mode: str
    tag_status: str
    folder_id: int | None
    folder_is_unfiled: bool
    include_trash: bool
    page: int


def _coerce_full_search_tag_mode(value) -> str:
    return value if value in _FULL_SEARCH_TAG_MODE_VALUES else FULL_SEARCH_TAG_MODE_DEFAULT


def _coerce_full_search_tag_status(value) -> str:
    return value if value in _FULL_SEARCH_TAG_STATUS_VALUES else FULL_SEARCH_TAG_STATUS_DEFAULT


def _coerce_full_search_search_in(value) -> str:
    return value if value in _FULL_SEARCH_SEARCH_IN_VALUES else FULL_SEARCH_SEARCH_IN_DEFAULT


def _parse_full_search_tags(request: HttpRequest, owner) -> tuple[list[int], bool]:
    """Returns `(valid_owned_tag_ids, too_many)`. Malformed/nonexistent/
    foreign-owner Tag IDs are silently discarded here and never appear
    in the returned list -- no disclosure of whether a foreign Tag ID
    exists. The returned list is deduplicated but **not** truncated
    even when it exceeds `MAX_FULL_SEARCH_TAGS` -- `too_many` signals
    that to the caller, which must treat the whole Tag filter as an
    invalid state rather than silently searching the first 20. Keeping
    the full list (not just the first 20) lets the filter form and
    active-filter chips still accurately reflect what the owner
    actually selected, so they can remove entries down to the limit."""
    raw_ids: list[int] = []
    for raw in request.GET.getlist("tag"):
        try:
            raw_ids.append(int(raw))
        except ValueError:
            continue
    if not raw_ids:
        return [], False
    owned_ids = set(Tag.objects.filter(owner=owner, pk__in=raw_ids).values_list("pk", flat=True))
    valid_ids = list(dict.fromkeys(i for i in raw_ids if i in owned_ids))
    return valid_ids, len(valid_ids) > MAX_FULL_SEARCH_TAGS


def _parse_full_search_folder(request: HttpRequest, owner) -> tuple[int | None, bool]:
    """Returns `(folder_id, folder_is_unfiled)`. A foreign/nonexistent/
    invalid `folder=` value fails safely to global scope (`None,
    False`) -- never disclosed, never trusted directly in a query,
    exactly like Admin Recovery's `?owner=` handling."""
    raw = request.GET.get("folder")
    if raw is None:
        return None, False
    if raw == FULL_SEARCH_UNFILED_VALUE:
        return None, True
    try:
        folder_id = int(raw)
    except ValueError:
        return None, False
    owned_ids = set(
        Folder.objects.filter(owner=owner, trashed_at__isnull=True).values_list("pk", flat=True)
    )
    return (folder_id, False) if folder_id in owned_ids else (None, False)


def _parse_full_search_state(
    request: HttpRequest,
) -> tuple[FullSearchState, str | None, str | None]:
    """Parses and validates the complete Full Search GET state in one
    place, before any query is built -- per the authorized correction
    that request/state validation belongs in the view, not inside
    `services.search_notes_full()`. Returns `(state, text_error,
    tags_error)`; `state` always reflects exactly what was submitted/
    safely-coerced, so the filter form and active-filter chips can
    echo it back even when the search itself is rejected as invalid."""
    owner = request.user
    q = request.GET.get("q", "")
    search_in = _coerce_full_search_search_in(request.GET.get("search_in"))
    tag_ids, too_many_tags = _parse_full_search_tags(request, owner)
    tag_mode = _coerce_full_search_tag_mode(request.GET.get("tag_mode"))
    tag_status = _coerce_full_search_tag_status(request.GET.get("tag_status"))
    folder_id, folder_is_unfiled = _parse_full_search_folder(request, owner)
    include_trash = request.GET.get("include_trash") == FULL_SEARCH_INCLUDE_TRASH_VALUE
    page = _coerce_all_notes_page(request.GET.get("page"))

    state = FullSearchState(
        q=q,
        search_in=search_in,
        tag_ids=tag_ids,
        tag_mode=tag_mode,
        tag_status=tag_status,
        folder_id=folder_id,
        folder_is_unfiled=folder_is_unfiled,
        include_trash=include_trash,
        page=page,
    )

    text_error = services.validate_full_search_text(q)

    tags_error = None
    if too_many_tags:
        tags_error = "too_many_tags"
    elif tag_ids and tag_status == "untagged":
        tags_error = "tags_conflict_with_untagged"

    return state, text_error, tags_error


def _full_search_base_params(state: FullSearchState) -> dict[str, object]:
    """The current filter state as a plain query-param dict, omitting
    any value that matches its default (so a serialized URL stays as
    short/clean as the established convention elsewhere on this page).
    Deliberately excludes `page` -- callers add that back explicitly
    where relevant."""
    params: dict[str, object] = {}
    if state.q:
        params["q"] = state.q
    if state.search_in != FULL_SEARCH_SEARCH_IN_DEFAULT:
        params["search_in"] = state.search_in
    if state.tag_ids:
        params["tag"] = list(state.tag_ids)
        if state.tag_mode != FULL_SEARCH_TAG_MODE_DEFAULT:
            params["tag_mode"] = state.tag_mode
    if state.tag_status != FULL_SEARCH_TAG_STATUS_DEFAULT:
        params["tag_status"] = state.tag_status
    if state.folder_is_unfiled:
        params["folder"] = FULL_SEARCH_UNFILED_VALUE
    elif state.folder_id is not None:
        params["folder"] = state.folder_id
    if state.include_trash:
        params["include_trash"] = FULL_SEARCH_INCLUDE_TRASH_VALUE
    return params


def _full_search_url(params: dict[str, object], *, page: int | None = None) -> str:
    query_params = dict(params)
    if page and page > 1:
        query_params["page"] = page
    query_string = urlencode(query_params, doseq=True)
    base = reverse("notes:full_search")
    return f"{base}?{query_string}" if query_string else base


def _full_search_active_filters(
    state: FullSearchState, *, selected_tags, selected_folder
) -> list[dict[str, str]]:
    """Server-rendered active-filter chips -- each one a plain `<a
    href>` to the remaining GET state with that one filter removed, so
    removal needs no JavaScript at all."""
    base_params = _full_search_base_params(state)
    chips: list[dict[str, str]] = []

    if state.q:
        params = dict(base_params)
        params.pop("q", None)
        params.pop("search_in", None)
        chips.append({"label": f'Text: "{state.q}"', "remove_url": _full_search_url(params)})

    if state.q and state.search_in != FULL_SEARCH_SEARCH_IN_DEFAULT:
        params = dict(base_params)
        params.pop("search_in", None)
        label = "Titles only" if state.search_in == "title" else "Notes only"
        chips.append({"label": f"Search in: {label}", "remove_url": _full_search_url(params)})

    for tag in selected_tags:
        params = dict(base_params)
        remaining = [tag_id for tag_id in state.tag_ids if tag_id != tag.id]
        if remaining:
            params["tag"] = remaining
        else:
            params.pop("tag", None)
            params.pop("tag_mode", None)
        chips.append({"label": f"Tag: {tag.name}", "remove_url": _full_search_url(params)})

    if state.folder_is_unfiled:
        params = dict(base_params)
        params.pop("folder", None)
        chips.append({"label": "Folder: Unfiled", "remove_url": _full_search_url(params)})
    elif selected_folder is not None:
        params = dict(base_params)
        params.pop("folder", None)
        chips.append(
            {"label": f"Folder: {selected_folder.name}", "remove_url": _full_search_url(params)}
        )

    if state.tag_status != FULL_SEARCH_TAG_STATUS_DEFAULT:
        params = dict(base_params)
        params.pop("tag_status", None)
        label = "Has tags" if state.tag_status == "tagged" else "Without tags"
        chips.append({"label": label, "remove_url": _full_search_url(params)})

    if state.include_trash:
        params = dict(base_params)
        params.pop("include_trash", None)
        chips.append({"label": "Include Trash", "remove_url": _full_search_url(params)})

    return chips


FULL_SEARCH_IDLE_MESSAGE = "Enter search text or choose one or more filters."


def _full_search_has_meaningful_criteria(state: FullSearchState) -> bool:
    """A bare visit
    (or an explicit all-defaults submission -- blank `q`, no Tags,
    `tag_mode=any`, `tag_status=any`, no folder, `include_trash`
    unchecked, default/absent `page`) must stay idle rather than
    silently running the broad "every Active note" search. Only called
    once the request has already passed validation -- an invalid filter
    state is handled separately and never reaches this check, per the
    authorized "do not treat invalid state as idle" requirement.
    `search_in` alone never makes a request meaningful -- it only
    modifies text search behavior, and text search isn't active without
    nonblank text, which is already covered by the `q` check below."""
    if state.q.strip():
        return True
    if state.tag_ids:
        return True
    if state.tag_status != FULL_SEARCH_TAG_STATUS_DEFAULT:
        return True
    if state.folder_is_unfiled or state.folder_id is not None:
        return True
    if state.include_trash:
        return True
    return False


@login_required
@require_GET
def full_search(request: HttpRequest) -> HttpResponse:
    owner = request.user
    state, text_error, tags_error = _parse_full_search_state(request)

    folders, unfiled_notes = services.notes_grouped_for_tree(owner=owner)
    selected_tags = (
        list(Tag.objects.filter(owner=owner, pk__in=state.tag_ids)) if state.tag_ids else []
    )
    selected_folder = None
    if state.folder_id is not None:
        selected_folder = next((folder for folder in folders if folder.id == state.folder_id), None)

    validation_error = None
    if text_error:
        validation_error = FULL_SEARCH_TEXT_ERROR_MESSAGES[text_error]
    elif tags_error:
        validation_error = FULL_SEARCH_TAGS_ERROR_MESSAGES[tags_error]

    is_idle = not validation_error and not _full_search_has_meaningful_criteria(state)
    idle_message = FULL_SEARCH_IDLE_MESSAGE if is_idle else None

    if validation_error or is_idle:
        page_obj = Paginator(Note.objects.none(), FULL_SEARCH_PAGE_SIZE).page(1)
        results: list[dict] = []
    else:
        notes_queryset = services.search_notes_full(
            owner=owner,
            query=state.q,
            search_in=state.search_in,
            tag_ids=state.tag_ids,
            tag_mode=state.tag_mode,
            tag_status=state.tag_status,
            folder_id=state.folder_id,
            folder_is_unfiled=state.folder_is_unfiled,
            include_trash=state.include_trash,
        )
        paginator = Paginator(notes_queryset, FULL_SEARCH_PAGE_SIZE)
        page_number = min(state.page, paginator.num_pages)
        page_obj = paginator.page(page_number)
        has_text = bool(state.q.strip())
        # Only ever read `.title_headline`/`.body_headline` for a field
        # `search_notes_full()` actually annotated -- it only annotates
        # the field(s) the selected `search_in` scope needs, so reading
        # an excluded field's headline here would raise `AttributeError`.
        # A field excluded from the search scope falls back to plain
        # display (handled entirely by `_full_search_row.html`'s
        # existing `{% if title_segments %}...{% else %}{{ note.title }}
        # {% endif %}`-style branching) -- never a fabricated highlight.
        show_title_segments = has_text and state.search_in in ("both", "title")
        show_body_segments = has_text and state.search_in in ("both", "body")
        results = [
            {
                "note": note,
                "title_segments": (
                    services.build_segments_from_headline(note.title_headline)
                    if show_title_segments
                    else None
                ),
                "body_segments": (
                    services.build_segments_from_headline(note.body_headline)
                    if show_body_segments
                    else None
                ),
            }
            for note in page_obj.object_list
        ]

    base_params = _full_search_base_params(state)
    active_filters = _full_search_active_filters(
        state, selected_tags=selected_tags, selected_folder=selected_folder
    )

    return render(
        request,
        "notes/full_search.html",
        {
            "nav_drawer_available": True,
            "note": None,
            "unfiled_notes": unfiled_notes,
            "state": state,
            "validation_error": validation_error,
            "idle_message": idle_message,
            "results": results,
            "page_obj": page_obj,
            "folders": folders,
            "tags": services.list_tags_for_owner(owner=owner),
            "selected_tags": selected_tags,
            "selected_folder": selected_folder,
            "tag_mode_choices": FULL_SEARCH_TAG_MODE_CHOICES,
            "tag_status_choices": FULL_SEARCH_TAG_STATUS_CHOICES,
            "search_in_choices": FULL_SEARCH_SEARCH_IN_CHOICES,
            "max_full_search_tags": MAX_FULL_SEARCH_TAGS,
            "full_search_unfiled_value": FULL_SEARCH_UNFILED_VALUE,
            "active_filters": active_filters,
            "clear_all_url": reverse("notes:full_search"),
            "prev_page_url": (
                _full_search_url(base_params, page=page_obj.previous_page_number())
                if page_obj.has_previous()
                else None
            ),
            "next_page_url": (
                _full_search_url(base_params, page=page_obj.next_page_number())
                if page_obj.has_next()
                else None
            ),
        },
    )


def _note_form_initial(note: Note) -> dict[str, str | int]:
    return {
        "title": note.title,
        "body_json": json.dumps(note.body_json),
        "version": note.version,
    }


def _render_note_detail(
    request: HttpRequest,
    *,
    note: Note,
    form: NoteUpdateForm,
    status: int = 200,
    new_folder_error: str | None = None,
    new_folder_name: str | None = None,
    tag_error: str | None = None,
    tag_name: str | None = None,
) -> HttpResponse:
    generated_title = documents.generated_title_for_timestamp(note.created_at)
    is_immediate_new_note_page = (
        request.method == "GET" and request.GET.get("new") == "1" and note.title == generated_title
    )
    folders, unfiled_notes = services.notes_grouped_for_tree(owner=note.owner)
    owner_tag_names = list(
        services.list_tags_for_owner(owner=note.owner).values_list("name", flat=True)
    )
    recent_notes = services.recent_notes_for_owner(owner=note.owner, exclude_note_id=note.id)
    return render(
        request,
        "notes/detail.html",
        {
            "form": form,
            "note": note,
            "folders": folders,
            "unfiled_notes": unfiled_notes,
            "editor_document_script_id": f"note-document-{note.id}",
            "editor_schema_version": note.editor_schema_version,
            "generated_title": generated_title,
            "is_immediate_new_note_page": is_immediate_new_note_page,
            "note_tags": note.tags.all(),
            "owner_tag_names": owner_tag_names,
            "tag_color_choices": TAG_COLOR_CHOICES,
            "recent_notes": recent_notes,
            "new_folder_error": new_folder_error,
            "new_folder_name": new_folder_name,
            "tag_error": tag_error,
            "tag_name": tag_name,
        },
        status=status,
    )


def _json_authentication_required() -> JsonResponse:
    return JsonResponse({"ok": False, "error": "authentication_required"}, status=401)


def _json_unsupported_schema() -> JsonResponse:
    return JsonResponse({"ok": False, "error": "unsupported_schema"}, status=409)


def _json_trashed_item() -> JsonResponse:
    return JsonResponse(
        {"ok": False, "error": "trashed", "message": TRASHED_ITEM_MESSAGE}, status=409
    )


@login_required
@require_http_methods(["GET", "POST"])
def note_detail(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if note.trashed_at is not None:
        return redirect("notes:trash")
    if not services.note_is_supported(note):
        if request.method == "POST":
            messages.error(
                request,
                "This note uses an unsupported document version and cannot be saved.",
            )
            return render(request, "notes/unsupported_schema.html", {"note": note}, status=409)
        return render(request, "notes/unsupported_schema.html", {"note": note})

    if request.method == "POST":
        form = NoteUpdateForm(request.POST)
        if form.is_valid():
            try:
                services.save_note(
                    note=note,
                    title=form.cleaned_data["title"],
                    body_json=form.cleaned_data["body_json"],
                    version=form.cleaned_data["version"],
                )
            except services.NoteSaveConflictError:
                messages.error(request, CONFLICT_MESSAGE)
                form.add_error(None, CONFLICT_MESSAGE)
                return _render_note_detail(request, note=note, form=form, status=409)
            except services.UnsupportedSchemaError:
                messages.error(
                    request,
                    "This note uses an unsupported document version and cannot be saved.",
                )
                return render(request, "notes/unsupported_schema.html", {"note": note}, status=409)
            messages.success(request, "Note saved.")
            return redirect("notes:detail", note_id=note.id)
    else:
        form = NoteUpdateForm(initial=_note_form_initial(note))

    return _render_note_detail(request, note=note, form=form)


@require_POST
def note_autosave(request: HttpRequest, note_id: int) -> JsonResponse:
    if not request.user.is_authenticated:
        return _json_authentication_required()

    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if not services.note_is_supported(note):
        return _json_unsupported_schema()
    if note.trashed_at is not None:
        return _json_trashed_item()

    try:
        payload = json.loads(request.body.decode() or "{}")
    except json.JSONDecodeError:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "__all__": [
                        {
                            "message": "The save request must be valid JSON.",
                            "code": "invalid_json",
                        }
                    ]
                },
            },
            status=400,
        )

    form_payload = dict(payload)
    if "body_json" in form_payload:
        form_payload["body_json"] = json.dumps(form_payload["body_json"])
    form = NoteUpdateForm(form_payload)
    if not form.is_valid():
        return JsonResponse({"ok": False, "errors": form.errors.get_json_data()}, status=400)

    try:
        saved_note = services.save_note(
            note=note,
            title=form.cleaned_data["title"],
            body_json=form.cleaned_data["body_json"],
            version=form.cleaned_data["version"],
        )
    except services.NoteSaveConflictError as exc:
        return JsonResponse(
            {
                "ok": False,
                "error": "conflict",
                "current_version": exc.current_version,
                "current_modified_at": exc.current_modified_at.isoformat(),
            },
            status=409,
        )
    except services.UnsupportedSchemaError:
        return _json_unsupported_schema()
    except services.TrashedItemMutationError:
        return _json_trashed_item()

    return JsonResponse(
        {
            "ok": True,
            "title": saved_note.title,
            "version": saved_note.version,
            "modified_at": saved_note.modified_at.isoformat(),
        }
    )


@require_GET
def note_freshness(request: HttpRequest, note_id: int) -> JsonResponse:
    if not request.user.is_authenticated:
        return _json_authentication_required()

    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if not services.note_content_accessible_to_owner(note):
        return _json_trashed_item()
    if not services.note_is_supported(note):
        return _json_unsupported_schema()

    version_text = request.GET.get("version", "")
    try:
        client_version = int(version_text)
    except (TypeError, ValueError):
        client_version = 0

    if client_version < 1:
        return JsonResponse(
            {
                "ok": False,
                "errors": {
                    "version": [
                        {
                            "message": "A valid note version is required.",
                            "code": "invalid",
                        }
                    ]
                },
            },
            status=400,
        )

    if client_version < note.version:
        return JsonResponse(
            {
                "ok": True,
                "has_update": True,
                "note": services.note_sync_payload(note),
            }
        )

    return JsonResponse(
        {
            "ok": True,
            "has_update": False,
            "version": note.version,
            "modified_at": note.modified_at.isoformat(),
        }
    )


@login_required
@require_GET
def note_print(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if not services.note_content_accessible_to_owner(note):
        messages.error(request, NOTE_CONTENT_NOT_AVAILABLE_MESSAGE)
        return redirect("notes:trash")
    if not services.note_is_supported(note):
        messages.error(request, "Unsupported document versions cannot be printed.")
        return render(request, "notes/unsupported_schema.html", {"note": note}, status=409)

    return render(
        request,
        "notes/print.html",
        {
            "note": note,
            "rendered_body": documents.render_document_html(note.body_json),
        },
    )


def _create_folder_from_post(request: HttpRequest) -> tuple[bool, str, str | None, int]:
    """Attempts folder creation and reports the outcome instead of messaging/redirecting.

    Returns (created, attempted_name, error_message, status). Callers redirect
    on success and render the current page in place (with the attempted name
    and error preserved for the New Folder popover) on failure, matching the
    codebase's existing bound-form-render-in-place convention rather than a
    Django message plus redirect.
    """
    form = FolderCreateForm(request.POST)
    attempted_name = request.POST.get("name", "")
    if form.is_valid():
        try:
            services.create_folder(owner=request.user, name=form.cleaned_data["name"])
        except services.FolderNameConflictError:
            return False, attempted_name, "A folder with that name already exists.", 409
        return True, attempted_name, None, 200
    return False, attempted_name, "Folder name is required.", 400


def _wants_json_response(request: HttpRequest) -> bool:
    """Content negotiation for the New Folder popover's async submission.

    The popover's enhanced (JavaScript) path asks for `application/json` so
    it can update itself in place; a plain browser POST (no JS, or the
    no-JS fallback) asks for HTML and gets the existing render-in-place/
    redirect behavior. Same route, same `_create_folder_from_post`
    validation/creation logic either way -- only the response shape differs.
    """
    return "application/json" in request.headers.get("Accept", "")


@login_required
@require_POST
def folder_create(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    created, attempted_name, error, status = _create_folder_from_post(request)
    redirect_url = reverse("notes:detail", args=[note.id])
    if _wants_json_response(request):
        if created:
            return JsonResponse({"ok": True, "redirect_url": redirect_url})
        return JsonResponse({"ok": False, "error": error, "name": attempted_name}, status=status)
    if created:
        return redirect(redirect_url)
    form = NoteUpdateForm(initial=_note_form_initial(note))
    return _render_note_detail(
        request,
        note=note,
        form=form,
        status=status,
        new_folder_error=error,
        new_folder_name=attempted_name,
    )


@login_required
@require_POST
def folder_create_home(request: HttpRequest) -> HttpResponse:
    created, attempted_name, error, status = _create_folder_from_post(request)
    redirect_url = reverse("home")
    if _wants_json_response(request):
        if created:
            return JsonResponse({"ok": True, "redirect_url": redirect_url})
        return JsonResponse({"ok": False, "error": error, "name": attempted_name}, status=status)
    if created:
        return redirect(redirect_url)
    context = _home_context(request)
    context["new_folder_error"] = error
    context["new_folder_name"] = attempted_name
    return render(request, "notes/home.html", context, status=status)


def _rename_folder_from_post(request: HttpRequest, folder: Folder) -> None:
    form = FolderRenameForm(request.POST)
    if form.is_valid():
        try:
            services.rename_folder(folder=folder, name=form.cleaned_data["name"])
        except services.TrashedItemMutationError:
            messages.error(request, TRASHED_ITEM_MESSAGE)
        except services.FolderNameConflictError:
            messages.error(request, "A folder with that name already exists.")
    else:
        messages.error(request, "Folder name is required.")


@login_required
@require_POST
def folder_rename(request: HttpRequest, note_id: int, folder_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    folder = services.folder_for_owner_or_404(folder_id=folder_id, owner=request.user)
    _rename_folder_from_post(request, folder)
    return redirect("notes:detail", note_id=note.id)


@login_required
@require_POST
def folder_rename_home(request: HttpRequest, folder_id: int) -> HttpResponse:
    folder = services.folder_for_owner_or_404(folder_id=folder_id, owner=request.user)
    _rename_folder_from_post(request, folder)
    return redirect("home")


# Delete-origin and confirmation-return
# convergence. A closed, server-validated return-state contract for
# where a delete confirmation's Cancel/success/already-in-Trash
# destinations point, replacing the previous unconditional
# `redirect("notes:trash")` every tree-originated delete used. Never
# extended with a raw path, URL, or user-supplied route name -- only
# ever used to select among a fixed set of hardcoded `redirect(...)`
# call sites, or the two proven All-Notes/note-detail helpers already
# used elsewhere in this module.
class DeleteOrigin(StrEnum):
    HOME = "home"
    ALL_NOTES = "all_notes"
    NOTE_DETAIL = "note_detail"


@dataclass(frozen=True)
class DeleteReturnState:
    origin: DeleteOrigin | None
    page: int
    sort: str
    # Parsed, not yet validated -- `current_note_id` is only checked
    # against the database at destination-resolution time (see
    # `_delete_destination_url` below), deliberately, so the same state
    # can be resolved both before a mutation (Cancel, or the
    # already-trashed race path) and again after one (POST success),
    # always reflecting the note's *current* state rather than a
    # snapshot taken earlier in the request.
    current_note_id: int | None


def _parse_delete_return_state(source) -> DeleteReturnState:
    try:
        origin = DeleteOrigin(source.get("origin"))
    except ValueError:
        origin = None
    try:
        current_note_id = int(source.get("current_note"))
    except (TypeError, ValueError):
        current_note_id = None
    return DeleteReturnState(
        origin=origin,
        page=_coerce_all_notes_page(source.get("page")),
        sort=_coerce_all_notes_sort(source.get("sort")),
        current_note_id=current_note_id,
    )


def _delete_destination_url(
    state: DeleteReturnState, *, owner, adjacent_note: Note | None = None
) -> str:
    if state.origin is DeleteOrigin.ALL_NOTES:
        query = urlencode({"page": state.page, "sort": state.sort})
        return f"{reverse('notes:all_notes')}?{query}"
    if state.origin is DeleteOrigin.NOTE_DETAIL and state.current_note_id is not None:
        if Note.objects.filter(
            pk=state.current_note_id, owner=owner, trashed_at__isnull=True
        ).exists():
            return reverse("notes:detail", args=[state.current_note_id])
        # The currently-open note is no longer
        # available (it was the note just deleted, or became trashed as a
        # folder-delete cascade side effect) -- if the caller found a
        # still-active sibling in the same folder (only computed when the
        # note *just deleted* was the currently-open one, never for the
        # folder-cascade case per the approved contract), land there
        # instead of unconditionally on Home.
        if adjacent_note is not None:
            return reverse("notes:detail", args=[adjacent_note.id])
    # `DeleteOrigin.HOME`, an unrecognized/missing origin, or a
    # note-detail origin whose hosting note is no longer valid and had no
    # adjacent note to fall back to -- the one fixed, safe fallback. Never
    # Trash.
    return reverse("home")


def _folder_delete(
    request: HttpRequest, folder: Folder, *, state: DeleteReturnState
) -> HttpResponse:
    # Confirmation is now the in-context dialog
    # (`delete-confirm.ts`), so both callers below are POST-only -- there
    # is no GET confirmation-page branch left to render.
    if folder.trashed_at is not None:
        messages.info(request, "That folder is already in Trash.")
        return redirect(_delete_destination_url(state, owner=folder.owner))

    services.move_folder_to_trash(folder=folder)
    messages.success(
        request,
        "Folder moved to Trash. You can restore it from Trash; "
        "its notes can be restored individually.",
    )
    # No adjacent-note lookup here: per the approved contract, deleting a
    # folder that contains the currently-open note always goes Home, not
    # to a sibling note -- `_delete_destination_url`'s existing DB-existence
    # check already sends it there once the cascade above trashes that
    # note, with no `adjacent_note` argument needed.
    return redirect(_delete_destination_url(state, owner=folder.owner))


@login_required
@require_POST
def folder_delete(request: HttpRequest, note_id: int, folder_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    folder = services.folder_for_owner_or_404(folder_id=folder_id, owner=request.user)
    # The hosting note is the `note_id` URL segment itself -- never a
    # request field -- so it cannot be tampered with independently of
    # Django's own URL resolution, and no query/hidden-field state is
    # needed to carry it.
    state = DeleteReturnState(
        origin=DeleteOrigin.NOTE_DETAIL,
        page=1,
        sort=ALL_NOTES_SORT_DEFAULT,
        current_note_id=note.id,
    )
    return _folder_delete(request, folder, state=state)


@login_required
@require_POST
def folder_delete_home(request: HttpRequest, folder_id: int) -> HttpResponse:
    folder = services.folder_for_owner_or_404(folder_id=folder_id, owner=request.user)
    state = _parse_delete_return_state(request.POST)
    return _folder_delete(request, folder, state=state)


@login_required
@require_POST
def folder_restore(request: HttpRequest, folder_id: int) -> HttpResponse:
    folder = services.folder_for_owner_or_404(folder_id=folder_id, owner=request.user)
    if folder.trashed_at is not None:
        try:
            result = services.restore_folder_from_trash(folder=folder)
        except services.TrashItemNotRestorableError:
            messages.error(request, RESTORE_NOT_AVAILABLE_MESSAGE)
        except services.FolderNameConflictError:
            messages.error(
                request,
                "Could not restore the folder because an active folder with "
                "that name already exists.",
            )
        else:
            # The count shown in the pre-POST
            # confirmation dialog is advisory only -- this message always
            # reports the actual count the locked transaction restored.
            restored_count = len(result.restored_note_ids)
            if restored_count == 0:
                messages.success(request, "Folder restored.")
            elif restored_count == 1:
                messages.success(request, "Folder restored. 1 associated note was also restored.")
            else:
                messages.success(
                    request,
                    f"Folder restored. {restored_count} associated notes were also restored.",
                )
    return redirect("notes:trash")


def _move_note_from_post(request: HttpRequest, note: Note) -> None:
    if request.POST.get("folder") == NoteMoveForm.KEEP_CURRENT_FOLDER_VALUE:
        # No deliberate destination was chosen (the note's current folder is
        # trashed, so its own row was rendered as this non-selectable
        # placeholder). Treat this as a no-op rather than risk silently
        # unfiling the note.
        return

    form = NoteMoveForm(request.POST, owner=request.user)
    if form.is_valid():
        try:
            services.assign_note_folder(note=note, folder=form.cleaned_data["folder"])
        except services.TrashedItemMutationError:
            messages.error(request, TRASHED_ITEM_MESSAGE)
    else:
        messages.error(request, "Could not move the note. Please try again.")


@login_required
@require_POST
def note_move(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    _move_note_from_post(request, note)
    return redirect("notes:detail", note_id=note.id)


@login_required
@require_POST
def note_move_home(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    _move_note_from_post(request, note)
    return redirect("home")


@login_required
@require_POST
def note_move_all_notes(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    _move_note_from_post(request, note)
    return _all_notes_redirect(page=request.POST.get("page"), sort=request.POST.get("sort"))


def _rename_note_from_post(request: HttpRequest, note: Note) -> None:
    form = NoteRenameForm(request.POST)
    if form.is_valid():
        try:
            services.rename_note(note=note, title=form.cleaned_data["title"])
        except services.TrashedItemMutationError:
            messages.error(request, TRASHED_ITEM_MESSAGE)
    else:
        messages.error(request, "Could not rename the note. Please try again.")


@login_required
@require_POST
def note_rename(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    _rename_note_from_post(request, note)
    return redirect("notes:detail", note_id=note.id)


@login_required
@require_POST
def note_rename_home(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    _rename_note_from_post(request, note)
    return redirect("home")


@login_required
@require_POST
def note_rename_all_notes(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    _rename_note_from_post(request, note)
    return _all_notes_redirect(page=request.POST.get("page"), sort=request.POST.get("sort"))


@login_required
@require_POST
def note_duplicate(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if note.trashed_at is not None:
        messages.error(request, TRASHED_ITEM_MESSAGE)
        return redirect("notes:detail", note_id=note.id)
    try:
        duplicate = services.duplicate_note(note=note)
    except services.TrashedItemMutationError:
        messages.error(request, TRASHED_ITEM_MESSAGE)
        return redirect("notes:detail", note_id=note.id)
    return redirect("notes:detail", note_id=duplicate.id)


def _set_note_pinned_from_post(request: HttpRequest, note: Note) -> None:
    form = NotePinForm(request.POST)
    if form.is_valid():
        services.set_note_pinned(note=note, pinned=form.cleaned_data["pinned"])
    else:
        messages.error(request, "Could not update the pinned state. Please try again.")


@login_required
@require_POST
def note_pin_set(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if note.trashed_at is not None:
        messages.error(request, TRASHED_ITEM_MESSAGE)
        return redirect("home")
    _set_note_pinned_from_post(request, note)
    return redirect("home")


@login_required
@require_POST
def note_pin_set_all_notes(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    page = request.POST.get("page")
    sort = request.POST.get("sort")
    if note.trashed_at is not None:
        messages.error(request, TRASHED_ITEM_MESSAGE)
        return _all_notes_redirect(page=page, sort=sort)
    _set_note_pinned_from_post(request, note)
    return _all_notes_redirect(page=page, sort=sort)


@login_required
@require_GET
def note_tag_suggestions(request: HttpRequest, note_id: int) -> JsonResponse:
    """Read-only, **existing-tag-only** autocomplete
    suggestion feed for the Add Tag input -- reuse/attach only, never a
    create preview. Owner-scoped via the same `note_for_owner_or_404()`
    every other note-scoped endpoint uses -- a foreign note ID 404s
    identically. Performs no mutation whatsoever; new-tag creation goes
    exclusively through the unchanged, ordinary `note_tag_assign()` POST
    flow below, entirely outside this endpoint. At the 20-tag limit, no
    suggestions are offered at all -- attaching any not-yet-attached tag
    would fail at the authoritative `assign_tag_to_note()` boundary
    regardless, so there is nothing useful to suggest; the ordinary Add
    Tag form/server remains the sole source of limit feedback."""
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    raw_query = request.GET.get("q", "")

    attached_tag_ids = list(note.tags.values_list("pk", flat=True))
    at_tag_limit = len(attached_tag_ids) >= services.MAX_TAGS_PER_NOTE

    existing: list[dict] = []
    if not at_tag_limit:
        matches = services.search_tags_for_owner(
            owner=request.user, query=raw_query, exclude_ids=attached_tag_ids
        )
        existing = [{"id": tag.pk, "name": tag.name, "color": tag.color} for tag in matches]

    return JsonResponse({"ok": True, "existing": existing, "at_tag_limit": at_tag_limit})


@login_required
@require_POST
def note_tag_assign(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if note.trashed_at is not None:
        messages.error(request, TRASHED_ITEM_MESSAGE)
        return redirect("notes:detail", note_id=note.id)
    form = NoteTagAssignForm(request.POST, user=request.user)
    if form.is_valid():
        attempted_name = form.cleaned_data["name"]
        # A duplicate tag name (case-insensitive, matched against tags
        # already on *this* note) must not fall through to the "success"
        # path below: `assign_tag_to_note` is a no-op for an already-present
        # tag, so the request would still redirect -- an unexplained extra
        # POST-then-GET round trip with no visible error, reading as a
        # flicker with nothing having happened. It is caught here, before the
        # tag-creation/assignment service is reached at all, and rendered
        # in place exactly like the blank-name error below.
        if note.tags.filter(name__iexact=attempted_name).exists():
            return _render_note_tag_error(
                request,
                note=note,
                message="This tag is already added to this note.",
                attempted_name=attempted_name,
            )
        # Resolved before `get_or_create_tag()` is
        # ever called -- that function's existing reuse/race-recovery
        # branches already discard whatever color they're passed when
        # returning an existing row, so this resolution only ever
        # matters (and only ever takes effect) if a new Tag row is
        # actually created.
        creation_color = services.resolve_tag_color_for_creation(
            form.cleaned_data["color"],
            attempted_name,
            semantic_color_enabled=request.user.tag_semantic_color_enabled,
            default_color=request.user.tag_default_color,
        )
        tag = services.get_or_create_tag(
            owner=request.user,
            name=attempted_name,
            color=creation_color,
        )
        try:
            services.assign_tag_to_note(note=note, tag=tag)
        except services.TagLimitExceededError as exc:
            # The tag object itself is already
            # created/reused above (harmless, and consistent with
            # zero-use tags persisting until explicitly deleted) --
            # only the attachment to *this* note is rejected here, at
            # the authoritative service boundary.
            return _render_note_tag_error(
                request, note=note, message=str(exc), attempted_name=attempted_name
            )
        return redirect("notes:detail", note_id=note.id)

    # Rendered in place (not a global message plus redirect) so the error
    # appears directly beside the tag controls the attempt came from,
    # matching the existing bound-form/render-in-place convention already
    # used elsewhere on this page (e.g. New Folder validation) -- the
    # attempted name is preserved and the tag-creation service itself is
    # never reached on this path. The actual form
    # error (blank, over-length, unsupported characters) is surfaced
    # verbatim rather than a single hardcoded message, since those are
    # now three genuinely different, user-actionable conditions.
    name_errors = form.errors.get("name")
    error_message = name_errors[0] if name_errors else "Tag name is required."
    return _render_note_tag_error(
        request,
        note=note,
        message=error_message,
        attempted_name=request.POST.get("name", ""),
    )


def _render_note_tag_error(
    request: HttpRequest, *, note: Note, message: str, attempted_name: str
) -> HttpResponse:
    note_update_form = NoteUpdateForm(initial=_note_form_initial(note))
    return _render_note_detail(
        request,
        note=note,
        form=note_update_form,
        status=400,
        tag_error=message,
        tag_name=attempted_name,
    )


@login_required
@require_POST
def note_tag_remove(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if note.trashed_at is not None:
        messages.error(request, TRASHED_ITEM_MESSAGE)
        return redirect("notes:detail", note_id=note.id)
    form = NoteTagRemoveForm(request.POST)
    if form.is_valid():
        tag = services.tag_for_owner_or_404(tag_id=form.cleaned_data["tag_id"], owner=request.user)
        services.remove_tag_from_note(note=note, tag=tag)
    else:
        messages.error(request, "Could not remove the tag. Please try again.")
    return redirect("notes:detail", note_id=note.id)


@login_required
@require_GET
def note_export(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if not services.note_content_accessible_to_owner(note):
        messages.error(request, NOTE_CONTENT_NOT_AVAILABLE_MESSAGE)
        return redirect("notes:trash")
    payload = services.note_export_payload(note)
    safe_title = slugify(note.title) or "note"
    response = HttpResponse(
        json.dumps(payload, indent=2, sort_keys=True),
        content_type="application/json; charset=utf-8",
    )
    response["Content-Disposition"] = (
        f'attachment; filename="{safe_title}-{note.id}.ridgenote.json"'
    )
    return response


@login_required
@require_POST
def note_delete(request: HttpRequest, note_id: int) -> HttpResponse:
    # The full-page GET confirmation step was
    # replaced by an in-context dialog (`delete-confirm.ts`), so this view
    # is POST-only now -- confirmation happens client-side before this
    # request is ever sent, using data already rendered on the page, with
    # no server round-trip to open it.
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    state = _parse_delete_return_state(request.POST)

    # Only computed when the note being deleted is the currently-open one
    # (never for an unrelated tree-row delete elsewhere), and strictly
    # before any mutation below, so the note's own position among its
    # still-active siblings can still be located.
    adjacent_note = None
    if state.origin is DeleteOrigin.NOTE_DETAIL and state.current_note_id == note.id:
        adjacent_note = services.adjacent_active_note_in_same_folder(note=note)

    if note.trashed_at is not None:
        messages.info(request, "That note is already in Trash.")
        return redirect(
            _delete_destination_url(state, owner=request.user, adjacent_note=adjacent_note)
        )

    result = services.move_note_to_trash(note=note, request=request)
    if result is None:
        messages.success(
            request,
            "This blank, unused note was discarded instead of moved to Trash.",
        )
    else:
        messages.success(request, "Note moved to Trash. You can restore it from Trash.")
    return redirect(_delete_destination_url(state, owner=request.user, adjacent_note=adjacent_note))


@login_required
@require_POST
def note_delete_all_notes(request: HttpRequest, note_id: int) -> HttpResponse:
    # Unlike `note_delete` above (whose success
    # destination is the same regardless of caller), this variant's
    # destination is caller-sensitive -- completion must return to the
    # same All Notes page/sort the row was deleted from, not the note's
    # own detail page. Confirmation is now the
    # in-context dialog (`delete-confirm.ts`), so this view is POST-only;
    # `page`/`sort` arrive as hidden fields the dialog copies from the
    # triggering row before submitting.
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    validated_page = _coerce_all_notes_page(request.POST.get("page"))
    validated_sort = _coerce_all_notes_sort(request.POST.get("sort"))

    if note.trashed_at is not None:
        messages.info(request, "That note is already in Trash.")
        return _all_notes_redirect(page=validated_page, sort=validated_sort)

    result = services.move_note_to_trash(note=note, request=request)
    if result is None:
        messages.success(
            request,
            "This blank, unused note was discarded instead of moved to Trash.",
        )
    else:
        messages.success(request, "Note moved to Trash. You can restore it from Trash.")
    return _all_notes_redirect(page=validated_page, sort=validated_sort)


@login_required
@require_GET
def trash(request: HttpRequest) -> HttpResponse:
    trashed_notes = services.list_trashed_notes_with_lifecycle_for_owner(owner=request.user)
    trashed_folders = services.list_trashed_folders_with_lifecycle_for_owner(owner=request.user)
    # Trash is now a tree destination, so this page
    # needs the same tree context Home/All Notes already render (folders,
    # unfiled notes, no current note) so its own copy of the shared tree
    # rail/drawer partials has something to show, with its own Trash row
    # marked active via `current_trash_active` below.
    folders, unfiled_notes = services.notes_grouped_for_tree(owner=request.user)
    return render(
        request,
        "notes/trash.html",
        {
            "trashed_notes": trashed_notes,
            "trashed_folders": trashed_folders,
            "show_empty_trash": bool(trashed_notes or trashed_folders),
            "has_hidden_recoverable_items": services.owner_has_hidden_recoverable_items(
                owner=request.user
            ),
            "folders": folders,
            "unfiled_notes": unfiled_notes,
            "note": None,
            "nav_drawer_available": True,
            "current_trash_active": True,
        },
    )


@login_required
@require_POST
def note_restore(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if note.trashed_at is not None:
        original_folder_id = note.folder_id
        try:
            services.restore_note_from_trash(note=note)
        except services.TrashItemNotRestorableError:
            messages.error(request, RESTORE_NOT_AVAILABLE_MESSAGE)
        else:
            if note.folder_id is not None and note.folder_id != original_folder_id:
                messages.success(
                    request,
                    f'Note restored to "{note.folder.name}" because its original '
                    "folder is still in Trash.",
                )
            else:
                messages.success(request, "Note restored.")
    return redirect("notes:trash")


@login_required
@require_POST
def note_permanent_delete(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if note.trashed_at is not None:
        try:
            services.permanently_delete_note_for_owner(note=note)
        except services.NoteNotEligibleForManualDeleteError:
            messages.error(request, PERMANENT_DELETE_NOT_AVAILABLE_MESSAGE)
        else:
            account_services.record_audit_event(
                AuditEvent.EVENT_NOTE_EMPTIED,
                actor=request.user,
                request=request,
                details={"note_id": note.id, "title": note.title},
            )
            messages.success(request, "Note permanently deleted.")
    return redirect("notes:trash")


@login_required
@require_http_methods(["GET", "POST"])
def trash_empty(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        notes_transitioned, folders_transitioned = services.empty_trash_for_owner(
            owner=request.user
        )
        if notes_transitioned or folders_transitioned:
            account_services.record_audit_event(
                AuditEvent.EVENT_NOTES_EMPTY_TRASH,
                actor=request.user,
                request=request,
                details={
                    "note_count": notes_transitioned,
                    "folder_count": folders_transitioned,
                },
            )
        messages.success(request, "Trash emptied.")
        return redirect("notes:trash")

    notes_count = len(services.list_trashed_notes_for_owner(owner=request.user))
    folders_count = len(services.list_trashed_folders_for_owner(owner=request.user))
    return render(
        request,
        "notes/trash_empty_confirm.html",
        {"notes_count": notes_count, "folders_count": folders_count},
    )


@login_required
@require_GET
def note_download_text(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if not services.note_content_accessible_to_owner(note):
        messages.error(request, NOTE_CONTENT_NOT_AVAILABLE_MESSAGE)
        return redirect("notes:trash")
    safe_title = slugify(note.title) or "note"
    response = HttpResponse(
        documents.derive_export_plain_text(note.body_json),
        content_type="text/plain; charset=utf-8",
    )
    response["Content-Disposition"] = f'attachment; filename="{safe_title}-{note.id}.txt"'
    return response


@login_required
@require_GET
def note_download_markdown(request: HttpRequest, note_id: int) -> HttpResponse:
    note = services.note_for_owner_or_404(note_id=note_id, owner=request.user)
    if not services.note_content_accessible_to_owner(note):
        messages.error(request, NOTE_CONTENT_NOT_AVAILABLE_MESSAGE)
        return redirect("notes:trash")
    # Mirrors `note_print`'s own check: `note_is_supported()` and
    # `MarkdownConversionError` guard against two different things --
    # an unsupported `editor_schema_version` never touches
    # `validate_canonical_document()` at all (it only inspects the
    # `body_json` node tree, not the version field), so both checks are
    # required for full parity with Print's existing behavior.
    if not services.note_is_supported(note):
        messages.error(request, "Unsupported document versions cannot be downloaded as Markdown.")
        return render(request, "notes/unsupported_schema.html", {"note": note}, status=409)
    try:
        markdown_text = note_to_markdown(note.title, note.body_json)
    except MarkdownConversionError:
        messages.error(request, "Unsupported document versions cannot be downloaded as Markdown.")
        return render(request, "notes/unsupported_schema.html", {"note": note}, status=409)
    safe_title = slugify(note.title) or "note"
    response = HttpResponse(markdown_text, content_type="text/markdown; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{safe_title}-{note.id}.md"'
    return response


def _parse_admin_recovery_owner(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _admin_recovery_redirect(*, owner) -> HttpResponse:
    # The one shared destination every Administrator Recovery action
    # redirects through -- always the fixed `notes:admin_recovery` route
    # name, with only this one independently-validated scalar value
    # varying. Never an arbitrary caller-supplied URL. Eligibility
    # (whether this owner still has anything recoverable) is deliberately
    # re-checked by `admin_recovery()` itself on the destination GET, not
    # here -- this helper only guarantees the value is a well-formed
    # integer, never a stale/invalid owner silently dropped mid-redirect.
    validated_owner = _parse_admin_recovery_owner(owner)
    if validated_owner is None:
        return redirect("notes:admin_recovery")
    query = urlencode({"owner": validated_owner})
    return redirect(f"{reverse('notes:admin_recovery')}?{query}")


@require_GET
def admin_recovery(request: HttpRequest) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    owner_options = services.list_administrator_recoverable_owners()
    eligible_owner_ids = {option["id"] for option in owner_options}
    requested_owner_id = _parse_admin_recovery_owner(request.GET.get("owner"))
    selected_owner_id = requested_owner_id if requested_owner_id in eligible_owner_ids else None
    notes = services.sort_administrator_recoverable_items(
        services.list_administrator_recoverable_notes(owner_id=selected_owner_id)
    )
    folders = services.sort_administrator_recoverable_items(
        services.list_administrator_recoverable_folders(owner_id=selected_owner_id)
    )
    return render(
        request,
        "notes/admin_recovery.html",
        {
            "notes": notes,
            "folders": folders,
            "owner_options": owner_options,
            "selected_owner_id": selected_owner_id,
        },
    )


@require_POST
def admin_note_restore(request: HttpRequest, note_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    owner = request.POST.get("owner")
    try:
        services.restore_note_for_administrator(note_id=note_id, actor=request.user)
    except services.TrashItemNotRestorableError:
        messages.error(request, RESTORE_NOT_AVAILABLE_MESSAGE)
    except services.RecoveryDestinationNamingExhaustedError:
        messages.error(request, "Could not find an available recovery destination folder name.")
    else:
        messages.success(request, "Note restored.")
    return _admin_recovery_redirect(owner=owner)


@require_POST
def admin_folder_restore(request: HttpRequest, folder_id: int) -> HttpResponse:
    denied = admin_required(request)
    if denied:
        return denied
    owner = request.POST.get("owner")
    try:
        result = services.restore_folder_for_administrator(folder_id=folder_id, actor=request.user)
    except services.TrashItemNotRestorableError:
        messages.error(request, RESTORE_NOT_AVAILABLE_MESSAGE)
    except services.FolderRestoreNamingExhaustedError:
        messages.error(request, "Could not find an available name for the restored folder.")
    else:
        # The count shown in the pre-POST confirmation
        # dialog is advisory only -- this message always reports the actual
        # count the locked transaction restored.
        restored_count = len(result.restored_note_ids)
        if restored_count == 0:
            messages.success(request, "Folder restored. No trashed notes were restored.")
        elif restored_count == 1:
            messages.success(request, "Folder restored. 1 trashed note was also restored.")
        else:
            messages.success(
                request, f"Folder restored. {restored_count} trashed notes were also restored."
            )
    return _admin_recovery_redirect(owner=owner)
