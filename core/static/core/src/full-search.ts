const FORM_SELECTOR = "[data-full-search-form]";
const TAG_PICKER_SELECTOR = "[data-full-search-tag-picker]";
const TAG_COMBOBOX_SELECTOR = "[data-full-search-tag-combobox]";
const TAG_FILTER_INPUT_SELECTOR = "[data-full-search-tag-filter]";
const TAG_PANEL_SELECTOR = "[data-full-search-tag-panel]";
const TAG_LIST_SELECTOR = "[data-full-search-tag-list]";
const TAG_CHECKBOX_SELECTOR = "[data-full-search-tag-checkbox]";
const TAG_OPTION_SELECTOR = "[data-full-search-tag-name]";
const TAG_STATUS_SELECTOR = "[data-full-search-tag-status]";
const TAG_LIMIT_NOTE_SELECTOR = "[data-full-search-tag-limit-note]";
const TAG_CHIPS_SELECTOR = "[data-full-search-tag-chips]";
const TAG_COLOR_ATTR_SELECTOR = "[data-tag-color]";

// Aligned with the server-side
// `MAX_TAGS_PER_NOTE`/`MAX_FULL_SEARCH_TAGS` limit. This client-side
// enforcement is a UX nicety only -- the server remains authoritative
// and independently rejects more than this many selected Tags.
const MAX_SELECTED_TAGS = 20;

/**
 * Whether a Tag's name matches the (already lower-cased, trimmed) filter
 * query. An empty query matches everything -- a pure function, kept
 * separate from the DOM so it's directly unit-testable.
 */
export function matchesTagFilter(tagName: string, query: string): boolean {
  const trimmed = query.trim().toLowerCase();
  if (!trimmed) return true;
  return tagName.toLowerCase().includes(trimmed);
}

/**
 * Full
 * Search's progressive-enhancement layer. Everything here is a UX
 * nicety on top of an already-fully-functional plain GET `<form>`:
 *
 * - with JS, the bounded checkbox list (the unconditional no-JS
 *   fallback -- always present in the server-rendered HTML, never
 *   hidden by the server) is collapsed into a compact popover panel
 *   opened by focusing/clicking the Tag filter input, closed on
 *   outside click or Escape;
 * - selected Tags are mirrored as small removable chips below the
 *   combobox, built entirely from the already-rendered checkbox/label
 *   markup (Tag name + stored color) -- no new network request;
 * - a soft max-20 selection cap and the existing Tag-status "Without
 *   tags" disable/clear behavior are unchanged in substance, just
 *   re-wired to also keep the chips row in sync.
 *
 * No search criterion is ever introduced that only exists in
 * client-side state -- every control here is a real, named form field
 * (the checkboxes) the server already validates independently; the
 * chips and popover are purely a presentation layer over them.
 */
export function initFullSearchDocument(doc: Document = document): boolean {
  const form = doc.querySelector<HTMLFormElement>(FORM_SELECTOR);
  if (!form) return false;

  const tagPicker =
    form.querySelector<HTMLFieldSetElement>(TAG_PICKER_SELECTOR);
  const combobox = form.querySelector<HTMLElement>(TAG_COMBOBOX_SELECTOR);
  const tagFilterInput = form.querySelector<HTMLInputElement>(
    TAG_FILTER_INPUT_SELECTOR,
  );
  const tagPanel = form.querySelector<HTMLElement>(TAG_PANEL_SELECTOR);
  const tagList = form.querySelector<HTMLElement>(TAG_LIST_SELECTOR);
  const tagStatusSelect =
    form.querySelector<HTMLSelectElement>(TAG_STATUS_SELECTOR);
  const limitNote = form.querySelector<HTMLElement>(TAG_LIMIT_NOTE_SELECTOR);
  const chipsContainer = form.querySelector<HTMLElement>(TAG_CHIPS_SELECTOR);

  const checkboxes = (): HTMLInputElement[] =>
    tagList
      ? Array.from(
          tagList.querySelectorAll<HTMLInputElement>(TAG_CHECKBOX_SELECTOR),
        )
      : [];

  const syncChips = () => {
    if (!chipsContainer) return;
    chipsContainer.replaceChildren();
    checkboxes()
      .filter((box) => box.checked)
      .forEach((box) => {
        const option = box.closest<HTMLElement>(TAG_OPTION_SELECTOR);
        const name = option?.dataset.fullSearchTagName ?? "";
        const colorEl = option?.querySelector<HTMLElement>(
          TAG_COLOR_ATTR_SELECTOR,
        );
        const color = colorEl?.dataset.tagColor ?? "slate";

        const chip = doc.createElement("span");
        chip.className = "note-list__tag-chip full-search-form__tag-chip";
        chip.dataset.tagColor = color;

        const label = doc.createElement("span");
        label.className = "note-list__tag-chip__label";
        label.textContent = name;
        chip.appendChild(label);

        const removeButton = doc.createElement("button");
        removeButton.type = "button";
        removeButton.className = "full-search-form__tag-chip-remove";
        removeButton.setAttribute("aria-label", `Remove ${name}`);
        removeButton.textContent = "×";
        removeButton.addEventListener("click", () => {
          box.checked = false;
          box.dispatchEvent(new Event("change"));
        });
        chip.appendChild(removeButton);

        chipsContainer.appendChild(chip);
      });
  };

  const enforceMaxSelection = () => {
    const boxes = checkboxes();
    const atMax =
      boxes.filter((box) => box.checked).length >= MAX_SELECTED_TAGS;
    boxes.forEach((box) => {
      if (!box.checked) box.disabled = atMax;
    });
    if (limitNote) limitNote.hidden = !atMax;
  };

  if (tagFilterInput && tagList) {
    tagFilterInput.hidden = false;
    tagFilterInput.addEventListener("input", () => {
      tagList
        .querySelectorAll<HTMLElement>(TAG_OPTION_SELECTOR)
        .forEach((option) => {
          const name = option.dataset.fullSearchTagName ?? "";
          option.hidden = !matchesTagFilter(name, tagFilterInput.value);
        });
    });
  }

  if (tagList) {
    checkboxes().forEach((box) =>
      box.addEventListener("change", () => {
        enforceMaxSelection();
        syncChips();
      }),
    );
    enforceMaxSelection();
    syncChips();
  }

  // Collapses the always-visible no-JS checkbox list into a popover,
  // opened by focusing/clicking the filter input and closed on Escape
  // or an outside click -- a self-contained disclosure, not a reuse of
  // the row-action-menu portal mechanism (that component tears down on
  // its own item clicks, which would fight this panel's persistent
  // multi-select checkboxes).
  if (combobox && tagFilterInput && tagPanel) {
    combobox.classList.add("full-search-form__tag-combobox--js");

    const openPanel = () => {
      tagPanel.hidden = false;
      tagFilterInput.setAttribute("aria-expanded", "true");
    };
    const closePanel = () => {
      tagPanel.hidden = true;
      tagFilterInput.setAttribute("aria-expanded", "false");
    };

    closePanel();

    tagFilterInput.addEventListener("focus", openPanel);
    tagFilterInput.addEventListener("click", openPanel);

    doc.addEventListener("click", (event) => {
      if (!combobox.contains(event.target as Node)) closePanel();
    });

    combobox.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        closePanel();
        tagFilterInput.blur();
      }
    });
  }

  if (tagStatusSelect && tagPicker) {
    const applyTagStatusState = (untagged: boolean) => {
      tagPicker.disabled = untagged;
      if (untagged && tagPanel) tagPanel.hidden = true;
    };

    applyTagStatusState(tagStatusSelect.value === "untagged");

    tagStatusSelect.addEventListener("change", () => {
      const untagged = tagStatusSelect.value === "untagged";
      applyTagStatusState(untagged);
      if (untagged) {
        checkboxes().forEach((box) => {
          box.checked = false;
        });
        enforceMaxSelection();
        syncChips();
      }
    });
  }

  return true;
}
