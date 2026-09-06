const TREE_CONTAINER_SELECTOR = ".tree-nav";
const VIEWPORT_SELECTOR = ".tree-nav__viewport";
const ACTIVE_ROW_SELECTOR = ".tree-nav__row--current";

export interface TreeScrollRectLike {
  bottom: number;
  top: number;
}

export interface TreeScrollRowLike {
  getBoundingClientRect(): TreeScrollRectLike;
}

export interface TreeScrollViewportLike {
  getBoundingClientRect(): TreeScrollRectLike;
  scrollTop: number;
}

/**
 * The minimal `scrollTop` delta needed to bring `row` fully within
 * `viewport`'s visible bounds -- nearest-edge only, never centering.
 * Positive when `row` is below the visible area (scroll down to reveal
 * its bottom), negative when above it (scroll up to reveal its top), and
 * `0` when `row` is already fully visible (no scroll at all).
 */
export function computeNearestEdgeScrollDelta(
  viewport: TreeScrollViewportLike,
  row: TreeScrollRowLike,
): number {
  const viewportRect = viewport.getBoundingClientRect();
  const rowRect = row.getBoundingClientRect();

  if (rowRect.top < viewportRect.top) {
    return rowRect.top - viewportRect.top;
  }
  if (rowRect.bottom > viewportRect.bottom) {
    return rowRect.bottom - viewportRect.bottom;
  }
  return 0;
}

/**
 * Scrolls `viewport` only enough to reveal `row`, using nearest-edge
 * behavior -- a no-op when `row` is `null` or already fully visible.
 */
export function revealActiveRowInViewport(
  viewport: TreeScrollViewportLike,
  row: TreeScrollRowLike | null,
): void {
  if (!row) {
    return;
  }
  const delta = computeNearestEdgeScrollDelta(viewport, row);
  if (delta !== 0) {
    viewport.scrollTop += delta;
  }
}

export interface TreeScrollElementLike
  extends TreeScrollViewportLike, TreeScrollRowLike {
  offsetParent: unknown;
  querySelector<T = TreeScrollElementLike>(selector: string): T | null;
}

export interface TreeScrollDocumentLike {
  querySelectorAll<T = TreeScrollElementLike>(selector: string): ArrayLike<T>;
  querySelector<T = TreeScrollClickTargetLike>(selector: string): T | null;
}

export interface TreeScrollClickTargetLike {
  addEventListener(type: "click", listener: () => void): void;
}

/**
 * Every note-tree
 * navigation is a full server-rendered page load (there is no
 * client-side routing), so the active row is always correctly present
 * and selected on arrival -- but the tree's own scrolling element
 * (`.tree-nav__viewport`) always starts at `scrollTop: 0` on a fresh
 * page like any other scrollable element, with nothing to reveal a row
 * that happens to render below the fold. Alphabetical
 * ordering (a note's tree position doesn't track how recently it was
 * opened) makes this more likely to matter in practice.
 *
 * Runs once per already-rendered `.tree-nav` copy (wide desktop tree,
 * narrow drawer), and only when that copy is actually visible
 * (`offsetParent !== null` -- false for a collapsed desktop tree pane or
 * a closed narrow drawer, both of which this deliberately leaves alone
 * rather than forcing open). Must run after `initTreeShellDocument` and
 * `initTreeExpansionDocument` have applied collapse/expansion state, so
 * the active row's real rendered position already reflects it.
 */
export function initTreeActiveRowScrollDocument(
  doc: TreeScrollDocumentLike = typeof document !== "undefined"
    ? (document as unknown as TreeScrollDocumentLike)
    : { querySelector: () => null, querySelectorAll: () => [] },
): boolean {
  const containers = Array.from(
    doc.querySelectorAll<TreeScrollElementLike>(TREE_CONTAINER_SELECTOR),
  );
  let revealedAny = false;

  for (const container of containers) {
    const viewport =
      container.querySelector<TreeScrollElementLike>(VIEWPORT_SELECTOR);
    if (!viewport || viewport.offsetParent === null) {
      continue;
    }
    const row =
      viewport.querySelector<TreeScrollElementLike>(ACTIVE_ROW_SELECTOR);
    if (!row) {
      continue;
    }
    revealActiveRowInViewport(viewport, row);
    revealedAny = true;
  }

  return revealedAny;
}

const DRAWER_TOGGLE_SELECTOR = "[data-drawer-toggle]";

/**
 * A closed
 * narrow drawer's `.tree-nav__viewport` is skipped by
 * `initTreeActiveRowScrollDocument` at page load (`offsetParent === null`
 * -- deliberately never forced open just to reveal a row). Once the user
 * opens the drawer later, that same active row may still be outside the
 * now-visible viewport, so this re-runs the exact same reveal pass at
 * that point -- cheap and idempotent, since every other already-visible
 * copy just recomputes a `0` delta and is untouched.
 *
 * Relies on `DrawerController`'s own click listener (`tree-shell.ts`,
 * registered earlier during `initTreeShellDocument`/
 * `initNarrowDrawerDocument`) having already run first: listeners on the
 * same element/event fire in registration order, so by the time this
 * listener runs, `hidden` is already `false` and `offsetParent` already
 * reflects it -- no extra timing hook needed.
 */
export function initDrawerActiveRowRevealOnOpenDocument(
  doc: TreeScrollDocumentLike = typeof document !== "undefined"
    ? (document as unknown as TreeScrollDocumentLike)
    : { querySelector: () => null, querySelectorAll: () => [] },
): boolean {
  const toggle = doc.querySelector<TreeScrollClickTargetLike>(
    DRAWER_TOGGLE_SELECTOR,
  );
  if (!toggle) {
    return false;
  }
  toggle.addEventListener("click", () => {
    initTreeActiveRowScrollDocument(doc);
  });
  return true;
}
