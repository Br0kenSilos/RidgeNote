export const GLOBAL_SEARCH_DEBOUNCE_MS = 250;

const RESULT_LINK_SELECTOR = ".global-search-panel__link";

export interface GlobalSearchSegment {
  highlight: boolean;
  text: string;
}

export interface GlobalSearchResult {
  body_segments: GlobalSearchSegment[];
  folder_label: string;
  id: number;
  title_segments: GlobalSearchSegment[];
  url: string;
}

export interface GlobalSearchSuccessResponse {
  ok: true;
  results: GlobalSearchResult[];
}

export interface GlobalSearchFailureResponse {
  ok: false;
}

export type GlobalSearchResponse =
  | GlobalSearchFailureResponse
  | GlobalSearchSuccessResponse;

export interface GlobalSearchFetchResponseLike {
  json(): Promise<GlobalSearchResponse>;
  ok: boolean;
}

export type GlobalSearchFetchFn = (
  url: string,
) => Promise<GlobalSearchFetchResponseLike>;

export interface GlobalSearchSchedulerLike {
  clearTimeout(handle: number): void;
  setTimeout(callback: () => void, delayMs: number): number;
}

/** Minimal shape returned by `documentLike.createElement` -- duck-typed
 * (rather than the real `HTMLElement`) so tests can supply a plain fake
 * without a full DOM implementation. Mirrors quick-switch.ts's approach. */
export interface GlobalSearchElementLike {
  appendChild(node: GlobalSearchElementLike): void;
  className: string;
  href?: string;
  textContent: string | null;
}

export interface GlobalSearchDocumentLike {
  createElement(tagName: string): GlobalSearchElementLike;
}

export interface GlobalSearchInitOptions {
  documentLike?: GlobalSearchDocumentLike;
  fetchFn?: GlobalSearchFetchFn;
  scheduler?: GlobalSearchSchedulerLike;
}

/** Clamps a candidate result index into `[0, resultCount - 1]`, or -1 when empty. */
export function clampResultIndex(index: number, resultCount: number): number {
  if (resultCount <= 0) {
    return -1;
  }
  if (index < 0) {
    return 0;
  }
  if (index > resultCount - 1) {
    return resultCount - 1;
  }
  return index;
}

export function buildGlobalSearchRequestUrl(
  baseUrl: string,
  query: string,
): string {
  const params = new URLSearchParams();
  params.set("q", query);
  return `${baseUrl}?${params.toString()}`;
}

function replaceListChildren(
  resultsList: Element,
  items: GlobalSearchElementLike[],
): void {
  (
    resultsList as unknown as {
      replaceChildren(...nodes: GlobalSearchElementLike[]): void;
    }
  ).replaceChildren(...items);
}

function renderMessage(
  resultsList: Element,
  documentLike: GlobalSearchDocumentLike,
  message: string,
): void {
  const item = documentLike.createElement("li");
  item.className = "global-search-panel__empty";
  item.textContent = message;
  replaceListChildren(resultsList, [item]);
}

/** Appends `segments` as text nodes (via `textContent`, never `innerHTML`)
 * into `container`, wrapping matched segments in a `<mark>` element built
 * through `createElement` -- shared by both the title line and the body
 * excerpt so segment-rendering logic exists exactly once. Hostile segment
 * text (e.g. a literal `<script>` string in a note) always becomes an inert
 * text node, never executable markup. */
function appendSegments(
  documentLike: GlobalSearchDocumentLike,
  container: GlobalSearchElementLike,
  segments: GlobalSearchSegment[],
): void {
  for (const segment of segments) {
    if (segment.highlight) {
      const mark = documentLike.createElement("mark");
      mark.textContent = segment.text;
      container.appendChild(mark);
    } else {
      const span = documentLike.createElement("span");
      span.textContent = segment.text;
      container.appendChild(span);
    }
  }
}

function renderResults(
  resultsList: Element,
  documentLike: GlobalSearchDocumentLike,
  results: GlobalSearchResult[],
): void {
  if (results.length === 0) {
    renderMessage(resultsList, documentLike, "No results.");
    return;
  }

  const items: GlobalSearchElementLike[] = results.map((result) => {
    const link = documentLike.createElement("a");
    link.className = "global-search-panel__link";
    link.href = result.url;

    const titleLine = documentLike.createElement("span");
    titleLine.className = "global-search-panel__result-title";
    appendSegments(documentLike, titleLine, result.title_segments);
    link.appendChild(titleLine);

    const folderLabel = documentLike.createElement("span");
    folderLabel.className = "global-search-panel__result-folder";
    folderLabel.textContent = result.folder_label;
    link.appendChild(folderLabel);

    if (result.body_segments.length > 0) {
      const excerpt = documentLike.createElement("p");
      excerpt.className = "global-search-panel__excerpt";
      appendSegments(documentLike, excerpt, result.body_segments);
      link.appendChild(excerpt);
    }

    const item = documentLike.createElement("li");
    item.className = "global-search-panel__item";
    item.appendChild(link);
    return item;
  });

  replaceListChildren(resultsList, items);
}

function wireResultLinkKeyboardNav(
  resultsList: HTMLUListElement,
  input: HTMLInputElement,
  closeAndReturnFocus: () => void,
): void {
  const links = Array.from(
    resultsList.querySelectorAll<HTMLAnchorElement>(RESULT_LINK_SELECTOR),
  );
  links.forEach((link, index) => {
    link.addEventListener("keydown", (event) => {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        const nextIndex = clampResultIndex(index + 1, links.length);
        if (nextIndex !== -1) {
          links[nextIndex]?.focus();
        }
      } else if (event.key === "ArrowUp") {
        event.preventDefault();
        if (index <= 0) {
          input.focus();
          return;
        }
        links[index - 1]?.focus();
      } else if (event.key === "Escape") {
        event.preventDefault();
        closeAndReturnFocus();
      }
    });
  });
}

/**
 * Wires the single shared Global Search `<dialog>` in `doc`, opened from any
 * number of `[data-global-search-toggle]` triggers (header, workspace
 * header, narrow drawer). Mirrors `initHelpPanel`'s multi-trigger,
 * drawer-dismissal-first, native-dialog-close-restores-focus pattern.
 */
export function initGlobalSearchPanel(
  doc: Document = document,
  options: GlobalSearchInitOptions = {},
): boolean {
  const dialog = doc.querySelector<HTMLDialogElement>("#global-search-panel");
  if (!dialog) {
    return false;
  }
  const input = dialog.querySelector<HTMLInputElement>(
    "[data-global-search-input]",
  );
  const resultsList = dialog.querySelector<HTMLUListElement>(
    "[data-global-search-results]",
  );
  const baseUrl = dialog.dataset.globalSearchUrl;
  if (!input || !resultsList || !baseUrl) {
    return false;
  }
  if (dialog.dataset.globalSearchInitialized === "true") {
    return false;
  }
  dialog.dataset.globalSearchInitialized = "true";

  wireGlobalSearchPanel(doc, dialog, input, resultsList, baseUrl, options);
  return true;
}

function wireGlobalSearchPanel(
  doc: Document,
  dialog: HTMLDialogElement,
  input: HTMLInputElement,
  resultsList: HTMLUListElement,
  baseUrl: string,
  options: GlobalSearchInitOptions,
): void {
  const fetchFn: GlobalSearchFetchFn =
    options.fetchFn ??
    ((url: string) =>
      window.fetch(url) as unknown as Promise<GlobalSearchFetchResponseLike>);
  const scheduler: GlobalSearchSchedulerLike = options.scheduler ?? {
    clearTimeout: window.clearTimeout.bind(window),
    setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
  };
  const documentLike: GlobalSearchDocumentLike =
    options.documentLike ?? (doc as unknown as GlobalSearchDocumentLike);

  let debounceHandle: number | null = null;
  let requestId = 0;
  let restoreFocusTarget: HTMLElement | null = null;

  function clearDebounce(): void {
    if (debounceHandle !== null) {
      scheduler.clearTimeout(debounceHandle);
      debounceHandle = null;
    }
  }

  function clearResults(): void {
    requestId += 1;
    replaceListChildren(resultsList, []);
  }

  function rewireLinks(): void {
    wireResultLinkKeyboardNav(resultsList, input, closeAndReturnFocus);
  }

  function runQuery(query: string): void {
    const thisRequestId = ++requestId;
    renderMessage(resultsList, documentLike, "Searching…");
    const url = buildGlobalSearchRequestUrl(baseUrl, query);
    fetchFn(url)
      .then((response) => {
        if (thisRequestId !== requestId) {
          return undefined;
        }
        if (!response.ok) {
          renderMessage(
            resultsList,
            documentLike,
            "Couldn't load results. Try again.",
          );
          return undefined;
        }
        return response.json().then((payload) => {
          if (thisRequestId !== requestId) {
            return;
          }
          if (!payload.ok) {
            renderMessage(
              resultsList,
              documentLike,
              "Couldn't load results. Try again.",
            );
            return;
          }
          renderResults(resultsList, documentLike, payload.results);
          rewireLinks();
        });
      })
      .catch(() => {
        if (thisRequestId !== requestId) {
          return;
        }
        renderMessage(
          resultsList,
          documentLike,
          "Couldn't load results. Try again.",
        );
      });
  }

  function closeAndReturnFocus(): void {
    dialog.close();
  }

  input.addEventListener("input", () => {
    clearDebounce();
    const query = input.value;
    if (query.trim() === "") {
      clearResults();
      return;
    }
    debounceHandle = scheduler.setTimeout(() => {
      runQuery(query);
    }, GLOBAL_SEARCH_DEBOUNCE_MS);
  });

  input.addEventListener("keydown", (event) => {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      const links = Array.from(
        resultsList.querySelectorAll<HTMLAnchorElement>(RESULT_LINK_SELECTOR),
      );
      links[0]?.focus();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      const links = Array.from(
        resultsList.querySelectorAll<HTMLAnchorElement>(RESULT_LINK_SELECTOR),
      );
      links[links.length - 1]?.focus();
    } else if (event.key === "Escape") {
      event.preventDefault();
      closeAndReturnFocus();
    }
  });

  doc
    .querySelectorAll<HTMLElement>("[data-global-search-toggle]")
    .forEach((trigger) => {
      trigger.addEventListener("click", () => {
        const inDrawer = trigger.closest("[data-drawer]") !== null;
        if (inDrawer) {
          const drawerCloseBtn = doc.querySelector<HTMLElement>(
            "[data-drawer-close]",
          );
          drawerCloseBtn?.click();
          restoreFocusTarget =
            doc.querySelector<HTMLElement>("[data-drawer-toggle]") ?? trigger;
        } else {
          restoreFocusTarget = trigger;
        }
        doc
          .querySelectorAll<HTMLElement>("[data-global-search-toggle]")
          .forEach((t) => {
            t.setAttribute("aria-expanded", "true");
          });
        dialog.showModal();
        input.focus();
      });
    });

  const closeBtn = dialog.querySelector<HTMLElement>(
    "[data-global-search-close]",
  );
  closeBtn?.addEventListener("click", () => {
    dialog.close();
  });

  dialog.addEventListener("close", () => {
    doc
      .querySelectorAll<HTMLElement>("[data-global-search-toggle]")
      .forEach((t) => {
        t.setAttribute("aria-expanded", "false");
      });
    clearDebounce();
    input.value = "";
    clearResults();
    restoreFocusTarget?.focus();
    restoreFocusTarget = null;
  });
}
