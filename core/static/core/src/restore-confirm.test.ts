import { describe, expect, it, vi } from "vitest";

import { initRestoreConfirmDialog } from "./restore-confirm";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type Handler = (event?: { target: unknown }) => void;

function makeTrigger(dataset: Record<string, string> = {}) {
  const clickHandlers: Handler[] = [];
  return {
    dataset,
    addEventListener: vi.fn((type: string, h: Handler) => {
      if (type === "click") clickHandlers.push(h);
    }),
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
  owner: MockField | null;
}) {
  return vi.fn((sel: string) => {
    if (sel === "[data-restore-confirm-form]") return parts.form;
    if (sel === "[data-restore-confirm-title]") return parts.title;
    if (sel === "[data-restore-confirm-consequence]") return parts.consequence;
    if (sel === "[data-restore-confirm-cancel]") return parts.cancelBtn;
    if (sel === '[data-restore-confirm-field="owner"]') return parts.owner;
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
      sel === "#restore-confirm-dialog" ? dialog : null,
    ),
    querySelectorAll: vi.fn((sel: string) =>
      sel === "[data-restore-trigger]" ? triggers : [],
    ),
  };
}

function setup(
  opts: {
    triggerDataset?: Record<string, string>;
    owner?: MockField | null;
  } = {},
) {
  const trigger = makeTrigger(opts.triggerDataset);
  const form = makeForm();
  const title = makeTextEl();
  const consequence = makeTextEl();
  const cancelBtn = makeCancelBtn();
  const owner = opts.owner === undefined ? makeField() : opts.owner;
  const dialogBase = makeDialog();
  const dialog = Object.assign(dialogBase, {
    querySelector: makeDialogQuerySelector({
      form,
      title,
      consequence,
      cancelBtn,
      owner,
    }),
  });
  const doc = makeDoc({ dialog, triggers: [trigger] });
  const result = initRestoreConfirmDialog(doc as unknown as Document);
  return {
    trigger,
    form,
    title,
    consequence,
    cancelBtn,
    owner,
    dialog,
    doc,
    result,
  };
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("initRestoreConfirmDialog – missing prerequisites", () => {
  it("returns false when the dialog is absent", () => {
    const doc = makeDoc({ dialog: null, triggers: [makeTrigger()] });
    expect(initRestoreConfirmDialog(doc as unknown as Document)).toBe(false);
  });

  it("returns false when there are no triggers", () => {
    const dialogBase = makeDialog();
    const dialog = Object.assign(dialogBase, {
      querySelector: makeDialogQuerySelector({
        form: makeForm(),
        title: makeTextEl(),
        consequence: makeTextEl(),
        cancelBtn: makeCancelBtn(),
        owner: makeField(),
      }),
    });
    const doc = makeDoc({ dialog, triggers: [] });
    expect(initRestoreConfirmDialog(doc as unknown as Document)).toBe(false);
  });

  it("does not throw when the dialog is missing its own required parts", () => {
    const dialogBase = makeDialog();
    const dialog = Object.assign(dialogBase, {
      querySelector: vi.fn(() => null),
    });
    const doc = makeDoc({ dialog, triggers: [makeTrigger()] });
    expect(() =>
      initRestoreConfirmDialog(doc as unknown as Document),
    ).not.toThrow();
    expect(initRestoreConfirmDialog(doc as unknown as Document)).toBe(false);
  });
});

describe("initRestoreConfirmDialog – folder trigger", () => {
  it("initializes successfully", () => {
    const { result } = setup({
      triggerDataset: {
        restoreKind: "folder",
        restoreName: "Recipes",
        restoreCount: "0",
        restoreAction: "/folders/9/restore/",
      },
    });
    expect(result).toBe(true);
  });

  it("opens the dialog on trigger click", () => {
    const { trigger, dialog } = setup({
      triggerDataset: { restoreName: "Recipes", restoreCount: "0" },
    });
    trigger.fireClick();
    expect(dialog.showModal).toHaveBeenCalledOnce();
  });

  it("sets the title text", () => {
    const { trigger, title } = setup({
      triggerDataset: { restoreName: "Recipes", restoreCount: "0" },
    });
    trigger.fireClick();
    expect(title.textContent).toBe("Restore folder?");
  });

  it("shows the zero-note legacy wording when the count is 0", () => {
    const { trigger, consequence } = setup({
      triggerDataset: { restoreName: "Recipes", restoreCount: "0" },
    });
    trigger.fireClick();
    expect(consequence.textContent).toBe(
      '"Recipes" will be restored. No associated notes will be restored.',
    );
  });

  it("uses singular wording for exactly one associated note", () => {
    const { trigger, consequence } = setup({
      triggerDataset: { restoreName: "Recipes", restoreCount: "1" },
    });
    trigger.fireClick();
    expect(consequence.textContent).toBe(
      '"Recipes" will be restored. 1 associated note will also be restored.',
    );
  });

  it("uses plural wording for multiple associated notes", () => {
    const { trigger, consequence } = setup({
      triggerDataset: { restoreName: "Recipes", restoreCount: "3" },
    });
    trigger.fireClick();
    expect(consequence.textContent).toBe(
      '"Recipes" will be restored. 3 associated notes will also be restored.',
    );
  });

  it("treats a missing/malformed count as zero rather than throwing or showing NaN", () => {
    const { trigger, consequence } = setup({
      triggerDataset: { restoreName: "Recipes" },
    });
    expect(() => trigger.fireClick()).not.toThrow();
    expect(consequence.textContent).toBe(
      '"Recipes" will be restored. No associated notes will be restored.',
    );
  });

  it("copies the action URL and owner field from the trigger's data attributes", () => {
    const { trigger, form, owner } = setup({
      triggerDataset: {
        restoreName: "Recipes",
        restoreCount: "2",
        restoreAction: "/admin/recovery/folders/9/restore/",
        restoreOwner: "42",
      },
    });
    trigger.fireClick();
    expect(form.action).toBe("/admin/recovery/folders/9/restore/");
    expect(owner?.value).toBe("42");
  });

  it("clears the owner field when the trigger did not set one, so a stale value never leaks into a new open", () => {
    const { trigger, owner } = setup({
      triggerDataset: { restoreName: "Recipes", restoreCount: "0" },
    });
    trigger.fireClick();
    expect(owner?.value).toBe("");
  });
});

describe("initRestoreConfirmDialog – cancel, backdrop, Escape, and focus restoration", () => {
  it("closes the dialog when Cancel is clicked", () => {
    const { cancelBtn, dialog } = setup({
      triggerDataset: { restoreName: "X", restoreCount: "0" },
    });
    cancelBtn.fireClick();
    expect(dialog.close).toHaveBeenCalledOnce();
  });

  it("closes the dialog on a backdrop click (event.target is the dialog itself)", () => {
    const { dialog } = setup({
      triggerDataset: { restoreName: "X", restoreCount: "0" },
    });
    dialog.fireClick(dialog);
    expect(dialog.close).toHaveBeenCalledOnce();
  });

  it("does not close the dialog on a click inside its own content", () => {
    const { dialog, form } = setup({
      triggerDataset: { restoreName: "X", restoreCount: "0" },
    });
    dialog.fireClick(form);
    expect(dialog.close).not.toHaveBeenCalled();
  });

  it("restores focus to the trigger that opened it after the dialog's close event", () => {
    const { trigger, dialog } = setup({
      triggerDataset: { restoreName: "X", restoreCount: "0" },
    });
    trigger.fireClick();
    dialog.fireClose();
    expect(trigger.focus).toHaveBeenCalledOnce();
  });

  it("registers no document-level keydown handler – Escape is left entirely to the native dialog", () => {
    const { doc } = setup({
      triggerDataset: { restoreName: "X", restoreCount: "0" },
    });
    const calls = doc.addEventListener.mock.calls;
    expect(calls.filter((call) => call[0] === "keydown")).toHaveLength(0);
  });

  it("allows repeated open/close cycles, overwriting content each time", () => {
    const { trigger, dialog } = setup({
      triggerDataset: { restoreName: "X", restoreCount: "0" },
    });
    trigger.fireClick();
    dialog.fireClose();
    trigger.fireClick();
    expect(dialog.showModal).toHaveBeenCalledTimes(2);
  });
});
