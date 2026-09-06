const DIALOG_SELECTOR = "#restore-confirm-dialog";
const TRIGGER_SELECTOR = "[data-restore-trigger]";

// Exact copy the confirmation shows before a folder restore -- the
// server-computed count is advisory only (see `restore_folder_from_trash`/
// `restore_folder_for_administrator` in `notes/services.py`, which
// re-evaluate eligibility under a real lock and report the actual
// restored count in the resulting Django message instead).
function consequence(name: string, count: number): string {
  if (count === 0) {
    return `"${name}" will be restored. No associated notes will be restored.`;
  }
  if (count === 1) {
    return `"${name}" will be restored. 1 associated note will also be restored.`;
  }
  return `"${name}" will be restored. ${count} associated notes will also be restored.`;
}

/**
 * A shared in-context native `<dialog>` for folder
 * restore, modeled directly on `delete-confirm.ts`'s established pattern
 * (itself modeled on `initHelpPanel`) -- `showModal()` for native focus
 * containment, native Escape (cancel -> close, no custom keydown
 * handler), a `close` handler restoring focus to whichever trigger opened
 * it, and backdrop-click-to-close (`event.target === dialog`). Serves
 * every folder Restore trigger (owner Trash, Administrator Recovery) at
 * once: each trigger carries its own name/count/action/owner as
 * `data-restore-*` attributes (computed server-side, reusing the exact
 * eligibility rules the restore service itself uses), and a click just
 * copies them onto the one shared form before opening -- no server
 * round-trip needed to show the dialog. Individual note restore is
 * unchanged -- it has no `data-restore-trigger` and keeps posting
 * directly, with no confirmation step, matching quick-trash's own
 * no-confirmation contract.
 */
export function initRestoreConfirmDialog(doc: Document = document): boolean {
  const dialog = doc.querySelector<HTMLDialogElement>(DIALOG_SELECTOR);
  const triggers = Array.from(
    doc.querySelectorAll<HTMLElement>(TRIGGER_SELECTOR),
  );
  if (!dialog || triggers.length === 0) return false;

  const form = dialog.querySelector<HTMLFormElement>(
    "[data-restore-confirm-form]",
  );
  const titleEl = dialog.querySelector<HTMLElement>(
    "[data-restore-confirm-title]",
  );
  const consequenceEl = dialog.querySelector<HTMLElement>(
    "[data-restore-confirm-consequence]",
  );
  const cancelBtn = dialog.querySelector<HTMLElement>(
    "[data-restore-confirm-cancel]",
  );
  if (!form || !titleEl || !consequenceEl || !cancelBtn) return false;

  const ownerField = dialog.querySelector<HTMLInputElement>(
    '[data-restore-confirm-field="owner"]',
  );

  let restoreFocusTarget: HTMLElement | null = null;

  for (const trigger of triggers) {
    trigger.addEventListener("click", () => {
      const name = trigger.dataset.restoreName ?? "";
      const count = Number.parseInt(trigger.dataset.restoreCount ?? "0", 10);

      titleEl.textContent = "Restore folder?";
      consequenceEl.textContent = consequence(
        name,
        Number.isNaN(count) ? 0 : count,
      );
      form.action = trigger.dataset.restoreAction ?? "";
      if (ownerField) ownerField.value = trigger.dataset.restoreOwner ?? "";

      restoreFocusTarget = trigger;
      dialog.showModal();
    });
  }

  cancelBtn.addEventListener("click", () => {
    dialog.close();
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });

  dialog.addEventListener("close", () => {
    restoreFocusTarget?.focus();
    restoreFocusTarget = null;
  });

  return true;
}
