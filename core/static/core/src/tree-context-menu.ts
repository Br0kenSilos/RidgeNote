import type { TreeShellWindowLike } from "./tree-shell";

export const ROW_ACTION_MENU_SELECTOR = ".row-action-menu";
export const ROW_MENU_PANEL_SELECTOR =
  ".tree-nav__row-menu-panel, .note-list__row-menu-panel";
export const ROW_MENU_PORTAL_SELECTOR = "[data-row-menu-portal]";

const ROW_MENU_GAP = 4;
const ROW_MENU_VIEWPORT_MARGIN = 4;

const NEW_FOLDER_ACTION_SELECTOR = ".tree-nav__action--new-folder";

/**
 * A structural (not `instanceof HTMLInputElement`)
 * type for the note-title input the Rename trigger focuses/selects --
 * this file's own tests exercise `initRowActionMenusDocument` against
 * hand-rolled fakes rather than real DOM elements, matching every other
 * `*Like` interface already used throughout this module.
 */
interface TitleFocusTargetLike {
  focus(): void;
  select(): void;
}

/**
 * A New Folder form
 * (standalone tree-toolbar popover, or nested inside the active-note
 * overflow) must return to a genuinely pristine state whenever it closes
 * -- collapsed AND with any typed value/inline validation error cleared
 * -- not just collapsed with stale content still sitting in the DOM ready
 * to reappear on reopen. Scoped to `.tree-nav__action--new-folder`
 * specifically: Rename's input is deliberately prefilled with the current
 * name and must never be cleared this way, and Move has no error state of
 * its own to reset.
 */
function resetNewFolderForm(disclosure: Element): void {
  const input =
    disclosure.querySelector<HTMLInputElement>('input[type="text"]');
  if (input) {
    input.value = "";
    input.removeAttribute("aria-invalid");
  }
  const error = disclosure.querySelector<HTMLElement>(
    ".tree-nav__popover-field-error",
  );
  if (error) {
    error.textContent = "";
    error.hidden = true;
  }
}

export interface RowMenuWindowLike extends TreeShellWindowLike {
  innerHeight: number;
}

const noopWindowLike: RowMenuWindowLike = {
  addEventListener() {},
  innerHeight: 0,
  innerWidth: 0,
};

export interface RowMenuRect {
  bottom: number;
  height: number;
  left: number;
  right: number;
  top: number;
  width: number;
}

export interface RowMenuPosition {
  left: number;
  top: number;
}

export type RowMenuAlign = "left" | "right";

/**
 * Pure geometry: no DOM, fully unit-testable. Shared-tree row menus live
 * inside `.tree-nav__viewport`, which clips descendants via its own
 * `overflow: auto` -- a `position: absolute` panel confined to that
 * container gets invisibly clipped once a row sits near the bottom of the
 * currently-scrolled viewport. Positioning the panel with `position: fixed` in true
 * viewport coordinates escapes that clipping entirely, regardless of which
 * scrollable ancestor the trigger happens to be inside. Flips above the
 * trigger when there is not enough room below, and is clamped within the
 * viewport on both axes so it never runs off a narrow drawer or the window
 * edge.
 *
 * Horizontal anchor defaults to `"right"` (the panel's right edge aligns
 * with the trigger's right edge -- correct for row menus, which sit at a
 * row's right-hand action rail). Toolbar popovers anchored near the *left*
 * end of a row (Move note, New Folder in the creation toolbar) instead pass
 * `"left"`, aligning the panel's left edge with the trigger's left edge, so
 * the popover doesn't shift far to the left of a narrow trigger the way a
 * right-anchored wide panel would.
 */
export function computeRowMenuPosition(
  triggerRect: RowMenuRect,
  panelRect: RowMenuRect,
  viewportWidth: number,
  viewportHeight: number,
  align: RowMenuAlign = "right",
): RowMenuPosition {
  let top = triggerRect.bottom + ROW_MENU_GAP;
  if (top + panelRect.height > viewportHeight - ROW_MENU_VIEWPORT_MARGIN) {
    const above = triggerRect.top - panelRect.height - ROW_MENU_GAP;
    if (above >= ROW_MENU_VIEWPORT_MARGIN) {
      top = above;
    }
  }
  top = Math.max(
    ROW_MENU_VIEWPORT_MARGIN,
    Math.min(top, viewportHeight - panelRect.height - ROW_MENU_VIEWPORT_MARGIN),
  );

  let left =
    align === "left" ? triggerRect.left : triggerRect.right - panelRect.width;
  left = Math.max(
    ROW_MENU_VIEWPORT_MARGIN,
    Math.min(left, viewportWidth - panelRect.width - ROW_MENU_VIEWPORT_MARGIN),
  );

  return { left, top };
}

export interface RowMenuRectLike {
  getBoundingClientRect(): RowMenuRect;
}

export interface RowMenuPanelLike extends RowMenuRectLike {
  style: {
    left: string;
    maxHeight: string;
    maxWidth: string;
    position: string;
    right: string;
    top: string;
  };
}

export interface RowMenuViewportLike {
  innerHeight: number;
  innerWidth: number;
}

/** Applies fixed positioning to an opened panel; a no-op if either element is missing. */
export function floatRowMenuPanel(
  trigger: RowMenuRectLike | null,
  panel: RowMenuPanelLike | null,
  viewport: RowMenuViewportLike,
  align: RowMenuAlign = "right",
): void {
  if (!trigger || !panel) return;
  panel.style.position = "fixed";
  panel.style.top = "0px";
  panel.style.left = "0px";
  // The stylesheet's fallback `right: 0` (for the non-floated, absolutely
  // positioned default) is otherwise still active here. With both `left`
  // and `right` set on a fixed-position box and no explicit `width`, CSS
  // stretches the box to fill the space between them -- forcing the panel
  // to span from the trigger almost to the viewport edge instead of
  // sizing to its own content. Clearing it lets the panel measure and
  // render at its natural content width.
  panel.style.right = "auto";
  // `computeRowMenuPosition` below correctly clamps the panel's *left*
  // edge to stay on-screen, but that alone doesn't help if the panel's
  // own natural width (governed by its stylesheet `max-width` -- 20rem
  // for most row menus, 24rem for the Recent-notes panel, both sized for
  // wide/medium layouts) is wider than the viewport itself: at 320-414px,
  // clamping `left` to the viewport-margin minimum still leaves
  // `left + panelWidth` running off the right edge, since nothing
  // previously shrank the panel itself. Setting an inline `max-width`
  // here, before measuring, overrides the stylesheet value only while
  // floating (position: fixed) and only when the viewport genuinely
  // can't fit the stylesheet's preferred width -- at wide viewports this
  // number is larger than the panel would ever naturally grow to, so it
  // has no visible effect there. One shared fix in this shared foundation
  // covers every `.row-action-menu` panel (Recent notes, More note
  // actions, tree row menus, Move note, New Folder) at once, rather than
  // a separate per-trigger or per-viewport correction.
  panel.style.maxWidth = `${viewport.innerWidth - ROW_MENU_VIEWPORT_MARGIN * 2}px`;
  // A panel whose
  // content grew (a nested `.tree-nav__action` disclosure -- Rename or
  // Move -- expanded) can be taller than the viewport itself, especially
  // near the bottom of a scrolled tree. `computeRowMenuPosition` below
  // already clamps `top` to keep the panel's *position* on-screen, but
  // that alone can't help once the panel's own natural height exceeds
  // the available space entirely. Bounding `max-height` here (paired
  // with the stylesheet's own unconditional `overflow-y: auto` on this
  // class -- a no-op when content already fits, since no scrollbar shows
  // unless it doesn't) guarantees every action, including the danger
  // "Move to Trash" item at the very end, stays reachable by scrolling
  // within the panel rather than being pushed off-screen entirely.
  panel.style.maxHeight = `${viewport.innerHeight - ROW_MENU_VIEWPORT_MARGIN * 2}px`;
  const triggerRect = trigger.getBoundingClientRect();
  const panelRect = panel.getBoundingClientRect();
  const { left, top } = computeRowMenuPosition(
    triggerRect,
    panelRect,
    viewport.innerWidth,
    viewport.innerHeight,
    align,
  );
  panel.style.top = `${top}px`;
  panel.style.left = `${left}px`;
}

/** Reverts a closed panel to its stylesheet-defined (non-floating) position. */
export function unfloatRowMenuPanel(panel: RowMenuPanelLike | null): void {
  if (!panel) return;
  panel.style.position = "";
  panel.style.top = "";
  panel.style.left = "";
  panel.style.right = "";
  panel.style.maxWidth = "";
  panel.style.maxHeight = "";
}

/**
 * Finds (or creates, once) a shared top-level container appended directly to
 * `doc.body`, used to hold an opened row-menu panel while it's floated.
 *
 * The fixed/sticky action rail gives every row-menu trigger
 * `position: sticky`, which -- per the CSS positioning spec -- always
 * establishes its own stacking context, regardless of z-index. Left inside
 * that trigger's `<details>`, an opened panel's `z-index: 300` only wins
 * *within* that one low-level stacking context; a *different* row's sticky
 * trigger (rendered later in the document) can still paint over it. Moving
 * the panel out to a plain, unpositioned body-level container while it's
 * open sidesteps this entirely: the panel competes for stacking order at the
 * document root instead of trapped inside its own trigger's context.
 */
function ensureRowMenuPortal(doc: Document): HTMLElement {
  const existing = doc.querySelector<HTMLElement>(ROW_MENU_PORTAL_SELECTOR);
  if (existing) return existing;
  const portal = doc.createElement("div");
  portal.setAttribute("data-row-menu-portal", "");
  doc.body.appendChild(portal);
  return portal;
}

/**
 * Wires every `.row-action-menu` <details> found in `doc` -- the
 * per-note-row menu shell (Open/Move/etc, plain existing links/forms
 * this controller never touches), and the tree/drawer creation toolbar's
 * Move note control, an anchored
 * floating popover. Ellipsis/icon opening and keyboard activation
 * are native <details>/<summary> behavior and need no JS. This controller
 * owns:
 *
 * - floating the opened panel out of `.tree-nav__viewport`'s overflow
 *   clipping via fixed positioning computed from the trigger's real
 *   position (see `floatRowMenuPanel`), and reverting it on close;
 * - moving the panel into a shared body-level portal while open, and back to
 *   its original position on close, so it always paints above every row's
 *   sticky action-rail trigger (see `ensureRowMenuPortal`);
 * - an explicit `[data-row-menu-cancel]` button inside a panel (e.g. Move
 *   note's Cancel), where present, closes that menu and returns focus to its
 *   trigger -- a no-op for panels with no such element;
 * - right-click on the owning row (its nearest `[data-note-id]` ancestor)
 *   opens the menu, suppressing the native browser context menu;
 * - opening one row menu closes every other open row menu;
 * - outside-click and Escape dismiss an open menu, mirroring
 *   `initAccountMenus` in app.ts (Escape also restores focus to the menu's
 *   own <summary>);
 * - for menus rendered inside the narrow drawer, the existing drawer-close
 *   button and backdrop (mirroring tree-drag.ts's closeTriggers) close every
 *   open menu, as does any window resize.
 */
export function initRowActionMenusDocument(
  doc: Document = document,
  windowLike: RowMenuWindowLike = typeof window !== "undefined"
    ? (window as unknown as RowMenuWindowLike)
    : noopWindowLike,
): boolean {
  const menus = Array.from(
    doc.querySelectorAll<HTMLDetailsElement>(ROW_ACTION_MENU_SELECTOR),
  );
  if (menus.length === 0) {
    return false;
  }

  const portal = ensureRowMenuPortal(doc);
  const panelsByMenu = new Map<HTMLDetailsElement, HTMLElement | null>();

  const closeAll = (except: HTMLDetailsElement | null): void => {
    for (const menu of menus) {
      if (menu === except) continue;
      if (menu.open) {
        menu.open = false;
      }
    }
  };

  let anyInDrawer = false;

  for (const menu of menus) {
    if (menu.dataset.rowMenuInitialized === "true") {
      continue;
    }

    const panel = menu.querySelector<HTMLElement>(ROW_MENU_PANEL_SELECTOR);
    const panelHome = panel?.parentElement ?? null;
    panelsByMenu.set(menu, panel);
    // Toolbar popovers anchored near the left end of a row (Move note, New
    // Folder) set `data-menu-align="left"` in the template; every other
    // menu (folder/note row actions) keeps the default right anchor.
    const align: RowMenuAlign =
      menu.dataset.menuAlign === "left" ? "left" : "right";

    // Present only in panels that need an explicit dismiss control (e.g. the
    // Move note popover's Cancel button); an empty list for every other
    // menu, which has no such element. A single panel can contain more than one
    // of these (the active-note overflow's New note, New folder, and Move
    // disclosures each have their own) -- `querySelectorAll` + wiring every
    // match is required here; the prior single `querySelector` only ever
    // bound the first Cancel button found in the panel, silently leaving
    // every other one inert.
    const cancelButtons = panel
      ? Array.from(
          panel.querySelectorAll<HTMLElement>("[data-row-menu-cancel]"),
        )
      : [];
    for (const cancelButton of cancelButtons) {
      cancelButton.addEventListener("click", () => {
        menu.open = false;
        menu.querySelector<HTMLElement>("summary")?.focus();
      });
    }

    // Present only in the active-note overflow
    // panel; a no-op for every other row-action menu. Closing `menu`
    // here (rather than leaving focus on its `<summary>`, unlike the
    // Cancel button above) reuses the existing close-path "toggle"
    // listener below to reset any open nested disclosure, then moves
    // focus into the note's own title input -- the single existing
    // autosave-backed field remains the only rename mechanism, so this
    // never creates a second title field or submits anything itself.
    const renameTrigger = panel?.querySelector<HTMLElement>(
      "[data-note-rename-trigger]",
    );
    renameTrigger?.addEventListener("click", () => {
      menu.open = false;
      const noteForm = doc.querySelector<HTMLElement>("[data-note-form]");
      const titleInputId = noteForm?.dataset.noteTitleInputId;
      const titleInput = titleInputId
        ? (doc.getElementById(titleInputId) as TitleFocusTargetLike | null)
        : null;
      titleInput?.focus();
      titleInput?.select();
    });

    // Any nested `.tree-nav__action` disclosure
    // (folder Rename, note Rename, note Move) inside this panel changes
    // the panel's own natural height when it opens or closes. Re-running
    // `floatRowMenuPanel` -- the exact same call the outer menu's own
    // open path uses below -- re-measures and repositions/re-clamps the
    // already-floated panel against that new height, but only while this
    // outer menu is actually open (a nested disclosure's `open` attribute
    // can also be server-rendered or reset programmatically while the
    // outer menu is closed; repositioning a panel that isn't currently
    // floated would be meaningless).
    const refloat = (): void => {
      if (!menu.open) {
        return;
      }
      floatRowMenuPanel(
        menu.querySelector<HTMLElement>("summary"),
        panel,
        {
          innerWidth: windowLike.innerWidth,
          innerHeight: windowLike.innerHeight,
        },
        align,
      );
    };
    const actionDisclosures = panel
      ? Array.from(
          panel.querySelectorAll<HTMLDetailsElement>(".tree-nav__action"),
        )
      : [];
    for (const action of actionDisclosures) {
      action.addEventListener("toggle", refloat);
    }

    menu.addEventListener("toggle", () => {
      if (menu.open) {
        closeAll(menu);
        if (panel) {
          portal.appendChild(panel);
        }
        // Read `windowLike.innerWidth`/`innerHeight` fresh here, at the
        // moment of opening, rather than once when `initRowActionMenusDocument`
        // first ran. A plain object captured at init time never updates when
        // the real viewport later resizes -- confirmed as the exact cause of
        // a real responsive-lifecycle defect: opening Recent notes or More
        // note actions after resizing from wide/medium down to narrow (with
        // no reload) positioned the panel using the *original*, now-stale
        // viewport width, running it off the actual narrow viewport, even
        // though the same panel opened correctly after a fresh page load at
        // that same narrow width. A reload re-ran this function and
        // recaptured the (now correct) dimensions, masking the bug as
        // "works after reload." Reading live values on every open fixes it
        // for every `.row-action-menu` at once (Recent notes, More note
        // actions, tree row menus, Move note, New Folder), since they all
        // share this one toggle handler.
        floatRowMenuPanel(
          menu.querySelector<HTMLElement>("summary"),
          panel,
          {
            innerWidth: windowLike.innerWidth,
            innerHeight: windowLike.innerHeight,
          },
          align,
        );
      } else {
        unfloatRowMenuPanel(panel);
        if (panel && panelHome) {
          panelHome.appendChild(panel);
        }
        // The
        // standalone tree-toolbar New Folder popover is itself the
        // closing `menu` here, not a nested action inside someone else's
        // panel -- reset it the same way on its own close path.
        if (menu.matches(NEW_FOLDER_ACTION_SELECTOR)) {
          resetNewFolderForm(menu);
        }
        // Reset any
        // nested Rename/Move disclosure back to collapsed whenever this
        // outer menu closes -- via any existing close path (outside
        // click, Escape, the trigger toggling closed again, or another
        // row menu opening and closing this one via `closeAll`), so the
        // menu always reopens in its compact default state rather than
        // remembering a previously expanded disclosure. Setting `.open`
        // on an already-closed `<details>` is a no-op (no redundant
        // 'toggle' dispatch), and closing a `<details>` never submits its
        // nested form.
        for (const action of actionDisclosures) {
          if (action.open) {
            action.open = false;
          }
          if (action.matches(NEW_FOLDER_ACTION_SELECTOR)) {
            resetNewFolderForm(action);
          }
        }
      }
    });

    const row = menu.closest<HTMLElement>("[data-note-id]");
    row?.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      closeAll(menu);
      menu.open = true;
    });

    if (menu.closest("[data-drawer]")) {
      anyInDrawer = true;
    }

    menu.dataset.rowMenuInitialized = "true";
  }

  doc.addEventListener("click", (event) => {
    const target = event.target as Node;
    for (const menu of menus) {
      if (!menu.open) continue;
      if (menu.contains(target)) continue;
      const panel = panelsByMenu.get(menu);
      if (panel?.contains(target)) continue;
      menu.open = false;
    }
  });

  doc.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    for (const menu of menus) {
      if (!menu.open) continue;
      menu.open = false;
      menu.querySelector<HTMLElement>("summary")?.focus();
    }
  });

  // An open panel is
  // floated with geometry computed for the viewport size at the moment it
  // opened -- a resize mid-open (e.g. wide to narrow) leaves it positioned
  // for a viewport that no longer exists, same root cause as the stale
  // `viewport` object removed above. Rather than repositioning it in place
  // (which would require re-running the same measure-and-clamp logic the
  // *next* open already does, duplicating it here), this closes it cleanly
  // and restores focus to its trigger -- mirroring the Escape handler
  // above exactly, so "resize while open" and "press Escape" leave the
  // page in the identical, already-tested state. The next open then uses
  // the live `windowLike.innerWidth`/`innerHeight` fix above and positions
  // correctly at whatever width the resize landed on.
  windowLike.addEventListener("resize", () => {
    for (const menu of menus) {
      if (!menu.open) continue;
      menu.open = false;
      menu.querySelector<HTMLElement>("summary")?.focus();
    }
  });

  if (anyInDrawer) {
    const drawerClose = doc.querySelector<HTMLElement>("[data-drawer-close]");
    drawerClose?.addEventListener("click", () => closeAll(null));
    const backdrop = doc.querySelector<HTMLElement>("[data-drawer-backdrop]");
    backdrop?.addEventListener("click", () => closeAll(null));
  }

  return true;
}
