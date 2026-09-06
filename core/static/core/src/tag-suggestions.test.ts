// @vitest-environment jsdom
import { describe, expect, it, vi } from "vitest";

import {
  buildTagSuggestionsRequestUrl,
  clampOptionIndex,
  initTagSuggestionsDocument,
  type TagSuggestionsFetchResponseLike,
  type TagSuggestionsResponse,
  type TagSuggestionsSchedulerLike,
} from "./tag-suggestions";
import { initTagNameUppercaseDocument } from "./tag-name-uppercase";

describe("buildTagSuggestionsRequestUrl", () => {
  it("includes q only -- no color parameter", () => {
    const url = buildTagSuggestionsRequestUrl(
      "/notes/1/tags/suggestions/",
      "red",
    );
    expect(url).toBe("/notes/1/tags/suggestions/?q=red");
  });

  it("URL-encodes special characters", () => {
    const url = buildTagSuggestionsRequestUrl(
      "/notes/1/tags/suggestions/",
      "a&b",
    );
    expect(url).toBe("/notes/1/tags/suggestions/?q=a%26b");
  });
});

describe("clampOptionIndex", () => {
  it("returns -1 for an empty list", () => {
    expect(clampOptionIndex(0, 0)).toBe(-1);
  });

  it("clamps below zero to 0", () => {
    expect(clampOptionIndex(-1, 3)).toBe(0);
  });

  it("clamps past the end to the last index", () => {
    expect(clampOptionIndex(9, 3)).toBe(2);
  });
});

// -- DOM-wiring tests (jsdom) ------------------------------------------
//
// This component's real-DOM ARIA/keyboard/pointer interactions are rich
// enough that hand-written duck-typed fakes (as used by quick-switch.ts's
// own tests) would be more brittle than the real thing -- this module
// instead follows the jsdom-backed precedent already established by
// note-editor-init.test.ts.

class FakeScheduler implements TagSuggestionsSchedulerLike {
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

function jsonResponse(
  payload: TagSuggestionsResponse,
): TagSuggestionsFetchResponseLike {
  return { ok: true, json: () => Promise.resolve(payload) };
}

function buildPage(): void {
  document.body.innerHTML = `
    <form data-tag-suggestions-url="/notes/1/tags/suggestions/" method="post" action="/notes/1/tags/assign/">
      <div class="note-tags__name-field">
        <label for="note-tag-name" class="visually-hidden">Tag name</label>
        <input type="text" id="note-tag-name" name="name" list="note-tag-options"
               data-tag-name-input>
        <datalist id="note-tag-options"><option value="RED"></option></datalist>
        <ul id="note-tag-suggestions-listbox" role="listbox" data-tag-suggestions-panel hidden></ul>
      </div>
      <select id="note-tag-color" name="color">
        <option value="">Default color</option>
        <option value="blue">Blue</option>
      </select>
      <p data-tag-name-length-warning hidden></p>
      <button type="submit">Add tag</button>
    </form>
  `;
}

function getEls() {
  const form = document.querySelector("form") as HTMLFormElement;
  const input = document.querySelector<HTMLInputElement>(
    "[data-tag-name-input]",
  )!;
  const panel = document.querySelector<HTMLUListElement>(
    "[data-tag-suggestions-panel]",
  )!;
  const colorSelect =
    document.querySelector<HTMLSelectElement>("#note-tag-color")!;
  return { form, input, panel, colorSelect };
}

describe("initTagSuggestionsDocument", () => {
  it("neutralizes the native datalist relationship on init", () => {
    buildPage();
    initTagSuggestionsDocument(document, { scheduler: new FakeScheduler() });
    const { input } = getEls();
    expect(input.getAttribute("list")).toBeNull();
  });

  it("wires combobox ARIA attributes on init", () => {
    buildPage();
    initTagSuggestionsDocument(document, { scheduler: new FakeScheduler() });
    const { input, panel } = getEls();
    expect(input.getAttribute("role")).toBe("combobox");
    expect(input.getAttribute("aria-autocomplete")).toBe("list");
    expect(input.getAttribute("aria-expanded")).toBe("false");
    expect(input.getAttribute("aria-controls")).toBe(panel.id);
  });

  it("initializes without requiring a color select in the form", () => {
    buildPage();
    document.querySelector("#note-tag-color")?.remove();
    const initialized = initTagSuggestionsDocument(document, {
      scheduler: new FakeScheduler(),
    });
    expect(initialized).toBe(true);
  });

  it("does not open a panel on focus alone, with no request sent", () => {
    buildPage();
    const fetchFn = vi.fn();
    initTagSuggestionsDocument(document, {
      fetchFn,
      scheduler: new FakeScheduler(),
    });
    const { input, panel } = getEls();
    input.dispatchEvent(new Event("focus"));
    expect(panel.hidden).toBe(true);
    expect(fetchFn).not.toHaveBeenCalled();
  });

  it("opens the panel with existing matches after typing one character, debounced", async () => {
    buildPage();
    const fetchFn = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        existing: [{ id: 1, name: "RED", color: "blue" }],
        at_tag_limit: false,
      }),
    );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input, panel } = getEls();

    input.value = "r";
    input.dispatchEvent(new Event("input"));
    expect(fetchFn).not.toHaveBeenCalled();
    expect(scheduler.pendingCount()).toBe(1);

    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(fetchFn).toHaveBeenCalledTimes(1);
    expect(fetchFn.mock.calls[0]![0]).toBe("/notes/1/tags/suggestions/?q=r");
    expect(panel.hidden).toBe(false);
  });

  it("closes the panel when the query stops matching anything, leaving the typed text untouched", async () => {
    buildPage();
    const fetchFn = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ ok: true, existing: [], at_tag_limit: false }),
      );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input, panel } = getEls();

    input.value = "zzz";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(panel.hidden).toBe(true);
    expect(panel.querySelectorAll('[role="option"]').length).toBe(0);
    expect(input.value).toBe("zzz");
  });

  it("leaves the ordinary color selector and Add Tag button usable after the panel closes", async () => {
    buildPage();
    const fetchFn = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ ok: true, existing: [], at_tag_limit: false }),
      );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input, colorSelect, form } = getEls();

    input.value = "greenb";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(colorSelect.disabled).toBe(false);
    expect(form.querySelector("button[type=submit]")).not.toBeNull();
  });

  it("debounces rapid typing into a single request", () => {
    buildPage();
    const fetchFn = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ ok: true, existing: [], at_tag_limit: false }),
      );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input } = getEls();

    input.value = "r";
    input.dispatchEvent(new Event("input"));
    input.value = "re";
    input.dispatchEvent(new Event("input"));
    input.value = "red";
    input.dispatchEvent(new Event("input"));

    expect(scheduler.pendingCount()).toBe(1);
    scheduler.runAll();
    expect(fetchFn).toHaveBeenCalledTimes(1);
  });

  it("closes the panel when the input becomes empty, with no request", () => {
    buildPage();
    const fetchFn = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ ok: true, existing: [], at_tag_limit: false }),
      );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input, panel } = getEls();

    input.value = "r";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();

    input.value = "";
    input.dispatchEvent(new Event("input"));
    expect(panel.hidden).toBe(true);
    expect(scheduler.pendingCount()).toBe(0);
  });

  it("discards a stale response that resolves after a newer request", async () => {
    buildPage();
    let resolveFirst!: (value: TagSuggestionsFetchResponseLike) => void;
    let resolveSecond!: (value: TagSuggestionsFetchResponseLike) => void;
    const fetchFn = vi
      .fn()
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveFirst = resolve)),
      )
      .mockImplementationOnce(
        () => new Promise((resolve) => (resolveSecond = resolve)),
      );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input, panel } = getEls();

    input.value = "r";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();

    input.value = "re";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();

    expect(fetchFn).toHaveBeenCalledTimes(2);

    resolveSecond(
      jsonResponse({
        ok: true,
        existing: [{ id: 2, name: "RECENT", color: "green" }],
        at_tag_limit: false,
      }),
    );
    await Promise.resolve();
    await Promise.resolve();
    expect(panel.querySelectorAll('[role="option"]').length).toBe(1);

    resolveFirst(
      jsonResponse({
        ok: true,
        existing: [{ id: 1, name: "OLD", color: "amber" }],
        at_tag_limit: false,
      }),
    );
    await Promise.resolve();
    await Promise.resolve();
    const labels = Array.from(
      panel.querySelectorAll(".tag-suggestion__pill"),
    ).map((el) => el.textContent);
    expect(labels).toEqual(["RECENT"]);
  });

  it("renders an existing tag's real stored color, not a color implied by its name", async () => {
    buildPage();
    const fetchFn = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        existing: [{ id: 1, name: "RED", color: "blue" }],
        at_tag_limit: false,
      }),
    );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input, panel } = getEls();

    input.value = "red";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    const pill = panel.querySelector(".tag-suggestion__pill")!;
    expect(pill.getAttribute("data-tag-color")).toBe("blue");
    expect(pill.textContent).toBe("RED");
  });

  it("renders no remove control in an existing-tag suggestion row", async () => {
    buildPage();
    const fetchFn = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        existing: [{ id: 1, name: "RED", color: "blue" }],
        at_tag_limit: false,
      }),
    );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input, panel } = getEls();

    input.value = "red";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(panel.querySelector("button")).toBeNull();
    expect(panel.textContent).not.toContain("×");
  });

  it("never renders a create row, even when a match is present", async () => {
    buildPage();
    const fetchFn = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        existing: [{ id: 1, name: "RED", color: "blue" }],
        at_tag_limit: false,
      }),
    );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input, panel } = getEls();

    input.value = "red";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(panel.textContent).not.toContain("Create");
    expect(panel.querySelectorAll('[role="option"]').length).toBe(1);
  });

  it("closes the panel entirely at the 20-tag limit, since the server omits all suggestions", async () => {
    buildPage();
    const fetchFn = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ ok: true, existing: [], at_tag_limit: true }),
      );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input, panel } = getEls();

    input.value = "r";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    expect(panel.hidden).toBe(true);
  });

  it("moves aria-activedescendant with Arrow Down / Arrow Up", async () => {
    buildPage();
    const fetchFn = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        existing: [
          { id: 1, name: "RED", color: "red" },
          { id: 2, name: "ROSE", color: "rose" },
        ],
        at_tag_limit: false,
      }),
    );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input } = getEls();

    input.value = "r";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown" }));
    const options = document.querySelectorAll('[role="option"]');
    expect(input.getAttribute("aria-activedescendant")).toBe(options[0]!.id);
    expect(options[0]!.getAttribute("aria-selected")).toBe("true");

    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown" }));
    expect(input.getAttribute("aria-activedescendant")).toBe(options[1]!.id);
    expect(options[0]!.getAttribute("aria-selected")).toBe("false");

    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowUp" }));
    expect(input.getAttribute("aria-activedescendant")).toBe(options[0]!.id);
  });

  it("Enter on a highlighted existing row attaches immediately via requestSubmit", async () => {
    buildPage();
    const fetchFn = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        existing: [{ id: 1, name: "RED", color: "blue" }],
        at_tag_limit: false,
      }),
    );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { form, input, panel } = getEls();
    form.requestSubmit = vi.fn();

    input.value = "red";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown" }));
    const enterEvent = new KeyboardEvent("keydown", {
      key: "Enter",
      cancelable: true,
    });
    input.dispatchEvent(enterEvent);

    expect(input.value).toBe("RED");
    expect(form.requestSubmit).toHaveBeenCalledTimes(1);
    expect(enterEvent.defaultPrevented).toBe(true);
    expect(panel.hidden).toBe(true);
  });

  it("Escape closes the panel without clearing the typed text", async () => {
    buildPage();
    const fetchFn = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        existing: [{ id: 1, name: "RED", color: "blue" }],
        at_tag_limit: false,
      }),
    );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input, panel } = getEls();

    input.value = "red";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    input.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    expect(panel.hidden).toBe(true);
    expect(input.value).toBe("red");
  });

  it("Tab closes the panel, moves focus normally, and never attaches or mutates the input", async () => {
    buildPage();
    const fetchFn = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        existing: [{ id: 1, name: "RED", color: "blue" }],
        at_tag_limit: false,
      }),
    );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { form, input, panel } = getEls();
    form.requestSubmit = vi.fn();

    input.value = "re";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    input.dispatchEvent(new KeyboardEvent("keydown", { key: "ArrowDown" }));
    const tabEvent = new KeyboardEvent("keydown", {
      key: "Tab",
      cancelable: true,
    });
    input.dispatchEvent(tabEvent);

    expect(input.value).toBe("re");
    expect(tabEvent.defaultPrevented).toBe(false);
    expect(form.requestSubmit).not.toHaveBeenCalled();
    expect(panel.hidden).toBe(true);
  });

  it("selects an existing row via pointerdown without a competing click handler double-activating", async () => {
    buildPage();
    const fetchFn = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        existing: [{ id: 1, name: "RED", color: "blue" }],
        at_tag_limit: false,
      }),
    );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { form, input, panel } = getEls();
    form.requestSubmit = vi.fn();

    input.value = "red";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();

    const option = panel.querySelector('[role="option"]')!;
    const pointerEvent = new Event("pointerdown", {
      cancelable: true,
      bubbles: true,
    });
    option.dispatchEvent(pointerEvent);
    option.dispatchEvent(new Event("click", { bubbles: true }));

    expect(pointerEvent.defaultPrevented).toBe(true);
    expect(input.value).toBe("RED");
    expect(form.requestSubmit).toHaveBeenCalledTimes(1);
  });

  it("closes the panel when focus genuinely leaves the component", () => {
    buildPage();
    const fetchFn = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ ok: true, existing: [], at_tag_limit: false }),
      );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { form, input, panel } = getEls();

    input.value = "r";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();

    const outside = document.createElement("button");
    document.body.appendChild(outside);
    form.dispatchEvent(
      new FocusEvent("focusout", { relatedTarget: outside, bubbles: true }),
    );
    expect(panel.hidden).toBe(true);
  });

  it("does not close when focus moves within the component (e.g. to the color select)", async () => {
    buildPage();
    const fetchFn = vi.fn().mockResolvedValue(
      jsonResponse({
        ok: true,
        existing: [{ id: 1, name: "RED", color: "blue" }],
        at_tag_limit: false,
      }),
    );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { form, input, panel, colorSelect } = getEls();

    input.value = "red";
    input.dispatchEvent(new Event("input"));
    scheduler.runAll();
    await Promise.resolve();
    await Promise.resolve();
    expect(panel.hidden).toBe(false);

    form.dispatchEvent(
      new FocusEvent("focusout", { relatedTarget: colorSelect, bubbles: true }),
    );
    expect(panel.hidden).not.toBe(true);
  });

  it("coexists cleanly with the live-uppercase module on the same input", () => {
    buildPage();
    initTagNameUppercaseDocument(document);
    const fetchFn = vi
      .fn()
      .mockResolvedValue(
        jsonResponse({ ok: true, existing: [], at_tag_limit: false }),
      );
    const scheduler = new FakeScheduler();
    initTagSuggestionsDocument(document, { fetchFn, scheduler });
    const { input } = getEls();

    input.value = "red";
    input.dispatchEvent(new Event("input"));

    expect(input.value).toBe("RED");
    expect(scheduler.pendingCount()).toBe(1);
  });

  it("only initializes a given form once", () => {
    buildPage();
    const scheduler = new FakeScheduler();
    const first = initTagSuggestionsDocument(document, { scheduler });
    const second = initTagSuggestionsDocument(document, { scheduler });
    expect(first).toBe(true);
    expect(second).toBe(false);
  });
});
