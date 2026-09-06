const ROOT_SELECTOR = "[data-palette-lab-root]";
const SELECT_SELECTOR = "[data-palette-lab-select]";
const PREVIEW_SELECTOR = "[data-palette-lab-preview]";
const SWATCH_SELECTOR = "[data-palette-lab-swatch]";

/**
 * Theme Calibration / Palette Lab.
 * Switches which internal design-candidate palette is previewed, purely
 * by toggling `data-palette-candidate` on the one preview wrapper (and
 * `aria-pressed`/active state on the swatch cards) -- every candidate's
 * CSS custom-property override already lives in `app.css`, scoped under
 * `.palette-lab-scope[data-palette-candidate="..."]`, so changing this
 * one attribute is enough for the whole preview subtree to re-cascade.
 * No network request, no persistence of any kind, no interaction with
 * the real theme-quick-select endpoint or `document.documentElement`/
 * `document.body`'s own `data-theme` -- the actual application theme
 * and `User.theme` are completely untouched by this module. */
export function initPaletteLabDocument(doc: Document): void {
  const root = doc.querySelector<HTMLElement>(ROOT_SELECTOR);
  if (!root) return;
  if (root.dataset.paletteLabInitialized === "true") return;
  root.dataset.paletteLabInitialized = "true";

  const select = root.querySelector<HTMLSelectElement>(SELECT_SELECTOR);
  const preview = root.querySelector<HTMLElement>(PREVIEW_SELECTOR);
  if (!select || !preview) return;

  const swatches = Array.from(
    root.querySelectorAll<HTMLElement>(SWATCH_SELECTOR),
  );
  const knownIds = new Set(
    swatches
      .map((swatch) => swatch.dataset.paletteLabSwatch)
      .filter((id): id is string => Boolean(id)),
  );

  function activate(candidateId: string | null | undefined): void {
    if (!candidateId || !knownIds.has(candidateId)) return;
    preview!.dataset.paletteCandidate = candidateId;
    select!.value = candidateId;
    swatches.forEach((swatch) => {
      const isActive = swatch.dataset.paletteLabSwatch === candidateId;
      swatch.setAttribute("aria-pressed", isActive ? "true" : "false");
      swatch.classList.toggle("palette-lab__swatch-card--active", isActive);
    });
  }

  select.addEventListener("change", () => activate(select.value));
  swatches.forEach((swatch) => {
    swatch.addEventListener("click", () => {
      activate(swatch.dataset.paletteLabSwatch);
    });
  });

  activate(select.value || preview.dataset.paletteCandidate);
}
