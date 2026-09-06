import { describe, expect, it } from "vitest";

import {
  computeNearestEdgeScrollDelta,
  initDrawerActiveRowRevealOnOpenDocument,
  initTreeActiveRowScrollDocument,
  revealActiveRowInViewport,
  type TreeScrollClickTargetLike,
  type TreeScrollElementLike,
  type TreeScrollRectLike,
} from "./tree-active-row-scroll";

class FakeRect implements TreeScrollRectLike {
  constructor(
    public top: number,
    public bottom: number,
  ) {}
}

describe("computeNearestEdgeScrollDelta", () => {
  it("returns 0 when the row is already fully visible", () => {
    const viewport = {
      getBoundingClientRect: () => new FakeRect(0, 400),
      scrollTop: 0,
    };
    const row = { getBoundingClientRect: () => new FakeRect(100, 130) };

    expect(computeNearestEdgeScrollDelta(viewport, row)).toBe(0);
  });

  it("returns a negative delta when the row is above the visible area", () => {
    const viewport = {
      getBoundingClientRect: () => new FakeRect(0, 400),
      scrollTop: 0,
    };
    const row = { getBoundingClientRect: () => new FakeRect(-50, -20) };

    expect(computeNearestEdgeScrollDelta(viewport, row)).toBe(-50);
  });

  it("returns a positive delta when the row is below the visible area", () => {
    const viewport = {
      getBoundingClientRect: () => new FakeRect(0, 400),
      scrollTop: 0,
    };
    const row = { getBoundingClientRect: () => new FakeRect(420, 450) };

    expect(computeNearestEdgeScrollDelta(viewport, row)).toBe(50);
  });

  it("prefers revealing the top edge when a row is taller than the viewport", () => {
    // Nearest-edge, not centering: when a row spans both edges (taller
    // than the viewport itself), the top-edge check runs first.
    const viewport = {
      getBoundingClientRect: () => new FakeRect(0, 100),
      scrollTop: 0,
    };
    const row = { getBoundingClientRect: () => new FakeRect(-10, 200) };

    expect(computeNearestEdgeScrollDelta(viewport, row)).toBe(-10);
  });
});

describe("revealActiveRowInViewport", () => {
  it("is a no-op when there is no active row", () => {
    const viewport = {
      getBoundingClientRect: () => new FakeRect(0, 400),
      scrollTop: 25,
    };

    revealActiveRowInViewport(viewport, null);

    expect(viewport.scrollTop).toBe(25);
  });

  it("does not move the viewport when the row is already visible", () => {
    const viewport = {
      getBoundingClientRect: () => new FakeRect(0, 400),
      scrollTop: 25,
    };
    const row = { getBoundingClientRect: () => new FakeRect(100, 130) };

    revealActiveRowInViewport(viewport, row);

    expect(viewport.scrollTop).toBe(25);
  });

  it("scrolls down by exactly the delta needed to reveal a row below the fold", () => {
    const viewport = {
      getBoundingClientRect: () => new FakeRect(0, 400),
      scrollTop: 0,
    };
    const row = { getBoundingClientRect: () => new FakeRect(500, 530) };

    revealActiveRowInViewport(viewport, row);

    expect(viewport.scrollTop).toBe(130);
  });

  it("scrolls up by exactly the delta needed to reveal a row above the visible area", () => {
    const viewport = {
      getBoundingClientRect: () => new FakeRect(0, 400),
      scrollTop: 200,
    };
    const row = { getBoundingClientRect: () => new FakeRect(-40, -10) };

    revealActiveRowInViewport(viewport, row);

    expect(viewport.scrollTop).toBe(160);
  });
});

class FakeTreeElement implements TreeScrollElementLike {
  offsetParent: unknown = {};
  scrollTop = 0;
  private readonly children = new Map<string, FakeTreeElement>();
  private rect: TreeScrollRectLike = new FakeRect(0, 400);

  setRect(rect: TreeScrollRectLike): this {
    this.rect = rect;
    return this;
  }

  setChild(selector: string, child: FakeTreeElement): this {
    this.children.set(selector, child);
    return this;
  }

  getBoundingClientRect(): TreeScrollRectLike {
    return this.rect;
  }

  querySelector<T = TreeScrollElementLike>(selector: string): T | null {
    return (this.children.get(selector) as unknown as T) ?? null;
  }
}

class FakeDrawerToggle implements TreeScrollClickTargetLike {
  private listener: (() => void) | null = null;

  addEventListener(_type: "click", listener: () => void): void {
    this.listener = listener;
  }

  click(): void {
    this.listener?.();
  }
}

class FakeTreeDocument {
  private containers: FakeTreeElement[] = [];
  private drawerToggle: FakeDrawerToggle | null = null;

  setContainers(containers: FakeTreeElement[]): this {
    this.containers = containers;
    return this;
  }

  setDrawerToggle(toggle: FakeDrawerToggle | null): this {
    this.drawerToggle = toggle;
    return this;
  }

  querySelectorAll<T = TreeScrollElementLike>(selector: string): ArrayLike<T> {
    if (selector === ".tree-nav") {
      return this.containers as unknown as T[];
    }
    return [];
  }

  querySelector<T = TreeScrollClickTargetLike>(selector: string): T | null {
    if (selector === "[data-drawer-toggle]") {
      return (this.drawerToggle as unknown as T) ?? null;
    }
    return null;
  }
}

function makeContainer(options: {
  viewportVisible?: boolean;
  hasActiveRow?: boolean;
  rowRect?: TreeScrollRectLike;
  viewportRect?: TreeScrollRectLike;
}): { container: FakeTreeElement; viewport: FakeTreeElement } {
  const viewport = new FakeTreeElement();
  viewport.offsetParent = options.viewportVisible === false ? null : {};
  viewport.setRect(options.viewportRect ?? new FakeRect(0, 400));

  if (options.hasActiveRow !== false) {
    const row = new FakeTreeElement().setRect(
      options.rowRect ?? new FakeRect(100, 130),
    );
    viewport.setChild(".tree-nav__row--current", row);
  }

  const container = new FakeTreeElement().setChild(
    ".tree-nav__viewport",
    viewport,
  );
  return { container, viewport };
}

describe("initTreeActiveRowScrollDocument", () => {
  it("reveals an active row that is below the visible tree viewport", () => {
    const { container, viewport } = makeContainer({
      rowRect: new FakeRect(500, 530),
    });
    const doc = new FakeTreeDocument().setContainers([container]);

    const revealed = initTreeActiveRowScrollDocument(doc);

    expect(revealed).toBe(true);
    expect(viewport.scrollTop).toBe(130);
  });

  it("reveals an active row that is above the visible tree viewport", () => {
    const { container, viewport } = makeContainer({
      rowRect: new FakeRect(-40, -10),
      viewportRect: new FakeRect(0, 400),
    });
    viewport.scrollTop = 200;
    const doc = new FakeTreeDocument().setContainers([container]);

    initTreeActiveRowScrollDocument(doc);

    expect(viewport.scrollTop).toBe(160);
  });

  it("does not scroll when the active row is already fully visible", () => {
    const { container, viewport } = makeContainer({});
    const doc = new FakeTreeDocument().setContainers([container]);

    initTreeActiveRowScrollDocument(doc);

    expect(viewport.scrollTop).toBe(0);
  });

  it("does not force a collapsed desktop tree pane open, and does not scroll it", () => {
    const { container, viewport } = makeContainer({
      viewportVisible: false,
      rowRect: new FakeRect(500, 530),
    });
    const doc = new FakeTreeDocument().setContainers([container]);

    const revealed = initTreeActiveRowScrollDocument(doc);

    expect(revealed).toBe(false);
    expect(viewport.scrollTop).toBe(0);
  });

  it("does not force a closed narrow drawer open, and does not scroll it", () => {
    const { container, viewport } = makeContainer({
      viewportVisible: false,
      rowRect: new FakeRect(500, 530),
    });
    const doc = new FakeTreeDocument().setContainers([container]);

    initTreeActiveRowScrollDocument(doc);

    expect(viewport.scrollTop).toBe(0);
  });

  it("handles a tree copy with no active row safely (e.g. viewing Trash)", () => {
    const { container, viewport } = makeContainer({ hasActiveRow: false });
    const doc = new FakeTreeDocument().setContainers([container]);

    expect(() => initTreeActiveRowScrollDocument(doc)).not.toThrow();
    expect(viewport.scrollTop).toBe(0);
  });

  it("handles an active row nested inside a folder the same as one in Unfiled", () => {
    // The module has no folder/Unfiled-specific logic -- it only ever
    // looks for `.tree-nav__row--current` anywhere inside the viewport,
    // so a row several levels deep behaves identically to a top-level one.
    const { container, viewport } = makeContainer({
      rowRect: new FakeRect(600, 630),
    });
    const doc = new FakeTreeDocument().setContainers([container]);

    initTreeActiveRowScrollDocument(doc);

    expect(viewport.scrollTop).toBe(230);
  });

  it("reveals the wide tree and narrow drawer copies independently", () => {
    const wide = makeContainer({ rowRect: new FakeRect(500, 530) });
    const drawer = makeContainer({ rowRect: new FakeRect(-30, -5) });
    drawer.viewport.scrollTop = 100;
    const doc = new FakeTreeDocument().setContainers([
      wide.container,
      drawer.container,
    ]);

    initTreeActiveRowScrollDocument(doc);

    expect(wide.viewport.scrollTop).toBe(130);
    expect(drawer.viewport.scrollTop).toBe(70);
  });

  it("only moves the tree viewport's own scrollTop, never touching anything else", () => {
    const { container, viewport } = makeContainer({
      rowRect: new FakeRect(500, 530),
    });
    const doc = new FakeTreeDocument().setContainers([container]);
    const before = { ...container };

    initTreeActiveRowScrollDocument(doc);

    // The container itself exposes no scrollTop of its own in this
    // fake's shape -- only its `.tree-nav__viewport` child does -- so a
    // changed viewport with an unchanged container confirms the module
    // never reaches for any other scrollable element (e.g. the document).
    expect(viewport.scrollTop).toBe(130);
    expect(container).toMatchObject({
      offsetParent: before.offsetParent,
      scrollTop: before.scrollTop,
    });
  });

  it("does nothing on a page with no tree at all, such as a plain page", () => {
    const doc = new FakeTreeDocument().setContainers([]);

    expect(() => initTreeActiveRowScrollDocument(doc)).not.toThrow();
    expect(initTreeActiveRowScrollDocument(doc)).toBe(false);
  });

  it("is safe when a viewport has no active row because tree filtering has hidden it", () => {
    // Filtering only ever sets `hidden` on non-matching rows -- it never
    // removes them from the DOM -- but the active row itself could in
    // principle be filtered out. `querySelector` still finds it (hidden
    // elements remain queryable), and revealing a hidden row is a safe
    // no-op in a real browser since it renders no box; nothing here
    // should throw either way.
    const { container } = makeContainer({
      rowRect: new FakeRect(500, 530),
    });
    const doc = new FakeTreeDocument().setContainers([container]);

    expect(() => initTreeActiveRowScrollDocument(doc)).not.toThrow();
  });
});

describe("initDrawerActiveRowRevealOnOpenDocument", () => {
  it("reveals the drawer's active row once the drawer toggle is clicked", () => {
    // Simulates the drawer already being open by the time this listener
    // runs -- `DrawerController`'s own click listener (registered earlier
    // during app.ts's init sequence) always fires first on the same
    // element/event, so by the time this one runs `offsetParent` already
    // reflects the now-visible drawer.
    const { container, viewport } = makeContainer({
      rowRect: new FakeRect(500, 530),
    });
    const toggle = new FakeDrawerToggle();
    const doc = new FakeTreeDocument()
      .setContainers([container])
      .setDrawerToggle(toggle);

    initDrawerActiveRowRevealOnOpenDocument(doc);
    expect(viewport.scrollTop).toBe(0); // not yet revealed before the click

    toggle.click();

    expect(viewport.scrollTop).toBe(130);
  });

  it("does nothing until the toggle is actually clicked", () => {
    const { container, viewport } = makeContainer({
      rowRect: new FakeRect(500, 530),
    });
    const toggle = new FakeDrawerToggle();
    const doc = new FakeTreeDocument()
      .setContainers([container])
      .setDrawerToggle(toggle);

    initDrawerActiveRowRevealOnOpenDocument(doc);

    expect(viewport.scrollTop).toBe(0);
  });

  it("does not throw and returns false when there is no drawer toggle on the page", () => {
    const doc = new FakeTreeDocument().setContainers([]);

    expect(() => initDrawerActiveRowRevealOnOpenDocument(doc)).not.toThrow();
    expect(initDrawerActiveRowRevealOnOpenDocument(doc)).toBe(false);
  });

  it("re-running the reveal pass leaves an already-visible wide tree row untouched", () => {
    const { container, viewport } = makeContainer({});
    const toggle = new FakeDrawerToggle();
    const doc = new FakeTreeDocument()
      .setContainers([container])
      .setDrawerToggle(toggle);

    initDrawerActiveRowRevealOnOpenDocument(doc);
    toggle.click();

    expect(viewport.scrollTop).toBe(0);
  });
});
