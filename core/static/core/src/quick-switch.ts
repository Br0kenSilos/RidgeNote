export const QUICK_SWITCH_DEBOUNCE_MS = 250;

const RESULT_LINK_SELECTOR = ".note-recent-switcher__link";

export interface QuickSwitchResult {
  folder_label: string;
  id: number;
  title: string;
  url: string;
}

export interface QuickSwitchSuccessResponse {
  ok: true;
  results: QuickSwitchResult[];
}

export interface QuickSwitchFailureResponse {
  ok: false;
}

export type QuickSwitchResponse =
  | QuickSwitchFailureResponse
  | QuickSwitchSuccessResponse;

export interface QuickSwitchFetchResponseLike {
  json(): Promise<QuickSwitchResponse>;
  ok: boolean;
}

export type QuickSwitchFetchFn = (
  url: string,
) => Promise<QuickSwitchFetchResponseLike>;

export interface QuickSwitchSchedulerLike {
  clearTimeout(handle: number): void;
  setTimeout(callback: () => void, delayMs: number): number;
}

/** Minimal shape returned by `documentLike.createElement` -- duck-typed
 * (rather than the real `HTMLElement`) so tests can supply a plain fake
 * without a full DOM implementation. */
export interface QuickSwitchElementLike {
  appendChild(node: QuickSwitchElementLike): void;
  className: string;
  href?: string;
  textContent: string | null;
}

export interface QuickSwitchDocumentLike {
  createElement(tagName: string): QuickSwitchElementLike;
}

export interface QuickSwitchInitOptions {
  documentLike?: QuickSwitchDocumentLike;
  fetchFn?: QuickSwitchFetchFn;
  scheduler?: QuickSwitchSchedulerLike;
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

export function buildQuickSwitchRequestUrl(
  baseUrl: string,
  query: string,
  excludeNoteId?: string,
): string {
  const params = new URLSearchParams();
  params.set("q", query);
  if (excludeNoteId) {
    params.set("exclude", excludeNoteId);
  }
  return `${baseUrl}?${params.toString()}`;
}

function replaceListChildren(
  resultsList: Element,
  items: QuickSwitchElementLike[],
): void {
  (
    resultsList as unknown as {
      replaceChildren(...nodes: QuickSwitchElementLike[]): void;
    }
  ).replaceChildren(...items);
}

function renderMessage(
  resultsList: Element,
  documentLike: QuickSwitchDocumentLike,
  message: string,
): void {
  const item = documentLike.createElement("li");
  item.className = "note-recent-switcher__empty";
  item.textContent = message;
  replaceListChildren(resultsList, [item]);
}

function renderResults(
  resultsList: Element,
  documentLike: QuickSwitchDocumentLike,
  results: QuickSwitchResult[],
): void {
  if (results.length === 0) {
    renderMessage(resultsList, documentLike, "No matching notes.");
    return;
  }

  const items: QuickSwitchElementLike[] = results.map((result) => {
    const link = documentLike.createElement("a");
    link.className = "note-recent-switcher__link";
    link.href = result.url;
    link.textContent = result.title;

    const folderLabel = documentLike.createElement("span");
    folderLabel.className = "note-recent-switcher__folder";
    folderLabel.textContent = result.folder_label;
    link.appendChild(folderLabel);

    const item = documentLike.createElement("li");
    item.className = "note-recent-switcher__item";
    item.appendChild(link);
    return item;
  });

  replaceListChildren(resultsList, items);
}

/**
 * (Re-)wires Arrow Down/Up/Escape keyboard handling on every currently
 * rendered result link in `resultsList`. Called after any content change
 * (initial load, default-recents restore, or a fresh query render) since
 * replacing `innerHTML`/children discards any previously attached listeners.
 */
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

function wireQuickSwitchContainer(
  container: HTMLElement,
  input: HTMLInputElement,
  resultsList: HTMLUListElement,
  baseUrl: string,
  fetchFn: QuickSwitchFetchFn,
  scheduler: QuickSwitchSchedulerLike,
  documentLike: QuickSwitchDocumentLike,
): void {
  const excludeNoteId = container.dataset.quickSwitchExcludeId || undefined;
  const defaultResultsHtml = resultsList.innerHTML;
  let debounceHandle: number | null = null;
  let requestId = 0;

  function clearDebounce(): void {
    if (debounceHandle !== null) {
      scheduler.clearTimeout(debounceHandle);
      debounceHandle = null;
    }
  }

  function rewireLinks(): void {
    wireResultLinkKeyboardNav(resultsList, input, closeAndReturnFocus);
  }

  function restoreDefaultResults(): void {
    clearDebounce();
    requestId += 1;
    resultsList.innerHTML = defaultResultsHtml;
    rewireLinks();
  }

  function runQuery(query: string): void {
    const thisRequestId = ++requestId;
    renderMessage(resultsList, documentLike, "Searching…");
    const url = buildQuickSwitchRequestUrl(baseUrl, query, excludeNoteId);
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
    clearDebounce();
    input.value = "";
    restoreDefaultResults();
    (container as unknown as { open: boolean }).open = false;
    container.querySelector<HTMLElement>("summary")?.focus();
  }

  input.addEventListener("input", () => {
    clearDebounce();
    const query = input.value;
    if (query.trim() === "") {
      restoreDefaultResults();
      return;
    }
    debounceHandle = scheduler.setTimeout(() => {
      runQuery(query);
    }, QUICK_SWITCH_DEBOUNCE_MS);
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

  // Wire keyboard nav on the server-rendered default recents up front.
  rewireLinks();
}

/**
 * Wires typed filtering + keyboard navigation for every quick-switch
 * `[data-note-recent-switcher]` container found in `doc`. Each container
 * (note-detail, Home) is wired fully independently.
 */
export function initQuickSwitchDocument(
  doc: Document = document,
  options: QuickSwitchInitOptions = {},
): boolean {
  const fetchFn: QuickSwitchFetchFn =
    options.fetchFn ??
    ((url: string) =>
      window.fetch(url) as unknown as Promise<QuickSwitchFetchResponseLike>);
  const scheduler: QuickSwitchSchedulerLike = options.scheduler ?? {
    clearTimeout: window.clearTimeout.bind(window),
    setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
  };
  const documentLike: QuickSwitchDocumentLike =
    options.documentLike ?? (document as unknown as QuickSwitchDocumentLike);

  let initializedAny = false;
  const containers = Array.from(
    doc.querySelectorAll<HTMLElement>("[data-note-recent-switcher]"),
  );

  for (const container of containers) {
    if (container.dataset.quickSwitchInitialized === "true") {
      continue;
    }
    const input = container.querySelector<HTMLInputElement>(
      "[data-quick-switch-input]",
    );
    const resultsList = container.querySelector<HTMLUListElement>(
      "[data-quick-switch-results]",
    );
    const baseUrl = container.dataset.quickSwitchUrl;
    if (!input || !resultsList || !baseUrl) {
      continue;
    }

    wireQuickSwitchContainer(
      container,
      input,
      resultsList,
      baseUrl,
      fetchFn,
      scheduler,
      documentLike,
    );
    container.dataset.quickSwitchInitialized = "true";
    initializedAny = true;
  }

  return initializedAny;
}
