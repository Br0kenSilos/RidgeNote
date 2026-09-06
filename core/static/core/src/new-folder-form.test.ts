import { describe, expect, it, vi } from "vitest";

import { initNewFolderFormsDocument } from "./new-folder-form";

class FakeInput {
  value = "";
  attributes: Record<string, string> = {};
  focusCalls = 0;
  selectCalls = 0;

  focus(): void {
    this.focusCalls += 1;
  }

  select(): void {
    this.selectCalls += 1;
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

class FakeErrorEl {
  textContent = "";
  hidden = true;
}

class FakeForm {
  dataset: Record<string, string> = {};
  action = "/notes/1/folders/new/";
  submitCalls = 0;
  private listeners: Record<string, Array<(event: unknown) => void>> = {};
  private readonly input: FakeInput | null;
  private readonly errorEl: FakeErrorEl | null;

  constructor(
    options: { input?: FakeInput | null; errorEl?: FakeErrorEl | null } = {},
  ) {
    this.input = options.input ?? new FakeInput();
    this.errorEl = options.errorEl ?? new FakeErrorEl();
  }

  querySelector(selector: string): unknown {
    if (selector === 'input[name="name"]') return this.input;
    if (selector === ".tree-nav__popover-field-error") return this.errorEl;
    return null;
  }

  addEventListener(type: string, listener: (event: unknown) => void): void {
    this.listeners[type] = this.listeners[type] ?? [];
    this.listeners[type].push(listener);
  }

  dispatch(type: string, event: unknown): void {
    this.listeners[type]?.forEach((listener) => listener(event));
  }

  submit(): void {
    this.submitCalls += 1;
  }

  getInput(): FakeInput | null {
    return this.input;
  }

  getErrorEl(): FakeErrorEl | null {
    return this.errorEl;
  }
}

function buildDoc(forms: FakeForm[]) {
  return {
    querySelectorAll: (selector: string) =>
      selector === "[data-new-folder-form]" ? forms : [],
  } as unknown as Document;
}

function fakeEvent() {
  return { preventDefault: vi.fn() };
}

function jsonResponse(data: unknown) {
  return Promise.resolve({ json: () => Promise.resolve(data) });
}

/** Flushes the fetch -> response.json() -> handler microtask chain. */
async function flushPromises(): Promise<void> {
  for (let i = 0; i < 4; i += 1) {
    await Promise.resolve();
  }
}

describe("initNewFolderFormsDocument", () => {
  it("returns false when there are no New Folder forms", () => {
    const doc = buildDoc([]);
    expect(
      initNewFolderFormsDocument(doc, {
        fetch: vi.fn(),
        location: { assign: vi.fn() },
      }),
    ).toBe(false);
  });

  it("submits via fetch with the Accept header and prevents the default navigation", async () => {
    const form = new FakeForm();
    const doc = buildDoc([form]);
    const fetchMock = vi
      .fn()
      .mockReturnValue(jsonResponse({ ok: true, redirect_url: "/notes/1/" }));
    const assign = vi.fn();

    initNewFolderFormsDocument(doc, { fetch: fetchMock, location: { assign } });
    const event = fakeEvent();
    form.dispatch("submit", event);
    await flushPromises();

    expect(event.preventDefault).toHaveBeenCalled();
    expect(fetchMock).toHaveBeenCalledWith(
      "/notes/1/folders/new/",
      expect.objectContaining({
        method: "POST",
        headers: { Accept: "application/json" },
      }),
    );
  });

  it("navigates to the redirect target on a successful creation", async () => {
    const form = new FakeForm();
    const doc = buildDoc([form]);
    const assign = vi.fn();
    const fetchMock = vi
      .fn()
      .mockReturnValue(jsonResponse({ ok: true, redirect_url: "/notes/1/" }));

    initNewFolderFormsDocument(doc, { fetch: fetchMock, location: { assign } });
    form.dispatch("submit", fakeEvent());
    await flushPromises();

    expect(assign).toHaveBeenCalledWith("/notes/1/");
  });

  it("shows the field error, sets aria-invalid, and focuses the input on failure, without navigating", async () => {
    const form = new FakeForm();
    const doc = buildDoc([form]);
    const assign = vi.fn();
    const fetchMock = vi.fn().mockReturnValue(
      jsonResponse({
        ok: false,
        error: "A folder with that name already exists.",
        name: "Projects",
      }),
    );

    initNewFolderFormsDocument(doc, { fetch: fetchMock, location: { assign } });
    form.dispatch("submit", fakeEvent());
    await flushPromises();

    expect(assign).not.toHaveBeenCalled();
    expect(form.getErrorEl()?.textContent).toBe(
      "A folder with that name already exists.",
    );
    expect(form.getErrorEl()?.hidden).toBe(false);
    expect(form.getInput()?.getAttribute("aria-invalid")).toBe("true");
    expect(form.getInput()?.focusCalls).toBe(1);
    expect(form.getInput()?.selectCalls).toBe(1);
  });

  it("clears the previous error before each new submission so repeated failures don't stack stale text", async () => {
    const form = new FakeForm();
    const doc = buildDoc([form]);
    const fetchMock = vi
      .fn()
      .mockReturnValueOnce(
        jsonResponse({ ok: false, error: "Folder name is required." }),
      )
      .mockReturnValueOnce(
        jsonResponse({
          ok: false,
          error: "A folder with that name already exists.",
        }),
      );

    initNewFolderFormsDocument(doc, {
      fetch: fetchMock,
      location: { assign: vi.fn() },
    });

    form.dispatch("submit", fakeEvent());
    await flushPromises();
    expect(form.getErrorEl()?.textContent).toBe("Folder name is required.");

    form.dispatch("submit", fakeEvent());
    await flushPromises();
    expect(form.getErrorEl()?.textContent).toBe(
      "A folder with that name already exists.",
    );
    expect(form.getErrorEl()?.hidden).toBe(false);
  });

  it("falls back to a real form submission if the fetch request fails outright", async () => {
    const form = new FakeForm();
    const doc = buildDoc([form]);
    const fetchMock = vi
      .fn()
      .mockReturnValue(Promise.reject(new Error("network down")));

    initNewFolderFormsDocument(doc, {
      fetch: fetchMock,
      location: { assign: vi.fn() },
    });
    form.dispatch("submit", fakeEvent());
    await flushPromises();
    await Promise.resolve();

    expect(form.submitCalls).toBe(1);
  });

  it("does not double-bind listeners on a second init call", async () => {
    const form = new FakeForm();
    const doc = buildDoc([form]);
    const assign = vi.fn();
    const fetchMock = vi
      .fn()
      .mockReturnValue(jsonResponse({ ok: true, redirect_url: "/notes/1/" }));

    initNewFolderFormsDocument(doc, { fetch: fetchMock, location: { assign } });
    initNewFolderFormsDocument(doc, { fetch: fetchMock, location: { assign } });

    form.dispatch("submit", fakeEvent());
    await flushPromises();

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
