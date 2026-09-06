import "../css/app.css";

import { initDeleteConfirmDialog } from "./delete-confirm";
import { initGlobalSearchPanel } from "./global-search";
import { initNoteEditorDocument } from "./note-editor";
import { initQuickSwitchDocument } from "./quick-switch";
import { initRowActionMenusDocument } from "./tree-context-menu";
import { initTreeDragAndDropDocument } from "./tree-drag";
import {
  initDrawerActiveRowRevealOnOpenDocument,
  initTreeActiveRowScrollDocument,
} from "./tree-active-row-scroll";
import { initTreeExpansionDocument } from "./tree-expansion";
import { initTreeFilterDocument } from "./tree-filter";
import { initTreeScrollPositionCaptureDocument } from "./tree-scroll-position";
import { initFolderActionAutofocusDocument } from "./tree-folder-actions";
import { initLibraryRestoreDocument } from "./library-restore";
import { initPreferencesTimezoneDocument } from "./preferences-timezone";
import { initNarrowDrawerDocument, initTreeShellDocument } from "./tree-shell";
import { initTooltipPlacementDocument } from "./tooltip-placement";
import { initNewFolderFormsDocument } from "./new-folder-form";
import { initTagErrorDismissDocument } from "./tag-error-dismiss";
import { initTagNameUppercaseDocument } from "./tag-name-uppercase";
import { initTagSuggestionsDocument } from "./tag-suggestions";
import { initTagManagementDocument } from "./tag-management";
import { initFullSearchDocument } from "./full-search";
import { initThemeQuickSelectorDocument } from "./theme";
import { initWorkspaceHeaderHeightDocument } from "./workspace-header-height";
import { initWorkspaceTreeRowTopDocument } from "./workspace-tree-row-top";
import { initNoteControlsFrameDocument } from "./note-controls-frame";
import { initRestoreConfirmDialog } from "./restore-confirm";
import { initTagChipTruncation } from "./tag-chip-truncation";
import { initTreeTitleTooltipDocument } from "./tree-title-truncation";
import { initInvitationCopyDocument } from "./invitation-copy";
import { initSetupMethodToggleDocument } from "./setup-method-toggle";
import { initPaletteLabDocument } from "./palette-lab";

export function staticReadyText(): string {
  return "Static assets loaded";
}

const DEFAULT_HELP_TOPIC = "help-introduction";

/**
 * Shows exactly one `[data-help-topic]` section inside `dialog` and
 * hides the rest via the native `hidden` attribute (never a
 * visual-only concealment), syncs `aria-current` on the matching
 * `[data-help-topic-link]`(s), and resets the article pane's scroll
 * position. Scoped entirely to `dialog` -- the canonical `/help/`
 * page never has a dialog in its DOM (see `initHelpPanel()` below), so
 * this can never run there; the canonical page always renders every
 * topic, unhidden, as one complete linear document. Returns false
 * (making no change) for an unrecognized topic id, so a stray/unknown
 * link falls back to real navigation instead of trapping the user.
 */
function showHelpTopic(dialog: HTMLDialogElement, topicId: string): boolean {
  const topics = dialog.querySelectorAll<HTMLElement>("[data-help-topic]");

  // Check for a match *before* touching anything: an unrecognized
  // topicId must leave the currently-shown topic completely
  // untouched, not hide everything and then discover there was
  // nothing to show in its place.
  let matched = false;
  topics.forEach((section) => {
    if (section.dataset.helpTopic === topicId) matched = true;
  });
  if (!matched) return false;

  topics.forEach((section) => {
    section.hidden = section.dataset.helpTopic !== topicId;
  });

  dialog
    .querySelectorAll<HTMLAnchorElement>("[data-help-topic-link]")
    .forEach((link) => {
      if (link.hash.slice(1) === topicId) {
        link.setAttribute("aria-current", "true");
      } else {
        link.removeAttribute("aria-current");
      }
    });

  dialog.querySelector<HTMLElement>(".help-article")?.scrollTo(0, 0);

  return true;
}

/**
 * Wires every `[data-help-topic-link]` inside `dialog` (TOC entries
 * and topic partials' own inline cross-references -- both real
 * `/help/#<id>` links, never JS-only controls) to switch the selected
 * topic in place rather than navigating away, then selects the
 * default topic so the dialog opens on Getting Started even before
 * its first `showModal()`. An unrecognized fragment (none exist today,
 * but a future stray/mistyped link should degrade safely) is left to
 * navigate normally instead of being silently swallowed.
 */
function initHelpTopics(dialog: HTMLDialogElement): void {
  dialog
    .querySelectorAll<HTMLAnchorElement>("[data-help-topic-link]")
    .forEach((link) => {
      link.addEventListener("click", (event) => {
        const topicId = link.hash.slice(1);
        if (topicId && showHelpTopic(dialog, topicId)) {
          event.preventDefault();
        }
      });
    });

  showHelpTopic(dialog, DEFAULT_HELP_TOPIC);
}

export function initHelpPanel(doc: Document): void {
  const dialog = doc.querySelector<HTMLDialogElement>("#help-panel");
  // `[data-help-toggle]` triggers are real
  // `<a href="/help/">` links (no-JS fallback). When the dialog is
  // absent -- e.g. the canonical `/help/` page itself suppresses it via
  // `base.html`'s `{% block help_dialog %}` -- no click listener is
  // attached at all, so the browser's normal link navigation proceeds
  // completely untouched. `preventDefault()` is only ever reachable
  // from inside a handler that requires `dialog` to exist.
  if (!dialog) return;

  initHelpTopics(dialog);

  let restoreFocusTarget: HTMLElement | null = null;

  doc.querySelectorAll<HTMLElement>("[data-help-toggle]").forEach((trigger) => {
    trigger.addEventListener("click", (event) => {
      event.preventDefault();
      const inDrawer = trigger.closest("[data-drawer]") !== null;
      if (inDrawer) {
        // Delegate full drawer dismissal to the existing close button so
        // tree-shell handles all cleanup: inert, scroll lock, backdrop, aria-expanded.
        const drawerCloseBtn = doc.querySelector<HTMLElement>(
          "[data-drawer-close]",
        );
        drawerCloseBtn?.click();
        // After drawer dismissal the drawer trigger is the correct focus target,
        // not the now-hidden in-drawer Help button.
        restoreFocusTarget =
          doc.querySelector<HTMLElement>("[data-drawer-toggle]") ?? trigger;
      } else {
        restoreFocusTarget = trigger;
      }
      doc.querySelectorAll<HTMLElement>("[data-help-toggle]").forEach((t) => {
        t.setAttribute("aria-expanded", "true");
      });
      // Every plain "?" open starts on Getting Started, regardless of
      // whatever topic was last selected before the dialog closed.
      showHelpTopic(dialog, DEFAULT_HELP_TOPIC);
      dialog.showModal();
    });
  });

  const closeBtn = dialog.querySelector<HTMLElement>("[data-help-close]");
  closeBtn?.addEventListener("click", () => {
    dialog.close();
  });

  // Handles both explicit close-button closure and native Escape (cancel → close).
  // No document-level keydown handler is added; Escape is owned by the native dialog.
  dialog.addEventListener("close", () => {
    doc.querySelectorAll<HTMLElement>("[data-help-toggle]").forEach((t) => {
      t.setAttribute("aria-expanded", "false");
    });
    restoreFocusTarget?.focus();
    restoreFocusTarget = null;
  });
}

// Manages the account menu's own outside-click/Escape dismissal. A native
// <details> element, so focus restoration targets the contained <summary>
// generically rather than an account-menu-specific class.
//
// This function's own outside-click
// check (`menu.contains(event.target)`) assumes the menu's panel content
// stays a real DOM descendant of the <details> element -- untrue for any
// `.row-action-menu` once open, since `tree-context-menu.ts`'s
// `initRowActionMenusDocument` moves that panel into a shared body-level
// portal while it's open (see that file's own `ensureRowMenuPortal`
// comment for why). `.note-workspace__overflow` is a
// `.row-action-menu` and is fully, correctly managed by
// that other controller -- including portal-aware outside-click/Escape,
// via its own `panelsByMenu` tracking -- so it is deliberately excluded
// from this function's own scope, leaving it managed by exactly one,
// portal-aware controller.
export function initAccountMenus(doc: Document): void {
  const menus = doc.querySelectorAll<HTMLDetailsElement>(".account-menu");
  if (menus.length === 0) return;

  doc.addEventListener("click", (event) => {
    menus.forEach((menu) => {
      if (!menu.open) return;
      if (menu.contains(event.target as Node)) return;
      menu.open = false;
    });
  });

  doc.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    menus.forEach((menu) => {
      if (!menu.open) return;
      menu.open = false;
      menu.querySelector<HTMLElement>("summary")?.focus();
    });
  });
}

if (typeof document !== "undefined") {
  const statusElement = document.querySelector<HTMLElement>(
    "[data-ridgenote-static-check]",
  );

  if (statusElement) {
    statusElement.textContent = staticReadyText();
    statusElement.dataset.ready = "true";
  }

  initTreeShellDocument(document);
  initNarrowDrawerDocument(document);
  initWorkspaceHeaderHeightDocument(document);
  initWorkspaceTreeRowTopDocument(document);
  initNoteControlsFrameDocument(document);
  initTreeDragAndDropDocument(document);
  initTreeFilterDocument(document);
  initTreeExpansionDocument(document);
  // Must run after initTreeShellDocument (desktop collapse state) and
  // initTreeExpansionDocument (folder expansion state) immediately above,
  // so the active row's real rendered position already reflects both. By
  // this point `_tree_nav.html`'s own inline bootstrap script has already
  // restored each viewport's prior scroll position (see that template's
  // comment), so this only ever makes the minimum further adjustment
  // needed to keep the active row visible -- see tree-active-row-scroll.ts's
  // own comment for the full picture.
  initTreeActiveRowScrollDocument(document);
  // Re-runs the same reveal pass when the narrow drawer opens later,
  // since its viewport was skipped above while hidden.
  initDrawerActiveRowRevealOnOpenDocument(document);
  // Captures each tree viewport's own scroll position live (on its
  // `scroll` event) for the *next* full-page navigation to restore --
  // must be wired after the reveal pass above so a scroll it triggers on
  // this load isn't itself captured as "the" position before the user has
  // scrolled anything themselves. (In practice `scroll` assignment doesn't
  // synchronously fire a `scroll` event before this line runs anyway, but
  // this ordering keeps the intent unambiguous.)
  initTreeScrollPositionCaptureDocument(document);
  // Own portal-based tooltip, independent of initTooltipPlacementDocument
  // below (that module's CSS-`::after` placement-flip mechanism can't
  // apply here -- see app.css's own comment on `.tree-title-tooltip` for
  // why tree titles need real, JS-positioned `position: fixed` tooltips
  // instead). Placed near the other tree-structural inits for grouping.
  initTreeTitleTooltipDocument(document);
  // initFolderActionAutofocusDocument must be wired before
  // initRowActionMenusDocument: both attach a "toggle" listener to New
  // Folder's <details> (it's now also a .row-action-menu), and listeners on
  // the same element/event fire in registration order. The autofocus
  // listener needs to find the name input while it's still inside the
  // <details>, before the row-action-menu listener moves the panel (and
  // that input) into the shared portal.
  initFolderActionAutofocusDocument(document);
  initRowActionMenusDocument(document);
  initNewFolderFormsDocument(document);
  initThemeQuickSelectorDocument(document);
  initTooltipPlacementDocument(document);
  initNoteEditorDocument(document);
  initTagErrorDismissDocument(document);
  initTagNameUppercaseDocument(document);
  // Runs after initTagNameUppercaseDocument so a query typed into the Add
  // Tag input is already live-uppercased by the time this module's own
  // `input` listener reads it -- both listeners attach independently to
  // the same input and coexist cleanly (neither replaces the other).
  initTagSuggestionsDocument(document);
  initQuickSwitchDocument(document);
  initGlobalSearchPanel(document);
  initHelpPanel(document);
  initDeleteConfirmDialog(document);
  initTagManagementDocument(document);
  initFullSearchDocument(document);
  initRestoreConfirmDialog(document);
  initLibraryRestoreDocument(document);
  initPreferencesTimezoneDocument(document);
  initTagChipTruncation(document);
  initAccountMenus(document);
  initInvitationCopyDocument(document);
  initSetupMethodToggleDocument(document);
  initPaletteLabDocument(document);
}
