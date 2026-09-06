import { describe, expect, it } from "vitest";

import { initWorkspaceTreeRowTopDocument } from "./workspace-tree-row-top";

class FakeRow {
  constructor(private readonly top: number) {}

  getBoundingClientRect(): { top: number } {
    return { top: this.top };
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

  constructor(private readonly row: FakeRow | null) {}

  querySelector<T>(selector: string): T | null {
    if (selector === ".workspace-shell__tree-rail") {
      return this.row as unknown as T | null;
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

describe("initWorkspaceTreeRowTopDocument", () => {
  it("returns false and sets nothing when no tree rail is present", () => {
    const doc = new FakeDocument(null);
    const win = new FakeWindow();

    expect(initWorkspaceTreeRowTopDocument(doc, win)).toBe(false);
    expect(
      doc.documentElement.style.properties["--workspace-tree-row-top"],
    ).toBeUndefined();
  });

  it("measures the tree rail's real rendered top and publishes it as a CSS custom property", () => {
    const doc = new FakeDocument(new FakeRow(96));
    const win = new FakeWindow();

    expect(initWorkspaceTreeRowTopDocument(doc, win)).toBe(true);
    expect(
      doc.documentElement.style.properties["--workspace-tree-row-top"],
    ).toBe("96px");
  });

  it("captures a Django messages banner pushing the row further down than the header alone would", () => {
    // 64px header + 32px banner, e.g. the success message shown after a
    // quick-trash/drag-to-Trash redirect -- the row's real top reflects
    // both, unlike a formula that only ever knew about the header.
    const doc = new FakeDocument(new FakeRow(96));
    const win = new FakeWindow();

    initWorkspaceTreeRowTopDocument(doc, win);

    expect(
      doc.documentElement.style.properties["--workspace-tree-row-top"],
    ).toBe("96px");
  });

  it("re-measures on window resize", () => {
    const row = new FakeRow(96);
    const doc = new FakeDocument(row);
    const win = new FakeWindow();

    initWorkspaceTreeRowTopDocument(doc, win);
    expect(
      doc.documentElement.style.properties["--workspace-tree-row-top"],
    ).toBe("96px");

    Object.defineProperty(row, "getBoundingClientRect", {
      value: () => ({ top: 64 }),
    });
    win.triggerResize();

    expect(
      doc.documentElement.style.properties["--workspace-tree-row-top"],
    ).toBe("64px");
  });

  it("does not overwrite the published value with a zero/negative measurement", () => {
    const row = new FakeRow(96);
    const doc = new FakeDocument(row);
    const win = new FakeWindow();

    initWorkspaceTreeRowTopDocument(doc, win);
    expect(
      doc.documentElement.style.properties["--workspace-tree-row-top"],
    ).toBe("96px");

    Object.defineProperty(row, "getBoundingClientRect", {
      value: () => ({ top: 0 }),
    });
    win.triggerResize();

    expect(
      doc.documentElement.style.properties["--workspace-tree-row-top"],
    ).toBe("96px");
  });
});
