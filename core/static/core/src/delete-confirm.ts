const DIALOG_SELECTOR = "#delete-confirm-dialog";
const TRIGGER_SELECTOR = "[data-delete-trigger]";
const ROW_MENU_SELECTOR = ".row-action-menu";

type DeleteKind = "note" | "folder" | "note-permanent";

const TITLE: Record<DeleteKind, string> = {
  note: "Move note to Trash?",
  folder: "Move folder to Trash?",
  "note-permanent": "Delete note permanently?",
};

// The submit button's label changes per kind,
// data-driven the same bounded way the title/consequence text are,
// rather than a broader refactor of this dialog's structure.
const SUBMIT_LABEL: Record<DeleteKind, string> = {
  note: "Move to Trash",
  folder: "Move to Trash",
  "note-permanent": "Delete permanently",
};

// The one shared source of consequence copy for note, folder, and
// permanent-delete confirmation, all of which reuse this single dialog.
function consequence(kind: DeleteKind, name: string): string {
  if (kind === "folder") {
    return (
      `"${name}" and the notes currently inside it will move to Trash. ` +
      "The folder can be restored from Trash separately, and each note " +
      "can be restored individually from Trash."
    );
  }
  if (kind === "note-permanent") {
    return (
      `"${name}" will leave your Trash and can no longer be accessed or ` +
      "restored by you. An administrator may still be able to recover it " +
      "until the normal 90-day retention period expires. Administrators " +
      "can only restore deleted notes back to your library; they cannot " +
      "read the contents of your notes. If you deleted a note by mistake, " +
      "contact your RidgeNote administrator as soon as possible so they " +
      "can attempt recovery."
    );
  }
  return `"${name}" will move to Trash. You can restore it from Trash later.`;
}

/**
 * A single, shared in-context
 * `<dialog>` for note and folder delete, modeled directly on `initHelpPanel`'s established pattern
 * (`app.ts`) -- `showModal()` for native focus containment, no document-level
 * keydown handler (Escape is owned by the native dialog: cancel -> close),
 * and a single `close` handler that restores focus to whichever trigger
 * opened it. Unlike Help/Search (one fixed trigger set, static content),
 * this dialog serves every note/folder Delete trigger across both tree
 * copies (wide + narrow drawer) and the All Notes row list at once: each
 * trigger carries the real item name/kind and its own already-correct
 * `action`/origin/current-note/page/sort as `data-*` attributes (computed
 * server-side exactly as the old confirm-page's hidden fields were), and
 * a click just copies them onto the one shared form before opening --
 * no per-trigger dialog instance, no URL-placeholder-substitution trick
 * like `tree-drag.ts` needs, since each trigger already has its own
 * correct final values.
 *
 * Backdrop click also closes (a gap neither existing dialog fills today,
 * scoped here rather than retrofitted onto Help/Search): a click whose
 * `target` is the `<dialog>` element itself, not a descendant, can only
 * land there via the backdrop, since the dialog's own content fills its
 * box.
 *
 * Quick-trash and drag-to-Trash are untouched --
 * neither has a `[data-delete-trigger]` and both keep posting directly,
 * with no confirmation step, exactly as before.
 */
export function initDeleteConfirmDialog(doc: Document = document): boolean {
  const dialog = doc.querySelector<HTMLDialogElement>(DIALOG_SELECTOR);
  const triggers = Array.from(
    doc.querySelectorAll<HTMLElement>(TRIGGER_SELECTOR),
  );
  if (!dialog || triggers.length === 0) return false;

  const form = dialog.querySelector<HTMLFormElement>(
    "[data-delete-confirm-form]",
  );
  const titleEl = dialog.querySelector<HTMLElement>(
    "[data-delete-confirm-title]",
  );
  const consequenceEl = dialog.querySelector<HTMLElement>(
    "[data-delete-confirm-consequence]",
  );
  const cancelBtn = dialog.querySelector<HTMLElement>(
    "[data-delete-confirm-cancel]",
  );
  const submitBtn = dialog.querySelector<HTMLElement>(
    "[data-delete-confirm-submit]",
  );
  if (!form || !titleEl || !consequenceEl || !cancelBtn || !submitBtn) {
    return false;
  }

  const field = (name: string): HTMLInputElement | null =>
    dialog.querySelector<HTMLInputElement>(
      `[data-delete-confirm-field="${name}"]`,
    );
  const originField = field("origin");
  const currentNoteField = field("current_note");
  const pageField = field("page");
  const sortField = field("sort");

  let restoreFocusTarget: HTMLElement | null = null;

  for (const trigger of triggers) {
    trigger.addEventListener("click", () => {
      // The Delete trigger typically lives inside an open `.row-action-menu`
      // <details> popover; that menu has no reason to stay open behind the
      // dialog (its own outside-click handling never fires here, since the
      // dialog isn't a click outside the still-open menu until later).
      const menu = trigger.closest<HTMLDetailsElement>(ROW_MENU_SELECTOR);
      if (menu) menu.open = false;

      const kind: DeleteKind =
        trigger.dataset.deleteKind === "folder"
          ? "folder"
          : trigger.dataset.deleteKind === "note-permanent"
            ? "note-permanent"
            : "note";
      const name = trigger.dataset.deleteName ?? "";

      titleEl.textContent = TITLE[kind];
      consequenceEl.textContent = consequence(kind, name);
      submitBtn.textContent = SUBMIT_LABEL[kind];
      form.action = trigger.dataset.deleteAction ?? "";
      if (originField) originField.value = trigger.dataset.deleteOrigin ?? "";
      if (currentNoteField) {
        currentNoteField.value = trigger.dataset.deleteCurrentNote ?? "";
      }
      if (pageField) pageField.value = trigger.dataset.deletePage ?? "";
      if (sortField) sortField.value = trigger.dataset.deleteSort ?? "";

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

  // Handles both explicit close-button/backdrop closure and native Escape
  // (cancel -> close). No document-level keydown handler is added; Escape
  // is owned by the native dialog -- matching `initHelpPanel`'s own
  // documented rationale exactly.
  dialog.addEventListener("close", () => {
    restoreFocusTarget?.focus();
    restoreFocusTarget = null;
  });

  return true;
}
