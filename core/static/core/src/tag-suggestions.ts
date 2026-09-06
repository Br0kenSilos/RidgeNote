export const TAG_SUGGESTIONS_DEBOUNCE_MS = 250;

const FORM_SELECTOR = "[data-tag-suggestions-url]";
const INPUT_SELECTOR = "[data-tag-name-input]";
const PANEL_SELECTOR = "[data-tag-suggestions-panel]";

export interface TagSuggestionExisting {
  id: number;
  name: string;
  color: string;
}

export interface TagSuggestionsSuccessResponse {
  ok: true;
  existing: TagSuggestionExisting[];
  at_tag_limit: boolean;
}

export interface TagSuggestionsFailureResponse {
  ok: false;
}

export type TagSuggestionsResponse =
  | TagSuggestionsFailureResponse
  | TagSuggestionsSuccessResponse;

export interface TagSuggestionsFetchResponseLike {
  json(): Promise<TagSuggestionsResponse>;
  ok: boolean;
}

export type TagSuggestionsFetchFn = (
  url: string,
) => Promise<TagSuggestionsFetchResponseLike>;

export interface TagSuggestionsSchedulerLike {
  clearTimeout(handle: number): void;
  setTimeout(callback: () => void, delayMs: number): number;
}

/**
 * Builds the suggestion-request URL, mirroring
 * `buildQuickSwitchRequestUrl`. Takes no `color` parameter -- the
 * suggestion panel is existing-tag-only, so there is no create-preview
 * color for the server to resolve, and the currently selected color is
 * irrelevant to what it returns.
 */
export function buildTagSuggestionsRequestUrl(
  baseUrl: string,
  query: string,
): string {
  const params = new URLSearchParams();
  params.set("q", query);
  return `${baseUrl}?${params.toString()}`;
}

/** Clamps a candidate index into `[0, length - 1]`, or -1 when empty. Does
 * not wrap -- matches `clampResultIndex` in `quick-switch.ts`. */
export function clampOptionIndex(index: number, length: number): number {
  if (length <= 0) {
    return -1;
  }
  if (index < 0) {
    return 0;
  }
  if (index > length - 1) {
    return length - 1;
  }
  return index;
}

interface RenderedOption {
  element: HTMLLIElement;
  name: string;
}

function buildExistingOption(
  doc: Document,
  tag: TagSuggestionExisting,
  optionId: string,
): RenderedOption {
  const li = doc.createElement("li");
  li.id = optionId;
  li.setAttribute("role", "option");
  li.setAttribute("aria-selected", "false");
  li.className = "tag-suggestions__option";
  li.setAttribute("aria-label", `Use existing tag "${tag.name}"`);

  const pill = doc.createElement("span");
  pill.className = "tag-suggestion__pill";
  pill.setAttribute("data-tag-color", tag.color);
  pill.textContent = tag.name;
  li.appendChild(pill);

  return { element: li, name: tag.name };
}

interface TagSuggestionsContext {
  fetchFn: TagSuggestionsFetchFn;
  form: HTMLFormElement;
  input: HTMLInputElement;
  panel: HTMLUListElement;
  scheduler: TagSuggestionsSchedulerLike;
  suggestionsUrl: string;
}

function wireTagSuggestions(ctx: TagSuggestionsContext, doc: Document): void {
  const { form, input, panel, suggestionsUrl, fetchFn, scheduler } = ctx;

  let debounceHandle: number | null = null;
  let requestId = 0;
  let options: RenderedOption[] = [];
  let activeIndex = -1;

  function clearDebounce(): void {
    if (debounceHandle !== null) {
      scheduler.clearTimeout(debounceHandle);
      debounceHandle = null;
    }
  }

  function closePanel(): void {
    clearDebounce();
    requestId += 1;
    options = [];
    activeIndex = -1;
    panel.hidden = true;
    panel.replaceChildren();
    input.setAttribute("aria-expanded", "false");
    input.removeAttribute("aria-activedescendant");
  }

  function setActiveIndex(index: number): void {
    if (activeIndex >= 0) {
      options[activeIndex]?.element.setAttribute("aria-selected", "false");
    }
    activeIndex = index;
    if (activeIndex >= 0) {
      const el = options[activeIndex]?.element;
      el?.setAttribute("aria-selected", "true");
      if (el) {
        input.setAttribute("aria-activedescendant", el.id);
        // Feature-detected rather than called unconditionally: real
        // browsers all support it, but it's absent in this project's
        // jsdom-backed test environment.
        el.scrollIntoView?.({ block: "nearest" });
      }
    } else {
      input.removeAttribute("aria-activedescendant");
    }
  }

  function attachExisting(name: string): void {
    closePanel();
    input.value = name;
    form.requestSubmit();
  }

  function wireOptionPointer(option: RenderedOption): void {
    option.element.addEventListener("pointerdown", (event) => {
      // Prevents the input from blurring (the browser's default action
      // for a pointerdown outside it) before selection logic runs --
      // the standard fix for the classic combobox blur-before-click
      // hazard. Pointer events cover mouse, touch, and pen uniformly in
      // every browser this project targets, so no separate touch path
      // is needed.
      event.preventDefault();
      attachExisting(option.name);
    });
  }

  function renderResponse(payload: TagSuggestionsSuccessResponse): void {
    if (payload.existing.length === 0) {
      // The
      // suggestion panel is existing-tag-only -- once nothing
      // existing matches (or the note is at its 20-tag limit, which the
      // server already reflects by omitting all suggestions), there is
      // nothing left to show. The panel simply closes, leaving the
      // typed text untouched and the ordinary Add Tag color selector/
      // submit button as the obvious next step -- no create row, no
      // explanatory message needed.
      closePanel();
      return;
    }

    const rendered = payload.existing.map((tag, index) =>
      buildExistingOption(doc, tag, `tag-suggestion-existing-${index}`),
    );

    options = rendered;
    activeIndex = -1;
    panel.replaceChildren(...rendered.map((option) => option.element));
    panel.hidden = false;
    input.setAttribute("aria-expanded", "true");
    rendered.forEach(wireOptionPointer);
  }

  function runQuery(query: string): void {
    const thisRequestId = ++requestId;
    const url = buildTagSuggestionsRequestUrl(suggestionsUrl, query);
    fetchFn(url)
      .then((response) => {
        if (thisRequestId !== requestId || !response.ok) {
          return undefined;
        }
        return response.json().then((data) => {
          if (thisRequestId !== requestId || !data.ok) {
            return;
          }
          renderResponse(data);
        });
      })
      .catch(() => {
        // Fails open: suggestions are a convenience layer, not the only
        // way to add a tag -- the plain Add Tag form keeps working
        // unchanged regardless of a suggestion-request failure.
      });
  }

  input.addEventListener("input", () => {
    clearDebounce();
    const query = input.value;
    if (query.trim() === "") {
      closePanel();
      return;
    }
    debounceHandle = scheduler.setTimeout(() => {
      runQuery(query);
    }, TAG_SUGGESTIONS_DEBOUNCE_MS);
  });

  input.addEventListener("keydown", (event) => {
    if (options.length === 0) {
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex(clampOptionIndex(activeIndex + 1, options.length));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex(clampOptionIndex(activeIndex - 1, options.length));
    } else if (event.key === "Enter") {
      if (activeIndex < 0) {
        return;
      }
      const option = options[activeIndex];
      if (!option) {
        return;
      }
      // The input still holds whatever text the user typed (which may
      // differ in case/whitespace from the tag's real stored name), so
      // the value is corrected to the exact stored name before
      // submitting -- Enter on a highlighted suggestion is the one case
      // native form submission must not be allowed to run as-is.
      event.preventDefault();
      attachExisting(option.name);
    } else if (event.key === "Escape") {
      event.preventDefault();
      closePanel();
    } else if (event.key === "Tab") {
      // Never traps focus -- Tab always continues to move focus
      // normally. With no create row left to "accept text into," a
      // highlighted suggestion now does nothing on Tab beyond closing
      // the panel: attaching on Tab would be a surprising side effect
      // of a pure focus-navigation key, and merely filling the input
      // with the candidate's name (this module's pre-correction
      // behavior) no longer has a clear purpose once the only two real
      // actions are "select an existing suggestion" (Enter/pointer) or
      // "type something new and use the ordinary Add Tag form" -- so
      // Tab is left to do exactly what it does everywhere else on this
      // page: move on.
      closePanel();
    }
  });

  form.addEventListener("focusout", (event) => {
    const nextFocus = (event as FocusEvent).relatedTarget as Node | null;
    if (nextFocus && form.contains(nextFocus)) {
      return;
    }
    closePanel();
  });
}

/**
 * Wires the custom tag-suggestion autocomplete for every Add Tag form
 * matching `[data-tag-suggestions-url]` found in `doc`. Neutralizes the
 * native `<datalist>` (`list=` attribute removed) so its browser-owned
 * popup cannot remain active underneath the custom panel once this module
 * has successfully initialized -- the `<datalist>` markup itself is left
 * in place, unmodified, as the no-JS fallback. The panel surfaces
 * existing-tag matches only -- reuse/attach, never a create preview. Typing a
 * candidate with no existing match simply closes the panel, leaving the
 * ordinary Add Tag color selector and submit button as the plain,
 * unchanged path to creating a new tag.
 */
export function initTagSuggestionsDocument(
  doc: Document = document,
  options: {
    fetchFn?: TagSuggestionsFetchFn;
    scheduler?: TagSuggestionsSchedulerLike;
  } = {},
): boolean {
  const fetchFn: TagSuggestionsFetchFn =
    options.fetchFn ??
    ((url: string) =>
      window.fetch(url) as unknown as Promise<TagSuggestionsFetchResponseLike>);
  const scheduler: TagSuggestionsSchedulerLike = options.scheduler ?? {
    clearTimeout: window.clearTimeout.bind(window),
    setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
  };

  let initializedAny = false;
  const forms = Array.from(
    doc.querySelectorAll<HTMLFormElement>(FORM_SELECTOR),
  );

  for (const form of forms) {
    if (form.dataset.tagSuggestionsInitialized === "true") {
      continue;
    }
    const input = form.querySelector<HTMLInputElement>(INPUT_SELECTOR);
    const panel = form.querySelector<HTMLUListElement>(PANEL_SELECTOR);
    const suggestionsUrl = form.dataset.tagSuggestionsUrl;
    if (!input || !panel || !suggestionsUrl) {
      continue;
    }

    input.removeAttribute("list");
    input.setAttribute("role", "combobox");
    input.setAttribute("aria-autocomplete", "list");
    input.setAttribute("aria-expanded", "false");
    input.setAttribute("aria-controls", panel.id);

    wireTagSuggestions(
      { form, input, panel, suggestionsUrl, fetchFn, scheduler },
      doc,
    );
    form.dataset.tagSuggestionsInitialized = "true";
    initializedAny = true;
  }

  return initializedAny;
}
