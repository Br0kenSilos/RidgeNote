const TAG_CHIP_SELECTOR = ".note-list__tag-chip";
const LABEL_SELECTOR = ".note-list__tag-chip__label";

export interface TagChipLabelElementLike {
  scrollWidth: number;
  clientWidth: number;
  textContent: string | null;
}

export interface TagChipElementLike {
  querySelector(selector: string): TagChipLabelElementLike | null;
  setAttribute(name: string, value: string): void;
  removeAttribute(name: string): void;
}

export interface TagChipDocumentLike {
  querySelectorAll(selector: string): ArrayLike<TagChipElementLike>;
}

export interface TagChipWindowLike {
  addEventListener(type: "resize", listener: () => void): void;
}

/**
 * A tag-name character count can't reliably predict whether a chip's own
 * `text-overflow: ellipsis` (app.css, bounded to `max-width: 9rem`) has
 * actually engaged -- proportional-font glyph widths vary per character,
 * theme, zoom level, and browser, so a fixed length threshold either
 * shows a redundant tooltip on a chip that already displays its full
 * label, or misses one that genuinely truncated. `scrollWidth >
 * clientWidth` is the standard, deterministic way to ask the real
 * rendered layout the same question CSS itself already answered when it
 * decided to clip the text -- so this only ever agrees with what's
 * visually true, not an estimate of it.
 *
 * The measurement targets `.note-list__tag-chip__label` -- the *inner*
 * element that actually has `overflow: hidden` (app.css) -- not the
 * outer `.note-list__tag-chip` pill itself, which deliberately has
 * `overflow: visible` so it can host the tooltip without clipping its
 * own `::after` (see app.css's comment on why those two responsibilities
 * were split onto two elements). Measuring the outer element here would
 * never detect truncation at all, since nothing about it clips.
 * `data-tooltip`/`tabindex` are still set on the *outer* chip, so the
 * whole pill -- not just its text -- is what becomes hoverable/
 * focusable.
 *
 * Mirrors `workspace-header-height.ts`'s "measure real layout, drive a
 * DOM decision, re-measure on resize" shape: every chip in `doc` is
 * measured once at init and again on every window resize (the only
 * event that can change a chip's own rendered width in this app, since
 * tag lists are server-rendered and never mutated client-side), so
 * "resizing across the truncation threshold" always leaves both the
 * tooltip and its keyboard-focusability in sync with the current
 * layout -- never a stale state left over from a previous width.
 *
 * A truncated chip gets `data-tooltip` (reusing the exact shared,
 * CSS-only `[data-tooltip]` mechanism `tooltip-placement.ts` and
 * `app.css` already provide for icon-buttons -- no new tooltip
 * presentation, no new JavaScript popover) and `tabindex="0"`, so it is
 * both hoverable and keyboard-focusable. A chip that fits gets neither:
 * no redundant tooltip, and no Tab stop it doesn't need. This is the one
 * generic pass over `.note-list__tag-chip` -- Home, All Notes, and any
 * other surface that ever renders this same shared class all get
 * identical behavior from this one function, with no surface-specific
 * logic anywhere.
 */
export function initTagChipTruncation(
  doc: TagChipDocumentLike = typeof document !== "undefined"
    ? (document as unknown as TagChipDocumentLike)
    : { querySelectorAll: () => [] },
  windowLike: TagChipWindowLike = typeof window !== "undefined"
    ? window
    : { addEventListener: () => {} },
): boolean {
  const chips = Array.from(doc.querySelectorAll(TAG_CHIP_SELECTOR));
  if (chips.length === 0) {
    return false;
  }

  const updateChip = (chip: TagChipElementLike): void => {
    const label = chip.querySelector(LABEL_SELECTOR);
    if (!label) {
      chip.removeAttribute("data-tooltip");
      chip.removeAttribute("tabindex");
      return;
    }
    const text = label.textContent ?? "";
    const isTruncated = text !== "" && label.scrollWidth > label.clientWidth;
    if (isTruncated) {
      chip.setAttribute("data-tooltip", text);
      chip.setAttribute("tabindex", "0");
    } else {
      chip.removeAttribute("data-tooltip");
      chip.removeAttribute("tabindex");
    }
  };

  const updateAll = (): void => {
    chips.forEach(updateChip);
  };

  updateAll();
  windowLike.addEventListener("resize", updateAll);

  return true;
}
