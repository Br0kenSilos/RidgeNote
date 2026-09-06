import { describe, expect, it } from "vitest";

import {
  TREE_COLLAPSED_STORAGE_KEY,
  TREE_DEFAULT_WIDTH,
  TREE_KEYBOARD_STEP,
  TREE_MAX_WIDTH,
  TREE_MIN_WIDTH,
  TREE_NARROW_BREAKPOINT,
  TREE_WIDTH_STORAGE_KEY,
  DrawerController,
  TreeShellController,
  clampTreeWidth,
  initNarrowDrawerDocument,
  initTreeShellDocument,
  parseStoredTreeCollapsed,
  parseStoredTreeWidth,
  type TreeShellBackdropLike,
  type TreeShellDocumentLike,
  type TreeShellDrawerCloseLike,
  type TreeShellDrawerLike,
  type TreeShellDrawerToggleLike,
  type TreeShellFocusableLike,
  type TreeShellInertTargetLike,
  type TreeShellStorageLike,
  type TreeShellWindowLike,
} from "./tree-shell";

class MockStorage implements TreeShellStorageLike {
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

class MockRoot {
  attributes = new Map<string, string>();
  dataset: Record<string, string> = {};
  style = {
    properties: new Map<string, string>(),
    setProperty: (name: string, value: string) => {
      this.style.properties.set(name, value);
    },
  };

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }
}

class MockToggle {
  attributes = new Map<string, string>();
  hidden = false;
  focusCalls = 0;
  private clickListener: ((event: { preventDefault(): void }) => void) | null =
    null;

  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void {
    if (type === "click") {
      this.clickListener = listener;
    }
  }

  focus(): void {
    this.focusCalls += 1;
  }

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }

  triggerClick(): void {
    this.clickListener?.({ preventDefault() {} });
  }
}

class MockTreePane {
  hidden = false;
  left = 100;

  getBoundingClientRect(): { left: number } {
    return { left: this.left };
  }
}

class MockSeparator {
  attributes = new Map<string, string>();
  hidden = false;
  capturedPointerId: number | null = null;
  releasedPointerId: number | null = null;
  private keydownListener:
    | ((event: { key: string; preventDefault(): void }) => void)
    | null = null;
  private pointerdownListener:
    | ((event: {
        clientX: number;
        pointerId: number;
        preventDefault(): void;
      }) => void)
    | null = null;

  addEventListener(
    type: "keydown" | "pointerdown",
    listener: (
      event:
        | { key: string; preventDefault(): void }
        | { clientX: number; pointerId: number; preventDefault(): void },
    ) => void,
  ): void {
    if (type === "keydown") {
      this.keydownListener = listener as (event: {
        key: string;
        preventDefault(): void;
      }) => void;
      return;
    }
    this.pointerdownListener = listener as (event: {
      clientX: number;
      pointerId: number;
      preventDefault(): void;
    }) => void;
  }

  releasePointerCapture(pointerId: number): void {
    this.releasedPointerId = pointerId;
  }

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }

  setPointerCapture(pointerId: number): void {
    this.capturedPointerId = pointerId;
  }

  triggerKeydown(key: string): void {
    this.keydownListener?.({ key, preventDefault() {} });
  }

  triggerPointerdown(clientX: number, pointerId = 1): void {
    this.pointerdownListener?.({ clientX, pointerId, preventDefault() {} });
  }
}

class MockWindow implements TreeShellWindowLike {
  innerWidth = 1200;
  private listeners = new Map<
    "pointercancel" | "pointermove" | "pointerup" | "resize",
    Array<
      (event?: {
        clientX: number;
        pointerId: number;
        preventDefault(): void;
      }) => void
    >
  >();

  addEventListener(
    type: "pointercancel" | "pointermove" | "pointerup" | "resize",
    listener: (event?: {
      clientX: number;
      pointerId: number;
      preventDefault(): void;
    }) => void,
  ): void {
    const current = this.listeners.get(type) ?? [];
    current.push(listener);
    this.listeners.set(type, current);
  }

  triggerPointermove(clientX: number, pointerId = 1): void {
    for (const listener of this.listeners.get("pointermove") ?? []) {
      listener({ clientX, pointerId, preventDefault() {} });
    }
  }

  triggerPointerup(clientX: number, pointerId = 1): void {
    for (const listener of this.listeners.get("pointerup") ?? []) {
      listener({ clientX, pointerId, preventDefault() {} });
    }
  }

  triggerResize(width: number): void {
    this.innerWidth = width;
    for (const listener of this.listeners.get("resize") ?? []) {
      listener();
    }
  }
}

function createControllerContext() {
  const root = new MockRoot();
  const storage = new MockStorage();
  const toggle = new MockToggle();
  const treePane = new MockTreePane();
  const separator = new MockSeparator();
  const windowLike = new MockWindow();
  const controller = new TreeShellController({
    root,
    separator,
    storage,
    toggle,
    treePane,
    windowLike,
  });

  return {
    controller,
    root,
    separator,
    storage,
    toggle,
    treePane,
    windowLike,
  };
}

describe("tree-shell helpers", () => {
  it("defaults to expanded when collapsed persistence is absent or invalid", () => {
    expect(parseStoredTreeCollapsed(null)).toBe(false);
    expect(parseStoredTreeCollapsed("false")).toBe(false);
    expect(parseStoredTreeCollapsed("banana")).toBe(false);
  });

  it("parses and clamps persisted widths safely", () => {
    expect(parseStoredTreeWidth(null)).toBe(TREE_DEFAULT_WIDTH);
    expect(parseStoredTreeWidth("240")).toBe(240);
    expect(parseStoredTreeWidth("150")).toBe(TREE_MIN_WIDTH);
    expect(parseStoredTreeWidth("999")).toBe(TREE_MAX_WIDTH);
    expect(parseStoredTreeWidth("wat")).toBe(TREE_DEFAULT_WIDTH);
  });

  it("clamps raw widths to the approved bounds", () => {
    expect(clampTreeWidth(TREE_MIN_WIDTH - 50)).toBe(TREE_MIN_WIDTH);
    expect(clampTreeWidth(TREE_MAX_WIDTH + 50)).toBe(TREE_MAX_WIDTH);
    expect(clampTreeWidth(287.6)).toBe(288);
  });
});

describe("TreeShellController", () => {
  it("starts expanded with the approved default width", () => {
    const context = createControllerContext();

    expect(context.controller.getState()).toEqual({
      collapsed: false,
      narrow: false,
      width: TREE_DEFAULT_WIDTH,
    });
    expect(context.toggle.attributes.get("aria-expanded")).toBe("true");
    expect(context.toggle.attributes.get("aria-label")).toBe("Hide note tree");
    expect(context.toggle.attributes.get("data-tooltip")).toBe(
      "Hide note tree",
    );
    expect(context.separator.attributes.get("aria-valuenow")).toBe(
      String(TREE_DEFAULT_WIDTH),
    );
    expect(context.root.style.properties.get("--tree-width")).toBe("220px");
  });

  it("restores persisted collapsed and expanded states", () => {
    const collapsed = createControllerContext();
    collapsed.storage.values.set(TREE_COLLAPSED_STORAGE_KEY, "true");
    const collapsedController = new TreeShellController({
      root: collapsed.root,
      separator: collapsed.separator,
      storage: collapsed.storage,
      toggle: collapsed.toggle,
      treePane: collapsed.treePane,
      windowLike: collapsed.windowLike,
    });

    expect(collapsedController.getState().collapsed).toBe(true);
    expect(collapsed.toggle.attributes.get("aria-expanded")).toBe("false");
    expect(collapsed.toggle.attributes.get("aria-label")).toBe(
      "Show note tree",
    );
    expect(collapsed.toggle.attributes.get("data-tooltip")).toBe(
      "Show note tree",
    );
    expect(collapsed.treePane.hidden).toBe(true);
    expect(collapsed.separator.hidden).toBe(true);
  });

  it("syncs collapse toggle state, aria, focus, and persistence", () => {
    const context = createControllerContext();

    context.toggle.triggerClick();

    expect(context.controller.getState().collapsed).toBe(true);
    expect(context.storage.values.get(TREE_COLLAPSED_STORAGE_KEY)).toBe("true");
    expect(context.toggle.attributes.get("aria-expanded")).toBe("false");
    expect(context.toggle.attributes.get("aria-label")).toBe("Show note tree");
    expect(context.toggle.attributes.get("data-tooltip")).toBe(
      "Show note tree",
    );
    expect(context.toggle.focusCalls).toBe(1);
  });

  it("uses a persisted valid width and keeps separator ARIA values synchronized", () => {
    const context = createControllerContext();
    context.storage.values.set(TREE_WIDTH_STORAGE_KEY, "300");
    const controller = new TreeShellController({
      root: context.root,
      separator: context.separator,
      storage: context.storage,
      toggle: context.toggle,
      treePane: context.treePane,
      windowLike: context.windowLike,
    });

    expect(controller.getState().width).toBe(300);
    expect(context.separator.attributes.get("aria-valuemin")).toBe(
      String(TREE_MIN_WIDTH),
    );
    expect(context.separator.attributes.get("aria-valuemax")).toBe(
      String(TREE_MAX_WIDTH),
    );
    expect(context.separator.attributes.get("aria-valuenow")).toBe("300");
  });

  it("falls back safely when persisted width is invalid", () => {
    const context = createControllerContext();
    context.storage.values.set(TREE_WIDTH_STORAGE_KEY, "not-a-number");
    const controller = new TreeShellController({
      root: context.root,
      separator: context.separator,
      storage: context.storage,
      toggle: context.toggle,
      treePane: context.treePane,
      windowLike: context.windowLike,
    });

    expect(controller.getState().width).toBe(TREE_DEFAULT_WIDTH);
  });

  it("resizes by pointer movement and persists the final clamped width", () => {
    const context = createControllerContext();

    context.separator.triggerPointerdown(260, 7);
    context.windowLike.triggerPointermove(430, 7);
    expect(context.controller.getState().width).toBe(330);
    context.windowLike.triggerPointermove(1000, 7);
    expect(context.controller.getState().width).toBe(TREE_MAX_WIDTH);
    context.windowLike.triggerPointerup(1000, 7);

    expect(context.storage.values.get(TREE_WIDTH_STORAGE_KEY)).toBe(
      String(TREE_MAX_WIDTH),
    );
    expect(context.separator.capturedPointerId).toBe(7);
    expect(context.separator.releasedPointerId).toBe(7);
    expect(context.root.attributes.get("data-tree-resizing")).toBe("false");
  });

  it("supports ArrowLeft and ArrowRight keyboard resizing", () => {
    const context = createControllerContext();

    context.separator.triggerKeydown("ArrowRight");
    expect(context.controller.getState().width).toBe(
      TREE_DEFAULT_WIDTH + TREE_KEYBOARD_STEP,
    );
    context.separator.triggerKeydown("ArrowLeft");
    expect(context.controller.getState().width).toBe(TREE_DEFAULT_WIDTH);
    expect(context.storage.values.get(TREE_WIDTH_STORAGE_KEY)).toBe(
      String(TREE_DEFAULT_WIDTH),
    );
  });

  it("supports Home and End keyboard resizing", () => {
    const context = createControllerContext();

    context.separator.triggerKeydown("Home");
    expect(context.controller.getState().width).toBe(TREE_MIN_WIDTH);
    context.separator.triggerKeydown("End");
    expect(context.controller.getState().width).toBe(TREE_MAX_WIDTH);
  });

  it("survives localStorage read and write failures", () => {
    const context = createControllerContext();
    context.storage.getError = true;
    context.storage.setError = true;

    const controller = new TreeShellController({
      root: context.root,
      separator: context.separator,
      storage: context.storage,
      toggle: context.toggle,
      treePane: context.treePane,
      windowLike: context.windowLike,
    });

    context.toggle.triggerClick();
    context.separator.triggerKeydown("End");

    expect(controller.getState().collapsed).toBe(true);
    expect(controller.getState().width).toBe(TREE_MAX_WIDTH);
  });

  it("suppresses tree controls on narrow layouts without losing expanded state", () => {
    const context = createControllerContext();

    context.windowLike.triggerResize(639);

    expect(context.controller.getState().narrow).toBe(true);
    expect(context.toggle.hidden).toBe(true);
    expect(context.treePane.hidden).toBe(true);
    expect(context.separator.hidden).toBe(true);
    expect(context.root.attributes.get("data-tree-layout")).toBe("narrow");
  });
});

class MockDrawerToggle implements TreeShellDrawerToggleLike {
  attributes = new Map<string, string>();
  hidden = true;
  focusCalls = 0;
  private clickListener: ((event: { preventDefault(): void }) => void) | null =
    null;

  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void {
    if (type === "click") this.clickListener = listener;
  }

  focus(): void {
    this.focusCalls += 1;
  }

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }

  triggerClick(): void {
    this.clickListener?.({ preventDefault() {} });
  }
}

class MockDrawerClose implements TreeShellDrawerCloseLike {
  focusCalls = 0;
  private clickListener: ((event: { preventDefault(): void }) => void) | null =
    null;

  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void {
    if (type === "click") this.clickListener = listener;
  }

  focus(): void {
    this.focusCalls += 1;
  }

  triggerClick(): void {
    this.clickListener?.({ preventDefault() {} });
  }
}

class MockFocusable implements TreeShellFocusableLike {
  focusCalls = 0;
  focus(): void {
    this.focusCalls += 1;
  }
}

class MockDrawer implements TreeShellDrawerLike {
  attributes = new Map<string, string>([["inert", ""]]);
  dataset: Record<string, string> = {};
  hidden = true;
  focusables: MockFocusable[] = [new MockFocusable(), new MockFocusable()];
  private keydownListener:
    | ((event: {
        key: string;
        shiftKey: boolean;
        preventDefault(): void;
      }) => void)
    | null = null;

  addEventListener(
    type: "keydown",
    listener: (event: {
      key: string;
      shiftKey: boolean;
      preventDefault(): void;
    }) => void,
  ): void {
    if (type === "keydown") this.keydownListener = listener;
  }

  querySelectorAll(selector: string): ArrayLike<TreeShellFocusableLike> {
    return selector ? this.focusables : [];
  }

  removeAttribute(name: string): void {
    this.attributes.delete(name);
  }

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }

  triggerKeydown(key: string, shiftKey = false): void {
    this.keydownListener?.({ key, shiftKey, preventDefault() {} });
  }
}

class MockBackdrop implements TreeShellBackdropLike {
  hidden = true;
  private clickListener: (() => void) | null = null;

  addEventListener(type: "click", listener: () => void): void {
    if (type === "click") this.clickListener = listener;
  }

  triggerClick(): void {
    this.clickListener?.();
  }
}

class MockDocument implements TreeShellDocumentLike {
  body = { style: { overflow: "" } };
  documentElement = { style: { overflow: "" } };
  activeElement: TreeShellFocusableLike | null = null;
  private keydownListeners: Array<
    (event: { key: string; shiftKey: boolean; preventDefault(): void }) => void
  > = [];

  addEventListener(
    type: "keydown",
    listener: (event: {
      key: string;
      shiftKey: boolean;
      preventDefault(): void;
    }) => void,
  ): void {
    if (type === "keydown") this.keydownListeners.push(listener);
  }

  triggerKeydown(key: string, shiftKey = false): void {
    for (const l of this.keydownListeners) {
      l({ key, shiftKey, preventDefault() {} });
    }
  }
}

class MockInertTarget implements TreeShellInertTargetLike {
  attributes = new Map<string, string | null>();

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }

  removeAttribute(name: string): void {
    this.attributes.delete(name);
  }
}

function createDrawerContext(narrowWidth = 400) {
  const base = createControllerContext();
  base.windowLike.innerWidth = narrowWidth;

  const drawerToggle = new MockDrawerToggle();
  const drawerClose = new MockDrawerClose();
  const drawer = new MockDrawer();
  const backdrop = new MockBackdrop();
  const documentLike = new MockDocument();
  const inertTarget = new MockInertTarget();

  const controller = new TreeShellController({
    root: base.root,
    separator: base.separator,
    storage: base.storage,
    toggle: base.toggle,
    treePane: base.treePane,
    windowLike: base.windowLike,
    drawerToggle,
    drawerClose,
    drawer,
    backdrop,
    documentLike,
    inertTargets: [inertTarget],
  });

  return {
    backdrop,
    controller,
    documentLike,
    drawer,
    drawerClose,
    drawerToggle,
    inertTarget,
    windowLike: base.windowLike,
  };
}

describe("TreeShellController drawer", () => {
  it("hides drawer trigger on wide layout and shows it on narrow", () => {
    const ctx = createDrawerContext(1200);

    expect(ctx.drawerToggle.hidden).toBe(true);

    ctx.windowLike.triggerResize(400);

    expect(ctx.drawerToggle.hidden).toBe(false);
  });

  it("opens the drawer and sets open state when trigger is clicked on narrow", () => {
    const ctx = createDrawerContext(400);

    ctx.drawerToggle.triggerClick();

    expect(ctx.drawer.attributes.get("data-open")).toBe("true");
    expect(ctx.drawer.hidden).toBe(false);
    expect(ctx.backdrop.hidden).toBe(false);
    expect(ctx.drawerToggle.attributes.get("aria-expanded")).toBe("true");
  });

  it("does not open the drawer when trigger is clicked on wide layout", () => {
    const ctx = createDrawerContext(1200);

    ctx.drawerToggle.triggerClick();

    expect(ctx.drawer.hidden).toBe(true);
    expect(ctx.backdrop.hidden).toBe(true);
  });

  it("closes the drawer and restores focus when the close button is clicked", () => {
    const ctx = createDrawerContext(400);
    ctx.drawerToggle.triggerClick();

    ctx.drawerClose.triggerClick();

    expect(ctx.drawer.attributes.get("data-open")).toBe("false");
    expect(ctx.drawer.hidden).toBe(true);
    expect(ctx.backdrop.hidden).toBe(true);
    expect(ctx.drawerToggle.focusCalls).toBeGreaterThan(0);
  });

  it("closes the drawer when the backdrop is clicked", () => {
    const ctx = createDrawerContext(400);
    ctx.drawerToggle.triggerClick();

    ctx.backdrop.triggerClick();

    expect(ctx.drawer.attributes.get("data-open")).toBe("false");
    expect(ctx.drawer.hidden).toBe(true);
    expect(ctx.backdrop.hidden).toBe(true);
  });

  it("closes the drawer on Escape key", () => {
    const ctx = createDrawerContext(400);
    ctx.drawerToggle.triggerClick();

    ctx.documentLike.triggerKeydown("Escape");

    expect(ctx.drawer.attributes.get("data-open")).toBe("false");
    expect(ctx.drawerToggle.attributes.get("aria-expanded")).toBe("false");
  });

  it("focuses the first focusable element in the drawer when opening", () => {
    const ctx = createDrawerContext(400);

    ctx.drawerToggle.triggerClick();

    expect(ctx.drawer.focusables[0].focusCalls).toBe(1);
  });

  it("applies inert to targets on open and removes it on close", () => {
    const ctx = createDrawerContext(400);

    ctx.drawerToggle.triggerClick();
    expect(ctx.inertTarget.attributes.get("inert")).toBe("");

    ctx.drawerClose.triggerClick();
    expect(ctx.inertTarget.attributes.has("inert")).toBe(false);
  });

  it("locks body scroll on open and restores it on close", () => {
    const ctx = createDrawerContext(400);

    ctx.drawerToggle.triggerClick();
    expect(ctx.documentLike.body.style.overflow).toBe("hidden");

    ctx.backdrop.triggerClick();
    expect(ctx.documentLike.body.style.overflow).toBe("");
  });

  it("locks documentElement (html) scroll on open and restores it on close", () => {
    // Locking `body` alone
    // leaves the page scrollable via wheel input over non-scrolling drawer
    // regions (header, footer, empty space) in a real narrow browser,
    // because a narrow-layout rule gives `html` its own
    // explicit `overflow-x: hidden`, which disqualifies the standard
    // body-to-viewport overflow-propagation mechanism -- `html` ends up
    // the actual scrolling element, unlocked by a body-only fix.
    const ctx = createDrawerContext(400);

    ctx.drawerToggle.triggerClick();
    expect(ctx.documentLike.documentElement.style.overflow).toBe("hidden");

    ctx.backdrop.triggerClick();
    expect(ctx.documentLike.documentElement.style.overflow).toBe("");
  });

  it("closes the drawer without restoring focus when resizing to wide", () => {
    const ctx = createDrawerContext(400);
    ctx.drawerToggle.triggerClick();

    const focusCallsBefore = ctx.drawerToggle.focusCalls;
    ctx.windowLike.triggerResize(1200);

    expect(ctx.drawer.attributes.get("data-open")).toBe("false");
    expect(ctx.drawerToggle.focusCalls).toBe(focusCallsBefore);
  });

  it("closed drawer starts inert", () => {
    const ctx = createDrawerContext(400);

    expect(ctx.drawer.attributes.get("inert")).toBe("");
  });

  it("opening the drawer removes its inert attribute", () => {
    const ctx = createDrawerContext(400);

    ctx.drawerToggle.triggerClick();

    expect(ctx.drawer.attributes.has("inert")).toBe(false);
  });

  it("closing the drawer restores its inert attribute", () => {
    const ctx = createDrawerContext(400);
    ctx.drawerToggle.triggerClick();

    ctx.drawerClose.triggerClick();

    expect(ctx.drawer.attributes.get("inert")).toBe("");
  });

  it("Tab from the last focusable wraps to the first", () => {
    const ctx = createDrawerContext(400);
    ctx.drawerToggle.triggerClick();
    const last = ctx.drawer.focusables[ctx.drawer.focusables.length - 1];
    ctx.documentLike.activeElement = last;

    ctx.drawer.triggerKeydown("Tab", false);

    // focusables[0] was focused once on open and once more by the Tab wrap
    expect(ctx.drawer.focusables[0].focusCalls).toBe(2);
  });

  it("Shift+Tab from the first focusable wraps to the last", () => {
    const ctx = createDrawerContext(400);
    ctx.drawerToggle.triggerClick();
    ctx.documentLike.activeElement = ctx.drawer.focusables[0];

    ctx.drawer.triggerKeydown("Tab", true);

    const last = ctx.drawer.focusables[ctx.drawer.focusables.length - 1];
    expect(last.focusCalls).toBe(1);
  });
});

function createStandaloneDrawerContext(narrowWidth = 400) {
  const windowLike = new MockWindow();
  windowLike.innerWidth = narrowWidth;

  const drawerToggle = new MockDrawerToggle();
  const drawerClose = new MockDrawerClose();
  const drawer = new MockDrawer();
  const backdrop = new MockBackdrop();
  const documentLike = new MockDocument();
  const inertTarget = new MockInertTarget();

  const controller = new DrawerController({
    drawer,
    drawerToggle,
    drawerClose,
    backdrop,
    documentLike,
    inertTargets: [inertTarget],
    isNarrow: () => windowLike.innerWidth < TREE_NARROW_BREAKPOINT,
    windowLike,
  });

  return {
    backdrop,
    controller,
    documentLike,
    drawer,
    drawerClose,
    drawerToggle,
    inertTarget,
    windowLike,
  };
}

describe("DrawerController (standalone, no wide-tree elements)", () => {
  it("opens and closes without any wide-tree shell present", () => {
    const ctx = createStandaloneDrawerContext(400);

    ctx.drawerToggle.triggerClick();
    expect(ctx.drawer.attributes.get("data-open")).toBe("true");
    expect(ctx.controller.isOpen()).toBe(true);

    ctx.drawerClose.triggerClick();
    expect(ctx.drawer.attributes.get("data-open")).toBe("false");
    expect(ctx.controller.isOpen()).toBe(false);
  });

  it("does not open on trigger click at wide layout", () => {
    const ctx = createStandaloneDrawerContext(1200);

    ctx.drawerToggle.triggerClick();

    expect(ctx.drawer.hidden).toBe(true);
  });

  it("focuses the first focusable element on open and restores focus to the toggle on close", () => {
    const ctx = createStandaloneDrawerContext(400);

    ctx.drawerToggle.triggerClick();
    expect(ctx.drawer.focusables[0].focusCalls).toBe(1);

    ctx.drawerClose.triggerClick();
    expect(ctx.drawerToggle.focusCalls).toBeGreaterThan(0);
  });

  it("closes on Escape", () => {
    const ctx = createStandaloneDrawerContext(400);
    ctx.drawerToggle.triggerClick();

    ctx.documentLike.triggerKeydown("Escape");

    expect(ctx.drawer.attributes.get("data-open")).toBe("false");
  });

  it("closes on backdrop click", () => {
    const ctx = createStandaloneDrawerContext(400);
    ctx.drawerToggle.triggerClick();

    ctx.backdrop.triggerClick();

    expect(ctx.drawer.attributes.get("data-open")).toBe("false");
  });

  it("contains focus with Tab wrap from the last focusable to the first", () => {
    const ctx = createStandaloneDrawerContext(400);
    ctx.drawerToggle.triggerClick();
    const last = ctx.drawer.focusables[ctx.drawer.focusables.length - 1];
    ctx.documentLike.activeElement = last;

    ctx.drawer.triggerKeydown("Tab", false);

    // focusables[0] was focused once on open and once more by the Tab wrap
    expect(ctx.drawer.focusables[0].focusCalls).toBe(2);
  });

  it("locks body scroll on open and restores it on close", () => {
    const ctx = createStandaloneDrawerContext(400);

    ctx.drawerToggle.triggerClick();
    expect(ctx.documentLike.body.style.overflow).toBe("hidden");

    ctx.backdrop.triggerClick();
    expect(ctx.documentLike.body.style.overflow).toBe("");
  });

  it("locks documentElement (html) scroll on open and restores it on close", () => {
    const ctx = createStandaloneDrawerContext(400);

    ctx.drawerToggle.triggerClick();
    expect(ctx.documentLike.documentElement.style.overflow).toBe("hidden");

    ctx.backdrop.triggerClick();
    expect(ctx.documentLike.documentElement.style.overflow).toBe("");
  });

  it("applies inert to targets on open and removes it on close", () => {
    const ctx = createStandaloneDrawerContext(400);

    ctx.drawerToggle.triggerClick();
    expect(ctx.inertTarget.attributes.get("inert")).toBe("");

    ctx.drawerClose.triggerClick();
    expect(ctx.inertTarget.attributes.has("inert")).toBe(false);
  });

  it("closes automatically when resized back to wide layout via its own resize listener", () => {
    const ctx = createStandaloneDrawerContext(400);
    ctx.drawerToggle.triggerClick();

    ctx.windowLike.triggerResize(1200);

    expect(ctx.drawer.attributes.get("data-open")).toBe("false");
  });
});

describe("initNarrowDrawerDocument", () => {
  it("returns false when a wide tree shell is present (handled by initTreeShellDocument instead)", () => {
    const doc = {
      querySelector: (selector: string) =>
        selector === "[data-tree-shell]" ? {} : null,
    } as unknown as Document;

    expect(initNarrowDrawerDocument(doc)).toBe(false);
  });

  it("returns false when no drawer is present", () => {
    const doc = {
      querySelector: () => null,
    } as unknown as Document;

    expect(initNarrowDrawerDocument(doc)).toBe(false);
  });

  it("initializes a standalone drawer only once", () => {
    const drawerToggle = new MockDrawerToggle();
    const drawerClose = new MockDrawerClose();
    const drawer = new MockDrawer() as MockDrawer & {
      querySelector(selector: string): MockDrawerClose | null;
    };
    drawer.querySelector = (selector: string) =>
      selector === "[data-drawer-close]" ? drawerClose : null;

    const doc = {
      addEventListener: () => {},
      body: { style: { overflow: "" } },
      documentElement: { style: { overflow: "" } },
      activeElement: null,
      querySelector: (selector: string) => {
        if (selector === "[data-tree-shell]") return null;
        if (selector === "[data-drawer]") return drawer;
        if (selector === "[data-drawer-toggle]") return drawerToggle;
        return null;
      },
      querySelectorAll: () => [],
    } as unknown as Document;
    const windowLike = new MockWindow();
    windowLike.innerWidth = 400;

    expect(initNarrowDrawerDocument(doc, windowLike)).toBe(true);
    expect(drawer.dataset.initialized).toBe("true");
    expect(initNarrowDrawerDocument(doc, windowLike)).toBe(false);

    drawerToggle.triggerClick();
    expect(drawer.attributes.get("data-open")).toBe("true");
  });
});

describe("initTreeShellDocument", () => {
  it("returns false when no tree shell is present", () => {
    const doc = {
      querySelector: () => null,
    } as unknown as Document;

    expect(initTreeShellDocument(doc)).toBe(false);
  });

  it("initializes a matching tree shell only once", () => {
    const root = new MockRoot() as MockRoot & {
      querySelector(
        selector: string,
      ): MockSeparator | MockToggle | MockTreePane | null;
    };
    const toggle = new MockToggle();
    const pane = new MockTreePane();
    const separator = new MockSeparator();
    root.querySelector = (innerSelector: string) => {
      if (innerSelector === "[data-tree-toggle]") {
        return toggle;
      }
      if (innerSelector === "[data-tree-pane]") {
        return pane;
      }
      if (innerSelector === "[data-tree-separator]") {
        return separator;
      }
      return null;
    };
    const doc = {
      querySelector: (selector: string) => {
        if (selector === "[data-tree-shell]") {
          return root;
        }
        return null;
      },
    } as unknown as Document;
    const storage = new MockStorage();
    const windowLike = new MockWindow();

    expect(initTreeShellDocument(doc, storage, windowLike)).toBe(true);
    expect(root.dataset.initialized).toBe("true");
    expect(initTreeShellDocument(doc, storage, windowLike)).toBe(false);
  });
});
