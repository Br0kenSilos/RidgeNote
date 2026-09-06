import { describe, expect, it } from "vitest";

import {
  TREE_FILTER_ACTIVE_TARGET_CLASS,
  applyTreeFilterToContainer,
  computeTreeFilterResult,
  initTreeFilterDocument,
  type TreeFilterOpenStateMap,
  type TreeFilterTreeInfo,
} from "./tree-filter";

describe("computeTreeFilterResult", () => {
  const tree: TreeFilterTreeInfo = {
    groups: [
      {
        title: "Alpha Folder",
        notes: [{ title: "Grocery List" }, { title: "Travel Plans" }],
      },
      {
        title: "Beta Folder",
        notes: [{ title: "Meeting Notes" }],
      },
      {
        title: "Unfiled",
        notes: [{ title: "Scratchpad" }, { title: "Recipe Ideas" }],
      },
    ],
  };

  it("shows everything when the filter is empty", () => {
    const result = computeTreeFilterResult(tree, "");
    expect(result.noMatches).toBe(false);
    expect(result.groups.every((g) => g.visible)).toBe(true);
    expect(result.groups.every((g) => g.noteVisibility.every(Boolean))).toBe(
      true,
    );
  });

  it("matches a note title case-insensitively", () => {
    const result = computeTreeFilterResult(tree, "grocery");
    const alpha = result.groups[0];
    expect(alpha.visible).toBe(true);
    expect(alpha.noteVisibility).toEqual([true, false]);
  });

  it("matches a folder title case-insensitively", () => {
    const result = computeTreeFilterResult(tree, "ALPHA");
    const alpha = result.groups[0];
    expect(alpha.visible).toBe(true);
    // Folder title itself matched, so every note inside stays visible.
    expect(alpha.noteVisibility).toEqual([true, true]);
  });

  it("keeps a folder visible as context when only a child note matches, hiding non-matching siblings", () => {
    const result = computeTreeFilterResult(tree, "travel");
    const alpha = result.groups[0];
    expect(alpha.visible).toBe(true);
    expect(alpha.noteVisibility).toEqual([false, true]);
  });

  it("hides a folder entirely when neither its title nor any child note matches", () => {
    const result = computeTreeFilterResult(tree, "travel");
    const beta = result.groups[1];
    expect(beta.visible).toBe(false);
    expect(beta.noteVisibility).toEqual([false]);
  });

  it("treats the Unfiled group the same as a folder for matching notes", () => {
    const result = computeTreeFilterResult(tree, "scratchpad");
    const unfiled = result.groups[2];
    expect(unfiled.visible).toBe(true);
    expect(unfiled.noteVisibility).toEqual([true, false]);
  });

  it("hides the Unfiled group when no Unfiled note matches and its title does not match", () => {
    const result = computeTreeFilterResult(tree, "meeting");
    const unfiled = result.groups[2];
    expect(unfiled.visible).toBe(false);
  });

  it("reports noMatches when nothing at all matches", () => {
    const result = computeTreeFilterResult(tree, "zzz-nonexistent");
    expect(result.noMatches).toBe(true);
    expect(result.groups.every((g) => !g.visible)).toBe(true);
  });

  it("does not report noMatches when at least one group matches", () => {
    const result = computeTreeFilterResult(tree, "beta");
    expect(result.noMatches).toBe(false);
  });

  it("trims surrounding whitespace before matching", () => {
    const result = computeTreeFilterResult(tree, "  grocery  ");
    expect(result.groups[0].noteVisibility).toEqual([true, false]);
  });
});

class MockClassList {
  private classes = new Set<string>();

  add(name: string): void {
    this.classes.add(name);
  }

  remove(name: string): void {
    this.classes.delete(name);
  }

  contains(name: string): boolean {
    return this.classes.has(name);
  }
}

class FakeDetails {
  open = false;
}

class FakeElement {
  hidden = false;
  textContent = "";
  classList = new MockClassList();
  dataset: Record<string, string> = {};
  detailsFake: FakeDetails | null = null;
  private selectorMap = new Map<string, unknown>();
  private selectorAllMap = new Map<string, unknown[]>();
  private listeners = new Map<string, Array<() => void>>();
  value = "";

  setQuery(selector: string, value: unknown): this {
    this.selectorMap.set(selector, value);
    return this;
  }

  setQueryAll(selector: string, values: unknown[]): this {
    this.selectorAllMap.set(selector, values);
    return this;
  }

  querySelector(selector: string): unknown {
    return this.selectorMap.get(selector) ?? null;
  }

  querySelectorAll(selector: string): unknown[] {
    return this.selectorAllMap.get(selector) ?? [];
  }

  addEventListener(type: string, listener: () => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  trigger(type: string): void {
    this.listeners.get(type)?.forEach((listener) => listener());
  }
}

function makeNoteElement(title: string): FakeElement {
  const label = new FakeElement();
  label.textContent = title;
  const noteEl = new FakeElement();
  noteEl.setQuery(".tree-nav__note-link .tree-nav__label", label);
  return noteEl;
}

function makeGroupElement(
  title: string,
  noteTitles: string[],
  initialOpen = false,
): FakeElement {
  const titleLabel = new FakeElement();
  titleLabel.textContent = title;
  const groupEl = new FakeElement();
  groupEl.setQuery(
    "details.tree-nav__folder-details > summary .tree-nav__label",
    titleLabel,
  );
  const noteEls = noteTitles.map(makeNoteElement);
  groupEl.setQueryAll(
    [
      ":scope > ul.tree-nav__folder-notes > li.tree-nav__note-item",
      ":scope > details.tree-nav__folder-details > ul.tree-nav__notes > li.tree-nav__note-item",
    ].join(", "),
    noteEls,
  );
  const summaryRow = new FakeElement();
  groupEl.setQuery(".tree-nav__folder-row", summaryRow);
  const details = new FakeDetails();
  details.open = initialOpen;
  groupEl.setQuery("details.tree-nav__folder-details", details);
  groupEl.detailsFake = details;
  return groupEl;
}

describe("applyTreeFilterToContainer", () => {
  function buildContainer(options?: {
    alphaOpen?: boolean;
    betaOpen?: boolean;
  }) {
    const alpha = makeGroupElement(
      "Alpha Folder",
      ["Grocery List", "Travel Plans"],
      options?.alphaOpen ?? false,
    );
    const beta = makeGroupElement(
      "Beta Folder",
      ["Meeting Notes"],
      options?.betaOpen ?? false,
    );
    const emptyMessage = new FakeElement();

    const container = new FakeElement();
    container.setQueryAll(
      ":scope > .tree-nav__viewport > .tree-nav__list > li",
      [alpha, beta],
    );
    container.setQuery("[data-tree-filter-empty]", emptyMessage);

    return { alpha, beta, container, emptyMessage };
  }

  it("hides non-matching sibling notes but keeps the folder visible as context", () => {
    const { alpha, container } = buildContainer();

    applyTreeFilterToContainer(container as unknown as Element, "travel");

    expect(alpha.hidden).toBe(false);
    const noteEls = alpha.querySelectorAll(
      [
        ":scope > ul.tree-nav__folder-notes > li.tree-nav__note-item",
        ":scope > details.tree-nav__folder-details > ul.tree-nav__notes > li.tree-nav__note-item",
      ].join(", "),
    ) as FakeElement[];
    expect(noteEls[0].hidden).toBe(true);
    expect(noteEls[1].hidden).toBe(false);
  });

  it("hides a folder entirely when it has no matches, and strips any active drop-target class", () => {
    const { beta, container } = buildContainer();
    const betaSummary = beta.querySelector(
      ".tree-nav__folder-row",
    ) as FakeElement;
    betaSummary.classList.add(TREE_FILTER_ACTIVE_TARGET_CLASS);

    applyTreeFilterToContainer(container as unknown as Element, "travel");

    expect(beta.hidden).toBe(true);
    expect(
      betaSummary.classList.contains(TREE_FILTER_ACTIVE_TARGET_CLASS),
    ).toBe(false);
  });

  it("restores full visibility when the filter is cleared", () => {
    const { alpha, beta, container } = buildContainer();

    applyTreeFilterToContainer(container as unknown as Element, "travel");
    applyTreeFilterToContainer(container as unknown as Element, "");

    expect(alpha.hidden).toBe(false);
    expect(beta.hidden).toBe(false);
    const noteEls = alpha.querySelectorAll(
      [
        ":scope > ul.tree-nav__folder-notes > li.tree-nav__note-item",
        ":scope > details.tree-nav__folder-details > ul.tree-nav__notes > li.tree-nav__note-item",
      ].join(", "),
    ) as FakeElement[];
    expect(noteEls.every((el) => !el.hidden)).toBe(true);
  });

  it("shows the filtered-empty message only when nothing matches a non-empty filter", () => {
    const { container, emptyMessage } = buildContainer();

    applyTreeFilterToContainer(
      container as unknown as Element,
      "zzz-nonexistent",
    );
    expect(emptyMessage.hidden).toBe(false);

    applyTreeFilterToContainer(container as unknown as Element, "alpha");
    expect(emptyMessage.hidden).toBe(true);

    applyTreeFilterToContainer(container as unknown as Element, "");
    expect(emptyMessage.hidden).toBe(true);
  });

  it("opens a previously collapsed folder when its own title matches", () => {
    const { alpha, container } = buildContainer({ alphaOpen: false });
    const openState: TreeFilterOpenStateMap = new WeakMap();

    applyTreeFilterToContainer(
      container as unknown as Element,
      "alpha",
      openState,
    );

    expect(alpha.detailsFake?.open).toBe(true);
  });

  it("opens a previously collapsed folder when a child note matches", () => {
    const { alpha, container } = buildContainer({ alphaOpen: false });
    const openState: TreeFilterOpenStateMap = new WeakMap();

    applyTreeFilterToContainer(
      container as unknown as Element,
      "travel",
      openState,
    );

    expect(alpha.detailsFake?.open).toBe(true);
  });

  it("restores each folder's pre-filter open/closed state when the filter is cleared", () => {
    const { alpha, beta, container } = buildContainer({
      alphaOpen: false,
      betaOpen: true,
    });
    const openState: TreeFilterOpenStateMap = new WeakMap();

    // Alpha was collapsed and gets force-opened by the match; Beta was
    // already open and has no notes matching "travel" so it collapses/hides.
    applyTreeFilterToContainer(
      container as unknown as Element,
      "travel",
      openState,
    );
    expect(alpha.detailsFake?.open).toBe(true);

    applyTreeFilterToContainer(container as unknown as Element, "", openState);

    expect(alpha.detailsFake?.open).toBe(false);
    expect(beta.detailsFake?.open).toBe(true);
  });

  it("does not re-capture the pre-filter open state across multiple keystrokes in the same filtering session", () => {
    const { alpha, container } = buildContainer({ alphaOpen: false });
    const openState: TreeFilterOpenStateMap = new WeakMap();

    applyTreeFilterToContainer(
      container as unknown as Element,
      "trav",
      openState,
    );
    expect(alpha.detailsFake?.open).toBe(true);

    // A manual close mid-session followed by another keystroke should not
    // overwrite the originally-captured (collapsed) pre-filter state.
    if (alpha.detailsFake) alpha.detailsFake.open = false;
    applyTreeFilterToContainer(
      container as unknown as Element,
      "travel",
      openState,
    );

    applyTreeFilterToContainer(container as unknown as Element, "", openState);
    expect(alpha.detailsFake?.open).toBe(false);
  });

  it("keeps open-state restoration independent per container instance", () => {
    const first = buildContainer({ alphaOpen: false });
    const second = buildContainer({ alphaOpen: false });
    const firstOpenState: TreeFilterOpenStateMap = new WeakMap();
    const secondOpenState: TreeFilterOpenStateMap = new WeakMap();

    applyTreeFilterToContainer(
      first.container as unknown as Element,
      "alpha",
      firstOpenState,
    );
    // Second container's map never saw this filter text; its folder must be
    // completely unaffected.
    expect(second.alpha.detailsFake?.open).toBe(false);

    applyTreeFilterToContainer(
      second.container as unknown as Element,
      "",
      secondOpenState,
    );
    expect(second.alpha.detailsFake?.open).toBe(false);
  });
});

describe("initTreeFilterDocument", () => {
  it("wires each tree-nav container's own filter input independently", () => {
    const alphaA = makeGroupElement("Alpha", ["Match One"]);
    const containerA = new FakeElement();
    containerA.setQueryAll(
      ":scope > .tree-nav__viewport > .tree-nav__list > li",
      [alphaA],
    );
    const inputA = new FakeElement();
    containerA.setQuery("[data-tree-filter-input]", inputA);

    const alphaB = makeGroupElement("Alpha", ["Match One"]);
    const containerB = new FakeElement();
    containerB.setQueryAll(
      ":scope > .tree-nav__viewport > .tree-nav__list > li",
      [alphaB],
    );
    const inputB = new FakeElement();
    containerB.setQuery("[data-tree-filter-input]", inputB);

    const doc = {
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [containerA, containerB] : [],
    } as unknown as Document;

    expect(initTreeFilterDocument(doc)).toBe(true);
    expect(containerA.dataset.filterInitialized).toBe("true");
    expect(containerB.dataset.filterInitialized).toBe("true");

    inputA.value = "zzz-nonexistent";
    inputA.trigger("input");

    expect(alphaA.hidden).toBe(true);
    // Container B's group must be completely unaffected by container A's filter.
    expect(alphaB.hidden).toBe(false);

    // Re-running init is a no-op (already initialized).
    expect(initTreeFilterDocument(doc)).toBe(false);
  });

  it("does not initialize a tree-nav container with no filter input", () => {
    const container = new FakeElement();
    container.setQuery("[data-tree-filter-input]", null);

    const doc = {
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [container] : [],
    } as unknown as Document;

    expect(initTreeFilterDocument(doc)).toBe(false);
  });
});
