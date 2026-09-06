const NOTE_LINK_SELECTOR = ".tree-nav__note-link";
const FOLDER_DISCLOSURE_SELECTOR = ".tree-nav__folder-disclosure";
const TRIGGER_SELECTOR = `${NOTE_LINK_SELECTOR}, ${FOLDER_DISCLOSURE_SELECTOR}`;
const LABEL_SELECTOR = ".tree-nav__label";
const VIEWPORT_SELECTOR = ".tree-nav__viewport";
const FOLDER_DETAILS_SELECTOR = ".tree-nav__folder-details";
const TOOLTIP_CLASS = "tree-title-tooltip";
const TOOLTIP_VISIBLE_CLASS = "tree-title-tooltip--visible";
const TOOLTIP_GAP = 6;
const TOOLTIP_VIEWPORT_MARGIN = 8;

export interface TreeTitleLabelElementLike {
  scrollWidth: number;
  clientWidth: number;
  textContent: string | null;
}

export interface TreeTitleTriggerElementLike {
  querySelector(selector: string): TreeTitleLabelElementLike | null;
  setAttribute(name: string, value: string): void;
  removeAttribute(name: string): void;
}

export interface TreeTitleUpdatableLabelElementLike extends TreeTitleLabelElementLike {
  closest(selector: string): TreeTitleTriggerElementLike | null;
}

export interface TreeTitleDocumentLike {
  querySelectorAll(
    selector: string,
  ): ArrayLike<TreeTitleTriggerElementLike | unknown>;
}

export interface TreeTitleResizeObserverLike {
  observe(target: unknown): void;
}

export interface TreeTitleWindowLike {
  addEventListener(type: "resize", listener: () => void): void;
  requestAnimationFrame?(callback: () => void): number;
  ResizeObserver?: unknown;
}

/**
 * A title's character count can't reliably predict whether the tree row's
 * own `text-overflow: ellipsis` (app.css, `.tree-nav__label`) has actually
 * engaged -- proportional-font glyph widths vary per character, theme, zoom
 * level, and available tree width (itself resizable, collapsible, and
 * different again in the narrow drawer). `scrollWidth > clientWidth` is the
 * same deterministic real-layout check `tag-chip-truncation.ts` already
 * uses for the identical problem on tag chips -- this generalizes that
 * exact technique to tree note and folder titles rather than inventing a
 * second measurement approach.
 *
 * The measurement targets `.tree-nav__label` -- the element that actually
 * has `overflow: hidden` -- not the outer `.tree-nav__note-link`/
 * `.tree-nav__folder-disclosure` trigger. `data-tooltip` is set on the
 * *trigger* (the whole clickable/focusable row control -- a real `<a>` for
 * notes, a real `<summary>` for folders) purely as this module's own state
 * flag -- see `wireTreeTitleTooltipPortal` below for why the tooltip is no
 * longer rendered from this attribute via CSS `::after`. Neither element
 * needs a new `tabindex`: both are already natively part of the tab order
 * regardless of truncation state (an `<a href>` and a `<summary>` inside a
 * `<details>` are both intrinsically focusable), so adding a tooltip never
 * makes an untruncated title tabbable that wasn't already.
 */
function updateTrigger(trigger: TreeTitleTriggerElementLike): boolean {
  const label = trigger.querySelector(LABEL_SELECTOR);
  if (!label) {
    trigger.removeAttribute("data-tooltip");
    return false;
  }
  const text = label.textContent ?? "";
  const isTruncated = text !== "" && label.scrollWidth > label.clientWidth;
  if (isTruncated) {
    trigger.setAttribute("data-tooltip", text);
  } else {
    trigger.removeAttribute("data-tooltip");
  }
  return isTruncated;
}

export interface TreeTooltipRect {
  bottom: number;
  height: number;
  left: number;
  right: number;
  top: number;
  width: number;
}

export interface TreeTooltipPosition {
  left: number;
  top: number;
}

/**
 * `.tree-nav__viewport`
 * scrolls (`overflow-y: auto`), and per CSS's own overflow-axis coupling
 * rule an explicit `overflow-y` that isn't `visible` forces the *other*
 * axis to compute as `auto` too, even with no `overflow-x` declared -- so a
 * CSS `::after` anchored inside it can never render further right/left
 * than the viewport's own edge, regardless of the anchor element's own
 * `overflow`. This is the same "can't escape an ancestor's clipping"
 * problem `tree-context-menu.ts`'s `floatRowMenuPanel`/
 * `computeRowMenuPosition` already solve for row-menu popovers, by
 * measuring the trigger's real `getBoundingClientRect()` and positioning a
 * `position: fixed` element with plain JS math instead of relying on CSS
 * containment. This function is that same math, adapted for a tooltip:
 * centered under (preferring above) the trigger, like the original
 * `::after`'s `left: 50%; transform: translateX(-50%)`, but clamped on all
 * four sides so it can never run off the viewport, and free to render at
 * its own natural (wrapped) content width up to the CSS `max-width` on
 * `.tree-title-tooltip` -- not constrained to the trigger's or the tree
 * pane's own width.
 */
export function computeTreeTooltipPosition(
  triggerRect: TreeTooltipRect,
  tooltipRect: TreeTooltipRect,
  viewportWidth: number,
  viewportHeight: number,
): TreeTooltipPosition {
  let top = triggerRect.top - tooltipRect.height - TOOLTIP_GAP;
  if (top < TOOLTIP_VIEWPORT_MARGIN) {
    const below = triggerRect.bottom + TOOLTIP_GAP;
    if (
      below + tooltipRect.height <=
      viewportHeight - TOOLTIP_VIEWPORT_MARGIN
    ) {
      top = below;
    }
  }
  top = Math.max(
    TOOLTIP_VIEWPORT_MARGIN,
    Math.min(
      top,
      viewportHeight - tooltipRect.height - TOOLTIP_VIEWPORT_MARGIN,
    ),
  );

  let left = triggerRect.left + triggerRect.width / 2 - tooltipRect.width / 2;
  left = Math.max(
    TOOLTIP_VIEWPORT_MARGIN,
    Math.min(left, viewportWidth - tooltipRect.width - TOOLTIP_VIEWPORT_MARGIN),
  );

  return { left, top };
}

interface PortalTrigger {
  addEventListener(type: string, listener: () => void): void;
  getAttribute(name: string): string | null;
  getBoundingClientRect(): TreeTooltipRect;
}

interface PortalTooltipElement {
  classList: { add(name: string): void; remove(name: string): void };
  getBoundingClientRect(): TreeTooltipRect;
  style: { left: string; top: string };
  textContent: string | null;
}

interface PortalDocumentLike {
  body: { appendChild(el: unknown): void };
  createElement(tag: string): PortalTooltipElement;
}

interface PortalWindowLike {
  innerHeight: number;
  innerWidth: number;
}

/**
 * Wires hover/focus-driven show/hide for the shared, body-level tooltip
 * portal against every current tree title trigger that may carry
 * `data-tooltip`. One tooltip element serves every row (never one DOM node
 * per row), shown/repositioned only while a truncated title is actually
 * hovered or focused, and hidden on `mouseleave`/`blur` or the moment a
 * re-measure (`hideIfStale`) finds the currently-shown trigger is no
 * longer truncated -- e.g. the tree widened while the tooltip was open.
 */
function wireTreeTitleTooltipPortal(
  triggers: PortalTrigger[],
  doc: PortalDocumentLike,
  windowLike: PortalWindowLike,
): { hideIfStale: (trigger: PortalTrigger) => void } {
  let tooltip: PortalTooltipElement | null = null;
  let activeTrigger: PortalTrigger | null = null;

  const ensureTooltip = (): PortalTooltipElement => {
    if (!tooltip) {
      tooltip = doc.createElement("div");
      tooltip.classList.add(TOOLTIP_CLASS);
      doc.body.appendChild(tooltip);
    }
    return tooltip;
  };

  const hide = (): void => {
    activeTrigger = null;
    tooltip?.classList.remove(TOOLTIP_VISIBLE_CLASS);
  };

  const show = (trigger: PortalTrigger): void => {
    const text = trigger.getAttribute("data-tooltip");
    if (!text) {
      return;
    }
    const el = ensureTooltip();
    activeTrigger = trigger;
    el.textContent = text;
    el.classList.add(TOOLTIP_VISIBLE_CLASS);
    const position = computeTreeTooltipPosition(
      trigger.getBoundingClientRect(),
      el.getBoundingClientRect(),
      windowLike.innerWidth,
      windowLike.innerHeight,
    );
    el.style.left = `${position.left}px`;
    el.style.top = `${position.top}px`;
  };

  for (const trigger of triggers) {
    trigger.addEventListener("mouseenter", () => show(trigger));
    trigger.addEventListener("focus", () => show(trigger));
    trigger.addEventListener("mouseleave", hide);
    trigger.addEventListener("blur", hide);
  }

  return {
    hideIfStale: (trigger: PortalTrigger): void => {
      if (activeTrigger === trigger && !trigger.getAttribute("data-tooltip")) {
        hide();
      }
    },
  };
}

export function initTreeTitleTooltipDocument(
  doc: TreeTitleDocumentLike = typeof document !== "undefined"
    ? (document as unknown as TreeTitleDocumentLike)
    : { querySelectorAll: () => [] },
  windowLike: TreeTitleWindowLike = typeof window !== "undefined"
    ? (window as unknown as TreeTitleWindowLike)
    : { addEventListener: () => {} },
): boolean {
  const triggers = Array.from(
    doc.querySelectorAll(TRIGGER_SELECTOR),
  ) as TreeTitleTriggerElementLike[];
  if (triggers.length === 0) {
    return false;
  }

  let portal: { hideIfStale: (trigger: PortalTrigger) => void } | null = null;
  const portalDoc = doc as unknown as PortalDocumentLike;
  const portalWin = windowLike as unknown as PortalWindowLike;
  if (
    typeof portalDoc.createElement === "function" &&
    typeof portalDoc.body?.appendChild === "function"
  ) {
    portal = wireTreeTitleTooltipPortal(
      triggers as unknown as PortalTrigger[],
      portalDoc,
      portalWin,
    );
  }

  const updateAll = (): void => {
    for (const trigger of triggers) {
      updateTrigger(trigger);
      portal?.hideIfStale(trigger as unknown as PortalTrigger);
    }
  };

  // A synchronous
  // pass here is correct for most rows, but two real sources of drift
  // exist -- (1) a row inside a folder that starts collapsed is
  // `display: none` (app.css, `.tree-nav__folder-notes`) until its own
  // `toggle` fires, so it measures 0/0 (never "truncated") until then; (2)
  // if a web font is still loading/swapping at this exact moment, the
  // measurement can run against fallback-font metrics. Neither is a timing
  // race this module can fix by measuring *harder* right now -- both need
  // a *later* re-measurement once the real condition resolves, which is
  // exactly what the listeners below provide, not an arbitrary timeout.
  updateAll();

  windowLike.addEventListener("resize", updateAll);

  // A single extra frame, scheduled once at init, catches any other
  // layout not yet settled at script-execution time (e.g. late style
  // recalculation) without guessing at a timeout duration.
  if (typeof windowLike.requestAnimationFrame === "function") {
    windowLike.requestAnimationFrame(updateAll);
  }

  const fontsLike = (doc as unknown as { fonts?: { ready?: Promise<unknown> } })
    .fonts;
  if (fontsLike?.ready && typeof fontsLike.ready.then === "function") {
    fontsLike.ready.then(updateAll).catch(() => {});
  }

  const folderDetailsList = Array.from(
    doc.querySelectorAll(FOLDER_DETAILS_SELECTOR),
  ) as Array<{ addEventListener(type: "toggle", listener: () => void): void }>;
  for (const details of folderDetailsList) {
    details.addEventListener("toggle", updateAll);
  }

  const ResizeObserverCtor = windowLike.ResizeObserver as
    | (new (callback: () => void) => TreeTitleResizeObserverLike)
    | undefined;
  if (typeof ResizeObserverCtor === "function") {
    const observer = new ResizeObserverCtor(updateAll);
    const viewports = Array.from(doc.querySelectorAll(VIEWPORT_SELECTOR));
    for (const viewport of viewports) {
      observer.observe(viewport);
    }
  }

  return true;
}

/**
 * Re-measures one already-rendered label directly, without a full
 * `querySelectorAll` pass -- called from `tree-title-sync.ts` right after
 * it updates a label's `textContent`, the one point in this app where a
 * tree title's text (and therefore its truncation state) can change
 * outside a full page render.
 */
export function refreshTreeTitleTooltipForLabel(
  label: TreeTitleUpdatableLabelElementLike | null,
): void {
  if (!label) {
    return;
  }
  const trigger = label.closest(TRIGGER_SELECTOR);
  if (trigger) {
    updateTrigger(trigger);
  }
}
