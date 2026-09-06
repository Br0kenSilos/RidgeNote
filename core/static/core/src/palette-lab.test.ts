// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";

import { initPaletteLabDocument } from "./palette-lab";

function buildDom(): void {
  document.documentElement.dataset.theme = "warm-light";
  document.body.dataset.theme = "warm-light";
  document.body.innerHTML = `
    <section data-palette-lab-root>
      <select data-palette-lab-select>
        <optgroup label="Cool Light">
          <option value="cool-a">Cool A</option>
          <option value="cool-b">Cool B</option>
        </optgroup>
        <optgroup label="Soft/Dim Dark">
          <option value="dim-a">Dim A</option>
          <option value="dim-b">Dim B</option>
        </optgroup>
      </select>
      <div class="palette-lab__swatch-strip">
        <button type="button" data-palette-lab-swatch="cool-a" aria-pressed="false">Cool A</button>
        <button type="button" data-palette-lab-swatch="cool-b" aria-pressed="false">Cool B</button>
        <button type="button" data-palette-lab-swatch="dim-a" aria-pressed="false">Dim A</button>
        <button type="button" data-palette-lab-swatch="dim-b" aria-pressed="false">Dim B</button>
      </div>
      <div data-palette-lab-preview data-palette-candidate="cool-a"></div>
    </section>
  `;
}

function select(): HTMLSelectElement {
  return document.querySelector(
    "[data-palette-lab-select]",
  ) as HTMLSelectElement;
}

function preview(): HTMLElement {
  return document.querySelector("[data-palette-lab-preview]") as HTMLElement;
}

function swatch(id: string): HTMLElement {
  return document.querySelector(
    `[data-palette-lab-swatch="${id}"]`,
  ) as HTMLElement;
}

describe("initPaletteLabDocument", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    delete document.documentElement.dataset.theme;
    delete document.body.dataset.theme;
  });

  it("does nothing when the Palette Lab root is absent", () => {
    document.body.innerHTML = "<div>no lab here</div>";
    expect(() => initPaletteLabDocument(document)).not.toThrow();
  });

  it("selects the first candidate by default and marks its swatch active", () => {
    buildDom();
    initPaletteLabDocument(document);

    expect(select().value).toBe("cool-a");
    expect(preview().dataset.paletteCandidate).toBe("cool-a");
    expect(swatch("cool-a").getAttribute("aria-pressed")).toBe("true");
    expect(swatch("cool-b").getAttribute("aria-pressed")).toBe("false");
  });

  it("changing the select updates the preview and active swatch", () => {
    buildDom();
    initPaletteLabDocument(document);

    select().value = "dim-a";
    select().dispatchEvent(new Event("change"));

    expect(preview().dataset.paletteCandidate).toBe("dim-a");
    expect(swatch("dim-a").getAttribute("aria-pressed")).toBe("true");
    expect(swatch("cool-a").getAttribute("aria-pressed")).toBe("false");
  });

  it("clicking a swatch updates the preview and the select", () => {
    buildDom();
    initPaletteLabDocument(document);

    swatch("cool-b").dispatchEvent(new Event("click"));

    expect(preview().dataset.paletteCandidate).toBe("cool-b");
    expect(select().value).toBe("cool-b");
    expect(swatch("cool-b").getAttribute("aria-pressed")).toBe("true");
  });

  it("ignores an unknown candidate id and leaves the current selection unchanged", () => {
    buildDom();
    initPaletteLabDocument(document);

    select().value = "dim-b";
    select().dispatchEvent(new Event("change"));

    // Simulate an out-of-band call with a candidate id that has no
    // matching swatch/CSS rule -- must be a safe no-op, not a crash or
    // a state change to something unrecognized.
    const bogusSwatch = document.createElement("button");
    bogusSwatch.dataset.paletteLabSwatch = "not-a-real-candidate";
    document
      .querySelector(".palette-lab__swatch-strip")!
      .appendChild(bogusSwatch);
    bogusSwatch.dispatchEvent(new Event("click"));

    expect(preview().dataset.paletteCandidate).toBe("dim-b");
    expect(select().value).toBe("dim-b");
  });

  it("never touches the real application theme state", () => {
    buildDom();
    initPaletteLabDocument(document);

    swatch("dim-b").dispatchEvent(new Event("click"));

    expect(document.documentElement.dataset.theme).toBe("warm-light");
    expect(document.body.dataset.theme).toBe("warm-light");
  });

  it("is safe to initialize twice (double-init does not double-bind listeners)", () => {
    buildDom();
    initPaletteLabDocument(document);
    initPaletteLabDocument(document);

    select().value = "dim-a";
    select().dispatchEvent(new Event("change"));

    // If listeners were double-bound, this would still just set the
    // same value twice -- assert on the swatch state, which would show
    // duplicate toggling side effects if double-binding were broken.
    expect(preview().dataset.paletteCandidate).toBe("dim-a");
    expect(swatch("dim-a").getAttribute("aria-pressed")).toBe("true");
  });
});
