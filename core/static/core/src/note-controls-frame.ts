const FRAME_SELECTOR = "[data-note-controls-frame]";
const RIBBON_SELECTOR = ".note-workspace__ribbon";
const CSS_CUSTOM_PROPERTY = "--note-controls-frame-height";

export interface ControlsFrameElementLike {
  getBoundingClientRect(): { top: number };
  style: { setProperty(name: string, value: string): void };
}

export interface ControlsFrameRibbonLike {
  getBoundingClientRect(): { bottom: number };
}

export interface ControlsFrameDocumentLike {
  querySelector<T>(selector: string): T | null;
}

export interface ControlsFrameResizeObserverLike {
  observe(target: unknown): void;
}

export interface ControlsFrameComputedStyleLike {
  marginBottom: string;
}

export interface ControlsFrameWindowLike {
  addEventListener(type: "resize", listener: () => void): void;
  ResizeObserver?: unknown;
  getComputedStyle(el: unknown): ControlsFrameComputedStyleLike;
}

/**
 * `.note-controls-frame` is
 * a decorative, absolutely-positioned box drawn behind the tag panel and
 * the title/actions ribbon (which already contains the formatting toolbar
 * when shown) to read as one outer controls container -- see the comment
 * on `.note-controls-frame` in app.css for why a literal DOM wrapper isn't
 * used instead. Its height can't be expressed in static CSS since the
 * combined height of those two elements changes with tag count, validation
 * errors, the save notice, and the formatting toolbar's visibility -- so,
 * matching `workspace-header-height.ts`'s "measure real layout, publish a
 * CSS custom property" approach, this measures the real gap between the
 * frame's own top (anchored to the note workspace's padding edge) and the
 * ribbon's rendered bottom edge, and publishes it directly onto the frame
 * element's own inline style. A `ResizeObserver` on the ribbon (whose
 * children include the tag-triggered notice and the formatting toolbar)
 * keeps it in sync with content changes that don't fire a window resize
 * event, with `resize` itself kept as a fallback for viewport/zoom changes.
 *
 * The `getComputedStyle(ribbon).marginBottom` term below accounts for
 * this: without it, the frame's bottom
 * edge would land exactly at the ribbon's own rendered bottom, with
 * no trailing space at all (`getBoundingClientRect().bottom` never
 * reflects an element's own `margin-bottom`). `.note-workspace__ribbon`
 * now carries a real `margin-bottom` in app.css for exactly this reading,
 * giving the frame a measurable, real value to add rather than a
 * hard-coded pixel guess -- reusing the CSS as the single source of truth
 * the same way `--space-6` already is for the tag panel's own top gap.
 */
export function initNoteControlsFrameDocument(
  doc: ControlsFrameDocumentLike = typeof document !== "undefined"
    ? (document as unknown as ControlsFrameDocumentLike)
    : { querySelector: () => null },
  windowLike: ControlsFrameWindowLike = typeof window !== "undefined"
    ? (window as unknown as ControlsFrameWindowLike)
    : {
        addEventListener: () => {},
        getComputedStyle: () => ({ marginBottom: "0px" }),
      },
): boolean {
  const frame = doc.querySelector<ControlsFrameElementLike>(FRAME_SELECTOR);
  const ribbon = doc.querySelector<ControlsFrameRibbonLike>(RIBBON_SELECTOR);
  if (!frame || !ribbon) {
    return false;
  }

  const measure = (): void => {
    const top = frame.getBoundingClientRect().top;
    const bottom = ribbon.getBoundingClientRect().bottom;
    const marginBottom =
      parseFloat(windowLike.getComputedStyle(ribbon).marginBottom) || 0;
    const height = bottom - top + marginBottom;
    if (height > 0) {
      frame.style.setProperty(CSS_CUSTOM_PROPERTY, `${height}px`);
    }
  };

  measure();
  windowLike.addEventListener("resize", measure);

  const ResizeObserverCtor = windowLike.ResizeObserver as
    | (new (callback: () => void) => ControlsFrameResizeObserverLike)
    | undefined;
  if (typeof ResizeObserverCtor === "function") {
    const observer = new ResizeObserverCtor(measure);
    observer.observe(ribbon);
  }

  return true;
}
