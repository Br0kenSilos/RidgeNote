import { describe, expect, it } from "vitest";

import { initWorkspaceHeaderHeightDocument } from "./workspace-header-height";

class FakeHeader {
  constructor(private readonly height: number) {}

  getBoundingClientRect(): { height: number } {
    return { height: this.height };
  }
}

class FakeStyle {
  properties: Record<string, string> = {};

  setProperty(name: string, value: string): void {
    this.properties[name] = value;
  }
}

class FakeRoot {
  style = new FakeStyle();
}

class FakeDocument {
  documentElement = new FakeRoot();

  constructor(private readonly header: FakeHeader | null) {}

  querySelector<T>(selector: string): T | null {
    if (selector === "[data-workspace-header]") {
      return this.header as unknown as T | null;
    }
    return null;
  }
}

class FakeWindow {
  private resizeListeners: Array<() => void> = [];

  addEventListener(type: "resize", listener: () => void): void {
    if (type === "resize") {
      this.resizeListeners.push(listener);
    }
  }

  triggerResize(): void {
    this.resizeListeners.forEach((listener) => listener());
  }
}

describe("initWorkspaceHeaderHeightDocument", () => {
  it("returns false and sets nothing when the workspace header is absent", () => {
    const doc = new FakeDocument(null);
    const win = new FakeWindow();

    expect(initWorkspaceHeaderHeightDocument(doc, win)).toBe(false);
    expect(
      doc.documentElement.style.properties["--workspace-header-height"],
    ).toBeUndefined();
  });

  it("measures the header's real rendered height and publishes it as a CSS custom property", () => {
    const doc = new FakeDocument(new FakeHeader(45.578125));
    const win = new FakeWindow();

    expect(initWorkspaceHeaderHeightDocument(doc, win)).toBe(true);
    expect(
      doc.documentElement.style.properties["--workspace-header-height"],
    ).toBe("45.578125px");
  });

  it("re-measures on window resize, so a height change (e.g. content reflow) is picked up", () => {
    const header = new FakeHeader(45.578125);
    const doc = new FakeDocument(header);
    const win = new FakeWindow();

    initWorkspaceHeaderHeightDocument(doc, win);
    expect(
      doc.documentElement.style.properties["--workspace-header-height"],
    ).toBe("45.578125px");

    // Simulate the header growing (e.g. content wrapping to a second line
    // at an unusually narrow width) and a resize firing.
    Object.defineProperty(header, "getBoundingClientRect", {
      value: () => ({ height: 62 }),
    });
    win.triggerResize();

    expect(
      doc.documentElement.style.properties["--workspace-header-height"],
    ).toBe("62px");
  });

  it("does not overwrite the published value with a zero/invalid measurement", () => {
    const header = new FakeHeader(45.578125);
    const doc = new FakeDocument(header);
    const win = new FakeWindow();

    initWorkspaceHeaderHeightDocument(doc, win);
    expect(
      doc.documentElement.style.properties["--workspace-header-height"],
    ).toBe("45.578125px");

    Object.defineProperty(header, "getBoundingClientRect", {
      value: () => ({ height: 0 }),
    });
    win.triggerResize();

    // A momentary 0 (e.g. mid-layout) must not clobber the last-known-good
    // value with something that would collapse the ribbon's offset to 0
    // and reintroduce the header/ribbon overlap this pass fixed.
    expect(
      doc.documentElement.style.properties["--workspace-header-height"],
    ).toBe("45.578125px");
  });
});
