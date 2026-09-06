const TOOLTIP_TRIGGER_SELECTOR = "[data-tooltip]";
const TOOLTIP_ESTIMATED_HEIGHT = 40;

// The application header sits in normal document flow, not overlapping in
// the box-model sense, but a tooltip positioned purely from the trigger's
// own top offset can still land its rendered box across the header's band
// (e.g. a toolbar icon close to the top of a tree/drawer with only a little
// clearance). Reserving space down to the header's own bottom edge -- not a
// flat guess -- keeps the tooltip from ever sitting behind/under it.
const FIXED_HEADER_SELECTOR = ".app-header, .workspace-drawer__header";

// `.workspace-drawer__tree` and `.tree-nav__viewport` are scrollable regions
// (`overflow-y: auto`) that a toolbar tooltip can sit right at the top edge
// of; a tooltip placed "above" the trigger there is clipped by that
// container's own overflow regardless of how much viewport space remains
// further up the page.
const CLIPPING_ANCESTOR_SELECTOR =
  ".workspace-drawer__tree, .tree-nav__viewport";

function usableTopBoundary(doc: Document, trigger: HTMLElement): number {
  let boundary = 0;

  for (const header of Array.from(
    doc.querySelectorAll<HTMLElement>(FIXED_HEADER_SELECTOR),
  )) {
    const rect = header.getBoundingClientRect();
    if (rect.bottom > boundary) {
      boundary = rect.bottom;
    }
  }

  const clippingAncestor = trigger.closest<HTMLElement>(
    CLIPPING_ANCESTOR_SELECTOR,
  );
  if (clippingAncestor) {
    boundary = Math.max(boundary, clippingAncestor.getBoundingClientRect().top);
  }

  return boundary;
}

/**
 * The shared tooltip (`.icon-button[data-tooltip]` in app.css) prefers
 * appearing above its control -- appearing below was found to obscure the
 * filter input or an open popover beneath it. Pure CSS can't measure
 * whether there is genuinely enough room above a *specific* trigger at the
 * moment it's shown, so this narrow script does exactly that one
 * measurement, on hover/focus, and sets or removes
 * `data-tooltip-placement="below"` for the CSS fallback rule to apply. It
 * never decides *whether* to show the tooltip at all -- that stays pure
 * CSS via `:hover`/`:focus-visible` -- only which side it renders on.
 *
 * The measurement compares the trigger's top against the actual usable
 * boundary above it (the header's bottom edge and/or a clipping ancestor's
 * top edge), not merely the trigger's raw distance from the viewport's own
 * top -- that alone can look "safe" while still leaving the tooltip's
 * rendered box crossing the header band or getting clipped by a scrollable
 * container.
 */
export function initTooltipPlacementDocument(
  doc: Document = document,
): boolean {
  const triggers = Array.from(
    doc.querySelectorAll<HTMLElement>(TOOLTIP_TRIGGER_SELECTOR),
  );
  if (triggers.length === 0) {
    return false;
  }

  for (const trigger of triggers) {
    if (trigger.dataset.tooltipPlacementInitialized === "true") {
      continue;
    }

    const updatePlacement = (): void => {
      const rect = trigger.getBoundingClientRect();
      const boundary = usableTopBoundary(doc, trigger);
      const usableSpaceAbove = rect.top - boundary;
      if (usableSpaceAbove < TOOLTIP_ESTIMATED_HEIGHT) {
        trigger.setAttribute("data-tooltip-placement", "below");
      } else {
        trigger.removeAttribute("data-tooltip-placement");
      }
    };

    trigger.addEventListener("mouseenter", updatePlacement);
    trigger.addEventListener("focus", updatePlacement);

    trigger.dataset.tooltipPlacementInitialized = "true";
  }

  return true;
}
