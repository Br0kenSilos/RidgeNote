/**
 * Mirrors `core/themes.py`'s
 * `THEME_CHOICES` (the same small-duplication pattern
 * `tag-management.ts`'s `tagColorLabel()` already uses for
 * `TAG_COLOR_CHOICES`) -- each side is covered by its own tests, not a
 * shared build-time source. Adding a theme is a matter of appending
 * an entry here (and to `core/themes.py`), not an architecture change.
 */
export const THEME_CHOICES: ReadonlyArray<readonly [string, string]> = [
  ["warm-light", "Warm Light"],
  ["dark", "Dark"],
  ["glacier", "Glacier"],
  ["granite", "Granite"],
  ["alpine-mist", "Alpine Mist"],
  ["blue-dusk", "Blue Dusk"],
  ["midnight-ridge", "Midnight Ridge"],
  ["nightfall", "Nightfall"],
];

export const THEME_VALUES: readonly string[] = THEME_CHOICES.map(
  ([value]) => value,
);

export type Theme =
  | "warm-light"
  | "dark"
  | "glacier"
  | "granite"
  | "alpine-mist"
  | "blue-dusk"
  | "midnight-ridge"
  | "nightfall";

const DEFAULT_THEME: Theme = "warm-light";

export const THEME_STORAGE_KEY = "ridgenote.theme";

function isValidTheme(value: string | null): value is Theme {
  return value !== null && THEME_VALUES.includes(value);
}

/**
 * `<html>` (`document
 * .documentElement`) is the single canonical/authoritative theme
 * element -- its own `:root` token cascade (`--color-bg-app` and every
 * other `[data-theme="..."]`-scoped custom property) only ever resolves
 * correctly against an attribute on `<html>` itself, never one merely
 * inherited from a descendant such as `<body>`. `<body>` is kept in
 * sync (see `applyTheme` below) purely for compatibility with any
 * existing `[data-theme="..."]`-scoped selector already written against
 * it; reading is scoped to the one canonical element so there is never
 * a question of which of two independently-tracked values is current.
 */
function currentTheme(doc: Document): Theme {
  const value = doc.documentElement.dataset.theme ?? null;
  return isValidTheme(value) ? value : DEFAULT_THEME;
}

/**
 * Applies a theme to the document and marks the matching quick
 * -selector option(s) as current via `aria-current`. The authoritative
 * source of the *initial* theme is now the server-rendered `data-theme`
 * attribute, rendered identically onto both `<html>` and `<body>` from
 * one resolved value (`base.html`'s `html_attrs`/`body_attrs` blocks,
 * both driven by the same `RIDGENOTE_CURRENT_THEME` context value) --
 * this function only ever changes it in response to a live selection,
 * it is never called at load time to bootstrap from a client-side
 * store. Written to both elements every time, in the same order, so
 * the two can never drift apart under this function's own control.
 */
export function applyTheme(doc: Document, theme: Theme): void {
  doc.documentElement.dataset.theme = theme;
  doc.body.dataset.theme = theme;
  doc
    .querySelectorAll<HTMLElement>("[data-theme-quick-option]")
    .forEach((option) => {
      if (option.dataset.themeQuickOption === theme) {
        option.setAttribute("aria-current", "true");
      } else {
        option.removeAttribute("aria-current");
      }
    });
}

interface FetchLike {
  (input: string, init?: Record<string, unknown>): Promise<{ ok: boolean }>;
}

const defaultFetch: FetchLike = (input, init) =>
  (window.fetch as unknown as FetchLike)(input, init);

interface PageshowEventLike {
  persisted: boolean;
}

interface WindowLike {
  addEventListener(
    type: "pageshow",
    listener: (event: PageshowEventLike) => void,
  ): void;
}

const noopWindowLike: WindowLike = {
  addEventListener: () => {},
};

function setStatus(form: HTMLFormElement, message: string): void {
  const status = form.querySelector<HTMLElement>("[data-theme-quick-status]");
  if (status) {
    status.textContent = message;
  }
}

/**
 * Wires every `[data-theme-quick-form]` in `doc` (in practice exactly
 * one, inside the account-menu popover -- reused as-is, no new open
 * /close/outside-click/Escape wiring needed). Each `[data-theme-quick
 * -option]` submit button is a real, no-JS-compatible form control
 * (its own `<form method="post" action="{% url
 * 'accounts:quick_set_theme' %}">`, real `next`/CSRF hidden inputs) --
 * without JS it submits normally, the server saves the preference and
 * redirects back, and the freshly-rendered page reflects it via
 * `html_attrs`/`body_attrs`. With JS, this intercepts the click: apply the theme
 * immediately (optimistic), persist via `fetch` reusing the exact same
 * form data, and on failure revert to whatever theme was active before
 * the click rather than leaving the UI showing an unsaved choice as if
 * it had saved.
 */
export function initThemeQuickSelectorDocument(
  doc: Document = document,
  fetchImpl: FetchLike = defaultFetch,
  storage: Storage = typeof window !== "undefined"
    ? window.sessionStorage
    : ({} as Storage),
  windowLike: WindowLike = typeof window !== "undefined"
    ? window
    : noopWindowLike,
): boolean {
  const forms = Array.from(
    doc.querySelectorAll<HTMLFormElement>("[data-theme-quick-form]"),
  );
  if (forms.length === 0) {
    return false;
  }

  for (const form of forms) {
    if (form.dataset.themeQuickInitialized === "true") {
      continue;
    }

    form
      .querySelectorAll<HTMLButtonElement>("[data-theme-quick-option]")
      .forEach((option) => {
        option.addEventListener("click", (event) => {
          const rawTheme = option.dataset.themeQuickOption ?? null;
          if (!isValidTheme(rawTheme)) {
            return;
          }
          const nextTheme: Theme = rawTheme;
          event.preventDefault();

          const previousTheme = currentTheme(doc);
          if (nextTheme === previousTheme) {
            return;
          }

          applyTheme(doc, nextTheme);
          setStatus(form, "Saving theme…");

          const formData = new FormData(form);
          formData.set("theme", nextTheme);

          fetchImpl(form.action, {
            method: "POST",
            body: formData,
            credentials: "same-origin",
          })
            .then((response) => {
              if (!response.ok) {
                throw new Error("theme save failed");
              }
              try {
                storage.setItem(THEME_STORAGE_KEY, nextTheme);
              } catch {
                // Best-effort only -- see the pageshow/visibilitychange
                // reconciliation comment below for why this isn't fatal.
              }
              setStatus(form, "Theme saved.");
            })
            .catch(() => {
              applyTheme(doc, previousTheme);
              setStatus(
                form,
                "Couldn't save your theme. The previous theme has been restored.",
              );
            });
        });
      });

    form.dataset.themeQuickInitialized = "true";
  }

  /**
   * `sessionStorage` is
   * NOT the authoritative theme source -- `request.user.theme` is,
   * rendered server-side on every navigation, and it is an
   * account-wide preference: the most recent successful change from
   * *any* device must win everywhere the account is next loaded fresh.
   * `sessionStorage`'s only legitimate remaining job is narrower than
   * "every page load": a JS-driven quick-switch never navigates, so a
   * *different* page already sitting in this same browser's back/
   * forward cache keeps whatever `data-theme` it was rendered with,
   * frozen at that page's own last live moment -- restoring it later
   * (a genuine bfcache restore, never re-running server-side rendering)
   * can surface that frozen, now-stale value with no other chance to
   * correct it.
   *
   * The bug this pass fixes: `pageshow` fires on *every* page load, not
   * only a bfcache restore -- `event.persisted` is the only reliable
   * signal distinguishing the two (`true` only for an actual bfcache
   * restore). The previous handler ignored `event.persisted` entirely,
   * so it reconciled from this browser's own last-saved
   * `sessionStorage` value on *every* ordinary fresh navigation too --
   * including a plain reload and a fresh login -- silently overwriting
   * the just-rendered, correct, authoritative server value with this
   * browser's own stale prior choice whenever another device had since
   * changed the account's theme. The `visibilitychange` listener made
   * this worse: it fired on any tab-focus/visibility change at all,
   * with no bfcache-restore signal whatsoever, and is removed outright
   * -- `pageshow`'s own `persisted` flag already covers every
   * legitimate bfcache-restore case; nothing else needs reconciling
   * that a normal server-rendered load doesn't already resolve
   * correctly on its own. Fresh server-rendered state is authoritative
   * by simply never being second-guessed except in this one narrow,
   * correctly-identified case.
   */
  if (doc.documentElement.dataset.themeLifecycleBound !== "true") {
    windowLike.addEventListener("pageshow", (event) => {
      if (!event.persisted) {
        return;
      }
      let stored: string | null = null;
      try {
        stored = storage.getItem(THEME_STORAGE_KEY);
      } catch {
        return;
      }
      if (!isValidTheme(stored)) {
        return;
      }
      if (stored !== currentTheme(doc)) {
        applyTheme(doc, stored);
      }
    });

    doc.documentElement.dataset.themeLifecycleBound = "true";
  }

  return true;
}
