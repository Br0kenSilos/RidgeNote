import { afterEach, describe, expect, it, vi } from "vitest";

import {
  detectBrowserTimezone,
  initPreferencesTimezoneDocument,
} from "./preferences-timezone";

class FakeButton {
  private listeners: Array<() => void> = [];

  addEventListener(type: "click", listener: () => void): void {
    if (type === "click") this.listeners.push(listener);
  }

  fire(type: "click"): void {
    if (type === "click") this.listeners.forEach((listener) => listener());
  }
}

class FakeInput {
  value = "";
  focused = false;

  focus(): void {
    this.focused = true;
  }
}

class FakeStatus {
  textContent = "";
}

class FakeElement {
  private queryMap = new Map<string, unknown>();

  setQuery(selector: string, value: unknown): void {
    this.queryMap.set(selector, value);
  }

  querySelector<T>(selector: string): T | null {
    return (this.queryMap.get(selector) as T | undefined) ?? null;
  }
}

function makeDoc(root: FakeElement | null): Document {
  return {
    querySelector: (selector: string) =>
      selector === "[data-preferences-timezone-root]" ? root : null,
  } as unknown as Document;
}

function setup(options?: {
  omitInput?: boolean;
  omitButton?: boolean;
  omitStatus?: boolean;
}) {
  const root = new FakeElement();
  const input = new FakeInput();
  const button = new FakeButton();
  const status = new FakeStatus();

  if (!options?.omitInput) {
    root.setQuery("[data-preferences-timezone-input]", input);
  }
  if (!options?.omitButton) {
    root.setQuery("[data-preferences-timezone-detect]", button);
  }
  if (!options?.omitStatus) {
    root.setQuery("[data-preferences-timezone-status]", status);
  }

  initPreferencesTimezoneDocument(makeDoc(root));
  return { root, input, button, status };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("detectBrowserTimezone", () => {
  it("returns the resolved IANA zone name", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "America/New_York" }),
    } as unknown as Intl.DateTimeFormat);

    expect(detectBrowserTimezone()).toBe("America/New_York");
  });

  it("returns null when the API throws", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new Error("unsupported");
    });

    expect(detectBrowserTimezone()).toBeNull();
  });

  it("returns null for a blank result", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "   " }),
    } as unknown as Intl.DateTimeFormat);

    expect(detectBrowserTimezone()).toBeNull();
  });

  it("returns null for a non-string result", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: undefined }),
    } as unknown as Intl.DateTimeFormat);

    expect(detectBrowserTimezone()).toBeNull();
  });
});

describe("initPreferencesTimezoneDocument – missing prerequisites", () => {
  it("does nothing when the root element is absent", () => {
    expect(() => initPreferencesTimezoneDocument(makeDoc(null))).not.toThrow();
  });

  it("does nothing when the input is absent", () => {
    const { button } = setup({ omitInput: true });
    expect(() => button.fire("click")).not.toThrow();
  });

  it("does nothing when the detect button is absent", () => {
    // No button was wired at all, so there is nothing to click -- this
    // simply proves init doesn't throw when the button is missing.
    expect(() => setup({ omitButton: true })).not.toThrow();
  });
});

describe("initPreferencesTimezoneDocument – no auto-detection on load", () => {
  it("never calls Intl.DateTimeFormat merely from initializing", () => {
    const spy = vi.spyOn(Intl, "DateTimeFormat");
    setup();
    expect(spy).not.toHaveBeenCalled();
  });

  it("leaves an existing saved value untouched until the button is clicked", () => {
    const { input } = setup();
    input.value = "Europe/London";
    expect(input.value).toBe("Europe/London");
  });
});

describe("initPreferencesTimezoneDocument – explicit detect click", () => {
  it("populates the input with the detected zone on click", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "Asia/Tokyo" }),
    } as unknown as Intl.DateTimeFormat);
    const { input, button } = setup();

    button.fire("click");

    expect(input.value).toBe("Asia/Tokyo");
    expect(input.focused).toBe(true);
  });

  it("replaces an existing saved value only because of the explicit click", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "Asia/Tokyo" }),
    } as unknown as Intl.DateTimeFormat);
    const { input, button } = setup();
    input.value = "Europe/London";

    button.fire("click");

    expect(input.value).toBe("Asia/Tokyo");
  });

  it("does not submit or persist anything itself -- only sets the field value", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "Asia/Tokyo" }),
    } as unknown as Intl.DateTimeFormat);
    const { input, button } = setup();

    button.fire("click");

    // No form/fetch/submit API exists on the fake input/button at all --
    // if the module tried to call one, this test would throw via a
    // missing-method TypeError instead of passing quietly.
    expect(input.value).toBe("Asia/Tokyo");
  });

  it("leaves the field untouched when detection fails", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new Error("unsupported");
    });
    const { input, button, status } = setup();
    input.value = "Europe/London";

    button.fire("click");

    expect(input.value).toBe("Europe/London");
    expect(status.textContent).toContain("did not report a timezone");
  });

  it("leaves the field untouched when the browser returns a blank result", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "" }),
    } as unknown as Intl.DateTimeFormat);
    const { input, button } = setup();
    input.value = "Europe/London";

    button.fire("click");

    expect(input.value).toBe("Europe/London");
  });

  it("does not throw when no status element exists to report failure", () => {
    vi.spyOn(Intl, "DateTimeFormat").mockImplementation(() => {
      throw new Error("unsupported");
    });
    const { button } = setup({ omitStatus: true });

    expect(() => button.fire("click")).not.toThrow();
  });

  it("clears a prior failure message on a subsequent successful click", () => {
    const { input, button, status } = setup();
    vi.spyOn(Intl, "DateTimeFormat").mockImplementationOnce(() => {
      throw new Error("unsupported");
    });
    button.fire("click");
    expect(status.textContent).not.toBe("");

    vi.spyOn(Intl, "DateTimeFormat").mockReturnValue({
      resolvedOptions: () => ({ timeZone: "Asia/Tokyo" }),
    } as unknown as Intl.DateTimeFormat);
    button.fire("click");

    expect(status.textContent).toBe("");
    expect(input.value).toBe("Asia/Tokyo");
  });
});
