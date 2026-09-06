import { describe, expect, it } from "vitest";

import {
  parseStoredScrollTop,
  TREE_SCROLL_STORAGE_KEY_DRAWER,
  TREE_SCROLL_STORAGE_KEY_WIDE,
  initTreeScrollPositionCaptureDocument,
  treeScrollStorageKeyForContainer,
} from "./tree-scroll-position";

describe("parseStoredScrollTop", () => {
  it("returns null when no value is stored", () => {
    expect(parseStoredScrollTop(null)).toBeNull();
  });

  it("parses a valid non-negative numeric string", () => {
    expect(parseStoredScrollTop("240")).toBe(240);
  });

  it("parses zero", () => {
    expect(parseStoredScrollTop("0")).toBe(0);
  });

  it("fails safe on a non-numeric value", () => {
    expect(parseStoredScrollTop("not-a-number")).toBeNull();
  });

  it("fails safe on an empty string", () => {
    expect(parseStoredScrollTop("")).toBeNull();
  });

  it("fails safe on a negative value", () => {
    expect(parseStoredScrollTop("-50")).toBeNull();
  });

  it("fails safe on Infinity", () => {
    expect(parseStoredScrollTop("Infinity")).toBeNull();
  });
});

class FakeContainer {
  constructor(private readonly scope: string | null) {}

  getAttribute(name: string): string | null {
    return name === "data-tree-scope" ? this.scope : null;
  }
}

describe("treeScrollStorageKeyForContainer", () => {
  it("uses the drawer key when data-tree-scope is exactly 'drawer'", () => {
    expect(treeScrollStorageKeyForContainer(new FakeContainer("drawer"))).toBe(
      TREE_SCROLL_STORAGE_KEY_DRAWER,
    );
  });

  it("uses the wide key for every other scope value (tree, allnotes, trash)", () => {
    for (const scope of ["tree", "allnotes", "trash"]) {
      expect(treeScrollStorageKeyForContainer(new FakeContainer(scope))).toBe(
        TREE_SCROLL_STORAGE_KEY_WIDE,
      );
    }
  });

  it("uses the wide key when data-tree-scope is missing", () => {
    expect(treeScrollStorageKeyForContainer(new FakeContainer(null))).toBe(
      TREE_SCROLL_STORAGE_KEY_WIDE,
    );
  });
});

class FakeStorage {
  private store = new Map<string, string>();

  getItem(key: string): string | null {
    return this.store.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.store.set(key, value);
  }
}

class FakeViewport {
  scrollTop = 0;
  private listener: (() => void) | null = null;

  addEventListener(_type: "scroll", listener: () => void): void {
    this.listener = listener;
  }

  fireScroll(): void {
    this.listener?.();
  }
}

class FakeCaptureContainer {
  dataset: { scrollCaptureInitialized?: string } = {};

  constructor(
    private readonly scope: string | null,
    private readonly viewport: FakeViewport | null,
  ) {}

  getAttribute(name: string): string | null {
    return name === "data-tree-scope" ? this.scope : null;
  }

  querySelector<T = FakeViewport>(selector: string): T | null {
    if (selector === ".tree-nav__viewport") {
      return (this.viewport as unknown as T) ?? null;
    }
    return null;
  }
}

class FakeCaptureDocument {
  constructor(private readonly containers: FakeCaptureContainer[]) {}

  querySelectorAll<T = FakeCaptureContainer>(selector: string): ArrayLike<T> {
    if (selector === ".tree-nav") {
      return this.containers as unknown as T[];
    }
    return [];
  }
}

describe("initTreeScrollPositionCaptureDocument", () => {
  it("stores the wide viewport's scrollTop under the wide key on scroll", () => {
    const viewport = new FakeViewport();
    const container = new FakeCaptureContainer("tree", viewport);
    const doc = new FakeCaptureDocument([container]);
    const storage = new FakeStorage();

    initTreeScrollPositionCaptureDocument(doc, storage);
    viewport.scrollTop = 180;
    viewport.fireScroll();

    expect(storage.getItem(TREE_SCROLL_STORAGE_KEY_WIDE)).toBe("180");
  });

  it("stores the drawer viewport's scrollTop under the drawer key independently", () => {
    const wideViewport = new FakeViewport();
    const drawerViewport = new FakeViewport();
    const wide = new FakeCaptureContainer("tree", wideViewport);
    const drawer = new FakeCaptureContainer("drawer", drawerViewport);
    const doc = new FakeCaptureDocument([wide, drawer]);
    const storage = new FakeStorage();

    initTreeScrollPositionCaptureDocument(doc, storage);
    wideViewport.scrollTop = 50;
    wideViewport.fireScroll();
    drawerViewport.scrollTop = 300;
    drawerViewport.fireScroll();

    expect(storage.getItem(TREE_SCROLL_STORAGE_KEY_WIDE)).toBe("50");
    expect(storage.getItem(TREE_SCROLL_STORAGE_KEY_DRAWER)).toBe("300");
  });

  it("updates the stored value on every subsequent scroll (live mirror, not a one-time snapshot)", () => {
    const viewport = new FakeViewport();
    const container = new FakeCaptureContainer("tree", viewport);
    const doc = new FakeCaptureDocument([container]);
    const storage = new FakeStorage();

    initTreeScrollPositionCaptureDocument(doc, storage);
    viewport.scrollTop = 10;
    viewport.fireScroll();
    viewport.scrollTop = 20;
    viewport.fireScroll();
    viewport.scrollTop = 15;
    viewport.fireScroll();

    expect(storage.getItem(TREE_SCROLL_STORAGE_KEY_WIDE)).toBe("15");
  });

  it("does not attach a listener twice to an already-initialized container", () => {
    const viewport = new FakeViewport();
    const container = new FakeCaptureContainer("tree", viewport);
    const doc = new FakeCaptureDocument([container]);
    const storage = new FakeStorage();

    initTreeScrollPositionCaptureDocument(doc, storage);
    const secondRun = initTreeScrollPositionCaptureDocument(doc, storage);

    expect(secondRun).toBe(false);
  });

  it("is a no-op on a page with no tree at all", () => {
    const doc = new FakeCaptureDocument([]);
    const storage = new FakeStorage();

    expect(initTreeScrollPositionCaptureDocument(doc, storage)).toBe(false);
  });

  it("safely handles a container with no viewport", () => {
    const container = new FakeCaptureContainer("tree", null);
    const doc = new FakeCaptureDocument([container]);
    const storage = new FakeStorage();

    expect(() =>
      initTreeScrollPositionCaptureDocument(doc, storage),
    ).not.toThrow();
  });

  it("never touches document scrolling -- only ever writes to storage", () => {
    const viewport = new FakeViewport();
    const container = new FakeCaptureContainer("tree", viewport);
    const doc = new FakeCaptureDocument([container]);
    const storage = new FakeStorage();

    initTreeScrollPositionCaptureDocument(doc, storage);
    viewport.scrollTop = 77;
    viewport.fireScroll();

    // The only mutation this module ever performs is a `sessionStorage`
    // write keyed by the two constants above; nothing here can reach the
    // document's own scroll position.
    expect(storage.getItem(TREE_SCROLL_STORAGE_KEY_WIDE)).toBe("77");
  });
});
