import { describe, expect, it } from "vitest";

import {
  computeTreeTooltipPosition,
  initTreeTitleTooltipDocument,
  refreshTreeTitleTooltipForLabel,
  type TreeTitleLabelElementLike,
  type TreeTitleTriggerElementLike,
  type TreeTitleUpdatableLabelElementLike,
  type TreeTooltipRect,
} from "./tree-title-truncation";

function rect(overrides: Partial<TreeTooltipRect> = {}): TreeTooltipRect {
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

class FakeLabel implements TreeTitleLabelElementLike {
  constructor(
    public textContent: string | null,
    public scrollWidth: number,
    public clientWidth: number,
  ) {}
}

type Listener = () => void;

class FakeTrigger implements TreeTitleTriggerElementLike {
  private readonly attributes = new Map<string, string>();
  private readonly listeners = new Map<string, Listener[]>();
  public boundingRect: TreeTooltipRect = rect({
    bottom: 40,
    height: 20,
    left: 10,
    right: 110,
    top: 20,
    width: 100,
  });

  constructor(public label: FakeLabel | null) {}

  querySelector(selector: string): TreeTitleLabelElementLike | null {
    return selector === ".tree-nav__label" ? this.label : null;
  }

  setAttribute(name: string, value: string): void {
    this.attributes.set(name, value);
  }

  removeAttribute(name: string): void {
    this.attributes.delete(name);
  }

  getAttribute(name: string): string | null {
    return this.attributes.has(name) ? this.attributes.get(name)! : null;
  }

  addEventListener(type: string, listener: Listener): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  dispatch(type: string): void {
    (this.listeners.get(type) ?? []).forEach((listener) => listener());
  }

  getBoundingClientRect(): TreeTooltipRect {
    return this.boundingRect;
  }
}

class FakeUpdatableLabel
  extends FakeLabel
  implements TreeTitleUpdatableLabelElementLike
{
  constructor(
    textContent: string | null,
    scrollWidth: number,
    clientWidth: number,
    private readonly trigger: FakeTrigger | null,
  ) {
    super(textContent, scrollWidth, clientWidth);
  }

  closest(selector: string): TreeTitleTriggerElementLike | null {
    return selector.includes(".tree-nav__note-link") ? this.trigger : null;
  }
}

class FakeResizeObserver {
  observed: unknown[] = [];
  constructor(private readonly callback: () => void) {}
  observe(target: unknown): void {
    this.observed.push(target);
  }
  trigger(): void {
    this.callback();
  }
}

class FakeFolderDetails {
  private readonly listeners: Listener[] = [];
  addEventListener(type: "toggle", listener: Listener): void {
    if (type === "toggle") {
      this.listeners.push(listener);
    }
  }
  triggerToggle(): void {
    this.listeners.forEach((listener) => listener());
  }
}

class FakeTooltipElement {
  public classes = new Set<string>();
  public style = { left: "", top: "" };
  public textContent: string | null = null;
  public rect: TreeTooltipRect = rect({
    height: 24,
    width: 120,
  });
  public classList = {
    add: (name: string): void => {
      this.classes.add(name);
    },
    remove: (name: string): void => {
      this.classes.delete(name);
    },
  };

  getBoundingClientRect(): TreeTooltipRect {
    return this.rect;
  }
}

class FakeDocument {
  public createdElements: FakeTooltipElement[] = [];
  public appended: FakeTooltipElement[] = [];
  public body = {
    appendChild: (el: FakeTooltipElement): void => {
      this.appended.push(el);
    },
  };
  public fonts?: { ready: Promise<void> };
  public omitPortal = false;

  constructor(
    private readonly triggers: FakeTrigger[],
    private readonly viewports: unknown[] = [],
    private readonly folderDetails: FakeFolderDetails[] = [],
  ) {}

  createElement(): FakeTooltipElement {
    const el = new FakeTooltipElement();
    this.createdElements.push(el);
    return el;
  }

  querySelectorAll(selector: string): unknown[] {
    if (selector === ".tree-nav__note-link, .tree-nav__folder-disclosure") {
      return this.triggers;
    }
    if (selector === ".tree-nav__viewport") {
      return this.viewports;
    }
    if (selector === ".tree-nav__folder-details") {
      return this.folderDetails;
    }
    return [];
  }
}

class PortalLessFakeDocument {
  constructor(private readonly triggers: FakeTrigger[]) {}
  querySelectorAll(selector: string): unknown[] {
    return selector === ".tree-nav__note-link, .tree-nav__folder-disclosure"
      ? this.triggers
      : [];
  }
}

function makeFakeResizeObserverCtor(
  win: FakeWindow,
): new (callback: () => void) => FakeResizeObserver {
  function ResizeObserverCtor(callback: () => void): FakeResizeObserver {
    const observer = new FakeResizeObserver(callback);
    win.lastObserver = observer;
    return observer;
  }
  return ResizeObserverCtor as unknown as new (
    callback: () => void,
  ) => FakeResizeObserver;
}

class FakeWindow {
  private resizeListeners: Array<() => void> = [];
  public lastObserver: FakeResizeObserver | null = null;
  public ResizeObserver?: unknown;
  public requestAnimationFrame?: (callback: () => void) => number;
  public innerWidth = 1280;
  public innerHeight = 900;

  addEventListener(type: "resize", listener: () => void): void {
    if (type === "resize") {
      this.resizeListeners.push(listener);
    }
  }

  triggerResize(): void {
    this.resizeListeners.forEach((listener) => listener());
  }

  enableResizeObserver(): void {
    this.ResizeObserver = makeFakeResizeObserverCtor(this);
  }

  enableSynchronousRaf(): void {
    this.requestAnimationFrame = (callback: () => void): number => {
      callback();
      return 0;
    };
  }
}

describe("initTreeTitleTooltipDocument", () => {
  it("returns false and does nothing when no tree titles are present", () => {
    const doc = new FakeDocument([]);
    const win = new FakeWindow();

    expect(initTreeTitleTooltipDocument(doc, win)).toBe(false);
  });

  it("leaves an untruncated note title with no tooltip", () => {
    const trigger = new FakeTrigger(new FakeLabel("Short note", 40, 40));
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    expect(initTreeTitleTooltipDocument(doc, win)).toBe(true);
    expect(trigger.getAttribute("data-tooltip")).toBeNull();
  });

  it("gives a genuinely truncated note title a full-title tooltip immediately, with no resize required", () => {
    const trigger = new FakeTrigger(
      new FakeLabel("A genuinely long note title that overflows", 220, 140),
    );
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);

    expect(trigger.getAttribute("data-tooltip")).toBe(
      "A genuinely long note title that overflows",
    );
  });

  it("gives a genuinely truncated folder title a full-title tooltip identically to notes", () => {
    const trigger = new FakeTrigger(
      new FakeLabel("A genuinely long folder name that overflows", 220, 140),
    );
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);

    expect(trigger.getAttribute("data-tooltip")).toBe(
      "A genuinely long folder name that overflows",
    );
  });

  it("never crashes and never adds a tooltip when a trigger has no label child", () => {
    const trigger = new FakeTrigger(null);
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    expect(() => initTreeTitleTooltipDocument(doc, win)).not.toThrow();
    expect(trigger.getAttribute("data-tooltip")).toBeNull();
  });

  it("never adds a tooltip for empty label text, even if somehow overflowing", () => {
    const trigger = new FakeTrigger(new FakeLabel("", 50, 10));
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);

    expect(trigger.getAttribute("data-tooltip")).toBeNull();
  });

  it("adds the tooltip on window resize once a title newly crosses the truncation threshold", () => {
    const label = new FakeLabel("Borderline title", 100, 100);
    const trigger = new FakeTrigger(label);
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);
    expect(trigger.getAttribute("data-tooltip")).toBeNull();

    label.clientWidth = 70;
    win.triggerResize();

    expect(trigger.getAttribute("data-tooltip")).toBe("Borderline title");
  });

  it("removes the tooltip on resize once a previously truncated title fits again", () => {
    const label = new FakeLabel("Borderline title", 100, 70);
    const trigger = new FakeTrigger(label);
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);
    expect(trigger.getAttribute("data-tooltip")).toBe("Borderline title");

    label.clientWidth = 100;
    win.triggerResize();

    expect(trigger.getAttribute("data-tooltip")).toBeNull();
  });

  it("evaluates every title independently -- a mix of fitting and truncated rows on the same page", () => {
    const fitting = new FakeTrigger(new FakeLabel("Fits", 30, 30));
    const truncated = new FakeTrigger(
      new FakeLabel("Does Not Fit At All In The Tree", 200, 140),
    );
    const doc = new FakeDocument([fitting, truncated]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);

    expect(fitting.getAttribute("data-tooltip")).toBeNull();
    expect(truncated.getAttribute("data-tooltip")).toBe(
      "Does Not Fit At All In The Tree",
    );
  });

  it("registers a ResizeObserver against every .tree-nav__viewport, not per row, when available", () => {
    const trigger = new FakeTrigger(new FakeLabel("Title", 40, 40));
    const viewportA = {};
    const viewportB = {};
    const doc = new FakeDocument([trigger], [viewportA, viewportB]);
    const win = new FakeWindow();
    win.enableResizeObserver();

    initTreeTitleTooltipDocument(doc, win);

    expect(win.lastObserver).not.toBeNull();
    expect(win.lastObserver?.observed).toEqual([viewportA, viewportB]);
  });

  it("recalculates truncation when the ResizeObserver fires, e.g. after a tree collapse/reopen or drawer resize", () => {
    const label = new FakeLabel("Borderline title", 100, 100);
    const trigger = new FakeTrigger(label);
    const doc = new FakeDocument([trigger], [{}]);
    const win = new FakeWindow();
    win.enableResizeObserver();

    initTreeTitleTooltipDocument(doc, win);
    expect(trigger.getAttribute("data-tooltip")).toBeNull();

    label.clientWidth = 70;
    win.lastObserver?.trigger();

    expect(trigger.getAttribute("data-tooltip")).toBe("Borderline title");
  });

  it("does not throw and skips ResizeObserver wiring when it is unavailable", () => {
    const trigger = new FakeTrigger(new FakeLabel("Title", 40, 40));
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    expect(() => initTreeTitleTooltipDocument(doc, win)).not.toThrow();
  });

  it("re-measures via a scheduled animation frame, catching layout not yet settled at the synchronous init pass", () => {
    // A row that starts inside a collapsed folder is display:none
    // (0/0) at the very first synchronous pass; simulate layout
    // "settling" to its real, truncated size by the time the scheduled
    // frame callback runs.
    const label = new FakeLabel(
      "A title only correct after layout settles",
      0,
      0,
    );
    const trigger = new FakeTrigger(label);
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();
    win.enableSynchronousRaf();

    // Flip to the "real" truncated measurement only once rAF fires, by
    // wrapping requestAnimationFrame so it mutates the label first.
    const realRaf = win.requestAnimationFrame!;
    win.requestAnimationFrame = (callback: () => void): number => {
      label.scrollWidth = 220;
      label.clientWidth = 140;
      return realRaf(callback);
    };

    initTreeTitleTooltipDocument(doc, win);

    expect(trigger.getAttribute("data-tooltip")).toBe(
      "A title only correct after layout settles",
    );
  });

  it("re-measures once document.fonts.ready resolves, catching a font swap after the initial pass", async () => {
    const label = new FakeLabel(
      "A title only correct after fonts settle",
      0,
      0,
    );
    const trigger = new FakeTrigger(label);
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    let resolveFonts: () => void = () => {};
    doc.fonts = {
      ready: new Promise<void>((resolve) => {
        resolveFonts = resolve;
      }),
    };

    initTreeTitleTooltipDocument(doc, win);
    expect(trigger.getAttribute("data-tooltip")).toBeNull();

    label.scrollWidth = 220;
    label.clientWidth = 140;
    resolveFonts();
    await doc.fonts.ready;
    // Allow the .then() microtask queued inside the module to run.
    await Promise.resolve();

    expect(trigger.getAttribute("data-tooltip")).toBe(
      "A title only correct after fonts settle",
    );
  });

  it("re-measures every title when any folder's <details> toggles open or closed", () => {
    // Simulates a note row inside a folder that starts collapsed
    // (display: none, 0/0) becoming visible with its real, truncated
    // size once the folder opens.
    const label = new FakeLabel("A title hidden until its folder opens", 0, 0);
    const trigger = new FakeTrigger(label);
    const folderDetails = new FakeFolderDetails();
    const doc = new FakeDocument([trigger], [], [folderDetails]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);
    expect(trigger.getAttribute("data-tooltip")).toBeNull();

    label.scrollWidth = 220;
    label.clientWidth = 140;
    folderDetails.triggerToggle();

    expect(trigger.getAttribute("data-tooltip")).toBe(
      "A title hidden until its folder opens",
    );
  });
});

describe("refreshTreeTitleTooltipForLabel", () => {
  it("does nothing when passed a null label", () => {
    expect(() => refreshTreeTitleTooltipForLabel(null)).not.toThrow();
  });

  it("does nothing when the label has no matching trigger ancestor", () => {
    const label = new FakeUpdatableLabel("Title", 200, 40, null);

    expect(() => refreshTreeTitleTooltipForLabel(label)).not.toThrow();
  });

  it("re-measures and sets the tooltip on the label's own trigger once truncated", () => {
    const trigger = new FakeTrigger(null);
    const label = new FakeUpdatableLabel(
      "A newly renamed, genuinely long note title",
      220,
      140,
      trigger,
    );
    trigger.label = label;

    refreshTreeTitleTooltipForLabel(label);

    expect(trigger.getAttribute("data-tooltip")).toBe(
      "A newly renamed, genuinely long note title",
    );
  });

  it("removes a stale tooltip once a renamed title no longer truncates", () => {
    const trigger = new FakeTrigger(null);
    const label = new FakeUpdatableLabel("Short now", 40, 40, trigger);
    trigger.label = label;
    trigger.setAttribute("data-tooltip", "A previous, longer title");

    refreshTreeTitleTooltipForLabel(label);

    expect(trigger.getAttribute("data-tooltip")).toBeNull();
  });
});

describe("computeTreeTooltipPosition", () => {
  it("prefers rendering above the trigger, horizontally centered, when there is room", () => {
    const triggerRect = rect({
      bottom: 340,
      left: 40,
      right: 220,
      top: 320,
      width: 180,
    });
    const tooltipRect = rect({ height: 40, width: 200 });

    const result = computeTreeTooltipPosition(
      triggerRect,
      tooltipRect,
      1280,
      900,
    );

    expect(result.top).toBe(274); // 320 - 40 - 6px gap
    expect(result.left).toBe(30); // (40 + 90) - 100, well within bounds
  });

  it("falls back below the trigger when there is not enough room above", () => {
    const triggerRect = rect({
      bottom: 30,
      left: 40,
      right: 220,
      top: 10,
      width: 180,
    });
    const tooltipRect = rect({ height: 40, width: 200 });

    const result = computeTreeTooltipPosition(
      triggerRect,
      tooltipRect,
      1280,
      900,
    );

    expect(result.top).toBe(36); // 30 + 6px gap
  });

  it("clamps the tooltip's left edge so it never runs off the right side of the viewport", () => {
    const triggerRect = rect({
      bottom: 340,
      left: 1200,
      right: 1270,
      top: 320,
      width: 70,
    });
    const tooltipRect = rect({ height: 40, width: 300 });

    const result = computeTreeTooltipPosition(
      triggerRect,
      tooltipRect,
      1280,
      900,
    );

    expect(result.left).toBe(1280 - 300 - 8);
  });

  it("clamps the tooltip's left edge so it never runs off the left side of the viewport", () => {
    const triggerRect = rect({
      bottom: 340,
      left: 0,
      right: 20,
      top: 320,
      width: 20,
    });
    const tooltipRect = rect({ height: 40, width: 300 });

    const result = computeTreeTooltipPosition(
      triggerRect,
      tooltipRect,
      1280,
      900,
    );

    expect(result.left).toBe(8);
  });

  it("clamps the tooltip's top edge so it never runs off the top of the viewport when neither side has room", () => {
    const triggerRect = rect({
      bottom: 10,
      left: 40,
      right: 220,
      top: 0,
      width: 180,
    });
    const tooltipRect = rect({ height: 900, width: 200 });

    const result = computeTreeTooltipPosition(
      triggerRect,
      tooltipRect,
      1280,
      900,
    );

    expect(result.top).toBeGreaterThanOrEqual(8);
  });

  it("is independent of the trigger's own width -- the tooltip may be wider than the tree row that anchors it", () => {
    const triggerRect = rect({
      bottom: 340,
      left: 40,
      right: 220,
      top: 320,
      width: 180,
    });
    const wideTooltipRect = rect({ height: 60, width: 448 }); // 28rem at 16px root

    const result = computeTreeTooltipPosition(
      triggerRect,
      wideTooltipRect,
      1280,
      900,
    );

    // No exception, and the tooltip is still placed on-screen despite
    // being far wider than the 180px-wide trigger.
    expect(result.left).toBeGreaterThanOrEqual(8);
    expect(result.left + wideTooltipRect.width).toBeLessThanOrEqual(1280 - 8);
  });
});

describe("tree title tooltip portal wiring", () => {
  it("creates and appends exactly one shared tooltip element to <body>, even with multiple rows", () => {
    const triggerA = new FakeTrigger(
      new FakeLabel("A genuinely long note title that overflows", 220, 140),
    );
    const triggerB = new FakeTrigger(
      new FakeLabel(
        "Another genuinely long note title that overflows",
        220,
        140,
      ),
    );
    const doc = new FakeDocument([triggerA, triggerB]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);
    // Nothing shown yet -- the element is created eagerly by this
    // implementation only once a trigger is actually hovered/focused.
    triggerA.dispatch("mouseenter");
    triggerB.dispatch("mouseenter");

    expect(doc.appended.length).toBe(1);
  });

  it("shows the shared tooltip with the trigger's full title on hover, positioned via computeTreeTooltipPosition", () => {
    const trigger = new FakeTrigger(
      new FakeLabel("A genuinely long note title that overflows", 220, 140),
    );
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);
    trigger.dispatch("mouseenter");

    expect(doc.appended.length).toBe(1);
    const tooltipEl = doc.appended[0];
    expect(tooltipEl.textContent).toBe(
      "A genuinely long note title that overflows",
    );
    expect(tooltipEl.classes.has("tree-title-tooltip--visible")).toBe(true);
    expect(tooltipEl.style.left).not.toBe("");
    expect(tooltipEl.style.top).not.toBe("");
  });

  it("shows the tooltip on keyboard focus, not only pointer hover", () => {
    const trigger = new FakeTrigger(
      new FakeLabel("A genuinely long folder name that overflows", 220, 140),
    );
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);
    trigger.dispatch("focus");

    expect(doc.appended[0].classes.has("tree-title-tooltip--visible")).toBe(
      true,
    );
  });

  it("hides the tooltip on mouseleave and on blur", () => {
    const trigger = new FakeTrigger(
      new FakeLabel("A genuinely long note title that overflows", 220, 140),
    );
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);
    trigger.dispatch("mouseenter");
    expect(doc.appended[0].classes.has("tree-title-tooltip--visible")).toBe(
      true,
    );

    trigger.dispatch("mouseleave");
    expect(doc.appended[0].classes.has("tree-title-tooltip--visible")).toBe(
      false,
    );

    trigger.dispatch("focus");
    trigger.dispatch("blur");
    expect(doc.appended[0].classes.has("tree-title-tooltip--visible")).toBe(
      false,
    );
  });

  it("never shows a tooltip for an untruncated title", () => {
    const trigger = new FakeTrigger(new FakeLabel("Short", 20, 40));
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);
    trigger.dispatch("mouseenter");

    expect(doc.appended.length).toBe(0);
  });

  it("hides an open tooltip once a re-measure finds its trigger's title no longer truncated", () => {
    const label = new FakeLabel("Borderline title", 100, 70);
    const trigger = new FakeTrigger(label);
    const doc = new FakeDocument([trigger]);
    const win = new FakeWindow();

    initTreeTitleTooltipDocument(doc, win);
    trigger.dispatch("mouseenter");
    expect(doc.appended[0].classes.has("tree-title-tooltip--visible")).toBe(
      true,
    );

    // The tree widens while the tooltip is still open (e.g. a resize
    // event) -- the currently-open tooltip must not keep showing stale
    // truncated-title content once the title genuinely fits.
    label.clientWidth = 100;
    win.triggerResize();

    expect(doc.appended[0].classes.has("tree-title-tooltip--visible")).toBe(
      false,
    );
  });

  it("does not throw and simply skips the portal when the document has no createElement/body", () => {
    const trigger = new FakeTrigger(
      new FakeLabel("A genuinely long note title that overflows", 220, 140),
    );
    const doc = new PortalLessFakeDocument([trigger]);
    const win = new FakeWindow();

    expect(() => initTreeTitleTooltipDocument(doc, win)).not.toThrow();
    expect(trigger.getAttribute("data-tooltip")).toBe(
      "A genuinely long note title that overflows",
    );
  });
});
