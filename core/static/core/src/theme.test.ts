import { describe, expect, it, vi } from "vitest";

import {
  applyTheme,
  initThemeQuickSelectorDocument,
  THEME_CHOICES,
  THEME_STORAGE_KEY,
  THEME_VALUES,
} from "./theme";

class FakeStorage {
  private store: Record<string, string> = {};
  throwOnGet = false;
  throwOnSet = false;

  getItem(key: string): string | null {
    if (this.throwOnGet) throw new Error("storage unavailable");
    return Object.prototype.hasOwnProperty.call(this.store, key)
      ? this.store[key]
      : null;
  }

  setItem(key: string, value: string): void {
    if (this.throwOnSet) throw new Error("storage unavailable");
    this.store[key] = value;
  }

  removeItem(key: string): void {
    delete this.store[key];
  }
}

class FakeOption {
  dataset: Record<string, string> = {};
  attributes: Record<string, string> = {};
  private listeners: Record<
    string,
    Array<(event: { preventDefault: () => void }) => void>
  > = {};
  preventDefaultCalls = 0;

  constructor(theme: string) {
    this.dataset.themeQuickOption = theme;
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

  addEventListener(
    type: string,
    listener: (event: { preventDefault: () => void }) => void,
  ): void {
    this.listeners[type] = this.listeners[type] ?? [];
    this.listeners[type].push(listener);
  }

  click(): void {
    const event = {
      preventDefault: () => {
        this.preventDefaultCalls += 1;
      },
    };
    this.listeners.click?.forEach((listener) => listener(event));
  }
}

class FakeStatus {
  textContent = "";
}

class FakeForm {
  dataset: Record<string, string> = {};
  action = "/accounts/preferences/theme/";
  private options: FakeOption[];
  private status: FakeStatus;

  constructor(options: FakeOption[], status: FakeStatus = new FakeStatus()) {
    this.options = options;
    this.status = status;
  }

  querySelectorAll(selector: string): unknown[] {
    if (selector === "[data-theme-quick-option]") return this.options;
    return [];
  }

  querySelector(selector: string): unknown {
    if (selector === "[data-theme-quick-status]") return this.status;
    return null;
  }
}

class FakeBody {
  dataset: Record<string, string> = {};
}

class FakeDocumentElement {
  dataset: Record<string, string> = {};
}

class FakeWindow {
  private listeners: Record<
    string,
    Array<(event: { persisted: boolean }) => void>
  > = {};

  addEventListener(
    type: string,
    listener: (event: { persisted: boolean }) => void,
  ): void {
    this.listeners[type] = this.listeners[type] ?? [];
    this.listeners[type].push(listener);
  }

  // `persisted` defaults to `true` (a genuine bfcache restore) since
  // that is the only case these fakes exist to exercise -- tests for
  // the "ordinary load, not a bfcache restore" case pass `false`
  // explicitly.
  fire(type: string, persisted = true): void {
    this.listeners[type]?.forEach((listener) => listener({ persisted }));
  }

  listenerCount(type: string): number {
    return this.listeners[type]?.length ?? 0;
  }
}

class FakeDocument {
  body = new FakeBody();
  documentElement = new FakeDocumentElement();
  visibilityState: "visible" | "hidden" = "visible";
  private forms: FakeForm[];
  private allOptions: FakeOption[];
  private listeners: Record<string, Array<() => void>> = {};

  constructor(forms: FakeForm[], allOptions: FakeOption[]) {
    this.forms = forms;
    this.allOptions = allOptions;
  }

  querySelectorAll(selector: string): unknown[] {
    if (selector === "[data-theme-quick-form]") return this.forms;
    if (selector === "[data-theme-quick-option]") return this.allOptions;
    return [];
  }

  addEventListener(type: string, listener: () => void): void {
    this.listeners[type] = this.listeners[type] ?? [];
    this.listeners[type].push(listener);
  }

  fire(type: string): void {
    this.listeners[type]?.forEach((listener) => listener());
  }
}

function buildFormAndDoc(themeValues: string[]) {
  const options = themeValues.map((value) => new FakeOption(value));
  const form = new FakeForm(options);
  const doc = new FakeDocument([form], options);
  return { doc: doc as unknown as Document, form, options };
}

// Production always writes `data-theme` to `<html>` and `<body>`
// together (see `applyTheme`); these two small helpers mirror that
// invariant for test setup/assertions, so the tests exercise the same
// "both elements agree" property production actually relies on rather
// than only ever checking one of the two.
function setInitialTheme(doc: Document, theme: string): void {
  const fakeDoc = doc as unknown as FakeDocument;
  fakeDoc.documentElement.dataset.theme = theme;
  fakeDoc.body.dataset.theme = theme;
}

function expectSyncedTheme(doc: Document, theme: string): void {
  const fakeDoc = doc as unknown as FakeDocument;
  expect(fakeDoc.documentElement.dataset.theme).toBe(theme);
  expect(fakeDoc.body.dataset.theme).toBe(theme);
}

// `FormData` is used directly by theme.ts's click handler (`new
// FormData(form)`); jsdom's global FormData requires a real DOM
// <form> element, which these lightweight fakes deliberately are not.
// A minimal global stub keeps the fakes usable without pulling in a
// real DOM form just to hold three string fields.
class StubFormData {
  private data = new Map<string, string>();
  set(key: string, value: string): void {
    this.data.set(key, value);
  }
}
vi.stubGlobal("FormData", StubFormData);

describe("THEME_CHOICES / THEME_VALUES", () => {
  it("registers exactly the eight approved themes, in lineup order", () => {
    expect(THEME_VALUES).toEqual([
      "warm-light",
      "dark",
      "glacier",
      "granite",
      "alpine-mist",
      "blue-dusk",
      "midnight-ridge",
      "nightfall",
    ]);
    expect(THEME_CHOICES).toEqual([
      ["warm-light", "Warm Light"],
      ["dark", "Dark"],
      ["glacier", "Glacier"],
      ["granite", "Granite"],
      ["alpine-mist", "Alpine Mist"],
      ["blue-dusk", "Blue Dusk"],
      ["midnight-ridge", "Midnight Ridge"],
      ["nightfall", "Nightfall"],
    ]);
  });
});

describe("promoted themes", () => {
  it("applies a promoted (non-legacy) theme value to both <html> and <body>", () => {
    const { doc, options } = buildFormAndDoc([
      "warm-light",
      "dark",
      "midnight-ridge",
    ]);
    applyTheme(doc, "midnight-ridge");

    expectSyncedTheme(doc, "midnight-ridge");
    expect(options[2].getAttribute("aria-current")).toBe("true");
    expect(options[0].getAttribute("aria-current")).toBeNull();
    expect(options[1].getAttribute("aria-current")).toBeNull();
  });

  it("optimistically switches to a promoted theme and rolls back on save failure", async () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "blue-dusk"]);
    setInitialTheme(doc, "warm-light");
    const fetchImpl = vi.fn().mockResolvedValue({ ok: false });
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );

    options[1].click();
    expectSyncedTheme(doc, "blue-dusk");

    await Promise.resolve();
    await Promise.resolve();

    expectSyncedTheme(doc, "warm-light");
  });

  it("ignores a click on a Palette Lab id even if one somehow appeared as an option value", () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "cool-a"]);
    setInitialTheme(doc, "warm-light");
    const fetchImpl = vi.fn();
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );

    options[1].click();

    expect(fetchImpl).not.toHaveBeenCalled();
    expectSyncedTheme(doc, "warm-light");
  });
});

describe("applyTheme", () => {
  it("sets the document's data-theme and marks the matching option current", () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "dark"]);
    applyTheme(doc, "dark");

    expectSyncedTheme(doc, "dark");
    expect(options[0].getAttribute("aria-current")).toBeNull();
    expect(options[1].getAttribute("aria-current")).toBe("true");
  });

  it("clears aria-current from every option that no longer matches", () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "dark"]);
    applyTheme(doc, "warm-light");
    applyTheme(doc, "dark");

    expect(options[0].getAttribute("aria-current")).toBeNull();
    expect(options[1].getAttribute("aria-current")).toBe("true");
  });
});

describe("initThemeQuickSelectorDocument", () => {
  it("returns false when there is no quick-selector form in the document", () => {
    const doc = new FakeDocument([], []) as unknown as Document;
    const fetchImpl = vi.fn();
    const result = initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );
    expect(result).toBe(false);
  });

  it("returns true and marks the form initialized when a quick-selector form exists", () => {
    const { doc, form } = buildFormAndDoc(["warm-light", "dark"]);
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true });
    const result = initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );
    expect(result).toBe(true);
    expect(form.dataset.themeQuickInitialized).toBe("true");
  });

  it("does not attach a second set of listeners on a form already initialized", () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "dark"]);
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true });
    const storage = new FakeStorage() as unknown as Storage;
    initThemeQuickSelectorDocument(doc, fetchImpl, storage);
    initThemeQuickSelectorDocument(doc, fetchImpl, storage);

    options[1].click();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
  });

  it("applies the clicked theme immediately (optimistic), before the fetch resolves", () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "dark"]);
    setInitialTheme(doc, "warm-light");
    let resolveFetch: (value: { ok: boolean }) => void = () => {};
    const fetchImpl = vi.fn(
      () => new Promise<{ ok: boolean }>((resolve) => (resolveFetch = resolve)),
    );
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );

    options[1].click();

    expectSyncedTheme(doc, "dark");
    expect(options[1].getAttribute("aria-current")).toBe("true");
    resolveFetch({ ok: true });
  });

  it("prevents default navigation on a recognized theme option", () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "dark"]);
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true });
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );

    options[1].click();
    expect(options[1].preventDefaultCalls).toBe(1);
  });

  it("treats <html> (documentElement), not <body>, as the single authoritative read source for the current theme", () => {
    // Deliberately desync the
    // two elements to prove `currentTheme()` reads from
    // `documentElement` specifically -- not `body`, and not "whichever
    // agrees with a stale assumption." If this ever regressed back to
    // reading `body`, this test would fail because the click below
    // would be treated as a no-op (clicking "dark" while `body` already
    // says "dark") instead of firing.
    const { doc, options } = buildFormAndDoc(["warm-light", "dark"]);
    const fakeDoc = doc as unknown as FakeDocument;
    fakeDoc.documentElement.dataset.theme = "warm-light";
    fakeDoc.body.dataset.theme = "dark";
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true });
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );

    options[1].click();

    expect(fetchImpl).toHaveBeenCalledTimes(1);
    expectSyncedTheme(doc, "dark");
  });

  it("does nothing (no fetch, no preventDefault) when the clicked theme is already current", () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "dark"]);
    setInitialTheme(doc, "warm-light");
    const fetchImpl = vi.fn();
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );

    options[0].click();
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("writes the chosen theme to sessionStorage only after a successful save", async () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "dark"]);
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true });
    const storage = new FakeStorage();
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      storage as unknown as Storage,
    );

    options[1].click();
    await Promise.resolve();
    await Promise.resolve();

    expect(storage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("reverts to the previous theme and does not write storage when the save fails (non-ok response)", async () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "dark"]);
    setInitialTheme(doc, "warm-light");
    const fetchImpl = vi.fn().mockResolvedValue({ ok: false });
    const storage = new FakeStorage();
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      storage as unknown as Storage,
    );

    options[1].click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expectSyncedTheme(doc, "warm-light");
    expect(options[0].getAttribute("aria-current")).toBe("true");
    expect(options[1].getAttribute("aria-current")).toBeNull();
    expect(storage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it("reverts to the previous theme when the fetch itself rejects (network failure)", async () => {
    const { doc, options } = buildFormAndDoc(["warm-light", "dark"]);
    setInitialTheme(doc, "warm-light");
    const fetchImpl = vi.fn().mockRejectedValue(new Error("network down"));
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );

    options[1].click();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expectSyncedTheme(doc, "warm-light");
  });

  it("posts a status message while saving and again once saved", async () => {
    const { doc, form, options } = buildFormAndDoc(["warm-light", "dark"]);
    const fetchImpl = vi.fn().mockResolvedValue({ ok: true });
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );

    options[1].click();
    const status = form.querySelector(
      "[data-theme-quick-status]",
    ) as FakeStatus;
    expect(status.textContent).toBe("Saving theme…");

    await Promise.resolve();
    await Promise.resolve();
    expect(status.textContent).toBe("Theme saved.");
  });

  it("ignores a click on an unrecognized/invalid theme option value", () => {
    const options = [
      new FakeOption("warm-light"),
      new FakeOption("not-a-real-theme"),
    ];
    const form = new FakeForm(options);
    const doc = new FakeDocument([form], options) as unknown as Document;
    const fetchImpl = vi.fn();
    initThemeQuickSelectorDocument(
      doc,
      fetchImpl,
      new FakeStorage() as unknown as Storage,
    );

    options[1].click();
    expect(fetchImpl).not.toHaveBeenCalled();
    expect(options[1].preventDefaultCalls).toBe(0);
  });

  describe("pageshow reconciliation (genuine bfcache restore only)", () => {
    it("does nothing on a bfcache-restore pageshow when sessionStorage is empty -- the server-rendered theme is trusted", () => {
      const { doc } = buildFormAndDoc(["warm-light", "dark"]);
      setInitialTheme(doc, "dark");
      const windowLike = new FakeWindow();
      initThemeQuickSelectorDocument(
        doc,
        vi.fn(),
        new FakeStorage() as unknown as Storage,
        windowLike as unknown as Window,
      );

      windowLike.fire("pageshow", true);
      expectSyncedTheme(doc, "dark");
    });

    it("reconciles a stale bfcache-restored DOM to the last known-good saved theme on a persisted pageshow", () => {
      const { doc } = buildFormAndDoc(["warm-light", "dark"]);
      setInitialTheme(doc, "warm-light");
      const storage = new FakeStorage();
      storage.setItem(THEME_STORAGE_KEY, "dark");
      const windowLike = new FakeWindow();
      initThemeQuickSelectorDocument(
        doc,
        vi.fn(),
        storage as unknown as Storage,
        windowLike as unknown as Window,
      );

      windowLike.fire("pageshow", true);
      expectSyncedTheme(doc, "dark");
    });

    /**
     * `pageshow` fires on *every* page
     * load, not only a bfcache restore -- a plain reload, a fresh
     * navigation, and a fresh login all fire it too, with
     * `event.persisted === false`. Ignoring `persisted` entirely and
     * reconciling from this browser's
     * own stale `sessionStorage` value regardless would silently overwrite
     * a freshly-rendered, correct, server-authoritative theme (e.g.
     * "nightfall", just set from another device) with this browser's
     * own older saved choice (e.g. "granite") on every single reload.
     */
    it("does NOT reconcile on an ordinary (non-bfcache) pageshow, even when sessionStorage disagrees with the freshly-rendered server theme", () => {
      const { doc } = buildFormAndDoc(["warm-light", "dark"]);
      // The server just rendered "dark" fresh (e.g. another device's
      // more recent change) -- sessionStorage still holds this
      // browser's own older "warm-light" choice from before that.
      setInitialTheme(doc, "dark");
      const storage = new FakeStorage();
      storage.setItem(THEME_STORAGE_KEY, "warm-light");
      const windowLike = new FakeWindow();
      initThemeQuickSelectorDocument(
        doc,
        vi.fn(),
        storage as unknown as Storage,
        windowLike as unknown as Window,
      );

      windowLike.fire("pageshow", false);

      expectSyncedTheme(doc, "dark");
    });

    it("no longer reconciles on visibilitychange at all -- the listener was removed outright", () => {
      const { doc } = buildFormAndDoc(["warm-light", "dark"]);
      const fakeDoc = doc as unknown as FakeDocument;
      setInitialTheme(doc, "warm-light");
      const storage = new FakeStorage();
      storage.setItem(THEME_STORAGE_KEY, "dark");
      initThemeQuickSelectorDocument(
        doc,
        vi.fn(),
        storage as unknown as Storage,
      );

      fakeDoc.visibilityState = "visible";
      fakeDoc.fire("visibilitychange");

      expectSyncedTheme(doc, "warm-light");
    });

    it("ignores an invalid stored value rather than applying it, even on a genuine bfcache restore", () => {
      const { doc } = buildFormAndDoc(["warm-light", "dark"]);
      setInitialTheme(doc, "warm-light");
      const storage = new FakeStorage();
      storage.setItem(THEME_STORAGE_KEY, "not-a-real-theme");
      const windowLike = new FakeWindow();
      initThemeQuickSelectorDocument(
        doc,
        vi.fn(),
        storage as unknown as Storage,
        windowLike as unknown as Window,
      );

      windowLike.fire("pageshow", true);
      expectSyncedTheme(doc, "warm-light");
    });

    it("does not throw and simply skips reconciliation when storage access throws", () => {
      const { doc } = buildFormAndDoc(["warm-light", "dark"]);
      setInitialTheme(doc, "warm-light");
      const storage = new FakeStorage();
      storage.throwOnGet = true;
      const windowLike = new FakeWindow();
      initThemeQuickSelectorDocument(
        doc,
        vi.fn(),
        storage as unknown as Storage,
        windowLike as unknown as Window,
      );

      expect(() => windowLike.fire("pageshow")).not.toThrow();
      expectSyncedTheme(doc, "warm-light");
    });

    it("binds the lifecycle listeners at most once even if init runs twice", () => {
      const { doc } = buildFormAndDoc(["warm-light", "dark"]);
      const windowLike = new FakeWindow();
      const storage = new FakeStorage() as unknown as Storage;
      initThemeQuickSelectorDocument(
        doc,
        vi.fn(),
        storage,
        windowLike as unknown as Window,
      );
      initThemeQuickSelectorDocument(
        doc,
        vi.fn(),
        storage,
        windowLike as unknown as Window,
      );

      expect(windowLike.listenerCount("pageshow")).toBe(1);
    });
  });
});
