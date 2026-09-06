# The canonical palette lives in
# `core.tag_colors` (a plain, Django-model-free constants module) so
# `accounts.models` can also import it without an app-loading-order
# hazard. Re-exported here under their original names -- every existing
# `from notes.models import TAG_COLOR_CHOICES`-style call site elsewhere
# in the codebase is unaffected by this move.
from core.tag_colors import DEFAULT_TAG_COLOR, TAG_COLOR_CHOICES, TAG_COLOR_VALUES
from django.conf import settings
from django.db import models
from django.db.models.functions import Lower


class Folder(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="folders",
    )
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    trashed_at = models.DateTimeField(null=True, blank=True)
    emptied_at = models.DateTimeField(null=True, blank=True)
    is_recovery_folder = models.BooleanField(default=False)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="notes_folder_name_not_blank_ck",
            ),
            models.UniqueConstraint(
                Lower("name"),
                "owner",
                name="notes_folder_owner_name_ci_uq",
                condition=models.Q(trashed_at__isnull=True),
            ),
            models.UniqueConstraint(
                fields=["owner"],
                condition=models.Q(is_recovery_folder=True),
                name="notes_folder_owner_recovery_marker_uq",
            ),
        ]
        ordering = [Lower("name").asc()]

    def __str__(self) -> str:
        return self.name


class Tag(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tags",
    )
    name = models.CharField(max_length=100)
    color = models.CharField(max_length=20, choices=TAG_COLOR_CHOICES, default=DEFAULT_TAG_COLOR)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(name=""),
                name="notes_tag_name_not_blank_ck",
            ),
            models.UniqueConstraint(
                Lower("name"),
                "owner",
                name="notes_tag_owner_name_ci_uq",
            ),
            models.CheckConstraint(
                condition=models.Q(color__in=TAG_COLOR_VALUES),
                name="notes_tag_color_valid_ck",
            ),
        ]
        ordering = [Lower("name").asc()]

    def __str__(self) -> str:
        return self.name


class Note(models.Model):
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="notes",
    )
    folder = models.ForeignKey(
        Folder,
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="notes",
    )
    # Set only inside `move_folder_to_trash()`'s own
    # cascade, on the same notes it newly moves to Trash -- never inferred
    # from `folder`/`trashed_at` after the fact. Means exactly "this note
    # was newly trashed by that folder's own trash operation," so a later
    # folder restore can reliably restore the notes that belong with it,
    # without ever restoring a note merely because it still references the
    # same folder. Cleared on every path that ends this note's association
    # with that specific trash operation (individual restore by owner or
    # administrator, restored together with the folder, or independently
    # trashed again later) -- see each call site's own comment. `SET_NULL`
    # rather than `RESTRICT`/`CASCADE`: this field is a lifecycle marker,
    # not the note's real location (that remains `folder`, unchanged,
    # already `RESTRICT`-protected) -- it must never itself block a folder
    # purge, and in practice a folder can only ever be purged once no note
    # (marked or not) still references it via `folder` at all, per
    # `notes/purge.py`'s existing `_folder_is_referenced` check.
    trashed_via_folder = models.ForeignKey(
        Folder,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="trash_cascade_notes",
    )
    tags = models.ManyToManyField(Tag, related_name="notes", blank=True)
    title = models.CharField(max_length=255)
    body_json = models.JSONField()
    body_plain_text = models.TextField()
    editor_schema_version = models.PositiveIntegerField(default=1)
    version = models.PositiveIntegerField(default=1)
    pinned = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    modified_at = models.DateTimeField(auto_now=True)
    trashed_at = models.DateTimeField(null=True, blank=True)
    emptied_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["owner", "-modified_at"], name="notes_note_owner_i_c43444_idx"),
        ]
        ordering = ["-modified_at", "-id"]

    def __str__(self) -> str:
        return self.title
