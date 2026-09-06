from __future__ import annotations

from notes import library_backup_upload

LIBRARY_BACKUP_RESTORE_PREVIEW_PATH = "/notes/library-backup/restore-preview/"
LIBRARY_BACKUP_RESTORE_PATH = "/notes/library-backup/restore/"

# Explicit allowlist, not a path-prefix match -- keeps the guarded
# surface exactly these two routes, so no future unrelated route under
# /notes/library-backup/ is ever accidentally captured.
LIBRARY_BACKUP_UPLOAD_GUARDED_PATHS = frozenset(
    {LIBRARY_BACKUP_RESTORE_PREVIEW_PATH, LIBRARY_BACKUP_RESTORE_PATH}
)


class LibraryBackupUploadGuardMiddleware:
    """Installs `LibraryBackupRequestSizeGuard` on POSTs to the library
    backup restore-preview and restore routes before any
    `process_view` hook runs.

    `CsrfViewMiddleware.process_view()` reads `request.POST` to check
    `csrfmiddlewaretoken`, which triggers Django's multipart parse
    using whatever upload handlers are installed at that moment and
    then permanently locks `request.upload_handlers`. That happens
    before the view function itself ever executes, so a handler
    installed inside the view is always too late. Every middleware's
    pre-`get_response()` code runs before every `process_view` hook
    (CSRF's included), regardless of MIDDLEWARE ordering, so installing
    the guard here -- rather than in the view -- reliably beats CSRF to
    the punch.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.method == "POST" and request.path in LIBRARY_BACKUP_UPLOAD_GUARDED_PATHS:
            guard = library_backup_upload.LibraryBackupRequestSizeGuard(request)
            request.upload_handlers.insert(0, guard)
            request.library_backup_upload_guard = guard
        return self.get_response(request)
