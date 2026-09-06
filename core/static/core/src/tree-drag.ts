import { TREE_NARROW_BREAKPOINT, type TreeShellWindowLike } from "./tree-shell";

export const TREE_DRAG_ACTIVE_CLASS = "tree-nav__folder-row--drop-active";
export const TREE_DRAG_TRASH_ACTIVE_CLASS =
  "tree-nav__trash-target--drop-active";
export const TREE_DRAG_HANDLE_DRAGGING_CLASS =
  "tree-nav__drag-handle--dragging";

export type TreeDragItemKind = "note" | "folder";

export interface TreeDragDataTransferLike {
  dropEffect: string;
  effectAllowed: string;
  setData(format: string, data: string): void;
}

export interface TreeDragEventLike {
  dataTransfer?: TreeDragDataTransferLike | null;
  preventDefault(): void;
  relatedTarget?: unknown;
}

export interface TreeDragClassListLike {
  add(name: string): void;
  remove(name: string): void;
}

export interface TreeDragHandleLike {
  addEventListener(
    type: "dragstart" | "dragend",
    listener: (event: TreeDragEventLike) => void,
  ): void;
  classList: TreeDragClassListLike;
}

export interface TreeDropTargetLike {
  addEventListener(
    type: "dragenter" | "dragover" | "dragleave" | "drop",
    listener: (event: TreeDragEventLike) => void,
  ): void;
  classList: TreeDragClassListLike;
  contains?(node: unknown): boolean;
}

export interface TreeMoveSelectLike {
  value: string;
}

export interface TreeSubmittableFormLike {
  action: string;
  requestSubmit?(): void;
  submit(): void;
}

export interface TreeDragHandleConfig {
  /** Only meaningful for `itemKind: "note"` -- unused for folders, since
   * folders have no "current group" a same-group drop could no-op against. */
  currentFolderValue: string;
  handle: TreeDragHandleLike;
  itemId: string;
  itemKind: TreeDragItemKind;
}

export type TreeDropTargetConfig =
  | { kind: "folder"; folderValue: string; target: TreeDropTargetLike }
  | { kind: "trash"; target: TreeDropTargetLike };

export interface TreeDragCloseTriggerLike {
  addEventListener(type: "click", listener: () => void): void;
}

export interface TreeDragKeydownDocumentLike {
  addEventListener(
    type: "keydown",
    listener: (event: { key: string }) => void,
  ): void;
}

export interface TreeDragAndDropOptions {
  closeTriggers?: ReadonlyArray<TreeDragCloseTriggerLike>;
  documentLike?: TreeDragKeydownDocumentLike;
  dropTargets: ReadonlyArray<TreeDropTargetConfig>;
  folderSelect: TreeMoveSelectLike;
  folderTrashForm: TreeSubmittableFormLike;
  /** Reversed with a `0` placeholder folder ID, mirroring `moveUrlTemplate`. */
  folderTrashUrlTemplate: string;
  handles: ReadonlyArray<TreeDragHandleConfig>;
  moveForm: TreeSubmittableFormLike;
  /**
   * The move form's `action` is bound to a single fixed note (the
   * note-detail page's own note) or has no meaningful default (Home's
   * hidden form), not to arbitrary dragged tree rows. `moveUrlTemplate` is
   * the container's own move route (`notes:note_move` in note-detail
   * context, `notes:note_move_home` in Home context) reversed with a `0`
   * placeholder note ID (see the shared tree partial's
   * `data-move-url-template`), so a drop on any row can retarget the exact
   * same form/route at the dragged note's real ID before submitting.
   */
  moveUrlTemplate: string;
  noteTrashForm: TreeSubmittableFormLike;
  /** Reversed with a `0` placeholder note ID, mirroring `moveUrlTemplate`. */
  noteTrashUrlTemplate: string;
  windowLike?: TreeShellWindowLike;
}

const MOVE_URL_PLACEHOLDER_SEGMENT = "/0/move/";
const TRASH_URL_PLACEHOLDER_SEGMENT = "/0/delete/";

function buildMoveUrl(template: string, noteId: string): string {
  return template.replace(MOVE_URL_PLACEHOLDER_SEGMENT, `/${noteId}/move/`);
}

function buildTrashUrl(template: string, itemId: string): string {
  return template.replace(TRASH_URL_PLACEHOLDER_SEGMENT, `/${itemId}/delete/`);
}

function submitForm(form: TreeSubmittableFormLike): void {
  if (typeof form.requestSubmit === "function") {
    form.requestSubmit();
  } else {
    form.submit();
  }
}

/**
 * Owns tree drag-and-drop: a dedicated drag handle per note/folder row
 * supplements (never replaces) the existing move/trash submission forms,
 * which remain the sole authoritative backend submission path -- a drop
 * never performs a real move or trash client-side, it only retargets one
 * of these existing forms to the dragged item's real ID and submits it,
 * exactly the way the pre-existing note-move behavior already worked. In
 * note-detail context the move form is the visible Move note disclosure;
 * in Home's narrow-drawer context (no visible Move disclosure) it is a
 * hidden, JS-only form carrying the same `.tree-nav__action--move`
 * marker so this controller finds and drives it identically either way.
 * The note-trash and folder-trash forms (`.tree-nav__action--trash-note`/
 * `-folder`) are always hidden -- there is no visible "confirm" step for
 * a drag-driven trash, matching the note-detail quick-trash button's own
 * no-confirmation contract.
 */
export class TreeDragAndDropController {
  private draggingItemId: string | null = null;
  private draggingItemKind: TreeDragItemKind | null = null;
  private draggingFolderValue: string | null = null;
  private draggingHandle: TreeDragHandleLike | null = null;
  private activeTarget: TreeDropTargetConfig | null = null;

  constructor(private readonly options: TreeDragAndDropOptions) {
    this.installHandlers();
  }

  /** Exposed for drawer-close/backdrop/Escape triggers outside normal drag events. */
  resetForExternalClose(): void {
    this.resetDragState();
  }

  private installHandlers(): void {
    for (const config of this.options.handles) {
      config.handle.addEventListener("dragstart", (event) => {
        this.draggingItemId = config.itemId;
        this.draggingItemKind = config.itemKind;
        this.draggingFolderValue = config.currentFolderValue;
        this.draggingHandle = config.handle;
        config.handle.classList.add(TREE_DRAG_HANDLE_DRAGGING_CLASS);
        if (event.dataTransfer) {
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", "move");
        }
      });

      config.handle.addEventListener("dragend", () => {
        this.resetDragState();
      });
    }

    for (const config of this.options.dropTargets) {
      const activeClass =
        config.kind === "trash"
          ? TREE_DRAG_TRASH_ACTIVE_CLASS
          : TREE_DRAG_ACTIVE_CLASS;

      const handleDragOver = (event: TreeDragEventLike): void => {
        if (this.draggingItemId === null) return;
        // A folder is only ever a valid drop payload for the Trash
        // destination -- there is no folder-to-folder nesting in this
        // app, so a folder dragged over another folder row shows no
        // valid-drop feedback at all and a drop there is a no-op below.
        if (config.kind === "folder" && this.draggingItemKind !== "note") {
          return;
        }
        event.preventDefault();
        if (event.dataTransfer) {
          event.dataTransfer.dropEffect = "move";
        }
        this.setActiveTarget(config, activeClass);
      };

      config.target.addEventListener("dragenter", handleDragOver);
      config.target.addEventListener("dragover", handleDragOver);

      config.target.addEventListener("dragleave", (event) => {
        if (this.activeTarget !== config) return;
        const related = event.relatedTarget;
        if (related && config.target.contains?.(related)) {
          return;
        }
        this.clearActiveTarget();
      });

      config.target.addEventListener("drop", (event) => {
        event.preventDefault();
        this.clearActiveTarget();
        const draggingItemId = this.draggingItemId;
        const draggingItemKind = this.draggingItemKind;
        const draggingFolderValue = this.draggingFolderValue;
        this.resetDragState();
        if (draggingItemId === null || draggingItemKind === null) return;

        if (config.kind === "folder") {
          if (draggingItemKind !== "note") {
            // Folder-onto-folder: not a supported interaction.
            return;
          }
          if (draggingFolderValue === config.folderValue) {
            // Same-group drop: silent client-side no-op, no request submitted.
            return;
          }
          this.options.folderSelect.value = config.folderValue;
          this.options.moveForm.action = buildMoveUrl(
            this.options.moveUrlTemplate,
            draggingItemId,
          );
          submitForm(this.options.moveForm);
          return;
        }

        // Trash.
        if (draggingItemKind === "note") {
          this.options.noteTrashForm.action = buildTrashUrl(
            this.options.noteTrashUrlTemplate,
            draggingItemId,
          );
          submitForm(this.options.noteTrashForm);
        } else {
          this.options.folderTrashForm.action = buildTrashUrl(
            this.options.folderTrashUrlTemplate,
            draggingItemId,
          );
          submitForm(this.options.folderTrashForm);
        }
      });
    }

    this.options.windowLike?.addEventListener("resize", () => {
      this.resetDragState();
    });

    for (const trigger of this.options.closeTriggers ?? []) {
      trigger.addEventListener("click", () => this.resetDragState());
    }

    this.options.documentLike?.addEventListener("keydown", (event) => {
      if (event.key === "Escape") {
        this.resetDragState();
      }
    });
  }

  private setActiveTarget(
    config: TreeDropTargetConfig,
    activeClass: string,
  ): void {
    if (this.activeTarget === config) return;
    this.clearActiveTarget();
    config.target.classList.add(activeClass);
    this.activeTarget = config;
  }

  private clearActiveTarget(): void {
    if (this.activeTarget) {
      const activeClass =
        this.activeTarget.kind === "trash"
          ? TREE_DRAG_TRASH_ACTIVE_CLASS
          : TREE_DRAG_ACTIVE_CLASS;
      this.activeTarget.target.classList.remove(activeClass);
      this.activeTarget = null;
    }
  }

  private resetDragState(): void {
    this.draggingItemId = null;
    this.draggingItemKind = null;
    this.draggingFolderValue = null;
    if (this.draggingHandle) {
      this.draggingHandle.classList.remove(TREE_DRAG_HANDLE_DRAGGING_CLASS);
      this.draggingHandle = null;
    }
    this.clearActiveTarget();
  }
}

function resolveGroupFolderValue(groupEl: HTMLElement | null): string {
  if (!groupEl) return "";
  if (groupEl.hasAttribute("data-folder-target")) return "";
  return groupEl.getAttribute("data-folder-id") ?? "";
}

/**
 * Activates drag-and-drop only for `.tree-nav` containers that already carry
 * a `.tree-nav__action--move` submission form (visible in note-detail
 * context, hidden in Home's narrow-drawer context) and at least one drag
 * handle. Home's flat list is a separate template with neither, so this
 * intentionally no-ops there without inspecting any other page signal.
 */
export function initTreeDragAndDropDocument(
  doc: Document = document,
  windowLike: TreeShellWindowLike = typeof window !== "undefined"
    ? window
    : ({
        addEventListener() {},
        innerWidth: TREE_NARROW_BREAKPOINT,
      } as TreeShellWindowLike),
): boolean {
  let initializedAny = false;
  const containers = Array.from(doc.querySelectorAll<HTMLElement>(".tree-nav"));

  for (const container of containers) {
    if (container.dataset.dragInitialized === "true") {
      continue;
    }

    const moveForm = container.querySelector<HTMLFormElement>(
      ".tree-nav__action--move form",
    );
    const folderSelect =
      moveForm?.querySelector<HTMLSelectElement>('select[name="folder"]') ??
      null;
    const moveUrlTemplate = container.getAttribute("data-move-url-template");
    const noteTrashForm = container.querySelector<HTMLFormElement>(
      ".tree-nav__action--trash-note form",
    );
    const noteTrashUrlTemplate = container.getAttribute(
      "data-note-trash-url-template",
    );
    const folderTrashForm = container.querySelector<HTMLFormElement>(
      ".tree-nav__action--trash-folder form",
    );
    const folderTrashUrlTemplate = container.getAttribute(
      "data-folder-trash-url-template",
    );
    const handleElements = Array.from(
      container.querySelectorAll<HTMLElement>(".tree-nav__drag-handle"),
    );

    if (
      !moveForm ||
      !folderSelect ||
      !moveUrlTemplate ||
      !noteTrashForm ||
      !noteTrashUrlTemplate ||
      !folderTrashForm ||
      !folderTrashUrlTemplate ||
      handleElements.length === 0
    ) {
      continue;
    }

    const handles: TreeDragHandleConfig[] = handleElements.map((handle) => {
      const noteEl = handle.closest<HTMLElement>("[data-note-id]");
      if (noteEl) {
        const groupEl = handle.closest<HTMLElement>(
          "[data-folder-id],[data-folder-target='unfiled']",
        );
        return {
          currentFolderValue: resolveGroupFolderValue(groupEl),
          handle,
          itemId: noteEl.getAttribute("data-note-id") ?? "",
          itemKind: "note" as const,
        };
      }
      const folderEl = handle.closest<HTMLElement>("[data-folder-id]");
      return {
        currentFolderValue: "",
        handle,
        itemId: folderEl?.getAttribute("data-folder-id") ?? "",
        itemKind: "folder" as const,
      };
    });

    const folderDropTargetElements = Array.from(
      container.querySelectorAll<HTMLElement>(".tree-nav__folder-row"),
    );
    const dropTargets: TreeDropTargetConfig[] = folderDropTargetElements.map(
      (target) => {
        const groupEl = target.closest<HTMLElement>(
          "[data-folder-id],[data-folder-target='unfiled']",
        );
        return {
          kind: "folder" as const,
          folderValue: resolveGroupFolderValue(groupEl),
          target,
        };
      },
    );

    // The Trash destination: this container's own fixed tree row.
    // The collapsed rail only reveals/hides the tree and carries no Trash
    // icon of its own, so this is the
    // sole drop target in both the wide tree and the narrow drawer.
    const trashTargetElements = Array.from(
      container.querySelectorAll<HTMLElement>("[data-tree-trash-target]"),
    );
    for (const target of trashTargetElements) {
      dropTargets.push({ kind: "trash" as const, target });
    }

    const drawerAncestor = container.closest<HTMLElement>("[data-drawer]");
    const closeTriggers: TreeDragCloseTriggerLike[] = [];
    if (drawerAncestor) {
      const drawerClose = drawerAncestor.querySelector<HTMLElement>(
        "[data-drawer-close]",
      );
      if (drawerClose) closeTriggers.push(drawerClose);
      const backdrop = doc.querySelector<HTMLElement>("[data-drawer-backdrop]");
      if (backdrop) closeTriggers.push(backdrop);
    }

    new TreeDragAndDropController({
      ...(closeTriggers.length > 0 ? { closeTriggers } : {}),
      ...(drawerAncestor ? { documentLike: doc } : {}),
      dropTargets,
      folderSelect,
      folderTrashForm,
      folderTrashUrlTemplate,
      handles,
      moveForm,
      moveUrlTemplate,
      noteTrashForm,
      noteTrashUrlTemplate,
      windowLike,
    });

    container.dataset.dragInitialized = "true";
    initializedAny = true;
  }

  return initializedAny;
}
