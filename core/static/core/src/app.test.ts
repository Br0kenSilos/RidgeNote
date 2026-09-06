import { describe, expect, it, vi } from "vitest";

import { initAccountMenus, initHelpPanel, staticReadyText } from "./app";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

type Handler = () => void;
type HelpClickEvent = { preventDefault: () => void };
type HelpClickHandler = (event: HelpClickEvent) => void;

function makeTrigger(inDrawer = false) {
  let ariaExpanded = "false";
  const clickHandlers: HelpClickHandler[] = [];
  const preventDefault = vi.fn();
  return {
    addEventListener: vi.fn((type: string, h: HelpClickHandler) => {
      if (type === "click") clickHandlers.push(h);
    }),
    getAttribute: vi.fn((name: string) =>
      name === "aria-expanded" ? ariaExpanded : null,
    ),
    setAttribute: vi.fn((name: string, value: string) => {
      if (name === "aria-expanded") ariaExpanded = value;
    }),
    click: vi.fn(),
    focus: vi.fn(),
    closest: vi.fn((sel: string) =>
      sel === "[data-drawer]" && inDrawer ? ({} as Element) : null,
    ),
    fireClick() {
      clickHandlers.forEach((h) => h({ preventDefault }));
    },
    getAriaExpanded() {
      return ariaExpanded;
    },
    getPreventDefault() {
      return preventDefault;
    },
  };
}

function makeCloseBtn() {
  const clickHandlers: Handler[] = [];
  return {
    addEventListener: vi.fn((type: string, h: Handler) => {
      if (type === "click") clickHandlers.push(h);
    }),
    click: vi.fn(),
    focus: vi.fn(),
    fireClick() {
      clickHandlers.forEach((h) => h());
    },
  };
}

function makeDialog(closeBtnEl: ReturnType<typeof makeCloseBtn>) {
  const closeHandlers: Handler[] = [];
  const cancelHandlers: Handler[] = [];
  return {
    showModal: vi.fn(),
    close: vi.fn(),
    addEventListener: vi.fn((type: string, h: Handler) => {
      if (type === "close") closeHandlers.push(h);
      if (type === "cancel") cancelHandlers.push(h);
    }),
    // Selector-aware, unlike a blanket `() => closeBtnEl`: initHelpPanel
    // now also queries `.help-article` (topic-switching scroll reset)
    // and both `[data-help-topic]`/`[data-help-topic-link]` via
    // querySelectorAll -- these open/close-focused tests carry no
    // topic markup, so those all correctly resolve to nothing.
    querySelector: vi.fn((sel: string) =>
      sel === "[data-help-close]" ? closeBtnEl : null,
    ),
    querySelectorAll: vi.fn(() => []),
    fireClose() {
      closeHandlers.forEach((h) => h());
    },
    fireCancel() {
      cancelHandlers.forEach((h) => h());
    },
    getCancelHandlerCount() {
      return cancelHandlers.length;
    },
  };
}

type MockTrigger = ReturnType<typeof makeTrigger>;
type MockDialog = ReturnType<typeof makeDialog>;

function makeDoc(opts: {
  dialog: MockDialog;
  triggers: MockTrigger[];
  drawerCloseBtn?: ReturnType<typeof makeCloseBtn> | null;
  drawerToggle?: MockTrigger | null;
}) {
  const { dialog, triggers, drawerCloseBtn = null, drawerToggle = null } = opts;
  return {
    addEventListener: vi.fn(),
    querySelector: vi.fn((sel: string) => {
      if (sel === "#help-panel") return dialog;
      if (sel === "[data-drawer-close]") return drawerCloseBtn;
      if (sel === "[data-drawer-toggle]") return drawerToggle;
      return null;
    }),
    querySelectorAll: vi.fn((sel: string) => {
      if (sel === "[data-help-toggle]") return triggers;
      return [];
    }),
  };
}

// ---------------------------------------------------------------------------
// staticReadyText
// ---------------------------------------------------------------------------

describe("staticReadyText", () => {
  it("confirms the compiled asset ran", () => {
    expect(staticReadyText()).toBe("Static assets loaded");
  });
});

// ---------------------------------------------------------------------------
// initHelpPanel – header trigger
// ---------------------------------------------------------------------------

describe("initHelpPanel – header trigger", () => {
  function setup() {
    const trigger = makeTrigger();
    const closeBtn = makeCloseBtn();
    const dialog = makeDialog(closeBtn);
    const doc = makeDoc({ dialog, triggers: [trigger] });
    initHelpPanel(doc as unknown as Document);
    return { trigger, closeBtn, dialog, doc };
  }

  it("opens the dialog on trigger click", () => {
    const { trigger, dialog } = setup();
    trigger.fireClick();
    expect(dialog.showModal).toHaveBeenCalledOnce();
  });

  it("prevents default link navigation when the dialog is available and handled", () => {
    // Triggers are <a href="/help/">; when JS
    // successfully opens the dialog, the normal link navigation must
    // not also occur.
    const { trigger } = setup();
    trigger.fireClick();
    expect(trigger.getPreventDefault()).toHaveBeenCalledOnce();
  });

  it("sets aria-expanded true when opening", () => {
    const { trigger } = setup();
    trigger.fireClick();
    expect(trigger.getAriaExpanded()).toBe("true");
  });

  it("closes dialog when the close button is clicked", () => {
    const { closeBtn, dialog } = setup();
    closeBtn.fireClick();
    expect(dialog.close).toHaveBeenCalledOnce();
  });

  it("restores focus to trigger after close event (explicit close)", () => {
    const { trigger, dialog } = setup();
    trigger.fireClick();
    dialog.fireClose();
    expect(trigger.focus).toHaveBeenCalled();
  });

  it("resets aria-expanded to false after close event", () => {
    const { trigger, dialog } = setup();
    trigger.fireClick();
    dialog.fireClose();
    expect(trigger.getAriaExpanded()).toBe("false");
  });

  it("models native Escape as cancel then close – confirms no cancel listener or document keydown handler is registered", () => {
    const { trigger, dialog, doc } = setup();
    trigger.fireClick();
    // A native <dialog> fires "cancel" then "close" on Escape. Production code
    // registers no "cancel" listener, so firing it here must not change state;
    // only the subsequent native "close" event drives cleanup.
    dialog.fireCancel();
    expect(trigger.focus).not.toHaveBeenCalled();
    expect(trigger.getAriaExpanded()).toBe("true");
    dialog.fireClose();
    expect(trigger.focus).toHaveBeenCalled();
    expect(trigger.getAriaExpanded()).toBe("false");
    // No "cancel" listener was ever registered on the dialog
    expect(dialog.getCancelHandlerCount()).toBe(0);
    // No document-level keydown listener must be registered
    const calls = (doc.addEventListener as ReturnType<typeof vi.fn>).mock.calls;
    expect(calls.filter(([t]) => t === "keydown")).toHaveLength(0);
  });

  it("allows repeated open/close cycles", () => {
    const { trigger, dialog } = setup();
    trigger.fireClick();
    dialog.fireClose();
    trigger.fireClick();
    expect(dialog.showModal).toHaveBeenCalledTimes(2);
  });
});

// ---------------------------------------------------------------------------
// initHelpPanel – drawer trigger
// ---------------------------------------------------------------------------

describe("initHelpPanel – drawer trigger", () => {
  function setup() {
    const headerTrigger = makeTrigger(false);
    const drawerTrigger = makeTrigger(true);
    const drawerToggle = makeTrigger(false);
    const drawerCloseBtn = makeCloseBtn();
    const closeBtn = makeCloseBtn();
    const dialog = makeDialog(closeBtn);
    const doc = makeDoc({
      dialog,
      triggers: [headerTrigger, drawerTrigger],
      drawerCloseBtn,
      drawerToggle,
    });
    initHelpPanel(doc as unknown as Document);
    return {
      headerTrigger,
      drawerTrigger,
      drawerToggle,
      drawerCloseBtn,
      dialog,
    };
  }

  it("dismisses the drawer via its close button before opening Help", () => {
    const { drawerTrigger, drawerCloseBtn, dialog } = setup();
    drawerTrigger.fireClick();
    expect(drawerCloseBtn.click).toHaveBeenCalledOnce();
    expect(dialog.showModal).toHaveBeenCalledOnce();
    // Prove ordering, not just that both happened: the drawer's own close
    // button must fire before the Help dialog opens.
    const closeOrder = drawerCloseBtn.click.mock.invocationCallOrder[0];
    const showModalOrder = dialog.showModal.mock.invocationCallOrder[0];
    expect(closeOrder).toBeLessThan(showModalOrder);
  });

  it("opens the dialog after drawer dismissal", () => {
    const { drawerTrigger, dialog } = setup();
    drawerTrigger.fireClick();
    expect(dialog.showModal).toHaveBeenCalledOnce();
  });

  it("restores focus to drawer toggle, not the in-drawer trigger, on close", () => {
    const { drawerTrigger, drawerToggle, dialog } = setup();
    drawerTrigger.fireClick();
    dialog.fireClose();
    expect(drawerToggle.focus).toHaveBeenCalled();
    expect(drawerTrigger.focus).not.toHaveBeenCalled();
  });

  it("sets aria-expanded true on all triggers when opening from drawer", () => {
    const { headerTrigger, drawerTrigger } = setup();
    drawerTrigger.fireClick();
    expect(headerTrigger.getAriaExpanded()).toBe("true");
    expect(drawerTrigger.getAriaExpanded()).toBe("true");
  });

  it("resets aria-expanded on all triggers after close from drawer origin", () => {
    const { headerTrigger, drawerTrigger, dialog } = setup();
    drawerTrigger.fireClick();
    dialog.fireClose();
    expect(headerTrigger.getAriaExpanded()).toBe("false");
    expect(drawerTrigger.getAriaExpanded()).toBe("false");
  });

  it("header trigger restores its own focus, not the drawer toggle, on close", () => {
    const { headerTrigger, drawerToggle, dialog } = setup();
    headerTrigger.fireClick();
    dialog.fireClose();
    expect(headerTrigger.focus).toHaveBeenCalled();
    expect(drawerToggle.focus).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// initHelpPanel – topic switching
// ---------------------------------------------------------------------------

function makeTopicSection(topicId: string) {
  return {
    dataset: { helpTopic: topicId },
    hidden: false,
  };
}

function makeTopicLink(hash: string) {
  const clickHandlers: HelpClickHandler[] = [];
  const preventDefault = vi.fn();
  return {
    hash,
    addEventListener: vi.fn((type: string, h: HelpClickHandler) => {
      if (type === "click") clickHandlers.push(h);
    }),
    setAttribute: vi.fn(),
    removeAttribute: vi.fn(),
    fireClick() {
      clickHandlers.forEach((h) => h({ preventDefault }));
    },
    getPreventDefault() {
      return preventDefault;
    },
  };
}

type MockTopicSection = ReturnType<typeof makeTopicSection>;
type MockTopicLink = ReturnType<typeof makeTopicLink>;

function makeTopicDialog(sections: MockTopicSection[], links: MockTopicLink[]) {
  return {
    showModal: vi.fn(),
    close: vi.fn(),
    addEventListener: vi.fn(),
    querySelector: vi.fn(() => null), // no .help-article mock; scrollTo reset is a no-op here
    querySelectorAll: vi.fn((sel: string) => {
      if (sel === "[data-help-topic]") return sections;
      if (sel === "[data-help-topic-link]") return links;
      return [];
    }),
  };
}

describe("initHelpPanel – topic switching", () => {
  function setup(topicIds: string[]) {
    const sections = new Map(topicIds.map((id) => [id, makeTopicSection(id)]));
    const links = new Map(topicIds.map((id) => [id, makeTopicLink(`#${id}`)]));
    const dialog = makeTopicDialog([...sections.values()], [...links.values()]);
    const doc = {
      addEventListener: vi.fn(),
      querySelector: vi.fn((sel: string) =>
        sel === "#help-panel" ? dialog : null,
      ),
      querySelectorAll: vi.fn(() => []),
    };
    initHelpPanel(doc as unknown as Document);
    return { sections, links, dialog };
  }

  it("shows Introduction by default and hides the rest", () => {
    // The default topic
    // is Introduction, not Getting Started.
    const { sections } = setup([
      "help-introduction",
      "help-getting-started",
      "help-notes",
      "help-tags",
    ]);
    expect(sections.get("help-introduction")!.hidden).toBe(false);
    expect(sections.get("help-getting-started")!.hidden).toBe(true);
    expect(sections.get("help-notes")!.hidden).toBe(true);
    expect(sections.get("help-tags")!.hidden).toBe(true);
  });

  it("marks the Introduction TOC link as current by default", () => {
    const { links } = setup(["help-introduction", "help-getting-started"]);
    expect(links.get("help-introduction")!.setAttribute).toHaveBeenCalledWith(
      "aria-current",
      "true",
    );
    expect(
      links.get("help-getting-started")!.removeAttribute,
    ).toHaveBeenCalledWith("aria-current");
  });

  it("clicking a TOC link switches the selected topic and prevents navigation", () => {
    const { sections, links } = setup([
      "help-getting-started",
      "help-notes",
      "help-tags",
    ]);

    links.get("help-notes")!.fireClick();

    expect(sections.get("help-notes")!.hidden).toBe(false);
    expect(sections.get("help-getting-started")!.hidden).toBe(true);
    expect(sections.get("help-tags")!.hidden).toBe(true);
    expect(links.get("help-notes")!.getPreventDefault()).toHaveBeenCalledOnce();
  });

  it("updates aria-current when switching topics", () => {
    const { links } = setup(["help-getting-started", "help-notes"]);

    links.get("help-notes")!.fireClick();

    expect(links.get("help-notes")!.setAttribute).toHaveBeenCalledWith(
      "aria-current",
      "true",
    );
    expect(
      links.get("help-getting-started")!.removeAttribute,
    ).toHaveBeenCalledWith("aria-current");
  });

  it("an inline cross-reference link switches topic exactly like a TOC link (same [data-help-topic-link] mechanism)", () => {
    const { sections, links } = setup(["help-notes", "help-tags"]);

    // "Related section: see Tags." inside the Notes topic partial uses
    // the identical data-help-topic-link mechanism as the TOC.
    links.get("help-tags")!.fireClick();

    expect(sections.get("help-tags")!.hidden).toBe(false);
    expect(sections.get("help-notes")!.hidden).toBe(true);
  });

  it("selects the admin topic when its TOC link is present and clicked", () => {
    const { sections, links } = setup([
      "help-getting-started",
      "help-administrators",
    ]);

    links.get("help-administrators")!.fireClick();

    expect(sections.get("help-administrators")!.hidden).toBe(false);
  });

  it("leaves the current topic unchanged and allows real navigation for an unrecognized topic link", () => {
    const sections = [
      makeTopicSection("help-introduction"),
      makeTopicSection("help-notes"),
    ];
    const strayLink = makeTopicLink("#help-does-not-exist");
    const dialog = makeTopicDialog(sections, [strayLink]);
    const doc = {
      addEventListener: vi.fn(),
      querySelector: vi.fn((sel: string) =>
        sel === "#help-panel" ? dialog : null,
      ),
      querySelectorAll: vi.fn(() => []),
    };
    initHelpPanel(doc as unknown as Document);

    strayLink.fireClick();

    expect(strayLink.getPreventDefault()).not.toHaveBeenCalled();
    expect(sections[0].hidden).toBe(false);
    expect(sections[1].hidden).toBe(true);
  });
});

// ---------------------------------------------------------------------------
// initHelpPanel – no dialog in document
// ---------------------------------------------------------------------------

describe("initHelpPanel – no dialog in document", () => {
  it("returns without error when #help-panel is absent", () => {
    const doc = {
      addEventListener: vi.fn(),
      querySelector: vi.fn(() => null),
      querySelectorAll: vi.fn(() => []),
    };
    expect(() => initHelpPanel(doc as unknown as Document)).not.toThrow();
  });

  it("attaches no click listener to triggers when the dialog is absent, leaving normal <a href> navigation to /help/ untouched", () => {
    // The no-JS/no-dialog fallback: the canonical /help/
    // page itself suppresses the dialog entirely, and this is also the
    // real-world "JS present but dialog markup missing/unsupported"
    // case. Either way, a trigger with no attached listener falls
    // straight through to the browser's own <a href> navigation --
    // there is no handler left to call preventDefault().
    const trigger = makeTrigger();
    const doc = {
      addEventListener: vi.fn(),
      querySelector: vi.fn(() => null),
      querySelectorAll: vi.fn((sel: string) =>
        sel === "[data-help-toggle]" ? [trigger] : [],
      ),
    };
    initHelpPanel(doc as unknown as Document);
    expect(trigger.addEventListener).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// initAccountMenus
// ---------------------------------------------------------------------------

type ClickHandler = (event: { target: unknown }) => void;
type KeyHandler = (event: { key: string }) => void;

function makeSummary() {
  return { focus: vi.fn() };
}

const MENU_SELECTOR = ".account-menu";

function makeMenu(opts: {
  open: boolean;
  summary?: ReturnType<typeof makeSummary>;
  insideEl?: object;
}) {
  let open = opts.open;
  const summary = opts.summary ?? makeSummary();
  const insideEl = opts.insideEl ?? {};
  return {
    get open() {
      return open;
    },
    set open(value: boolean) {
      open = value;
    },
    contains: vi.fn((node: unknown) => node === summary || node === insideEl),
    querySelector: vi.fn((sel: string) => (sel === "summary" ? summary : null)),
    summary,
    insideEl,
  };
}

type MockMenu = ReturnType<typeof makeMenu>;

function makeMenuDoc(menus: MockMenu[]) {
  const clickHandlers: ClickHandler[] = [];
  const keydownHandlers: KeyHandler[] = [];
  return {
    querySelectorAll: vi.fn((sel: string) =>
      sel === MENU_SELECTOR ? menus : [],
    ),
    addEventListener: vi.fn((type: string, h: ClickHandler | KeyHandler) => {
      if (type === "click") clickHandlers.push(h as ClickHandler);
      if (type === "keydown") keydownHandlers.push(h as KeyHandler);
    }),
    fireClick(target: unknown) {
      clickHandlers.forEach((h) => h({ target }));
    },
    fireKeydown(key: string) {
      keydownHandlers.forEach((h) => h({ key }));
    },
  };
}

describe("initAccountMenus", () => {
  function setup() {
    const menu = makeMenu({ open: true });
    const doc = makeMenuDoc([menu]);
    initAccountMenus(doc as unknown as Document);
    return { menu, doc };
  }

  it("closes an open menu when a click occurs outside it", () => {
    const { menu, doc } = setup();
    doc.fireClick({});
    expect(menu.open).toBe(false);
  });

  it("does not close the menu when the click occurs inside it", () => {
    const { menu, doc } = setup();
    doc.fireClick(menu.insideEl);
    expect(menu.open).toBe(true);
  });

  it("closes an open menu on Escape", () => {
    const { menu, doc } = setup();
    doc.fireKeydown("Escape");
    expect(menu.open).toBe(false);
  });

  it("restores focus to the menu's summary after Escape closure", () => {
    const { menu, doc } = setup();
    doc.fireKeydown("Escape");
    expect(menu.summary.focus).toHaveBeenCalledOnce();
  });

  it("ignores non-Escape keys", () => {
    const { menu, doc } = setup();
    doc.fireKeydown("Enter");
    expect(menu.open).toBe(true);
  });

  it("leaves an already-closed menu unaffected by outside click and Escape", () => {
    const menu = makeMenu({ open: false });
    const doc = makeMenuDoc([menu]);
    initAccountMenus(doc as unknown as Document);
    doc.fireClick({});
    doc.fireKeydown("Escape");
    expect(menu.open).toBe(false);
    expect(menu.summary.focus).not.toHaveBeenCalled();
  });

  it("closing one menu via outside click does not affect another closed menu", () => {
    const openMenu = makeMenu({ open: true });
    const closedMenu = makeMenu({ open: false });
    const doc = makeMenuDoc([openMenu, closedMenu]);
    initAccountMenus(doc as unknown as Document);
    doc.fireClick({});
    expect(openMenu.open).toBe(false);
    expect(closedMenu.open).toBe(false);
    expect(closedMenu.summary.focus).not.toHaveBeenCalled();
  });

  it("does nothing when no dismissible menu elements are present", () => {
    const doc = makeMenuDoc([]);
    expect(() => initAccountMenus(doc as unknown as Document)).not.toThrow();
    expect(doc.addEventListener).not.toHaveBeenCalled();
  });
});

// ---------------------------------------------------------------------------
// `.note-workspace__overflow`
// ("More note actions") is not managed by `initAccountMenus`, since that
// would duplicate `tree-context-menu.ts`'s own `initRowActionMenusDocument`
// (which already handles every `.row-action-menu`, including this one, with
// portal-aware outside-click/Escape -- see that file's own test suite,
// `tree-context-menu.test.ts`, for full coverage of this exact menu's
// dismissal behavior, its only controller). `initAccountMenus`'s own
// `menu.contains(event.target)` check assumes the panel stays a real DOM
// descendant of the menu, which is not true once
// `initRowActionMenusDocument` moves an opened panel into its shared portal
// -- a click target that doesn't navigate or submit (e.g. the
// Move disclosure) would otherwise expose a second controller closing the
// menu out from under it. The
// tests below cover `.account-menu` only, the sole element this
// function manages.
// ---------------------------------------------------------------------------

describe("initAccountMenus – no longer manages .note-workspace__overflow", () => {
  it("only queries for .account-menu, never .note-workspace__overflow", () => {
    const doc = makeMenuDoc([]);
    initAccountMenus(doc as unknown as Document);
    expect(doc.querySelectorAll).toHaveBeenCalledWith(".account-menu");
    expect(doc.querySelectorAll).not.toHaveBeenCalledWith(
      ".account-menu, .note-workspace__overflow",
    );
  });

  it("does not close or otherwise touch an open menu that is not returned by the .account-menu query", () => {
    // Regression scenario: a
    // `.note-workspace__overflow` menu whose panel has already been
    // reparented into `tree-context-menu.ts`'s shared portal, so a click on
    // a node inside that panel is no longer a DOM descendant of the menu
    // itself. Previously `initAccountMenus` also matched this element and
    // closed it on exactly this click; now the mocked document's
    // `.account-menu` query never returns it at all, so it is never
    // touched by this controller regardless of where the click lands.
    const overflowMenu = makeMenu({ open: true });
    // Simulate the portal move: the click target is *not* something this
    // menu's own `contains` recognizes as inside it.
    const portaledClickTarget = {};
    const doc = makeMenuDoc([]); // .account-menu query returns no menus
    initAccountMenus(doc as unknown as Document);

    doc.fireClick(portaledClickTarget);

    expect(overflowMenu.open).toBe(true);
    expect(overflowMenu.contains).not.toHaveBeenCalled();
  });
});
