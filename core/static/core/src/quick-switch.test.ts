import { describe, expect, it, vi } from "vitest";

import {
  buildQuickSwitchRequestUrl,
  clampResultIndex,
  initQuickSwitchDocument,
  type QuickSwitchDocumentLike,
  type QuickSwitchElementLike,
  type QuickSwitchFetchResponseLike,
  type QuickSwitchResponse,
  type QuickSwitchSchedulerLike,
} from "./quick-switch";

describe("clampResultIndex", () => {
  it("returns -1 when there are no results", () => {
    expect(clampResultIndex(0, 0)).toBe(-1);
    expect(clampResultIndex(3, 0)).toBe(-1);
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

describe("buildQuickSwitchRequestUrl", () => {
  it("builds a URL with only the query when no exclusion is given", () => {
    const url = buildQuickSwitchRequestUrl("/notes/quick-switch/", "meeting");
    expect(url).toBe("/notes/quick-switch/?q=meeting");
  });

  it("includes the exclude parameter when given", () => {
    const url = buildQuickSwitchRequestUrl(
      "/notes/quick-switch/",
      "meeting",
      "42",
    );
    expect(url).toBe("/notes/quick-switch/?q=meeting&exclude=42");
  });

  it("URL-encodes special characters in the query", () => {
    const url = buildQuickSwitchRequestUrl("/notes/quick-switch/", "a&b");
    expect(url).toBe("/notes/quick-switch/?q=a%26b");
  });
});

// -- DOM-wiring tests ---------------------------------------------------
//
// This project's Vitest suite runs without a real DOM (no jsdom
// dependency), matching the existing precedent in tree-filter.test.ts and
// note-editor.test.ts: production code is typed against the real DOM
// interfaces, and tests supply small hand-written fakes cast with
// `as unknown as <RealType>` at the call boundary.

class FakeFocusable {
  className = "";
  textContent: string | null = null;
  href?: string;
  children: FakeFocusable[] = [];
  focusMock = vi.fn();
  private listeners = new Map<string, Array<(event: unknown) => void>>();

  appendChild(node: QuickSwitchElementLike): void {
    this.children.push(node as FakeFocusable);
  }

  addEventListener(type: string, listener: (event: unknown) => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  trigger(type: string, event: unknown): void {
    this.listeners.get(type)?.forEach((listener) => listener(event));
  }

  focus(): void {
    this.focusMock();
  }
}

class FakeResultsList {
  children: FakeFocusable[] = [];

  get innerHTML(): string {
    return this.children as unknown as string;
  }

  set innerHTML(value: string) {
    this.children = value as unknown as FakeFocusable[];
  }

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

class FakeContainer {
  dataset: Record<string, string> = {};
  open = true;
  private children = new Map<string, unknown>();

  setChild(selector: string, value: unknown): void {
    this.children.set(selector, value);
  }

  querySelector(selector: string): unknown {
    return this.children.get(selector) ?? null;
  }
}

class FakeScheduler implements QuickSwitchSchedulerLike {
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

function fakeDocumentLike(): QuickSwitchDocumentLike {
  return {
    createElement(): QuickSwitchElementLike {
      return new FakeFocusable();
    },
  };
}

function keyEvent(key: string) {
  return { key, preventDefault: vi.fn() };
}

interface BuildOptions {
  excludeNoteId?: string;
  initialLinks?: FakeFocusable[];
}

function buildSwitcher(options: BuildOptions = {}) {
  const summary = new FakeFocusable();
  const container = new FakeContainer();
  container.setChild("summary", summary);
  if (options.excludeNoteId) {
    container.dataset.quickSwitchExcludeId = options.excludeNoteId;
  }

  const input = new FakeInput();
  const resultsList = new FakeResultsList();
  resultsList.children = options.initialLinks ?? [];

  container.setChild("[data-quick-switch-input]", input);
  container.setChild("[data-quick-switch-results]", resultsList);
  container.dataset.quickSwitchUrl = "/notes/quick-switch/";

  const doc = {
    querySelectorAll: (selector: string) =>
      selector === "[data-note-recent-switcher]" ? [container] : [],
  } as unknown as Document;

  return { container, doc, input, resultsList, summary };
}

function makeDefaultLink(title: string, href: string): FakeFocusable {
  const link = new FakeFocusable();
  link.className = "note-recent-switcher__link";
  link.textContent = title;
  link.href = href;
  return link;
}

function resolvedFetch(
  payload: QuickSwitchResponse,
  ok = true,
): (url: string) => Promise<QuickSwitchFetchResponseLike> {
  return () =>
    Promise.resolve({
      json: () => Promise.resolve(payload),
      ok,
    });
}

describe("initQuickSwitchDocument", () => {
  it("initializes a container with the required parts and marks it initialized", () => {
    const { container, doc } = buildSwitcher();
    const scheduler = new FakeScheduler();

    const result = initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler,
    });

    expect(result).toBe(true);
    expect(container.dataset.quickSwitchInitialized).toBe("true");
  });

  it("does not initialize a container missing the filter input", () => {
    const container = new FakeContainer();
    container.dataset.quickSwitchUrl = "/notes/quick-switch/";
    container.setChild("[data-quick-switch-results]", new FakeResultsList());
    const doc = {
      querySelectorAll: () => [container],
    } as unknown as Document;

    const result = initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    expect(result).toBe(false);
  });

  it("does not re-initialize an already-initialized container", () => {
    const { doc } = buildSwitcher();
    const scheduler = new FakeScheduler();
    const options = {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler,
    };

    expect(initQuickSwitchDocument(doc, options)).toBe(true);
    expect(initQuickSwitchDocument(doc, options)).toBe(false);
  });
});

describe("quick-switch typed filtering behavior", () => {
  it("does not fetch immediately on input; waits for the debounce delay", () => {
    const { doc, input } = buildSwitcher();
    const scheduler = new FakeScheduler();
    const fetchFn = vi.fn(resolvedFetch({ ok: true, results: [] }));

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "meeting";
    input.trigger("input");

    expect(fetchFn).not.toHaveBeenCalled();
    expect(scheduler.pendingCount()).toBe(1);
  });

  it("queries the backend after the debounce elapses, with the query and exclude id", async () => {
    const { doc, input } = buildSwitcher({ excludeNoteId: "7" });
    const scheduler = new FakeScheduler();
    const fetchFn = vi.fn(resolvedFetch({ ok: true, results: [] }));

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "meeting";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(fetchFn).toHaveBeenCalledWith(
      "/notes/quick-switch/?q=meeting&exclude=7",
    );
  });

  it("restores the default recents without fetching when the query is cleared", () => {
    const defaultLink = makeDefaultLink("Older note", "/notes/1/");
    const { doc, input, resultsList } = buildSwitcher({
      initialLinks: [defaultLink],
    });
    const scheduler = new FakeScheduler();
    const fetchFn = vi.fn(resolvedFetch({ ok: true, results: [] }));

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "meeting";
    input.trigger("input");
    input.value = "";
    input.trigger("input");

    expect(scheduler.pendingCount()).toBe(0);
    expect(fetchFn).not.toHaveBeenCalled();
    expect(resultsList.children).toEqual([defaultLink]);
  });

  it("renders matching results with title, folder label, and url", async () => {
    const { doc, input, resultsList } = buildSwitcher();
    const scheduler = new FakeScheduler();
    const fetchFn = resolvedFetch({
      ok: true,
      results: [
        { folder_label: "Work", id: 5, title: "Weekly sync", url: "/notes/5/" },
      ],
    });

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "weekly";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(resultsList.children).toHaveLength(1);
    const item = resultsList.children[0];
    expect(item.className).toBe("note-recent-switcher__item");
    const link = item.children[0];
    expect(link.className).toBe("note-recent-switcher__link");
    expect(link.href).toBe("/notes/5/");
    expect(link.textContent).toBe("Weekly sync");
    const folderLabel = link.children[0];
    expect(folderLabel.textContent).toBe("Work");
  });

  it("shows an empty-results message when nothing matches", async () => {
    const { doc, input, resultsList } = buildSwitcher();
    const scheduler = new FakeScheduler();

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler,
    });

    input.value = "zzz-nonexistent";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(resultsList.children).toHaveLength(1);
    expect(resultsList.children[0].className).toBe(
      "note-recent-switcher__empty",
    );
    expect(resultsList.children[0].textContent).toBe("No matching notes.");
  });

  it("shows a compact error message when the response is not ok", async () => {
    const { doc, input, resultsList } = buildSwitcher();
    const scheduler = new FakeScheduler();

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: false }, false),
      scheduler,
    });

    input.value = "meeting";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(resultsList.children[0].textContent).toBe(
      "Couldn't load results. Try again.",
    );
  });

  it("shows a compact error message when the fetch rejects", async () => {
    const { doc, input, resultsList } = buildSwitcher();
    const scheduler = new FakeScheduler();

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: () => Promise.reject(new Error("network down")),
      scheduler,
    });

    input.value = "meeting";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(resultsList.children[0].textContent).toBe(
      "Couldn't load results. Try again.",
    );
  });

  it("ignores a stale response when a newer query has since been issued", async () => {
    const { doc, input, resultsList } = buildSwitcher();
    const scheduler = new FakeScheduler();
    let resolveFirst!: (response: QuickSwitchFetchResponseLike) => void;
    const firstPromise = new Promise<QuickSwitchFetchResponseLike>((res) => {
      resolveFirst = res;
    });
    const fetchFn = vi
      .fn()
      .mockImplementationOnce(() => firstPromise)
      .mockImplementationOnce(() =>
        Promise.resolve({
          json: () =>
            Promise.resolve({
              ok: true,
              results: [
                {
                  folder_label: "Unfiled",
                  id: 9,
                  title: "Second",
                  url: "/notes/9/",
                },
              ],
            }),
          ok: true,
        }),
      );

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn,
      scheduler,
    });

    input.value = "first";
    input.trigger("input");
    scheduler.runAll();

    input.value = "second";
    input.trigger("input");
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    // The stale first request resolves after the second has already rendered.
    resolveFirst({
      json: () =>
        Promise.resolve({
          ok: true,
          results: [
            {
              folder_label: "Unfiled",
              id: 1,
              title: "First",
              url: "/notes/1/",
            },
          ],
        }),
      ok: true,
    });
    await Promise.resolve();
    await Promise.resolve();
    await Promise.resolve();

    expect(resultsList.children).toHaveLength(1);
    expect(resultsList.children[0].children[0].textContent).toBe("Second");
  });
});

describe("quick-switch keyboard navigation", () => {
  it("ArrowDown on the input focuses the first result link", () => {
    const linkA = makeDefaultLink("A", "/notes/1/");
    const linkB = makeDefaultLink("B", "/notes/2/");
    const { doc, input } = buildSwitcher({ initialLinks: [linkA, linkB] });

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    input.trigger("keydown", keyEvent("ArrowDown"));

    expect(linkA.focusMock).toHaveBeenCalledTimes(1);
    expect(linkB.focusMock).not.toHaveBeenCalled();
  });

  it("ArrowUp on the input focuses the last result link", () => {
    const linkA = makeDefaultLink("A", "/notes/1/");
    const linkB = makeDefaultLink("B", "/notes/2/");
    const { doc, input } = buildSwitcher({ initialLinks: [linkA, linkB] });

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    input.trigger("keydown", keyEvent("ArrowUp"));

    expect(linkB.focusMock).toHaveBeenCalledTimes(1);
    expect(linkA.focusMock).not.toHaveBeenCalled();
  });

  it("does nothing (no crash) when Arrow keys are pressed with zero results", () => {
    const { doc, input } = buildSwitcher({ initialLinks: [] });

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    expect(() => {
      input.trigger("keydown", keyEvent("ArrowDown"));
      input.trigger("keydown", keyEvent("ArrowUp"));
    }).not.toThrow();
  });

  it("ArrowDown moves from one result link to the next", () => {
    const linkA = makeDefaultLink("A", "/notes/1/");
    const linkB = makeDefaultLink("B", "/notes/2/");
    const { doc } = buildSwitcher({ initialLinks: [linkA, linkB] });

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    linkA.trigger("keydown", keyEvent("ArrowDown"));

    expect(linkB.focusMock).toHaveBeenCalledTimes(1);
  });

  it("ArrowDown on the last result link stays on the last link (clamped)", () => {
    const linkA = makeDefaultLink("A", "/notes/1/");
    const linkB = makeDefaultLink("B", "/notes/2/");
    const { doc } = buildSwitcher({ initialLinks: [linkA, linkB] });

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    linkB.trigger("keydown", keyEvent("ArrowDown"));

    expect(linkB.focusMock).toHaveBeenCalledTimes(1);
  });

  it("ArrowUp on the first result link returns focus to the input", () => {
    const linkA = makeDefaultLink("A", "/notes/1/");
    const linkB = makeDefaultLink("B", "/notes/2/");
    const { doc, input } = buildSwitcher({ initialLinks: [linkA, linkB] });

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    linkA.trigger("keydown", keyEvent("ArrowUp"));

    expect(input.focusMock).toHaveBeenCalledTimes(1);
  });

  it("ArrowUp moves from a later result link to the previous one", () => {
    const linkA = makeDefaultLink("A", "/notes/1/");
    const linkB = makeDefaultLink("B", "/notes/2/");
    const { doc } = buildSwitcher({ initialLinks: [linkA, linkB] });

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    linkB.trigger("keydown", keyEvent("ArrowUp"));

    expect(linkA.focusMock).toHaveBeenCalledTimes(1);
  });

  it("Escape on the input clears the query, restores defaults, closes the panel, and returns focus to the trigger", () => {
    const defaultLink = makeDefaultLink("Default", "/notes/1/");
    const { container, doc, input, summary } = buildSwitcher({
      initialLinks: [defaultLink],
    });

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    input.value = "meeting";
    input.trigger("input");
    input.trigger("keydown", keyEvent("Escape"));

    expect(input.value).toBe("");
    expect(container.open).toBe(false);
    expect(summary.focusMock).toHaveBeenCalledTimes(1);
  });

  it("Escape on a result link also closes the panel and returns focus to the trigger", () => {
    const linkA = makeDefaultLink("A", "/notes/1/");
    const { container, doc, summary } = buildSwitcher({
      initialLinks: [linkA],
    });

    initQuickSwitchDocument(doc, {
      documentLike: fakeDocumentLike(),
      fetchFn: resolvedFetch({ ok: true, results: [] }),
      scheduler: new FakeScheduler(),
    });

    linkA.trigger("keydown", keyEvent("Escape"));

    expect(container.open).toBe(false);
    expect(summary.focusMock).toHaveBeenCalledTimes(1);
  });
});
