import { describe, expect, it } from "vitest";

import { initTooltipPlacementDocument } from "./tooltip-placement";

class FakeAncestor {
  constructor(private readonly rect: { top: number; bottom: number }) {}

  getBoundingClientRect(): { top: number; bottom: number } {
    return this.rect;
  }
}

class FakeTrigger {
  dataset: Record<string, string> = {};
  private attributes: Record<string, string> = {};
  private listeners: Record<string, Array<() => void>> = {};
  private top: number;
  private clippingAncestor: FakeAncestor | null;

  constructor(top: number, clippingAncestor: FakeAncestor | null = null) {
    this.top = top;
    this.clippingAncestor = clippingAncestor;
  }

  setTop(top: number): void {
    this.top = top;
  }

  getBoundingClientRect(): { top: number } {
    return { top: this.top };
  }

  closest(): FakeAncestor | null {
    return this.clippingAncestor;
  }

  addEventListener(type: string, listener: () => void): void {
    this.listeners[type] = this.listeners[type] ?? [];
    this.listeners[type].push(listener);
  }

  trigger(type: string): void {
    this.listeners[type]?.forEach((listener) => listener());
  }

  setAttribute(name: string, value: string): void {
    this.attributes[name] = value;
  }

  removeAttribute(name: string): void {
    delete this.attributes[name];
  }

  getAttribute(name: string): string | null {
    return this.attributes[name] ?? null;
  }
}

function buildDoc(triggers: FakeTrigger[], headers: FakeAncestor[] = []) {
  return {
    querySelectorAll: (selector: string) => {
      if (selector === "[data-tooltip]") return triggers;
      if (selector === ".app-header, .workspace-drawer__header") return headers;
      return [];
    },
  } as unknown as Document;
}

describe("initTooltipPlacementDocument", () => {
  it("returns false when no tooltip triggers exist", () => {
    const doc = buildDoc([]);
    expect(initTooltipPlacementDocument(doc)).toBe(false);
  });

  it("does not mark placement below when there is enough room above on hover", () => {
    const trigger = new FakeTrigger(200);
    const doc = buildDoc([trigger]);

    expect(initTooltipPlacementDocument(doc)).toBe(true);
    trigger.trigger("mouseenter");

    expect(trigger.getAttribute("data-tooltip-placement")).toBeNull();
  });

  it("marks placement below when there is not enough room above on hover", () => {
    const trigger = new FakeTrigger(10);
    const doc = buildDoc([trigger]);

    initTooltipPlacementDocument(doc);
    trigger.trigger("mouseenter");

    expect(trigger.getAttribute("data-tooltip-placement")).toBe("below");
  });

  it("also updates placement on keyboard focus, not just hover", () => {
    const trigger = new FakeTrigger(10);
    const doc = buildDoc([trigger]);

    initTooltipPlacementDocument(doc);
    trigger.trigger("focus");

    expect(trigger.getAttribute("data-tooltip-placement")).toBe("below");
  });

  it("re-evaluates on every hover, clearing a stale below placement once there is room", () => {
    const trigger = new FakeTrigger(10);
    const doc = buildDoc([trigger]);

    initTooltipPlacementDocument(doc);
    trigger.trigger("mouseenter");
    expect(trigger.getAttribute("data-tooltip-placement")).toBe("below");

    trigger.setTop(300);
    trigger.trigger("mouseenter");
    expect(trigger.getAttribute("data-tooltip-placement")).toBeNull();
  });

  it("does not double-bind listeners on a second init call", () => {
    const trigger = new FakeTrigger(10);
    const doc = buildDoc([trigger]);

    initTooltipPlacementDocument(doc);
    initTooltipPlacementDocument(doc);
    trigger.trigger("mouseenter");

    expect(trigger.getAttribute("data-tooltip-placement")).toBe("below");
  });

  it("flips below when the trigger's raw top offset looks safe but the application header still reaches further down", () => {
    // 90px above the viewport top would previously read as "plenty of
    // room" (well past the old flat 40px constant), but a 64px-tall header
    // only leaves 26px of genuinely usable space above this trigger --
    // less than the tooltip needs to render fully clear of the header band.
    const trigger = new FakeTrigger(90);
    const header = new FakeAncestor({ top: 0, bottom: 64 });
    const doc = buildDoc([trigger], [header]);

    initTooltipPlacementDocument(doc);
    trigger.trigger("mouseenter");

    expect(trigger.getAttribute("data-tooltip-placement")).toBe("below");
  });

  it("stays above when there is enough usable space below the header", () => {
    const trigger = new FakeTrigger(200);
    const header = new FakeAncestor({ top: 0, bottom: 64 });
    const doc = buildDoc([trigger], [header]);

    initTooltipPlacementDocument(doc);
    trigger.trigger("mouseenter");

    expect(trigger.getAttribute("data-tooltip-placement")).toBeNull();
  });

  it("flips below when a scrollable ancestor (e.g. the narrow drawer's tree region) would clip the tooltip above the trigger", () => {
    const clippingAncestor = new FakeAncestor({ top: 150, bottom: 400 });
    const trigger = new FakeTrigger(180, clippingAncestor);
    const doc = buildDoc([trigger]);

    initTooltipPlacementDocument(doc);
    trigger.trigger("mouseenter");

    expect(trigger.getAttribute("data-tooltip-placement")).toBe("below");
  });

  it("stays above when the scrollable ancestor starts well above the trigger", () => {
    const clippingAncestor = new FakeAncestor({ top: 50, bottom: 400 });
    const trigger = new FakeTrigger(200, clippingAncestor);
    const doc = buildDoc([trigger]);

    initTooltipPlacementDocument(doc);
    trigger.trigger("mouseenter");

    expect(trigger.getAttribute("data-tooltip-placement")).toBeNull();
  });
});
