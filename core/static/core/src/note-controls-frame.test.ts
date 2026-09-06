import { describe, expect, it, vi } from "vitest";

import {
  type ControlsFrameDocumentLike,
  type ControlsFrameElementLike,
  type ControlsFrameRibbonLike,
  type ControlsFrameWindowLike,
  initNoteControlsFrameDocument,
} from "./note-controls-frame";

class MockFrame implements ControlsFrameElementLike {
  top = 40;
  style = { setProperty: vi.fn() };

  getBoundingClientRect(): { top: number } {
    return { top: this.top };
  }
}

class MockRibbon implements ControlsFrameRibbonLike {
  bottom = 260;

  getBoundingClientRect(): { bottom: number } {
    return { bottom: this.bottom };
  }
}

class MockResizeObserver {
  static instances: MockResizeObserver[] = [];
  callback: () => void;
  observedTargets: unknown[] = [];

  constructor(callback: () => void) {
    this.callback = callback;
    MockResizeObserver.instances.push(this);
  }

  observe(target: unknown): void {
    this.observedTargets.push(target);
  }
}

function createDocument(
  frame: MockFrame | null,
  ribbon: MockRibbon | null,
): ControlsFrameDocumentLike {
  return {
    querySelector: <T>(selector: string): T | null => {
      if (selector === "[data-note-controls-frame]")
        return frame as unknown as T | null;
      if (selector === ".note-workspace__ribbon")
        return ribbon as unknown as T | null;
      return null;
    },
  };
}

function createWindow(marginBottom = "0px"): ControlsFrameWindowLike & {
  addEventListener: ReturnType<
    typeof vi.fn<(type: "resize", listener: () => void) => void>
  >;
} {
  return {
    addEventListener: vi.fn<(type: "resize", listener: () => void) => void>(),
    getComputedStyle: () => ({ marginBottom }),
  };
}

describe("initNoteControlsFrameDocument", () => {
  it("returns false and does nothing when the frame element is absent", () => {
    const doc = createDocument(null, new MockRibbon());
    const windowLike = createWindow();

    expect(initNoteControlsFrameDocument(doc, windowLike)).toBe(false);
    expect(windowLike.addEventListener).not.toHaveBeenCalled();
  });

  it("returns false and does nothing when the ribbon element is absent", () => {
    const doc = createDocument(new MockFrame(), null);
    const windowLike = createWindow();

    expect(initNoteControlsFrameDocument(doc, windowLike)).toBe(false);
    expect(windowLike.addEventListener).not.toHaveBeenCalled();
  });

  it("publishes the measured height as an inline custom property on the frame element", () => {
    const frame = new MockFrame();
    const ribbon = new MockRibbon();
    const doc = createDocument(frame, ribbon);
    const windowLike = createWindow();

    expect(initNoteControlsFrameDocument(doc, windowLike)).toBe(true);

    expect(frame.style.setProperty).toHaveBeenCalledWith(
      "--note-controls-frame-height",
      "220px",
    );
  });

  it("does not publish a non-positive measurement", () => {
    const frame = new MockFrame();
    frame.top = 300;
    const ribbon = new MockRibbon();
    ribbon.bottom = 100;
    const doc = createDocument(frame, ribbon);
    const windowLike = createWindow();

    initNoteControlsFrameDocument(doc, windowLike);

    expect(frame.style.setProperty).not.toHaveBeenCalled();
  });

  it("re-measures on window resize", () => {
    const frame = new MockFrame();
    const ribbon = new MockRibbon();
    const doc = createDocument(frame, ribbon);
    const windowLike = createWindow();

    initNoteControlsFrameDocument(doc, windowLike);
    frame.style.setProperty.mockClear();

    ribbon.bottom = 300;
    const resizeHandler = windowLike.addEventListener.mock
      .calls[0][1] as () => void;
    resizeHandler();

    expect(frame.style.setProperty).toHaveBeenCalledWith(
      "--note-controls-frame-height",
      "260px",
    );
  });

  it("observes the ribbon with a ResizeObserver when one is available, and re-measures on its callback", () => {
    MockResizeObserver.instances = [];
    const frame = new MockFrame();
    const ribbon = new MockRibbon();
    const doc = createDocument(frame, ribbon);
    const windowLike = createWindow();
    windowLike.ResizeObserver = MockResizeObserver;

    initNoteControlsFrameDocument(doc, windowLike);

    expect(MockResizeObserver.instances).toHaveLength(1);
    expect(MockResizeObserver.instances[0].observedTargets).toEqual([ribbon]);

    frame.style.setProperty.mockClear();
    ribbon.bottom = 400;
    MockResizeObserver.instances[0].callback();

    expect(frame.style.setProperty).toHaveBeenCalledWith(
      "--note-controls-frame-height",
      "360px",
    );
  });

  it("adds the ribbon's real rendered margin-bottom to the measured height", () => {
    const frame = new MockFrame();
    const ribbon = new MockRibbon();
    const doc = createDocument(frame, ribbon);
    const windowLike = createWindow("7.2px");

    initNoteControlsFrameDocument(doc, windowLike);

    // top=40, bottom=260, marginBottom=7.2 -> 260 - 40 + 7.2 = 227.2
    expect(frame.style.setProperty).toHaveBeenCalledWith(
      "--note-controls-frame-height",
      "227.2px",
    );
  });

  it("does not throw when ResizeObserver is unavailable on the injected window", () => {
    const frame = new MockFrame();
    const ribbon = new MockRibbon();
    const doc = createDocument(frame, ribbon);
    const windowLike = createWindow();

    expect(() => initNoteControlsFrameDocument(doc, windowLike)).not.toThrow();
  });
});
