import { describe, expect, it } from "vitest";

import {
  TREE_EXPANDED_FOLDERS_STORAGE_KEY,
  initTreeExpansionDocument,
  parseStoredExpandedFolderIds,
  serializeExpandedFolderIds,
  type TreeExpansionStorageLike,
} from "./tree-expansion";

class MockStorage implements TreeExpansionStorageLike {
  values = new Map<string, string>();
  getError = false;
  setError = false;

  getItem(key: string): string | null {
    if (this.getError) {
      throw new Error("no storage");
    }
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    if (this.setError) {
      throw new Error("no storage");
    }
    this.values.set(key, value);
  }
}

class FakeDetails {
  open = false;
  private listeners: Array<() => void> = [];

  addEventListener(type: "toggle", listener: () => void): void {
    if (type === "toggle") {
      this.listeners.push(listener);
    }
  }

  // Simulates a real native toggle: flips `open` the way a user click or a
  // script assignment would, then fires every registered "toggle" listener,
  // matching how the browser actually behaves.
  toggle(nextOpen: boolean): void {
    this.open = nextOpen;
    this.listeners.forEach((listener) => listener());
  }
}

class FakeInput {
  value = "";
}

class FakeElement {
  dataset: Record<string, string> = {};
  private queryMap = new Map<string, unknown>();
  private queryAllMap = new Map<string, unknown[]>();

  setQuery(selector: string, value: unknown): void {
    this.queryMap.set(selector, value);
  }

  setQueryAll(selector: string, values: unknown[]): void {
    this.queryAllMap.set(selector, values);
  }

  querySelector<T>(selector: string): T | null {
    return (this.queryMap.get(selector) as T | undefined) ?? null;
  }

  querySelectorAll<T>(selector: string): T[] {
    return (this.queryAllMap.get(selector) as T[] | undefined) ?? [];
  }
}

interface FolderFixture {
  details: FakeDetails;
  id: string;
  li: FakeElement;
}

function makeFolder(id: string, initialOpen = false): FolderFixture {
  const details = new FakeDetails();
  details.open = initialOpen;
  const li = new FakeElement();
  li.dataset.folderId = id;
  li.setQuery("details.tree-nav__folder-details", details);
  return { details, id, li };
}

function makeContainer(folders: FolderFixture[]) {
  const container = new FakeElement();
  container.setQueryAll(
    "li.tree-nav__folder[data-folder-id]",
    folders.map((folder) => folder.li),
  );
  const filterInput = new FakeInput();
  container.setQuery("[data-tree-filter-input]", filterInput);
  return { container, filterInput };
}

function makeDoc(containers: FakeElement[]) {
  return {
    querySelectorAll: (selector: string) =>
      selector === ".tree-nav" ? containers : [],
  } as unknown as Document;
}

describe("parseStoredExpandedFolderIds", () => {
  it("returns an empty set for null", () => {
    expect(parseStoredExpandedFolderIds(null)).toEqual(new Set());
  });

  it("returns an empty set for invalid JSON", () => {
    expect(parseStoredExpandedFolderIds("{not json")).toEqual(new Set());
  });

  it("returns an empty set when the JSON value isn't an array", () => {
    expect(parseStoredExpandedFolderIds('{"a":1}')).toEqual(new Set());
  });

  it("drops non-string entries", () => {
    expect(parseStoredExpandedFolderIds('["12",3,null,"45"]')).toEqual(
      new Set(["12", "45"]),
    );
  });

  it("round-trips through serializeExpandedFolderIds", () => {
    const ids = new Set(["1", "2", "3"]);
    expect(
      parseStoredExpandedFolderIds(serializeExpandedFolderIds(ids)),
    ).toEqual(ids);
  });
});

describe("initTreeExpansionDocument", () => {
  it("restores multiple expanded folders on load", () => {
    const folders = [makeFolder("1"), makeFolder("2"), makeFolder("3")];
    const { container } = makeContainer(folders);
    const storage = new MockStorage();
    storage.setItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY, '["1","3"]');

    initTreeExpansionDocument(makeDoc([container]), storage);

    expect(folders[0].details.open).toBe(true);
    expect(folders[1].details.open).toBe(false);
    expect(folders[2].details.open).toBe(true);
  });

  it("persists expand and collapse toggles", () => {
    const folders = [makeFolder("1"), makeFolder("2")];
    const { container } = makeContainer(folders);
    const storage = new MockStorage();

    initTreeExpansionDocument(makeDoc([container]), storage);

    folders[0].details.toggle(true);
    expect(
      parseStoredExpandedFolderIds(
        storage.getItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY),
      ),
    ).toEqual(new Set(["1"]));

    folders[1].details.toggle(true);
    expect(
      parseStoredExpandedFolderIds(
        storage.getItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY),
      ),
    ).toEqual(new Set(["1", "2"]));

    folders[0].details.toggle(false);
    expect(
      parseStoredExpandedFolderIds(
        storage.getItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY),
      ),
    ).toEqual(new Set(["2"]));
  });

  it("synchronizes the wide tree and narrow drawer copies when either toggles", () => {
    const wideFolders = [makeFolder("1"), makeFolder("2")];
    const drawerFolders = [makeFolder("1"), makeFolder("2")];
    const { container: wideContainer } = makeContainer(wideFolders);
    const { container: drawerContainer } = makeContainer(drawerFolders);
    const storage = new MockStorage();

    initTreeExpansionDocument(
      makeDoc([wideContainer, drawerContainer]),
      storage,
    );

    // Toggling folder "1" open in the wide tree must also open it in the
    // narrow drawer copy, even though they are two independent <details>.
    wideFolders[0].details.toggle(true);
    expect(wideFolders[0].details.open).toBe(true);
    expect(drawerFolders[0].details.open).toBe(true);
    expect(drawerFolders[1].details.open).toBe(false);

    // The reverse direction works too.
    drawerFolders[1].details.toggle(true);
    expect(drawerFolders[1].details.open).toBe(true);
    expect(wideFolders[1].details.open).toBe(true);

    expect(
      parseStoredExpandedFolderIds(
        storage.getItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY),
      ),
    ).toEqual(new Set(["1", "2"]));
  });

  it("lets the current note's folder open automatically without erasing other stored expansions", () => {
    // Folder "2" is rendered `open` server-side because it holds the note
    // currently being viewed -- this must not be lost, and stored folder
    // "1" (expanded on an earlier page, unrelated to this note) must still
    // be restored too.
    const folders = [makeFolder("1"), makeFolder("2", true), makeFolder("3")];
    const { container } = makeContainer(folders);
    const storage = new MockStorage();
    storage.setItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY, '["1"]');

    initTreeExpansionDocument(makeDoc([container]), storage);

    expect(folders[0].details.open).toBe(true);
    expect(folders[1].details.open).toBe(true);
    expect(folders[2].details.open).toBe(false);
    // The auto-opened current-note folder ("2") must not have been written
    // into storage -- only what the user actually toggled belongs there.
    expect(
      parseStoredExpandedFolderIds(
        storage.getItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY),
      ),
    ).toEqual(new Set(["1"]));
  });

  it("restores the same state across a simulated navigation/page reload", () => {
    const storage = new MockStorage();

    const firstPageFolders = [makeFolder("1"), makeFolder("2")];
    const { container: firstContainer } = makeContainer(firstPageFolders);
    initTreeExpansionDocument(makeDoc([firstContainer]), storage);
    firstPageFolders[0].details.toggle(true);

    // A full page navigation discards the entire DOM and server-renders a
    // fresh one -- simulated here with brand-new fixture objects reusing
    // the same storage instance, exactly like a real second page load.
    const secondPageFolders = [makeFolder("1"), makeFolder("2")];
    const { container: secondContainer } = makeContainer(secondPageFolders);
    initTreeExpansionDocument(makeDoc([secondContainer]), storage);

    expect(secondPageFolders[0].details.open).toBe(true);
    expect(secondPageFolders[1].details.open).toBe(false);
  });

  it("removes a stale folder ID that no longer exists in the rendered tree", () => {
    const storage = new MockStorage();
    storage.setItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY, '["1","2","999"]');
    const folders = [makeFolder("1"), makeFolder("2")];
    const { container } = makeContainer(folders);

    initTreeExpansionDocument(makeDoc([container]), storage);

    expect(
      parseStoredExpandedFolderIds(
        storage.getItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY),
      ),
    ).toEqual(new Set(["1", "2"]));
  });

  it("cleanly drops a trashed/deleted folder's stored ID without error", () => {
    const storage = new MockStorage();
    storage.setItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY, '["42"]');
    // Folder 42 was trashed since the ID was stored -- it simply isn't in
    // this render at all.
    const folders = [makeFolder("7")];
    const { container } = makeContainer(folders);

    expect(() =>
      initTreeExpansionDocument(makeDoc([container]), storage),
    ).not.toThrow();
    expect(
      parseStoredExpandedFolderIds(
        storage.getItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY),
      ),
    ).toEqual(new Set());
    expect(folders[0].details.open).toBe(false);
  });

  it("retains a folder's stored expansion by ID across a rename", () => {
    const storage = new MockStorage();
    storage.setItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY, '["5"]');

    // A rename changes only the folder's name/label, never its ID -- the
    // fixture below stands in for "the same folder, re-rendered after a
    // rename," identified purely by data-folder-id="5".
    const folders = [makeFolder("5")];
    const { container } = makeContainer(folders);

    initTreeExpansionDocument(makeDoc([container]), storage);

    expect(folders[0].details.open).toBe(true);
  });

  it("degrades safely on invalid JSON in storage", () => {
    const storage = new MockStorage();
    storage.setItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY, "not json at all");
    const folders = [makeFolder("1")];
    const { container } = makeContainer(folders);

    expect(() =>
      initTreeExpansionDocument(makeDoc([container]), storage),
    ).not.toThrow();
    expect(folders[0].details.open).toBe(false);
  });

  it("degrades safely when storage reads throw", () => {
    const storage = new MockStorage();
    storage.getError = true;
    const folders = [makeFolder("1")];
    const { container } = makeContainer(folders);

    expect(() =>
      initTreeExpansionDocument(makeDoc([container]), storage),
    ).not.toThrow();
    expect(folders[0].details.open).toBe(false);
  });

  it("degrades safely when storage writes throw", () => {
    const storage = new MockStorage();
    storage.setError = true;
    const folders = [makeFolder("1")];
    const { container } = makeContainer(folders);
    initTreeExpansionDocument(makeDoc([container]), storage);

    expect(() => folders[0].details.toggle(true)).not.toThrow();
    expect(folders[0].details.open).toBe(true);
  });

  it("degrades safely with no storage available at all", () => {
    const folders = [makeFolder("1")];
    const { container } = makeContainer(folders);

    expect(() =>
      initTreeExpansionDocument(makeDoc([container]), undefined),
    ).not.toThrow();
    expect(folders[0].details.open).toBe(false);
  });

  it("does not interfere with native details toggling -- no click/keydown interception", () => {
    const folders = [makeFolder("1")];
    const { container } = makeContainer(folders);
    const storage = new MockStorage();
    initTreeExpansionDocument(makeDoc([container]), storage);

    // The only listener attached is "toggle" -- simulating the native
    // open/close (as a real click on <summary> or Enter/Space would
    // produce) must be reflected exactly, never reverted or blocked.
    folders[0].details.toggle(true);
    expect(folders[0].details.open).toBe(true);
    folders[0].details.toggle(false);
    expect(folders[0].details.open).toBe(false);
  });

  it("ignores a toggle while the container's filter is active, without changing filter behavior", () => {
    const folders = [makeFolder("1")];
    const { container, filterInput } = makeContainer(folders);
    const storage = new MockStorage();
    initTreeExpansionDocument(makeDoc([container]), storage);

    filterInput.value = "something";
    folders[0].details.toggle(true);

    expect(
      parseStoredExpandedFolderIds(
        storage.getItem(TREE_EXPANDED_FOLDERS_STORAGE_KEY),
      ),
    ).toEqual(new Set());
  });

  it("is a no-op on a second call once every container is already initialized", () => {
    const folders = [makeFolder("1")];
    const { container } = makeContainer(folders);
    const storage = new MockStorage();

    expect(initTreeExpansionDocument(makeDoc([container]), storage)).toBe(true);
    expect(initTreeExpansionDocument(makeDoc([container]), storage)).toBe(
      false,
    );
  });
});
