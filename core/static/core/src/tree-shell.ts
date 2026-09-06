export const TREE_COLLAPSED_STORAGE_KEY = "ridgenote.tree.collapsed";
export const TREE_WIDTH_STORAGE_KEY = "ridgenote.tree.width";
export const TREE_DEFAULT_WIDTH = 220;
export const TREE_MIN_WIDTH = 160;
export const TREE_MAX_WIDTH = 400;
export const TREE_KEYBOARD_STEP = 8;
export const TREE_NARROW_BREAKPOINT = 640;

const FOCUSABLE_SELECTOR =
  'a[href],button:not([disabled]),input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])';

export interface TreeShellStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export interface TreeShellToggleLike {
  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void;
  focus(): void;
  hidden: boolean;
  setAttribute(name: string, value: string): void;
}

export interface TreeShellPaneLike {
  getBoundingClientRect(): { left: number };
  hidden: boolean;
}

export interface TreeShellRootLike {
  dataset?: {
    initialized?: string;
  };
  setAttribute(name: string, value: string): void;
  style: {
    setProperty(name: string, value: string): void;
  };
}

export interface TreeShellSeparatorLike {
  addEventListener(
    type: "keydown" | "pointerdown",
    listener: (
      event:
        | { key: string; preventDefault(): void }
        | { clientX: number; pointerId: number; preventDefault(): void },
    ) => void,
  ): void;
  hidden: boolean;
  releasePointerCapture?(pointerId: number): void;
  setAttribute(name: string, value: string): void;
  setPointerCapture?(pointerId: number): void;
}

export interface TreeShellWindowLike {
  addEventListener(
    type: "pointercancel" | "pointermove" | "pointerup" | "resize",
    listener: (event?: {
      clientX: number;
      pointerId: number;
      preventDefault(): void;
    }) => void,
  ): void;
  innerWidth: number;
}

export interface TreeShellFocusableLike {
  focus(): void;
}

export interface TreeShellDrawerToggleLike {
  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void;
  focus(): void;
  hidden: boolean;
  setAttribute(name: string, value: string): void;
}

export interface TreeShellDrawerCloseLike {
  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void;
  focus(): void;
}

export interface TreeShellDrawerLike {
  addEventListener(
    type: "keydown",
    listener: (event: {
      key: string;
      shiftKey: boolean;
      preventDefault(): void;
    }) => void,
  ): void;
  dataset?: {
    initialized?: string;
  };
  hidden: boolean;
  querySelectorAll(selector: string): ArrayLike<TreeShellFocusableLike>;
  removeAttribute(name: string): void;
  setAttribute(name: string, value: string): void;
}

export interface TreeShellBackdropLike {
  addEventListener(type: "click", listener: () => void): void;
  hidden: boolean;
}

export interface TreeShellDocumentLike {
  activeElement: TreeShellFocusableLike | null;
  addEventListener(
    type: "keydown",
    listener: (event: {
      key: string;
      shiftKey: boolean;
      preventDefault(): void;
    }) => void,
  ): void;
  body: { style: { overflow: string } };
  documentElement: { style: { overflow: string } };
}

export interface TreeShellInertTargetLike {
  removeAttribute(name: string): void;
  setAttribute(name: string, value: string): void;
}

export interface DrawerControllerOptions {
  backdrop?: TreeShellBackdropLike;
  documentLike?: TreeShellDocumentLike;
  drawer: TreeShellDrawerLike;
  drawerClose?: TreeShellDrawerCloseLike;
  drawerToggle: TreeShellDrawerToggleLike;
  inertTargets?: ReadonlyArray<TreeShellInertTargetLike>;
  isNarrow(): boolean;
  windowLike?: TreeShellWindowLike;
}

/**
 * Owns narrow-drawer open/close, focus entry/restoration, Escape, backdrop,
 * focus containment, inert handling, and body-scroll lock independently of
 * any wide-tree split-pane elements, so it can run standalone (e.g. Home) or
 * be composed inside TreeShellController (note detail) without duplication.
 */
export class DrawerController {
  private open = false;

  constructor(private readonly options: DrawerControllerOptions) {
    this.installHandlers();
    if (options.windowLike) {
      options.windowLike.addEventListener("resize", () => this.closeIfWide());
    }
  }

  isOpen(): boolean {
    return this.open;
  }

  closeIfWide(): void {
    if (!this.options.isNarrow() && this.open) {
      this.setOpen(false, false);
    }
  }

  private installHandlers(): void {
    const { backdrop, documentLike, drawer, drawerClose, drawerToggle } =
      this.options;

    drawerToggle.addEventListener("click", (event) => {
      event.preventDefault();
      if (this.options.isNarrow()) {
        this.setOpen(true);
      }
    });

    drawerClose?.addEventListener("click", (event) => {
      event.preventDefault();
      this.setOpen(false);
    });

    backdrop?.addEventListener("click", () => {
      this.setOpen(false);
    });

    drawer.addEventListener("keydown", (event) => {
      if (!this.open || event.key !== "Tab") {
        return;
      }
      const focusable = Array.from(drawer.querySelectorAll(FOCUSABLE_SELECTOR));
      if (focusable.length === 0) {
        event.preventDefault();
        return;
      }
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      const active = documentLike?.activeElement ?? null;
      if (event.shiftKey && active === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && active === last) {
        event.preventDefault();
        first.focus();
      }
    });

    documentLike?.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this.open) {
        event.preventDefault();
        this.setOpen(false);
      }
    });
  }

  setOpen(open: boolean, restoreFocus = true): void {
    const { backdrop, documentLike, drawer, drawerToggle, inertTargets } =
      this.options;
    this.open = open;
    if (open) {
      drawer.setAttribute("data-open", "true");
      drawer.hidden = false;
      drawer.removeAttribute("inert");
      if (backdrop) {
        backdrop.hidden = false;
      }
      drawerToggle.setAttribute("aria-expanded", "true");
      if (documentLike) {
        // Locking only `body`
        // is insufficient -- wheel input over the drawer's own non-scrolling regions
        // (header, footer/account, empty space) still scrolled the page
        // behind it. Root cause: the narrow-layout rule elsewhere in this
        // file that sets `overflow-x: hidden` on both `html` and `body`
        // (to prevent horizontal overflow) gives `html` an explicitly
        // non-"visible" computed overflow, which disqualifies the
        // standard CSS body-to-viewport overflow-propagation mechanism
        // (it only propagates body's overflow to the viewport when the
        // root element's own computed overflow is exactly the initial
        // "visible"). Once disqualified, `html` becomes its own
        // independent scrolling box using its own overflow-y (never
        // explicitly set, so effectively "auto"), completely bypassing
        // `body.style.overflow = "hidden"`. Locking `documentElement`
        // (`html`) directly, the same way, addresses the actual
        // scrolling element rather than the one that merely usually is
        // one -- confirmed to fully stop the leak in the same real
        // browser test that found it.
        documentLike.body.style.overflow = "hidden";
        documentLike.documentElement.style.overflow = "hidden";
      }
      for (const target of inertTargets ?? []) {
        target.setAttribute("inert", "");
      }
      const focusable = Array.from(drawer.querySelectorAll(FOCUSABLE_SELECTOR));
      if (focusable.length > 0) {
        focusable[0].focus();
      }
    } else {
      drawer.setAttribute("data-open", "false");
      drawer.hidden = true;
      drawer.setAttribute("inert", "");
      if (backdrop) {
        backdrop.hidden = true;
      }
      drawerToggle.setAttribute("aria-expanded", "false");
      if (documentLike) {
        documentLike.body.style.overflow = "";
        documentLike.documentElement.style.overflow = "";
      }
      for (const target of inertTargets ?? []) {
        target.removeAttribute("inert");
      }
      if (restoreFocus) {
        drawerToggle.focus();
      }
    }
  }
}

export interface TreeShellControllerOptions {
  root: TreeShellRootLike;
  separator: TreeShellSeparatorLike;
  storage?: TreeShellStorageLike;
  toggle: TreeShellToggleLike;
  treePane: TreeShellPaneLike;
  windowLike: TreeShellWindowLike;
  backdrop?: TreeShellBackdropLike;
  documentLike?: TreeShellDocumentLike;
  drawer?: TreeShellDrawerLike;
  drawerClose?: TreeShellDrawerCloseLike;
  drawerToggle?: TreeShellDrawerToggleLike;
  inertTargets?: ReadonlyArray<TreeShellInertTargetLike>;
}

export function clampTreeWidth(width: number): number {
  if (!Number.isFinite(width)) {
    return TREE_DEFAULT_WIDTH;
  }
  return Math.min(TREE_MAX_WIDTH, Math.max(TREE_MIN_WIDTH, Math.round(width)));
}

export function parseStoredTreeCollapsed(rawValue: string | null): boolean {
  if (rawValue === "true") {
    return true;
  }
  if (rawValue === "false" || rawValue === null) {
    return false;
  }
  return false;
}

export function parseStoredTreeWidth(rawValue: string | null): number {
  if (rawValue === null) {
    return TREE_DEFAULT_WIDTH;
  }
  const parsed = Number(rawValue);
  return clampTreeWidth(parsed);
}

function safeGetItem(
  storage: TreeShellStorageLike | undefined,
  key: string,
): string | null {
  if (!storage) {
    return null;
  }
  try {
    return storage.getItem(key);
  } catch {
    return null;
  }
}

function safeSetItem(
  storage: TreeShellStorageLike | undefined,
  key: string,
  value: string,
): void {
  if (!storage) {
    return;
  }
  try {
    storage.setItem(key, value);
  } catch {
    // Ignore browser-local persistence failures and keep the workspace usable.
  }
}

export class TreeShellController {
  private collapsed = false;
  private pointerId: number | null = null;
  private width = TREE_DEFAULT_WIDTH;
  private readonly drawerController: DrawerController | null;

  constructor(private readonly options: TreeShellControllerOptions) {
    this.collapsed = parseStoredTreeCollapsed(
      safeGetItem(options.storage, TREE_COLLAPSED_STORAGE_KEY),
    );
    this.width = parseStoredTreeWidth(
      safeGetItem(options.storage, TREE_WIDTH_STORAGE_KEY),
    );
    this.drawerController =
      options.drawerToggle && options.drawer
        ? new DrawerController({
            drawer: options.drawer,
            drawerToggle: options.drawerToggle,
            ...(options.drawerClose
              ? { drawerClose: options.drawerClose }
              : {}),
            ...(options.backdrop ? { backdrop: options.backdrop } : {}),
            ...(options.documentLike
              ? { documentLike: options.documentLike }
              : {}),
            ...(options.inertTargets
              ? { inertTargets: options.inertTargets }
              : {}),
            isNarrow: () => this.isNarrowLayout(),
          })
        : null;
    this.installEventHandlers();
    this.render();
  }

  getState(): { collapsed: boolean; narrow: boolean; width: number } {
    return {
      collapsed: this.collapsed,
      narrow: this.isNarrowLayout(),
      width: this.width,
    };
  }

  private installEventHandlers(): void {
    this.options.toggle.addEventListener("click", (event) => {
      event.preventDefault();
      this.setCollapsed(!this.collapsed, true);
      this.options.toggle.focus();
    });

    this.options.separator.addEventListener("pointerdown", (event) => {
      const pointerEvent = event as {
        clientX: number;
        pointerId: number;
        preventDefault(): void;
      };
      if (this.collapsed || this.isNarrowLayout()) {
        return;
      }
      pointerEvent.preventDefault();
      this.pointerId = pointerEvent.pointerId;
      this.options.root.setAttribute("data-tree-resizing", "true");
      this.options.separator.setPointerCapture?.(pointerEvent.pointerId);
      this.updateWidthFromClientX(pointerEvent.clientX, false);
    });

    this.options.separator.addEventListener("keydown", (event) => {
      const keyEvent = event as { key: string; preventDefault(): void };
      let nextWidth: number | null = null;
      switch (keyEvent.key) {
        case "ArrowLeft":
          nextWidth = this.width - TREE_KEYBOARD_STEP;
          break;
        case "ArrowRight":
          nextWidth = this.width + TREE_KEYBOARD_STEP;
          break;
        case "Home":
          nextWidth = TREE_MIN_WIDTH;
          break;
        case "End":
          nextWidth = TREE_MAX_WIDTH;
          break;
        default:
          return;
      }
      keyEvent.preventDefault();
      this.setWidth(nextWidth, true);
    });

    this.options.windowLike.addEventListener("pointermove", (event) => {
      const pointerEvent = event as
        | { clientX: number; pointerId: number; preventDefault(): void }
        | undefined;
      if (!pointerEvent || this.pointerId === null) {
        return;
      }
      if (pointerEvent.pointerId !== this.pointerId) {
        return;
      }
      pointerEvent.preventDefault();
      this.updateWidthFromClientX(pointerEvent.clientX, false);
    });

    const finishResize = (event?: {
      clientX: number;
      pointerId: number;
      preventDefault(): void;
    }) => {
      if (
        !event ||
        this.pointerId === null ||
        event.pointerId !== this.pointerId
      ) {
        return;
      }
      event.preventDefault();
      this.options.separator.releasePointerCapture?.(event.pointerId);
      this.pointerId = null;
      this.options.root.setAttribute("data-tree-resizing", "false");
      safeSetItem(
        this.options.storage,
        TREE_WIDTH_STORAGE_KEY,
        String(this.width),
      );
    };

    this.options.windowLike.addEventListener("pointerup", finishResize);
    this.options.windowLike.addEventListener("pointercancel", finishResize);
    this.options.windowLike.addEventListener("resize", () => {
      this.render();
      this.drawerController?.closeIfWide();
    });
  }

  private isNarrowLayout(): boolean {
    return this.options.windowLike.innerWidth < TREE_NARROW_BREAKPOINT;
  }

  private setCollapsed(collapsed: boolean, persist: boolean): void {
    this.collapsed = collapsed;
    if (persist) {
      safeSetItem(
        this.options.storage,
        TREE_COLLAPSED_STORAGE_KEY,
        collapsed ? "true" : "false",
      );
    }
    this.render();
  }

  private setWidth(width: number, persist: boolean): void {
    this.width = clampTreeWidth(width);
    if (persist) {
      safeSetItem(
        this.options.storage,
        TREE_WIDTH_STORAGE_KEY,
        String(this.width),
      );
    }
    this.render();
  }

  private updateWidthFromClientX(clientX: number, persist: boolean): void {
    const left = this.options.treePane.getBoundingClientRect().left;
    this.setWidth(clientX - left, persist);
  }

  private render(): void {
    const narrow = this.isNarrowLayout();
    this.options.root.style.setProperty("--tree-width", `${this.width}px`);
    this.options.root.setAttribute(
      "data-tree-collapsed",
      this.collapsed ? "true" : "false",
    );
    this.options.root.setAttribute(
      "data-tree-layout",
      narrow ? "narrow" : "split",
    );
    this.options.toggle.hidden = narrow;
    this.options.toggle.setAttribute(
      "aria-expanded",
      this.collapsed ? "false" : "true",
    );
    this.options.toggle.setAttribute(
      "aria-label",
      this.collapsed ? "Show note tree" : "Hide note tree",
    );
    this.options.toggle.setAttribute(
      "data-tooltip",
      this.collapsed ? "Show note tree" : "Hide note tree",
    );
    this.options.treePane.hidden = narrow || this.collapsed;
    this.options.separator.hidden = narrow || this.collapsed;
    this.options.separator.setAttribute(
      "aria-valuemin",
      String(TREE_MIN_WIDTH),
    );
    this.options.separator.setAttribute(
      "aria-valuemax",
      String(TREE_MAX_WIDTH),
    );
    this.options.separator.setAttribute("aria-valuenow", String(this.width));
    if (this.options.drawerToggle) {
      this.options.drawerToggle.hidden = !narrow;
    }
  }
}

export function initTreeShellDocument(
  doc: Document = document,
  storage: TreeShellStorageLike | undefined = typeof window !== "undefined"
    ? window.localStorage
    : undefined,
  windowLike: TreeShellWindowLike = typeof window !== "undefined"
    ? window
    : ({
        addEventListener() {},
        innerWidth: TREE_NARROW_BREAKPOINT,
      } as TreeShellWindowLike),
): boolean {
  const root = doc.querySelector<HTMLElement>("[data-tree-shell]");
  if (!root || root.dataset.initialized === "true") {
    return false;
  }

  const toggle = root.querySelector<HTMLButtonElement>("[data-tree-toggle]");
  const treePane = root.querySelector<HTMLElement>("[data-tree-pane]");
  const separator = root.querySelector<HTMLElement>("[data-tree-separator]");

  if (!toggle || !treePane || !separator) {
    return false;
  }

  const drawerToggle = doc.querySelector<HTMLButtonElement>(
    "[data-drawer-toggle]",
  );
  const drawer = doc.querySelector<HTMLElement>("[data-drawer]");
  const drawerClose =
    drawer?.querySelector<HTMLButtonElement>("[data-drawer-close]") ?? null;
  const backdrop = doc.querySelector<HTMLElement>("[data-drawer-backdrop]");
  const workspaceHeader = doc.querySelector<HTMLElement>(
    "[data-workspace-header]",
  );
  const inertTargets: TreeShellInertTargetLike[] = (
    [workspaceHeader, root] as Array<HTMLElement | null>
  ).filter((el): el is HTMLElement => el !== null);

  new TreeShellController({
    root,
    separator,
    storage,
    toggle,
    treePane,
    windowLike,
    ...(drawerToggle !== null ? { drawerToggle } : {}),
    ...(drawer !== null ? { drawer } : {}),
    ...(drawerClose !== null ? { drawerClose } : {}),
    ...(backdrop !== null ? { backdrop } : {}),
    documentLike: doc as unknown as TreeShellDocumentLike,
    inertTargets,
  });
  root.dataset.initialized = "true";
  return true;
}

/**
 * Initializes a narrow navigation drawer on pages that have no wide-tree
 * split-pane shell (e.g. Home). Pages that do have a `[data-tree-shell]`
 * root are already fully handled by `initTreeShellDocument`, which composes
 * the same `DrawerController` internally, so this bootstrap intentionally
 * no-ops there to avoid initializing the drawer twice.
 */
export function initNarrowDrawerDocument(
  doc: Document = document,
  windowLike: TreeShellWindowLike = typeof window !== "undefined"
    ? window
    : ({
        addEventListener() {},
        innerWidth: TREE_NARROW_BREAKPOINT,
      } as TreeShellWindowLike),
): boolean {
  if (doc.querySelector("[data-tree-shell]")) {
    return false;
  }

  const drawer = doc.querySelector<HTMLElement>("[data-drawer]");
  const drawerToggle = doc.querySelector<HTMLButtonElement>(
    "[data-drawer-toggle]",
  );
  if (!drawer || !drawerToggle || drawer.dataset.initialized === "true") {
    return false;
  }

  const drawerClose =
    drawer.querySelector<HTMLButtonElement>("[data-drawer-close]") ?? null;
  const backdrop = doc.querySelector<HTMLElement>("[data-drawer-backdrop]");
  const inertTargets = Array.from(
    doc.querySelectorAll<HTMLElement>(
      "[data-app-header],[data-drawer-inert-target]",
    ),
  );

  new DrawerController({
    drawer,
    drawerToggle,
    ...(drawerClose !== null ? { drawerClose } : {}),
    ...(backdrop !== null ? { backdrop } : {}),
    documentLike: doc as unknown as TreeShellDocumentLike,
    inertTargets,
    isNarrow: () => windowLike.innerWidth < TREE_NARROW_BREAKPOINT,
    windowLike,
  });

  drawer.dataset.initialized = "true";
  return true;
}
