import { describe, expect, it } from "vitest";

import {
  findTreeNoteTitleLabels,
  parseNoteIdFromDetailUrl,
  repositionTreeNoteRow,
  syncTreeNoteTitle,
} from "./tree-title-sync";

class FakeLabel {
  textContent = "";
  scrollWidth = 0;
  clientWidth = 0;

  // `syncTreeNoteTitle` calls
  // `refreshTreeTitleTooltipForLabel`, which needs a `closest()` method
  // to look up the label's trigger ancestor. This suite tests title-sync
  // behavior only, not tooltip behavior (see `tree-title-truncation.test.ts`
  // for that) -- returning null here is a safe, valid "no trigger found"
  // case that `refreshTreeTitleTooltipForLabel` already handles as a no-op.
  closest(): null {
    return null;
  }
}

class FakeNoteItem {
  private attrs: Record<string, string> = {};
  private selectorMap = new Map<string, unknown>();

  setAttr(name: string, value: string): this {
    this.attrs[name] = value;
    return this;
  }

  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null;
  }

  setQuery(selector: string, value: unknown): this {
    this.selectorMap.set(selector, value);
    return this;
  }

  querySelector(selector: string): unknown {
    return this.selectorMap.get(selector) ?? null;
  }
}

class FakeRoot {
  private selectorAllMap = new Map<string, unknown[]>();

  setQueryAll(selector: string, values: unknown[]): this {
    this.selectorAllMap.set(selector, values);
    return this;
  }

  querySelectorAll(selector: string): unknown[] {
    return this.selectorAllMap.get(selector) ?? [];
  }
}

const NOTE_ITEM_SELECTOR = "li.tree-nav__note-item[data-note-id]";
const LABEL_SELECTOR = ".tree-nav__note-link .tree-nav__label";

function makeNoteItem(
  noteId: string,
  title: string,
): {
  item: FakeNoteItem;
  label: FakeLabel;
} {
  const label = new FakeLabel();
  label.textContent = title;
  const item = new FakeNoteItem()
    .setAttr("data-note-id", noteId)
    .setQuery(LABEL_SELECTOR, label);
  return { item, label };
}

describe("parseNoteIdFromDetailUrl", () => {
  it("extracts the numeric note ID from a notes:detail URL", () => {
    expect(parseNoteIdFromDetailUrl("/notes/42/")).toBe("42");
  });

  it("returns null for a URL with no note ID segment", () => {
    expect(parseNoteIdFromDetailUrl("/notes/")).toBeNull();
    expect(parseNoteIdFromDetailUrl("/home/")).toBeNull();
  });

  it("returns null for an empty string", () => {
    expect(parseNoteIdFromDetailUrl("")).toBeNull();
  });
});

describe("findTreeNoteTitleLabels", () => {
  it("finds the label for the target note ID across multiple tree copies", () => {
    const wide = makeNoteItem("7", "Viewer Note");
    const drawer = makeNoteItem("7", "Viewer Note");
    const other = makeNoteItem("9", "Other Note");
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [
      wide.item,
      drawer.item,
      other.item,
    ]);

    const labels = findTreeNoteTitleLabels(root as unknown as ParentNode, "7");

    expect(labels).toEqual([wide.label, drawer.label]);
  });

  it("returns an empty list when no row matches the target note ID", () => {
    const other = makeNoteItem("9", "Other Note");
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [other.item]);

    const labels = findTreeNoteTitleLabels(root as unknown as ParentNode, "7");

    expect(labels).toEqual([]);
  });

  it("returns an empty list when no tree rows exist at all", () => {
    const root = new FakeRoot();

    const labels = findTreeNoteTitleLabels(root as unknown as ParentNode, "7");

    expect(labels).toEqual([]);
  });

  it("skips a matching row that has no label element", () => {
    const bare = new FakeNoteItem().setAttr("data-note-id", "7");
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [bare]);

    const labels = findTreeNoteTitleLabels(root as unknown as ParentNode, "7");

    expect(labels).toEqual([]);
  });
});

describe("syncTreeNoteTitle", () => {
  it("updates all matching rows for the target note ID", () => {
    const wide = makeNoteItem("7", "Old Title");
    const drawer = makeNoteItem("7", "Old Title");
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [
      wide.item,
      drawer.item,
    ]);

    syncTreeNoteTitle(root as unknown as ParentNode, "7", "New Title");

    expect(wide.label.textContent).toBe("New Title");
    expect(drawer.label.textContent).toBe("New Title");
  });

  it("updates the wide tree copy and the narrow drawer copy independently on the same page", () => {
    const wide = makeNoteItem("7", "Old Title");
    const drawer = makeNoteItem("7", "Old Title");
    const differentNote = makeNoteItem("9", "Unrelated");
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [
      wide.item,
      drawer.item,
      differentNote.item,
    ]);

    syncTreeNoteTitle(root as unknown as ParentNode, "7", "Renamed");

    expect(wide.label.textContent).toBe("Renamed");
    expect(drawer.label.textContent).toBe("Renamed");
    expect(differentNote.label.textContent).toBe("Unrelated");
  });

  it("leaves non-target note rows completely unchanged", () => {
    const target = makeNoteItem("7", "Old Title");
    const other = makeNoteItem("9", "Other Note");
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [
      target.item,
      other.item,
    ]);

    syncTreeNoteTitle(root as unknown as ParentNode, "7", "New Title");

    expect(other.label.textContent).toBe("Other Note");
  });

  it("is a no-op when no matching row exists on the page yet", () => {
    const other = makeNoteItem("9", "Other Note");
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [other.item]);

    expect(() =>
      syncTreeNoteTitle(root as unknown as ParentNode, "7", "New Title"),
    ).not.toThrow();
    expect(other.label.textContent).toBe("Other Note");
  });

  it("safely handles an unchanged title as a no-op", () => {
    const target = makeNoteItem("7", "Same Title");
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [target.item]);

    syncTreeNoteTitle(root as unknown as ParentNode, "7", "Same Title");

    expect(target.label.textContent).toBe("Same Title");
  });

  it("safely handles an empty title", () => {
    const target = makeNoteItem("7", "Old Title");
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [target.item]);

    syncTreeNoteTitle(root as unknown as ParentNode, "7", "");

    expect(target.label.textContent).toBe("");
  });

  it("safely handles being called repeatedly with the same title", () => {
    const target = makeNoteItem("7", "Old Title");
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [target.item]);

    syncTreeNoteTitle(root as unknown as ParentNode, "7", "New Title");
    syncTreeNoteTitle(root as unknown as ParentNode, "7", "New Title");
    syncTreeNoteTitle(root as unknown as ParentNode, "7", "New Title");

    expect(target.label.textContent).toBe("New Title");
  });

  it("updates only the label and leaves other row markers untouched", () => {
    const target = makeNoteItem("7", "Old Title");
    const pinMarker = { role: "img", ariaLabel: "Pinned" };
    // Simulate a pin marker rendered as a sibling of the label inside the
    // same note row; syncTreeNoteTitle never queries for it and must never
    // touch it.
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [target.item]);

    syncTreeNoteTitle(root as unknown as ParentNode, "7", "New Title");

    expect(pinMarker).toEqual({ role: "img", ariaLabel: "Pinned" });
    expect(target.label.textContent).toBe("New Title");
  });

  it("never matches folder-row labels, which share the same .tree-nav__label class but are outside .tree-nav__note-item", () => {
    // The note-item selector used by findTreeNoteTitleLabels/syncTreeNoteTitle
    // is scoped to `li.tree-nav__note-item[data-note-id]`, so a folder's own
    // `.tree-nav__label` (rendered under `.tree-nav__folder-row`, not a note
    // item) is structurally never returned by querySelectorAll(NOTE_ITEM_SELECTOR)
    // in the first place.
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, []);

    const labels = findTreeNoteTitleLabels(root as unknown as ParentNode, "7");

    expect(labels).toEqual([]);
  });

  it("does not touch anything on a page with no tree at all, such as Home's flat list", () => {
    // Home's flat-list rows are not `.tree-nav__note-item` elements, so a
    // root representing that page structure simply has nothing registered
    // for the note-item selector -- confirming the sync is a safe no-op.
    const root = new FakeRoot();

    expect(() =>
      syncTreeNoteTitle(root as unknown as ParentNode, "7", "New Title"),
    ).not.toThrow();
  });
});

// Renaming a note
// changes its correct alphabetical tree position, so an already-rendered
// row needs re-sorting, not just `syncTreeNoteTitle`'s
// label-text-in-place update. These fakes model just enough of a
// real `<li class="tree-nav__note-item">` row and its enclosing `<ul>`
// list to exercise `repositionTreeNoteRow`'s DOM move (`insertBefore`)
// without needing jsdom, mirroring this file's own `MockContainer`-style
// pattern used elsewhere in this codebase (see `tree-context-menu.test.ts`).
class FakeTreeRowList {
  children: FakeTreeRow[] = [];

  add(row: FakeTreeRow): this {
    row.parentElement = this;
    this.children.push(row);
    return this;
  }

  insertBefore(node: FakeTreeRow, reference: FakeTreeRow | null): void {
    const currentIndex = this.children.indexOf(node);
    if (currentIndex !== -1) {
      this.children.splice(currentIndex, 1);
    }
    if (reference === null) {
      this.children.push(node);
    } else {
      const refIndex = this.children.indexOf(reference);
      this.children.splice(
        refIndex === -1 ? this.children.length : refIndex,
        0,
        node,
      );
    }
    node.parentElement = this;
  }

  noteIds(): string[] {
    return this.children.map((row) => row.noteId);
  }
}

class FakeTreeRowLabel {
  constructor(
    public textContent: string,
    private readonly ownerRow: FakeTreeRow,
  ) {}

  // Real `Element.closest()` walks up from the label to its enclosing
  // `<li class="tree-nav__note-item">`; `refreshTreeTitleTooltipForLabel`
  // also calls `closest()` with an unrelated tooltip-trigger selector,
  // which must safely resolve to `null` here, exactly like the plain
  // `FakeLabel` above.
  closest(selector: string): FakeTreeRow | null {
    return selector === "li.tree-nav__note-item" ? this.ownerRow : null;
  }
}

class FakeTreeRow {
  parentElement: FakeTreeRowList | null = null;
  readonly label: FakeTreeRowLabel;

  constructor(
    public noteId: string,
    title: string,
    private pinned = false,
  ) {
    this.label = new FakeTreeRowLabel(title, this);
  }

  matches(selector: string): boolean {
    return selector === "li.tree-nav__note-item";
  }

  getAttribute(name: string): string | null {
    return name === "data-note-id" ? this.noteId : null;
  }

  querySelector(selector: string): { textContent: string | null } | null {
    if (selector.includes(".tree-nav__label")) {
      return this.label;
    }
    if (selector.includes(".tree-nav__pin-marker")) {
      return this.pinned ? { textContent: null } : null;
    }
    return null;
  }
}

describe("repositionTreeNoteRow", () => {
  it("moves an unpinned row to its correct alphabetical position", () => {
    const list = new FakeTreeRowList();
    const apple = new FakeTreeRow("1", "Apple");
    const cherry = new FakeTreeRow("2", "Cherry");
    const banana = new FakeTreeRow("3", "banana");
    list.add(apple).add(cherry).add(banana);

    repositionTreeNoteRow(banana as unknown as Element);

    expect(list.noteIds()).toEqual(["1", "3", "2"]);
  });

  it("compares titles case-insensitively", () => {
    const list = new FakeTreeRowList();
    const apple = new FakeTreeRow("1", "apple");
    const banana = new FakeTreeRow("2", "Banana");
    const cherry = new FakeTreeRow("3", "CHERRY");
    list.add(cherry).add(apple).add(banana);

    repositionTreeNoteRow(cherry as unknown as Element);

    expect(list.noteIds()).toEqual(["1", "2", "3"]);
  });

  it("keeps pinned rows above every unpinned row regardless of title", () => {
    const list = new FakeTreeRowList();
    const zPinned = new FakeTreeRow("1", "Zebra", true);
    const aUnpinned = new FakeTreeRow("2", "Apple", false);
    list.add(aUnpinned).add(zPinned);

    repositionTreeNoteRow(zPinned as unknown as Element);

    expect(list.noteIds()).toEqual(["1", "2"]);
  });

  it("sorts alphabetically within the pinned group, not by recency", () => {
    const list = new FakeTreeRowList();
    const zebra = new FakeTreeRow("1", "Zebra", true);
    const apple = new FakeTreeRow("2", "Apple", true);
    list.add(zebra).add(apple);

    repositionTreeNoteRow(apple as unknown as Element);

    expect(list.noteIds()).toEqual(["2", "1"]);
  });

  it("breaks ties between equal case-insensitive titles by ID descending", () => {
    const list = new FakeTreeRowList();
    const lowerId = new FakeTreeRow("5", "Draft");
    const higherId = new FakeTreeRow("9", "draft");
    list.add(lowerId).add(higherId);

    repositionTreeNoteRow(higherId as unknown as Element);

    expect(list.noteIds()).toEqual(["9", "5"]);
  });

  it("is a no-op when the row is already in the correct position", () => {
    const list = new FakeTreeRowList();
    const apple = new FakeTreeRow("1", "Apple");
    const banana = new FakeTreeRow("2", "Banana");
    list.add(apple).add(banana);

    repositionTreeNoteRow(apple as unknown as Element);

    expect(list.noteIds()).toEqual(["1", "2"]);
  });

  it("does nothing when the row has no parent", () => {
    const orphan = new FakeTreeRow("1", "Orphan");

    expect(() =>
      repositionTreeNoteRow(orphan as unknown as Element),
    ).not.toThrow();
  });

  it("ignores non-note-row siblings such as an empty-list placeholder", () => {
    const list = new FakeTreeRowList();
    const apple = new FakeTreeRow("1", "Apple");
    const banana = new FakeTreeRow("2", "Banana");
    const placeholder = { matches: () => false } as unknown as FakeTreeRow;
    list.add(apple).add(banana);
    list.children.push(placeholder);
    placeholder.parentElement = list;

    expect(() =>
      repositionTreeNoteRow(banana as unknown as Element),
    ).not.toThrow();
    expect(list.noteIds().filter((id) => id !== undefined)).toEqual(["1", "2"]);
  });
});

describe("syncTreeNoteTitle repositioning after a rename", () => {
  function rootFor(rows: FakeTreeRow[]): FakeRoot {
    return new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, rows);
  }

  it("repositions an unpinned note alphabetically after its title is renamed", () => {
    const list = new FakeTreeRowList();
    const apple = new FakeTreeRow("1", "Apple");
    const cherry = new FakeTreeRow("2", "Cherry");
    const target = new FakeTreeRow("3", "Old Title");
    list.add(apple).add(cherry).add(target);
    const root = rootFor([apple, cherry, target]);

    syncTreeNoteTitle(root as unknown as ParentNode, "3", "banana");

    expect(target.label.textContent).toBe("banana");
    expect(list.noteIds()).toEqual(["1", "3", "2"]);
  });

  it("repositions a pinned note within the pinned group after a rename", () => {
    const list = new FakeTreeRowList();
    const zebraPinned = new FakeTreeRow("1", "Zebra", true);
    const target = new FakeTreeRow("2", "Old Title", true);
    const unpinned = new FakeTreeRow("3", "Aardvark", false);
    list.add(zebraPinned).add(target).add(unpinned);
    const root = rootFor([zebraPinned, target, unpinned]);

    syncTreeNoteTitle(root as unknown as ParentNode, "2", "Apple");

    // Pinned group (renamed note now alphabetically first among pinned)
    // stays entirely above the unpinned row.
    expect(list.noteIds()).toEqual(["2", "1", "3"]);
  });

  it("updates both the wide tree copy and the narrow drawer copy independently", () => {
    const wideList = new FakeTreeRowList();
    const wideApple = new FakeTreeRow("1", "Apple");
    const wideTarget = new FakeTreeRow("2", "Old Title");
    wideList.add(wideApple).add(wideTarget);

    const drawerList = new FakeTreeRowList();
    const drawerApple = new FakeTreeRow("1", "Apple");
    const drawerTarget = new FakeTreeRow("2", "Old Title");
    drawerList.add(drawerApple).add(drawerTarget);

    const root = rootFor([wideApple, wideTarget, drawerApple, drawerTarget]);

    syncTreeNoteTitle(root as unknown as ParentNode, "2", "Aardvark");

    expect(wideList.noteIds()).toEqual(["2", "1"]);
    expect(drawerList.noteIds()).toEqual(["2", "1"]);
  });

  it("does not reposition when the title is unchanged (a no-op)", () => {
    const list = new FakeTreeRowList();
    const apple = new FakeTreeRow("1", "Apple");
    const target = new FakeTreeRow("2", "Same Title");
    list.add(apple).add(target);
    const root = rootFor([apple, target]);

    syncTreeNoteTitle(root as unknown as ParentNode, "2", "Same Title");

    expect(list.noteIds()).toEqual(["1", "2"]);
  });

  it("still updates the label even when no enclosing row can be found", () => {
    const label = new FakeLabel();
    label.textContent = "Old Title";
    const item = new FakeNoteItem()
      .setAttr("data-note-id", "7")
      .setQuery(".tree-nav__note-link .tree-nav__label", label);
    const root = new FakeRoot().setQueryAll(NOTE_ITEM_SELECTOR, [item]);

    expect(() =>
      syncTreeNoteTitle(root as unknown as ParentNode, "7", "New Title"),
    ).not.toThrow();
    expect(label.textContent).toBe("New Title");
  });
});
