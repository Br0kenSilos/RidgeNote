import { describe, expect, it } from "vitest";

import {
  initTagErrorDismissDocument,
  type TagErrorDismissDocumentLike,
} from "./tag-error-dismiss";

class FakeInput {
  dataset: { tagErrorDismissInitialized?: string } = {};
  attributes: Record<string, string> = {
    "aria-invalid": "true",
    "aria-describedby": "note-tag-error",
  };
  private listeners: Array<() => void> = [];

  addEventListener(type: "input", listener: () => void): void {
    if (type === "input") {
      this.listeners.push(listener);
    }
  }

  removeAttribute(name: string): void {
    delete this.attributes[name];
  }

  fireInput(): void {
    this.listeners.forEach((listener) => listener());
  }
}

class FakeError {
  removed = false;

  remove(): void {
    this.removed = true;
  }
}

function buildDoc(options: {
  input: FakeInput | null;
  error: FakeError | null;
}): TagErrorDismissDocumentLike {
  return {
    querySelector: <T>(selector: string): T | null => {
      if (selector === "#note-tag-name")
        return options.input as unknown as T | null;
      if (selector === ".note-tags__error")
        return options.error as unknown as T | null;
      return null;
    },
  };
}

describe("initTagErrorDismissDocument", () => {
  it("returns false and does nothing when the tag-name input is absent", () => {
    const doc = buildDoc({ input: null, error: null });

    expect(initTagErrorDismissDocument(doc)).toBe(false);
  });

  it("wires the input once and reports success", () => {
    const input = new FakeInput();
    const doc = buildDoc({ input, error: null });

    expect(initTagErrorDismissDocument(doc)).toBe(true);
    expect(input.dataset.tagErrorDismissInitialized).toBe("true");
  });

  it("does not wire the same input twice", () => {
    const input = new FakeInput();
    const doc = buildDoc({ input, error: null });

    initTagErrorDismissDocument(doc);
    expect(initTagErrorDismissDocument(doc)).toBe(false);
  });

  it("removes the rendered error and clears aria-invalid/aria-describedby on the first edit", () => {
    const input = new FakeInput();
    const error = new FakeError();
    const doc = buildDoc({ input, error });

    initTagErrorDismissDocument(doc);
    input.fireInput();

    expect(error.removed).toBe(true);
    expect(input.attributes["aria-invalid"]).toBeUndefined();
    expect(input.attributes["aria-describedby"]).toBeUndefined();
  });

  it("does not throw on an edit when no error is currently rendered", () => {
    const input = new FakeInput();
    const doc = buildDoc({ input, error: null });

    initTagErrorDismissDocument(doc);

    expect(() => input.fireInput()).not.toThrow();
  });

  it("re-queries the error on every edit, so a second edit after the error is already gone is a harmless no-op", () => {
    const input = new FakeInput();
    const error = new FakeError();
    const doc = buildDoc({ input, error });

    initTagErrorDismissDocument(doc);
    input.fireInput();
    expect(error.removed).toBe(true);

    expect(() => input.fireInput()).not.toThrow();
  });
});
