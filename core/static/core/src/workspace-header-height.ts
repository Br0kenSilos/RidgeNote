const HEADER_SELECTOR = "[data-workspace-header]";
const CSS_CUSTOM_PROPERTY = "--workspace-header-height";

export interface HeaderHeightElementLike {
  getBoundingClientRect(): { height: number };
}

export interface HeaderHeightRootStyleLike {
  setProperty(name: string, value: string): void;
}

export interface HeaderHeightRootLike {
  style: HeaderHeightRootStyleLike;
}

export interface HeaderHeightDocumentLike {
  documentElement: HeaderHeightRootLike;
  querySelector<T extends HeaderHeightElementLike>(selector: string): T | null;
}

export interface HeaderHeightWindowLike {
  addEventListener(type: "resize", listener: () => void): void;
}

/**
 * The narrow note-workspace
 * ribbon needs to stick immediately below the also-sticky global header,
 * not overlap it -- which requires knowing the header's actual rendered
 * height. A hand-picked literal is a viewport-specific magic number that's
 * unreliable: header height is driven by real
 * content (icon-button sizing, padding, font metrics) that can differ
 * subtly across browsers, zoom levels, and viewport widths in ways a
 * single hard-coded guess can't track, and CSS alone has no way to read a
 * sibling's true rendered size. This measures it directly -- the same
 * "measure real layout, drive a CSS decision" shape already used by
 * `tooltip-placement.ts` for tooltip placement, not scroll-event handling
 * of any kind -- and publishes it as one shared CSS custom property that
 * both the header and the ribbon's own narrow `top` offset reference, so
 * they can never drift out of sync with each other or with reality.
 * Re-measures on resize (narrow breakpoint changes, orientation change,
 * font-size/zoom changes) but not on scroll.
 */
export function initWorkspaceHeaderHeightDocument(
  doc: HeaderHeightDocumentLike = typeof document !== "undefined"
    ? (document as unknown as HeaderHeightDocumentLike)
    : {
        documentElement: { style: { setProperty: () => {} } },
        querySelector: () => null,
      },
  windowLike: HeaderHeightWindowLike = typeof window !== "undefined"
    ? window
    : { addEventListener: () => {} },
): boolean {
  const header = doc.querySelector<HeaderHeightElementLike>(HEADER_SELECTOR);
  if (!header) {
    return false;
  }

  const measure = (): void => {
    const height = header.getBoundingClientRect().height;
    if (height > 0) {
      doc.documentElement.style.setProperty(CSS_CUSTOM_PROPERTY, `${height}px`);
    }
  };

  measure();
  windowLike.addEventListener("resize", measure);

  return true;
}
