const NEW_NOTE_PANEL_SELECTOR = ".tree-nav__action--new-note";
const NEW_FOLDER_SELECTOR = ".tree-nav__action--new-folder";
const RENAME_PANEL_SELECTOR = ".tree-nav__action--rename-folder";
const MOVE_NOTE_PANEL_SELECTOR = ".tree-nav__action--move-note";

/**
 * Wires the New Folder popover, every Rename panel (folder rows, and
 * tree note rows too -- both reuse the exact
 * same `.tree-nav__action--rename-folder` class), and the tree
 * note Move panel (all plain <details>/<summary>) so that opening one
 * moves keyboard focus straight into its primary field -- the name input
 * for Rename/New Folder, the destination `<select>` for Move -- selecting
 * any prefilled text where applicable, matching the same immediate-
 * ready-to-use behavior a modal would give without introducing one. The
 * 'toggle' event only fires on a real open/close state change -- never
 * for a server-rendered `open` attribute present at initial parse -- so
 * an ordinary page load where a form was not intentionally opened never
 * autofocuses from the toggle listener alone.
 *
 * One exception is handled explicitly: New Folder can itself be
 * server-rendered already `open` after a validation error (invalid/
 * duplicate name), so the popover doesn't just show an error message and
 * quietly close -- the field must still be focused and ready for another
 * attempt. Since no 'toggle' event fires for that case (the state didn't
 * change from RidgeNote's client-side perspective, only relative to the
 * fresh page load), this is checked once directly at init time.
 *
 * Closing a Rename or Move panel specifically returns focus to its
 * owning row's three-dot menu trigger, since both live inside that menu
 * rather than being their own persistent control. New Folder's own
 * close/focus-return (Cancel, Escape, outside click) is handled generically
 * by tree-context-menu.ts, since New Folder is also a `.row-action-menu`.
 *
 * Within one tree
 * note row menu, Rename and Move behave as an accordion -- opening one
 * closes the other if it was open (see the sibling-closing block below).
 * Resetting both to collapsed whenever the *containing row menu itself*
 * closes (outside click, Escape, re-toggling the trigger, or another row
 * menu opening) is handled separately, in tree-context-menu.ts's own
 * close lifecycle, not here.
 *
 * New note and New folder join this same
 * accordion/focus-return treatment when nested inside the active-note
 * overflow menu (alongside its own Move disclosure) -- but New Folder's
 * *standalone* tree-toolbar popover, also matched by `NEW_FOLDER_SELECTOR`,
 * is itself a top-level `.row-action-menu`, not a disclosure nested inside
 * one, and must keep the existing generic handling in tree-context-menu.ts
 * untouched. `isNestedAccordionMember` distinguishes the two: only the
 * standalone popover ever carries the `row-action-menu` class itself.
 */
export function initFolderActionAutofocusDocument(
  doc: Document = document,
): boolean {
  const disclosures = Array.from(
    doc.querySelectorAll<HTMLDetailsElement>(
      `${NEW_NOTE_PANEL_SELECTOR}, ${NEW_FOLDER_SELECTOR}, ${RENAME_PANEL_SELECTOR}, ${MOVE_NOTE_PANEL_SELECTOR}`,
    ),
  );
  if (disclosures.length === 0) {
    return false;
  }

  const isNestedAccordionMember = (disclosure: HTMLDetailsElement): boolean =>
    !disclosure.classList.contains("row-action-menu") &&
    (disclosure.matches(NEW_NOTE_PANEL_SELECTOR) ||
      disclosure.matches(NEW_FOLDER_SELECTOR) ||
      disclosure.matches(RENAME_PANEL_SELECTOR) ||
      disclosure.matches(MOVE_NOTE_PANEL_SELECTOR));

  const focusAndSelectPrimaryField = (disclosure: HTMLDetailsElement): void => {
    const input =
      disclosure.querySelector<HTMLInputElement>('input[type="text"]');
    if (input) {
      input.focus();
      input.select();
      return;
    }
    disclosure.querySelector<HTMLSelectElement>("select")?.focus();
  };

  for (const disclosure of disclosures) {
    if (disclosure.dataset.folderActionInitialized === "true") {
      continue;
    }

    if (disclosure.open) {
      focusAndSelectPrimaryField(disclosure);
    }

    disclosure.addEventListener("toggle", () => {
      if (disclosure.open) {
        // Rename and
        // Move operate as an accordion within one tree note row menu --
        // opening one closes the other if it's open. Scoped to the
        // enclosing `.tree-nav__row-menu-panel` (not the whole document),
        // so this only ever finds the sibling Rename/Move that lives in
        // the *same* row's menu, never a different row's. Folder rows
        // have no Move sibling at all, so this naturally no-ops there --
        // no folder-specific branch needed. Setting a sibling's `.open`
        // to `false` here is synchronous and fires that sibling's own
        // 'toggle' listener (registered in this same loop) immediately,
        // including its close-branch focus-return below; this listener's
        // own deferred `setTimeout` focus call further down still runs
        // last, after that, so the newly opened field still wins the
        // final focus -- matching the existing "click on <summary> is
        // followed by a deferred correction" pattern already established
        // for New Folder/Rename above.
        if (isNestedAccordionMember(disclosure)) {
          const panel = disclosure.closest<HTMLElement>(
            ".tree-nav__row-menu-panel",
          );
          const siblings = panel
            ? Array.from(
                panel.querySelectorAll<HTMLDetailsElement>(
                  `${NEW_NOTE_PANEL_SELECTOR}, ${NEW_FOLDER_SELECTOR}, ${RENAME_PANEL_SELECTOR}, ${MOVE_NOTE_PANEL_SELECTOR}`,
                ),
              )
            : [];
          for (const sibling of siblings) {
            if (sibling !== disclosure && sibling.open) {
              sibling.open = false;
            }
          }
        }

        // Looked up synchronously, before deferring: tree-context-menu.ts
        // also listens for this same 'toggle' event (New Folder is now
        // also a `.row-action-menu`) and, still within this same
        // synchronous dispatch regardless of listener registration order,
        // moves this panel (and its field) into the shared portal. Waiting
        // to look the field up inside the deferred callback below would
        // find nothing -- it's already been moved by then. The reference
        // itself stays valid after the move (an element keeps working
        // after being reparented within the same document), so capturing
        // it now and only deferring the focus() call is enough.
        const input =
          disclosure.querySelector<HTMLInputElement>('input[type="text"]');
        const select = input
          ? null
          : disclosure.querySelector<HTMLSelectElement>("select");
        // Deferred to the next task: a user click on <summary> natively
        // focuses that summary as the click's default action, which is not
        // guaranteed to have happened yet inside a synchronous 'toggle'
        // handler and can clobber a focus() called here immediately --
        // confirmed live, where the field briefly gained focus and then
        // lost it back to the summary. Running after the current task lets
        // the field's focus win.
        setTimeout(() => {
          if (input) {
            input.focus();
            input.select();
          } else {
            select?.focus();
          }
        }, 0);
        return;
      }

      if (isNestedAccordionMember(disclosure)) {
        const menuTrigger = disclosure
          .closest<HTMLElement>(".row-action-menu")
          ?.querySelector<HTMLElement>(":scope > summary");
        menuTrigger?.focus();
      }
    });

    disclosure.dataset.folderActionInitialized = "true";
  }

  return true;
}
