from __future__ import annotations

import json
from typing import Any

from django import forms

from notes import documents, services
from notes.models import TAG_COLOR_CHOICES, Folder


class NoteUpdateForm(forms.Form):
    title = forms.CharField(max_length=255, required=False)
    body_json = forms.CharField(widget=forms.HiddenInput)
    version = forms.IntegerField(min_value=1, widget=forms.HiddenInput)

    def clean_title(self) -> str:
        return self.cleaned_data["title"].strip()

    def clean_body_json(self) -> dict[str, Any]:
        raw_body = self.cleaned_data["body_json"]
        try:
            parsed = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise forms.ValidationError("The note body must be valid JSON.") from exc
        return documents.validate_canonical_document(parsed)


class FolderCreateForm(forms.Form):
    name = forms.CharField(max_length=255)

    def clean_name(self) -> str:
        trimmed_name = self.cleaned_data["name"].strip()
        if not trimmed_name:
            raise forms.ValidationError("Folder name is required.")
        return trimmed_name


class FolderRenameForm(forms.Form):
    name = forms.CharField(max_length=255)

    def clean_name(self) -> str:
        trimmed_name = self.cleaned_data["name"].strip()
        if not trimmed_name:
            raise forms.ValidationError("Folder name is required.")
        return trimmed_name


class NotePinForm(forms.Form):
    pinned = forms.TypedChoiceField(
        choices=(("true", "true"), ("false", "false")),
        coerce=lambda value: value == "true",
    )


class NoteRenameForm(forms.Form):
    title = forms.CharField(max_length=255)

    def clean_title(self) -> str:
        trimmed_title = self.cleaned_data["title"].strip()
        if not trimmed_title:
            raise forms.ValidationError("Title is required.")
        return trimmed_title


class NoteMoveForm(forms.Form):
    # Rendered (never as a real selectable destination) when a note's current
    # folder is trashed, so the "Unfiled" option is never falsely pre-selected
    # and an unmodified form submission never silently unfiles the note.
    KEEP_CURRENT_FOLDER_VALUE = "keep-current-folder"

    folder = forms.ModelChoiceField(
        queryset=Folder.objects.none(),
        required=False,
        empty_label="Unfiled",
    )

    def __init__(self, *args: Any, owner: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.fields["folder"].queryset = Folder.objects.filter(owner=owner, trashed_at__isnull=True)


class NoteTagAssignForm(forms.Form):
    # `error_messages` keeps the pre-existing, friendlier message for the
    # blank-after-strip case -- Django's `CharField` (`strip=True` by
    # default) already reduces whitespace-only input to `""` and raises
    # its own required-field error before `clean_name()` (and therefore
    # `normalize_tag_name()`) ever runs, so this is the one message this
    # form still owns directly rather than delegating to the shared
    # normalization helper.
    name = forms.CharField(max_length=100, error_messages={"required": "Tag name is required."})
    # The leading blank choice is the "Default
    # color" placeholder -- a real, meaningful value (no explicit color
    # was chosen), not merely "missing." `required=False` plus this
    # explicit blank choice is what lets an intentional Slate selection
    # stay distinguishable from no selection at all.
    color = forms.ChoiceField(
        choices=(("", "Default color"), *TAG_COLOR_CHOICES),
        required=False,
    )

    def __init__(self, *args, user=None, **kwargs):
        # The same `user=` keyword shape already
        # established by `AccountPasswordChangeForm` -- `clean_name()`
        # needs the current owner to resolve their `tag_uppercase_enabled`
        # preference. Both existing callers (`note_tag_assign()`,
        # `tag_create()`) always pass `user=request.user`; the
        # `if self.user else True` fallback below is defensive only, so
        # a form built without one (none exist today) still behaves
        # as if the preference were on rather than raising.
        self.user = user
        super().__init__(*args, **kwargs)

    def clean_name(self) -> str:
        uppercase = self.user.tag_uppercase_enabled if self.user is not None else True
        try:
            return services.normalize_tag_name(self.cleaned_data["name"], uppercase=uppercase)
        except services.TagNameValidationError as exc:
            raise forms.ValidationError(str(exc)) from exc

    def clean_color(self) -> str:
        # Blank has real meaning ("no
        # explicit color was chosen") and must reach
        # `resolve_tag_color_for_creation()` unresolved -- no longer
        # coerced to a real palette value here.
        return self.cleaned_data["color"]


class NoteTagRemoveForm(forms.Form):
    tag_id = forms.IntegerField(min_value=1)


class LibraryBackupUploadForm(forms.Form):
    # `accept` is a browser usability hint only -- extension, MIME
    # type, and filename are never trusted; the only real check is the
    # ZIP container structure itself, performed by
    # notes.library_backup_upload.validate_library_backup_upload().
    backup_file = forms.FileField(widget=forms.ClearableFileInput(attrs={"accept": ".zip"}))


LIBRARY_BACKUP_RESTORE_CONFIRMATION_PHRASE = "REPLACE MY CURRENT LIBRARY"


class LibraryBackupRestoreForm(forms.Form):
    backup_file = forms.FileField(widget=forms.ClearableFileInput(attrs={"accept": ".zip"}))
    confirmation = forms.CharField(
        label=f"Type {LIBRARY_BACKUP_RESTORE_CONFIRMATION_PHRASE} to confirm",
        widget=forms.TextInput(attrs={"autocomplete": "off", "spellcheck": "false"}),
    )

    def clean_confirmation(self) -> str:
        # Only leading/trailing whitespace is tolerated -- internal
        # spacing and case must match exactly, a deliberately stricter
        # comparison than PurgeUserAccountForm's normalized username
        # check, since this phrase exists specifically to require
        # careful, attentive typing.
        entered = self.cleaned_data["confirmation"].strip()
        if entered != LIBRARY_BACKUP_RESTORE_CONFIRMATION_PHRASE:
            raise forms.ValidationError(
                f"Type {LIBRARY_BACKUP_RESTORE_CONFIRMATION_PHRASE} exactly to confirm."
            )
        return entered
