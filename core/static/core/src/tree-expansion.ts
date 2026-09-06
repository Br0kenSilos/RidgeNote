export const TREE_EXPANDED_FOLDERS_STORAGE_KEY =
  "ridgenote.tree.expandedFolders";

export interface TreeExpansionStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

function safeGetItem(
  storage: TreeExpansionStorageLike | undefined,
  key: string,
): string | null {
  if (!storage) {
    return null;
  }
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function safeSetItem(
  storage: TreeExpansionStorageLike | undefined,
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
 * Parses the stored expanded-folder-ID set. Any invalid JSON, a value that
 * isn't a JSON array, or non-string entries all degrade to an empty set --
 * never an error, never a fallback that expands everything.
 */
export function parseStoredExpandedFolderIds(
  rawValue: string | null,
): Set<string> {
  if (!rawValue) {
    return new Set();
  }
  try {
    const parsed: unknown = JSON.parse(rawValue);
    if (!Array.isArray(parsed)) {
      return new Set();
    }
    return new Set(
      parsed.filter((value): value is string => typeof value === "string"),
    );
  } catch {
    return new Set();
  }
}

export function serializeExpandedFolderIds(ids: ReadonlySet<string>): string {
  return JSON.stringify(Array.from(ids));
}

interface FolderDetailsEntry {
  container: Element;
  details: HTMLDetailsElement;
  folderId: string;
}

function collectFolderDetailsEntries(doc: Document): FolderDetailsEntry[] {
  const entries: FolderDetailsEntry[] = [];
  const containers = Array.from(doc.querySelectorAll<HTMLElement>(".tree-nav"));

  for (const container of containers) {
    const folderLis = Array.from(
      container.querySelectorAll<HTMLElement>(
        "li.tree-nav__folder[data-folder-id]",
      ),
    );
    for (const li of folderLis) {
      const folderId = li.dataset.folderId;
      const details = li.querySelector<HTMLDetailsElement>(
        "details.tree-nav__folder-details",
      );
      if (folderId && details) {
        entries.push({ container, details, folderId });
      }
    }
  }

  return entries;
}

/**
 * Restores persisted per-folder expand/collapse state on every full-page
 * render, then keeps it in sync as the user toggles folders -- across both
 * the wide tree and narrow drawer copies, which are two independent
 * `.tree-nav` DOM instances sharing one persisted set (see
 * `_workspace_tree_rail.html`/`_nav_drawer.html`).
 *
 * The current note's own folder may still render `open` from the server
 * (`_tree_nav.html`'s `folder.id == note.folder_id` condition) regardless
 * of what's stored -- this only ever *adds* to that starting state for
 * folders present in the stored set, never removes it, so an existing
 * stored expansion is never lost by a navigation that happens to also
 * auto-open a different folder.
 *
 * Storage keys are `folder.id` values already present in each folder row's
 * `data-folder-id` attribute; the "Unfiled" group has no ID and is left
 * exactly as it already behaves (always open), out of scope here. Any
 * stored ID with no matching folder in the freshly rendered tree is
 * dropped as part of this same pass -- never treated as an error.
 */
export function initTreeExpansionDocument(
  doc: Document = document,
  storage: TreeExpansionStorageLike | undefined = typeof window !== "undefined"
    ? window.localStorage
    : undefined,
): boolean {
  const containers = Array.from(doc.querySelectorAll<HTMLElement>(".tree-nav"));
  const uninitialized = containers.filter(
    (container) => container.dataset.expansionInitialized !== "true",
  );
  if (uninitialized.length === 0) {
    return false;
  }

  const entries = collectFolderDetailsEntries(doc);
  const storedIds = parseStoredExpandedFolderIds(
    safeGetItem(storage, TREE_EXPANDED_FOLDERS_STORAGE_KEY),
  );

  for (const entry of entries) {
    if (storedIds.has(entry.folderId)) {
      entry.details.open = true;
    }
  }

  const presentIds = new Set(entries.map((entry) => entry.folderId));
  const currentIds = new Set(
    Array.from(storedIds).filter((id) => presentIds.has(id)),
  );
  if (currentIds.size !== storedIds.size) {
    safeSetItem(
      storage,
      TREE_EXPANDED_FOLDERS_STORAGE_KEY,
      serializeExpandedFolderIds(currentIds),
    );
  }

  let suppressSync = false;

  for (const entry of entries) {
    entry.details.addEventListener("toggle", () => {
      if (suppressSync) {
        return;
      }

      // A filter session temporarily forces matching folders open and
      // restores them on clear -- neither is a deliberate user choice
      // about this folder's expansion, so both are ignored here rather
      // than persisted. This reads the filter input's live value only;
      // it never changes filter behavior itself.
      const filterInput = entry.container.querySelector<HTMLInputElement>(
        "[data-tree-filter-input]",
      );
      if (filterInput && filterInput.value.trim() !== "") {
        return;
      }

      if (entry.details.open) {
        currentIds.add(entry.folderId);
      } else {
        currentIds.delete(entry.folderId);
      }
      safeSetItem(
        storage,
        TREE_EXPANDED_FOLDERS_STORAGE_KEY,
        serializeExpandedFolderIds(currentIds),
      );

      suppressSync = true;
      for (const other of entries) {
        if (other === entry || other.folderId !== entry.folderId) {
          continue;
        }
        if (other.details.open !== entry.details.open) {
          other.details.open = entry.details.open;
        }
      }
      suppressSync = false;
    });
  }

  for (const container of uninitialized) {
    container.dataset.expansionInitialized = "true";
  }

  return true;
}
