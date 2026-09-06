import { refreshTreeTitleTooltipForLabel } from "./tree-title-truncation";

const NOTE_DETAIL_URL_ID_PATTERN = /\/notes\/(\d+)\/?(?:$|[?#])/;

const NOTE_ROW_SELECTOR = "li.tree-nav__note-item";
const PIN_MARKER_SELECTOR = ".tree-nav__pin-marker";
const ROW_TITLE_LABEL_SELECTOR = ".tree-nav__label";
const NOTE_ID_ATTRIBUTE = "data-note-id";

/**
 * Extracts the numeric note ID from an existing `notes:detail` URL such as
 * `/notes/42/`, so the current note's ID never needs a new template
 * attribute -- it is derived from markup already rendered on the page.
 */
export function parseNoteIdFromDetailUrl(detailUrl: string): string | null {
  const match = detailUrl.match(NOTE_DETAIL_URL_ID_PATTERN);
  return match ? match[1] : null;
}

/**
 * Finds the visible title label for `noteId` in every already-rendered tree
 * copy under `root` (e.g. the note-detail wide tree and the narrow drawer).
 * Scoped to `.tree-nav__note-item` rows specifically so folder-title labels,
 * which share the same `.tree-nav__label` class, are never matched.
 */
export function findTreeNoteTitleLabels(
  root: ParentNode,
  noteId: string,
): HTMLElement[] {
  const items = Array.from(
    root.querySelectorAll<HTMLElement>("li.tree-nav__note-item[data-note-id]"),
  );
  const labels: HTMLElement[] = [];
  for (const item of items) {
    if (item.getAttribute("data-note-id") !== noteId) {
      continue;
    }
    const label = item.querySelector<HTMLElement>(
      ".tree-nav__note-link .tree-nav__label",
    );
    if (label) {
      labels.push(label);
    }
  }
  return labels;
}

function isRowPinned(row: Element): boolean {
  return row.querySelector(PIN_MARKER_SELECTOR) !== null;
}

function rowTitleKey(row: Element): string {
  return (
    row.querySelector(ROW_TITLE_LABEL_SELECTOR)?.textContent ?? ""
  ).toLowerCase();
}

function rowNoteIdNumber(row: Element): number {
  return Number(row.getAttribute(NOTE_ID_ATTRIBUTE));
}

/**
 * Mirrors `TREE_NOTE_DISPLAY_ORDER` (`notes/services.py`):
 * pinned rows before unpinned, then case-insensitive title,
 * then ID descending. A lightweight, read-only client-side echo of that
 * same comparison -- used only to reposition one already-rendered row
 * immediately after its own title changes client-side. The server's own
 * render (any full page load or navigation) remains the sole source of
 * truth for the actual order; this never persists or overrides it.
 */
function compareTreeNoteRows(a: Element, b: Element): number {
  const pinnedA = isRowPinned(a);
  const pinnedB = isRowPinned(b);
  if (pinnedA !== pinnedB) {
    return pinnedA ? -1 : 1;
  }
  const titleA = rowTitleKey(a);
  const titleB = rowTitleKey(b);
  if (titleA !== titleB) {
    return titleA < titleB ? -1 : 1;
  }
  return rowNoteIdNumber(b) - rowNoteIdNumber(a);
}

/**
 * Moves `row` to its correct position among its sibling note rows in the
 * same list (a folder's note list, or the Unfiled list) after a rename
 * has changed its title client-side. A pure DOM move (`insertBefore`) --
 * no row is destroyed or recreated, so the active row's selection state,
 * editor/title focus, and every sibling folder's expansion state are all
 * left completely untouched. Safe to call when `row` is already in the
 * correct position (a same-position `insertBefore` is a no-op) or has no
 * parent yet.
 */
export function repositionTreeNoteRow(row: Element): void {
  const parent = row.parentElement;
  if (!parent) {
    return;
  }
  let target: Element | null = null;
  for (const sibling of Array.from(parent.children)) {
    if (sibling === row || !sibling.matches(NOTE_ROW_SELECTOR)) {
      continue;
    }
    if (compareTreeNoteRows(row, sibling) < 0) {
      target = sibling;
      break;
    }
  }
  parent.insertBefore(row, target);
}

/**
 * Live, forward-only sync of one note's visible tree title after its own
 * successful save/autosave response. Updates every matching row in every
 * already-rendered tree copy on the page and leaves every other row --
 * including other notes' rows and folder rows -- untouched. Safe to call
 * with an unchanged title (a no-op) or when no matching row exists yet.
 *
 * Also re-measures that row's truncated-title
 * tooltip state right after the text actually changes -- the one place a
 * tree title's text can change outside a full page render, so this is
 * `tree-title-truncation.ts`'s own hook for staying in sync with it,
 * rather than that module adding a `MutationObserver` of its own.
 *
 * Also repositions each updated row
 * (via `repositionTreeNoteRow`) immediately after its title text changes
 * -- a rename can move a note to a different point in the now-alphabetical
 * tree order, and nothing else re-sorts an already-rendered row on the
 * page.
 */
export function syncTreeNoteTitle(
  root: ParentNode,
  noteId: string,
  title: string,
): void {
  for (const label of findTreeNoteTitleLabels(root, noteId)) {
    if (label.textContent !== title) {
      label.textContent = title;
      refreshTreeTitleTooltipForLabel(label);
      const row = label.closest(NOTE_ROW_SELECTOR);
      if (row) {
        repositionTreeNoteRow(row);
      }
    }
  }
}
