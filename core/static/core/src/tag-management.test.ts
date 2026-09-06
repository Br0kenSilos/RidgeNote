// @vitest-environment jsdom
import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  initTagManagementDocument,
  normalizeTagNameForPreview,
  previewTagColor,
  tagColorLabel,
  TAG_FILTER_DEBOUNCE_MS,
  uniqueNoteCount,
} from "./tag-management";
import { initTagNameUppercaseDocument } from "./tag-name-uppercase";

// A failed assertion inside a `vi.useFakeTimers()` test would otherwise
// skip that test's own `vi.useRealTimers()` cleanup and leave fake timers
// bleeding into every later test in this file -- this safety net runs
// regardless of pass/fail.
afterEach(() => {
  vi.useRealTimers();
});

function readAppCss(): string {
  const cssPath = join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "css",
    "app.css",
  );
  return readFileSync(cssPath, "utf-8");
}

// Density comes from
// layout (an ellipsis row-menu replacing permanently-visible actions), not
// from shrinking fonts/controls -- the wide row must render as one real
// table row with normal body text, and the narrow row collapses to a
// two-line CSS grid keyed off `.tag-management__row` (not every `tr`,
// since the inline-editor `tr`s must NOT get the row grid).
describe("Tag manager CSS -- ellipsis menu, compact rows", () => {
  it("no longer renders a data-label prefix on every narrow cell", () => {
    const css = readAppCss();
    expect(css).not.toMatch(/tag-management__table td\[data-label\]::before/);
  });

  it("does not shrink Tag manager's own row/cell font size below normal body text", () => {
    const css = readAppCss();
    const cellRuleMatch = css.match(
      /\.tag-management__table th,\s*\.tag-management__table td \{[^}]*\}/,
    );
    expect(cellRuleMatch).not.toBeNull();
    expect(cellRuleMatch![0]).not.toMatch(/font-size/);
  });

  it("narrow closed rows use a two-line CSS grid scoped to .tag-management__row, not every tr", () => {
    const css = readAppCss();
    expect(css).toMatch(/\.tag-management__row \{[^}]*display: grid;/);
    expect(css).toMatch(/grid-template-areas:\s*"select name usage menu"/);
  });

  it("each cell has its own grid-area assignment for the narrow layout, including the menu cell", () => {
    const css = readAppCss();
    expect(css).toMatch(
      /\.tag-management__cell--select \{\s*grid-area: select;/,
    );
    expect(css).toMatch(/\.tag-management__cell--name \{\s*grid-area: name;/);
    expect(css).toMatch(/\.tag-management__cell--usage \{\s*grid-area: usage;/);
    expect(css).toMatch(/\.tag-management__cell--color \{\s*grid-area: color;/);
    expect(css).toMatch(/\.tag-management__cell--menu \{\s*grid-area: menu;/);
  });

  it("renders the stored color as a pill, not the earlier tiny swatch/dot", () => {
    const css = readAppCss();
    expect(css).toMatch(/\.tag-management__color-pill \{/);
    expect(css).not.toMatch(/\.tag-management__color-chip \{/);
    expect(css).not.toMatch(/\.tag-management__color-swatch \{/);
  });

  it("the color pill reuses the same --color-tag-* custom properties as the existing pill families", () => {
    const css = readAppCss();
    const pillRuleMatch = css.match(
      /\.tag-management__color-pill\[data-tag-color="violet"\] \{[^}]*\}/,
    );
    expect(pillRuleMatch).not.toBeNull();
    expect(pillRuleMatch![0]).toMatch(/var\(--color-tag-violet-bg\)/);
    expect(pillRuleMatch![0]).toMatch(/var\(--color-tag-violet-text\)/);
  });

  it("bounds desktop column widths instead of stretching them across the container", () => {
    const css = readAppCss();
    expect(css).toMatch(/\.tag-management__table \{[^}]*table-layout: fixed;/);
    expect(css).toMatch(/\.tag-management__col--color \{[^}]*width:/);
    expect(css).toMatch(/\.tag-management__col--usage \{[^}]*width:/);
  });

  it("centers the usage column header and values consistently", () => {
    const css = readAppCss();
    const usageRuleMatch = css.match(/\.tag-management__cell--usage \{[^}]*\}/);
    expect(usageRuleMatch).not.toBeNull();
    expect(usageRuleMatch![0]).toMatch(/text-align: center;/);
  });

  it("fixes the narrow [hidden] specificity bug that showed Rename and Recolor editors simultaneously", () => {
    const css = readAppCss();
    expect(css).toMatch(
      /\.tag-management__editor-row\[hidden\] \{\s*display: none;/,
    );
  });

  it("does not touch the shared tag-suggestion pill or note-tags chip rules", () => {
    const css = readAppCss();
    const chipSection = css.slice(
      css.indexOf(".tag-suggestion__pill {"),
      css.indexOf(".tag-suggestion__pill {") + 400,
    );
    expect(chipSection).not.toMatch(/tag-management/);
  });

  it("no longer defines the rejected correction-1 two-button action-row classes", () => {
    const css = readAppCss();
    expect(css).not.toMatch(/\.tag-management__action \{/);
    expect(css).not.toMatch(/\.tag-management__action-trigger \{/);
  });

  it("styles the inline editor row distinctly from the main row grid", () => {
    const css = readAppCss();
    expect(css).toMatch(/\.tag-management__editor-row td \{/);
    expect(css).toMatch(/\.tag-management__inline-form \{/);
  });
});

describe("Tag manager CSS -- uppercase pills, merged toolbar, filtered-row fix", () => {
  it("renders every Tag-manager color pill (stored and preview) in uppercase, presentation-only", () => {
    const css = readAppCss();
    const pillRuleMatch = css.match(/\.tag-management__color-pill \{[^}]*\}/);
    expect(pillRuleMatch).not.toBeNull();
    expect(pillRuleMatch![0]).toMatch(/text-transform: uppercase;/);
  });

  it("+ New tag is the toolbar form's own child, not a separately wrapped/staggered control", () => {
    const css = readAppCss();
    // The old two-flex-context wrapper classes are gone.
    expect(css).not.toMatch(/\.tag-management__filter-form \{/);
    expect(css).toMatch(/\.tag-management__toolbar \{[^}]*display: flex;/);
    expect(css).toMatch(/\.tag-management__bulk-bar \{[^}]*display: flex;/);
  });

  it("fixes the same [hidden] specificity bug for filtered-out .tag-management__row at narrow widths", () => {
    const css = readAppCss();
    expect(css).toMatch(/\.tag-management__row\[hidden\] \{\s*display: none;/);
  });

  it("styles the New tag preview and length-warning distinctly from the muted rename notice", () => {
    const css = readAppCss();
    expect(css).toMatch(/\.tag-management__inline-form-preview \{/);
    expect(css).toMatch(/\.tag-management__inline-form-length-warning \{/);
  });
});

describe("Tag manager CSS -- Filter-button fix, compact toolbar/editors, uppercase color choices", () => {
  it("fixes the [hidden]-vs-.button-link cascade bug that kept the Filter fallback button visible after JS init", () => {
    const css = readAppCss();
    expect(css).toMatch(
      /\.tag-management__toolbar \[hidden\] \{\s*display: none;/,
    );
  });

  it("bounds the toolbar's filter input and sort select to compact, deliberate widths", () => {
    const css = readAppCss();
    const filterInputRule = css.match(
      /\.tag-management__toolbar input\[type="text"\] \{[^}]*\}/,
    );
    const sortSelectRule = css.match(
      /\.tag-management__toolbar select\[data-tag-sort-select\] \{[^}]*\}/,
    );
    expect(filterInputRule).not.toBeNull();
    expect(filterInputRule![0]).toMatch(/width:/);
    expect(sortSelectRule).not.toBeNull();
    expect(sortSelectRule![0]).toMatch(/width:/);
  });

  it("bounds every inline editor's name input and color select to compact widths (New tag, Rename, Recolor share one rule)", () => {
    const css = readAppCss();
    const inputRule = css.match(
      /\.tag-management__inline-form input\[type="text"\] \{[^}]*\}/,
    );
    const selectRule = css.match(
      /\.tag-management__inline-form select \{[^}]*\}/,
    );
    expect(inputRule).not.toBeNull();
    expect(inputRule![0]).toMatch(/width:/);
    expect(selectRule).not.toBeNull();
    expect(selectRule![0]).toMatch(/width:/);
  });

  it("renders inline-editor color select options uppercase (New tag + Recolor), presentation-only", () => {
    const css = readAppCss();
    const selectRule = css.match(
      /\.tag-management__inline-form select \{[^}]*\}/,
    );
    expect(selectRule).not.toBeNull();
    expect(selectRule![0]).toMatch(/text-transform: uppercase;/);
  });
});

describe("Tag manager CSS -- fixed-table name column, quiet disabled Delete selected", () => {
  it("leaves the Name column with no explicit width -- the sole flexible column in the fixed table layout", () => {
    const css = readAppCss();
    expect(css).not.toMatch(/\.tag-management__col--name \{/);
  });

  it("still bounds every other column to a fixed width", () => {
    const css = readAppCss();
    expect(css).toMatch(/\.tag-management__col--select \{[^}]*width:/);
    expect(css).toMatch(/\.tag-management__col--color \{[^}]*width:/);
    expect(css).toMatch(/\.tag-management__col--usage \{[^}]*width:/);
    expect(css).toMatch(/\.tag-management__col--menu \{[^}]*width:/);
  });

  it("gives disabled Tag-manager buttons (e.g. Delete selected) the same quiet treatment as .icon-button:disabled, scoped to the page only", () => {
    const css = readAppCss();
    const rule = css.match(
      /\.tag-management-page \.button-link:disabled \{[^}]*\}/,
    );
    expect(rule).not.toBeNull();
    expect(rule![0]).toMatch(/opacity: 0\.5;/);
    expect(rule![0]).toMatch(/cursor: not-allowed;/);
  });
});

describe("Tag manager CSS -- one-line toolbar/New-tag editor at wide, compact control heights", () => {
  it("removes the generic form max-width cap from the toolbar and every inline-form (New tag/Rename/Recolor)", () => {
    const css = readAppCss();
    const toolbarRule = css.match(/\.tag-management__toolbar \{[^}]*\}/);
    const inlineFormRule = css.match(/\.tag-management__inline-form \{[^}]*\}/);
    expect(toolbarRule).not.toBeNull();
    expect(toolbarRule![0]).toMatch(/max-width: none;/);
    expect(inlineFormRule).not.toBeNull();
    expect(inlineFormRule![0]).toMatch(/max-width: none;/);
  });

  it("keeps the toolbar and New-tag/Rename/Recolor editors on one line (no wrap) at wide widths", () => {
    const css = readAppCss();
    const toolbarRule = css.match(/\.tag-management__toolbar \{[^}]*\}/);
    const inlineFormRule = css.match(/\.tag-management__inline-form \{[^}]*\}/);
    expect(toolbarRule![0]).toMatch(/flex-wrap: nowrap;/);
    expect(inlineFormRule![0]).toMatch(/flex-wrap: nowrap;/);
  });

  it("restores wrapping for the toolbar and inline-form only inside the narrow media query", () => {
    const css = readAppCss();
    const narrowBlockStart = css.indexOf(
      "/* Narrow: a purpose-built compact row",
    );
    expect(narrowBlockStart).toBeGreaterThan(-1);
    const narrowBlockEnd = css.indexOf(
      "\n}\n",
      css.indexOf("@media (width < 640px) {", narrowBlockStart),
    );
    const narrowBlock = css.slice(narrowBlockStart, narrowBlockEnd);
    expect(narrowBlock).toMatch(
      /\.tag-management__toolbar \{\s*flex-wrap: wrap;/,
    );
    expect(narrowBlock).toMatch(
      /\.tag-management__inline-form \{\s*flex-wrap: wrap;/,
    );
  });

  it("gives Tag-manager buttons, toolbar inputs/selects, and inline-form inputs/selects a shared compact ~32-34px height", () => {
    const css = readAppCss();
    const buttonRule = css.match(
      /\.tag-management-page \.button-link \{[^}]*\}/,
    );
    const toolbarInputRule = css.match(
      /\.tag-management__toolbar input\[type="text"\] \{[^}]*\}/,
    );
    const toolbarSelectRule = css.match(
      /\.tag-management__toolbar select\[data-tag-sort-select\] \{[^}]*\}/,
    );
    const inlineInputRule = css.match(
      /\.tag-management__inline-form input\[type="text"\] \{[^}]*\}/,
    );
    const inlineSelectRule = css.match(
      /\.tag-management__inline-form select \{[^}]*\}/,
    );
    for (const rule of [
      buttonRule,
      toolbarInputRule,
      toolbarSelectRule,
      inlineInputRule,
      inlineSelectRule,
    ]) {
      expect(rule).not.toBeNull();
      expect(rule![0]).toMatch(/min-height: 2\.125rem;/);
    }
  });
});

describe("Tag manager CSS -- button label never wraps", () => {
  it("stops every page-local action button's label from wrapping (+ New tag, Add tag, Save, Cancel, Delete selected all share .button-link)", () => {
    const css = readAppCss();
    const buttonRule = css.match(
      /\.tag-management-page \.button-link \{[^}]*\}/,
    );
    expect(buttonRule).not.toBeNull();
    expect(buttonRule![0]).toMatch(/white-space: nowrap;/);
  });

  it("prevents the button from being flex-shrunk narrower than its own label, so it sizes to content instead of wrapping", () => {
    const css = readAppCss();
    const buttonRule = css.match(
      /\.tag-management-page \.button-link \{[^}]*\}/,
    );
    expect(buttonRule).not.toBeNull();
    expect(buttonRule![0]).toMatch(/flex-shrink: 0;/);
  });

  it("does not touch the button's own height -- min-height stays exactly the correction-7 34px target", () => {
    const css = readAppCss();
    const buttonRule = css.match(
      /\.tag-management-page \.button-link \{[^}]*\}/,
    );
    expect(buttonRule![0]).toMatch(/min-height: 2\.125rem;/);
    expect(buttonRule![0]).not.toMatch(/min-height: (?!2\.125rem;)/);
  });
});

describe("uniqueNoteCount", () => {
  it("returns 0 for no lists", () => {
    expect(uniqueNoteCount([])).toBe(0);
  });

  it("returns 0 for empty-string lists", () => {
    expect(uniqueNoteCount(["", ""])).toBe(0);
  });

  it("counts a single list's own entries", () => {
    expect(uniqueNoteCount(["1,2,3"])).toBe(3);
  });

  it("deduplicates notes shared across multiple tags", () => {
    expect(uniqueNoteCount(["1,2", "2,3"])).toBe(3);
  });
});

// This alias table
// and label table are a deliberate, explicitly-acknowledged duplicate of
// `notes/services.py`'s `_SEMANTIC_TAG_COLOR_ALIASES` and
// `notes/models.py`'s `TAG_COLOR_CHOICES`, kept here only so this parity
// test fails loudly if `tag-management.ts`'s own preview tables ever
// silently drift from the server's real ones.
const EXPECTED_SEMANTIC_ALIASES: Record<string, string> = {
  SLATE: "slate",
  GRAY: "slate",
  GREY: "slate",
  BLUE: "blue",
  TEAL: "teal",
  CYAN: "teal",
  GREEN: "green",
  YELLOW: "yellow",
  AMBER: "amber",
  ORANGE: "orange",
  RED: "red",
  ROSE: "rose",
  PINK: "rose",
  VIOLET: "violet",
  PURPLE: "violet",
  BROWN: "brown",
};

const EXPECTED_COLOR_LABELS: Record<string, string> = {
  slate: "Slate",
  blue: "Blue",
  teal: "Teal",
  green: "Green",
  yellow: "Yellow",
  amber: "Amber",
  orange: "Orange",
  red: "Red",
  rose: "Rose",
  violet: "Violet",
  brown: "Brown",
};

describe("normalizeTagNameForPreview", () => {
  it("trims outer whitespace", () => {
    expect(normalizeTagNameForPreview("  red  ")).toBe("RED");
  });

  it("collapses repeated internal whitespace", () => {
    expect(normalizeTagNameForPreview("server   room")).toBe("SERVER ROOM");
  });

  it("uppercases", () => {
    expect(normalizeTagNameForPreview("teal")).toBe("TEAL");
  });
});

describe("previewTagColor -- precedence parity", () => {
  it("matches every alias in the server's own semantic table", () => {
    for (const [alias, expectedColor] of Object.entries(
      EXPECTED_SEMANTIC_ALIASES,
    )) {
      expect(previewTagColor(alias, "")).toBe(expectedColor);
      expect(previewTagColor(alias.toLowerCase(), "")).toBe(expectedColor);
    }
  });

  it("falls back to slate for an ordinary, non-aliased name", () => {
    expect(previewTagColor("SERVER", "")).toBe("slate");
  });

  it("falls back to slate for a blank name", () => {
    expect(previewTagColor("", "")).toBe("slate");
  });

  it("an explicit color always wins over a semantic match", () => {
    expect(previewTagColor("RED", "violet")).toBe("violet");
  });

  it("an explicit color always wins for an ordinary name too", () => {
    expect(previewTagColor("SERVER", "rose")).toBe("rose");
  });

  it("near-miss names (not exact aliases) never match semantically", () => {
    expect(previewTagColor("BLUEPRINT", "")).toBe("slate");
    expect(previewTagColor("GREENHOUSE", "")).toBe("slate");
  });
});

// Semantic-enabled/default-color
// come from the owner's actual preferences, not hardcoded constants.
describe("previewTagColor -- preference options", () => {
  it("skips semantic matching entirely when semanticEnabled is false", () => {
    expect(previewTagColor("RED", "", { semanticEnabled: false })).toBe(
      "slate",
    );
  });

  it("falls back to a custom defaultColor when no explicit color and no semantic match", () => {
    expect(previewTagColor("SERVER", "", { defaultColor: "violet" })).toBe(
      "violet",
    );
  });

  it("falls back to a custom defaultColor when semantic is disabled, even for an alias name", () => {
    expect(
      previewTagColor("RED", "", {
        semanticEnabled: false,
        defaultColor: "violet",
      }),
    ).toBe("violet");
  });

  it("a semantic match still wins over a custom defaultColor when semantic remains enabled", () => {
    expect(previewTagColor("RED", "", { defaultColor: "violet" })).toBe("red");
  });

  it("an explicit color always wins over both semanticEnabled and defaultColor options", () => {
    expect(
      previewTagColor("RED", "amber", {
        semanticEnabled: false,
        defaultColor: "violet",
      }),
    ).toBe("amber");
  });

  it("omitting options entirely still behaves exactly as before (semantic on, Slate default)", () => {
    expect(previewTagColor("RED", "")).toBe("red");
    expect(previewTagColor("SERVER", "")).toBe("slate");
  });
});

describe("tagColorLabel", () => {
  it("matches every label in the server's own TAG_COLOR_CHOICES", () => {
    for (const [color, expectedLabel] of Object.entries(
      EXPECTED_COLOR_LABELS,
    )) {
      expect(tagColorLabel(color)).toBe(expectedLabel);
    }
  });
});

function tagRowHtml(options: {
  id: number;
  name: string;
  usageCount: number;
  noteIds: string;
  consequence: string;
}): string {
  const { id, name, usageCount, noteIds, consequence } = options;
  return `
    <tr class="tag-management__row" data-tag-row data-tag-note-ids="${noteIds}" data-tag-usage-count="${usageCount}">
      <td><input type="checkbox" form="tag-bulk-select-form" name="tag_ids" value="${id}" data-tag-select aria-label="Select ${name}"></td>
      <td class="tag-management__cell--name">${name}</td>
      <td>
        <details class="row-action-menu" data-row-menu="${id}">
          <summary aria-label="Actions for ${name}">⋮</summary>
          <div>
            <button type="button" data-tag-inline-edit-trigger data-tag-inline-edit-target="tag-rename-row-${id}">Rename</button>
            <button type="button" data-tag-inline-edit-trigger data-tag-inline-edit-target="tag-recolor-row-${id}">Recolor</button>
            <form id="tag-delete-form-${id}" method="post" action="/accounts/tags/${id}/delete/"></form>
            <button type="button" data-tag-delete-trigger data-tag-delete-kind="single"
                    data-tag-delete-name="${name}" data-tag-delete-consequence="${consequence}"
                    data-tag-delete-form-id="tag-delete-form-${id}">Delete</button>
          </div>
        </details>
      </td>
    </tr>
    <tr class="tag-management__editor-row" id="tag-rename-row-${id}" data-tag-inline-editor hidden>
      <td colspan="3">
        <input type="text" value="${name}">
        <button type="button" data-tag-inline-edit-cancel>Cancel</button>
      </td>
    </tr>
    <tr class="tag-management__editor-row" id="tag-recolor-row-${id}" data-tag-inline-editor hidden>
      <td colspan="3">
        <select><option value="red">Red</option></select>
        <button type="button" data-tag-inline-edit-cancel>Cancel</button>
      </td>
    </tr>
  `;
}

function buildPage(): void {
  document.body.innerHTML = `
    <form data-tag-filter-form method="get" action="/accounts/tags/">
      <button type="button" data-tag-inline-edit-trigger data-tag-inline-edit-target="tag-new-editor">+ New tag</button>
      <input type="text" data-tag-filter-input name="q" value="">
      <select data-tag-sort-select name="sort">
        <option value="name_asc" selected>Name A-Z</option>
        <option value="name_desc">Name Z-A</option>
        <option value="usage_asc">Usage low-high</option>
        <option value="usage_desc">Usage high-low</option>
      </select>
      <button type="submit" data-tag-filter-submit>Filter</button>
      <a href="/accounts/tags/?sort=name_asc" class="tag-management__clear-link">Clear</a>
    </form>
    <div id="tag-new-editor" data-tag-inline-editor data-tag-new-editor data-tag-semantic-enabled="true" data-tag-default-color="slate" hidden>
      <form method="post" action="/accounts/tags/create/">
        <input type="hidden" name="next" value="/accounts/tags/?sort=name_asc">
        <input type="text" name="name" value="" data-tag-name-input>
        <p data-tag-name-length-warning hidden></p>
        <select data-tag-new-color-select>
          <option value="">Default color</option>
          <option value="violet">Violet</option>
          <option value="rose">Rose</option>
        </select>
        <span data-tag-color-preview data-tag-color="slate">Slate</span>
        <button type="submit">Add tag</button>
        <button type="button" data-tag-inline-edit-cancel>Cancel</button>
      </form>
    </div>
    <div class="tag-management__bulk-bar">
      <label>
        <input type="checkbox" data-tag-select-all aria-label="Select all visible tags">
        Select all
      </label>
      <span data-tag-selected-count aria-live="polite">0 selected</span>
      <button type="button" data-tag-bulk-delete-trigger data-tag-delete-kind="bulk" disabled>Delete selected</button>
    </div>
    <form id="tag-bulk-select-form" method="post" action="/accounts/tags/bulk-delete/"></form>
    <table class="tag-management__table">
      <tbody>
        ${tagRowHtml({ id: 1, name: "RED", usageCount: 2, noteIds: "1,2", consequence: "Deleting this tag removes it from 2 notes. The notes themselves will not be deleted." })}
        ${tagRowHtml({ id: 2, name: "BLUE", usageCount: 0, noteIds: "2,3", consequence: "Deleting this tag removes it from your tag list." })}
        ${tagRowHtml({ id: 3, name: "GREEN", usageCount: 0, noteIds: "", consequence: "Deleting this tag removes it from your tag list." })}
      </tbody>
    </table>
    <dialog id="tag-delete-confirm-dialog" data-tag-delete-confirm-dialog>
      <h2 data-tag-delete-confirm-title></h2>
      <p data-tag-delete-confirm-consequence></p>
      <button type="button" data-tag-delete-confirm-cancel>Cancel</button>
      <button type="submit" data-tag-delete-confirm-submit>Delete</button>
    </dialog>
  `;
}

function getEls() {
  const selectAll = document.querySelector<HTMLInputElement>(
    "[data-tag-select-all]",
  )!;
  const checkboxes = Array.from(
    document.querySelectorAll<HTMLInputElement>("[data-tag-select]"),
  );
  const selectedCount = document.querySelector<HTMLElement>(
    "[data-tag-selected-count]",
  )!;
  const bulkTrigger = document.querySelector<HTMLButtonElement>(
    "[data-tag-bulk-delete-trigger]",
  )!;
  const dialog = document.querySelector<HTMLDialogElement>(
    "[data-tag-delete-confirm-dialog]",
  )!;
  return { selectAll, checkboxes, selectedCount, bulkTrigger, dialog };
}

function visibleRowNames(): string[] {
  return Array.from(
    document.querySelectorAll<HTMLTableRowElement>("[data-tag-row]"),
  )
    .filter((row) => !row.hidden)
    .map(
      (row) => row.querySelector(".tag-management__cell--name")!.textContent,
    );
}

function allRowNamesInDomOrder(): string[] {
  return Array.from(
    document.querySelectorAll<HTMLTableRowElement>("[data-tag-row]"),
  ).map((row) => row.querySelector(".tag-management__cell--name")!.textContent);
}

describe("initTagManagementDocument", () => {
  it("starts with the bulk-delete trigger disabled and 0 selected", () => {
    buildPage();
    initTagManagementDocument(document);
    const { bulkTrigger, selectedCount } = getEls();
    expect(bulkTrigger.disabled).toBe(true);
    expect(selectedCount.textContent).toBe("0 selected");
  });

  it("updates the selected count and enables the bulk trigger when a row is checked", () => {
    buildPage();
    initTagManagementDocument(document);
    const { checkboxes, bulkTrigger, selectedCount } = getEls();

    checkboxes[0]!.checked = true;
    checkboxes[0]!.dispatchEvent(new Event("change"));

    expect(selectedCount.textContent).toBe("1 selected");
    expect(bulkTrigger.disabled).toBe(false);
  });

  it("disables the bulk trigger again once every row is unchecked", () => {
    buildPage();
    initTagManagementDocument(document);
    const { checkboxes, bulkTrigger } = getEls();

    checkboxes[0]!.checked = true;
    checkboxes[0]!.dispatchEvent(new Event("change"));
    checkboxes[0]!.checked = false;
    checkboxes[0]!.dispatchEvent(new Event("change"));

    expect(bulkTrigger.disabled).toBe(true);
  });

  it("select-all checks every currently visible row and updates the count", () => {
    buildPage();
    initTagManagementDocument(document);
    const { selectAll, checkboxes, selectedCount, bulkTrigger } = getEls();

    selectAll.checked = true;
    selectAll.dispatchEvent(new Event("change"));

    expect(checkboxes.every((cb) => cb.checked)).toBe(true);
    expect(selectedCount.textContent).toBe("3 selected");
    expect(bulkTrigger.disabled).toBe(false);
  });

  it("select-all unchecks every row when toggled off", () => {
    buildPage();
    initTagManagementDocument(document);
    const { selectAll, checkboxes } = getEls();

    selectAll.checked = true;
    selectAll.dispatchEvent(new Event("change"));
    selectAll.checked = false;
    selectAll.dispatchEvent(new Event("change"));

    expect(checkboxes.every((cb) => !cb.checked)).toBe(true);
  });

  it("marks select-all indeterminate for a partial selection", () => {
    buildPage();
    initTagManagementDocument(document);
    const { checkboxes, selectAll } = getEls();

    checkboxes[0]!.checked = true;
    checkboxes[0]!.dispatchEvent(new Event("change"));

    expect(selectAll.indeterminate).toBe(true);
    expect(selectAll.checked).toBe(false);
  });

  it("opens the dialog for a single delete trigger with its precomputed consequence text", () => {
    buildPage();
    initTagManagementDocument(document);
    const trigger = document.querySelectorAll<HTMLButtonElement>(
      "[data-tag-delete-trigger]",
    )[0]!;
    const showModal = (HTMLDialogElement.prototype.showModal = vi.fn());

    trigger.click();

    const { dialog } = getEls();
    const title = dialog.querySelector("[data-tag-delete-confirm-title]")!;
    const consequence = dialog.querySelector(
      "[data-tag-delete-confirm-consequence]",
    )!;
    const submit = dialog.querySelector("[data-tag-delete-confirm-submit]")!;
    expect(title.textContent).toBe('Delete "RED"?');
    expect(consequence.textContent).toBe(
      "Deleting this tag removes it from 2 notes. The notes themselves will not be deleted.",
    );
    expect(submit.getAttribute("form")).toBe("tag-delete-form-1");
    expect(showModal).toHaveBeenCalledTimes(1);
  });

  it("opens the dialog for bulk delete with the unique-note count and points the submit at the bulk form", () => {
    buildPage();
    initTagManagementDocument(document);
    const { checkboxes, bulkTrigger, dialog } = getEls();
    HTMLDialogElement.prototype.showModal = vi.fn();

    checkboxes[0]!.checked = true;
    checkboxes[0]!.dispatchEvent(new Event("change"));
    checkboxes[1]!.checked = true;
    checkboxes[1]!.dispatchEvent(new Event("change"));
    bulkTrigger.click();

    const title = dialog.querySelector("[data-tag-delete-confirm-title]")!;
    const consequence = dialog.querySelector(
      "[data-tag-delete-confirm-consequence]",
    )!;
    const submit = dialog.querySelector("[data-tag-delete-confirm-submit]")!;
    expect(title.textContent).toBe("Delete 2 selected tags?");
    // Rows carry note IDs "1,2" and "2,3" -- union is {1,2,3}, size 3.
    expect(consequence.textContent).toContain("3 notes");
    expect(submit.getAttribute("form")).toBe("tag-bulk-select-form");
  });

  it("bulk trigger does nothing if clicked with no selection (defensive, normally disabled)", () => {
    buildPage();
    initTagManagementDocument(document);
    const { bulkTrigger } = getEls();
    const showModal = (HTMLDialogElement.prototype.showModal = vi.fn());

    bulkTrigger.click();

    expect(showModal).not.toHaveBeenCalled();
  });

  it("cancel closes the dialog without submitting", () => {
    buildPage();
    initTagManagementDocument(document);
    const { dialog } = getEls();
    HTMLDialogElement.prototype.showModal = vi.fn();
    HTMLDialogElement.prototype.close = vi.fn(function (
      this: HTMLDialogElement,
    ) {
      this.open = false;
    });

    const trigger = document.querySelectorAll<HTMLButtonElement>(
      "[data-tag-delete-trigger]",
    )[0]!;
    trigger.click();
    const cancel = dialog.querySelector<HTMLButtonElement>(
      "[data-tag-delete-confirm-cancel]",
    )!;
    cancel.click();

    expect(dialog.close).toHaveBeenCalledTimes(1);
  });

  it("restores focus to the trigger when the dialog closes", () => {
    buildPage();
    initTagManagementDocument(document);
    HTMLDialogElement.prototype.showModal = vi.fn();
    const trigger = document.querySelectorAll<HTMLButtonElement>(
      "[data-tag-delete-trigger]",
    )[0]!;
    const focusSpy = vi.fn();
    trigger.focus = focusSpy;

    trigger.click();
    const { dialog } = getEls();
    dialog.dispatchEvent(new Event("close"));

    expect(focusSpy).toHaveBeenCalledTimes(1);
  });

  it("each row exposes exactly one ellipsis menu trigger, not permanently visible action buttons", () => {
    buildPage();
    initTagManagementDocument(document);
    const rows = document.querySelectorAll("[data-tag-row]");
    rows.forEach((row) => {
      expect(row.querySelectorAll(".row-action-menu").length).toBe(1);
      expect(
        row.querySelectorAll("button:not(.row-action-menu button)").length,
      ).toBe(0);
    });
  });

  it("the row menu exposes Rename, Recolor, and Delete", () => {
    buildPage();
    initTagManagementDocument(document);
    const menu = document.querySelector(".row-action-menu")!;
    const labels = Array.from(menu.querySelectorAll("button")).map(
      (button) => button.textContent,
    );
    expect(labels).toEqual(["Rename", "Recolor", "Delete"]);
  });

  it("clicking a Rename trigger reveals its editor row and closes the row menu", () => {
    buildPage();
    initTagManagementDocument(document);
    const menu = document.querySelector<HTMLDetailsElement>(
      '.row-action-menu[data-row-menu="1"]',
    )!;
    menu.open = true;
    const renameTrigger = menu.querySelector<HTMLElement>(
      '[data-tag-inline-edit-trigger][data-tag-inline-edit-target="tag-rename-row-1"]',
    )!;

    renameTrigger.click();

    const editor = document.getElementById("tag-rename-row-1")!;
    expect((editor as HTMLElement).hidden).toBe(false);
    expect(menu.open).toBe(false);
  });

  it("opening a second tag's editor hides any other tag's already-open editor", () => {
    buildPage();
    initTagManagementDocument(document);
    document
      .querySelector<HTMLElement>(
        '[data-tag-inline-edit-target="tag-rename-row-1"]',
      )!
      .click();
    document
      .querySelector<HTMLElement>(
        '[data-tag-inline-edit-target="tag-recolor-row-2"]',
      )!
      .click();

    expect(
      (document.getElementById("tag-rename-row-1") as HTMLElement).hidden,
    ).toBe(true);
    expect(
      (document.getElementById("tag-recolor-row-2") as HTMLElement).hidden,
    ).toBe(false);
  });

  it("cancel hides the editor row and restores focus to the trigger that opened it", () => {
    buildPage();
    initTagManagementDocument(document);
    const renameTrigger = document.querySelector<HTMLElement>(
      '[data-tag-inline-edit-target="tag-rename-row-1"]',
    )!;
    const focusSpy = vi.fn();
    renameTrigger.focus = focusSpy;
    renameTrigger.click();

    const editor = document.getElementById("tag-rename-row-1")!;
    editor.querySelector<HTMLElement>("[data-tag-inline-edit-cancel]")!.click();

    expect((editor as HTMLElement).hidden).toBe(true);
    expect(focusSpy).toHaveBeenCalledTimes(1);
  });

  it("clicking a single-delete trigger inside an open row menu closes that menu", () => {
    buildPage();
    initTagManagementDocument(document);
    HTMLDialogElement.prototype.showModal = vi.fn();
    const menu = document.querySelector<HTMLDetailsElement>(
      '.row-action-menu[data-row-menu="1"]',
    )!;
    menu.open = true;
    const deleteTrigger = menu.querySelector<HTMLElement>(
      "[data-tag-delete-trigger]",
    )!;

    deleteTrigger.click();

    expect(menu.open).toBe(false);
  });

  it("New tag trigger opens the new-tag editor", () => {
    buildPage();
    initTagManagementDocument(document);
    const trigger = document.querySelector<HTMLElement>(
      '[data-tag-inline-edit-target="tag-new-editor"]',
    )!;

    trigger.click();

    expect(
      (document.getElementById("tag-new-editor") as HTMLElement).hidden,
    ).toBe(false);
  });

  it("opening the New tag editor closes an already-open row editor", () => {
    buildPage();
    initTagManagementDocument(document);
    document
      .querySelector<HTMLElement>(
        '[data-tag-inline-edit-target="tag-rename-row-1"]',
      )!
      .click();

    document
      .querySelector<HTMLElement>(
        '[data-tag-inline-edit-target="tag-new-editor"]',
      )!
      .click();

    expect(
      (document.getElementById("tag-rename-row-1") as HTMLElement).hidden,
    ).toBe(true);
    expect(
      (document.getElementById("tag-new-editor") as HTMLElement).hidden,
    ).toBe(false);
  });

  it("opening a row editor closes an already-open New tag editor", () => {
    buildPage();
    initTagManagementDocument(document);
    document
      .querySelector<HTMLElement>(
        '[data-tag-inline-edit-target="tag-new-editor"]',
      )!
      .click();

    document
      .querySelector<HTMLElement>(
        '[data-tag-inline-edit-target="tag-rename-row-1"]',
      )!
      .click();

    expect(
      (document.getElementById("tag-new-editor") as HTMLElement).hidden,
    ).toBe(true);
    expect(
      (document.getElementById("tag-rename-row-1") as HTMLElement).hidden,
    ).toBe(false);
  });

  it("cancel closes the New tag editor", () => {
    buildPage();
    initTagManagementDocument(document);
    document
      .querySelector<HTMLElement>(
        '[data-tag-inline-edit-target="tag-new-editor"]',
      )!
      .click();
    const editor = document.getElementById("tag-new-editor")!;

    editor.querySelector<HTMLElement>("[data-tag-inline-edit-cancel]")!.click();

    expect((editor as HTMLElement).hidden).toBe(true);
  });

  it("opening Rename for one tag closes Recolor for the same tag, and vice versa", () => {
    buildPage();
    initTagManagementDocument(document);
    document
      .querySelector<HTMLElement>(
        '[data-tag-inline-edit-target="tag-recolor-row-1"]',
      )!
      .click();
    expect(
      (document.getElementById("tag-recolor-row-1") as HTMLElement).hidden,
    ).toBe(false);

    document
      .querySelector<HTMLElement>(
        '[data-tag-inline-edit-target="tag-rename-row-1"]',
      )!
      .click();

    expect(
      (document.getElementById("tag-recolor-row-1") as HTMLElement).hidden,
    ).toBe(true);
    expect(
      (document.getElementById("tag-rename-row-1") as HTMLElement).hidden,
    ).toBe(false);
  });

  it("is idempotent -- calling init twice does not double-bind listeners", () => {
    buildPage();
    initTagManagementDocument(document);
    initTagManagementDocument(document);
    const { checkboxes, selectedCount } = getEls();
    HTMLDialogElement.prototype.showModal = vi.fn();

    checkboxes[0]!.checked = true;
    checkboxes[0]!.dispatchEvent(new Event("change"));

    // If listeners were double-bound, the count text would be
    // duplicated/garbled rather than a clean "1 selected".
    expect(selectedCount.textContent).toBe("1 selected");
  });
});

// Client-side
// filtering/sorting, rather than a requestSubmit()-per-keystroke
// design, which would steal focus from the filter input mid-type.
describe("initTagManagementDocument -- dynamic client-side filter", () => {
  it("hides the Filter submit button once JS wiring runs, leaving it as a no-JS-only fallback", () => {
    buildPage();
    const submitButton = document.querySelector<HTMLElement>(
      "[data-tag-filter-submit]",
    )!;
    expect(submitButton.hidden).toBe(false);

    initTagManagementDocument(document);

    expect(submitButton.hidden).toBe(true);
  });

  it("typing does not submit/navigate the form", () => {
    buildPage();
    initTagManagementDocument(document);
    const form = document.querySelector<HTMLFormElement>(
      "[data-tag-filter-form]",
    )!;
    const requestSubmit = (form.requestSubmit = vi.fn());
    const submitSpy = vi.fn();
    form.addEventListener("submit", submitSpy);
    const input = document.querySelector<HTMLInputElement>(
      "[data-tag-filter-input]",
    )!;

    input.value = "RE";
    input.dispatchEvent(new Event("input"));
    vi.useFakeTimers();
    input.dispatchEvent(new Event("input"));
    vi.advanceTimersByTime(TAG_FILTER_DEBOUNCE_MS + 10);
    vi.useRealTimers();

    expect(requestSubmit).not.toHaveBeenCalled();
  });

  it("a real form submission (e.g. Enter key, no-JS fallback) is prevented once JS has initialized", () => {
    buildPage();
    initTagManagementDocument(document);
    const form = document.querySelector<HTMLFormElement>(
      "[data-tag-filter-form]",
    )!;
    const event = new Event("submit", { cancelable: true });

    form.dispatchEvent(event);

    expect(event.defaultPrevented).toBe(true);
  });

  it("input retains focus across a debounced filter application", () => {
    vi.useFakeTimers();
    buildPage();
    initTagManagementDocument(document);
    const input = document.querySelector<HTMLInputElement>(
      "[data-tag-filter-input]",
    )!;
    input.focus();
    expect(document.activeElement).toBe(input);

    input.value = "RE";
    input.dispatchEvent(new Event("input"));
    vi.advanceTimersByTime(TAG_FILTER_DEBOUNCE_MS + 10);

    expect(document.activeElement).toBe(input);
    vi.useRealTimers();
  });

  it("filters visible rows by a case-insensitive substring match, after the debounce", () => {
    vi.useFakeTimers();
    buildPage();
    initTagManagementDocument(document);
    const input = document.querySelector<HTMLInputElement>(
      "[data-tag-filter-input]",
    )!;

    input.value = "re";
    input.dispatchEvent(new Event("input"));
    vi.advanceTimersByTime(TAG_FILTER_DEBOUNCE_MS + 10);

    expect(visibleRowNames()).toEqual(["GREEN", "RED"]);
    vi.useRealTimers();
  });

  it("clearing the filter restores every row", () => {
    vi.useFakeTimers();
    buildPage();
    initTagManagementDocument(document);
    const input = document.querySelector<HTMLInputElement>(
      "[data-tag-filter-input]",
    )!;

    input.value = "red";
    input.dispatchEvent(new Event("input"));
    vi.advanceTimersByTime(TAG_FILTER_DEBOUNCE_MS + 10);
    expect(visibleRowNames()).toEqual(["RED"]);

    input.value = "";
    input.dispatchEvent(new Event("input"));
    vi.advanceTimersByTime(TAG_FILTER_DEBOUNCE_MS + 10);

    expect(visibleRowNames().sort()).toEqual(["BLUE", "GREEN", "RED"]);
    vi.useRealTimers();
  });

  it("updates the q query parameter via history.replaceState, without a navigation", () => {
    vi.useFakeTimers();
    buildPage();
    initTagManagementDocument(document);
    const replaceStateSpy = vi.spyOn(window.history, "replaceState");
    const input = document.querySelector<HTMLInputElement>(
      "[data-tag-filter-input]",
    )!;

    input.value = "red";
    input.dispatchEvent(new Event("input"));
    vi.advanceTimersByTime(TAG_FILTER_DEBOUNCE_MS + 10);

    expect(replaceStateSpy).toHaveBeenCalled();
    const [, , url] = replaceStateSpy.mock.calls.at(-1)!;
    expect(String(url)).toContain("q=red");
    vi.useRealTimers();
    replaceStateSpy.mockRestore();
  });

  it("a hidden (filtered-out) row is automatically deselected", () => {
    vi.useFakeTimers();
    buildPage();
    initTagManagementDocument(document);
    const { checkboxes, selectedCount } = getEls();
    checkboxes[0]!.checked = true; // RED
    checkboxes[0]!.dispatchEvent(new Event("change"));
    expect(selectedCount.textContent).toBe("1 selected");

    const input = document.querySelector<HTMLInputElement>(
      "[data-tag-filter-input]",
    )!;
    input.value = "blue";
    input.dispatchEvent(new Event("input"));
    vi.advanceTimersByTime(TAG_FILTER_DEBOUNCE_MS + 10);

    expect(checkboxes[0]!.checked).toBe(false);
    expect(selectedCount.textContent).toBe("0 selected");
    vi.useRealTimers();
  });

  it("select all after filtering selects only the currently visible rows", () => {
    vi.useFakeTimers();
    buildPage();
    initTagManagementDocument(document);
    const { selectAll, selectedCount } = getEls();
    const input = document.querySelector<HTMLInputElement>(
      "[data-tag-filter-input]",
    )!;

    input.value = "re";
    input.dispatchEvent(new Event("input"));
    vi.advanceTimersByTime(TAG_FILTER_DEBOUNCE_MS + 10);

    selectAll.checked = true;
    selectAll.dispatchEvent(new Event("change"));

    expect(selectedCount.textContent).toBe("2 selected");
    const blueCheckbox = document.querySelector<HTMLInputElement>(
      '[data-tag-select][aria-label="Select BLUE"]',
    )!;
    expect(blueCheckbox.checked).toBe(false);
    vi.useRealTimers();
  });
});

describe("initTagManagementDocument -- dynamic client-side sort", () => {
  it("sort change does not submit/navigate the form", () => {
    buildPage();
    initTagManagementDocument(document);
    const form = document.querySelector<HTMLFormElement>(
      "[data-tag-filter-form]",
    )!;
    const requestSubmit = (form.requestSubmit = vi.fn());
    const select = document.querySelector<HTMLSelectElement>(
      "[data-tag-sort-select]",
    )!;

    select.value = "name_desc";
    select.dispatchEvent(new Event("change"));

    expect(requestSubmit).not.toHaveBeenCalled();
  });

  it("applies immediately, no debounce", () => {
    vi.useFakeTimers();
    buildPage();
    initTagManagementDocument(document);
    const select = document.querySelector<HTMLSelectElement>(
      "[data-tag-sort-select]",
    )!;

    select.value = "name_desc";
    select.dispatchEvent(new Event("change"));

    expect(allRowNamesInDomOrder()).toEqual(["RED", "GREEN", "BLUE"]);
    vi.useRealTimers();
  });

  it("orders name_asc alphabetically", () => {
    buildPage();
    initTagManagementDocument(document);
    const select = document.querySelector<HTMLSelectElement>(
      "[data-tag-sort-select]",
    )!;
    select.value = "name_asc";
    select.dispatchEvent(new Event("change"));
    expect(allRowNamesInDomOrder()).toEqual(["BLUE", "GREEN", "RED"]);
  });

  it("orders name_desc reverse-alphabetically", () => {
    buildPage();
    initTagManagementDocument(document);
    const select = document.querySelector<HTMLSelectElement>(
      "[data-tag-sort-select]",
    )!;
    select.value = "name_desc";
    select.dispatchEvent(new Event("change"));
    expect(allRowNamesInDomOrder()).toEqual(["RED", "GREEN", "BLUE"]);
  });

  it("orders usage_asc by usage count, tie-broken by name ascending (BLUE=0, GREEN=0, RED=2)", () => {
    buildPage();
    initTagManagementDocument(document);
    const select = document.querySelector<HTMLSelectElement>(
      "[data-tag-sort-select]",
    )!;
    select.value = "usage_asc";
    select.dispatchEvent(new Event("change"));
    expect(allRowNamesInDomOrder()).toEqual(["BLUE", "GREEN", "RED"]);
  });

  it("orders usage_desc by usage count descending, tie-broken by name ascending (same as usage_asc's tiebreak)", () => {
    buildPage();
    initTagManagementDocument(document);
    const select = document.querySelector<HTMLSelectElement>(
      "[data-tag-sort-select]",
    )!;
    select.value = "usage_desc";
    select.dispatchEvent(new Event("change"));
    expect(allRowNamesInDomOrder()).toEqual(["RED", "BLUE", "GREEN"]);
  });

  it("keeps q filtering active across a sort change", () => {
    vi.useFakeTimers();
    buildPage();
    initTagManagementDocument(document);
    const input = document.querySelector<HTMLInputElement>(
      "[data-tag-filter-input]",
    )!;
    input.value = "re";
    input.dispatchEvent(new Event("input"));
    vi.advanceTimersByTime(TAG_FILTER_DEBOUNCE_MS + 10);
    vi.useRealTimers();

    const select = document.querySelector<HTMLSelectElement>(
      "[data-tag-sort-select]",
    )!;
    select.value = "name_desc";
    select.dispatchEvent(new Event("change"));

    expect(visibleRowNames()).toEqual(["RED", "GREEN"]);
  });

  it("updates the sort query parameter via history.replaceState, without a navigation", () => {
    buildPage();
    initTagManagementDocument(document);
    const replaceStateSpy = vi.spyOn(window.history, "replaceState");
    const select = document.querySelector<HTMLSelectElement>(
      "[data-tag-sort-select]",
    )!;

    select.value = "usage_desc";
    select.dispatchEvent(new Event("change"));

    expect(replaceStateSpy).toHaveBeenCalled();
    const [, , url] = replaceStateSpy.mock.calls.at(-1)!;
    expect(String(url)).toContain("sort=usage_desc");
    replaceStateSpy.mockRestore();
  });
});

// The New tag name
// field reuses the shared `tag-name-uppercase.ts` module verbatim
// -- these tests only confirm Tag manager's own field is wired to it (its
// own dedicated test file already covers the shared module's internals
// exhaustively) and that the color preview reacts on top of it.
describe("New tag editor -- live uppercase + color preview", () => {
  it("uppercases typed input live, reusing the shared script", () => {
    buildPage();
    initTagNameUppercaseDocument(document);
    initTagManagementDocument(document);
    const nameInput = document.querySelector<HTMLInputElement>(
      "#tag-new-editor [data-tag-name-input]",
    )!;

    nameInput.value = "server room";
    nameInput.dispatchEvent(new Event("input"));

    expect(nameInput.value).toBe("SERVER ROOM");
  });

  it("uppercases a pasted value the same way (paste already updates .value before 'input' fires)", () => {
    buildPage();
    initTagNameUppercaseDocument(document);
    initTagManagementDocument(document);
    const nameInput = document.querySelector<HTMLInputElement>(
      "#tag-new-editor [data-tag-name-input]",
    )!;

    nameInput.value = "pasted lowercase";
    nameInput.dispatchEvent(new Event("input"));

    expect(nameInput.value).toBe("PASTED LOWERCASE");
  });

  it("previews the Slate fallback for an ordinary name with no explicit color", () => {
    buildPage();
    initTagManagementDocument(document);
    const nameInput = document.querySelector<HTMLInputElement>(
      "#tag-new-editor [data-tag-name-input]",
    )!;
    const pill = document.querySelector<HTMLElement>(
      "[data-tag-color-preview]",
    )!;

    nameInput.value = "SERVER";
    nameInput.dispatchEvent(new Event("input"));

    expect(pill.getAttribute("data-tag-color")).toBe("slate");
    expect(pill.textContent).toBe("Slate");
  });

  it("previews a semantic color match (RED)", () => {
    buildPage();
    initTagManagementDocument(document);
    const nameInput = document.querySelector<HTMLInputElement>(
      "#tag-new-editor [data-tag-name-input]",
    )!;
    const pill = document.querySelector<HTMLElement>(
      "[data-tag-color-preview]",
    )!;

    nameInput.value = "RED";
    nameInput.dispatchEvent(new Event("input"));

    expect(pill.getAttribute("data-tag-color")).toBe("red");
    expect(pill.textContent).toBe("Red");
  });

  it("previews a semantic color match (TEAL)", () => {
    buildPage();
    initTagManagementDocument(document);
    const nameInput = document.querySelector<HTMLInputElement>(
      "#tag-new-editor [data-tag-name-input]",
    )!;
    const pill = document.querySelector<HTMLElement>(
      "[data-tag-color-preview]",
    )!;

    nameInput.value = "TEAL";
    nameInput.dispatchEvent(new Event("input"));

    expect(pill.getAttribute("data-tag-color")).toBe("teal");
    expect(pill.textContent).toBe("Teal");
  });

  it("an explicit color selection overrides the semantic preview", () => {
    buildPage();
    initTagManagementDocument(document);
    const nameInput = document.querySelector<HTMLInputElement>(
      "#tag-new-editor [data-tag-name-input]",
    )!;
    const colorSelect = document.querySelector<HTMLSelectElement>(
      "[data-tag-new-color-select]",
    )!;
    const pill = document.querySelector<HTMLElement>(
      "[data-tag-color-preview]",
    )!;

    nameInput.value = "RED";
    nameInput.dispatchEvent(new Event("input"));
    colorSelect.value = "violet";
    colorSelect.dispatchEvent(new Event("change"));

    expect(pill.getAttribute("data-tag-color")).toBe("violet");
    expect(pill.textContent).toBe("Violet");
  });
});

// The New tag editor reads the owner's actual
// preferences from `data-tag-semantic-enabled`/`data-tag-default-color`
// (server-rendered, no network call) instead of the hardcoded
// semantic-on/Slate assumption -- proves the plumbing, not just the
// pure `previewTagColor()` function already covered above.
describe("New tag editor -- preference plumbing", () => {
  it("skips the semantic preview entirely when data-tag-semantic-enabled is false", () => {
    buildPage();
    document
      .getElementById("tag-new-editor")!
      .setAttribute("data-tag-semantic-enabled", "false");
    initTagManagementDocument(document);
    const nameInput = document.querySelector<HTMLInputElement>(
      "#tag-new-editor [data-tag-name-input]",
    )!;
    const pill = document.querySelector<HTMLElement>(
      "[data-tag-color-preview]",
    )!;

    nameInput.value = "RED";
    nameInput.dispatchEvent(new Event("input"));

    expect(pill.getAttribute("data-tag-color")).toBe("slate");
  });

  it("uses a custom data-tag-default-color as the preview fallback", () => {
    buildPage();
    document
      .getElementById("tag-new-editor")!
      .setAttribute("data-tag-default-color", "violet");
    initTagManagementDocument(document);
    const nameInput = document.querySelector<HTMLInputElement>(
      "#tag-new-editor [data-tag-name-input]",
    )!;
    const pill = document.querySelector<HTMLElement>(
      "[data-tag-color-preview]",
    )!;

    nameInput.value = "SERVER";
    nameInput.dispatchEvent(new Event("input"));

    expect(pill.getAttribute("data-tag-color")).toBe("violet");
    expect(pill.textContent).toBe("Violet");
  });

  it("a semantic match still wins over a custom default color when semantic stays enabled", () => {
    buildPage();
    document
      .getElementById("tag-new-editor")!
      .setAttribute("data-tag-default-color", "violet");
    initTagManagementDocument(document);
    const nameInput = document.querySelector<HTMLInputElement>(
      "#tag-new-editor [data-tag-name-input]",
    )!;
    const pill = document.querySelector<HTMLElement>(
      "[data-tag-color-preview]",
    )!;

    nameInput.value = "RED";
    nameInput.dispatchEvent(new Event("input"));

    expect(pill.getAttribute("data-tag-color")).toBe("red");
  });

  it("initializes the preview pill to the resolved default immediately, before any typing", () => {
    buildPage();
    document
      .getElementById("tag-new-editor")!
      .setAttribute("data-tag-default-color", "teal");

    initTagManagementDocument(document);

    const pill = document.querySelector<HTMLElement>(
      "[data-tag-color-preview]",
    )!;
    expect(pill.getAttribute("data-tag-color")).toBe("teal");
  });
});

// When the owner's uppercase preference is off,
// the template omits data-tag-name-input entirely -- confirms the New
// tag field then behaves exactly like an ordinary text input (no live
// transform), which is the entire mechanism, with zero JS change.
describe("New tag editor -- uppercase preference disabled", () => {
  it("does not uppercase live when data-tag-name-input is absent", () => {
    buildPage();
    document
      .getElementById("tag-new-editor")!
      .querySelector("input[name='name']")!
      .removeAttribute("data-tag-name-input");
    initTagNameUppercaseDocument(document);
    initTagManagementDocument(document);
    const nameInput = document.querySelector<HTMLInputElement>(
      "#tag-new-editor input[name='name']",
    )!;

    nameInput.value = "server room";
    nameInput.dispatchEvent(new Event("input"));

    expect(nameInput.value).toBe("server room");
  });
});
