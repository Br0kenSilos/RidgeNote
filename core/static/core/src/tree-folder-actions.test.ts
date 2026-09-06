import { describe, expect, it } from "vitest";

import { initFolderActionAutofocusDocument } from "./tree-folder-actions";

/** Flushes a real `setTimeout(fn, 0)` scheduled during the autofocus-on-open path. */
function flushDeferredFocus(): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, 0));
}

class FakeInput {
  focusCalls = 0;
  selectCalls = 0;

  focus(): void {
    this.focusCalls += 1;
  }

  select(): void {
    this.selectCalls += 1;
  }
}

class FakeSelect {
  focusCalls = 0;

  focus(): void {
    this.focusCalls += 1;
  }
}

class FakeSummary {
  focusCalls = 0;

  focus(): void {
    this.focusCalls += 1;
  }
}

class FakeMenu {
  constructor(private readonly summary: FakeSummary | null) {}

  querySelector(selector: string): unknown {
    if (selector === ":scope > summary") return this.summary;
    return null;
  }
}

const ACCORDION_SELECTOR =
  ".tree-nav__action--new-note, .tree-nav__action--new-folder, .tree-nav__action--rename-folder, .tree-nav__action--move-note";

/** Stand-in for the enclosing `.tree-nav__row-menu-panel`, used only by the accordion tests. */
class FakePanel {
  constructor(private readonly disclosures: FakeDisclosure[]) {}

  querySelectorAll(): FakeDisclosure[] {
    return this.disclosures.filter(
      (d) =>
        d.matches(".tree-nav__action--new-note") ||
        d.matches(".tree-nav__action--new-folder") ||
        d.matches(".tree-nav__action--rename-folder") ||
        d.matches(".tree-nav__action--move-note"),
    );
  }
}

type PanelKind = "new-note" | "new-folder" | "rename" | "move-note";

/** Fake `DOMTokenList`: only `.contains("row-action-menu")` is ever read. */
class FakeClassList {
  constructor(private readonly isRowActionMenu: boolean) {}

  contains(name: string): boolean {
    return name === "row-action-menu" && this.isRowActionMenu;
  }
}

class FakeDisclosure {
  dataset: Record<string, string> = {};
  private _open: boolean;
  private toggleListeners: Array<() => void> = [];
  private readonly input: FakeInput | null;
  private readonly select: FakeSelect | null;
  private readonly menu: FakeMenu | null;
  private readonly kind: PanelKind;
  public panel: FakePanel | null = null;
  readonly classList: FakeClassList;

  constructor(
    options: {
      input?: FakeInput | null;
      select?: FakeSelect | null;
      menu?: FakeMenu | null;
      kind?: PanelKind;
      initialOpen?: boolean;
      /**
       * Mirrors production: only the standalone tree-toolbar New Folder
       * popover carries the `row-action-menu` class itself -- a New
       * Folder/New note disclosure nested inside another menu's panel
       * (the active-note overflow) never does.
       */
      topLevelRowActionMenu?: boolean;
    } = {},
  ) {
    this.input = options.input ?? null;
    this.select = options.select ?? null;
    this.menu = options.menu ?? null;
    this.kind = options.kind ?? "new-folder";
    this._open = options.initialOpen ?? false;
    this.classList = new FakeClassList(options.topLevelRowActionMenu ?? false);
  }

  get open(): boolean {
    return this._open;
  }

  set open(value: boolean) {
    if (value === this._open) return;
    this._open = value;
    this.toggleListeners.forEach((listener) => listener());
  }

  addEventListener(_type: "toggle", listener: () => void): void {
    this.toggleListeners.push(listener);
  }

  querySelector(selector: string): unknown {
    if (selector === 'input[type="text"]') return this.input;
    if (selector === "select") return this.select;
    return null;
  }

  matches(selector: string): boolean {
    if (this.kind === "new-note") {
      return selector === ".tree-nav__action--new-note";
    }
    if (this.kind === "new-folder") {
      return selector === ".tree-nav__action--new-folder";
    }
    if (this.kind === "rename") {
      return selector === ".tree-nav__action--rename-folder";
    }
    if (this.kind === "move-note") {
      return selector === ".tree-nav__action--move-note";
    }
    return false;
  }

  closest(selector: string): unknown {
    if (selector === ".tree-nav__row-menu-panel") return this.panel;
    return this.menu;
  }

  triggerOpen(): void {
    this.open = true;
  }

  triggerClose(): void {
    this.open = false;
  }
}

function buildDoc(disclosures: FakeDisclosure[]) {
  return {
    querySelectorAll: (selector: string) =>
      selector === ACCORDION_SELECTOR ? disclosures : [],
  } as unknown as Document;
}

describe("initFolderActionAutofocusDocument", () => {
  it("focuses and selects the input when the New Folder disclosure opens", async () => {
    const input = new FakeInput();
    const disclosure = new FakeDisclosure({ input });
    const doc = buildDoc([disclosure]);

    expect(initFolderActionAutofocusDocument(doc)).toBe(true);
    disclosure.triggerOpen();
    await flushDeferredFocus();

    expect(input.focusCalls).toBe(1);
    expect(input.selectCalls).toBe(1);
  });

  it("does not autofocus on an ordinary page load (no toggle fired yet)", () => {
    const input = new FakeInput();
    const disclosure = new FakeDisclosure({ input });
    const doc = buildDoc([disclosure]);

    initFolderActionAutofocusDocument(doc);

    expect(input.focusCalls).toBe(0);
    expect(input.selectCalls).toBe(0);
  });

  it("does not throw for a disclosure with no input", async () => {
    const disclosure = new FakeDisclosure({ input: null });
    const doc = buildDoc([disclosure]);

    initFolderActionAutofocusDocument(doc);

    expect(() => disclosure.triggerOpen()).not.toThrow();
    await flushDeferredFocus();
  });

  it("returns focus to the folder menu trigger when the Rename panel closes", () => {
    const summary = new FakeSummary();
    const menu = new FakeMenu(summary);
    const input = new FakeInput();
    const disclosure = new FakeDisclosure({ input, menu, kind: "rename" });
    const doc = buildDoc([disclosure]);

    initFolderActionAutofocusDocument(doc);
    disclosure.triggerOpen();
    disclosure.triggerClose();

    expect(summary.focusCalls).toBe(1);
  });

  it("does not move focus via this mechanism when the standalone tree-toolbar New Folder popover closes (handled generically by tree-context-menu.ts instead, since it's itself a .row-action-menu)", () => {
    const summary = new FakeSummary();
    const menu = new FakeMenu(summary);
    const input = new FakeInput();
    const disclosure = new FakeDisclosure({
      input,
      menu,
      kind: "new-folder",
      topLevelRowActionMenu: true,
    });
    const doc = buildDoc([disclosure]);

    initFolderActionAutofocusDocument(doc);
    disclosure.triggerOpen();
    disclosure.triggerClose();

    expect(summary.focusCalls).toBe(0);
  });

  it("does not double-bind listeners on a second init call", async () => {
    const input = new FakeInput();
    const disclosure = new FakeDisclosure({ input });
    const doc = buildDoc([disclosure]);

    initFolderActionAutofocusDocument(doc);
    expect(initFolderActionAutofocusDocument(doc)).toBe(true);

    disclosure.triggerOpen();
    await flushDeferredFocus();

    expect(input.focusCalls).toBe(1);
  });

  it("returns false when there are no matching disclosures", () => {
    const doc = buildDoc([]);
    expect(initFolderActionAutofocusDocument(doc)).toBe(false);
  });

  it("focuses and selects the input immediately when New Folder is already server-rendered open (validation-error reopen)", () => {
    // No 'toggle' event fires for a server-rendered `open` attribute present
    // at initial parse, so this case must be checked directly at init time
    // rather than relying on the toggle listener alone.
    const input = new FakeInput();
    const disclosure = new FakeDisclosure({ input, initialOpen: true });
    const doc = buildDoc([disclosure]);

    initFolderActionAutofocusDocument(doc);

    expect(input.focusCalls).toBe(1);
    expect(input.selectCalls).toBe(1);
  });

  describe("tree note Move disclosure", () => {
    it("focuses the destination select (no text input present) when the Move disclosure opens", async () => {
      const select = new FakeSelect();
      const disclosure = new FakeDisclosure({ select, kind: "move-note" });
      const doc = buildDoc([disclosure]);

      initFolderActionAutofocusDocument(doc);
      disclosure.triggerOpen();
      await flushDeferredFocus();

      expect(select.focusCalls).toBe(1);
    });

    it("returns focus to the row menu trigger when the Move disclosure closes", () => {
      const summary = new FakeSummary();
      const menu = new FakeMenu(summary);
      const select = new FakeSelect();
      const disclosure = new FakeDisclosure({
        select,
        menu,
        kind: "move-note",
      });
      const doc = buildDoc([disclosure]);

      initFolderActionAutofocusDocument(doc);
      disclosure.triggerOpen();
      disclosure.triggerClose();

      expect(summary.focusCalls).toBe(1);
    });

    it("does not throw for a Move disclosure with no select", async () => {
      const disclosure = new FakeDisclosure({ kind: "move-note" });
      const doc = buildDoc([disclosure]);

      initFolderActionAutofocusDocument(doc);

      expect(() => disclosure.triggerOpen()).not.toThrow();
      await flushDeferredFocus();
    });
  });

  describe("tree note Rename disclosure reuses the shared folder-Rename selector", () => {
    it("focuses and selects the input, and returns focus to the trigger on close, identically to folder Rename", async () => {
      const summary = new FakeSummary();
      const menu = new FakeMenu(summary);
      const input = new FakeInput();
      const disclosure = new FakeDisclosure({ input, menu, kind: "rename" });
      const doc = buildDoc([disclosure]);

      initFolderActionAutofocusDocument(doc);
      disclosure.triggerOpen();
      await flushDeferredFocus();
      expect(input.focusCalls).toBe(1);
      expect(input.selectCalls).toBe(1);

      disclosure.triggerClose();
      expect(summary.focusCalls).toBe(1);
    });
  });

  describe("Rename/Move accordion within one row menu", () => {
    it("closes Move when Rename opens", async () => {
      const rename = new FakeDisclosure({
        input: new FakeInput(),
        kind: "rename",
      });
      const move = new FakeDisclosure({
        select: new FakeSelect(),
        kind: "move-note",
      });
      const panel = new FakePanel([rename, move]);
      rename.panel = panel;
      move.panel = panel;
      const doc = buildDoc([rename, move]);

      initFolderActionAutofocusDocument(doc);
      move.triggerOpen();
      expect(move.open).toBe(true);

      rename.triggerOpen();
      await flushDeferredFocus();

      expect(rename.open).toBe(true);
      expect(move.open).toBe(false);
    });

    it("closes Rename when Move opens", async () => {
      const rename = new FakeDisclosure({
        input: new FakeInput(),
        kind: "rename",
      });
      const move = new FakeDisclosure({
        select: new FakeSelect(),
        kind: "move-note",
      });
      const panel = new FakePanel([rename, move]);
      rename.panel = panel;
      move.panel = panel;
      const doc = buildDoc([rename, move]);

      initFolderActionAutofocusDocument(doc);
      rename.triggerOpen();
      expect(rename.open).toBe(true);

      move.triggerOpen();
      await flushDeferredFocus();

      expect(move.open).toBe(true);
      expect(rename.open).toBe(false);
    });

    it("the newly opened disclosure's own field wins final focus, even though closing the sibling focuses the (shared) menu trigger synchronously first", async () => {
      const summary = new FakeSummary();
      const menu = new FakeMenu(summary);
      const renameInput = new FakeInput();
      const rename = new FakeDisclosure({
        input: renameInput,
        menu,
        kind: "rename",
      });
      const move = new FakeDisclosure({
        select: new FakeSelect(),
        menu,
        kind: "move-note",
      });
      const panel = new FakePanel([rename, move]);
      rename.panel = panel;
      move.panel = panel;
      const doc = buildDoc([rename, move]);

      initFolderActionAutofocusDocument(doc);
      move.triggerOpen();
      rename.triggerOpen();
      await flushDeferredFocus();

      expect(renameInput.focusCalls).toBe(1);
    });

    it("activating the currently open disclosure still collapses it normally (no accordion interference)", () => {
      const rename = new FakeDisclosure({
        input: new FakeInput(),
        kind: "rename",
      });
      const panel = new FakePanel([rename]);
      rename.panel = panel;
      const doc = buildDoc([rename]);

      initFolderActionAutofocusDocument(doc);
      rename.triggerOpen();
      expect(rename.open).toBe(true);

      rename.triggerClose();
      expect(rename.open).toBe(false);
    });

    it("does not affect a folder row's own Rename, which has no Move sibling in its panel", async () => {
      const input = new FakeInput();
      const rename = new FakeDisclosure({ input, kind: "rename" });
      const panel = new FakePanel([rename]);
      rename.panel = panel;
      const doc = buildDoc([rename]);

      initFolderActionAutofocusDocument(doc);
      expect(() => rename.triggerOpen()).not.toThrow();
      await flushDeferredFocus();

      expect(rename.open).toBe(true);
      expect(input.focusCalls).toBe(1);
    });
  });

  describe("New note/New folder/Move accordion within the active-note overflow", () => {
    it("closes New folder and Move when New note opens", () => {
      const newNote = new FakeDisclosure({ kind: "new-note" });
      const newFolder = new FakeDisclosure({
        input: new FakeInput(),
        kind: "new-folder",
      });
      const move = new FakeDisclosure({
        select: new FakeSelect(),
        kind: "move-note",
      });
      const panel = new FakePanel([newNote, newFolder, move]);
      newNote.panel = panel;
      newFolder.panel = panel;
      move.panel = panel;
      const doc = buildDoc([newNote, newFolder, move]);

      initFolderActionAutofocusDocument(doc);
      newFolder.triggerOpen();
      move.triggerOpen();
      expect(newFolder.open).toBe(false); // Move's own open already closed it.

      newFolder.triggerOpen();
      expect(newFolder.open).toBe(true);
      expect(move.open).toBe(false);

      newNote.triggerOpen();

      expect(newNote.open).toBe(true);
      expect(newFolder.open).toBe(false);
      expect(move.open).toBe(false);
    });

    it("closes New note and New folder when Move opens", async () => {
      const newNote = new FakeDisclosure({ kind: "new-note" });
      const newFolder = new FakeDisclosure({
        input: new FakeInput(),
        kind: "new-folder",
      });
      const move = new FakeDisclosure({
        select: new FakeSelect(),
        kind: "move-note",
      });
      const panel = new FakePanel([newNote, newFolder, move]);
      newNote.panel = panel;
      newFolder.panel = panel;
      move.panel = panel;
      const doc = buildDoc([newNote, newFolder, move]);

      initFolderActionAutofocusDocument(doc);
      newNote.triggerOpen();
      expect(newNote.open).toBe(true);

      move.triggerOpen();
      await flushDeferredFocus();

      expect(move.open).toBe(true);
      expect(newNote.open).toBe(false);
    });

    it("does not throw opening a nested New note disclosure that has no field at all", () => {
      const newNote = new FakeDisclosure({ kind: "new-note" });
      const panel = new FakePanel([newNote]);
      newNote.panel = panel;
      const doc = buildDoc([newNote]);

      initFolderActionAutofocusDocument(doc);

      expect(() => newNote.triggerOpen()).not.toThrow();
      expect(newNote.open).toBe(true);
    });

    it("returns focus to the overflow trigger when the nested New note disclosure closes", () => {
      const summary = new FakeSummary();
      const menu = new FakeMenu(summary);
      const newNote = new FakeDisclosure({ kind: "new-note", menu });
      const panel = new FakePanel([newNote]);
      newNote.panel = panel;
      const doc = buildDoc([newNote]);

      initFolderActionAutofocusDocument(doc);
      newNote.triggerOpen();
      newNote.triggerClose();

      expect(summary.focusCalls).toBe(1);
    });

    it("focuses the folder-name input and returns focus to the overflow trigger for a nested (non-standalone) New folder disclosure", async () => {
      const summary = new FakeSummary();
      const menu = new FakeMenu(summary);
      const input = new FakeInput();
      const newFolder = new FakeDisclosure({
        input,
        menu,
        kind: "new-folder",
        // topLevelRowActionMenu deliberately omitted/false: this
        // represents the overflow-nested copy, not the standalone
        // tree-toolbar popover.
      });
      const panel = new FakePanel([newFolder]);
      newFolder.panel = panel;
      const doc = buildDoc([newFolder]);

      initFolderActionAutofocusDocument(doc);
      newFolder.triggerOpen();
      await flushDeferredFocus();
      expect(input.focusCalls).toBe(1);
      expect(input.selectCalls).toBe(1);

      newFolder.triggerClose();
      expect(summary.focusCalls).toBe(1);
    });
  });
});
