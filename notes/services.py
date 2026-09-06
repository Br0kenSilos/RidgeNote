from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from accounts import services as account_services
from accounts.models import AuditEvent, User
from django.contrib.postgres.search import SearchHeadline, SearchQuery, SearchRank, SearchVector
from django.db import IntegrityError, connection, transaction
from django.db.models import Case, Count, F, IntegerField, Prefetch, Q, Value, When
from django.db.models.functions import Lower
from django.http import Http404, HttpRequest
from django.utils import timezone

from notes import documents
from notes.models import DEFAULT_TAG_COLOR, TAG_COLOR_VALUES, Folder, Note, Tag

FOLDER_NAME_CONFLICT_CONSTRAINT = "notes_folder_owner_name_ci_uq"
TAG_NAME_CONFLICT_CONSTRAINT = "notes_tag_owner_name_ci_uq"
RECOVERY_MARKER_CONFLICT_CONSTRAINT = "notes_folder_owner_recovery_marker_uq"

# Tag identity, canonicalization, and limits.
MAX_TAG_NAME_LENGTH = 40
MAX_TAGS_PER_NOTE = 20

# The exact, narrow, already-reviewed character class this project uses to
# reject visually dangerous text without banning legitimate international
# Unicode content -- the same set `library-restore.ts`'s
# `sanitizeDisplayFilename()` already strips for backup-filename display.
# ASCII control characters (incl. DEL) plus Unicode bidi/format-control
# characters and the byte-order-mark; nothing else is excluded.
_TAG_NAME_PROHIBITED_CHARACTERS = frozenset(
    [chr(code) for code in range(0x00, 0x20)]  # ASCII controls
    + [chr(0x7F)]  # DEL
    + ["؜", "‎", "‏", "﻿"]  # ALM, LRM, RLM, BOM
    + [chr(code) for code in range(0x202A, 0x202F)]  # LRE-RLO, PDF
    + [chr(code) for code in range(0x2066, 0x206A)]  # LRI-PDI
)

_TAG_NAME_WHITESPACE_RE = re.compile(r"\s+")

# Trash lifecycle boundaries: `trashed_at` is
# the sole authoritative clock for both boundaries. `emptied_at` only records
# an early owner-triggered transition into the administrator-only stage and
# never substitutes for `trashed_at` in these comparisons.
TRASH_VISIBLE_MAX_AGE = timedelta(days=30)
TRASH_RECOVERABLE_MAX_AGE = timedelta(days=90)

# Pinned-first, then most-recently-modified. NOT the tree's own visual
# order -- see `TREE_NOTE_DISPLAY_ORDER` below
# for that. This constant is kept exactly as-is because Home's dashboard
# reuses it by reference via
# `recent_notes_for_owner(pinned_first=True)`, and the note-detail Recent
# switcher's own `-modified_at, -id` default must stay unaffected too.
TREE_NOTE_ORDER: tuple[str, ...] = ("-pinned", "-modified_at", "-id")

# The note tree's own visual order -- pinned notes
# first, then case-insensitive alphabetical by title, then ID descending
# as a deterministic tie-breaker for equal normalized titles. A single
# named source of truth so `notes_grouped_for_tree()` and the post-delete
# adjacent-note lookup below share one definition instead of each risking
# a drifting sort. Deliberately plain `Lower("title")` -- no natural/
# numeric sorting, no punctuation or whitespace normalization, no custom
# collation.
TREE_NOTE_DISPLAY_ORDER: tuple = ("-pinned", Lower("title").asc(), "-id")


class NoteSaveConflictError(ValueError):
    def __init__(self, *, current_version: int, current_modified_at: datetime):
        super().__init__("The note has changed in another session.")
        self.current_version = current_version
        self.current_modified_at = current_modified_at


class UnsupportedSchemaError(ValueError):
    pass


class FolderNameConflictError(ValueError):
    def __init__(self, *, name: str):
        super().__init__(f"A folder named {name!r} already exists.")
        self.name = name


class TrashItemNotRestorableError(ValueError):
    def __init__(self):
        super().__init__("This item is no longer available for self-service restore.")


class NoteNotEligibleForManualDeleteError(ValueError):
    def __init__(self):
        super().__init__("This note is no longer available for self-service permanent delete.")


class TrashedItemMutationError(ValueError):
    def __init__(self):
        super().__init__("This item is in Trash and can no longer be modified.")


class RecoveryDestinationNamingExhaustedError(ValueError):
    def __init__(self, *, owner):
        super().__init__("Could not find an available name for the recovery destination folder.")
        self.owner = owner


class TagNameValidationError(ValueError):
    pass


class TagNameConflictError(ValueError):
    """Raised by `rename_tag()` when the
    normalized new name case-insensitively collides with a *different*
    tag already owned by the same owner. Never triggers a merge --
    the caller must fail safely and let the user pick another name."""

    def __init__(self, *, name: str):
        super().__init__(f"A tag named {name!r} already exists.")
        self.name = name


class TagLimitExceededError(ValueError):
    def __init__(self):
        super().__init__(f"A note can have at most {MAX_TAGS_PER_NOTE} tags.")


class FolderRestoreNamingExhaustedError(ValueError):
    def __init__(self, *, folder_id: int):
        super().__init__("Could not find an available top-level name for the restored folder.")
        self.folder_id = folder_id


def _assert_not_trashed(item: Note | Folder) -> None:
    if item.trashed_at is not None:
        raise TrashedItemMutationError()


def note_for_owner_or_404(*, note_id: int, owner) -> Note:
    try:
        return Note.objects.get(pk=note_id, owner=owner)
    except Note.DoesNotExist as exc:
        raise Http404("Note not found.") from exc


def folder_for_owner_or_404(*, folder_id: int, owner) -> Folder:
    try:
        return Folder.objects.get(pk=folder_id, owner=owner)
    except Folder.DoesNotExist as exc:
        raise Http404("Folder not found.") from exc


def list_folders_for_owner(*, owner):
    return Folder.objects.filter(owner=owner, trashed_at__isnull=True)


def tag_for_owner_or_404(*, tag_id: int, owner) -> Tag:
    try:
        return Tag.objects.get(pk=tag_id, owner=owner)
    except Tag.DoesNotExist as exc:
        raise Http404("Tag not found.") from exc


def list_tags_for_owner(*, owner):
    return Tag.objects.filter(owner=owner)


def _integrity_error_is_folder_name_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
    return constraint_name == FOLDER_NAME_CONFLICT_CONSTRAINT


def _integrity_error_is_tag_name_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
    return constraint_name == TAG_NAME_CONFLICT_CONSTRAINT


def create_folder(*, owner, name: str) -> Folder:
    trimmed_name = name.strip()
    if not trimmed_name:
        raise ValueError("Folder name is required.")

    if Folder.objects.filter(
        owner=owner, name__iexact=trimmed_name, trashed_at__isnull=True
    ).exists():
        raise FolderNameConflictError(name=trimmed_name)

    try:
        with transaction.atomic():
            return Folder.objects.create(owner=owner, name=trimmed_name)
    except IntegrityError as exc:
        if _integrity_error_is_folder_name_conflict(exc):
            raise FolderNameConflictError(name=trimmed_name) from exc
        raise


def rename_folder(*, folder: Folder, name: str) -> Folder:
    _assert_not_trashed(folder)
    trimmed_name = name.strip()
    if not trimmed_name:
        raise ValueError("Folder name is required.")

    if trimmed_name == folder.name:
        return folder

    if (
        Folder.objects.filter(
            owner=folder.owner, name__iexact=trimmed_name, trashed_at__isnull=True
        )
        .exclude(pk=folder.pk)
        .exists()
    ):
        raise FolderNameConflictError(name=trimmed_name)

    try:
        with transaction.atomic():
            folder.name = trimmed_name
            folder.save(update_fields=["name"])
    except IntegrityError as exc:
        if _integrity_error_is_folder_name_conflict(exc):
            raise FolderNameConflictError(name=trimmed_name) from exc
        raise

    folder.refresh_from_db()
    return folder


def normalize_tag_name(raw: str, *, uppercase: bool) -> str:
    """The single authoritative tag-name
    normalization/validation path. Deterministic order: reject
    prohibited characters **first, against the raw, untouched input**
    (never against a trimmed/collapsed value), so a prohibited ASCII
    control or bidi/format character is always rejected outright --
    never silently trimmed away at the edges or collapsed into an
    ordinary space internally. Only once that check has passed does
    trimming and whitespace collapse run, guaranteed at that point to
    operate on a string already free of every prohibited character;
    the whitespace regex still matches (and deliberately still
    collapses) legitimate Unicode whitespace outside the prohibited set
    (e.g. a non-breaking space) exactly like an ordinary space -- that
    breadth is intentional, not an oversight, consistent with allowing
    broad legitimate Unicode elsewhere in a tag name. Uppercase is
    applied only when `uppercase` is True (an explicit parameter, never
    a hardcoded transform, so a future user preference can pass `False`
    without any rewrite here), then length is checked against
    `MAX_TAG_NAME_LENGTH` -- never silently truncated. Raises
    `TagNameValidationError` (a `ValueError` subclass, matching this
    module's existing exception convention) with a safe, user-facing
    message on any failure. Does not itself perform the
    case-insensitive lookup/create -- callers pass the normalized
    result to the existing, unchanged `get_or_create_tag()`."""
    if any(character in _TAG_NAME_PROHIBITED_CHARACTERS for character in raw):
        raise TagNameValidationError("Tag name contains unsupported characters.")

    trimmed = raw.strip()
    collapsed = _TAG_NAME_WHITESPACE_RE.sub(" ", trimmed)
    if not collapsed:
        raise TagNameValidationError("Tag name is required.")

    result = collapsed.upper() if uppercase else collapsed

    if len(result) > MAX_TAG_NAME_LENGTH:
        raise TagNameValidationError(f"Tag name must be {MAX_TAG_NAME_LENGTH} characters or fewer.")

    return result


# Semantic initial tag color behavior. Exact,
# case-normalized, whole-string match only -- deliberately a plain dict,
# never a regex/substring/prefix match (`BLUEPRINT`, `GREENHOUSE`,
# `REDDIT`, `AMBER ALERT`, `PURPLE TEAM`, `TEALIGHT`, `BROWNIE`,
# `ORANGEBOX`, and `YELLOWSTONE` must never match). Values are
# `TAG_COLOR_VALUES` codes, not display labels. `RED`,
# `ORANGE`, `YELLOW`, and `BROWN` are distinct stored colors rather
# than collapsing into `rose`/`amber`/`slate`; `TEAL`/`CYAN` are their own
# family. No separate stored values for `cyan`, `pink`, or `purple` --
# those remain aliases onto `teal`, `rose`, and `violet`.
_SEMANTIC_TAG_COLOR_ALIASES: dict[str, str] = {
    "SLATE": "slate",
    "GRAY": "slate",
    "GREY": "slate",
    "BLUE": "blue",
    "TEAL": "teal",
    "CYAN": "teal",
    "GREEN": "green",
    "YELLOW": "yellow",
    "AMBER": "amber",
    "ORANGE": "orange",
    "RED": "red",
    "ROSE": "rose",
    "PINK": "rose",
    "VIOLET": "violet",
    "PURPLE": "violet",
    "BROWN": "brown",
}


def semantic_tag_color(normalized_name: str) -> str | None:
    """Exact-match lookup only, against a name
    that must already be fully normalized (`normalize_tag_name()`'s own
    output) -- this function never trims, uppercases, or collapses
    whitespace itself, and never duplicates that normalization."""
    return _SEMANTIC_TAG_COLOR_ALIASES.get(normalized_name)


def resolve_tag_color_for_creation(
    explicit_color: str,
    normalized_name: str,
    *,
    semantic_color_enabled: bool = True,
    default_color: str = DEFAULT_TAG_COLOR,
) -> str:
    """The authoritative color-precedence
    resolver for a *new* tag, called before `get_or_create_tag()` --
    never inside it, and never applied to an existing tag, which
    `get_or_create_tag()`'s own unchanged reuse/race-recovery branches
    already return untouched regardless of what color is passed in.
    Precedence: (1) `explicit_color`, when the user actually selected
    one (a blank string means no explicit choice was made -- the
    Add Tag form's own "Default color" placeholder option, not a
    real palette value); (2) a semantic match, only when
    `semantic_color_enabled` is True; (3) `default_color` -- the
    per-user configured default Tag color, passed by every real caller
    as `owner.tag_default_color`. Defaults to the global
    `DEFAULT_TAG_COLOR` (Slate) so any caller that doesn't pass one
    still behaves exactly as if that default applied."""
    if explicit_color:
        return explicit_color
    if semantic_color_enabled:
        matched = semantic_tag_color(normalized_name)
        if matched:
            return matched
    return default_color


def get_or_create_tag(*, owner, name: str, color: str) -> Tag:
    trimmed_name = name.strip()
    if not trimmed_name:
        raise ValueError("Tag name is required.")

    existing_tag = Tag.objects.filter(owner=owner, name__iexact=trimmed_name).first()
    if existing_tag is not None:
        return existing_tag

    try:
        with transaction.atomic():
            return Tag.objects.create(owner=owner, name=trimmed_name, color=color)
    except IntegrityError as exc:
        if _integrity_error_is_tag_name_conflict(exc):
            return Tag.objects.get(owner=owner, name__iexact=trimmed_name)
        raise


def assign_tag_to_note(*, note: Note, tag: Tag) -> None:
    """`MAX_TAGS_PER_NOTE` is enforced here, the
    authoritative service boundary, not merely in a form or in
    JavaScript. A tag already attached to this note is never counted
    against the limit -- reusing/re-adding it is always a no-op via
    `note.tags.add()` regardless, so the count check is skipped entirely
    in that case rather than raising for a call that would not actually
    change anything."""
    _assert_not_trashed(note)
    if not note.tags.filter(pk=tag.pk).exists() and note.tags.count() >= MAX_TAGS_PER_NOTE:
        raise TagLimitExceededError()
    note.tags.add(tag)


# Custom tag autocomplete/typeahead. A generous,
# screen-fitting bound reusing `QUICK_SWITCH_RESULT_LIMIT`'s own precedent
# (`notes/services.py`) rather than inventing a new number -- RidgeNote is
# a private, single-to-few-owner scratchpad with realistically small tag
# vocabularies, so 8 comfortably bounds worst-case rendering without any
# demonstrated need for a larger cap.
TAG_SUGGESTION_RESULT_LIMIT = 8


def search_tags_for_owner(
    *, owner, query: str, exclude_ids: Iterable[int] = (), limit: int = TAG_SUGGESTION_RESULT_LIMIT
) -> list[Tag]:
    """Owner-scoped, case-insensitive tag-name
    search feeding the autocomplete suggestion endpoint. Deliberately
    does **not** run `normalize_tag_name()`'s prohibited-character/
    length validation against `query` -- that validation exists to gate
    what may be *stored*, not what may be *typed while searching*; a
    query need not itself be a valid future tag name to usefully filter
    existing ones. Ranking (exact -> prefix -> substring -> alphabetical
    within each group) is computed in a single query via a `Case`/`When`
    rank annotation, mirroring `search_notes_for_quick_switch()`'s own
    shape (trim, bail early on blank, single owner-scoped queryset,
    explicit ordering, hard slice limit) rather than a new pattern. No
    fuzzy/edit-distance matching, no recently-used prioritization."""
    trimmed_query = query.strip()
    if not trimmed_query:
        return []

    tags = Tag.objects.filter(owner=owner, name__icontains=trimmed_query)
    if exclude_ids:
        tags = tags.exclude(pk__in=exclude_ids)

    tags = tags.annotate(
        _match_rank=Case(
            When(name__iexact=trimmed_query, then=Value(0)),
            When(name__istartswith=trimmed_query, then=Value(1)),
            default=Value(2),
            output_field=IntegerField(),
        )
    ).order_by("_match_rank", Lower("name"))
    return list(tags[:limit])


# Tag Management. Sort keys are a small, closed
# set of validated strings (never a raw client-supplied order expression)
# mapped to explicit `order_by()` arguments below.
TAG_MANAGEMENT_SORTS = ("name_asc", "name_desc", "usage_asc", "usage_desc")
TAG_MANAGEMENT_DEFAULT_SORT = "name_asc"

_TAG_MANAGEMENT_ORDER_BY: dict[str, tuple[Any, ...]] = {
    "name_asc": (Lower("name").asc(),),
    "name_desc": (Lower("name").desc(),),
    # `Lower("name")` as a stable secondary key on both usage sorts so
    # tied usage counts never reorder unpredictably between requests.
    "usage_asc": ("usage_count", Lower("name").asc()),
    "usage_desc": ("-usage_count", Lower("name").asc()),
}


def list_tags_for_owner_with_usage(
    *, owner, query: str = "", sort: str = TAG_MANAGEMENT_DEFAULT_SORT
):
    """The Tag Management list query. Usage count
    is a plain `Count("notes", distinct=True)` with **no lifecycle
    filtering at all** -- active, Trash, and administrator-recoverable
    ("emptied") notes are all the same surviving `Note` row with the
    same intact `tags` relationship, so an unfiltered count already,
    automatically, counts every note the tag is actually attached to; a
    permanently purged note's through-table row is the only thing that
    ever actually disappears. `exclude`/`filter` on `trashed_at`/
    `emptied_at` is deliberately absent, not merely unwritten. A single
    `prefetch_related` fetches each tag's related note IDs in one extra
    query (independent of the aggregate `Count`, no double-counting,
    no N+1) so the view can compute a bulk-delete "unique notes
    affected" figure client-side without a second endpoint."""
    tags = Tag.objects.filter(owner=owner)
    trimmed_query = query.strip()
    if trimmed_query:
        tags = tags.filter(name__icontains=trimmed_query)

    tags = tags.annotate(usage_count=Count("notes", distinct=True)).prefetch_related(
        Prefetch("notes", queryset=Note.objects.only("pk"))
    )
    ordering = _TAG_MANAGEMENT_ORDER_BY.get(
        sort, _TAG_MANAGEMENT_ORDER_BY[TAG_MANAGEMENT_DEFAULT_SORT]
    )
    return list(tags.order_by(*ordering))


def create_tag_for_owner(*, owner, name: str, color: str) -> Tag:
    """Creates a
    persistent, zero-use `Tag` vocabulary object directly from Tag
    manager -- never attaches it to any `Note`. Reuses
    `normalize_tag_name()` and `resolve_tag_color_for_creation()`
    verbatim, the exact same identity/color-precedence rules
    note-detail's own Add Tag path (`note_tag_assign`) already applies,
    so the two entry points can never drift apart. An owner-scoped
    case-insensitive collision fails safely with `TagNameConflictError`
    -- never merges into the existing tag -- mirroring `rename_tag()`'s
    own collision contract, including the same race-safe `IntegrityError`
    mapping for a concurrent create under the same normalized name.

    Normalizes `name` again here even though
    `NoteTagAssignForm.clean_name()` (the only current caller's own
    validation layer) already normalized it once -- deliberately kept,
    not removed, so this function stays safe and authoritative on its
    own if a future caller ever invokes it without going through that
    form first. Both this pass and the form's own pass read the exact
    same `owner.tag_uppercase_enabled`/`owner.tag_semantic_color_enabled`/
    `owner.tag_default_color` preferences, so the two normalizations can
    never disagree with each other -- the second pass is idempotent
    given identical input and identical preference values, never a
    second, independent policy."""
    normalized = normalize_tag_name(name, uppercase=owner.tag_uppercase_enabled)
    if Tag.objects.filter(owner=owner, name__iexact=normalized).exists():
        raise TagNameConflictError(name=normalized)

    resolved_color = resolve_tag_color_for_creation(
        color,
        normalized,
        semantic_color_enabled=owner.tag_semantic_color_enabled,
        default_color=owner.tag_default_color,
    )
    try:
        with transaction.atomic():
            return Tag.objects.create(owner=owner, name=normalized, color=resolved_color)
    except IntegrityError as exc:
        if _integrity_error_is_tag_name_conflict(exc):
            raise TagNameConflictError(name=normalized) from exc
        raise


def rename_tag(*, tag: Tag, new_name: str) -> Tag:
    """A global rename of the `Tag` object itself.
    Reuses `normalize_tag_name()` verbatim -- same prohibited-character/
    trim/collapse/40-char/uppercase rules as tag creation, never
    duplicated here. Renaming to the tag's own current name (including a
    value that only normalizes back to it) is a safe no-op success,
    mirroring `rename_folder()`'s own identical early-return. Colliding
    with a *different* tag owned by the same owner fails safely with
    `TagNameConflictError` -- never merges. Only `Tag.name` changes:
    color and every `Note` relationship are completely untouched, and
    every note already carrying this tag reflects the new name for
    free, simply by referencing the same row.

    Uppercase follows the tag's own owner's
    `tag_uppercase_enabled` preference (`tag.owner`, not the current
    request user -- there is no cross-owner rename path, so these are
    always the same account, but reading it off the tag keeps this
    function's own signature unchanged and self-contained). Renaming is
    authoritative here exactly as creation is: the preference is never
    re-read anywhere else for this operation."""
    normalized = normalize_tag_name(new_name, uppercase=tag.owner.tag_uppercase_enabled)
    if normalized == tag.name:
        return tag

    if Tag.objects.filter(owner=tag.owner, name__iexact=normalized).exclude(pk=tag.pk).exists():
        raise TagNameConflictError(name=normalized)

    try:
        with transaction.atomic():
            tag.name = normalized
            tag.save(update_fields=["name"])
    except IntegrityError as exc:
        if _integrity_error_is_tag_name_conflict(exc):
            raise TagNameConflictError(name=normalized) from exc
        raise

    return tag


def recolor_tag(*, tag: Tag, color: str) -> Tag:
    """A global recolor of the `Tag` object
    itself -- an explicit, deliberate choice, never a re-derivation.
    Never calls `resolve_tag_color_for_creation()`/`semantic_tag_color()`;
    those apply only at new-tag creation. Only `Tag.color` changes: name
    and every `Note` relationship are completely untouched."""
    if color not in TAG_COLOR_VALUES:
        raise ValueError("Unsupported tag color.")
    tag.color = color
    tag.save(update_fields=["color"])
    return tag


def delete_tag(*, tag: Tag) -> None:
    """Deletes the `Tag` row itself. Django only
    ever removes the M2M through-table rows referencing a deleted side
    of a `ManyToManyField` -- the *other* side's rows (here, `Note`) are
    never touched, cascaded to, or otherwise affected. No `Note` is ever
    deleted, trashed, or modified by this call. `transaction.atomic()`
    is used for consistency with this module's established convention,
    not because a single `.delete()` needs it."""
    with transaction.atomic():
        tag.delete()


def bulk_delete_tags(*, owner, tag_ids: Iterable[int]) -> int:
    """Deletes every tag in `tag_ids` that the
    given `owner` actually owns -- resolved via an owner-scoped
    queryset, so any foreign ID is silently excluded rather than
    trusted, raising nothing and mutating nothing outside the caller's
    own data. All resolved tags are deleted in one
    `transaction.atomic()` (all-or-nothing: a mid-batch failure leaves
    every tag in the batch untouched). Returns the number of tags
    actually deleted. No `Note` row is ever touched, matching
    `delete_tag()`'s own guarantee exactly."""
    with transaction.atomic():
        queryset = Tag.objects.filter(owner=owner, pk__in=tag_ids)
        deleted_count = queryset.count()
        queryset.delete()
    return deleted_count


def remove_tag_from_note(*, note: Note, tag: Tag) -> None:
    _assert_not_trashed(note)
    note.tags.remove(tag)


def rename_note(*, note: Note, title: str) -> Note:
    _assert_not_trashed(note)
    trimmed_title = title.strip()
    if not trimmed_title:
        raise ValueError("Title is required.")

    note.title = trimmed_title
    note.save(update_fields=["title"])
    note.refresh_from_db()
    return note


def assign_note_folder(*, note: Note, folder: Folder | None) -> Note:
    # The destination folder must be locked and
    # revalidated inside the same transaction as the note write --
    # trusting the caller's already-loaded `folder` object (or even a
    # freshly form-validated one) leaves a window where the folder is
    # concurrently trashed between validation and this write, letting an
    # active note end up referencing a trashed folder. Destination
    # Folder is locked before the Note, matching `create_note()` and
    # `move_folder_to_trash()` -- the note doesn't yet need to be locked
    # to determine which folder to contend for, and locking the folder
    # first lets a concurrent `move_folder_to_trash()` (which locks the
    # same folder row first) serialize cleanly against this move.
    with transaction.atomic():
        locked_folder = None
        if folder is not None:
            locked_folder = (
                Folder.objects.select_for_update()
                .filter(pk=folder.pk, owner=note.owner, trashed_at__isnull=True)
                .first()
            )
            if locked_folder is None:
                raise TrashedItemMutationError()
        locked_note = Note.objects.select_for_update().get(pk=note.pk, owner=note.owner)
        _assert_not_trashed(locked_note)
        locked_note.folder = locked_folder
        locked_note.save(update_fields=["folder"])
    note.refresh_from_db()
    return note


def set_note_pinned(*, note: Note, pinned: bool) -> Note:
    _assert_not_trashed(note)
    note.pinned = pinned
    note.save(update_fields=["pinned"])
    note.refresh_from_db()
    return note


def _is_visible_and_self_restorable(*, trashed_at, emptied_at, now) -> bool:
    if trashed_at is None or emptied_at is not None:
        return False
    return now - trashed_at <= TRASH_VISIBLE_MAX_AGE


def _is_hidden_but_administrator_recoverable(*, trashed_at, emptied_at, now) -> bool:
    if trashed_at is None:
        return False
    if _is_visible_and_self_restorable(trashed_at=trashed_at, emptied_at=emptied_at, now=now):
        return False
    return now - trashed_at <= TRASH_RECOVERABLE_MAX_AGE


def note_is_visible_and_self_restorable(note: Note) -> bool:
    return _is_visible_and_self_restorable(
        trashed_at=note.trashed_at, emptied_at=note.emptied_at, now=timezone.now()
    )


def note_content_accessible_to_owner(note: Note) -> bool:
    """Whether the owner may retrieve this Note's normal body/content
    through an ordinary content route (print, export, download,
    freshness sync). Distinct from `note_for_owner_or_404()`'s
    ownership-only lookup, and distinct from
    `note_is_visible_and_self_restorable()`'s restore-eligibility
    question (true throughout the 30-day owner-visible Trash window) --
    content access is strictly narrower than restore eligibility:
    Active only. A Note in owner-visible Trash can still be restored by
    its owner, but its content is not ordinarily readable until it is."""
    return note.trashed_at is None


def folder_is_visible_and_self_restorable(folder: Folder) -> bool:
    return _is_visible_and_self_restorable(
        trashed_at=folder.trashed_at, emptied_at=folder.emptied_at, now=timezone.now()
    )


def _visible_trash_filter(*, now: datetime) -> Q:
    cutoff = now - TRASH_VISIBLE_MAX_AGE
    return Q(trashed_at__isnull=False, emptied_at__isnull=True, trashed_at__gte=cutoff)


def _hidden_but_recoverable_filter(*, now: datetime) -> Q:
    visible_cutoff = now - TRASH_VISIBLE_MAX_AGE
    recoverable_cutoff = now - TRASH_RECOVERABLE_MAX_AGE
    return Q(trashed_at__isnull=False, trashed_at__gte=recoverable_cutoff) & (
        Q(emptied_at__isnull=False) | Q(trashed_at__lt=visible_cutoff)
    )


# Notes eligible to be restored *together with* a
# trashed folder -- carrying that folder's own `trashed_via_folder` marker
# (never inferred from `folder_id`/`trashed_at` alone, per the explicit
# "do not restore a note merely because it references the same folder"
# requirement), still trashed, and independently still within the
# requested lifecycle stage. Unlocked -- used both for the advisory count
# shown before a restore POST and, separately, re-evaluated under a real
# lock inside the restore transaction itself.
def _associated_trashed_notes_queryset(*, folder: Folder, now: datetime, for_administrator: bool):
    eligibility_filter = (
        _hidden_but_recoverable_filter(now=now)
        if for_administrator
        else _visible_trash_filter(now=now)
    )
    return Note.objects.filter(owner=folder.owner, folder=folder, trashed_via_folder=folder).filter(
        eligibility_filter
    )


def count_notes_associated_with_trashed_folder(*, folder: Folder, for_administrator: bool) -> int:
    return _associated_trashed_notes_queryset(
        folder=folder, now=timezone.now(), for_administrator=for_administrator
    ).count()


def owner_has_hidden_recoverable_items(*, owner) -> bool:
    now = timezone.now()
    hidden_filter = _hidden_but_recoverable_filter(now=now)
    if Note.objects.filter(owner=owner).filter(hidden_filter).exists():
        return True
    return Folder.objects.filter(owner=owner).filter(hidden_filter).exists()


# Administrator recovery metadata view. These functions
# use an explicit `.values(...)` projection so the query itself never selects
# content-bearing fields (body_json, body_plain_text, tags, pinned) -- not
# merely relying on the template to avoid rendering them.
ADMINISTRATOR_RECOVERY_STAGE_LABEL = "Administrator-only recovery"


def list_administrator_recoverable_notes(*, owner_id: int | None = None) -> list[dict[str, Any]]:
    now = timezone.now()
    hidden_filter = _hidden_but_recoverable_filter(now=now)
    queryset = Note.objects.filter(hidden_filter)
    if owner_id is not None:
        queryset = queryset.filter(owner_id=owner_id)
    rows = queryset.values(
        "id",
        "title",
        "trashed_at",
        "emptied_at",
        "owner_id",
        "owner__username",
        "owner__display_name",
        "folder__name",
    )
    return [
        {
            "item_type": "Note",
            "id": row["id"],
            "owner_id": row["owner_id"],
            "owner_label": row["owner__display_name"] or row["owner__username"],
            "title": row["title"],
            "folder_label": row["folder__name"] or UNFILED_FOLDER_LABEL,
            # Raw, nullable folder name -- distinct from `folder_label`
            # above (kept unchanged for existing callers/tests). The
            # template uses this to distinguish "no folder" from a real
            # folder literally named "Unfiled" when building its
            # "Original folder: ..." wording, the same way Trash's own
            # template checks the real FK rather than comparing label text.
            "folder_name": row["folder__name"],
            "trashed_at": row["trashed_at"],
            "emptied_at": row["emptied_at"],
            "owner_visible_expiration_at": row["trashed_at"] + TRASH_VISIBLE_MAX_AGE,
            "final_purge_at": row["trashed_at"] + TRASH_RECOVERABLE_MAX_AGE,
            "stage_label": ADMINISTRATOR_RECOVERY_STAGE_LABEL,
        }
        for row in rows
    ]


def list_administrator_recoverable_folders(*, owner_id: int | None = None) -> list[dict[str, Any]]:
    now = timezone.now()
    hidden_filter = _hidden_but_recoverable_filter(now=now)
    queryset = Folder.objects.filter(hidden_filter)
    if owner_id is not None:
        queryset = queryset.filter(owner_id=owner_id)
    folder_rows = list(
        queryset.values(
            "id",
            "name",
            "trashed_at",
            "emptied_at",
            "owner_id",
            "owner__username",
            "owner__display_name",
        )
    )
    folder_ids = [row["id"] for row in folder_rows]
    note_counts = {
        entry["folder_id"]: entry["note_count"]
        for entry in Note.objects.filter(folder_id__in=folder_ids)
        .filter(hidden_filter)
        .values("folder_id")
        .annotate(note_count=Count("id"))
    }
    # Deliberately a *separate* count from
    # `note_count` above -- that one is purely descriptive ("how many
    # currently-recoverable notes happen to reference this folder right
    # now"), unrelated to trash-cascade association. This one drives the
    # restore confirmation's advisory count and
    # is scoped to the `trashed_via_folder` marker only, per the same
    # "never restore a note merely because it references the folder" rule
    # `_associated_trashed_notes_queryset()` already enforces.
    associated_restorable_note_counts = {
        entry["trashed_via_folder"]: entry["note_count"]
        for entry in Note.objects.filter(trashed_via_folder_id__in=folder_ids)
        .filter(hidden_filter)
        .values("trashed_via_folder")
        .annotate(note_count=Count("id"))
    }
    return [
        {
            "item_type": "Folder",
            "id": row["id"],
            "owner_id": row["owner_id"],
            "owner_label": row["owner__display_name"] or row["owner__username"],
            "title": row["name"],
            "note_count": note_counts.get(row["id"], 0),
            "associated_restorable_note_count": associated_restorable_note_counts.get(row["id"], 0),
            "trashed_at": row["trashed_at"],
            "emptied_at": row["emptied_at"],
            "owner_visible_expiration_at": row["trashed_at"] + TRASH_VISIBLE_MAX_AGE,
            "final_purge_at": row["trashed_at"] + TRASH_RECOVERABLE_MAX_AGE,
            "stage_label": ADMINISTRATOR_RECOVERY_STAGE_LABEL,
        }
        for row in folder_rows
    ]


def _recoverable_item_sort_key(item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item["final_purge_at"],
        item["owner_label"].lower(),
        item["item_type"],
        item["title"].lower(),
    )


def sort_administrator_recoverable_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Deterministic ordering shared by every Administrator Recovery list
    view -- final purge soonest first, then owner, then title -- so an
    owner-filtered list preserves exactly the same tie-break rules as the
    unfiltered combined list."""
    return sorted(items, key=_recoverable_item_sort_key)


# Owner filtering. The dropdown label deliberately
# differs from `owner_label` (`display_name or username`, used for row
# metadata): a bare display name is ambiguous across owners sharing one
# (display names are not unique), so the option label disambiguates with
# the always-unique username whenever it would otherwise be dropped.
def _administrator_recovery_owner_option_label(*, username: str, display_name: str) -> str:
    if display_name and display_name != username:
        return f"{display_name} ({username})"
    return username


def list_administrator_recoverable_owners() -> list[dict[str, Any]]:
    """Owners eligible for the Administrator Recovery owner filter --
    exactly those with at least one currently administrator-recoverable
    note or folder, never the full user table. Two small, bounded
    queries (one per model, each already joining owner identity columns
    the same way the item queries themselves do) rather than a query
    that scales with item count."""
    now = timezone.now()
    hidden_filter = _hidden_but_recoverable_filter(now=now)
    note_owner_rows = (
        Note.objects.filter(hidden_filter)
        .values("owner_id", "owner__username", "owner__display_name")
        .distinct()
    )
    folder_owner_rows = (
        Folder.objects.filter(hidden_filter)
        .values("owner_id", "owner__username", "owner__display_name")
        .distinct()
    )
    owners_by_id: dict[int, dict[str, Any]] = {}
    for row in list(note_owner_rows) + list(folder_owner_rows):
        owners_by_id[row["owner_id"]] = {
            "id": row["owner_id"],
            "label": _administrator_recovery_owner_option_label(
                username=row["owner__username"], display_name=row["owner__display_name"]
            ),
        }
    options = list(owners_by_id.values())
    options.sort(key=lambda option: (option["label"].lower(), option["id"]))
    return options


# Administrator note restore. Destination category
# labels recorded in the audit event and returned to callers.
DESTINATION_CATEGORY_ORIGINAL_FOLDER = "original_folder"
DESTINATION_CATEGORY_RECOVERED_ITEMS = "recovered_items"

RECOVERY_FOLDER_NAME_BASE = "Recovered Items"
# Generous and explicit -- ordinary use
# cannot realistically reach this bound; exhaustion raises a narrow,
# dedicated exception rather than looping unboundedly.
RECOVERY_FOLDER_NAMING_MAX_ATTEMPTS = 500


@dataclass(frozen=True)
class AdministratorNoteRestoreResult:
    note_id: int
    owner_id: int
    destination_category: str
    destination_folder_id: int
    fallback_used: bool


def _integrity_error_is_recovery_marker_conflict(exc: IntegrityError) -> bool:
    constraint_name = getattr(getattr(exc.__cause__, "diag", None), "constraint_name", None)
    return constraint_name == RECOVERY_MARKER_CONFLICT_CONSTRAINT


def _recovery_folder_name_candidate(*, attempt: int) -> str:
    return RECOVERY_FOLDER_NAME_BASE if attempt == 1 else f"{RECOVERY_FOLDER_NAME_BASE} ({attempt})"


def _get_or_create_recovery_folder(*, owner) -> Folder:
    existing = (
        Folder.objects.select_for_update()
        .filter(owner=owner, is_recovery_folder=True, trashed_at__isnull=True)
        .first()
    )
    if existing is not None:
        return existing

    for attempt in range(1, RECOVERY_FOLDER_NAMING_MAX_ATTEMPTS + 1):
        candidate_name = _recovery_folder_name_candidate(attempt=attempt)
        if Folder.objects.filter(
            owner=owner, name__iexact=candidate_name, trashed_at__isnull=True
        ).exists():
            continue
        try:
            with transaction.atomic():
                return Folder.objects.create(
                    owner=owner, name=candidate_name, is_recovery_folder=True
                )
        except IntegrityError as exc:
            if _integrity_error_is_recovery_marker_conflict(exc):
                winner = (
                    Folder.objects.select_for_update()
                    .filter(owner=owner, is_recovery_folder=True, trashed_at__isnull=True)
                    .first()
                )
                if winner is not None:
                    return winner
                continue
            if _integrity_error_is_folder_name_conflict(exc):
                continue
            raise

    raise RecoveryDestinationNamingExhaustedError(owner=owner)


def _resolve_recovery_destination(*, note: Note) -> tuple[Folder, str, bool]:
    if note.folder_id is not None:
        original = (
            Folder.objects.select_for_update()
            .filter(pk=note.folder_id, owner=note.owner, trashed_at__isnull=True)
            .first()
        )
        if original is not None:
            return original, DESTINATION_CATEGORY_ORIGINAL_FOLDER, False

    destination = _get_or_create_recovery_folder(owner=note.owner)
    return destination, DESTINATION_CATEGORY_RECOVERED_ITEMS, True


def restore_note_for_administrator(*, note_id: int, actor: User) -> AdministratorNoteRestoreResult:
    with transaction.atomic():
        try:
            note = Note.objects.select_for_update().get(pk=note_id)
        except Note.DoesNotExist as exc:
            raise TrashItemNotRestorableError() from exc

        if not _is_hidden_but_administrator_recoverable(
            trashed_at=note.trashed_at, emptied_at=note.emptied_at, now=timezone.now()
        ):
            raise TrashItemNotRestorableError()

        original_trashed_at = note.trashed_at
        emptied_at_was_cleared = note.emptied_at is not None

        destination, destination_category, fallback_used = _resolve_recovery_destination(note=note)

        note.folder = destination
        note.trashed_at = None
        note.emptied_at = None
        # Individual restore ends this note's
        # association with whatever folder-trash operation swept it in, if
        # any -- see the identical comment on `restore_note_from_trash()`.
        note.trashed_via_folder = None
        note.save(update_fields=["folder", "trashed_at", "emptied_at", "trashed_via_folder"])

        account_services.record_audit_event(
            AuditEvent.EVENT_ADMINISTRATOR_NOTE_RESTORE,
            actor=actor,
            target_user=note.owner,
            details={
                "note_id": note.id,
                "title_snapshot": note.title,
                "original_trashed_at": original_trashed_at.isoformat(),
                "emptied_at_cleared": emptied_at_was_cleared,
                "destination_category": destination_category,
                "destination_folder_id": destination.id,
                "fallback_used": fallback_used,
            },
        )

    return AdministratorNoteRestoreResult(
        note_id=note.id,
        owner_id=note.owner_id,
        destination_category=destination_category,
        destination_folder_id=destination.id,
        fallback_used=fallback_used,
    )


# Administrator folder restore. Folder-row only: this
# service never queries or writes any Note row. Contained notes keep their
# own folder_id/trashed_at/emptied_at exactly as they were, regardless of
# their individual lifecycle stage.
ADMINISTRATOR_FOLDER_RESTORE_NAMING_MAX_ATTEMPTS = 500


@dataclass(frozen=True)
class AdministratorFolderRestoreResult:
    folder_id: int
    owner_id: int
    restored_name: str
    fallback_naming_used: bool
    restored_note_ids: list[int]


def _administrator_folder_restore_name_candidate(*, original_name: str, attempt: int) -> str:
    if attempt == 1:
        return original_name
    suffix = " (Restored)" if attempt == 2 else f" (Restored {attempt - 1})"
    max_length = Folder._meta.get_field("name").max_length
    available = max(max_length - len(suffix), 0)
    truncated_original_name = (
        original_name if len(original_name) <= available else original_name[:available]
    )
    return f"{truncated_original_name}{suffix}"


def _resolve_administrator_folder_restore_name(*, folder: Folder) -> tuple[str, bool]:
    original_name = folder.name
    for attempt in range(1, ADMINISTRATOR_FOLDER_RESTORE_NAMING_MAX_ATTEMPTS + 1):
        candidate_name = _administrator_folder_restore_name_candidate(
            original_name=original_name, attempt=attempt
        )
        if (
            Folder.objects.filter(
                owner=folder.owner, name__iexact=candidate_name, trashed_at__isnull=True
            )
            .exclude(pk=folder.pk)
            .exists()
        ):
            continue
        try:
            with transaction.atomic():
                folder.name = candidate_name
                folder.trashed_at = None
                folder.emptied_at = None
                folder.save(update_fields=["name", "trashed_at", "emptied_at"])
        except IntegrityError as exc:
            if _integrity_error_is_folder_name_conflict(exc):
                continue
            raise
        else:
            return candidate_name, attempt > 1

    raise FolderRestoreNamingExhaustedError(folder_id=folder.pk)


def restore_folder_for_administrator(
    *, folder_id: int, actor: User
) -> AdministratorFolderRestoreResult:
    with transaction.atomic():
        try:
            folder = Folder.objects.select_for_update().get(pk=folder_id)
        except Folder.DoesNotExist as exc:
            raise TrashItemNotRestorableError() from exc

        if not _is_hidden_but_administrator_recoverable(
            trashed_at=folder.trashed_at, emptied_at=folder.emptied_at, now=timezone.now()
        ):
            raise TrashItemNotRestorableError()

        original_name = folder.name
        original_trashed_at = folder.trashed_at
        emptied_at_was_cleared = folder.emptied_at is not None

        restored_name, fallback_naming_used = _resolve_administrator_folder_restore_name(
            folder=folder
        )

        # The folder's own `trashed_at`/`emptied_at`
        # are already cleared at this point (inside
        # `_resolve_administrator_folder_restore_name()`'s own successful
        # save), so a restored note's folder is valid immediately -- still
        # the same outer transaction, so folder and notes restore or roll
        # back together.
        restored_note_ids = _restore_notes_associated_with_folder(
            folder=folder, for_administrator=True
        )

        account_services.record_audit_event(
            AuditEvent.EVENT_ADMINISTRATOR_FOLDER_RESTORE,
            actor=actor,
            target_user=folder.owner,
            details={
                "folder_id": folder.id,
                "original_name": original_name,
                "restored_name": restored_name,
                "original_trashed_at": original_trashed_at.isoformat(),
                "emptied_at_cleared": emptied_at_was_cleared,
                "fallback_naming_used": fallback_naming_used,
                "restored_note_ids": restored_note_ids,
                "restored_note_count": len(restored_note_ids),
            },
        )

    return AdministratorFolderRestoreResult(
        folder_id=folder.id,
        owner_id=folder.owner_id,
        restored_name=restored_name,
        fallback_naming_used=fallback_naming_used,
        restored_note_ids=restored_note_ids,
    )


def _is_untouched_placeholder(note: Note) -> bool:
    """Whether ``note`` is still exactly as `create_note()` left it --
    never edited, tagged, pinned, or otherwise engaged with. Content-based,
    not based on `version`: a save with byte-identical content still bumps
    `version` (see `save_note()`), so `version == 1` is not a safe signal
    -- only comparing current field values against their canonical
    just-created form is. Folder assignment at creation is deliberately
    not checked here -- "New note in this folder" already sets `folder`
    as part of ordinary creation, not as evidence of editing."""
    if note.pinned:
        return False
    if note.tags.exists():
        return False
    if note.title != documents.generated_title_for_timestamp(note.created_at):
        return False
    if note.body_json != documents.canonical_empty_document():
        return False
    return True


@transaction.atomic
def move_note_to_trash(*, note: Note, request: HttpRequest | None = None) -> Note | None:
    """Move ``note`` to Trash, unless it is still an untouched, disposable
    placeholder -- in which case it is hard-deleted immediately instead,
    never through Trash, with a required audit event written in the same
    transaction (a failed audit write rolls back the delete too, since the
    whole function is atomic). Returns the (now-trashed) `Note` in the
    normal case, or `None` when the placeholder was discarded instead --
    callers must branch their user-facing message on this return value.

    Safety: the eligibility check runs against a freshly locked, reloaded
    row (`select_for_update()`), never the possibly-stale in-memory state
    ``note`` had before this call, so a concurrent edit from another tab/
    request cannot race this decision. The caller's own ``note`` object is
    refreshed in place from that locked row (matching this function's
    long-standing in-place-mutation contract), so any other reference the
    caller still holds reflects the real outcome too. When eligibility is
    uncertain for any reason, this must fall through to ordinary Trash
    behavior, never the reverse."""
    locked = Note.objects.select_for_update().get(pk=note.pk, owner=note.owner)
    note.__dict__.update(locked.__dict__)

    if note.trashed_at is not None:
        return note

    if _is_untouched_placeholder(note):
        account_services.record_audit_event(
            AuditEvent.EVENT_NOTE_PLACEHOLDER_DISCARDED,
            actor=note.owner,
            request=request,
            details={
                "note_id": note.id,
                "title": note.title,
                "created_at": note.created_at.isoformat(),
            },
        )
        note.delete()
        return None

    # This is always an *independent* trash action
    # (a already-trashed note returns early above; `move_folder_to_trash()`
    # sets `trashed_via_folder` itself, via a bulk `.update()`, and never
    # calls this function) -- so any marker is explicitly cleared here too,
    # defensively, rather than assumed absent.
    note.trashed_at = timezone.now()
    note.trashed_via_folder = None
    note.save(update_fields=["trashed_at", "trashed_via_folder"])
    note.refresh_from_db()
    return note


def restore_note_from_trash(*, note: Note) -> Note:
    with transaction.atomic():
        locked_note = Note.objects.select_for_update().get(pk=note.pk, owner=note.owner)
        if not note_is_visible_and_self_restorable(locked_note):
            raise TrashItemNotRestorableError()

        # An originally unfiled note has no original
        # folder to be unsafe, so destination resolution is skipped
        # entirely; it stays unfiled. A filed note keeps its original
        # folder only if that folder is still active; otherwise it falls
        # back to the owner's marker-based Recovered Items folder, so no
        # newly restored active note is ever left referencing a trashed
        # folder.
        if locked_note.folder_id is not None:
            destination, _category, _fallback_used = _resolve_recovery_destination(note=locked_note)
            locked_note.folder = destination

        locked_note.trashed_at = None
        locked_note.emptied_at = None
        # Individual restore ends this note's
        # association with whatever folder-trash operation swept it in, if
        # any -- it is no longer a candidate for that folder's own later
        # grouped restore.
        locked_note.trashed_via_folder = None
        locked_note.save(update_fields=["folder", "trashed_at", "emptied_at", "trashed_via_folder"])

    note.refresh_from_db()
    return note


def permanently_delete_note_for_owner(*, note: Note) -> Note:
    """Owner-facing "Delete permanently": ends self-service Trash access for
    one Note immediately by reusing the exact `emptied_at` transition
    `empty_trash_for_owner()` already performs in bulk, scoped here to one
    row under `select_for_update()`. Introduces no new lifecycle state --
    the Note becomes administrator-recoverable exactly as it would after
    Empty Trash, and remains purge-eligible only at the existing
    `trashed_at`-based horizon (`notes/purge.py`'s `_is_purge_eligible()`
    never reads `emptied_at`), never sooner. `trashed_at`, `folder`, and
    `tags` are left untouched. Eligibility is revalidated under the lock
    against a freshly reloaded row via the same
    `note_is_visible_and_self_restorable()` predicate restore already
    uses, so a concurrent Restore/Empty Trash/final-purge cannot race this
    decision."""
    with transaction.atomic():
        locked_note = Note.objects.select_for_update().get(pk=note.pk, owner=note.owner)
        if not note_is_visible_and_self_restorable(locked_note):
            raise NoteNotEligibleForManualDeleteError()

        locked_note.emptied_at = timezone.now()
        locked_note.save(update_fields=["emptied_at"])

    note.refresh_from_db()
    return note


def list_trashed_notes_for_owner(*, owner) -> list[Note]:
    notes = (
        Note.objects.filter(owner=owner)
        .filter(_visible_trash_filter(now=timezone.now()))
        .select_related("folder")
    )
    return list(notes.order_by("-trashed_at", "-id"))


# Owner-visible Trash lifecycle presentation. A narrow,
# presentation-only projection reusing the exact deadline arithmetic already
# proven by `list_administrator_recoverable_notes`/`_folders` above, applied
# to the owner-facing context instead. Every row here is, by construction,
# still inside `_visible_trash_filter`'s window (owner-visible and
# self-restorable) -- items past that boundary never reach this projection,
# so no administrator-stage field or branching belongs here.
@dataclass(frozen=True)
class TrashedNoteRow:
    note: Note
    moved_to_trash_at: datetime
    owner_visible_until: datetime
    final_purge_at: datetime


@dataclass(frozen=True)
class TrashedFolderRow:
    folder: Folder
    moved_to_trash_at: datetime
    owner_visible_until: datetime
    final_purge_at: datetime
    # The advisory count shown before a restore --
    # owner-eligible notes carrying this folder's own trash-cascade marker.
    associated_note_count: int


def _trash_lifecycle_deadlines(*, trashed_at: datetime) -> tuple[datetime, datetime]:
    return trashed_at + TRASH_VISIBLE_MAX_AGE, trashed_at + TRASH_RECOVERABLE_MAX_AGE


def list_trashed_notes_with_lifecycle_for_owner(*, owner) -> list[TrashedNoteRow]:
    rows = []
    for note in list_trashed_notes_for_owner(owner=owner):
        owner_visible_until, final_purge_at = _trash_lifecycle_deadlines(trashed_at=note.trashed_at)
        rows.append(
            TrashedNoteRow(
                note=note,
                moved_to_trash_at=note.trashed_at,
                owner_visible_until=owner_visible_until,
                final_purge_at=final_purge_at,
            )
        )
    return rows


def list_trashed_folders_with_lifecycle_for_owner(*, owner) -> list[TrashedFolderRow]:
    folders = list_trashed_folders_for_owner(owner=owner)
    folder_ids = [folder.id for folder in folders]
    # Batched in one query rather than per row --
    # the trash page's own regression test asserts a flat query count
    # regardless of row count.
    associated_note_counts = {
        entry["trashed_via_folder"]: entry["note_count"]
        for entry in Note.objects.filter(trashed_via_folder_id__in=folder_ids)
        .filter(_visible_trash_filter(now=timezone.now()))
        .values("trashed_via_folder")
        .annotate(note_count=Count("id"))
    }
    rows = []
    for folder in folders:
        owner_visible_until, final_purge_at = _trash_lifecycle_deadlines(
            trashed_at=folder.trashed_at
        )
        rows.append(
            TrashedFolderRow(
                folder=folder,
                moved_to_trash_at=folder.trashed_at,
                owner_visible_until=owner_visible_until,
                final_purge_at=final_purge_at,
                associated_note_count=associated_note_counts.get(folder.id, 0),
            )
        )
    return rows


@transaction.atomic
def _trash_notes_associated_with_folder(*, folder: Folder, trashed_at) -> None:
    # `trashed_via_folder` is set in the same
    # `.update()` as `trashed_at`, on the same already-`trashed_at__isnull=
    # True` filter -- so it only ever marks notes newly swept into Trash by
    # *this* cascade, never a note already trashed before it (which this
    # filter already excluded, unchanged). This marker, not the shared
    # timestamp, is what a later folder restore uses to find "the notes
    # that belong with it."
    Note.objects.filter(owner=folder.owner, folder=folder, trashed_at__isnull=True).update(
        trashed_at=trashed_at, trashed_via_folder=folder
    )


def move_folder_to_trash(*, folder: Folder) -> Folder:
    # The folder's own lifecycle update and its
    # associated-note cascade must commit together or not at all --
    # two separate, independently autocommitting
    # statements would let a failure between them leave a trashed folder
    # with untouched active notes even without any concurrency race.
    # Locking the folder first (Folder -> associated Notes, matching
    # `restore_folder_from_trash()`) also lets this serialize cleanly
    # against a concurrent `assign_note_folder()`/`create_note()`, which
    # lock the same folder row first: whichever gets here first
    # determines the outcome, and the loser observes a fully committed,
    # consistent result rather than a half-applied one.
    trashed_at = timezone.now()
    with transaction.atomic():
        locked_folder = Folder.objects.select_for_update().get(pk=folder.pk, owner=folder.owner)
        locked_folder.trashed_at = trashed_at
        locked_folder.is_recovery_folder = False
        locked_folder.save(update_fields=["trashed_at", "is_recovery_folder"])
        _trash_notes_associated_with_folder(folder=locked_folder, trashed_at=trashed_at)
    folder.refresh_from_db()
    return folder


def _restore_notes_associated_with_folder(*, folder: Folder, for_administrator: bool) -> list[int]:
    """Restores every note carrying `folder`'s own `trashed_via_folder`
    marker that is still independently eligible under the caller's own
    lifecycle stage (owner-visible, or administrator-recoverable), and
    detaches the marker from *every* marked note regardless of outcome --
    once a folder is restored, its association with that specific trash
    operation is over either way (a note left behind, ineligible right
    now, is not silently re-attached to a folder that is no longer
    trashed; it remains individually restorable on its own merits, per
    the existing per-note restore paths, which already clear this same
    marker). Must be called from inside the same `transaction.atomic()`
    block the folder's own row is already locked in, after the folder's
    own `trashed_at`/`emptied_at` have already been cleared, so a
    restored note's folder is valid (active) by the time it is saved, and
    so this batch lock participates in the same all-or-nothing unit of
    work as the folder itself. Never restores a note merely because it
    currently references `folder` -- only ones carrying its marker."""
    now = timezone.now()
    marked_notes = list(
        Note.objects.select_for_update().filter(
            owner=folder.owner,
            folder=folder,
            trashed_via_folder=folder,
            trashed_at__isnull=False,
        )
    )
    restored_note_ids: list[int] = []
    for note in marked_notes:
        is_eligible = (
            _is_hidden_but_administrator_recoverable(
                trashed_at=note.trashed_at, emptied_at=note.emptied_at, now=now
            )
            if for_administrator
            else _is_visible_and_self_restorable(
                trashed_at=note.trashed_at, emptied_at=note.emptied_at, now=now
            )
        )
        if is_eligible:
            note.trashed_at = None
            note.emptied_at = None
            note.trashed_via_folder = None
            note.save(update_fields=["trashed_at", "emptied_at", "trashed_via_folder"])
            restored_note_ids.append(note.id)
        else:
            note.trashed_via_folder = None
            note.save(update_fields=["trashed_via_folder"])
    return restored_note_ids


@dataclass(frozen=True)
class OwnerFolderRestoreResult:
    folder: Folder
    restored_note_ids: list[int]


def restore_folder_from_trash(*, folder: Folder) -> OwnerFolderRestoreResult:
    try:
        with transaction.atomic():
            locked_folder = Folder.objects.select_for_update().get(pk=folder.pk, owner=folder.owner)
            if not folder_is_visible_and_self_restorable(locked_folder):
                raise TrashItemNotRestorableError()

            if (
                Folder.objects.filter(
                    owner=locked_folder.owner,
                    name__iexact=locked_folder.name,
                    trashed_at__isnull=True,
                )
                .exclude(pk=locked_folder.pk)
                .exists()
            ):
                raise FolderNameConflictError(name=locked_folder.name)

            locked_folder.trashed_at = None
            locked_folder.save(update_fields=["trashed_at"])

            restored_note_ids = _restore_notes_associated_with_folder(
                folder=locked_folder, for_administrator=False
            )
    except IntegrityError as exc:
        if _integrity_error_is_folder_name_conflict(exc):
            raise FolderNameConflictError(name=folder.name) from exc
        raise

    folder.refresh_from_db()
    return OwnerFolderRestoreResult(folder=folder, restored_note_ids=restored_note_ids)


def list_trashed_folders_for_owner(*, owner) -> list[Folder]:
    folders = Folder.objects.filter(owner=owner).filter(_visible_trash_filter(now=timezone.now()))
    return list(folders.order_by("-trashed_at", "-id"))


@transaction.atomic
def empty_trash_for_owner(*, owner) -> tuple[int, int]:
    now = timezone.now()
    eligible = _visible_trash_filter(now=now)
    notes_transitioned = Note.objects.filter(owner=owner).filter(eligible).update(emptied_at=now)
    folders_transitioned = (
        Folder.objects.filter(owner=owner).filter(eligible).update(emptied_at=now)
    )
    return notes_transitioned, folders_transitioned


RECENT_NOTES_LIMIT = 8


def recent_notes_for_owner(
    *,
    owner,
    exclude_note_id: int | None = None,
    limit: int = RECENT_NOTES_LIMIT,
    with_tags: bool = False,
    pinned_first: bool = False,
) -> list[Note]:
    # `select_related("folder")` is here for Home's
    # dashboard, which renders each result's current folder location --
    # without it, that would cost one extra query per displayed note.
    # Harmless for the note-detail popover caller, which
    # doesn't use the relation.
    #
    # `limit` defaults to `RECENT_NOTES_LIMIT` (the note-detail popover's
    # own, independent cap) -- Home's dashboard passes its own
    # explicit `limit=10` rather than this
    # default ever changing, so the two callers never silently share one
    # cap.
    #
    # `with_tags` defaults off: only Home's dashboard
    # renders tag chips here, so the note-detail popover caller
    # doesn't pay for a `tags` prefetch it never uses.
    #
    # `pinned_first` defaults off, preserving this function's original
    # `-modified_at, -id` order for its existing caller (the note-detail
    # Recent-notes switcher, which must keep no pin priority). Home's
    # dashboard alone opts in with `pinned_first=True`
    # to reuse `TREE_NOTE_ORDER` -- the exact same `-pinned, -modified_at,
    # -id` formula already shipped for the tree and for All Notes' own
    # `pinned_new` default -- rather than inventing a second variation of
    # the same ordering rule.
    notes = Note.objects.filter(owner=owner, trashed_at__isnull=True).select_related("folder")
    if with_tags:
        notes = notes.prefetch_related("tags")
    if exclude_note_id is not None:
        notes = notes.exclude(pk=exclude_note_id)
    order_by = TREE_NOTE_ORDER if pinned_first else ("-modified_at", "-id")
    return list(notes.order_by(*order_by)[:limit])


def all_active_notes_for_owner(*, owner, order_by: tuple):
    # The All Notes view's own query -- owner-scoped,
    # active notes only, `select_related("folder")` and
    # `prefetch_related("tags")` so rendering folder location and tag
    # chips for a full page of results costs exactly one extra query
    # each, not one per row. `order_by` is the caller's already-resolved
    # `_ALL_NOTES_SORT_ORDER_BY[sort]` tuple -- this function has no
    # sort-name knowledge of its own, keeping the sort vocabulary owned
    # by `notes/views.py` alone. Returns a lazy queryset (not `list(...)`)
    # so the caller can paginate it without evaluating the full result
    # set first.
    return (
        Note.objects.filter(owner=owner, trashed_at__isnull=True)
        .select_related("folder")
        .prefetch_related("tags")
        .order_by(*order_by)
    )


QUICK_SWITCH_RESULT_LIMIT = 8
UNFILED_FOLDER_LABEL = "Unfiled"


def search_notes_for_quick_switch(
    *, owner, query: str, exclude_note_id: int | None = None
) -> list[Note]:
    trimmed_query = query.strip()
    if not trimmed_query:
        return []

    notes = Note.objects.filter(owner=owner, trashed_at__isnull=True).select_related("folder")
    if exclude_note_id is not None:
        notes = notes.exclude(pk=exclude_note_id)

    match_q = Q(title__icontains=trimmed_query) | Q(folder__name__icontains=trimmed_query)
    if trimmed_query.lower() in UNFILED_FOLDER_LABEL.lower():
        match_q |= Q(folder__isnull=True)

    notes = notes.filter(match_q)
    return list(notes.order_by("-modified_at", "-id")[:QUICK_SWITCH_RESULT_LIMIT])


GLOBAL_SEARCH_RESULT_LIMIT = 10

# A conservative, documented bound on search-box input,
# enforced in pure Python before any database work -- appropriate for a small
# private scratchpad, not tied to any model field's own max_length.
GLOBAL_SEARCH_MAX_QUERY_LENGTH = 200

# Sentinel delimiters passed to PostgreSQL's own `ts_headline` (via Django's
# `SearchHeadline`) so its output can be parsed back into the existing safe
# `{"text": ..., "highlight": bool}` structured-segment shape. Control
# characters, chosen specifically because they cannot occur in ordinary note
# text and are never rendered -- they are consumed entirely by
# `build_segments_from_headline()` below and never reach a template or the
# client.
_HEADLINE_START_SEL = "\x01"
_HEADLINE_STOP_SEL = "\x02"
_HEADLINE_FRAGMENT_DELIMITER = " … "  # visible separator between body fragments

_BODY_HEADLINE_MAX_FRAGMENTS = 2
_BODY_HEADLINE_MAX_WORDS = 15
_BODY_HEADLINE_MIN_WORDS = 5


def _query_has_lexemes(trimmed_query: str) -> bool:
    """Whether PostgreSQL's own `websearch_to_tsquery` reduces ``trimmed_query``
    to at least one lexeme. A stop-word-only or punctuation-only query parses
    to an empty tsquery -- PostgreSQL is the sole source of truth for this
    (English stop-word/parsing rules are never reimplemented in Python). One
    cheap scalar round trip; no `notes_note` table access."""
    with connection.cursor() as cursor:
        cursor.execute("SELECT websearch_to_tsquery('english', %s) != ''::tsquery", [trimmed_query])
        return bool(cursor.fetchone()[0])


def build_segments_from_headline(headline: str) -> list[dict[str, Any]]:
    """Parse a `SearchHeadline` string delimited with `_HEADLINE_START_SEL`/
    `_HEADLINE_STOP_SEL` into the safe `{"text": ..., "highlight": bool}`
    segment shape -- shared by both title and body headlines, so the parsing
    logic exists exactly once. This is the only place PostgreSQL's own
    headline markup is ever inspected -- the sentinels are fully consumed
    here and never appear in the return value, so no raw PostgreSQL markup
    and no HTML of any kind reaches the view, template, or client."""
    segments: list[dict[str, Any]] = []
    remaining = headline
    while remaining:
        start_idx = remaining.find(_HEADLINE_START_SEL)
        if start_idx == -1:
            segments.append({"text": remaining, "highlight": False})
            break
        if start_idx > 0:
            segments.append({"text": remaining[:start_idx], "highlight": False})
        remaining = remaining[start_idx + len(_HEADLINE_START_SEL) :]
        stop_idx = remaining.find(_HEADLINE_STOP_SEL)
        if stop_idx == -1:
            # Defensive only -- `ts_headline` always closes what it opens.
            # Never treat an unterminated span as a match.
            segments.append({"text": remaining, "highlight": False})
            break
        segments.append({"text": remaining[:stop_idx], "highlight": True})
        remaining = remaining[stop_idx + len(_HEADLINE_STOP_SEL) :]
    return segments


def search_notes_global(*, owner, query: str) -> list[Note]:
    """Owner-scoped, active-notes-only PostgreSQL full-text search using
    `websearch_to_tsquery` semantics (unquoted terms AND together; quoted
    text becomes a native phrase match; malformed input degrades gracefully;
    stemming remains active). Ranking and highlighting are driven by the
    exact same parsed query: each returned `Note` carries `.title_headline`/
    `.body_headline` raw sentinel-delimited strings (parse with
    `build_segments_from_headline()`), computed by PostgreSQL's own
    `SearchHeadline` in the same query that computes `.rank` -- ranking and
    the visible explanation of why a result matched can never diverge, which
    is exactly the mismatch this design replaces. Folder names are not part
    of the search vector; folder context remains display-only.

    Three bounded technical-token limitations are known and accepted --
    none warrants a hybrid substring fallback or
    a user-facing warning, and each is pinned by its own regression test
    (`notes/tests/test_global_search.py`, "approved technical-token
    limitations"): (1) full-text search matches whole lexemes only, so a
    partial/prefix token (e.g. an incomplete IP address) never matches a
    complete stored one; (2) a punctuation-heavy compound embedded in a
    longer slash path (e.g. a full `registry/org/image:tag` pull reference)
    may not self-match, since `to_tsvector` (indexing) and
    `websearch_to_tsquery` (querying) can tokenize the same compound string
    differently -- searching a standalone useful component instead is the
    supported path; (3) PostgreSQL's English dictionary reduces some
    contractions (e.g. "don't") to zero lexemes, a silent non-match, never
    an error or a false broad match."""
    trimmed_query = query.strip()
    if not trimmed_query or len(trimmed_query) > GLOBAL_SEARCH_MAX_QUERY_LENGTH:
        return []
    if not _query_has_lexemes(trimmed_query):
        return []

    search_query = SearchQuery(trimmed_query, search_type="websearch")
    vector = SearchVector("title", weight="A") + SearchVector("body_plain_text", weight="B")
    notes = (
        Note.objects.filter(owner=owner, trashed_at__isnull=True)
        .annotate(
            search=vector,
            rank=SearchRank(vector, search_query),
            title_headline=SearchHeadline(
                "title",
                search_query,
                start_sel=_HEADLINE_START_SEL,
                stop_sel=_HEADLINE_STOP_SEL,
                highlight_all=True,
            ),
            body_headline=SearchHeadline(
                "body_plain_text",
                search_query,
                start_sel=_HEADLINE_START_SEL,
                stop_sel=_HEADLINE_STOP_SEL,
                max_fragments=_BODY_HEADLINE_MAX_FRAGMENTS,
                max_words=_BODY_HEADLINE_MAX_WORDS,
                min_words=_BODY_HEADLINE_MIN_WORDS,
                fragment_delimiter=_HEADLINE_FRAGMENT_DELIMITER,
            ),
        )
        # `rank__gt=0` alone is not a safe match filter: PostgreSQL's
        # `ts_rank` can return a tiny nonzero float (e.g. `1e-20`) for a
        # document that does NOT actually satisfy the query's boolean/phrase
        # structure (`vector @@ query` is false) -- confirmed empirically
        # against a real phrase query. The `search=search_query` filter
        # compiles to the actual `@@` match operator, the only correct test
        # for "does this row match at all"; rank is used for ordering only.
        .filter(search=search_query)
        .select_related("folder")
        .order_by("-rank", "-modified_at", "-id")
    )
    return list(notes[:GLOBAL_SEARCH_RESULT_LIMIT])


# Full Search/Filter. A genuinely separate,
# dedicated destination page from Global Search above: real pagination
# instead of a hard result cap, and text/Tag/folder/lifecycle filters
# composable in any combination. `search_notes_full()` reuses
# `_query_has_lexemes()`, `build_segments_from_headline()`, and the
# `_HEADLINE_*` sentinels already defined above rather than re-deriving
# equivalent logic; `search_notes_global()` itself is untouched.


def validate_full_search_text(query: str) -> str | None:
    """Validates Full Search's optional text criterion *before* any
    query is built. Blank/whitespace-only input is always valid --
    it means "no text filter" and is never an error. Nonblank input
    that PostgreSQL's own `websearch_to_tsquery` cannot use at all
    (stop-word-only, punctuation-only) or that exceeds the established
    length limit is invalid and must not be silently treated as though
    no text had been supplied -- doing so would silently broaden the
    search to a materially different, Tag/folder/lifecycle-only query
    than the one the user actually asked for. Returns one of a closed
    set of error codes for that nonblank-but-unusable case, or `None`
    when the text is either blank or genuinely usable. Malformed-but-
    processable input (e.g. an unmatched quote) is "usable" here --
    PostgreSQL's own parser already degrades it gracefully, exactly as
    Global Search already relies on."""
    trimmed_query = query.strip()
    if not trimmed_query:
        return None
    if len(trimmed_query) > GLOBAL_SEARCH_MAX_QUERY_LENGTH:
        return "query_too_long"
    if not _query_has_lexemes(trimmed_query):
        return "query_has_no_searchable_terms"
    return None


def _full_search_trash_scope_filter(*, include_trash: bool, now: datetime) -> Q:
    """Full Search's user-facing Lifecycle control is a single
    `Include Trash` checkbox -- there is no Trash-only mode in
    Full Search (the dedicated Trash surface remains the right
    destination for that). Unchecked is the same plain
    `trashed_at__isnull` check every active-notes query already uses;
    checked is that same Active filter OR the existing
    `_visible_trash_filter()` (owner-visible Trash only).
    `_hidden_but_recoverable_filter()` (administrator-only recoverable
    Notes) is deliberately never referenced here, or anywhere else in
    Full Search -- that exclusion is a privacy/correctness boundary, not
    a style choice."""
    if include_trash:
        return Q(trashed_at__isnull=True) | _visible_trash_filter(now=now)
    return Q(trashed_at__isnull=True)


def _apply_full_search_tag_status(notes, tag_status: str):
    if tag_status == "tagged":
        return notes.annotate(_full_search_tag_count=Count("tags", distinct=True)).filter(
            _full_search_tag_count__gt=0
        )
    if tag_status == "untagged":
        return notes.filter(tags__isnull=True)
    return notes


def _apply_full_search_tags(notes, *, tag_ids: list[int], tag_mode: str):
    if tag_mode == "all":
        return notes.annotate(
            _full_search_matched_tags=Count("tags", filter=Q(tags__in=tag_ids), distinct=True)
        ).filter(_full_search_matched_tags=len(tag_ids))
    return notes.filter(tags__in=tag_ids).distinct()


def _full_search_annotate_text(notes, *, trimmed_query: str, search_in: str):
    """Constructs only the SearchVector/SearchHeadline pieces the
    selected `search_in` scope actually needs -- `"title"`/`"body"`
    never compute (or annotate) the excluded field at all, not merely
    hide it afterward. Title-over-body weighting (`A`/`B`) is preserved
    only in `"both"` mode, where both fields genuinely compete in one
    rank; a single-field scope has nothing to weight against, so its
    sole vector uses `A`."""
    search_query = SearchQuery(trimmed_query, search_type="websearch")
    include_title = search_in in ("both", "title")
    include_body = search_in in ("both", "body")

    vector = None
    if include_title:
        vector = SearchVector("title", weight="A")
    if include_body:
        body_vector = SearchVector("body_plain_text", weight="B" if include_title else "A")
        vector = vector + body_vector if vector is not None else body_vector

    annotations: dict[str, Any] = {"search": vector, "rank": SearchRank(vector, search_query)}
    if include_title:
        annotations["title_headline"] = SearchHeadline(
            "title",
            search_query,
            start_sel=_HEADLINE_START_SEL,
            stop_sel=_HEADLINE_STOP_SEL,
            highlight_all=True,
        )
    if include_body:
        annotations["body_headline"] = SearchHeadline(
            "body_plain_text",
            search_query,
            start_sel=_HEADLINE_START_SEL,
            stop_sel=_HEADLINE_STOP_SEL,
            max_fragments=_BODY_HEADLINE_MAX_FRAGMENTS,
            max_words=_BODY_HEADLINE_MAX_WORDS,
            min_words=_BODY_HEADLINE_MIN_WORDS,
            fragment_delimiter=_HEADLINE_FRAGMENT_DELIMITER,
        )

    return (
        notes.annotate(**annotations)
        # See `search_notes_global()` above for why `search=search_query`
        # (the real `@@` match operator) is the match filter, never
        # `rank__gt=0`.
        .filter(search=search_query)
        .order_by("-rank", "-modified_at", "-id")
    )


def search_notes_full(
    *,
    owner,
    query: str,
    search_in: str,
    tag_ids: list[int],
    tag_mode: str,
    tag_status: str,
    folder_id: int | None,
    folder_is_unfiled: bool,
    include_trash: bool,
):
    """Full Search's dedicated query. Returns a
    lazy, unsliced queryset -- unlike `search_notes_global()`'s capped
    `list` -- so the view can hand it directly to a real `Paginator`.

    Assumes the caller has *already* validated every criterion (`query`
    via `validate_full_search_text()`; the Tag selection is <= 20 valid
    owned IDs; `tag_ids` and `tag_status="untagged"` are never both
    non-empty/active at once) -- this function only ever builds a query
    from already-valid input and holds no validation or error-reporting
    logic of its own, by deliberate design."""
    notes = Note.objects.filter(owner=owner).filter(
        _full_search_trash_scope_filter(include_trash=include_trash, now=timezone.now())
    )

    if folder_is_unfiled:
        notes = notes.filter(folder__isnull=True)
    elif folder_id is not None:
        notes = notes.filter(folder_id=folder_id)

    notes = _apply_full_search_tag_status(notes, tag_status)

    if tag_ids:
        notes = _apply_full_search_tags(notes, tag_ids=tag_ids, tag_mode=tag_mode)

    trimmed_query = query.strip()
    if trimmed_query:
        notes = _full_search_annotate_text(notes, trimmed_query=trimmed_query, search_in=search_in)
    else:
        notes = notes.order_by("-modified_at", "-id")

    return notes.select_related("folder").prefetch_related("tags")


def notes_grouped_for_tree(*, owner) -> tuple[list[Folder], list[Note]]:
    notes_prefetch = Prefetch(
        "notes",
        queryset=Note.objects.filter(owner=owner, trashed_at__isnull=True).order_by(
            *TREE_NOTE_DISPLAY_ORDER
        ),
    )
    all_folders = list(
        Folder.objects.filter(owner=owner, trashed_at__isnull=True).prefetch_related(notes_prefetch)
    )
    # An empty "Recovered Items" marker folder is hidden from the tree --
    # presentation only, never deleted or recreated. The marker row (and
    # `_get_or_create_recovery_folder()`'s lookup-by-flag) are completely
    # untouched by this: restore destination resolution never queries
    # through tree enumeration, so it still finds the hidden row exactly
    # as before. Ordinary empty folders remain visible; only the recovery
    # marker gets this treatment. `folder.notes.all()` reads the already-
    # prefetched cache here (safe only once `all_folders` above has been
    # fully materialized), never issuing a query per folder.
    folders = [
        folder
        for folder in all_folders
        if not (folder.is_recovery_folder and not folder.notes.all())
    ]
    unfiled_notes = list(
        Note.objects.filter(owner=owner, folder__isnull=True, trashed_at__isnull=True).order_by(
            *TREE_NOTE_DISPLAY_ORDER
        )
    )
    return folders, unfiled_notes


def adjacent_active_note_in_same_folder(*, note: Note) -> Note | None:
    """For post-delete navigation when the note being
    deleted is the currently-open note. Finds the nearest remaining active
    note in `note`'s own folder (or Unfiled, when `note.folder_id` is
    `None`), using the exact tree ordering (`TREE_NOTE_DISPLAY_ORDER`) --
    never a new sort. Must be called *before* `note` is trashed/deleted, so
    its own position among its still-active siblings can still be located.
    Prefers the note immediately after `note` in that order (the one that
    visually "takes its place"); falls back to the one immediately before
    it if `note` was last; returns `None` if no sibling remains."""
    siblings = list(
        Note.objects.filter(
            owner=note.owner_id, folder_id=note.folder_id, trashed_at__isnull=True
        ).order_by(*TREE_NOTE_DISPLAY_ORDER)
    )
    try:
        index = next(i for i, sibling in enumerate(siblings) if sibling.pk == note.pk)
    except StopIteration:
        return None
    if index + 1 < len(siblings):
        return siblings[index + 1]
    if index > 0:
        return siblings[index - 1]
    return None


def create_note(*, owner, folder: Folder | None = None) -> Note:
    # The note does not exist yet, so there is no
    # note row to lock -- the destination folder is the only thing to
    # revalidate, under its own lock, inside the same transaction as
    # the insert. See `assign_note_folder()`'s matching comment.
    with transaction.atomic():
        locked_folder = None
        if folder is not None:
            locked_folder = (
                Folder.objects.select_for_update()
                .filter(pk=folder.pk, owner=owner, trashed_at__isnull=True)
                .first()
            )
            if locked_folder is None:
                raise TrashedItemMutationError()
        body_json = documents.canonical_empty_document()
        note = Note.objects.create(
            owner=owner,
            folder=locked_folder,
            title="",
            body_json=body_json,
            body_plain_text=documents.derive_plain_text(body_json),
            editor_schema_version=documents.EDITOR_SCHEMA_VERSION,
            version=1,
        )
        note.title = documents.generated_title_for_timestamp(note.created_at)
        note.save(update_fields=["title"])
    return note


def _duplicate_title_candidate(*, base_title: str, attempt: int) -> str:
    prefix = "Copy of " if attempt == 1 else f"Copy {attempt} of "
    max_length = Note._meta.get_field("title").max_length
    available = max(max_length - len(prefix), 0)
    truncated_base_title = base_title if len(base_title) <= available else base_title[:available]
    return f"{prefix}{truncated_base_title}"


def duplicate_note(*, note: Note) -> Note:
    _assert_not_trashed(note)
    owner = note.owner
    # The duplicate inherits the source note's
    # current folder, so that folder -- not the (already-checked)
    # source note -- is the one that must be locked and revalidated;
    # see `assign_note_folder()`'s matching comment for why.
    with transaction.atomic():
        locked_folder = None
        if note.folder_id is not None:
            locked_folder = (
                Folder.objects.select_for_update()
                .filter(pk=note.folder_id, owner=owner, trashed_at__isnull=True)
                .first()
            )
            if locked_folder is None:
                raise TrashedItemMutationError()

        attempt = 1
        candidate_title = _duplicate_title_candidate(base_title=note.title, attempt=attempt)
        while Note.objects.filter(owner=owner, title=candidate_title).exists():
            attempt += 1
            candidate_title = _duplicate_title_candidate(base_title=note.title, attempt=attempt)

        duplicate = Note.objects.create(
            owner=owner,
            folder=locked_folder,
            title=candidate_title,
            body_json=note.body_json,
            body_plain_text=note.body_plain_text,
            editor_schema_version=note.editor_schema_version,
            version=1,
            pinned=False,
        )
        duplicate.tags.set(note.tags.all())
    return duplicate


def normalize_note_title(*, note: Note, submitted_title: str) -> str:
    title = submitted_title.strip()
    if title:
        return title
    created_at = note.created_at or timezone.now()
    return documents.generated_title_for_timestamp(created_at)


def note_is_supported(note: Note) -> bool:
    return documents.is_supported_schema_version(note.editor_schema_version)


def note_sync_payload(note: Note) -> dict[str, Any]:
    return {
        "title": note.title,
        "body_json": note.body_json,
        "editor_schema_version": note.editor_schema_version,
        "version": note.version,
        "modified_at": note.modified_at.isoformat(),
    }


@transaction.atomic
def save_note(*, note: Note, title: str, body_json: dict[str, Any], version: int) -> Note:
    current_note = Note.objects.select_for_update().get(pk=note.pk, owner=note.owner)
    if not note_is_supported(current_note):
        raise UnsupportedSchemaError("This note uses an unsupported document version.")
    _assert_not_trashed(current_note)

    normalized_title = normalize_note_title(note=current_note, submitted_title=title)
    body_plain_text = documents.derive_plain_text(body_json)
    modified_at = timezone.now()
    updated = Note.objects.filter(
        pk=current_note.pk,
        owner=current_note.owner,
        version=version,
        editor_schema_version=documents.EDITOR_SCHEMA_VERSION,
    ).update(
        title=normalized_title,
        body_json=body_json,
        body_plain_text=body_plain_text,
        modified_at=modified_at,
        version=F("version") + 1,
    )
    if updated != 1:
        current_note.refresh_from_db()
        if not note_is_supported(current_note):
            raise UnsupportedSchemaError("This note uses an unsupported document version.")
        raise NoteSaveConflictError(
            current_version=current_note.version,
            current_modified_at=current_note.modified_at,
        )

    note.refresh_from_db()
    return note


def note_export_payload(note: Note) -> dict[str, Any]:
    return {
        "format_identifier": documents.EXPORT_FORMAT_IDENTIFIER,
        "format_version": documents.EXPORT_FORMAT_VERSION,
        "editor_schema_version": note.editor_schema_version,
        "title": note.title,
        "body_json": note.body_json,
        "body_plain_text": note.body_plain_text,
        "created_at": note.created_at.isoformat(),
        "modified_at": note.modified_at.isoformat(),
        "exported_at": timezone.localtime().isoformat(),
    }
