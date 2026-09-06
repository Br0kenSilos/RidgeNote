export const TREE_SCROLL_STORAGE_KEY_WIDE = "ridgenote.tree.scrollTop.wide";
export const TREE_SCROLL_STORAGE_KEY_DRAWER = "ridgenote.tree.scrollTop.drawer";

const TREE_CONTAINER_SELECTOR = ".tree-nav";
const VIEWPORT_SELECTOR = ".tree-nav__viewport";
const TREE_SCOPE_ATTRIBUTE = "data-tree-scope";
const DRAWER_SCOPE_VALUE = "drawer";

export interface TreeScrollPositionStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function safeSetItem(
  storage: TreeScrollPositionStorageLike | undefined,
  key: string,
  value: string,
): void {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(key, value);
  } catch {
    // Ignore browser-local persistence failures and keep the workspace usable.
  }
}

/**
 * A stale/malformed stored value (missing, non-numeric, negative) always
 * degrades to `null` -- never an error, never a fallback that scrolls
 * anywhere unexpected. A browser silently clamps an out-of-range
 * `scrollTop` assignment on its own, so no upper-bound check is needed
 * here.
 */
export function parseStoredScrollTop(rawValue: string | null): number | null {
  if (rawValue === null || rawValue.trim() === "") {
    return null;
  }
  const parsed = Number(rawValue);
  if (!Number.isFinite(parsed) || parsed < 0) {
    return null;
  }
  return parsed;
}

/**
 * Distinguishes
 * the narrow drawer's `.tree-nav` copy (always `data-tree-scope="drawer"`,
 * see `_nav_drawer.html`) from the wide desktop copy, whose own
 * `data-tree-scope` value varies by page (`tree`, `allnotes`, `trash`) --
 * so "not drawer" is the correct wide-copy test, not any specific value.
 * Desktop and drawer intentionally use separate storage keys so neither
 * copy's scroll position can overwrite the other's.
 */
export function treeScrollStorageKeyForContainer(container: {
  getAttribute(name: string): string | null;
}): string {
  return container.getAttribute(TREE_SCOPE_ATTRIBUTE) === DRAWER_SCOPE_VALUE
    ? TREE_SCROLL_STORAGE_KEY_DRAWER
    : TREE_SCROLL_STORAGE_KEY_WIDE;
}

export interface TreeScrollPositionViewportLike {
  addEventListener(type: "scroll", listener: () => void): void;
  scrollTop: number;
}

export interface TreeScrollPositionContainerLike {
  dataset: { scrollCaptureInitialized?: string };
  getAttribute(name: string): string | null;
  querySelector<T = TreeScrollPositionViewportLike>(selector: string): T | null;
}

export interface TreeScrollPositionDocumentLike {
  querySelectorAll<T = TreeScrollPositionContainerLike>(
    selector: string,
  ): ArrayLike<T>;
}

/**
 * Live-mirrors each already-rendered tree copy's own scroll position into
 * `sessionStorage` on every `scroll` event, independently for the wide
 * desktop tree and the narrow drawer. `sessionStorage`, not
 * `localStorage`: this is transient, in-session viewport state (like
 * scroll restoration on browser back), not a deliberate, lasting user
 * preference the way tree-collapse/folder-expansion are -- it should
 * reset for a fresh tab/session rather than accumulate indefinitely.
 *
 * Deliberately not tied to specific link clicks (tree note, Home, Recent
 * switcher, etc.) or to `beforeunload`: capturing continuously on the
 * viewport's own `scroll` event means whichever navigation path the user
 * actually takes -- any of those links, or browser back/forward -- already
 * has the last real scroll position stored, with no per-link-type special
 * casing needed. Writes are direct (no throttling): a small note tree's
 * scroll events are not frequent enough for a single `sessionStorage.
 * setItem` call per event to be a meaningful cost.
 */
export function initTreeScrollPositionCaptureDocument(
  doc: TreeScrollPositionDocumentLike = typeof document !== "undefined"
    ? (document as unknown as TreeScrollPositionDocumentLike)
    : { querySelectorAll: () => [] },
  storage: TreeScrollPositionStorageLike | undefined = typeof window !==
  "undefined"
    ? window.sessionStorage
    : undefined,
): boolean {
  const containers = Array.from(
    doc.querySelectorAll<TreeScrollPositionContainerLike>(
      TREE_CONTAINER_SELECTOR,
    ),
  );
  let initializedAny = false;

  for (const container of containers) {
    if (container.dataset.scrollCaptureInitialized === "true") {
      continue;
    }
    const viewport =
      container.querySelector<TreeScrollPositionViewportLike>(
        VIEWPORT_SELECTOR,
      );
    if (!viewport) {
      continue;
    }
    const key = treeScrollStorageKeyForContainer(container);
    viewport.addEventListener("scroll", () => {
      safeSetItem(storage, key, String(viewport.scrollTop));
    });
    container.dataset.scrollCaptureInitialized = "true";
    initializedAny = true;
  }

  return initializedAny;
}
