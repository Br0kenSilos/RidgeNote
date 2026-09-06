// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

import { initFullSearchDocument, matchesTagFilter } from "./full-search";

function readAppCss(): string {
  const cssPath = join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "css",
    "app.css",
  );
  return readFileSync(cssPath, "utf-8");
}

function readBaseTemplate(): string {
  const templatePath = join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "..",
    "..",
    "templates",
    "base.html",
  );
  return readFileSync(templatePath, "utf-8");
}

function buildPage(tagCount = 3): void {
  const tagOptions = Array.from({ length: tagCount }, (_, i) => {
    const name = `TAG${i}`;
    return `
      <label class="full-search-form__tag-option" data-full-search-tag-name="${name}">
        <input type="checkbox" name="tag" value="${i + 1}" data-full-search-tag-checkbox>
        <span class="note-list__tag-chip" data-tag-color="blue"><span>${name}</span></span>
      </label>
    `;
  }).join("");

  document.body.innerHTML = `
    <form method="get" data-full-search-form>
      <input type="text" name="q" value="">
      <fieldset data-full-search-tag-picker>
        <div class="full-search-form__tag-row">
          <div class="full-search-form__tag-combobox" data-full-search-tag-combobox>
            <input type="text" data-full-search-tag-filter hidden>
            <div data-full-search-tag-panel>
              <div data-full-search-tag-list>
                ${tagOptions}
              </div>
              <p data-full-search-tag-limit-note hidden></p>
            </div>
          </div>
          <select data-full-search-tag-mode>
            <option value="any">Any</option>
            <option value="all">All</option>
          </select>
        </div>
        <div data-full-search-tag-chips></div>
      </fieldset>
      <select data-full-search-tag-status>
        <option value="any">Any</option>
        <option value="tagged">Has tags</option>
        <option value="untagged">Without tags</option>
      </select>
    </form>
  `;
}

function getEls() {
  return {
    combobox: document.querySelector<HTMLElement>(
      "[data-full-search-tag-combobox]",
    )!,
    filterInput: document.querySelector<HTMLInputElement>(
      "[data-full-search-tag-filter]",
    )!,
    panel: document.querySelector<HTMLElement>("[data-full-search-tag-panel]")!,
    tagList: document.querySelector<HTMLElement>(
      "[data-full-search-tag-list]",
    )!,
    checkboxes: Array.from(
      document.querySelectorAll<HTMLInputElement>(
        "[data-full-search-tag-checkbox]",
      ),
    ),
    options: Array.from(
      document.querySelectorAll<HTMLElement>("[data-full-search-tag-name]"),
    ),
    tagStatus: document.querySelector<HTMLSelectElement>(
      "[data-full-search-tag-status]",
    )!,
    tagPicker: document.querySelector<HTMLFieldSetElement>(
      "[data-full-search-tag-picker]",
    )!,
    limitNote: document.querySelector<HTMLElement>(
      "[data-full-search-tag-limit-note]",
    )!,
    chips: document.querySelector<HTMLElement>("[data-full-search-tag-chips]")!,
  };
}

describe("matchesTagFilter", () => {
  it("matches everything for an empty query", () => {
    expect(matchesTagFilter("SERVER", "")).toBe(true);
    expect(matchesTagFilter("SERVER", "   ")).toBe(true);
  });

  it("matches a case-insensitive substring", () => {
    expect(matchesTagFilter("SERVER", "erv")).toBe(true);
    expect(matchesTagFilter("SERVER", "erv".toUpperCase())).toBe(true);
  });

  it("does not match a non-substring", () => {
    expect(matchesTagFilter("SERVER", "network")).toBe(false);
  });
});

describe("initFullSearchDocument", () => {
  it("returns false and wires nothing when the form is absent", () => {
    document.body.innerHTML = "<p>no form here</p>";
    expect(initFullSearchDocument(document)).toBe(false);
  });

  it("reveals the tag-filter input once initialized", () => {
    buildPage();
    initFullSearchDocument(document);
    const { filterInput } = getEls();
    expect(filterInput.hidden).toBe(false);
  });

  it("filters the visible tag options as the filter input changes", () => {
    buildPage();
    initFullSearchDocument(document);
    const { filterInput, options } = getEls();

    filterInput.value = "TAG1";
    filterInput.dispatchEvent(new Event("input"));

    expect(options[0].hidden).toBe(true); // TAG0
    expect(options[1].hidden).toBe(false); // TAG1
    expect(options[2].hidden).toBe(true); // TAG2
  });

  it("clearing the filter input shows every tag option again", () => {
    buildPage();
    initFullSearchDocument(document);
    const { filterInput, options } = getEls();

    filterInput.value = "TAG1";
    filterInput.dispatchEvent(new Event("input"));
    filterInput.value = "";
    filterInput.dispatchEvent(new Event("input"));

    options.forEach((option) => expect(option.hidden).toBe(false));
  });

  it("disables remaining unchecked checkboxes once 20 are selected and shows the limit note", () => {
    buildPage(21);
    initFullSearchDocument(document);
    const { checkboxes, limitNote } = getEls();

    checkboxes.slice(0, 20).forEach((box) => {
      box.checked = true;
      box.dispatchEvent(new Event("change"));
    });

    expect(checkboxes[20].disabled).toBe(true);
    expect(limitNote.hidden).toBe(false);
    checkboxes.slice(0, 20).forEach((box) => expect(box.disabled).toBe(false));
  });

  it("re-enables checkboxes once selection drops back below 20", () => {
    buildPage(21);
    initFullSearchDocument(document);
    const { checkboxes, limitNote } = getEls();

    checkboxes.slice(0, 20).forEach((box) => {
      box.checked = true;
      box.dispatchEvent(new Event("change"));
    });
    checkboxes[0].checked = false;
    checkboxes[0].dispatchEvent(new Event("change"));

    expect(checkboxes[20].disabled).toBe(false);
    expect(limitNote.hidden).toBe(true);
  });

  it("disables the tag picker fieldset when tag status is switched to untagged", () => {
    buildPage();
    initFullSearchDocument(document);
    const { tagStatus, tagPicker } = getEls();

    tagStatus.value = "untagged";
    tagStatus.dispatchEvent(new Event("change"));

    expect(tagPicker.disabled).toBe(true);
  });

  it("clears checked tags when switching to untagged", () => {
    buildPage();
    initFullSearchDocument(document);
    const { tagStatus, checkboxes } = getEls();

    checkboxes[0].checked = true;
    checkboxes[0].dispatchEvent(new Event("change"));

    tagStatus.value = "untagged";
    tagStatus.dispatchEvent(new Event("change"));

    expect(checkboxes[0].checked).toBe(false);
  });

  it("re-enables the tag picker when tag status returns to any", () => {
    buildPage();
    initFullSearchDocument(document);
    const { tagStatus, tagPicker } = getEls();

    tagStatus.value = "untagged";
    tagStatus.dispatchEvent(new Event("change"));
    tagStatus.value = "any";
    tagStatus.dispatchEvent(new Event("change"));

    expect(tagPicker.disabled).toBe(false);
  });

  it("does not clear an existing selection on initial load even if tag status starts as untagged", () => {
    buildPage();
    const { checkboxes, tagStatus } = getEls();
    checkboxes[0].checked = true;
    tagStatus.value = "untagged";

    initFullSearchDocument(document);

    // Init-time sync only disables the fieldset; it must not have wiped
    // an already-checked box that was part of the server-rendered state.
    expect(checkboxes[0].checked).toBe(true);
    expect(getEls().tagPicker.disabled).toBe(true);
  });

  describe("the tag popover panel", () => {
    it("collapses the panel on init, marking the combobox as JS-enhanced", () => {
      buildPage();
      initFullSearchDocument(document);
      const { panel, combobox } = getEls();

      expect(panel.hidden).toBe(true);
      expect(
        combobox.classList.contains("full-search-form__tag-combobox--js"),
      ).toBe(true);
    });

    it("opens the panel when the filter input is focused", () => {
      buildPage();
      initFullSearchDocument(document);
      const { filterInput, panel } = getEls();

      filterInput.dispatchEvent(new Event("focus"));

      expect(panel.hidden).toBe(false);
      expect(filterInput.getAttribute("aria-expanded")).toBe("true");
    });

    it("opens the panel when the filter input is clicked", () => {
      buildPage();
      initFullSearchDocument(document);
      const { filterInput, panel } = getEls();

      filterInput.dispatchEvent(new Event("click"));

      expect(panel.hidden).toBe(false);
    });

    it("closes the panel on an outside click", () => {
      buildPage();
      initFullSearchDocument(document);
      const { filterInput, panel } = getEls();

      filterInput.dispatchEvent(new Event("focus"));
      expect(panel.hidden).toBe(false);

      document.body.dispatchEvent(new MouseEvent("click", { bubbles: true }));

      expect(panel.hidden).toBe(true);
    });

    it("does not close the panel when clicking inside it (e.g. a checkbox)", () => {
      buildPage();
      initFullSearchDocument(document);
      const { filterInput, panel, checkboxes } = getEls();

      filterInput.dispatchEvent(new Event("focus"));
      checkboxes[0].dispatchEvent(new MouseEvent("click", { bubbles: true }));

      expect(panel.hidden).toBe(false);
    });

    it("closes the panel on Escape", () => {
      buildPage();
      initFullSearchDocument(document);
      const { combobox, filterInput, panel } = getEls();

      filterInput.dispatchEvent(new Event("focus"));
      expect(panel.hidden).toBe(false);

      combobox.dispatchEvent(
        new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
      );

      expect(panel.hidden).toBe(true);
    });

    it("force-closes the panel when tag status switches to untagged", () => {
      buildPage();
      initFullSearchDocument(document);
      const { filterInput, panel, tagStatus } = getEls();

      filterInput.dispatchEvent(new Event("focus"));
      expect(panel.hidden).toBe(false);

      tagStatus.value = "untagged";
      tagStatus.dispatchEvent(new Event("change"));

      expect(panel.hidden).toBe(true);
    });
  });

  describe("selected Tag chips", () => {
    it("renders no chips when nothing is selected", () => {
      buildPage();
      initFullSearchDocument(document);
      const { chips } = getEls();

      expect(chips.children.length).toBe(0);
    });

    it("renders a chip mirroring a server-rendered checked box on init", () => {
      buildPage();
      const { checkboxes } = getEls();
      checkboxes[0].checked = true;

      initFullSearchDocument(document);
      const { chips } = getEls();

      expect(chips.children.length).toBe(1);
      expect(chips.textContent).toContain("TAG0");
    });

    it("adds a chip when a checkbox is checked", () => {
      buildPage();
      initFullSearchDocument(document);
      const { checkboxes, chips } = getEls();

      checkboxes[1].checked = true;
      checkboxes[1].dispatchEvent(new Event("change"));

      expect(chips.children.length).toBe(1);
      expect(chips.textContent).toContain("TAG1");
    });

    it("removes a chip when its checkbox is unchecked", () => {
      buildPage();
      initFullSearchDocument(document);
      const { checkboxes, chips } = getEls();

      checkboxes[0].checked = true;
      checkboxes[0].dispatchEvent(new Event("change"));
      checkboxes[0].checked = false;
      checkboxes[0].dispatchEvent(new Event("change"));

      expect(chips.children.length).toBe(0);
    });

    it("unchecks the checkbox when a chip's remove control is clicked", () => {
      buildPage();
      initFullSearchDocument(document);
      const { checkboxes, chips } = getEls();

      checkboxes[0].checked = true;
      checkboxes[0].dispatchEvent(new Event("change"));

      const removeButton = chips.querySelector<HTMLButtonElement>("button")!;
      removeButton.click();

      expect(checkboxes[0].checked).toBe(false);
      expect(chips.children.length).toBe(0);
    });

    it("clears all chips when tag status switches to untagged", () => {
      buildPage();
      initFullSearchDocument(document);
      const { checkboxes, chips, tagStatus } = getEls();

      checkboxes[0].checked = true;
      checkboxes[0].dispatchEvent(new Event("change"));
      expect(chips.children.length).toBe(1);

      tagStatus.value = "untagged";
      tagStatus.dispatchEvent(new Event("change"));

      expect(chips.children.length).toBe(0);
    });
  });
});

describe("flash-of-unenhanced-content prevention", () => {
  it("hides the raw Tag panel before JS enhancement, only while JS is capable and not yet initialized", () => {
    const css = readAppCss();

    // The early-hide rule must exist, must be gated on the `[data-js]`
    // marker, and must stop matching once `full-search.ts` adds
    // `.full-search-form__tag-combobox--js` -- otherwise it would also
    // suppress the panel while genuinely open post-enhancement.
    const earlyHideMatch = css.match(
      /\[data-js\]\s+\.full-search-form__tag-combobox:not\(\.full-search-form__tag-combobox--js\)\s+\.full-search-form__tag-panel\s*\{\s*display: none;/,
    );
    expect(earlyHideMatch).not.toBeNull();
  });

  it("does not hide the Tag panel unconditionally -- the no-JS fallback stays visible with no marker present", () => {
    const css = readAppCss();

    // The panel's own base (unconditioned) rule must never itself set
    // `display: none` -- only the `[data-js]`-gated rule above (which
    // requires the marker `initFullSearchDocument` never sees applied
    // without JavaScript) may do that.
    const baseRuleIndex = css.indexOf(".full-search-form__tag-panel {");
    expect(baseRuleIndex).toBeGreaterThan(-1);
    const baseRuleBlock = css.slice(
      baseRuleIndex,
      css.indexOf("}", baseRuleIndex),
    );
    expect(baseRuleBlock).not.toContain("display: none");
  });

  it("marks the document as JS-capable via an early, synchronous, pre-paint inline script -- not a deferred/module one", () => {
    const html = readBaseTemplate();

    const scriptMatch = html.match(
      /<script>document\.documentElement\.dataset\.js="true";/,
    );
    expect(scriptMatch).not.toBeNull();

    // This must be the same early, blocking inline script already used
    // for the theme bootstrap (immediately inside <body>, before any
    // visible content) -- not the deferred module bundle, which cannot
    // run until after this markup has already been parsed and painted.
    const scriptTag = scriptMatch![0];
    expect(scriptTag).not.toContain("defer");
    expect(scriptTag).not.toContain("async");
    expect(scriptTag).not.toContain('type="module"');
  });

  it("collapses the panel and marks the combobox as enhanced on init (handing off from the CSS marker to JS)", () => {
    buildPage();
    initFullSearchDocument(document);
    const { panel, combobox } = getEls();

    expect(panel.hidden).toBe(true);
    expect(
      combobox.classList.contains("full-search-form__tag-combobox--js"),
    ).toBe(true);
  });

  it("still opens and closes normally once initialized", () => {
    buildPage();
    initFullSearchDocument(document);
    const { filterInput, panel, combobox } = getEls();

    filterInput.dispatchEvent(new Event("focus"));
    expect(panel.hidden).toBe(false);

    combobox.dispatchEvent(
      new KeyboardEvent("keydown", { key: "Escape", bubbles: true }),
    );
    expect(panel.hidden).toBe(true);
  });
});
