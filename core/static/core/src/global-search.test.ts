import { describe, expect, it, vi } from "vitest";

import {
  buildGlobalSearchRequestUrl,
  clampResultIndex,
  initGlobalSearchPanel,
  type GlobalSearchDocumentLike,
  type GlobalSearchElementLike,
  type GlobalSearchFetchResponseLike,
  type GlobalSearchResponse,
  type GlobalSearchSchedulerLike,
} from "./global-search";

describe("clampResultIndex", () => {
  it("returns -1 when there are no results", () => {
    expect(clampResultIndex(0, 0)).toBe(-1);
  });

  it("clamps a negative index to 0", () => {
    expect(clampResultIndex(-1, 5)).toBe(0);
  });

  it("clamps an index past the end to the last result", () => {
    expect(clampResultIndex(9, 5)).toBe(4);
  });

  it("returns the index unchanged when already in range", () => {
    expect(clampResultIndex(2, 5)).toBe(2);
  });
});

describe("buildGlobalSearchRequestUrl", () => {
  it("builds a URL with the query parameter", () => {
    expect(buildGlobalSearchRequestUrl("/notes/search/", "roadmap")).toBe(
      "/notes/search/?q=roadmap",
    );
  });

  it("URL-encodes special characters in the query", () => {
    expect(buildGlobalSearchRequestUrl("/notes/search/", "a&b")).toBe(
      "/notes/search/?q=a%26b",
    );
  });
});

// -- DOM-wiring tests ---------------------------------------------------
//
// This project's Vitest suite runs without a real DOM (no jsdom
// dependency); tests supply small hand-written fakes cast with
// `as unknown as <RealType>`, matching quick-switch.test.ts's precedent.

class FakeFocusable {
  className = "";
  textContent: string | null = null;
  href?: string;
  children: FakeFocusable[] = [];
  focusMock = vi.fn();
  private listeners = new Map<string, Array<(event: unknown) => void>>();

  appendChild(node: GlobalSearchElementLike): void {
    this.children.push(node as FakeFocusable);
  }

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  trigger(type: string, event: unknown = {}): void {
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }

  click(): void {
    this.trigger("click");
  }

  focus(): void {
    this.focusMock();
  }

  setAttribute(): void {
    // no-op; only aria-expanded bookkeeping in production code uses this
  }

  closest(): null {
    return null;
  }
}

class FakeResultsList {
  children: FakeFocusable[] = [];

  replaceChildren(...nodes: FakeFocusable[]): void {
    this.children = nodes;
  }

  querySelectorAll(selector: string): FakeFocusable[] {
    const className = selector.replace(/^\./, "");
    const found: FakeFocusable[] = [];
    const walk = (nodes: FakeFocusable[]): void => {
      for (const node of nodes) {
        if ((node.className || "").split(" ").includes(className)) {
          found.push(node);
        }
        walk(node.children);
      }
    };
    walk(this.children);
    return found;
  }
}

class FakeInput {
  value = "";
  focusMock = vi.fn();
  private listeners = new Map<string, Array<(event: unknown) => void>>();

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  trigger(type: string, event: unknown = {}): void {
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }

  focus(): void {
    this.focusMock();
  }
}

class FakeDialog {
  dataset: Record<string, string> = {};
  open = false;
  showModalMock = vi.fn(() => {
    this.open = true;
  });
  closeMock = vi.fn(() => {
    this.open = false;
    this.trigger("close");
  });
  private listeners = new Map<string, Array<(event: unknown) => void>>();
  private children = new Map<string, unknown>();

  setChild(selector: string, value: unknown): void {
    this.children.set(selector, value);
  }

  querySelector(selector: string): unknown {
    return this.children.get(selector) ?? null;
  }

  showModal(): void {
    this.showModalMock();
  }

  close(): void {
    this.closeMock();
  }

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  trigger(type: string, event: unknown = {}): void {
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }
}

class FakeScheduler implements GlobalSearchSchedulerLike {
  private nextHandle = 1;
  private timers = new Map<number, () => void>();

  setTimeout(callback: () => void): number {
    const handle = this.nextHandle++;
    this.timers.set(handle, callback);
    return handle;
  }

  clearTimeout(handle: number): void {
    this.timers.delete(handle);
  }

  pendingCount(): number {
    return this.timers.size;
  }

  runAll(): void {
    const callbacks = Array.from(this.timers.values());
    this.timers.clear();
    callbacks.forEach((callback) => callback());
  }
}

function fakeDocumentLike(): GlobalSearchDocumentLike {
  return {
    createElement(): GlobalSearchElementLike {
      return new FakeFocusable();
    },
  };
}

function keyEvent(key: string) {
  return { key, preventDefault: vi.fn() };
}

function resolvedFetch(
  payload: GlobalSearchResponse,
  ok = true,
): (url: string) => Promise<GlobalSearchFetchResponseLike> {
  return () =>
    Promise.resolve({
      json: () => Promise.resolve(payload),
      ok,
    });
}

function buildPanel(triggers: FakeFocusable[] = []) {
  const dialog = new FakeDialog();
  dialog.dataset.globalSearchUrl = "/notes/search/";
  const input = new FakeInput();
  const resultsList = new FakeResultsList();
  const closeBtn = new FakeFocusable();
  dialog.setChild("[data-global-search-input]", input);
  dialog.setChild("[data-global-search-results]", resultsList);
  dialog.setChild("[data-global-search-close]", closeBtn);

  const doc = {
    querySelector: (selector: string) =>
      selector === "#global-search-panel" ? dialog : null,
    querySelectorAll: (selector: string) =>
      selector === "[data-global-search-toggle]" ? triggers : [],
  } as unknown as Document;

  return { closeBtn, dialog, doc, input, resultsList };
}

describe("initGlobalSearchPanel", () => {
  it("returns false when the dialog is not present", () => {
    const doc = {
      querySelector: () => null,
      querySelectorAll: () => [],
    } as unknown as Document;

    expect(initGlobalSearchPanel(doc)).toBe(false);
  });

  it("initializes once and is a no-op on a second call", () => {
    const { doc } = buildPanel();
    const options = {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    };

    expect(initGlobalSearchPanel(doc, options)).toBe(true);
    expect(initGlobalSearchPanel(doc, options)).toBe(false);
  });

  it("opens the dialog and focuses the input when a trigger is clicked", () => {
    const trigger = new FakeFocusable();
    const { dialog, doc, input } = buildPanel([trigger]);

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    trigger.click();

    expect(dialog.showModalMock).toHaveBeenCalledTimes(1);
    expect(input.focusMock).toHaveBeenCalledTimes(1);
  });

  it("closes via the close button", () => {
    const trigger = new FakeFocusable();
    const { closeBtn, dialog, doc } = buildPanel([trigger]);

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    closeBtn.trigger("click");

    expect(dialog.closeMock).toHaveBeenCalledTimes(1);
  });

  it("does not fetch immediately on input; waits for the debounce delay", () => {
    const { doc, input } = buildPanel([new FakeFocusable()]);
    const scheduler = new FakeScheduler();
    const fetchFn = vi.fn(resolvedFetch({ ok: true, results: [] }));

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "roadmap";
    input.trigger("input");

    expect(fetchFn).not.toHaveBeenCalled();
    expect(scheduler.pendingCount()).toBe(1);
  });

  it("queries the backend with the query after the debounce elapses", async () => {
    const { doc, input } = buildPanel([new FakeFocusable()]);
    const scheduler = new FakeScheduler();
    const fetchFn = vi.fn(resolvedFetch({ ok: true, results: [] }));

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "roadmap";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(fetchFn).toHaveBeenCalledWith("/notes/search/?q=roadmap");
  });

  it("clears results without fetching when the query is cleared", () => {
    const { doc, input, resultsList } = buildPanel([new FakeFocusable()]);
    const scheduler = new FakeScheduler();
    const fetchFn = vi.fn(resolvedFetch({ ok: true, results: [] }));

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "roadmap";
    input.trigger("input");
    input.value = "";
    input.trigger("input");

    expect(scheduler.pendingCount()).toBe(0);
    expect(fetchFn).not.toHaveBeenCalled();
    expect(resultsList.children).toEqual([]);
  });

  it("renders results with a highlighted title, folder label, and a highlighted excerpt", async () => {
    const { doc, input, resultsList } = buildPanel([new FakeFocusable()]);
    const scheduler = new FakeScheduler();
    const fetchFn = resolvedFetch({
      ok: true,
      results: [
        {
          body_segments: [
            { highlight: false, text: "spotted a rare " },
            { highlight: true, text: "pangolin" },
            { highlight: false, text: " on the trip" },
          ],
          folder_label: "Field",
          id: 5,
          title_segments: [
            { highlight: false, text: "Field " },
            { highlight: true, text: "Notes" },
          ],
          url: "/notes/5/",
        },
      ],
    });

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "pangolin";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(resultsList.children).toHaveLength(1);
    const link = resultsList.children[0].children[0];
    expect(link.className).toBe("global-search-panel__link");
    expect(link.href).toBe("/notes/5/");
    const [titleEl, folderEl, excerptEl] = link.children;
    const [titlePlain, titleMark] = titleEl.children;
    expect(titlePlain.textContent).toBe("Field ");
    expect(titleMark.textContent).toBe("Notes");
    expect(folderEl.textContent).toBe("Field");
    expect(excerptEl.className).toBe("global-search-panel__excerpt");
    const [before, mark, after] = excerptEl.children;
    expect(before.textContent).toBe("spotted a rare ");
    expect(mark.textContent).toBe("pangolin");
    expect(after.textContent).toBe(" on the trip");
  });

  it("renders a title-only match with no excerpt element and no fabricated highlight", async () => {
    const { doc, input, resultsList } = buildPanel([new FakeFocusable()]);
    const scheduler = new FakeScheduler();
    const fetchFn = resolvedFetch({
      ok: true,
      results: [
        {
          body_segments: [],
          folder_label: "Unfiled",
          id: 9,
          title_segments: [
            { highlight: false, text: "Migration " },
            { highlight: true, text: "Notes" },
          ],
          url: "/notes/9/",
        },
      ],
    });

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "notes";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    const link = resultsList.children[0].children[0];
    expect(link.children).toHaveLength(2); // title, folder -- no excerpt <p>
    const [titleEl, folderEl] = link.children;
    expect(folderEl.textContent).toBe("Unfiled");
    const [titlePlain, titleMark] = titleEl.children;
    expect(titlePlain.textContent).toBe("Migration ");
    expect(titleMark.textContent).toBe("Notes");
  });

  it("renders hostile segment text as inert text nodes, never HTML", async () => {
    const { doc, input, resultsList } = buildPanel([new FakeFocusable()]);
    const scheduler = new FakeScheduler();
    const fetchFn = resolvedFetch({
      ok: true,
      results: [
        {
          body_segments: [
            {
              highlight: false,
              text: "<img onerror=alert(1) src=x> unrelated",
            },
          ],
          folder_label: "Unfiled",
          id: 11,
          title_segments: [
            { highlight: false, text: "<script>alert(1)</script> " },
            { highlight: true, text: "pangolin" },
          ],
          url: "/notes/11/",
        },
      ],
    });

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "pangolin";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    const link = resultsList.children[0].children[0];
    const [titleEl, , excerptEl] = link.children;
    const [titlePlain, titleMark] = titleEl.children;
    expect(titlePlain.textContent).toBe("<script>alert(1)</script> ");
    expect(titleMark.textContent).toBe("pangolin");
    const [bodyPlain] = excerptEl.children;
    expect(bodyPlain.textContent).toBe(
      "<img onerror=alert(1) src=x> unrelated",
    );
    // Set via textContent only (never innerHTML), so this text can never be
    // interpreted as markup by a real DOM -- verified structurally: each
    // segment became its own span/mark element with plain text content,
    // not a single blob of concatenated HTML.
  });

  it("shows a no-results message when nothing matches", async () => {
    const { doc, input, resultsList } = buildPanel([new FakeFocusable()]);
    const scheduler = new FakeScheduler();

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler,
    });

    input.value = "zzznonexistent";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(resultsList.children).toHaveLength(1);
    expect(resultsList.children[0].className).toBe(
      "global-search-panel__empty",
    );
    expect(resultsList.children[0].textContent).toBe("No results.");
  });

  it("shows a compact error message when the response is not ok", async () => {
    const { doc, input, resultsList } = buildPanel([new FakeFocusable()]);
    const scheduler = new FakeScheduler();

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: false }, false),
      scheduler,
    });

    input.value = "roadmap";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(resultsList.children[0].textContent).toBe(
      "Couldn't load results. Try again.",
    );
  });

  it("shows a compact error message when the fetch rejects", async () => {
    const { doc, input, resultsList } = buildPanel([new FakeFocusable()]);
    const scheduler = new FakeScheduler();

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: () => Promise.reject(new Error("network down")),
      scheduler,
    });

    input.value = "roadmap";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(resultsList.children[0].textContent).toBe(
      "Couldn't load results. Try again.",
    );
  });

  it("ArrowDown on the input focuses the first result link", () => {
    const linkA = new FakeFocusable();
    linkA.className = "global-search-panel__link";
    const linkB = new FakeFocusable();
    linkB.className = "global-search-panel__link";
    const { doc, input, resultsList } = buildPanel([new FakeFocusable()]);
    resultsList.children = [linkA, linkB];

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    input.trigger("keydown", keyEvent("ArrowDown"));

    expect(linkA.focusMock).toHaveBeenCalledTimes(1);
    expect(linkB.focusMock).not.toHaveBeenCalled();
  });

  it("ArrowUp on the input focuses the last result link", () => {
    const linkA = new FakeFocusable();
    linkA.className = "global-search-panel__link";
    const linkB = new FakeFocusable();
    linkB.className = "global-search-panel__link";
    const { doc, input, resultsList } = buildPanel([new FakeFocusable()]);
    resultsList.children = [linkA, linkB];

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    input.trigger("keydown", keyEvent("ArrowUp"));

    expect(linkB.focusMock).toHaveBeenCalledTimes(1);
  });

  it("does nothing (no crash) when Arrow keys are pressed with zero results", () => {
    const { doc, input } = buildPanel([new FakeFocusable()]);

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    expect(() => {
      input.trigger("keydown", keyEvent("ArrowDown"));
      input.trigger("keydown", keyEvent("ArrowUp"));
    }).not.toThrow();
  });

  it("Escape on the input closes the dialog", () => {
    const { dialog, doc, input } = buildPanel([new FakeFocusable()]);

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    input.trigger("keydown", keyEvent("Escape"));

    expect(dialog.closeMock).toHaveBeenCalledTimes(1);
  });

  it("closing the dialog clears the query, results, and returns focus to the trigger", () => {
    const trigger = new FakeFocusable();
    const { dialog, doc, input, resultsList } = buildPanel([trigger]);
    resultsList.children = [new FakeFocusable()];

    initGlobalSearchPanel(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    trigger.click();
    input.value = "roadmap";
    dialog.close();

    expect(input.value).toBe("");
    expect(resultsList.children).toEqual([]);
    expect(trigger.focusMock).toHaveBeenCalledTimes(1);
  });
});
