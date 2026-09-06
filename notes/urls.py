from django.urls import path

from notes import views

app_name = "notes"

urlpatterns = [
    path("notes/new/", views.note_create, name="create"),
    path(
        "notes/<int:note_id>/notes/new/",
        views.note_create_sibling,
        name="note_create_sibling",
    ),
    path("notes/all/", views.all_notes, name="all_notes"),
    path(
        "notes/library-backup/download/",
        views.library_backup_download,
        name="library_backup_download",
    ),
    path(
        "notes/library-backup/restore-preview/",
        views.library_backup_restore_preview,
        name="library_backup_restore_preview",
    ),
    path(
        "notes/library-backup/restore/",
        views.library_backup_restore,
        name="library_backup_restore",
    ),
    path("notes/quick-switch/", views.quick_switch_search, name="quick_switch_search"),
    path("notes/search/", views.global_search, name="global_search"),
    path("notes/search/full/", views.full_search, name="full_search"),
    path("notes/<int:note_id>/", views.note_detail, name="detail"),
    path("notes/<int:note_id>/autosave/", views.note_autosave, name="autosave"),
    path("notes/<int:note_id>/freshness/", views.note_freshness, name="freshness"),
    path("notes/<int:note_id>/print/", views.note_print, name="print"),
    path("notes/<int:note_id>/export/", views.note_export, name="export"),
    path("notes/<int:note_id>/download/text/", views.note_download_text, name="download_text"),
    path(
        "notes/<int:note_id>/download/markdown/",
        views.note_download_markdown,
        name="download_markdown",
    ),
    path("notes/<int:note_id>/folders/new/", views.folder_create, name="folder_create"),
    path("folders/new/", views.folder_create_home, name="folder_create_home"),
    path(
        "notes/<int:note_id>/folders/<int:folder_id>/notes/new/",
        views.folder_note_create,
        name="folder_note_create",
    ),
    path(
        "folders/<int:folder_id>/notes/new/",
        views.folder_note_create_home,
        name="folder_note_create_home",
    ),
    path(
        "notes/<int:note_id>/folders/<int:folder_id>/rename/",
        views.folder_rename,
        name="folder_rename",
    ),
    path("folders/<int:folder_id>/rename/", views.folder_rename_home, name="folder_rename_home"),
    path(
        "notes/<int:note_id>/folders/<int:folder_id>/delete/",
        views.folder_delete,
        name="folder_delete",
    ),
    path("folders/<int:folder_id>/delete/", views.folder_delete_home, name="folder_delete_home"),
    path("folders/<int:folder_id>/restore/", views.folder_restore, name="folder_restore"),
    path("notes/<int:note_id>/move/", views.note_move, name="note_move"),
    path("notes/<int:note_id>/move/home/", views.note_move_home, name="note_move_home"),
    path("notes/<int:note_id>/move/all/", views.note_move_all_notes, name="note_move_all_notes"),
    path("notes/<int:note_id>/rename/", views.note_rename, name="note_rename"),
    path("notes/<int:note_id>/rename/home/", views.note_rename_home, name="note_rename_home"),
    path(
        "notes/<int:note_id>/rename/all/",
        views.note_rename_all_notes,
        name="note_rename_all_notes",
    ),
    path("notes/<int:note_id>/pin/", views.note_pin_set, name="note_pin_set"),
    path(
        "notes/<int:note_id>/pin/all/", views.note_pin_set_all_notes, name="note_pin_set_all_notes"
    ),
    path(
        "notes/<int:note_id>/tags/suggestions/",
        views.note_tag_suggestions,
        name="note_tag_suggestions",
    ),
    path("notes/<int:note_id>/tags/assign/", views.note_tag_assign, name="note_tag_assign"),
    path("notes/<int:note_id>/tags/remove/", views.note_tag_remove, name="note_tag_remove"),
    path("notes/<int:note_id>/duplicate/", views.note_duplicate, name="note_duplicate"),
    path("notes/<int:note_id>/delete/", views.note_delete, name="note_delete"),
    path(
        "notes/<int:note_id>/delete/all/",
        views.note_delete_all_notes,
        name="note_delete_all_notes",
    ),
    path("notes/<int:note_id>/restore/", views.note_restore, name="note_restore"),
    path(
        "notes/<int:note_id>/permanent-delete/",
        views.note_permanent_delete,
        name="note_permanent_delete",
    ),
    path("trash/", views.trash, name="trash"),
    path("trash/empty/", views.trash_empty, name="trash_empty"),
    path("admin/recovery/", views.admin_recovery, name="admin_recovery"),
    path(
        "admin/recovery/notes/<int:note_id>/restore/",
        views.admin_note_restore,
        name="admin_note_restore",
    ),
    path(
        "admin/recovery/folders/<int:folder_id>/restore/",
        views.admin_folder_restore,
        name="admin_folder_restore",
    ),
]
