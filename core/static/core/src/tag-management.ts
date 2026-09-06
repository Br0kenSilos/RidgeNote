const SELECT_ALL_SELECTOR = "[data-tag-select-all]";
const ROW_SELECTOR = "[data-tag-select]";
const ROW_CONTAINER_SELECTOR = "[data-tag-row]";
const NAME_CELL_SELECTOR = ".tag-management__cell--name";
const EDITOR_ROW_CLASS = "tag-management__editor-row";
const SELECTED_COUNT_SELECTOR = "[data-tag-selected-count]";
const BULK_TRIGGER_SELECTOR = "[data-tag-bulk-delete-trigger]";
const SINGLE_TRIGGER_SELECTOR = "[data-tag-delete-trigger]";
const DIALOG_SELECTOR = "[data-tag-delete-confirm-dialog]";
const BULK_FORM_ID = "tag-bulk-select-form";
// The same
// `.row-action-menu` class this codebase's shared `tree-context-menu.ts`
// (`initRowActionMenusDocument`, already wired globally in `app.ts`)
// already looks for -- every Tag manager row's `⋮` trigger reuses that
// existing, already-tested menu primitive (floating/portal positioning,
// one-menu-open-at-a-time, outside-click/Escape/resize dismissal)
// verbatim. No new menu system, no duplicated JS.
const ROW_MENU_SELECTOR = ".row-action-menu";
const INLINE_EDIT_TRIGGER_SELECTOR = "[data-tag-inline-edit-trigger]";
const INLINE_EDIT_CANCEL_SELECTOR = "[data-tag-inline-edit-cancel]";
const INLINE_EDITOR_SELECTOR = "[data-tag-inline-editor]";
const FILTER_FORM_SELECTOR = "[data-tag-filter-form]";
const FILTER_INPUT_SELECTOR = "[data-tag-filter-input]";
const SORT_SELECT_SELECTOR = "[data-tag-sort-select]";
const FILTER_SUBMIT_SELECTOR = "[data-tag-filter-submit]";
const CLEAR_LINK_SELECTOR = ".tag-management__clear-link";
const TABLE_BODY_SELECTOR = ".tag-management__table tbody";
const NEW_TAG_EDITOR_SELECTOR = "[data-tag-new-editor]";
const NEW_TAG_NAME_INPUT_SELECTOR = "[data-tag-name-input]";
const NEW_TAG_COLOR_SELECT_SELECTOR = "[data-tag-new-color-select]";
const PREVIEW_PILL_SELECTOR = "[data-tag-color-preview]";

// 200ms. Filtering/sorting are pure client-side
// operations over already-rendered rows (no request in flight), so the
// debounce exists only to avoid re-filtering on every single keystroke,
// not to throttle network calls.
export const TAG_FILTER_DEBOUNCE_MS = 200;

/** Pure helper: the union of comma-separated note-ID lists carried by
 * every selected row's `data-tag-note-ids` -- the "unique notes
 * affected" count for a bulk-delete confirmation, computed entirely
 * client-side from data already rendered on the page (no extra
 * request), since a realistic owner tag vocabulary is small. Exported
 * for direct unit testing. */
export function uniqueNoteCount(noteIdLists: readonly string[]): number {
  const ids = new Set<string>();
  for (const list of noteIdLists) {
    for (const id of list.split(",")) {
      const trimmed = id.trim();
      if (trimmed) {
        ids.add(trimmed);
      }
    }
  }
  return ids.size;
}

function noun(count: number, singular: string, plural: string): string {
  return count === 1 ? singular : plural;
}

// New tag color
// preview. This table and `DEFAULT_TAG_COLOR` deliberately mirror
// `notes/services.py`'s `_SEMANTIC_TAG_COLOR_ALIASES`/`DEFAULT_TAG_COLOR`
// exactly (same 15 aliases onto the same 11 stored colors) so the preview
// shown before submission matches what `resolve_tag_color_for_creation()`
// will actually store. This is a **preview-only** mirror -- the server
// remains the sole authority on the color actually stored, and creation
// never trusts this table; a focused parity test enumerates the exact
// same alias list to keep the two from silently drifting apart.
const SEMANTIC_TAG_COLOR_ALIASES: Readonly<Record<string, string>> = {
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

const DEFAULT_TAG_COLOR = "slate";

// Mirrors `notes/models.py`'s `TAG_COLOR_CHOICES` labels exactly (same 11
// values, same display strings) -- presentation only, for the preview
// pill's text. The server's own `color_label` (via `TAG_COLOR_CHOICES`)
// remains authoritative for every already-created tag's own pill.
const TAG_COLOR_LABELS: Readonly<Record<string, string>> = {
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

/** Pure helper, exported for direct unit testing: the same trim +
 * whitespace-collapse + uppercase shape `normalize_tag_name()` applies
 * server-side, used here only to key the preview's semantic-alias
 * lookup -- never used to validate or reject characters, and never the
 * value actually submitted (the real `<input>` value is). */
export function normalizeTagNameForPreview(raw: string): string {
  return raw.trim().replace(/\s+/g, " ").toUpperCase();
}

/** Pure helper, exported for direct unit testing: the exact
 * `resolve_tag_color_for_creation()` precedence -- explicit color, then
 * a semantic match on the normalized name (only when `semanticEnabled`,
 * mirroring the owner's `tag_semantic_color_enabled` preference), then
 * `defaultColor` (the owner's configured `tag_default_color`,
 * server-rendered onto the page -- see `wireNewTagPreview()`). Defaults
 * to today's shipped behavior (semantic on, Slate) so any caller that
 * doesn't pass options still
 * behaves exactly as before. Preview-only; never authoritative and
 * never itself creates or mutates anything. */
export function previewTagColor(
  name: string,
  explicitColor: string,
  options: { semanticEnabled?: boolean; defaultColor?: string } = {},
): string {
  const { semanticEnabled = true, defaultColor = DEFAULT_TAG_COLOR } = options;
  if (explicitColor) {
    return explicitColor;
  }
  if (semanticEnabled) {
    const normalized = normalizeTagNameForPreview(name);
    const matched = SEMANTIC_TAG_COLOR_ALIASES[normalized];
    if (matched) {
      return matched;
    }
  }
  return defaultColor;
}

/** Pure helper, exported for direct unit testing. */
export function tagColorLabel(color: string): string {
  return TAG_COLOR_LABELS[color] ?? color;
}

/**
 * Rename/Recolor
 * live outside the `⋮` menu's own panel, in a compact inline editor row
 * directly beneath the tag's own row (a "collapse back
 * to the normal compact row on Save/Cancel" design) -- selecting either
 * from the menu closes the menu (never left open simultaneously with the
 * editor) and reveals that one hidden `<tr data-tag-inline-editor
 * hidden>` sibling; any other currently-open editor closes first, so at
 * most one is ever visible per page. The New tag
 * editor is part of this exact same set (it shares `data-tag-inline-editor`),
 * so it participates in the same mutual-exclusion for free. Save is a
 * real POST + redirect (collapse happens for free on the next page
 * load); Cancel is the only case needing JS, restoring focus to the `⋮`
 * trigger that opened it.
 */
function wireInlineEditors(doc: Document): void {
  const triggers = Array.from(
    doc.querySelectorAll<HTMLElement>(INLINE_EDIT_TRIGGER_SELECTOR),
  );
  if (triggers.length === 0) {
    return;
  }

  const restoreFocusByEditorId = new Map<string, HTMLElement>();

  function closeEditor(editor: HTMLElement): void {
    editor.hidden = true;
    const trigger = restoreFocusByEditorId.get(editor.id);
    trigger?.focus();
  }

  triggers.forEach((trigger) => {
    trigger.addEventListener("click", () => {
      const targetId = trigger.dataset.tagInlineEditTarget ?? "";
      const target = doc.getElementById(targetId);
      if (!target) {
        return;
      }

      const menu = trigger.closest<HTMLDetailsElement>(ROW_MENU_SELECTOR);
      if (menu) {
        menu.open = false;
      }

      doc
        .querySelectorAll<HTMLElement>(INLINE_EDITOR_SELECTOR)
        .forEach((editor) => {
          if (editor !== target && !editor.hidden) {
            editor.hidden = true;
          }
        });

      restoreFocusByEditorId.set(targetId, trigger);
      target.hidden = false;
      target.querySelector<HTMLElement>("input, select")?.focus();
    });
  });

  const cancelButtons = Array.from(
    doc.querySelectorAll<HTMLElement>(INLINE_EDIT_CANCEL_SELECTOR),
  );
  cancelButtons.forEach((cancelButton) => {
    cancelButton.addEventListener("click", () => {
      const editor = cancelButton.closest<HTMLElement>(INLINE_EDITOR_SELECTOR);
      if (editor) {
        closeEditor(editor);
      }
    });
  });
}

/**
 * Reuses the
 * shared live-uppercase input (`[data-tag-name-input]`,
 * `tag-name-uppercase.ts`, already globally wired in `app.ts` and run
 * *before* this module's own init) for the New tag name field -- no
 * second uppercase implementation. That element is only present at all
 * when the owner's `tag_uppercase_enabled` preference is on (the
 * template omits the attribute entirely when it's off, the
 * narrowest possible mechanism -- no JS-side enabled/disabled flag
 * needed here). This function only adds the color preview on top: it
 * reads whatever the shared script already wrote to `.value` (already
 * uppercased by the time this listener runs, since listener execution
 * follows registration order on the same event, when uppercase is on)
 * and the currently selected color, and updates the read-only preview
 * pill to show exactly the color `resolve_tag_color_for_creation()`
 * would store if submitted right now. Deliberately never calls the
 * server -- `previewTagColor()` is a pure, local mirror.
 *
 * `semanticEnabled`/`defaultColor` are read once
 * from the New tag editor's own `data-tag-semantic-enabled`/
 * `data-tag-default-color` attributes -- server-rendered from the
 * owner's actual preferences, not hardcoded, not fetched. No network
 * request is ever made for this.
 */
function wireNewTagPreview(doc: Document): void {
  const editor = doc.querySelector<HTMLElement>(NEW_TAG_EDITOR_SELECTOR);
  if (!editor) {
    return;
  }
  const nameInput = editor.querySelector<HTMLInputElement>(
    NEW_TAG_NAME_INPUT_SELECTOR,
  );
  const colorSelect = editor.querySelector<HTMLSelectElement>(
    NEW_TAG_COLOR_SELECT_SELECTOR,
  );
  const pill = editor.querySelector<HTMLElement>(PREVIEW_PILL_SELECTOR);
  if (!nameInput || !colorSelect || !pill) {
    return;
  }

  const semanticEnabled = editor.dataset.tagSemanticEnabled !== "false";
  const defaultColor = editor.dataset.tagDefaultColor || DEFAULT_TAG_COLOR;

  function refresh(): void {
    const color = previewTagColor(nameInput!.value, colorSelect!.value, {
      semanticEnabled,
      defaultColor,
    });
    pill!.setAttribute("data-tag-color", color);
    pill!.textContent = tagColorLabel(color);
  }

  nameInput.addEventListener("input", refresh);
  colorSelect.addEventListener("change", refresh);
  refresh();
}

/**
 * Every mutating
 * form on this page (Rename/Recolor/Delete/bulk-delete/New tag) carries
 * a hidden `next` field so its POST redirects back to "wherever the
 * user currently is." That used to mean the server-rendered
 * `current_path` captured at the last full page load -- stale as soon
 * as filtering/sorting became client-side-only (no new GET request).
 * This listens for *any* form submission on the page and, immediately
 * before it leaves, refreshes that form's own `next` field (if it has
 * one) to the URL actually showing right now -- including whatever `q`/
 * `sort` `wireDynamicFilterAndSort()`'s own `history.replaceState()`
 * calls have already written -- so a create/rename/recolor/delete
 * redirect always lands back on the filter/sort state the user was
 * actually looking at, not a stale one. A single generic listener
 * rather than duplicating this per form.
 */
function wireNextFieldSync(doc: Document): void {
  doc.addEventListener("submit", (event) => {
    const form = event.target;
    if (!(form instanceof HTMLFormElement)) {
      return;
    }
    const nextInput =
      form.querySelector<HTMLInputElement>('input[name="next"]');
    if (nextInput) {
      nextInput.value = window.location.pathname + window.location.search;
    }
  });
}

interface TagRowGroup {
  row: HTMLTableRowElement;
  editors: HTMLTableRowElement[];
  name: string;
  usageCount: number;
}

// Captured once,
// at init, from the server-rendered `<tbody>` -- every tag row is
// followed immediately by its own two (initially hidden) Rename/Recolor
// `<tr class="tag-management__editor-row">` siblings, so this walk never
// needs to match rows to editors by ID. These groups are reused for
// every subsequent client-side filter/sort pass; DOM nodes are moved
// (not cloned/recreated), so event listeners and any open editor state
// survive a reorder untouched.
function collectRowGroups(tbody: HTMLElement): TagRowGroup[] {
  const rows = Array.from(
    tbody.querySelectorAll<HTMLTableRowElement>(ROW_CONTAINER_SELECTOR),
  );
  return rows.map((row) => {
    const editors: HTMLTableRowElement[] = [];
    let sibling = row.nextElementSibling;
    while (sibling && sibling.classList.contains(EDITOR_ROW_CLASS)) {
      editors.push(sibling as HTMLTableRowElement);
      sibling = sibling.nextElementSibling;
    }
    const nameCell = row.querySelector<HTMLElement>(NAME_CELL_SELECTOR);
    return {
      row,
      editors,
      name: nameCell?.textContent?.trim() ?? "",
      usageCount: Number(row.dataset.tagUsageCount ?? "0"),
    };
  });
}

function compareNames(a: string, b: string): number {
  const aLower = a.toLowerCase();
  const bLower = b.toLowerCase();
  if (aLower < bLower) {
    return -1;
  }
  if (aLower > bLower) {
    return 1;
  }
  return 0;
}

// Mirrors `notes/services.py`'s `_TAG_MANAGEMENT_ORDER_BY` exactly,
// including that *both* usage sorts break ties on name **ascending**
// (never descending) -- the same deterministic tiebreak the server uses,
// so client-side reordering never disagrees with what a fresh page load
// under the same `sort` would show.
function sortGroups(groups: TagRowGroup[], sort: string): TagRowGroup[] {
  const sorted = [...groups];
  switch (sort) {
    case "name_desc":
      sorted.sort((a, b) => -compareNames(a.name, b.name));
      break;
    case "usage_asc":
      sorted.sort(
        (a, b) => a.usageCount - b.usageCount || compareNames(a.name, b.name),
      );
      break;
    case "usage_desc":
      sorted.sort(
        (a, b) => b.usageCount - a.usageCount || compareNames(a.name, b.name),
      );
      break;
    case "name_asc":
    default:
      sorted.sort((a, b) => compareNames(a.name, b.name));
      break;
  }
  return sorted;
}

// Filtering is a
// case-insensitive substring match against the rendered name text --
// equivalent to the server's `name__icontains` -- applied purely
// client-side (toggling `hidden`, never removing rows from the DOM) so
// no request/navigation/focus loss occurs while typing. A row that
// becomes hidden also force-closes any open Rename/Recolor editor
// beneath it and auto-deselects its checkbox, keeping the existing
// "visible rows only" selection contract unambiguous. Sorting always
// reruns together with filtering (moving every group, hidden or not) so
// clearing the filter never reveals a stale order.
function applyFilterAndSort(
  groups: TagRowGroup[],
  tbody: HTMLElement,
  query: string,
  sort: string,
): void {
  const sorted = sortGroups(groups, sort);
  const q = query.trim().toLowerCase();
  const fragment = tbody.ownerDocument.createDocumentFragment();

  sorted.forEach((group) => {
    const matches = q === "" || group.name.toLowerCase().includes(q);
    group.row.hidden = !matches;
    if (!matches) {
      group.editors.forEach((editor) => {
        editor.hidden = true;
      });
      const checkbox = group.row.querySelector<HTMLInputElement>(ROW_SELECTOR);
      if (checkbox) {
        checkbox.checked = false;
      }
    }
    fragment.appendChild(group.row);
    group.editors.forEach((editor) => fragment.appendChild(editor));
  });

  tbody.appendChild(fragment);
}

/**
 * Pure client-side filtering/sorting over
 * the already-rendered, unpaginated row set -- no request, no
 * navigation, no focus loss (a real navigation on every debounce firing
 * would steal focus from the filter input mid-type). The server
 * `?q=`/`?sort=` GET contract is
 * untouched and remains authoritative for initial load, refresh, and
 * the no-JS fallback (the visible Filter submit button, hidden only once
 * this wiring actually succeeds); `history.replaceState()` keeps the URL
 * in sync purely for refresh/bookmark/back-button meaningfulness, never
 * triggering a request itself. The sort `<select>` applies immediately
 * (no debounce) and always re-applies whatever `q` currently holds, so a
 * sort change never drops an in-progress filter.
 */
function wireDynamicFilterAndSort(
  doc: Document,
  updateSelectionUi: () => void,
): void {
  const form = doc.querySelector<HTMLFormElement>(FILTER_FORM_SELECTOR);
  if (!form) {
    return;
  }

  const input = form.querySelector<HTMLInputElement>(FILTER_INPUT_SELECTOR);
  const sortSelect =
    form.querySelector<HTMLSelectElement>(SORT_SELECT_SELECTOR);
  const submitButton = form.querySelector<HTMLElement>(FILTER_SUBMIT_SELECTOR);
  const tbody = doc.querySelector<HTMLElement>(TABLE_BODY_SELECTOR);
  const clearLink = doc.querySelector<HTMLAnchorElement>(CLEAR_LINK_SELECTOR);

  if (submitButton) {
    submitButton.hidden = true;
  }
  // No full navigation once JS has taken over -- including an Enter
  // keypress in the filter input, which would otherwise still submit
  // the (still-real, no-JS-fallback) GET form.
  form.addEventListener("submit", (event) => {
    event.preventDefault();
  });

  if (!input || !sortSelect || !tbody) {
    return;
  }

  const groups = collectRowGroups(tbody);

  function syncUrl(): void {
    const url = new URL(window.location.href);
    const q = input!.value.trim();
    if (q) {
      url.searchParams.set("q", q);
    } else {
      url.searchParams.delete("q");
    }
    url.searchParams.set("sort", sortSelect!.value);
    window.history.replaceState(null, "", `${url.pathname}${url.search}`);

    if (clearLink) {
      const clearUrl = new URL(clearLink.href, window.location.href);
      clearUrl.searchParams.set("sort", sortSelect!.value);
      clearLink.href = `${clearUrl.pathname}${clearUrl.search}`;
    }
  }

  function apply(): void {
    applyFilterAndSort(groups, tbody!, input!.value, sortSelect!.value);
    updateSelectionUi();
  }

  let debounceTimer: ReturnType<typeof setTimeout> | undefined;

  input.addEventListener("input", () => {
    if (debounceTimer !== undefined) {
      clearTimeout(debounceTimer);
    }
    debounceTimer = setTimeout(() => {
      apply();
      syncUrl();
    }, TAG_FILTER_DEBOUNCE_MS);
  });

  sortSelect.addEventListener("change", () => {
    if (debounceTimer !== undefined) {
      clearTimeout(debounceTimer);
      debounceTimer = undefined;
    }
    apply();
    syncUrl();
  });
}

/**
 * Checkbox selection + select-all (currently
 * *visible* rows only -- rows
 * can be hidden by client-side filtering, not just "there is no
 * pagination") + selected-count `aria-live` feedback + the bulk-delete
 * trigger's enabled state, plus wiring both the single-tag and bulk
 * delete triggers into one shared confirmation `<dialog>`, modeled
 * directly on `delete-confirm.ts`'s established shape (`showModal()`,
 * backdrop/Escape close, focus restored to the trigger on close). Unlike
 * that dialog, this one never copies hidden fields onto itself -- its
 * "Delete" button's own `form` attribute is repointed at whichever real
 * `<form>` should actually submit (a single tag's own tiny delete form,
 * or the page's one shared `#tag-bulk-select-form` that every checkbox
 * is already associated with via its own `form=` attribute), so no
 * mutation logic is duplicated in JavaScript.
 */
export function initTagManagementDocument(doc: Document = document): boolean {
  const selectAll = doc.querySelector<HTMLInputElement>(SELECT_ALL_SELECTOR);
  const rows = Array.from(doc.querySelectorAll<HTMLInputElement>(ROW_SELECTOR));
  const selectedCountEl = doc.querySelector<HTMLElement>(
    SELECTED_COUNT_SELECTOR,
  );
  const bulkTrigger = doc.querySelector<HTMLButtonElement>(
    BULK_TRIGGER_SELECTOR,
  );
  const dialog = doc.querySelector<HTMLDialogElement>(DIALOG_SELECTOR);

  if (rows.length === 0 && !dialog) {
    return false;
  }

  function isRowVisible(checkbox: HTMLInputElement): boolean {
    const row = checkbox.closest<HTMLElement>(ROW_CONTAINER_SELECTOR);
    return !row?.hidden;
  }

  function visibleRows(): HTMLInputElement[] {
    return rows.filter(isRowVisible);
  }

  function selectedRows(): HTMLInputElement[] {
    return visibleRows().filter((row) => row.checked);
  }

  function updateSelectionUi(): void {
    const visible = visibleRows();
    const count = visible.filter((row) => row.checked).length;
    if (selectedCountEl) {
      selectedCountEl.textContent = `${count} selected`;
    }
    if (bulkTrigger) {
      bulkTrigger.disabled = count === 0;
    }
    if (selectAll) {
      selectAll.checked = visible.length > 0 && count === visible.length;
      selectAll.indeterminate = count > 0 && count < visible.length;
    }
  }

  rows.forEach((row) => {
    row.addEventListener("change", updateSelectionUi);
  });

  selectAll?.addEventListener("change", () => {
    visibleRows().forEach((row) => {
      row.checked = selectAll.checked;
    });
    updateSelectionUi();
  });

  updateSelectionUi();
  wireInlineEditors(doc);
  wireNewTagPreview(doc);
  wireNextFieldSync(doc);
  wireDynamicFilterAndSort(doc, updateSelectionUi);

  if (!dialog) {
    return true;
  }

  const titleEl = dialog.querySelector<HTMLElement>(
    "[data-tag-delete-confirm-title]",
  );
  const consequenceEl = dialog.querySelector<HTMLElement>(
    "[data-tag-delete-confirm-consequence]",
  );
  const cancelBtn = dialog.querySelector<HTMLElement>(
    "[data-tag-delete-confirm-cancel]",
  );
  const submitBtn = dialog.querySelector<HTMLButtonElement>(
    "[data-tag-delete-confirm-submit]",
  );
  if (!titleEl || !consequenceEl || !cancelBtn || !submitBtn) {
    return true;
  }

  let restoreFocusTarget: HTMLElement | null = null;

  function openDialog(target: HTMLElement): void {
    restoreFocusTarget = target;
    dialog!.showModal();
  }

  const singleTriggers = Array.from(
    doc.querySelectorAll<HTMLElement>(SINGLE_TRIGGER_SELECTOR),
  );
  singleTriggers.forEach((trigger) => {
    trigger.addEventListener("click", () => {
      // The trigger lives inside an open `.row-action-menu` panel; that
      // menu has no reason to stay open behind the confirmation dialog,
      // mirroring `delete-confirm.ts`'s own established handling of the
      // exact same situation for note/folder Delete triggers.
      const menu = trigger.closest<HTMLDetailsElement>(ROW_MENU_SELECTOR);
      if (menu) {
        menu.open = false;
      }

      const name = trigger.dataset.tagDeleteName ?? "";
      const formId = trigger.dataset.tagDeleteFormId ?? "";
      titleEl.textContent = `Delete "${name}"?`;
      consequenceEl.textContent = trigger.dataset.tagDeleteConsequence ?? "";
      submitBtn.setAttribute("form", formId);
      openDialog(trigger);
    });
  });

  if (bulkTrigger) {
    bulkTrigger.addEventListener("click", () => {
      const selected = selectedRows();
      const tagCount = selected.length;
      if (tagCount === 0) {
        return;
      }
      const noteIdLists = selected
        .map((row) => row.closest<HTMLElement>(ROW_CONTAINER_SELECTOR))
        .map((row) => row?.dataset.tagNoteIds ?? "");
      const noteCount = uniqueNoteCount(noteIdLists);

      titleEl.textContent = `Delete ${tagCount} selected ${noun(tagCount, "tag", "tags")}?`;
      consequenceEl.textContent =
        noteCount === 0
          ? "This removes them from your tag list. The notes themselves will not be deleted."
          : `This removes them from ${noteCount} ${noun(noteCount, "note", "notes")}. The notes themselves will not be deleted.`;
      submitBtn.setAttribute("form", BULK_FORM_ID);
      openDialog(bulkTrigger);
    });
  }

  cancelBtn.addEventListener("click", () => {
    dialog.close();
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });

  dialog.addEventListener("close", () => {
    restoreFocusTarget?.focus();
    restoreFocusTarget = null;
  });

  return true;
}
