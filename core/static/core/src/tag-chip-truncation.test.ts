import { describe, expect, it } from "vitest";

import {
  initTagChipTruncation,
  type TagChipElementLike,
  type TagChipLabelElementLike,
} from "./tag-chip-truncation";

class FakeLabel implements TagChipLabelElementLike {
  constructor(
    public textContent: string | null,
    public scrollWidth: number,
    public clientWidth: number,
  ) {}
}

class FakeChip implements TagChipElementLike {
  private readonly attributes = new Map<string, string>();

  constructor(private readonly label: FakeLabel | null) {}

  querySelector(selector: string): TagChipLabelElementLike | null {
    return selector === ".note-list__tag-chip__label" ? this.label : null;
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
}

class FakeDocument {
  constructor(private readonly chips: FakeChip[]) {}

  querySelectorAll(selector: string): FakeChip[] {
    return selector === ".note-list__tag-chip" ? this.chips : [];
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

describe("initTagChipTruncation", () => {
  it("returns false and does nothing when no tag chips are present", () => {
    const doc = new FakeDocument([]);
    const win = new FakeWindow();

    expect(initTagChipTruncation(doc, win)).toBe(false);
  });

  it("leaves a chip whose label fits (scrollWidth === clientWidth) with no tooltip or tabindex", () => {
    const chip = new FakeChip(new FakeLabel("Short", 40, 40));
    const doc = new FakeDocument([chip]);
    const win = new FakeWindow();

    expect(initTagChipTruncation(doc, win)).toBe(true);
    expect(chip.getAttribute("data-tooltip")).toBeNull();
    expect(chip.getAttribute("tabindex")).toBeNull();
  });

  it("gives the outer chip a full-label tooltip and keyboard focus when the inner label is genuinely truncated (scrollWidth > clientWidth)", () => {
    const chip = new FakeChip(
      new FakeLabel("A Genuinely Long Tag Name", 220, 144),
    );
    const doc = new FakeDocument([chip]);
    const win = new FakeWindow();

    initTagChipTruncation(doc, win);

    expect(chip.getAttribute("data-tooltip")).toBe("A Genuinely Long Tag Name");
    expect(chip.getAttribute("tabindex")).toBe("0");
  });

  it("measures the inner label, not the outer chip -- the outer element has no overflow of its own to detect", () => {
    // Even though this label reports plenty of overflow, a chip with no
    // label child at all (malformed markup) must never crash or guess.
    const chip = new FakeChip(null);
    const doc = new FakeDocument([chip]);
    const win = new FakeWindow();

    expect(() => initTagChipTruncation(doc, win)).not.toThrow();
    expect(chip.getAttribute("data-tooltip")).toBeNull();
    expect(chip.getAttribute("tabindex")).toBeNull();
  });

  it("never adds a tooltip to a chip with no label text, even if somehow overflowing", () => {
    const chip = new FakeChip(new FakeLabel("", 50, 10));
    const doc = new FakeDocument([chip]);
    const win = new FakeWindow();

    initTagChipTruncation(doc, win);

    expect(chip.getAttribute("data-tooltip")).toBeNull();
    expect(chip.getAttribute("tabindex")).toBeNull();
  });

  it("adds the tooltip/tabindex on resize once a chip newly crosses the truncation threshold", () => {
    const label = new FakeLabel("Borderline Tag", 100, 100);
    const chip = new FakeChip(label);
    const doc = new FakeDocument([chip]);
    const win = new FakeWindow();

    initTagChipTruncation(doc, win);
    expect(chip.getAttribute("data-tooltip")).toBeNull();

    // Simulate the viewport narrowing enough that the label's own
    // available width shrinks below its content width.
    label.clientWidth = 70;
    win.triggerResize();

    expect(chip.getAttribute("data-tooltip")).toBe("Borderline Tag");
    expect(chip.getAttribute("tabindex")).toBe("0");
  });

  it("removes the tooltip/tabindex on resize once a previously-truncated chip fits again", () => {
    const label = new FakeLabel("Borderline Tag", 100, 70);
    const chip = new FakeChip(label);
    const doc = new FakeDocument([chip]);
    const win = new FakeWindow();

    initTagChipTruncation(doc, win);
    expect(chip.getAttribute("data-tooltip")).toBe("Borderline Tag");
    expect(chip.getAttribute("tabindex")).toBe("0");

    // Simulate the viewport widening (e.g. rotating a tablet to
    // landscape) so the chip's full label now fits without truncation.
    label.clientWidth = 100;
    win.triggerResize();

    expect(chip.getAttribute("data-tooltip")).toBeNull();
    expect(chip.getAttribute("tabindex")).toBeNull();
  });

  it("evaluates every chip independently -- a mix of fitting and truncated chips on the same page", () => {
    const fitting = new FakeChip(new FakeLabel("Fits", 30, 30));
    const truncated = new FakeChip(
      new FakeLabel("Does Not Fit At All", 200, 144),
    );
    const doc = new FakeDocument([fitting, truncated]);
    const win = new FakeWindow();

    initTagChipTruncation(doc, win);

    expect(fitting.getAttribute("data-tooltip")).toBeNull();
    expect(truncated.getAttribute("data-tooltip")).toBe("Does Not Fit At All");
  });
});
