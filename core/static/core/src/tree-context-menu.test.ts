import { describe, expect, it } from "vitest";

import {
  ROW_MENU_PORTAL_SELECTOR,
  computeRowMenuPosition,
  floatRowMenuPanel,
  initRowActionMenusDocument,
  unfloatRowMenuPanel,
  type RowMenuRect,
} from "./tree-context-menu";

type Listener<E> = (event: E) => void;

interface ContextMenuEvent {
  preventDefault(): void;
}

interface ClickEvent {
  target: unknown;
}

interface KeyEvent {
  key: string;
}

function rect(overrides: Partial<RowMenuRect> = {}): RowMenuRect {
  return {
    bottom: 0,
    height: 0,
    left: 0,
    right: 0,
    top: 0,
    width: 0,
    ...overrides,
  };
}

class MockTrigger {
  focusCalls = 0;
  private rect: RowMenuRect;

  constructor(triggerRect: RowMenuRect = rect()) {
    this.rect = triggerRect;
  }

  focus(): void {
    this.focusCalls += 1;
  }

  getBoundingClientRect(): RowMenuRect {
    return this.rect;
  }
}

/**
 * Mirrors the DOM contract `initRowActionMenusDocument` relies on for the
 * portal move: `appendChild` detaches a node from wherever it currently
 * lives before attaching it here, `removeChild` detaches, and `contains`
 * reports current membership -- exactly like a real `Element`.
 */
class MockContainer {
  children: unknown[] = [];
  attributes: Record<string, string> = {};

  setAttribute(name: string, value: string): void {
    this.attributes[name] = value;
  }

  appendChild(node: { parentElement?: unknown } & object): void {
    const prevParent = node.parentElement as MockContainer | undefined;
    if (prevParent && prevParent !== this) {
      prevParent.removeChild(node);
    }
    if (!this.children.includes(node)) {
      this.children.push(node);
    }
    (node as { parentElement?: unknown }).parentElement = this;
  }

  removeChild(node: unknown): void {
    this.children = this.children.filter((child) => child !== node);
  }

  /** Matches real `Node.contains()`: true for any descendant, not just direct children. */
  contains(node: unknown): boolean {
    return this.children.some(
      (child) =>
        child === node ||
        (child instanceof MockContainer && child.contains(node)),
    );
  }
}

class MockCancelButton {
  private listeners: Array<() => void> = [];

  addEventListener(_type: "click", listener: () => void): void {
    this.listeners.push(listener);
  }

  triggerClick(): void {
    this.listeners.forEach((listener) => listener());
  }
}

/** The active-note overflow's Rename trigger -- same clickable shape as Cancel. */
class MockRenameButton extends MockCancelButton {}

/** Stand-in for the note-detail title `<input>` Rename focuses/selects. */
class MockTitleInput {
  focusCalls = 0;
  selectCalls = 0;

  focus(): void {
    this.focusCalls += 1;
  }

  select(): void {
    this.selectCalls += 1;
  }
}

/** Minimal stand-in for a New Folder text input, tracking value/aria-invalid resets. */
class FakeFieldInput {
  value = "";
  ariaInvalidRemoved = false;

  removeAttribute(name: string): void {
    if (name === "aria-invalid") {
      this.ariaInvalidRemoved = true;
    }
  }
}

/** Minimal stand-in for New Folder's `.tree-nav__popover-field-error` paragraph. */
class FakeErrorParagraph {
  textContent = "";
  hidden = false;
}

/** Minimal stand-in for a nested `.tree-nav__action` (Rename/Move/New Folder) `<details>`. */
class FakeActionDisclosure {
  private _open = false;
  private toggleListeners: Array<() => void> = [];

  constructor(
    private readonly options: {
      isNewFolder?: boolean;
      input?: FakeFieldInput;
      errorEl?: FakeErrorParagraph;
    } = {},
  ) {}

  get open(): boolean {
    return this._open;
  }

  set open(value: boolean) {
    if (value === this._open) return;
    this._open = value;
    this.toggleListeners.forEach((listener) => listener());
  }

  addEventListener(_type: "toggle", listener: () => void): void {
    this.toggleListeners.push(listener);
  }

  matches(selector: string): boolean {
    return (
      Boolean(this.options.isNewFolder) &&
      selector === ".tree-nav__action--new-folder"
    );
  }

  querySelector(selector: string): unknown {
    if (selector === 'input[type="text"]') return this.options.input ?? null;
    if (selector === ".tree-nav__popover-field-error")
      return this.options.errorEl ?? null;
    return null;
  }
}

class MockPanel {
  style = {
    left: "",
    maxHeight: "",
    maxWidth: "",
    position: "",
    right: "",
    top: "",
  };
  parentElement: MockContainer | null = null;
  private rect: RowMenuRect;
  private insideNodes: unknown[] = [];
  private cancelButtons: MockCancelButton[] = [];
  private renameTrigger: MockRenameButton | null = null;
  private actionDisclosures: FakeActionDisclosure[] = [];

  constructor(panelRect: RowMenuRect = rect()) {
    this.rect = panelRect;
  }

  getBoundingClientRect(): RowMenuRect {
    return this.rect;
  }

  /** Registers a node as "inside" this panel, for outside-click tests. */
  addInsideNode(node: unknown): this {
    this.insideNodes.push(node);
    return this;
  }

  contains(node: unknown): boolean {
    return this.insideNodes.includes(node);
  }

  /** Single-Cancel-button convenience, unchanged sugar over `setCancelButtons`. */
  setCancelButton(button: MockCancelButton | null): this {
    this.cancelButtons = button ? [button] : [];
    return this;
  }

  /**
   * A real panel can
   * contain more than one `[data-row-menu-cancel]` button at once (the
   * active-note overflow's New note, New folder, and Move disclosures each
   * have their own, all inside the same outer panel) -- production wires
   * every match via `querySelectorAll`, so this fixture must be able to
   * model more than one coexisting in the same panel too.
   */
  setCancelButtons(buttons: MockCancelButton[]): this {
    this.cancelButtons = buttons;
    return this;
  }

  setRenameTrigger(button: MockRenameButton | null): this {
    this.renameTrigger = button;
    return this;
  }

  /** Registers a nested `.tree-nav__action` disclosure (Rename/Move) for the reflow/reset tests. */
  setActionDisclosures(disclosures: FakeActionDisclosure[]): this {
    this.actionDisclosures = disclosures;
    return this;
  }

  querySelector(selector: string): unknown {
    if (selector.includes("row-menu-cancel"))
      return this.cancelButtons[0] ?? null;
    if (selector.includes("note-rename-trigger")) return this.renameTrigger;
    return null;
  }

  querySelectorAll(selector: string): unknown[] {
    if (selector === ".tree-nav__action") return this.actionDisclosures;
    if (selector === "[data-row-menu-cancel]") return this.cancelButtons;
    return [];
  }
}

class FakeMenu {
  dataset: Record<string, string> = {};
  private _open = false;
  private toggleListeners: Array<() => void> = [];
  private container = new MockContainer();
  private rowEl: unknown = null;
  private drawerEl: unknown = null;
  private summary = new MockTrigger();
  private panel: MockPanel | null = new MockPanel();
  private isNewFolder = false;
  private newFolderInput: FakeFieldInput | null = null;
  private newFolderError: FakeErrorParagraph | null = null;

  constructor() {
    if (this.panel) {
      this.container.appendChild(this.panel);
    }
  }

  /** Marks this menu as the standalone tree-toolbar New Folder popover itself. */
  setNewFolder(input?: FakeFieldInput, errorEl?: FakeErrorParagraph): this {
    this.isNewFolder = true;
    this.newFolderInput = input ?? null;
    this.newFolderError = errorEl ?? null;
    return this;
  }

  matches(selector: string): boolean {
    return this.isNewFolder && selector === ".tree-nav__action--new-folder";
  }

  get open(): boolean {
    return this._open;
  }

  // Mirrors a real HTMLDetailsElement: assigning `.open` (by user
  // interaction or by script, e.g. this controller's own closeAll) fires
  // the "toggle" event whenever the value actually changes.
  set open(value: boolean) {
    if (value === this._open) return;
    this._open = value;
    this.triggerToggle();
  }

  setRow(rowEl: unknown): this {
    this.rowEl = rowEl;
    return this;
  }

  setDrawerAncestor(drawerEl: unknown): this {
    this.drawerEl = drawerEl;
    return this;
  }

  setPanel(panel: MockPanel | null): this {
    if (this.panel) {
      this.container.removeChild(this.panel);
    }
    this.panel = panel;
    if (panel) {
      this.container.appendChild(panel);
    }
    return this;
  }

  addContainedNode(node: unknown): this {
    this.container.appendChild(node as { parentElement?: unknown });
    return this;
  }

  get summaryEl(): MockTrigger {
    return this.summary;
  }

  get panelEl(): MockPanel | null {
    return this.panel;
  }

  contains(node: unknown): boolean {
    return this.container.contains(node);
  }

  appendChild(node: { parentElement?: unknown }): void {
    this.container.appendChild(node);
  }

  removeChild(node: unknown): void {
    this.container.removeChild(node);
  }

  querySelector(selector: string): unknown {
    if (selector === "summary") return this.summary;
    if (selector.includes("row-menu-panel")) return this.panel;
    if (selector === 'input[type="text"]') return this.newFolderInput;
    if (selector === ".tree-nav__popover-field-error")
      return this.newFolderError;
    return null;
  }

  closest(selector: string): unknown {
    if (selector.includes("data-drawer")) return this.drawerEl;
    if (selector.includes("data-note-id")) return this.rowEl;
    return null;
  }

  addEventListener(_type: "toggle", listener: () => void): void {
    this.toggleListeners.push(listener);
  }

  triggerToggle(): void {
    this.toggleListeners.forEach((listener) => listener());
  }

  setOpen(open: boolean): void {
    this.open = open;
  }
}

class FakeRow {
  private listeners: Array<Listener<ContextMenuEvent>> = [];

  addEventListener(
    _type: "contextmenu",
    listener: Listener<ContextMenuEvent>,
  ): void {
    this.listeners.push(listener);
  }

  triggerContextMenu(event: Partial<ContextMenuEvent> = {}): void {
    const defaultEvent: ContextMenuEvent = { preventDefault() {}, ...event };
    this.listeners.forEach((listener) => listener(defaultEvent));
  }
}

class FakeCloseTrigger {
  private listeners: Array<() => void> = [];

  addEventListener(_type: "click", listener: () => void): void {
    this.listeners.push(listener);
  }

  triggerClick(): void {
    this.listeners.forEach((listener) => listener());
  }
}

class FakeWindowLike {
  innerWidth = 1280;
  innerHeight = 900;
  private listeners: Array<() => void> = [];

  addEventListener(_type: "resize", listener: () => void): void {
    this.listeners.push(listener);
  }

  triggerResize(): void {
    this.listeners.forEach((listener) => listener());
  }
}

class FakeDocument {
  body = new MockContainer();
  private clickListeners: Array<Listener<ClickEvent>> = [];
  private keydownListeners: Array<Listener<KeyEvent>> = [];
  private queryMap = new Map<string, unknown>();
  private queryAllMap = new Map<string, unknown[]>();
  private elementsById = new Map<string, unknown>();

  setQuery(selector: string, value: unknown): this {
    this.queryMap.set(selector, value);
    return this;
  }

  /** Backs the Rename trigger's `getElementById` lookup of the title input. */
  setElementById(id: string, value: unknown): this {
    this.elementsById.set(id, value);
    return this;
  }

  getElementById(id: string): unknown {
    return this.elementsById.get(id) ?? null;
  }

  setQueryAll(selector: string, values: unknown[]): this {
    this.queryAllMap.set(selector, values);
    return this;
  }

  createElement(): MockContainer {
    return new MockContainer();
  }

  querySelector(selector: string): unknown {
    return this.queryMap.get(selector) ?? null;
  }

  querySelectorAll(selector: string): unknown[] {
    return this.queryAllMap.get(selector) ?? [];
  }

  addEventListener(
    type: "click" | "keydown",
    listener: Listener<ClickEvent> | Listener<KeyEvent>,
  ): void {
    if (type === "click") {
      this.clickListeners.push(listener as Listener<ClickEvent>);
    } else {
      this.keydownListeners.push(listener as Listener<KeyEvent>);
    }
  }

  triggerClick(target: unknown): void {
    this.clickListeners.forEach((listener) => listener({ target }));
  }

  triggerKeydown(key: string): void {
    this.keydownListeners.forEach((listener) => listener({ key }));
  }
}

function buildDoc(menus: FakeMenu[]) {
  const doc = new FakeDocument();
  doc.setQueryAll(".row-action-menu", menus);
  return doc;
}

describe("computeRowMenuPosition", () => {
  it("places the panel below the trigger, right-aligned, when there is room", () => {
    const triggerRect = rect({ bottom: 100, left: 300, right: 400, top: 80 });
    const panelRect = rect({ height: 150, width: 160 });

    const result = computeRowMenuPosition(triggerRect, panelRect, 1280, 900);

    expect(result.top).toBe(104); // triggerRect.bottom + 4px gap
    expect(result.left).toBe(240); // triggerRect.right - panelRect.width, well within bounds
  });

  it("flips the panel above the trigger when there is not enough room below", () => {
    const triggerRect = rect({ bottom: 880, left: 40, right: 120, top: 860 });
    const panelRect = rect({ height: 150, width: 160 });

    const result = computeRowMenuPosition(triggerRect, panelRect, 1280, 900);

    // 860 - 150 - 4 = 706, well above the trigger and within the viewport.
    expect(result.top).toBe(706);
  });

  it("clamps the top edge so the panel never runs off the bottom of a short viewport", () => {
    const triggerRect = rect({ bottom: 200, left: 10, right: 90, top: 180 });
    const panelRect = rect({ height: 150, width: 160 });

    // Viewport is only 220px tall; below (204) and above (26) both technically
    // fit within bounds once clamped, but below overflows so it flips above,
    // then that result is itself clamped within [4, viewportHeight - height - 4].
    const result = computeRowMenuPosition(triggerRect, panelRect, 1280, 220);

    expect(result.top).toBeGreaterThanOrEqual(4);
    expect(result.top + 150).toBeLessThanOrEqual(220 - 4 + 0.001);
  });

  it("clamps the left edge so the panel never runs off a narrow drawer's right edge", () => {
    const triggerRect = rect({ bottom: 100, left: 250, right: 270, top: 80 });
    const panelRect = rect({ height: 150, width: 160 });

    // A 280px-wide drawer viewport; right-aligning under the trigger (270 - 160 = 110)
    // actually fits here, but a trigger further right would not -- confirm clamping engages.
    const narrowResult = computeRowMenuPosition(
      triggerRect,
      panelRect,
      280,
      900,
    );
    expect(narrowResult.left).toBeGreaterThanOrEqual(4);
    expect(narrowResult.left + 160).toBeLessThanOrEqual(280 - 4 + 0.001);
  });

  it("clamps the left edge so the panel never runs off the left edge of the viewport", () => {
    const triggerRect = rect({ bottom: 100, left: 0, right: 20, top: 80 });
    const panelRect = rect({ height: 150, width: 160 });

    const result = computeRowMenuPosition(triggerRect, panelRect, 1280, 900);

    expect(result.left).toBeGreaterThanOrEqual(4);
  });

  it("defaults to right-aligning the panel's right edge with the trigger's right edge", () => {
    const triggerRect = rect({ bottom: 100, left: 40, right: 60, top: 80 });
    const panelRect = rect({ height: 150, width: 200 });

    const result = computeRowMenuPosition(triggerRect, panelRect, 1280, 900);

    // Right-aligned: left = triggerRect.right - panelRect.width = 60 - 200 = -140,
    // clamped to the viewport margin (4) since it would otherwise run off-screen.
    expect(result.left).toBe(4);
  });

  it("left-aligns the panel's left edge with the trigger's left edge when align is 'left'", () => {
    const triggerRect = rect({ bottom: 100, left: 40, right: 60, top: 80 });
    const panelRect = rect({ height: 150, width: 200 });

    const result = computeRowMenuPosition(
      triggerRect,
      panelRect,
      1280,
      900,
      "left",
    );

    // Left-aligned: left = triggerRect.left = 40, well within the viewport,
    // so no clamping is needed -- this is the fix for Move note/New Folder
    // shifting far to the left when right-aligned like a row menu.
    expect(result.left).toBe(40);
  });

  it("still clamps a left-aligned panel that would otherwise run off the right edge", () => {
    const triggerRect = rect({ bottom: 100, left: 1200, right: 1220, top: 80 });
    const panelRect = rect({ height: 150, width: 200 });

    const result = computeRowMenuPosition(
      triggerRect,
      panelRect,
      1280,
      900,
      "left",
    );

    expect(result.left).toBeLessThanOrEqual(1280 - 200 - 4);
  });
});

describe("floatRowMenuPanel / unfloatRowMenuPanel", () => {
  it("applies fixed positioning with viewport-computed coordinates", () => {
    const trigger = new MockTrigger(
      rect({ bottom: 300, left: 40, right: 200, top: 280 }),
    );
    const panel = new MockPanel(rect({ height: 150, width: 160 }));

    floatRowMenuPanel(trigger, panel, { innerHeight: 900, innerWidth: 1280 });

    expect(panel.style.position).toBe("fixed");
    expect(panel.style.top).toBe("304px");
    expect(panel.style.left).toBe("40px");
    expect(panel.style.right).toBe("auto");
  });

  it("is a no-op when the trigger or panel is missing", () => {
    const panel = new MockPanel();
    expect(() =>
      floatRowMenuPanel(null, panel, { innerHeight: 900, innerWidth: 1280 }),
    ).not.toThrow();
    expect(panel.style.position).toBe("");
  });

  it("reverts a panel to its stylesheet-defined position", () => {
    const panel = new MockPanel();
    panel.style.position = "fixed";
    panel.style.top = "304px";
    panel.style.left = "40px";
    panel.style.right = "auto";

    unfloatRowMenuPanel(panel);

    expect(panel.style.position).toBe("");
    expect(panel.style.top).toBe("");
    expect(panel.style.left).toBe("");
    expect(panel.style.right).toBe("");
  });

  /**
   * Regression coverage: `computeRowMenuPosition`'s existing `left` clamping only
   * protects the panel's *position*, not its *width* -- a panel whose
   * stylesheet `max-width` (20rem for most row menus, 24rem for Recent
   * notes) is wider than the actual viewport still ran off the right
   * edge at narrow widths, since nothing previously shrank the panel
   * itself before measuring it. `floatRowMenuPanel` now sets an inline
   * `max-width` (viewport width minus the same margin used for
   * position-clamping, on both sides) before calling
   * `getBoundingClientRect()`, so a real browser's layout engine
   * actually shrinks/wraps the panel's content before its measured width
   * is ever used for positioning.
   */
  it("sets an inline max-width derived from the viewport, before measuring the panel", () => {
    const trigger = new MockTrigger(
      rect({ bottom: 100, left: 300, right: 340, top: 80 }),
    );
    const panel = new MockPanel(rect({ height: 150, width: 160 }));

    floatRowMenuPanel(trigger, panel, { innerHeight: 700, innerWidth: 375 });

    // ROW_MENU_VIEWPORT_MARGIN is 4px on each side -> 375 - 8 = 367.
    expect(panel.style.maxWidth).toBe("367px");
  });

  it("scales the max-width down for narrower viewports, and up for wider ones -- one shared rule, not per-viewport constants", () => {
    const trigger = new MockTrigger(
      rect({ bottom: 100, left: 20, right: 60, top: 80 }),
    );
    const panel = new MockPanel(rect({ height: 150, width: 160 }));

    floatRowMenuPanel(trigger, panel, { innerHeight: 700, innerWidth: 320 });
    expect(panel.style.maxWidth).toBe("312px");

    floatRowMenuPanel(trigger, panel, { innerHeight: 700, innerWidth: 414 });
    expect(panel.style.maxWidth).toBe("406px");

    floatRowMenuPanel(trigger, panel, { innerHeight: 900, innerWidth: 1280 });
    expect(panel.style.maxWidth).toBe("1272px");
  });

  it("clears the inline max-width on unfloat, restoring the stylesheet-defined value", () => {
    const panel = new MockPanel();
    panel.style.maxWidth = "367px";

    unfloatRowMenuPanel(panel);

    expect(panel.style.maxWidth).toBe("");
  });

  /**
   * A panel whose
   * nested Rename/Move disclosure expanded can be taller than the
   * viewport itself. `max-width` alone (above) only guards horizontal
   * overflow; this mirrors that exact fix for the vertical axis, paired
   * with the stylesheet's own unconditional `overflow-y: auto` so a
   * panel that's actually taller than this bound scrolls internally
   * rather than being clipped or pushed off-screen.
   */
  it("sets an inline max-height derived from the viewport, before measuring the panel", () => {
    const trigger = new MockTrigger(
      rect({ bottom: 300, left: 40, right: 200, top: 280 }),
    );
    const panel = new MockPanel(rect({ height: 150, width: 160 }));

    floatRowMenuPanel(trigger, panel, { innerHeight: 700, innerWidth: 1280 });

    // ROW_MENU_VIEWPORT_MARGIN is 4px on each side -> 700 - 8 = 692.
    expect(panel.style.maxHeight).toBe("692px");
  });

  it("clears the inline max-height on unfloat, restoring the stylesheet-defined value", () => {
    const panel = new MockPanel();
    panel.style.maxHeight = "692px";

    unfloatRowMenuPanel(panel);

    expect(panel.style.maxHeight).toBe("");
  });
});

describe("initRowActionMenusDocument", () => {
  it("does nothing and returns false when no row-action menu exists", () => {
    const doc = buildDoc([]);
    const windowLike = new FakeWindowLike();

    expect(
      initRowActionMenusDocument(
        doc as unknown as Document,
        windowLike as unknown as never,
      ),
    ).toBe(false);
  });

  it("opens the menu on right-click of the owning row, suppressing the native context menu", () => {
    const row = new FakeRow();
    const menu = new FakeMenu().setRow(row);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();
    let prevented = false;

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    row.triggerContextMenu({
      preventDefault: () => {
        prevented = true;
      },
    });

    expect(prevented).toBe(true);
    expect(menu.open).toBe(true);
  });

  it("closes every other open row menu when one opens", () => {
    const menuA = new FakeMenu();
    const menuB = new FakeMenu();
    const doc = buildDoc([menuA, menuB]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menuA.setOpen(true);
    expect(menuA.open).toBe(true);

    menuB.setOpen(true);
    expect(menuB.open).toBe(true);
    expect(menuA.open).toBe(false);
  });

  it("closes an open menu on outside click", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    doc.triggerClick({});

    expect(menu.open).toBe(false);
  });

  it("does not close a menu on a click inside it", () => {
    const menu = new FakeMenu();
    const insideNode = {};
    menu.addContainedNode(insideNode);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    doc.triggerClick(insideNode);

    expect(menu.open).toBe(true);
  });

  it("closes an open menu on Escape and restores focus to its own summary", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    doc.triggerKeydown("Escape");

    expect(menu.open).toBe(false);
    expect(menu.summaryEl.focusCalls).toBe(1);
  });

  it("ignores non-Escape keys", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    doc.triggerKeydown("Tab");

    expect(menu.open).toBe(true);
  });

  it("closes every open menu on window resize", () => {
    const menuA = new FakeMenu();
    const menuB = new FakeMenu();
    const doc = buildDoc([menuA, menuB]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menuA.setOpen(true);
    menuB.setOpen(true);
    windowLike.triggerResize();

    expect(menuA.open).toBe(false);
    expect(menuB.open).toBe(false);
  });

  it("restores focus to the trigger of a menu closed by a window resize, mirroring Escape", () => {
    // Resizing while a
    // panel is open closes it cleanly and refocuses its trigger,
    // rather than leaving it clipped off-screen at the new viewport size.
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    windowLike.triggerResize();

    expect(menu.open).toBe(false);
    expect(menu.summaryEl.focusCalls).toBe(1);
  });

  it("does not focus the trigger of a menu that was already closed when a resize fires", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    windowLike.triggerResize();

    expect(menu.summaryEl.focusCalls).toBe(0);
  });

  it("reads the window's current width/height when opening, not a snapshot captured when the document was first initialized", () => {
    // A responsive-lifecycle defect: resizing
    // from wide/medium down to narrow without a reload, then opening
    // Recent notes or More note actions, positioned the panel using the
    // *original* (wide/medium) viewport width, because it had been
    // captured once into a plain object at init time and never updated.
    // A page reload re-ran initialization and recaptured the (by then
    // correct) narrow dimensions, which is exactly why the bug "went away"
    // after Ctrl+R -- masking a live-resize defect as a load-order one.
    const menu = new FakeMenu();
    menu.summaryEl.getBoundingClientRect = () =>
      rect({ bottom: 100, left: 900, right: 1000, top: 80 });
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();
    windowLike.innerWidth = 1280;
    windowLike.innerHeight = 900;

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    // Simulate a real resize down to a narrow viewport, exactly as a
    // browser would update `window.innerWidth`/`innerHeight` -- no reload.
    windowLike.innerWidth = 375;
    windowLike.innerHeight = 700;
    windowLike.triggerResize();

    menu.setOpen(true);

    // maxWidth is derived from the *current* viewport width
    // (`viewport.innerWidth - margin*2`, see floatRowMenuPanel): 375 - 8 =
    // 367px if the fix reads live values, versus the stale 1280 - 8 =
    // 1272px if it were still using the value captured at init time.
    expect(menu.panelEl?.style.maxWidth).toBe("367px");
  });

  it("re-reads live window dimensions on every open, so repeated resizes never leave the panel positioned for a stale width", () => {
    const menu = new FakeMenu();
    menu.summaryEl.getBoundingClientRect = () =>
      rect({ bottom: 100, left: 900, right: 1000, top: 80 });
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();
    windowLike.innerWidth = 1280;

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    windowLike.innerWidth = 320;
    windowLike.triggerResize();
    menu.setOpen(true);
    expect(menu.panelEl?.style.maxWidth).toBe("312px");
    menu.setOpen(false);

    windowLike.innerWidth = 1280;
    windowLike.triggerResize();
    menu.setOpen(true);
    expect(menu.panelEl?.style.maxWidth).toBe("1272px");
    menu.setOpen(false);

    windowLike.innerWidth = 414;
    windowLike.triggerResize();
    menu.setOpen(true);
    expect(menu.panelEl?.style.maxWidth).toBe("406px");
  });

  it("closes every open menu when the drawer-close trigger fires, for menus rendered inside the drawer", () => {
    const drawerMarker = {};
    const menu = new FakeMenu().setDrawerAncestor(drawerMarker);
    const doc = buildDoc([menu]);
    const closeTrigger = new FakeCloseTrigger();
    doc.setQuery("[data-drawer-close]", closeTrigger);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    closeTrigger.triggerClick();

    expect(menu.open).toBe(false);
  });

  it("closes every open menu when the drawer backdrop fires, for menus rendered inside the drawer", () => {
    const drawerMarker = {};
    const menu = new FakeMenu().setDrawerAncestor(drawerMarker);
    const doc = buildDoc([menu]);
    const backdrop = new FakeCloseTrigger();
    doc.setQuery("[data-drawer-backdrop]", backdrop);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    backdrop.triggerClick();

    expect(menu.open).toBe(false);
  });

  it("does not wire drawer close triggers when no menu is inside the drawer", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const closeTrigger = new FakeCloseTrigger();
    doc.setQuery("[data-drawer-close]", closeTrigger);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    closeTrigger.triggerClick();

    // The controller never registered a listener on this unrelated trigger.
    expect(menu.open).toBe(true);
  });

  it("initializes each menu only once even if called again", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );
    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    // Opening once should still only trigger one round of sibling-closing
    // wiring, not duplicate listeners causing double toggling side effects.
    menu.setOpen(true);
    expect(menu.open).toBe(true);
  });

  it("floats the panel out of ancestor overflow clipping when opened", () => {
    const menu = new FakeMenu();
    menu.summaryEl.getBoundingClientRect = () =>
      rect({ bottom: 300, left: 40, right: 200, top: 280 });
    (menu.panelEl as MockPanel).getBoundingClientRect = () =>
      rect({ height: 150, width: 160 });
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);

    expect(menu.panelEl?.style.position).toBe("fixed");
    expect(menu.panelEl?.style.top).toBe("304px");
    expect(menu.panelEl?.style.left).toBe("40px");
  });

  it("reverts the floated panel position when the menu closes", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    expect(menu.panelEl?.style.position).toBe("fixed");

    menu.setOpen(false);
    expect(menu.panelEl?.style.position).toBe("");
  });

  it("reverts the floated panel position when closed via outside click", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    doc.triggerClick({});

    expect(menu.open).toBe(false);
    expect(menu.panelEl?.style.position).toBe("");
  });

  it("reverts the floated panel position when closed via Escape", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    doc.triggerKeydown("Escape");

    expect(menu.panelEl?.style.position).toBe("");
  });

  it("reverts the floated panel position when closed via window resize", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    windowLike.triggerResize();

    expect(menu.panelEl?.style.position).toBe("");
  });

  it("reverts the floated panel position when closed via the drawer-close trigger", () => {
    const drawerMarker = {};
    const menu = new FakeMenu().setDrawerAncestor(drawerMarker);
    const doc = buildDoc([menu]);
    const closeTrigger = new FakeCloseTrigger();
    doc.setQuery("[data-drawer-close]", closeTrigger);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    closeTrigger.triggerClick();

    expect(menu.panelEl?.style.position).toBe("");
  });

  it("reverts the previous menu's floated position when another menu opens", () => {
    const menuA = new FakeMenu();
    const menuB = new FakeMenu();
    const doc = buildDoc([menuA, menuB]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menuA.setOpen(true);
    expect(menuA.panelEl?.style.position).toBe("fixed");

    menuB.setOpen(true);
    expect(menuA.panelEl?.style.position).toBe("");
    expect(menuB.panelEl?.style.position).toBe("fixed");
  });

  it("keeps floating behavior working when a menu has no panel found (defensive no-op)", () => {
    const menu = new FakeMenu().setPanel(null);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    expect(() => menu.setOpen(true)).not.toThrow();
    expect(menu.open).toBe(true);
  });

  it("keyboard-driven open (Enter on a focused summary) still floats correctly", () => {
    // Enter/Space on a native <summary> toggles the <details> natively; this
    // controller only reacts to the resulting "toggle" event, so keyboard
    // activation floats the panel exactly the same as a click would.
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true); // simulates the native toggle fired by Enter/Space
    expect(menu.panelEl?.style.position).toBe("fixed");
  });

  // -- Menu layering/stacking ------------------------------------------

  it("moves the opened panel into a shared body-level portal, out of its own menu", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    const panel = menu.panelEl;
    expect(menu.contains(panel)).toBe(true);

    menu.setOpen(true);

    // The panel is no longer inside its own trigger's <details> -- exactly
    // the change needed so a *different* row's sticky action-rail trigger
    // (its own low-level stacking context) can never paint over it.
    expect(menu.contains(panel)).toBe(false);
    expect((doc as unknown as FakeDocument).body.contains(panel)).toBe(true);
  });

  it("moves the panel back out of the portal when the menu closes", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    const panel = menu.panelEl;
    menu.setOpen(true);
    expect((doc as unknown as FakeDocument).body.contains(panel)).toBe(true);

    menu.setOpen(false);

    expect((doc as unknown as FakeDocument).body.contains(panel)).toBe(false);
    expect(menu.contains(panel)).toBe(true);
  });

  it("reuses the same portal element across multiple menus rather than creating one per menu", () => {
    const menuA = new FakeMenu();
    const menuB = new FakeMenu();
    const doc = buildDoc([menuA, menuB]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menuA.setOpen(true);
    menuA.setOpen(false);
    menuB.setOpen(true);

    // Both menus' panels pass through the same single body-level container.
    const body = (doc as unknown as FakeDocument).body;
    expect(body.contains(menuB.panelEl)).toBe(true);
    expect(body.children.length).toBe(1); // one portal div, reused
  });

  it("does not close a menu on a click inside its panel once floated to the portal", () => {
    const menu = new FakeMenu();
    const insideNode = {};
    (menu.panelEl as MockPanel).addInsideNode(insideNode);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    // Once floated, the panel is no longer a descendant of `menu` in the
    // real DOM either -- the outside-click check must therefore also
    // consult the panel's own containment, not just the trigger's.
    doc.triggerClick(insideNode);

    expect(menu.open).toBe(true);
  });

  it(`creates the portal container with the ${ROW_MENU_PORTAL_SELECTOR} marker`, () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    const portal = (doc as unknown as FakeDocument).body
      .children[0] as MockContainer;
    expect(portal.attributes["data-row-menu-portal"]).toBe("");
  });

  // -- Move note popover -------------------------------------------------

  it("closes the menu and returns focus to the trigger when its Cancel button is clicked", () => {
    const menu = new FakeMenu();
    const cancelButton = new MockCancelButton();
    (menu.panelEl as MockPanel).setCancelButton(cancelButton);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    cancelButton.triggerClick();

    expect(menu.open).toBe(false);
    expect(menu.summaryEl.focusCalls).toBe(1);
  });

  it("moves the panel back out of the portal when closed via its Cancel button", () => {
    const menu = new FakeMenu();
    const cancelButton = new MockCancelButton();
    (menu.panelEl as MockPanel).setCancelButton(cancelButton);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    const panel = menu.panelEl;
    menu.setOpen(true);
    expect((doc as unknown as FakeDocument).body.contains(panel)).toBe(true);

    cancelButton.triggerClick();

    expect((doc as unknown as FakeDocument).body.contains(panel)).toBe(false);
    expect(menu.contains(panel)).toBe(true);
  });

  it("does nothing when a panel has no Cancel button (defensive no-op)", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    expect(() => {
      initRowActionMenusDocument(
        doc as unknown as Document,
        windowLike as unknown as never,
      );
      menu.setOpen(true);
    }).not.toThrow();
    expect(menu.open).toBe(true);
  });

  it("closes the active-note overflow and focuses/selects the title input when Rename is clicked", () => {
    const menu = new FakeMenu();
    const renameButton = new MockRenameButton();
    (menu.panelEl as MockPanel).setRenameTrigger(renameButton);
    const titleInput = new MockTitleInput();
    const doc = buildDoc([menu]);
    doc.setQuery("[data-note-form]", {
      dataset: { noteTitleInputId: "note-title" },
    });
    doc.setElementById("note-title", titleInput);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    renameButton.triggerClick();

    expect(menu.open).toBe(false);
    expect(titleInput.focusCalls).toBe(1);
    expect(titleInput.selectCalls).toBe(1);
    // Unlike the Cancel button above, focus must land in the title input,
    // not return to the overflow's own trigger.
    expect(menu.summaryEl.focusCalls).toBe(0);
  });

  it("resets an open nested action disclosure when Rename is clicked", () => {
    const menu = new FakeMenu();
    const renameButton = new MockRenameButton();
    const disclosure = new FakeActionDisclosure();
    disclosure.open = true;
    (menu.panelEl as MockPanel)
      .setRenameTrigger(renameButton)
      .setActionDisclosures([disclosure]);
    const titleInput = new MockTitleInput();
    const doc = buildDoc([menu]);
    doc.setQuery("[data-note-form]", {
      dataset: { noteTitleInputId: "note-title" },
    });
    doc.setElementById("note-title", titleInput);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    renameButton.triggerClick();

    expect(disclosure.open).toBe(false);
  });

  it("does nothing when a panel has no Rename trigger (defensive no-op)", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    expect(() => {
      initRowActionMenusDocument(
        doc as unknown as Document,
        windowLike as unknown as never,
      );
      menu.setOpen(true);
    }).not.toThrow();
    expect(menu.open).toBe(true);
  });

  it("does not throw when Rename is clicked but no note form/title input is present (defensive no-op)", () => {
    const menu = new FakeMenu();
    const renameButton = new MockRenameButton();
    (menu.panelEl as MockPanel).setRenameTrigger(renameButton);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);

    expect(() => renameButton.triggerClick()).not.toThrow();
    expect(menu.open).toBe(false);
  });

  it('left-aligns the panel when the menu has data-menu-align="left" (Move note/New Folder)', () => {
    const menu = new FakeMenu();
    menu.dataset.menuAlign = "left";
    menu.summaryEl.getBoundingClientRect = () =>
      rect({ bottom: 100, left: 40, right: 60, top: 80 });
    (menu.panelEl as MockPanel).getBoundingClientRect = () =>
      rect({ height: 150, width: 200 });
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);

    // Left-aligned: left = triggerRect.left = 40 (not right-aligned, which
    // would clamp to 4 here since 60 - 200 runs off-screen).
    expect(menu.panelEl?.style.left).toBe("40px");
  });

  it("right-aligns the panel by default when data-menu-align is absent (folder/note row menus)", () => {
    const menu = new FakeMenu();
    menu.summaryEl.getBoundingClientRect = () =>
      rect({ bottom: 100, left: 40, right: 60, top: 80 });
    (menu.panelEl as MockPanel).getBoundingClientRect = () =>
      rect({ height: 150, width: 200 });
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);

    expect(menu.panelEl?.style.left).toBe("4px");
  });

  // -- Nested Rename/
  // -- Move disclosure reset-on-close and reposition-on-toggle ------------

  it("resets an open nested action disclosure to closed when the row menu closes via outside click", () => {
    const menu = new FakeMenu();
    const action = new FakeActionDisclosure();
    (menu.panelEl as MockPanel).setActionDisclosures([action]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    action.open = true;
    expect(action.open).toBe(true);

    doc.triggerClick({});

    expect(menu.open).toBe(false);
    expect(action.open).toBe(false);
  });

  it("resets an open nested action disclosure to closed when the row menu closes via Escape", () => {
    const menu = new FakeMenu();
    const action = new FakeActionDisclosure();
    (menu.panelEl as MockPanel).setActionDisclosures([action]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    action.open = true;

    doc.triggerKeydown("Escape");

    expect(menu.open).toBe(false);
    expect(action.open).toBe(false);
  });

  it("resets an open nested action disclosure when the trigger itself is toggled closed again", () => {
    const menu = new FakeMenu();
    const action = new FakeActionDisclosure();
    (menu.panelEl as MockPanel).setActionDisclosures([action]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    action.open = true;

    menu.setOpen(false);

    expect(action.open).toBe(false);
  });

  it("resets an open nested action disclosure when a different row menu opens (closeAll)", () => {
    const menuA = new FakeMenu();
    const menuB = new FakeMenu();
    const action = new FakeActionDisclosure();
    (menuA.panelEl as MockPanel).setActionDisclosures([action]);
    const doc = buildDoc([menuA, menuB]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menuA.setOpen(true);
    action.open = true;

    menuB.setOpen(true);

    expect(menuA.open).toBe(false);
    expect(action.open).toBe(false);
  });

  it("reopens the row menu with the nested disclosure collapsed after a prior close reset it", () => {
    const menu = new FakeMenu();
    const action = new FakeActionDisclosure();
    (menu.panelEl as MockPanel).setActionDisclosures([action]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    action.open = true;
    menu.setOpen(false);
    expect(action.open).toBe(false);

    menu.setOpen(true);

    expect(action.open).toBe(false);
  });

  it("does not throw and does not reset anything for a menu with no nested action disclosures", () => {
    const menu = new FakeMenu();
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    expect(() => {
      menu.setOpen(true);
      menu.setOpen(false);
    }).not.toThrow();
  });

  it("recomputes the floated panel's position when a nested action disclosure opens while the row menu is open", () => {
    const menu = new FakeMenu();
    const action = new FakeActionDisclosure();
    (menu.panelEl as MockPanel).setActionDisclosures([action]);
    // Trigger near the bottom of a 900px-tall viewport.
    menu.summaryEl.getBoundingClientRect = () =>
      rect({ bottom: 850, left: 40, right: 60, top: 830 });
    let panelHeight = 150;
    (menu.panelEl as MockPanel).getBoundingClientRect = () =>
      rect({ height: panelHeight, width: 200 });
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    const topBeforeExpand = menu.panelEl?.style.top;

    // Simulate the panel growing taller once the nested disclosure
    // expands -- still too tall to fit below the trigger either way, but
    // the flipped-above position shifts further up to keep fitting.
    panelHeight = 500;
    action.open = true;

    expect(menu.panelEl?.style.top).not.toBe(topBeforeExpand);
  });

  it("does not reposition a nested action disclosure's toggle while the row menu itself is closed", () => {
    const menu = new FakeMenu();
    const action = new FakeActionDisclosure();
    (menu.panelEl as MockPanel).setActionDisclosures([action]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    // The row menu itself was never opened -- a server-rendered or
    // programmatic nested-disclosure toggle here must not float a panel
    // that isn't actually showing.
    expect(() => {
      action.open = true;
    }).not.toThrow();
    expect(menu.panelEl?.style.position).toBe("");
  });

  // -- Active-note
  // -- overflow's Move disclosure must behave like every other nested
  // -- .tree-nav__action disclosure once its containing .row-action-menu
  // -- (`.note-workspace__overflow`) is managed solely by this controller --

  it("keeps the overflow menu open and the Move disclosure expanded after it is activated", () => {
    const menu = new FakeMenu();
    const move = new FakeActionDisclosure();
    (menu.panelEl as MockPanel).setActionDisclosures([move]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    move.open = true;

    expect(menu.open).toBe(true);
    expect(move.open).toBe(true);
  });

  it("does not close the overflow menu or collapse Move on a click inside the floated panel (e.g. the destination select)", () => {
    const menu = new FakeMenu();
    const move = new FakeActionDisclosure();
    (menu.panelEl as MockPanel).setActionDisclosures([move]);
    const destinationSelect = {};
    (menu.panelEl as MockPanel).addInsideNode(destinationSelect);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    move.open = true;

    // Simulates both a plain click inside the Move form and a destination
    // <select> change bubbling a click/focus event up to the document --
    // the exact click this controller's own (portal-aware) outside-click
    // check must recognize as "inside," now that it is the menu's only
    // controller.
    doc.triggerClick(destinationSelect);

    expect(menu.open).toBe(true);
    expect(move.open).toBe(true);
  });

  it("closes the overflow menu and resets Move to collapsed on outside click, Escape, or trigger re-toggle", () => {
    const menu = new FakeMenu();
    const move = new FakeActionDisclosure();
    (menu.panelEl as MockPanel).setActionDisclosures([move]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    // Outside click.
    menu.setOpen(true);
    move.open = true;
    doc.triggerClick({});
    expect(menu.open).toBe(false);
    expect(move.open).toBe(false);

    // Escape.
    menu.setOpen(true);
    move.open = true;
    doc.triggerKeydown("Escape");
    expect(menu.open).toBe(false);
    expect(move.open).toBe(false);

    // Trigger re-toggle (clicking the overflow's own summary again).
    menu.setOpen(true);
    move.open = true;
    menu.setOpen(false);
    expect(menu.open).toBe(false);
    expect(move.open).toBe(false);
  });
});

// -- New Folder must
// -- return to a genuinely pristine state (not just collapsed) whenever it
// -- closes, in both its nested-overflow and standalone-toolbar forms -----

describe("New Folder form reset on close", () => {
  it("clears a nested New Folder disclosure's value, aria-invalid, and error when the outer menu closes via outside click", () => {
    const menu = new FakeMenu();
    const input = new FakeFieldInput();
    input.value = "duplicate";
    const errorEl = new FakeErrorParagraph();
    errorEl.textContent = "A folder with that name already exists.";
    errorEl.hidden = false;
    const newFolder = new FakeActionDisclosure({
      isNewFolder: true,
      input,
      errorEl,
    });
    (menu.panelEl as MockPanel).setActionDisclosures([newFolder]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    newFolder.open = true;
    doc.triggerClick({});

    expect(menu.open).toBe(false);
    expect(newFolder.open).toBe(false);
    expect(input.value).toBe("");
    expect(input.ariaInvalidRemoved).toBe(true);
    expect(errorEl.textContent).toBe("");
    expect(errorEl.hidden).toBe(true);
  });

  it("clears a nested New Folder disclosure on Escape", () => {
    const menu = new FakeMenu();
    const input = new FakeFieldInput();
    input.value = "duplicate";
    const errorEl = new FakeErrorParagraph();
    errorEl.hidden = false;
    const newFolder = new FakeActionDisclosure({
      isNewFolder: true,
      input,
      errorEl,
    });
    (menu.panelEl as MockPanel).setActionDisclosures([newFolder]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    newFolder.open = true;
    doc.triggerKeydown("Escape");

    expect(input.value).toBe("");
    expect(errorEl.hidden).toBe(true);
  });

  it("clears a nested New Folder disclosure when the trigger itself is toggled closed again", () => {
    const menu = new FakeMenu();
    const input = new FakeFieldInput();
    input.value = "duplicate";
    const errorEl = new FakeErrorParagraph();
    errorEl.hidden = false;
    const newFolder = new FakeActionDisclosure({
      isNewFolder: true,
      input,
      errorEl,
    });
    (menu.panelEl as MockPanel).setActionDisclosures([newFolder]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    newFolder.open = true;
    menu.setOpen(false);

    expect(input.value).toBe("");
    expect(errorEl.hidden).toBe(true);
  });

  it("reopens with a fully pristine New Folder disclosure after a prior close reset it", () => {
    const menu = new FakeMenu();
    const input = new FakeFieldInput();
    input.value = "duplicate";
    const errorEl = new FakeErrorParagraph();
    errorEl.textContent = "A folder with that name already exists.";
    errorEl.hidden = false;
    const newFolder = new FakeActionDisclosure({
      isNewFolder: true,
      input,
      errorEl,
    });
    (menu.panelEl as MockPanel).setActionDisclosures([newFolder]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    newFolder.open = true;
    menu.setOpen(false);
    expect(input.value).toBe("");
    expect(errorEl.hidden).toBe(true);

    menu.setOpen(true);

    expect(newFolder.open).toBe(false);
    expect(input.value).toBe("");
    expect(errorEl.hidden).toBe(true);
  });

  it("clears the standalone tree-toolbar New Folder popover's own value and error when it closes", () => {
    const input = new FakeFieldInput();
    input.value = "duplicate";
    const errorEl = new FakeErrorParagraph();
    errorEl.textContent = "A folder with that name already exists.";
    errorEl.hidden = false;
    const menu = new FakeMenu().setNewFolder(input, errorEl);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    menu.setOpen(false);

    expect(input.value).toBe("");
    expect(input.ariaInvalidRemoved).toBe(true);
    expect(errorEl.textContent).toBe("");
    expect(errorEl.hidden).toBe(true);
  });

  it("clears the standalone New Folder popover when dismissed via its own Cancel button", () => {
    const input = new FakeFieldInput();
    input.value = "duplicate";
    const errorEl = new FakeErrorParagraph();
    errorEl.hidden = false;
    const cancelButton = new MockCancelButton();
    const menu = new FakeMenu().setNewFolder(input, errorEl);
    (menu.panelEl as MockPanel).setCancelButton(cancelButton);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    cancelButton.triggerClick();

    expect(menu.open).toBe(false);
    expect(input.value).toBe("");
    expect(errorEl.hidden).toBe(true);
  });

  it("clears a nested New Folder disclosure (e.g. the active-note overflow's copy) via a Cancel button in the outer panel", () => {
    // The active-note overflow itself is the `.row-action-menu` (not New
    // Folder), with New Folder nested inside it. Its Cancel button
    // lives in the shared outer panel (matching the tree-toolbar's own
    // standalone popover exactly), so activating it closes the outer
    // overflow -- the existing, generic behavior is deliberately reused
    // rather than inventing a narrower handler
    // that would only collapse the nested disclosure.
    const menu = new FakeMenu();
    const input = new FakeFieldInput();
    input.value = "duplicate";
    const errorEl = new FakeErrorParagraph();
    errorEl.textContent = "A folder with that name already exists.";
    errorEl.hidden = false;
    const newFolder = new FakeActionDisclosure({
      isNewFolder: true,
      input,
      errorEl,
    });
    (menu.panelEl as MockPanel).setActionDisclosures([newFolder]);
    const cancelButton = new MockCancelButton();
    (menu.panelEl as MockPanel).setCancelButton(cancelButton);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    newFolder.open = true;
    cancelButton.triggerClick();

    expect(menu.open).toBe(false);
    expect(newFolder.open).toBe(false);
    expect(input.value).toBe("");
    expect(input.ariaInvalidRemoved).toBe(true);
    expect(errorEl.textContent).toBe("");
    expect(errorEl.hidden).toBe(true);
  });

  it("regression: wires every Cancel button in a panel, not just the first", () => {
    // The active-note overflow's real panel contains three
    // [data-row-menu-cancel] buttons at once -- New note's, New folder's,
    // and Move's -- all inside the same outer panel. A singular
    // `querySelector` implementation would bind only the first match, silently leaving the other two inert; this
    // is the exact class of failure (Move's Cancel doing
    // nothing). Modeling all three together, and triggering each one in
    // turn, is required to actually catch that class of regression --
    // a fixture with only one cancel button per panel (as every other
    // test in this file uses) cannot reproduce it.
    const newNoteCancel = new MockCancelButton();
    const newFolderCancel = new MockCancelButton();
    const moveCancel = new MockCancelButton();
    const newNote = new FakeActionDisclosure();
    const newFolder = new FakeActionDisclosure({ isNewFolder: true });
    const move = new FakeActionDisclosure();
    const menu = new FakeMenu();
    (menu.panelEl as MockPanel).setActionDisclosures([
      newNote,
      newFolder,
      move,
    ]);
    (menu.panelEl as MockPanel).setCancelButtons([
      newNoteCancel,
      newFolderCancel,
      moveCancel,
    ]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    // New note's Cancel (the first button in the panel) closes the menu.
    menu.setOpen(true);
    newNote.open = true;
    newNoteCancel.triggerClick();
    expect(menu.open).toBe(false);
    expect(newNote.open).toBe(false);

    // New folder's Cancel (the second/middle button) also closes the menu
    // -- this is the button every pre-existing test in this file already
    // covered in isolation, re-proven here alongside its siblings.
    menu.setOpen(true);
    newFolder.open = true;
    newFolderCancel.triggerClick();
    expect(menu.open).toBe(false);
    expect(newFolder.open).toBe(false);

    // Move's Cancel (the third/last button) is the one the singular
    // `querySelector` implementation silently failed to wire.
    menu.setOpen(true);
    move.open = true;
    moveCancel.triggerClick();
    expect(menu.open).toBe(false);
    expect(move.open).toBe(false);
  });

  it("does not clear a Rename disclosure's prefilled value when the outer menu closes (regression guard)", () => {
    const menu = new FakeMenu();
    const renameInput = new FakeFieldInput();
    renameInput.value = "Existing Title";
    const rename = new FakeActionDisclosure({ input: renameInput });
    (menu.panelEl as MockPanel).setActionDisclosures([rename]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    menu.setOpen(true);
    rename.open = true;
    doc.triggerClick({});

    expect(renameInput.value).toBe("Existing Title");
    expect(renameInput.ariaInvalidRemoved).toBe(false);
  });

  it("does not throw when a New Folder disclosure has no input or error element present", () => {
    const menu = new FakeMenu();
    const newFolder = new FakeActionDisclosure({ isNewFolder: true });
    (menu.panelEl as MockPanel).setActionDisclosures([newFolder]);
    const doc = buildDoc([menu]);
    const windowLike = new FakeWindowLike();

    initRowActionMenusDocument(
      doc as unknown as Document,
      windowLike as unknown as never,
    );

    expect(() => {
      menu.setOpen(true);
      newFolder.open = true;
      menu.setOpen(false);
    }).not.toThrow();
  });
});
