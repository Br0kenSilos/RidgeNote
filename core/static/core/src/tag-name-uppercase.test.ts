import { describe, expect, it } from "vitest";

import {
  initTagNameUppercaseDocument,
  normalizedTagNameLength,
  uppercaseTagNameValue,
  type TagNameUppercaseDocumentLike,
} from "./tag-name-uppercase";

class FakeInput {
  value = "";
  selectionStart: number | null = null;
  selectionEnd: number | null = null;
  dataset: { tagNameUppercaseInitialized?: string } = {};
  attributes: Record<string, string> = {};
  private listeners: Array<() => void> = [];

  addEventListener(type: "input", listener: () => void): void {
    if (type === "input") {
      this.listeners.push(listener);
    }
  }

  setAttribute(name: string, value: string): void {
    this.attributes[name] = value;
  }

  removeAttribute(name: string): void {
    delete this.attributes[name];
  }

  fireInput(): void {
    this.listeners.forEach((listener) => listener());
  }
}

class FakeLengthWarning {
  hidden = true;
  textContent = "";
}

function buildDoc(
  input: FakeInput | null,
  lengthWarning: FakeLengthWarning | null = null,
): TagNameUppercaseDocumentLike {
  return {
    querySelector: <T>(selector: string): T | null => {
      if (selector === "[data-tag-name-input]")
        return input as unknown as T | null;
      if (selector === "[data-tag-name-length-warning]")
        return lengthWarning as unknown as T | null;
      return null;
    },
  };
}

describe("uppercaseTagNameValue", () => {
  it("uppercases lowercase input", () => {
    expect(uppercaseTagNameValue("docker")).toBe("DOCKER");
  });

  it("uppercases mixed-case input", () => {
    expect(uppercaseTagNameValue("DoCkEr")).toBe("DOCKER");
  });

  it("leaves already-uppercase input unchanged", () => {
    expect(uppercaseTagNameValue("DOCKER")).toBe("DOCKER");
  });

  it("preserves punctuation and spaces", () => {
    expect(uppercaseTagNameValue("o365/admin backup-plan")).toBe(
      "O365/ADMIN BACKUP-PLAN",
    );
  });
});

describe("normalizedTagNameLength", () => {
  it("counts ordinary characters directly", () => {
    expect(normalizedTagNameLength("DOCKER")).toBe(6);
  });

  it("does not count trimmable outer whitespace", () => {
    expect(normalizedTagNameLength("   DOCKER   ")).toBe(6);
  });

  it("counts repeated internal whitespace as a single collapsed space", () => {
    expect(normalizedTagNameLength("HOME     LAB")).toBe(8); // "HOME LAB"
  });
});

describe("initTagNameUppercaseDocument", () => {
  it("returns false and does nothing when the input is absent", () => {
    expect(initTagNameUppercaseDocument(buildDoc(null))).toBe(false);
  });

  it("wires the input once and reports success", () => {
    const input = new FakeInput();
    expect(initTagNameUppercaseDocument(buildDoc(input))).toBe(true);
    expect(input.dataset.tagNameUppercaseInitialized).toBe("true");
  });

  it("does not wire the same input twice", () => {
    const input = new FakeInput();
    const doc = buildDoc(input);
    initTagNameUppercaseDocument(doc);
    expect(initTagNameUppercaseDocument(doc)).toBe(false);
  });

  it("uppercases the value live as the user types", () => {
    const input = new FakeInput();
    initTagNameUppercaseDocument(buildDoc(input));

    input.value = "d";
    input.fireInput();
    expect(input.value).toBe("D");

    input.value = "do";
    input.fireInput();
    expect(input.value).toBe("DO");
  });

  it("uppercases mixed-case typed input", () => {
    const input = new FakeInput();
    initTagNameUppercaseDocument(buildDoc(input));

    input.value = "DoCkEr";
    input.fireInput();

    expect(input.value).toBe("DOCKER");
  });

  it("uppercases a pasted value the same way typed input is handled (paste already updates .value before 'input' fires)", () => {
    const input = new FakeInput();
    initTagNameUppercaseDocument(buildDoc(input));

    input.value = "home lab";
    input.fireInput();

    expect(input.value).toBe("HOME LAB");
  });

  it("does not reassign .value when already fully uppercase, avoiding an unnecessary cursor reset", () => {
    const input = new FakeInput();
    initTagNameUppercaseDocument(buildDoc(input));
    input.value = "DOCKER";
    input.selectionStart = 3;
    input.selectionEnd = 3;

    input.fireInput();

    expect(input.selectionStart).toBe(3);
    expect(input.selectionEnd).toBe(3);
  });

  it("restores cursor position after a same-length uppercase transform", () => {
    const input = new FakeInput();
    initTagNameUppercaseDocument(buildDoc(input));
    input.value = "docker";
    input.selectionStart = 3;
    input.selectionEnd = 3;

    input.fireInput();

    expect(input.value).toBe("DOCKER");
    expect(input.selectionStart).toBe(3);
    expect(input.selectionEnd).toBe(3);
  });

  it("never submits or navigates on its own -- only mutates the input's own value/selection", () => {
    const input = new FakeInput();
    initTagNameUppercaseDocument(buildDoc(input));

    input.value = "docker";
    expect(() => input.fireInput()).not.toThrow();
  });

  it("does nothing with the length warning when no such element exists in the document", () => {
    const input = new FakeInput();
    initTagNameUppercaseDocument(buildDoc(input, null));

    input.value = "A".repeat(41);
    expect(() => input.fireInput()).not.toThrow();
  });

  it("shows the exact server message immediately once the normalized value exceeds 40 characters", () => {
    const input = new FakeInput();
    const warning = new FakeLengthWarning();
    initTagNameUppercaseDocument(buildDoc(input, warning));

    input.value = "a".repeat(41);
    input.fireInput();

    expect(warning.hidden).toBe(false);
    expect(warning.textContent).toBe(
      "Tag name must be 40 characters or fewer.",
    );
    expect(input.attributes["aria-invalid"]).toBe("true");
    expect(input.attributes["aria-describedby"]).toBe(
      "note-tag-length-warning",
    );
  });

  it("keeps the warning hidden at exactly 40 characters", () => {
    const input = new FakeInput();
    const warning = new FakeLengthWarning();
    initTagNameUppercaseDocument(buildDoc(input, warning));

    input.value = "a".repeat(40);
    input.fireInput();

    expect(warning.hidden).toBe(true);
    expect(warning.textContent).toBe("");
    expect(input.attributes["aria-invalid"]).toBeUndefined();
    expect(input.attributes["aria-describedby"]).toBeUndefined();
  });

  it("bases the length check on the normalized (whitespace-collapsed) value, not the raw one", () => {
    // 45 raw characters, but only 9 once repeated internal whitespace
    // collapses -- must not be flagged as over-length.
    const input = new FakeInput();
    const warning = new FakeLengthWarning();
    initTagNameUppercaseDocument(buildDoc(input, warning));

    input.value = "a" + " ".repeat(40) + "bcdefgh";
    input.fireInput();

    expect(warning.hidden).toBe(true);
  });

  it("hides the warning again once the value is shortened back under the limit", () => {
    const input = new FakeInput();
    const warning = new FakeLengthWarning();
    initTagNameUppercaseDocument(buildDoc(input, warning));

    input.value = "a".repeat(41);
    input.fireInput();
    expect(warning.hidden).toBe(false);

    input.value = "a".repeat(20);
    input.fireInput();

    expect(warning.hidden).toBe(true);
    expect(warning.textContent).toBe("");
    expect(input.attributes["aria-invalid"]).toBeUndefined();
  });

  it("never truncates the input value, even when over the limit", () => {
    const input = new FakeInput();
    const warning = new FakeLengthWarning();
    initTagNameUppercaseDocument(buildDoc(input, warning));

    const overLong = "a".repeat(50);
    input.value = overLong;
    input.fireInput();

    expect(input.value).toBe(overLong.toUpperCase());
  });

  it("does not auto-submit when the length warning appears", () => {
    const input = new FakeInput();
    const warning = new FakeLengthWarning();
    initTagNameUppercaseDocument(buildDoc(input, warning));

    input.value = "a".repeat(41);
    expect(() => input.fireInput()).not.toThrow();
  });
});
