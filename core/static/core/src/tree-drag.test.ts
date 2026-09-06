import { describe, expect, it } from "vitest";

import {
  TREE_DRAG_ACTIVE_CLASS,
  TREE_DRAG_HANDLE_DRAGGING_CLASS,
  TREE_DRAG_TRASH_ACTIVE_CLASS,
  TreeDragAndDropController,
  initTreeDragAndDropDocument,
  type TreeDragEventLike,
  type TreeDropTargetConfig,
} from "./tree-drag";

class MockClassList {
  adds: string[] = [];
  removes: string[] = [];

  add(name: string): void {
    this.adds.push(name);
  }

  remove(name: string): void {
    this.removes.push(name);
  }

  has(name: string): boolean {
    return (
      this.adds.includes(name) && !this.removes.slice().reverse().includes(name)
    );
  }
}

type Listener = (event: TreeDragEventLike) => void;

class MockHandle {
  classList = new MockClassList();
  private listeners = new Map<string, Listener[]>();

  addEventListener(type: string, listener: Listener): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  trigger(type: string, event: Partial<TreeDragEventLike> = {}): void {
    const defaultEvent: TreeDragEventLike = { preventDefault() {}, ...event };
    this.listeners.get(type)?.forEach((listener) => listener(defaultEvent));
  }
}

class MockDropTarget {
  classList = new MockClassList();
  containedNodes: unknown[] = [];
  private listeners = new Map<string, Listener[]>();

  addEventListener(type: string, listener: Listener): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  contains(node: unknown): boolean {
    return this.containedNodes.includes(node);
  }

  trigger(type: string, event: Partial<TreeDragEventLike> = {}): void {
    const defaultEvent: TreeDragEventLike = { preventDefault() {}, ...event };
    this.listeners.get(type)?.forEach((listener) => listener(defaultEvent));
  }
}

class MockSelect {
  value = "";
}

class MockForm {
  action = "";
  submitCalls = 0;
  requestSubmit(): void {
    this.submitCalls += 1;
  }
  submit(): void {
    this.submitCalls += 1;
  }
}

class MockWindowLike {
  innerWidth = 1280;
  private listeners = new Map<string, Array<() => void>>();

  addEventListener(type: string, listener: () => void): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  triggerResize(): void {
    this.listeners.get("resize")?.forEach((listener) => listener());
  }
}

class MockCloseTrigger {
  private listeners: Array<() => void> = [];

  addEventListener(_type: "click", listener: () => void): void {
    this.listeners.push(listener);
  }

  triggerClick(): void {
    this.listeners.forEach((listener) => listener());
  }
}

class MockDocumentLike {
  private listeners: Array<(event: { key: string }) => void> = [];

  addEventListener(
    _type: "keydown",
    listener: (event: { key: string }) => void,
  ): void {
    this.listeners.push(listener);
  }

  triggerKeydown(key: string): void {
    this.listeners.forEach((listener) => listener({ key }));
  }
}

function buildController(overrides?: {
  extraDropTarget?: MockDropTarget;
  extraFolderValue?: string;
  handleCurrentFolderValue?: string;
  handleItemKind?: "note" | "folder";
}) {
  const handle = new MockHandle();
  const folderTarget = new MockDropTarget();
  const unfiledTarget = new MockDropTarget();
  const trashTarget = new MockDropTarget();
  const folderSelect = new MockSelect();
  const moveForm = new MockForm();
  const noteTrashForm = new MockForm();
  const folderTrashForm = new MockForm();
  const windowLike = new MockWindowLike();
  const closeTrigger = new MockCloseTrigger();
  const documentLike = new MockDocumentLike();

  const dropTargets: TreeDropTargetConfig[] = [
    { kind: "folder", folderValue: "7", target: folderTarget },
    { kind: "folder", folderValue: "", target: unfiledTarget },
    { kind: "trash", target: trashTarget },
  ];
  if (overrides?.extraDropTarget) {
    dropTargets.push({
      kind: "folder",
      folderValue: overrides.extraFolderValue ?? "9",
      target: overrides.extraDropTarget,
    });
  }

  const controller = new TreeDragAndDropController({
    closeTriggers: [closeTrigger],
    documentLike,
    dropTargets,
    folderSelect,
    folderTrashForm,
    folderTrashUrlTemplate: "/folders/0/delete/",
    handles: [
      {
        currentFolderValue: overrides?.handleCurrentFolderValue ?? "",
        handle,
        itemId: "42",
        itemKind: overrides?.handleItemKind ?? "note",
      },
    ],
    moveForm,
    moveUrlTemplate: "/notes/0/move/",
    noteTrashForm,
    noteTrashUrlTemplate: "/notes/0/delete/",
    windowLike,
  });

  return {
    closeTrigger,
    controller,
    documentLike,
    folderSelect,
    folderTarget,
    folderTrashForm,
    handle,
    moveForm,
    noteTrashForm,
    trashTarget,
    unfiledTarget,
    windowLike,
  };
}

describe("TreeDragAndDropController", () => {
  it("submits the existing move form, retargeted at the dragged note's URL, on a cross-group drop", () => {
    const { folderSelect, folderTarget, handle, moveForm } = buildController();

    handle.trigger("dragstart");
    folderTarget.trigger("dragover");
    folderTarget.trigger("drop");

    expect(folderSelect.value).toBe("7");
    expect(moveForm.action).toBe("/notes/42/move/");
    expect(moveForm.submitCalls).toBe(1);
  });

  it("targets the Unfiled value when dropped there from a different folder", () => {
    const { folderSelect, handle, moveForm, unfiledTarget } = buildController({
      handleCurrentFolderValue: "7",
    });

    handle.trigger("dragstart");
    unfiledTarget.trigger("dragover");
    unfiledTarget.trigger("drop");

    expect(folderSelect.value).toBe("");
    expect(moveForm.submitCalls).toBe(1);
  });

  it("treats a same-group drop as a silent no-op with no submission", () => {
    const { handle, moveForm, unfiledTarget } = buildController();

    // The handle's currentFolderValue defaults to "" (Unfiled), matching unfiledTarget.
    handle.trigger("dragstart");
    unfiledTarget.trigger("dragover");
    unfiledTarget.trigger("drop");

    expect(moveForm.submitCalls).toBe(0);
  });

  it("submits the note-trash form, retargeted at the dragged note's URL, when a note is dropped on Trash", () => {
    const { handle, noteTrashForm, trashTarget } = buildController();

    handle.trigger("dragstart");
    trashTarget.trigger("dragover");
    trashTarget.trigger("drop");

    expect(noteTrashForm.action).toBe("/notes/42/delete/");
    expect(noteTrashForm.submitCalls).toBe(1);
  });

  it("submits the folder-trash form, retargeted at the dragged folder's URL, when a folder is dropped on Trash", () => {
    const { folderTrashForm, handle, trashTarget } = buildController({
      handleItemKind: "folder",
    });

    handle.trigger("dragstart");
    trashTarget.trigger("dragover");
    trashTarget.trigger("drop");

    expect(folderTrashForm.action).toBe("/folders/42/delete/");
    expect(folderTrashForm.submitCalls).toBe(1);
  });

  it("never submits the note-move machinery for a folder-onto-Trash drop", () => {
    const { folderSelect, folderTrashForm, handle, moveForm, trashTarget } =
      buildController({ handleItemKind: "folder" });

    handle.trigger("dragstart");
    trashTarget.trigger("dragover");
    trashTarget.trigger("drop");

    expect(moveForm.submitCalls).toBe(0);
    expect(folderSelect.value).toBe("");
    expect(folderTrashForm.submitCalls).toBe(1);
  });

  it("treats a folder dropped on an ordinary folder row as an unsupported no-op", () => {
    const { folderTarget, handle, moveForm, folderTrashForm } = buildController(
      {
        handleItemKind: "folder",
      },
    );

    handle.trigger("dragstart");
    folderTarget.trigger("dragover");
    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(false);
    folderTarget.trigger("drop");

    expect(moveForm.submitCalls).toBe(0);
    expect(folderTrashForm.submitCalls).toBe(0);
  });

  it("uses a distinct active class for the Trash target vs. an ordinary folder row", () => {
    const { folderTarget, handle, trashTarget } = buildController();

    handle.trigger("dragstart");
    folderTarget.trigger("dragover");
    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(true);
    expect(folderTarget.classList.has(TREE_DRAG_TRASH_ACTIVE_CLASS)).toBe(
      false,
    );

    trashTarget.trigger("dragover");
    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(false);
    expect(trashTarget.classList.has(TREE_DRAG_TRASH_ACTIVE_CLASS)).toBe(true);
  });

  it("only styles one active drop target at a time", () => {
    const extraDropTarget = new MockDropTarget();
    const { folderTarget, handle } = buildController({
      extraDropTarget,
      extraFolderValue: "9",
    });

    handle.trigger("dragstart");
    folderTarget.trigger("dragover");
    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(true);

    extraDropTarget.trigger("dragover");
    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(false);
    expect(extraDropTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(true);
  });

  it("clears active-target state on dragleave when the pointer truly leaves", () => {
    const { folderTarget, handle } = buildController();

    handle.trigger("dragstart");
    folderTarget.trigger("dragover");
    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(true);

    folderTarget.trigger("dragleave", { relatedTarget: null });
    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(false);
  });

  it("does not clear active-target state on dragleave into a contained child", () => {
    const { folderTarget, handle } = buildController();
    const childNode = {};
    folderTarget.containedNodes.push(childNode);

    handle.trigger("dragstart");
    folderTarget.trigger("dragover");
    folderTarget.trigger("dragleave", { relatedTarget: childNode });

    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(true);
  });

  it("clears drag state on dragend, including the dragging handle class", () => {
    const { folderTarget, handle } = buildController();

    handle.trigger("dragstart");
    expect(handle.classList.has(TREE_DRAG_HANDLE_DRAGGING_CLASS)).toBe(true);
    folderTarget.trigger("dragover");

    handle.trigger("dragend");

    expect(handle.classList.has(TREE_DRAG_HANDLE_DRAGGING_CLASS)).toBe(false);
    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(false);
  });

  it("clears the Trash target's active class after a successful drop", () => {
    const { handle, trashTarget } = buildController();

    handle.trigger("dragstart");
    trashTarget.trigger("dragover");
    expect(trashTarget.classList.has(TREE_DRAG_TRASH_ACTIVE_CLASS)).toBe(true);

    trashTarget.trigger("drop");

    expect(trashTarget.classList.has(TREE_DRAG_TRASH_ACTIVE_CLASS)).toBe(false);
  });

  it("clears drag state on a window resize (responsive layout transition)", () => {
    const { folderTarget, handle, windowLike } = buildController();

    handle.trigger("dragstart");
    folderTarget.trigger("dragover");
    windowLike.triggerResize();

    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(false);
  });

  it("clears drag state when an external close trigger (drawer close/backdrop) fires", () => {
    const { closeTrigger, folderTarget, handle } = buildController();

    handle.trigger("dragstart");
    folderTarget.trigger("dragover");
    closeTrigger.triggerClick();

    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(false);
  });

  it("clears drag state on Escape via the wired documentLike", () => {
    const { documentLike, folderTarget, handle } = buildController();

    handle.trigger("dragstart");
    folderTarget.trigger("dragover");
    documentLike.triggerKeydown("Escape");

    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(false);
  });

  it("ignores dragover/drop before any drag has started", () => {
    const { folderSelect, folderTarget, moveForm } = buildController();

    folderTarget.trigger("dragover");
    folderTarget.trigger("drop");

    expect(folderTarget.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(false);
    expect(folderSelect.value).toBe("");
    expect(moveForm.submitCalls).toBe(0);
  });
});

function makeGroupElement(attrs: Record<string, string | undefined>) {
  return {
    getAttribute: (name: string) => attrs[name] ?? null,
    hasAttribute: (name: string) => attrs[name] !== undefined,
  };
}

class FakeElement {
  dataset: Record<string, string> = {};
  classList = new MockClassList();
  private groupEl: ReturnType<typeof makeGroupElement> | null = null;
  private itemEl: ReturnType<typeof makeGroupElement> | null = null;
  private drawerEl: FakeElement | null = null;
  private shellEl: FakeElement | null = null;
  private attrs: Record<string, string> = {};
  private selectorMap = new Map<string, unknown>();
  private selectorAllMap = new Map<string, unknown[]>();
  private listeners = new Map<string, Listener[]>();

  setGroup(groupEl: ReturnType<typeof makeGroupElement> | null): this {
    this.groupEl = groupEl;
    return this;
  }

  setItem(itemEl: ReturnType<typeof makeGroupElement> | null): this {
    this.itemEl = itemEl;
    return this;
  }

  setDrawerAncestor(drawerEl: FakeElement | null): this {
    this.drawerEl = drawerEl;
    return this;
  }

  setShellAncestor(shellEl: FakeElement | null): this {
    this.shellEl = shellEl;
    return this;
  }

  setAttr(name: string, value: string): this {
    this.attrs[name] = value;
    return this;
  }

  setQuery(selector: string, value: unknown): this {
    this.selectorMap.set(selector, value);
    return this;
  }

  setQueryAll(selector: string, values: unknown[]): this {
    this.selectorAllMap.set(selector, values);
    return this;
  }

  getAttribute(name: string): string | null {
    return this.attrs[name] ?? null;
  }

  querySelector(selector: string): unknown {
    return this.selectorMap.get(selector) ?? null;
  }

  querySelectorAll(selector: string): unknown[] {
    return this.selectorAllMap.get(selector) ?? [];
  }

  closest(selector: string): unknown {
    if (selector.includes("data-drawer")) return this.drawerEl;
    if (selector.includes("data-tree-shell")) return this.shellEl;
    if (selector.includes("data-note-id")) return this.itemEl;
    return this.groupEl;
  }

  addEventListener(type: string, listener: Listener): void {
    const existing = this.listeners.get(type) ?? [];
    existing.push(listener);
    this.listeners.set(type, existing);
  }

  trigger(type: string, event: Partial<TreeDragEventLike> = {}): void {
    const defaultEvent: TreeDragEventLike = { preventDefault() {}, ...event };
    this.listeners.get(type)?.forEach((listener) => listener(defaultEvent));
  }
}

class FakeForm {
  action = "";
  submitCalls = 0;
  private selectorMap = new Map<string, unknown>();

  setQuery(selector: string, value: unknown): this {
    this.selectorMap.set(selector, value);
    return this;
  }

  querySelector(selector: string): unknown {
    return this.selectorMap.get(selector) ?? null;
  }

  requestSubmit(): void {
    this.submitCalls += 1;
  }

  submit(): void {
    this.submitCalls += 1;
  }
}

function makeStandardContainer(overrides?: {
  extraHandles?: FakeElement[];
  extraFolderRows?: FakeElement[];
  extraTrashTargets?: FakeElement[];
  drawer?: FakeElement | null;
  shell?: FakeElement | null;
}) {
  const folderGroup = makeGroupElement({ "data-folder-id": "7" });
  const unfiledGroup = makeGroupElement({ "data-folder-target": "unfiled" });
  const noteItem = makeGroupElement({ "data-note-id": "42" });

  const handleEl = new FakeElement().setGroup(unfiledGroup).setItem(noteItem);
  const folderRow = new FakeElement().setGroup(folderGroup);
  const unfiledRow = new FakeElement().setGroup(unfiledGroup);
  const trashTarget = new FakeElement();

  const select = new MockSelect();
  const moveForm = new FakeForm().setQuery('select[name="folder"]', select);
  const noteTrashForm = new FakeForm();
  const folderTrashForm = new FakeForm();

  const container = new FakeElement();
  container.setAttr("data-move-url-template", "/notes/0/move/");
  container.setAttr("data-note-trash-url-template", "/notes/0/delete/");
  container.setAttr("data-folder-trash-url-template", "/folders/0/delete/");
  container.setQuery(".tree-nav__action--move form", moveForm);
  container.setQuery(".tree-nav__action--trash-note form", noteTrashForm);
  container.setQuery(".tree-nav__action--trash-folder form", folderTrashForm);
  container.setQueryAll(".tree-nav__drag-handle", [
    handleEl,
    ...(overrides?.extraHandles ?? []),
  ]);
  container.setQueryAll(".tree-nav__folder-row", [
    folderRow,
    unfiledRow,
    ...(overrides?.extraFolderRows ?? []),
  ]);
  container.setQueryAll("[data-tree-trash-target]", [
    trashTarget,
    ...(overrides?.extraTrashTargets ?? []),
  ]);
  if (overrides?.drawer !== undefined) {
    container.setDrawerAncestor(overrides.drawer);
  }
  if (overrides?.shell !== undefined) {
    container.setShellAncestor(overrides.shell);
  }

  return {
    container,
    folderRow,
    handleEl,
    moveForm,
    folderTrashForm,
    noteTrashForm,
    select,
    trashTarget,
    unfiledRow,
  };
}

describe("initTreeDragAndDropDocument", () => {
  it("does not initialize a tree-nav container missing any required element", () => {
    const emptyContainer = new FakeElement();
    emptyContainer.setQuery(".tree-nav__action--move form", null);
    emptyContainer.setQueryAll(".tree-nav__drag-handle", []);

    const doc = {
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [emptyContainer] : [],
      querySelector: () => null,
    } as unknown as Document;

    expect(initTreeDragAndDropDocument(doc)).toBe(false);
    expect(emptyContainer.dataset.dragInitialized).toBeUndefined();
  });

  it("does not initialize a container with drag handles but no trash form/URL template", () => {
    const folderGroup = makeGroupElement({ "data-folder-id": "7" });
    const noteItem = makeGroupElement({ "data-note-id": "42" });
    const handleEl = new FakeElement().setGroup(folderGroup).setItem(noteItem);
    const select = new MockSelect();
    const moveForm = new FakeForm().setQuery('select[name="folder"]', select);

    const container = new FakeElement();
    container.setAttr("data-move-url-template", "/notes/0/move/");
    container.setQuery(".tree-nav__action--move form", moveForm);
    container.setQueryAll(".tree-nav__drag-handle", [handleEl]);
    container.setQueryAll(".tree-nav__folder-row", []);
    // Deliberately no trash form/template attributes set.

    const doc = {
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [container] : [],
      querySelector: () => null,
    } as unknown as Document;

    expect(initTreeDragAndDropDocument(doc)).toBe(false);
  });

  it("wires note-detail context: drag handles bind to the correct note ID and folder group, and a real drop submits the retargeted move form exactly once", () => {
    const { container, folderRow, handleEl, moveForm, select } =
      makeStandardContainer();

    const doc = {
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [container] : [],
      querySelector: () => null,
    } as unknown as Document;

    expect(initTreeDragAndDropDocument(doc)).toBe(true);
    expect(container.dataset.dragInitialized).toBe("true");

    // Drive the wired controller: dragging note 42 (currently Unfiled) onto folder 7.
    handleEl.trigger("dragstart");
    folderRow.trigger("dragover");
    folderRow.trigger("drop");

    expect(select.value).toBe("7");
    expect(moveForm.action).toBe("/notes/42/move/");
    expect(moveForm.submitCalls).toBe(1);

    // Re-running init is a no-op (already initialized).
    expect(initTreeDragAndDropDocument(doc)).toBe(false);
  });

  it("wires the note-trash and folder-trash forms and drives a real drop on the tree row's own Trash destination", () => {
    const { container, handleEl, noteTrashForm, trashTarget } =
      makeStandardContainer();

    const doc = {
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [container] : [],
      querySelector: () => null,
    } as unknown as Document;

    initTreeDragAndDropDocument(doc);

    handleEl.trigger("dragstart");
    trashTarget.trigger("dragover");
    expect(trashTarget.classList.has(TREE_DRAG_TRASH_ACTIVE_CLASS)).toBe(true);
    trashTarget.trigger("drop");

    expect(noteTrashForm.action).toBe("/notes/42/delete/");
    expect(noteTrashForm.submitCalls).toBe(1);
    expect(trashTarget.classList.has(TREE_DRAG_TRASH_ACTIVE_CLASS)).toBe(false);
  });

  it("wires a folder's own drag handle and drives a real drop of that folder on Trash", () => {
    const folderGroupForHandle = makeGroupElement({ "data-folder-id": "9" });
    const folderHandle = new FakeElement().setGroup(folderGroupForHandle);
    // No `.setItem(...)` -- a folder handle has no `[data-note-id]` ancestor.

    const { container, folderTrashForm, trashTarget } = makeStandardContainer({
      extraHandles: [folderHandle],
    });

    const doc = {
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [container] : [],
      querySelector: () => null,
    } as unknown as Document;

    initTreeDragAndDropDocument(doc);

    folderHandle.trigger("dragstart");
    trashTarget.trigger("dragover");
    trashTarget.trigger("drop");

    expect(folderTrashForm.action).toBe("/folders/9/delete/");
    expect(folderTrashForm.submitCalls).toBe(1);
  });

  it("does not look up a collapsed-rail Trash icon at all -- the tree row's own fixed Trash destination is the sole drop target in both the wide tree and the narrow drawer", () => {
    const shell = new FakeElement();
    const railTrash = new FakeElement();
    shell.setQuery("[data-tree-rail-trash]", railTrash);

    const { container, handleEl, noteTrashForm, trashTarget } =
      makeStandardContainer({ shell });

    const doc = {
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [container] : [],
      querySelector: () => null,
    } as unknown as Document;

    initTreeDragAndDropDocument(doc);

    // Even though a `[data-tree-rail-trash]` element still exists on the
    // shared shell ancestor in this test's fixture, the controller never
    // looks it up anymore -- dropping on it does nothing.
    handleEl.trigger("dragstart");
    railTrash.trigger("dragover");
    railTrash.trigger("drop");
    expect(noteTrashForm.submitCalls).toBe(0);

    // The tree row's own fixed Trash destination still works.
    trashTarget.trigger("dragover");
    trashTarget.trigger("drop");
    expect(noteTrashForm.submitCalls).toBe(1);
  });

  it("wires Home drawer context: drag handles route through the hidden move form to notes:note_move_home", () => {
    const { container, folderRow, handleEl, moveForm, select } =
      makeStandardContainer();
    container.setAttr("data-move-url-template", "/notes/0/move/home/");

    const doc = {
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [container] : [],
      querySelector: () => null,
    } as unknown as Document;

    expect(initTreeDragAndDropDocument(doc)).toBe(true);

    handleEl.trigger("dragstart");
    folderRow.trigger("dragover");
    folderRow.trigger("drop");

    expect(select.value).toBe("7");
    expect(moveForm.action).toBe("/notes/42/move/home/");
    expect(moveForm.submitCalls).toBe(1);
  });

  it("performs a silent no-op with no submission when dropped back onto the note's current group", () => {
    const { container, handleEl, moveForm, unfiledRow } =
      makeStandardContainer();

    const doc = {
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [container] : [],
      querySelector: () => null,
    } as unknown as Document;

    initTreeDragAndDropDocument(doc);

    handleEl.trigger("dragstart");
    unfiledRow.trigger("dragover");
    unfiledRow.trigger("drop");

    expect(moveForm.submitCalls).toBe(0);
  });

  it("wires a drawer-scoped container's close/backdrop triggers to reset drag state", () => {
    const drawerClose = new FakeElement();
    const drawer = new FakeElement().setQuery(
      "[data-drawer-close]",
      drawerClose,
    );
    const backdrop = new FakeElement();

    const { container, folderRow, handleEl, moveForm } = makeStandardContainer({
      drawer,
    });

    const doc = {
      addEventListener: () => {},
      querySelectorAll: (selector: string) =>
        selector === ".tree-nav" ? [container] : [],
      querySelector: (selector: string) =>
        selector === "[data-drawer-backdrop]" ? backdrop : null,
    } as unknown as Document;

    initTreeDragAndDropDocument(doc);

    handleEl.trigger("dragstart");
    folderRow.trigger("dragover");
    expect(folderRow.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(true);

    drawerClose.trigger("click");

    expect(folderRow.classList.has(TREE_DRAG_ACTIVE_CLASS)).toBe(false);
    expect(moveForm.submitCalls).toBe(0);
  });
});
