const ROW_SELECTOR = ".workspace-shell__tree-rail";
const CSS_CUSTOM_PROPERTY = "--workspace-tree-row-top";

export interface TreeRowTopElementLike {
  getBoundingClientRect(): { top: number };
}

export interface TreeRowTopRootStyleLike {
  setProperty(name: string, value: string): void;
}

export interface TreeRowTopRootLike {
  style: TreeRowTopRootStyleLike;
}

export interface TreeRowTopDocumentLike {
  documentElement: TreeRowTopRootLike;
  querySelector<T extends TreeRowTopElementLike>(selector: string): T | null;
}

export interface TreeRowTopWindowLike {
  addEventListener(type: "resize", listener: () => void): void;
}

/**
 * Home/All Notes size the tree rail/
 * pane to a `max-height` derived from "the viewport minus whatever sits
 * above it", so the fixed Trash destination inside it always ends up
 * within the visible viewport rather than needing a scroll to reach. The
 * existing `--workspace-header-height` (see `workspace-header-height.ts`)
 * only ever measured the global header -- correct on a plain page load,
 * but too generous whenever a Django messages banner (e.g. the redirect
 * after a quick-trash or drag-to-Trash action) also renders above the
 * tree, pushing its *real* on-page position further down than that
 * formula assumed. This measures the tree rail's own actual rendered
 * `top` directly -- inherently correct regardless of anything rendering
 * above it, banner or no banner -- rather than trying to separately
 * account for every possible thing that could occupy that space. Same
 * "measure real layout, publish a CSS custom property" shape as
 * `workspace-header-height.ts`; re-measures on resize, not scroll (the
 * rail's own `position: sticky` handles staying in view as the page
 * scrolls once this initial, correct height is set).
 */
export function initWorkspaceTreeRowTopDocument(
  doc: TreeRowTopDocumentLike = typeof document !== "undefined"
    ? (document as unknown as TreeRowTopDocumentLike)
    : {
        documentElement: { style: { setProperty: () => {} } },
        querySelector: () => null,
      },
  windowLike: TreeRowTopWindowLike = typeof window !== "undefined"
    ? window
    : { addEventListener: () => {} },
): boolean {
  const row = doc.querySelector<TreeRowTopElementLike>(ROW_SELECTOR);
  if (!row) {
    return false;
  }

  const measure = (): void => {
    const top = row.getBoundingClientRect().top;
    if (top > 0) {
      doc.documentElement.style.setProperty(CSS_CUSTOM_PROPERTY, `${top}px`);
    }
  };

  measure();
  windowLike.addEventListener("resize", measure);

  return true;
}
