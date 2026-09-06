import { describe, expect, it, vi } from "vitest";

import { initDeleteConfirmDialog } from "./delete-confirm";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type Handler = (event?: { target: unknown }) => void;

function makeMenu(open = true) {
  return { open };
}

function makeTrigger(opts: {
  dataset?: Record<string, string>;
  menu?: ReturnType<typeof makeMenu> | null;
}) {
  const { dataset = {}, menu = null } = opts;
  const clickHandlers: Handler[] = [];
  return {
    dataset,
    addEventListener: vi.fn((type: string, h: Handler) => {
      if (type === "click") clickHandlers.push(h);
    }),
    closest: vi.fn((sel: string) => (sel === ".row-action-menu" ? menu : null)),
    focus: vi.fn(),
    fireClick() {
      clickHandlers.forEach((h) => h());
    },
  };
}

function makeField() {
  return { value: "" };
}

function makeTextEl() {
  return { textContent: "" };
}

function makeCancelBtn() {
  const clickHandlers: Handler[] = [];
  return {
    addEventListener: vi.fn((type: string, h: Handler) => {
      if (type === "click") clickHandlers.push(h);
    }),
    fireClick() {
      clickHandlers.forEach((h) => h());
    },
  };
}

function makeForm() {
  return { action: "" };
}

function makeDialog() {
  const closeHandlers: Handler[] = [];
  const clickHandlers: Handler[] = [];
  return {
    showModal: vi.fn(),
    close: vi.fn(() => {
      closeHandlers.forEach((h) => h());
    }),
    addEventListener: vi.fn((type: string, h: Handler) => {
      if (type === "close") closeHandlers.push(h);
      if (type === "click") clickHandlers.push(h);
    }),
    fireClose() {
      closeHandlers.forEach((h) => h());
    },
    fireClick(target: unknown) {
      clickHandlers.forEach((h) => h({ target }));
    },
  };
}

type MockTrigger = ReturnType<typeof makeTrigger>;
type MockField = ReturnType<typeof makeField>;
type MockTextEl = ReturnType<typeof makeTextEl>;
type MockCancelBtn = ReturnType<typeof makeCancelBtn>;
type MockForm = ReturnType<typeof makeForm>;
type MockDialog = ReturnType<typeof makeDialog>;

function makeDialogQuerySelector(parts: {
  form: MockForm;
  title: MockTextEl;
  consequence: MockTextEl;
  cancelBtn: MockCancelBtn;
  fields: Record<string, MockField>;
  submitBtn?: MockTextEl;
}) {
  return vi.fn((sel: string) => {
    if (sel === "[data-delete-confirm-form]") return parts.form;
    if (sel === "[data-delete-confirm-title]") return parts.title;
    if (sel === "[data-delete-confirm-consequence]") return parts.consequence;
    if (sel === "[data-delete-confirm-cancel]") return parts.cancelBtn;
    if (sel === "[data-delete-confirm-submit]") return parts.submitBtn ?? null;
    const match = /\[data-delete-confirm-field="(\w+)"\]/.exec(sel);
    if (match) return parts.fields[match[1]] ?? null;
    return null;
  });
}

function makeDoc(opts: {
  dialog: (MockDialog & { querySelector: ReturnType<typeof vi.fn> }) | null;
  triggers: MockTrigger[];
}) {
  const { dialog, triggers } = opts;
  return {
    addEventListener: vi.fn(),
    querySelector: vi.fn((sel: string) =>
      sel === "#delete-confirm-dialog" ? dialog : null,
    ),
    querySelectorAll: vi.fn((sel: string) =>
      sel === "[data-delete-trigger]" ? triggers : [],
    ),
  };
}

function setup(
  opts: {
    triggerDataset?: Record<string, string>;
    menu?: ReturnType<typeof makeMenu> | null;
  } = {},
) {
  const trigger = makeTrigger({
    dataset: opts.triggerDataset,
    menu: opts.menu,
  });
  const form = makeForm();
  const title = makeTextEl();
  const consequence = makeTextEl();
  const cancelBtn = makeCancelBtn();
  const submitBtn = makeTextEl();
  const fields = {
    origin: makeField(),
    current_note: makeField(),
    page: makeField(),
    sort: makeField(),
  };
  const dialogBase = makeDialog();
  const dialog = Object.assign(dialogBase, {
    querySelector: makeDialogQuerySelector({
      form,
      title,
      consequence,
      cancelBtn,
      fields,
      submitBtn,
    }),
  });
  const doc = makeDoc({ dialog, triggers: [trigger] });
  const result = initDeleteConfirmDialog(doc as unknown as Document);
  return {
    trigger,
    form,
    title,
    consequence,
    cancelBtn,
    submitBtn,
    fields,
    dialog,
    doc,
    result,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("initDeleteConfirmDialog – missing prerequisites", () => {
  it("returns false when the dialog is absent", () => {
    const doc = makeDoc({ dialog: null, triggers: [makeTrigger({})] });
    expect(initDeleteConfirmDialog(doc as unknown as Document)).toBe(false);
  });

  it("returns false when there are no triggers", () => {
    const dialogBase = makeDialog();
    const dialog = Object.assign(dialogBase, {
      querySelector: makeDialogQuerySelector({
        form: makeForm(),
        title: makeTextEl(),
        consequence: makeTextEl(),
        cancelBtn: makeCancelBtn(),
        fields: {},
      }),
    });
    const doc = makeDoc({ dialog, triggers: [] });
    expect(initDeleteConfirmDialog(doc as unknown as Document)).toBe(false);
  });

  it("does not throw when the dialog is missing its own required parts", () => {
    const dialogBase = makeDialog();
    const dialog = Object.assign(dialogBase, {
      querySelector: vi.fn(() => null),
    });
    const doc = makeDoc({ dialog, triggers: [makeTrigger({})] });
    expect(() =>
      initDeleteConfirmDialog(doc as unknown as Document),
    ).not.toThrow();
    expect(initDeleteConfirmDialog(doc as unknown as Document)).toBe(false);
  });
});

describe("initDeleteConfirmDialog – note trigger", () => {
  it("initializes successfully", () => {
    const { result } = setup({
      triggerDataset: {
        deleteKind: "note",
        deleteName: "Grocery list",
        deleteAction: "/notes/5/delete/",
        deleteOrigin: "note_detail",
        deleteCurrentNote: "5",
      },
    });
    expect(result).toBe(true);
  });

  it("opens the dialog on trigger click", () => {
    const { trigger, dialog } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "Grocery list" },
    });
    trigger.fireClick();
    expect(dialog.showModal).toHaveBeenCalledOnce();
  });

  it("sets the note title text", () => {
    const { trigger, title } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "Grocery list" },
    });
    trigger.fireClick();
    expect(title.textContent).toBe("Move note to Trash?");
  });

  it("sets the note consequence text with the exact item name, matching the replaced confirm page's copy", () => {
    const { trigger, consequence } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "Grocery list" },
    });
    trigger.fireClick();
    expect(consequence.textContent).toBe(
      '"Grocery list" will move to Trash. You can restore it from Trash later.',
    );
  });

  it("copies the action URL and hidden field values from the trigger's data attributes", () => {
    const { trigger, form, fields } = setup({
      triggerDataset: {
        deleteKind: "note",
        deleteName: "Grocery list",
        deleteAction: "/notes/5/delete/",
        deleteOrigin: "note_detail",
        deleteCurrentNote: "5",
        deletePage: "2",
        deleteSort: "title",
      },
    });
    trigger.fireClick();
    expect(form.action).toBe("/notes/5/delete/");
    expect(fields.origin.value).toBe("note_detail");
    expect(fields.current_note.value).toBe("5");
    expect(fields.page.value).toBe("2");
    expect(fields.sort.value).toBe("title");
  });

  it("clears fields the trigger did not set, so a stale value from a previous open never leaks into a new one", () => {
    const { trigger, fields } = setup({
      triggerDataset: {
        deleteKind: "note",
        deleteName: "Grocery list",
        deleteAction: "/notes/5/delete/",
        deleteOrigin: "home",
      },
    });
    trigger.fireClick();
    expect(fields.current_note.value).toBe("");
    expect(fields.page.value).toBe("");
    expect(fields.sort.value).toBe("");
  });
});

describe("initDeleteConfirmDialog – folder trigger", () => {
  it("sets the folder title and consequence text, matching the replaced confirm page's copy", () => {
    const { trigger, title, consequence } = setup({
      triggerDataset: {
        deleteKind: "folder",
        deleteName: "Recipes",
        deleteAction: "/folders/9/delete/",
      },
    });
    trigger.fireClick();
    expect(title.textContent).toBe("Move folder to Trash?");
    expect(consequence.textContent).toBe(
      '"Recipes" and the notes currently inside it will move to Trash. The folder can be restored from Trash separately, and each note can be restored individually from Trash.',
    );
  });
});

describe("initDeleteConfirmDialog – note-permanent trigger", () => {
  it("sets the permanent-delete title, honest consequence text, and correct action URL", () => {
    const { trigger, title, consequence, form } = setup({
      triggerDataset: {
        deleteKind: "note-permanent",
        deleteName: "Grocery list",
        deleteAction: "/notes/5/permanent-delete/",
      },
    });
    trigger.fireClick();
    expect(title.textContent).toBe("Delete note permanently?");
    expect(consequence.textContent).toBe(
      '"Grocery list" will leave your Trash and can no longer be accessed or ' +
        "restored by you. An administrator may still be able to recover it " +
        "until the normal 90-day retention period expires. Administrators " +
        "can only restore deleted notes back to your library; they cannot " +
        "read the contents of your notes. If you deleted a note by mistake, " +
        "contact your RidgeNote administrator as soon as possible so they " +
        "can attempt recovery.",
    );
    expect(form.action).toBe("/notes/5/permanent-delete/");
  });

  it("never implies a fresh retention window or immediate physical destruction", () => {
    const { trigger, consequence } = setup({
      triggerDataset: { deleteKind: "note-permanent", deleteName: "Notes" },
    });
    trigger.fireClick();
    const text = consequence.textContent ?? "";
    expect(text).not.toMatch(/new 90-day|fresh 90-day|restarts?/i);
    expect(text).not.toMatch(
      /immediately deleted|permanently erased|destroyed/i,
    );
  });
});

describe("initDeleteConfirmDialog – kind-aware submit label", () => {
  it("labels the submit button 'Move to Trash' for the note kind", () => {
    const { trigger, submitBtn } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "Grocery list" },
    });
    trigger.fireClick();
    expect(submitBtn.textContent).toBe("Move to Trash");
  });

  it("labels the submit button 'Move to Trash' for the folder kind", () => {
    const { trigger, submitBtn } = setup({
      triggerDataset: { deleteKind: "folder", deleteName: "Recipes" },
    });
    trigger.fireClick();
    expect(submitBtn.textContent).toBe("Move to Trash");
  });

  it("labels the submit button 'Delete permanently' for the note-permanent kind", () => {
    const { trigger, submitBtn } = setup({
      triggerDataset: {
        deleteKind: "note-permanent",
        deleteName: "Grocery list",
      },
    });
    trigger.fireClick();
    expect(submitBtn.textContent).toBe("Delete permanently");
  });

  it("resets the submit label back to 'Move to Trash' when a permanent-delete dialog is reopened for an ordinary note", () => {
    const form = makeForm();
    const title = makeTextEl();
    const consequence = makeTextEl();
    const cancelBtn = makeCancelBtn();
    const submitBtn = makeTextEl();
    const permanentTrigger = makeTrigger({
      dataset: { deleteKind: "note-permanent", deleteName: "Old note" },
    });
    const noteTrigger = makeTrigger({
      dataset: { deleteKind: "note", deleteName: "New note" },
    });
    const dialogBase = makeDialog();
    const dialog = Object.assign(dialogBase, {
      querySelector: makeDialogQuerySelector({
        form,
        title,
        consequence,
        cancelBtn,
        fields: {},
        submitBtn,
      }),
    });
    const doc = makeDoc({ dialog, triggers: [permanentTrigger, noteTrigger] });
    initDeleteConfirmDialog(doc as unknown as Document);

    permanentTrigger.fireClick();
    expect(submitBtn.textContent).toBe("Delete permanently");

    noteTrigger.fireClick();
    expect(submitBtn.textContent).toBe("Move to Trash");
  });
});

describe("initDeleteConfirmDialog – row-menu interaction", () => {
  it("closes an open row-action menu the trigger lives inside", () => {
    const menu = makeMenu(true);
    const { trigger } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "X" },
      menu,
    });
    trigger.fireClick();
    expect(menu.open).toBe(false);
  });

  it("is a no-op when the trigger has no row-action menu ancestor", () => {
    const { trigger } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "X" },
      menu: null,
    });
    expect(() => trigger.fireClick()).not.toThrow();
  });
});

describe("initDeleteConfirmDialog – cancel, backdrop, Escape, and focus restoration", () => {
  it("closes the dialog when Cancel is clicked", () => {
    const { cancelBtn, dialog } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "X" },
    });
    cancelBtn.fireClick();
    expect(dialog.close).toHaveBeenCalledOnce();
  });

  it("closes the dialog on a backdrop click (event.target is the dialog itself)", () => {
    const { dialog } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "X" },
    });
    dialog.fireClick(dialog);
    expect(dialog.close).toHaveBeenCalledOnce();
  });

  it("does not close the dialog on a click inside its own content", () => {
    const { dialog, form } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "X" },
    });
    dialog.fireClick(form);
    expect(dialog.close).not.toHaveBeenCalled();
  });

  it("restores focus to the trigger that opened it after the dialog's close event", () => {
    const { trigger, dialog } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "X" },
    });
    trigger.fireClick();
    dialog.fireClose();
    expect(trigger.focus).toHaveBeenCalledOnce();
  });

  it("registers no document-level keydown handler – Escape is left entirely to the native dialog", () => {
    const { doc } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "X" },
    });
    const calls = doc.addEventListener.mock.calls;
    expect(calls.filter((call) => call[0] === "keydown")).toHaveLength(0);
  });

  it("allows repeated open/close cycles, overwriting content each time", () => {
    const { trigger, dialog } = setup({
      triggerDataset: { deleteKind: "note", deleteName: "X" },
    });
    trigger.fireClick();
    dialog.fireClose();
    trigger.fireClick();
    expect(dialog.showModal).toHaveBeenCalledTimes(2);
  });
});
