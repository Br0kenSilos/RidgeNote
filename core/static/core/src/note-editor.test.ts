import { getSchema, resolveExtensions } from "@tiptap/core";
import { isAllowedUri as extensionIsAllowedUri } from "@tiptap/extension-link";
import { EditorState, TextSelection } from "@tiptap/pm/state";
import { describe, expect, it, vi } from "vitest";

import {
  AUTOSAVE_DEBOUNCE_MS,
  FRESHNESS_CHECK_INTERVAL_MS,
  IDLE_WARNING_SAVE_HEADER,
  MAX_PENDING_SAVE_MS,
  NoteSaveController,
  RETRY_DELAY_MS,
  buildEditorExtensions,
  canInitializeNoteEditor,
  buildPlainTextTabResult,
  copyTextWithFallback,
  createBeforeUnloadHandler,
  focusEditorBody,
  getActiveLinkHref,
  installEditorExitGesture,
  installFormattingToolbarToggle,
  installOneTimeGeneratedTitleSelection,
  normalizeAllowedLinkHref,
  openValidatedLink,
  resolveModifierClickLinkHref,
  runToolbarCommand,
  setFormattingToolbarExpanded,
  shouldApplyNewNoteFocusBehavior,
  shouldAutoFocusNewNoteBody,
  EditorExitGesture,
  LinkOpenGesture,
  type AutosaveResponse,
  type BeforeUnloadEventLike,
  type CopyButtonLike,
  type CopyDocumentLike,
  type EditorChain,
  type EditorExitGestureTarget,
  type EditorExitKeydownEventLike,
  type EditorLike,
  type LinkAnchorLike,
  type LinkClickRootLike,
  type LinkClickTargetLike,
  type FetchResponseLike,
  type FormLike,
  type FreshnessResponse,
  type NavGuardTargetLike,
  type NavigatorLike,
  type NoticeActionsLike,
  type NoticeRegionLike,
  type NoticeTextLike,
  type PrintTabLike,
  type ReloadButtonLike,
  type SaveButtonLike,
  type SchedulerLike,
  type StatusRegionLike,
  type TitleInputLike,
  type ToolbarRegionLike,
  type ToolbarToggleButtonLike,
} from "./note-editor";

class MockChain implements EditorChain {
  calls: string[] = [];

  focus() {
    this.calls.push("focus");
    return this;
  }

  setTextSelection(position: number) {
    this.calls.push(`setTextSelection:${position}`);
    return this;
  }

  toggleBold() {
    this.calls.push("toggleBold");
    return this;
  }

  toggleItalic() {
    this.calls.push("toggleItalic");
    return this;
  }

  toggleUnderline() {
    this.calls.push("toggleUnderline");
    return this;
  }

  toggleHeading(attrs: { level: number }) {
    this.calls.push(`toggleHeading:${attrs.level}`);
    return this;
  }

  toggleBulletList() {
    this.calls.push("toggleBulletList");
    return this;
  }

  toggleOrderedList() {
    this.calls.push("toggleOrderedList");
    return this;
  }

  extendMarkRange(mark: string) {
    this.calls.push(`extendMarkRange:${mark}`);
    return this;
  }

  setLink(attrs: { href: string }) {
    this.calls.push(`setLink:${attrs.href}`);
    return this;
  }

  unsetLink() {
    this.calls.push("unsetLink");
    return this;
  }

  unsetAllMarks() {
    this.calls.push("unsetAllMarks");
    return this;
  }

  clearNodes() {
    this.calls.push("clearNodes");
    return this;
  }

  undo() {
    this.calls.push("undo");
    return this;
  }

  redo() {
    this.calls.push("redo");
    return this;
  }

  run() {
    this.calls.push("run");
    return true;
  }
}

class MockEditor implements EditorLike {
  chainInstance = new MockChain();
  editable = true;
  json: unknown;

  constructor(json: unknown = {}) {
    this.json = json;
  }

  chain() {
    return this.chainInstance;
  }

  destroy() {}

  getJSON() {
    return this.json;
  }

  setEditable(editable: boolean) {
    this.editable = editable;
  }
}

class MockTitleInput implements TitleInputLike {
  value: string;
  readOnly = false;
  selectCalls = 0;
  private focusListener: (() => void) | null = null;
  private inputListener: (() => void) | null = null;

  constructor(value: string) {
    this.value = value;
  }

  addEventListener(type: "focus" | "input", listener: () => void) {
    if (type === "focus") {
      this.focusListener = listener;
      return;
    }
    this.inputListener = listener;
  }

  select() {
    this.selectCalls += 1;
  }

  triggerFocus() {
    this.focusListener?.();
  }

  triggerInput() {
    this.inputListener?.();
  }
}

class MockToolbarButton implements ToolbarToggleButtonLike {
  textContent: string | null = null;
  disabled = false;
  attributes = new Map<string, string>();
  private clickListener: ((event: { preventDefault(): void }) => void) | null =
    null;

  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ) {
    if (type === "click") {
      this.clickListener = listener;
    }
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  triggerClick() {
    this.clickListener?.({ preventDefault() {} });
  }
}

class MockToolbarRegion implements ToolbarRegionLike {
  constructor(public hidden: boolean) {}
}

class MockSaveButton implements SaveButtonLike {
  disabled = false;
  private clickListener: ((event: { preventDefault(): void }) => void) | null =
    null;

  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ) {
    if (type === "click") {
      this.clickListener = listener;
    }
  }

  triggerClick() {
    this.clickListener?.({ preventDefault() {} });
  }
}

class MockForm implements FormLike {
  attributes = new Map<string, string>();
  private submitListener: ((event: { preventDefault(): void }) => void) | null =
    null;

  addEventListener(
    type: "submit",
    listener: (event: { preventDefault(): void }) => void,
  ) {
    if (type === "submit") {
      this.submitListener = listener;
    }
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  triggerSubmit() {
    this.submitListener?.({ preventDefault() {} });
  }
}

class MockActionLink implements NavGuardTargetLike {
  href: string;
  attributes = new Map<string, string>();
  private clickListener: ((event: { preventDefault(): void }) => void) | null =
    null;
  private submitListener: ((event: { preventDefault(): void }) => void) | null =
    null;

  constructor(href: string) {
    this.href = href;
  }

  addEventListener(
    type: "click" | "submit",
    listener: (event: { preventDefault(): void }) => void,
  ) {
    if (type === "click") {
      this.clickListener = listener;
      return;
    }
    this.submitListener = listener;
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  triggerClick() {
    let prevented = false;
    this.clickListener?.({
      preventDefault() {
        prevented = true;
      },
    });
    return prevented;
  }

  triggerSubmit() {
    let prevented = false;
    this.submitListener?.({
      preventDefault() {
        prevented = true;
      },
    });
    return prevented;
  }
}

class MockPrintTab implements PrintTabLike {
  assignedUrl: string | null = null;
  closed = false;
  location = {
    assign: (url: string) => {
      this.assignedUrl = url;
    },
  };

  close() {
    this.closed = true;
  }
}

class MockReloadButton implements ReloadButtonLike {
  hidden = true;
  private clickListener: ((event: { preventDefault(): void }) => void) | null =
    null;

  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ) {
    if (type === "click") {
      this.clickListener = listener;
    }
  }

  triggerClick() {
    this.clickListener?.({ preventDefault() {} });
  }
}

class MockCopyButton implements CopyButtonLike {
  hidden = true;
  disabled = false;
  textContent: string | null = "Copy local draft";
  attributes = new Map<string, string>();
  private clickListener: ((event: { preventDefault(): void }) => void) | null =
    null;

  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ) {
    if (type === "click") {
      this.clickListener = listener;
    }
  }

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }

  triggerClick() {
    this.clickListener?.({ preventDefault() {} });
  }
}

class MockStatusRegion implements StatusRegionLike {
  textContent: string | null = null;
  attributes = new Map<string, string>();

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }
}

class MockNoticeRegion implements NoticeRegionLike {
  hidden = true;
  attributes = new Map<string, string>();

  setAttribute(name: string, value: string) {
    this.attributes.set(name, value);
  }
}

class MockNoticeActions implements NoticeActionsLike {
  hidden = true;
}

class MockNoticeText implements NoticeTextLike {
  textContent: string | null = null;
}

class MockHiddenInput {
  constructor(public value: string) {}
}

class ManualScheduler implements SchedulerLike {
  private nextId = 1;
  private tasks = new Map<number, { callback: () => void; delayMs: number }>();

  clearTimeout(handle: number): void {
    this.tasks.delete(handle);
  }

  setTimeout(callback: () => void, delayMs: number): number {
    const handle = this.nextId;
    this.nextId += 1;
    this.tasks.set(handle, { callback, delayMs });
    return handle;
  }

  hasDelay(delayMs: number): boolean {
    return [...this.tasks.values()].some((task) => task.delayMs === delayMs);
  }

  countDelay(delayMs: number): number {
    return [...this.tasks.values()].filter((task) => task.delayMs === delayMs)
      .length;
  }

  runDelay(delayMs: number): void {
    const handles = [...this.tasks.entries()]
      .filter(([, task]) => task.delayMs === delayMs)
      .map(([handle]) => handle);
    for (const handle of handles) {
      const task = this.tasks.get(handle);
      if (!task) {
        continue;
      }
      this.tasks.delete(handle);
      task.callback();
    }
  }
}

class MockWindowEvents {
  beforeUnloadListener: ((event: BeforeUnloadEventLike) => void) | null = null;
  focusListener: (() => void) | null = null;

  addEventListener(
    type: "beforeunload" | "focus",
    listener: ((event: BeforeUnloadEventLike) => void) | (() => void),
  ) {
    if (type === "beforeunload") {
      this.beforeUnloadListener = listener as (
        event: BeforeUnloadEventLike,
      ) => void;
      return;
    }
    this.focusListener = listener as () => void;
  }

  triggerFocus() {
    this.focusListener?.();
  }
}

class MockDocumentEvents {
  visibilityState = "visible";
  visibilityListener: (() => void) | null = null;

  addEventListener(type: "visibilitychange", listener: () => void) {
    if (type === "visibilitychange") {
      this.visibilityListener = listener;
    }
  }

  triggerVisibility(state: string) {
    this.visibilityState = state;
    this.visibilityListener?.();
  }
}

type DeferredFetch = {
  promise: Promise<FetchResponseLike>;
  reject: (error: unknown) => void;
  resolve: (response: FetchResponseLike) => void;
};

function createDeferredFetch(): DeferredFetch {
  let resolve!: (response: FetchResponseLike) => void;
  let reject!: (error: unknown) => void;
  const promise = new Promise<FetchResponseLike>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, reject, resolve };
}

function createResponse(
  status: number,
  payload: AutosaveResponse | FreshnessResponse,
): FetchResponseLike {
  return {
    status,
    json: async () => payload,
  };
}

async function flushAsyncWork(cycles = 6) {
  for (let index = 0; index < cycles; index += 1) {
    await Promise.resolve();
  }
}

function createSaveControllerContext(
  fetchPlans: Array<DeferredFetch | FetchResponseLike>,
) {
  const editor = new MockEditor({ type: "doc", content: [] });
  const titleInput = new MockTitleInput("Generated title");
  const hiddenBodyInput = new MockHiddenInput(
    JSON.stringify({ type: "doc", content: [] }),
  );
  const versionInput = new MockHiddenInput("1");
  const saveButton = new MockSaveButton();
  const form = new MockForm();
  const printLink = new MockActionLink("/notes/1/print/");
  const overflowPrintLink = new MockActionLink("/notes/1/print/");
  const printLinks = [printLink, overflowPrintLink];
  const exportLink = new MockActionLink("/notes/1/export/");
  const overflowExportLink = new MockActionLink("/notes/1/export/");
  const exportLinks = [exportLink, overflowExportLink];
  const navLink = new MockActionLink("/");
  const reloadButton = new MockReloadButton();
  const copyLocalButton = new MockCopyButton();
  const noticeRegion = new MockNoticeRegion();
  const noticeActions = new MockNoticeActions();
  const noticeText = new MockNoticeText();
  const statusRegion = new MockStatusRegion();
  const scheduler = new ManualScheduler();
  const windowEvents = new MockWindowEvents();
  const documentEvents = new MockDocumentEvents();
  const toolbar = new MockToolbarRegion(true);
  const toolbarToggle = new MockToolbarButton();
  const locationAssign = vi.fn();
  const printTabs: MockPrintTab[] = [];
  const openBlankTab = vi.fn(() => {
    const tab = new MockPrintTab();
    printTabs.push(tab);
    return tab;
  });
  const confirmFn = vi.fn(() => false);
  const copyTextFn = vi.fn((text: string) => Promise.resolve(text.length >= 0));
  const onTitleSaved = vi.fn();
  const fetchCalls: Array<{
    body?: string;
    headers: Record<string, string>;
    input: string;
    method: string;
  }> = [];
  let nextFetchPlan = 0;

  const controller = new NoteSaveController({
    autosaveUrl: "/notes/1/autosave/",
    copyLocalButton,
    copyTextFn,
    csrfToken: "csrf-token",
    detailUrl: "/notes/1/",
    documentEvents,
    editor,
    exportLinks,
    fetchFn: (input, init) => {
      fetchCalls.push({
        input,
        method: init.method,
        body: init.body,
        headers: init.headers,
      });
      const plan = fetchPlans[nextFetchPlan];
      nextFetchPlan += 1;
      if (!plan) {
        throw new Error("Unexpected fetch call");
      }
      if ("promise" in plan) {
        return plan.promise;
      }
      return Promise.resolve(plan);
    },
    form,
    freshnessUrl: "/notes/1/freshness/",
    hiddenBodyInput,
    locationAssign,
    navigationGuardTargets: [navLink],
    noticeActions,
    noticeRegion,
    noticeText,
    onTitleSaved,
    openBlankTab,
    printLinks,
    printUrl: "/notes/1/print/",
    reloadButton,
    replaceEditorDocument: (bodyJson) => {
      editor.json = bodyJson;
    },
    saveButton,
    scheduler,
    statusRegion,
    titleInput,
    toolbar,
    toolbarToggle,
    versionInput,
    windowEvents,
    confirmFn,
  });

  return {
    confirmFn,
    controller,
    copyLocalButton,
    copyTextFn,
    documentEvents,
    editor,
    exportLink,
    exportLinks,
    fetchCalls,
    form,
    hiddenBodyInput,
    locationAssign,
    navLink,
    noticeActions,
    noticeRegion,
    noticeText,
    onTitleSaved,
    openBlankTab,
    printLink,
    printLinks,
    printTabs,
    reloadButton,
    saveButton,
    scheduler,
    statusRegion,
    titleInput,
    toolbar,
    toolbarToggle,
    versionInput,
    windowEvents,
  };
}

describe("buildEditorExtensions", () => {
  it("keeps hardBreak support enabled for Shift+Enter", () => {
    const [starterKit] = buildEditorExtensions();

    const starterKitOptions = starterKit.options as { hardBreak?: unknown };

    expect(starterKitOptions.hardBreak).toEqual({});
  });

  it("disables StarterKit's bundled link and underline so RidgeNote's own registrations are the only ones", () => {
    const [starterKit] = buildEditorExtensions();

    const starterKitOptions = starterKit.options as {
      link?: unknown;
      underline?: unknown;
    };

    expect(starterKitOptions.link).toBe(false);
    expect(starterKitOptions.underline).toBe(false);
  });

  it("resolves to exactly one link extension and one underline extension, with no duplicate-name warning", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});

    const resolved = resolveExtensions(buildEditorExtensions());
    const names = resolved.map((extension) => extension.name);

    expect(names.filter((name) => name === "link")).toHaveLength(1);
    expect(names.filter((name) => name === "underline")).toHaveLength(1);
    expect(warnSpy).not.toHaveBeenCalled();

    warnSpy.mockRestore();
  });

  it("keeps RidgeNote's explicit Link configuration as the sole registration", () => {
    const resolved = resolveExtensions(buildEditorExtensions());
    const link = resolved.find((extension) => extension.name === "link");

    expect(link).toBeDefined();
    const linkOptions = link?.options as {
      autolink?: boolean;
      linkOnPaste?: boolean;
      openOnClick?: boolean;
    };
    expect(linkOptions.autolink).toBe(false);
    expect(linkOptions.linkOnPaste).toBe(false);
    expect(linkOptions.openOnClick).toBe(false);
  });

  it("gates candidate hrefs through isAllowedUri: built-in protocol policy AND syntactic URL validity", () => {
    const resolved = resolveExtensions(buildEditorExtensions());
    const link = resolved.find((extension) => extension.name === "link");
    const linkOptions = link?.options as {
      isAllowedUri?: (
        url: string,
        ctx: {
          defaultValidate: (url: string) => boolean;
          protocols: unknown[];
          defaultProtocol: string;
        },
      ) => boolean;
    };
    expect(linkOptions.isAllowedUri).toBeTypeOf("function");

    const ctx = {
      defaultValidate: (url: string) => !!extensionIsAllowedUri(url, []),
      protocols: [],
      defaultProtocol: "http",
    };
    const isAllowedUri = linkOptions.isAllowedUri!;

    // The malformed candidate a bracket-balancing linkify scan can produce
    // from pasted `[url](url)`-shaped text: valid protocol prefix, but not
    // a syntactically valid absolute URL.
    expect(
      isAllowedUri(
        "http://caldavsynchronizer.org](http://caldavsynchronizer.org)",
        ctx,
      ),
    ).toBe(false);
    // Ordinary valid hrefs remain accepted.
    expect(isAllowedUri("http://caldavsynchronizer.org", ctx)).toBe(true);
    expect(isAllowedUri("https://example.com", ctx)).toBe(true);
    expect(isAllowedUri("mailto:someone@example.com", ctx)).toBe(true);
    // Existing protocol policy is still enforced, not bypassed by the new syntax check.
    expect(isAllowedUri("javascript:alert(1)", ctx)).toBeFalsy();
  });
});

describe("buildPlainTextTabResult", () => {
  const schema = getSchema(buildEditorExtensions());

  function createState(
    docJson: Record<string, unknown>,
    from: number,
    to: number = from,
  ): EditorState {
    const doc = schema.nodeFromJSON(docJson);
    return EditorState.create({
      schema,
      doc,
      selection: TextSelection.create(doc, from, to),
    });
  }

  function applyResult(
    state: EditorState,
    direction: "insert" | "remove",
  ): {
    state: EditorState;
    result: ReturnType<typeof buildPlainTextTabResult>;
  } {
    const result = buildPlainTextTabResult(state, direction);
    if (result.type !== "apply") {
      return { state, result };
    }
    return { state: state.apply(result.transaction), result };
  }

  function docText(state: EditorState): string {
    return state.doc.textBetween(0, state.doc.content.size, "\n\n");
  }

  function selectedText(state: EditorState): string {
    const { from, to } = state.selection;
    return state.doc.textBetween(from, to);
  }

  const singleParagraphDoc = (text: string) => ({
    type: "doc",
    content: [
      {
        type: "paragraph",
        content: text ? [{ type: "text", text }] : [],
      },
    ],
  });

  const twoParagraphDoc = (first: string, second: string) => ({
    type: "doc",
    content: [
      {
        type: "paragraph",
        content: first ? [{ type: "text", text: first }] : [],
      },
      {
        type: "paragraph",
        content: second ? [{ type: "text", text: second }] : [],
      },
    ],
  });

  const listDoc = {
    type: "doc",
    content: [
      {
        type: "bulletList",
        content: [
          {
            type: "listItem",
            content: [
              { type: "paragraph", content: [{ type: "text", text: "one" }] },
            ],
          },
        ],
      },
    ],
  };

  describe("collapsed cursor", () => {
    it("inserts two spaces in a blank paragraph", () => {
      const state = createState(singleParagraphDoc(""), 1);

      const { state: next } = applyResult(state, "insert");

      expect(docText(next)).toBe("  ");
      expect(next.selection.empty).toBe(true);
      expect(next.selection.from).toBe(3);
    });

    it("inserts two spaces at the start of a paragraph", () => {
      const state = createState(singleParagraphDoc("hello"), 1);

      const { state: next } = applyResult(state, "insert");

      expect(docText(next)).toBe("  hello");
    });

    it("inserts two spaces in the middle of a paragraph", () => {
      const state = createState(singleParagraphDoc("hello"), 3);

      const { state: next } = applyResult(state, "insert");

      expect(docText(next)).toBe("he  llo");
    });

    it("inserts two spaces at the end of a paragraph", () => {
      const state = createState(singleParagraphDoc("hello"), 6);

      const { state: next } = applyResult(state, "insert");

      expect(docText(next)).toBe("hello  ");
    });

    it("removes two preceding spaces with Shift+Tab", () => {
      const state = createState(singleParagraphDoc("he  llo"), 5);

      const { state: next } = applyResult(state, "remove");

      expect(docText(next)).toBe("hello");
    });

    it("removes only one preceding space when just one is present", () => {
      const state = createState(singleParagraphDoc("he llo"), 4);

      const { state: next } = applyResult(state, "remove");

      expect(docText(next)).toBe("hello");
    });

    it("is a handled no-op when no preceding spaces exist", () => {
      const state = createState(singleParagraphDoc("hello"), 3);

      const { state: next, result } = applyResult(state, "remove");

      expect(result.type).toBe("noop");
      expect(docText(next)).toBe("hello");
      expect(next).toBe(state);
    });

    it("does not remove spaces across a paragraph boundary", () => {
      const state = createState(singleParagraphDoc("hello"), 1);

      const { result } = applyResult(state, "remove");

      expect(result.type).toBe("noop");
    });
  });

  describe("single-paragraph selection", () => {
    it("inserts two spaces before the selection and keeps it selected", () => {
      // "hello" with "ell" selected (positions 2-5)
      const state = createState(singleParagraphDoc("hello"), 2, 5);

      const { state: next } = applyResult(state, "insert");

      expect(docText(next)).toBe("h  ello");
      expect(next.selection.empty).toBe(false);
      expect(selectedText(next)).toBe("ell");
    });

    it("removes up to two spaces before the selection and keeps it selected", () => {
      // "he  llo" with "llo" selected (positions 5-8)
      const state = createState(singleParagraphDoc("he  llo"), 5, 8);

      const { state: next } = applyResult(state, "remove");

      expect(docText(next)).toBe("hello");
      expect(next.selection.empty).toBe(false);
      expect(selectedText(next)).toBe("llo");
    });

    it("is a handled no-op removing from a selection with no preceding spaces", () => {
      const state = createState(singleParagraphDoc("hello"), 2, 5);

      const { state: next, result } = applyResult(state, "remove");

      expect(result.type).toBe("noop");
      expect(docText(next)).toBe("hello");
      expect(selectedText(next)).toBe("ell");
    });
  });

  describe("multi-paragraph selection", () => {
    it("inserts two spaces at the start of every selected paragraph", () => {
      const state = createState(twoParagraphDoc("alpha", "beta"), 3, 10);

      const { state: next } = applyResult(state, "insert");

      expect(docText(next)).toBe("  alpha\n\n  beta");
    });

    it("does not merge blocks or replace selected text when inserting", () => {
      const state = createState(twoParagraphDoc("alpha", "beta"), 3, 10);

      const { state: next } = applyResult(state, "insert");

      // Still two distinct paragraph nodes (not merged into one block), and
      // neither paragraph's original characters were deleted or replaced.
      expect(next.doc.childCount).toBe(2);
      expect(next.doc.child(0).textContent).toBe("  alpha");
      expect(next.doc.child(1).textContent).toBe("  beta");
    });

    it("removes up to two leading spaces from every selected paragraph", () => {
      const state = createState(twoParagraphDoc("  alpha", "  beta"), 5, 14);

      const { state: next } = applyResult(state, "remove");

      expect(docText(next)).toBe("alpha\n\nbeta");
    });

    it("leaves paragraphs without leading spaces unchanged safely", () => {
      const state = createState(twoParagraphDoc("alpha", "  beta"), 3, 12);

      const { state: next } = applyResult(state, "remove");

      expect(docText(next)).toBe("alpha\n\nbeta");
    });

    it("is a handled no-op when no selected paragraph has leading spaces", () => {
      const state = createState(twoParagraphDoc("alpha", "beta"), 3, 10);

      const { state: next, result } = applyResult(state, "remove");

      expect(result.type).toBe("noop");
      expect(docText(next)).toBe("alpha\n\nbeta");
    });
  });

  describe("list contexts", () => {
    it("returns bail for a collapsed cursor inside a list item", () => {
      const doc = schema.nodeFromJSON(listDoc);
      const state = EditorState.create({
        schema,
        doc,
        selection: TextSelection.create(doc, 3),
      });

      const result = buildPlainTextTabResult(state, "insert");

      expect(result.type).toBe("bail");
    });

    it("returns bail when a selection endpoint is inside a list item", () => {
      const doc = schema.nodeFromJSON({
        type: "doc",
        content: [
          {
            type: "paragraph",
            content: [{ type: "text", text: "before" }],
          },
          listDoc.content[0],
        ],
      });
      const state = EditorState.create({
        schema,
        doc,
        // Selection spans from inside the plain paragraph into the list item.
        selection: TextSelection.create(doc, 3, 12),
      });

      const result = buildPlainTextTabResult(state, "insert");

      expect(result.type).toBe("bail");
    });

    it("leaves list-item content completely untouched", () => {
      const doc = schema.nodeFromJSON(listDoc);
      const state = EditorState.create({
        schema,
        doc,
        selection: TextSelection.create(doc, 3),
      });

      buildPlainTextTabResult(state, "insert");

      expect(docText(state)).toBe("one");
    });
  });

  describe("undo/redo compatibility", () => {
    it("does not opt the transaction out of history tracking", () => {
      const state = createState(singleParagraphDoc("hello"), 1);

      const result = buildPlainTextTabResult(state, "insert");

      expect(result.type).toBe("apply");
      if (result.type === "apply") {
        expect(result.transaction.getMeta("addToHistory")).not.toBe(false);
      }
    });
  });
});

describe("canInitializeNoteEditor", () => {
  it("accepts only the supported schema version", () => {
    expect(canInitializeNoteEditor(1)).toBe(true);
    expect(canInitializeNoteEditor(2)).toBe(false);
  });
});

describe("normalizeAllowedLinkHref", () => {
  it("accepts approved schemes", () => {
    expect(normalizeAllowedLinkHref("https://example.com")).toBe(
      "https://example.com",
    );
    expect(normalizeAllowedLinkHref("http://example.com")).toBe(
      "http://example.com",
    );
    expect(normalizeAllowedLinkHref("mailto:test@example.com")).toBe(
      "mailto:test@example.com",
    );
  });

  it("rejects forbidden or malformed links", () => {
    expect(normalizeAllowedLinkHref("javascript:alert(1)")).toBeNull();
    expect(normalizeAllowedLinkHref("data:text/plain,hi")).toBeNull();
    expect(normalizeAllowedLinkHref("file:///tmp/test")).toBeNull();
    expect(normalizeAllowedLinkHref("//example.com")).toBeNull();
    expect(normalizeAllowedLinkHref("example.com")).toBeNull();
  });
});

describe("new-note focus behavior", () => {
  const baseOptions = {
    isImmediateNewPage: true,
    generatedTitle: "Untitled - 2026-06-28 14-06",
    titleValue: "Untitled - 2026-06-28 14-06",
    hasSelectedTitle: false,
  };

  it("autofocuses the body only on the immediate new-note page", () => {
    expect(shouldAutoFocusNewNoteBody({ isImmediateNewPage: true })).toBe(true);
    expect(shouldAutoFocusNewNoteBody({ isImmediateNewPage: false })).toBe(
      false,
    );
  });

  it("skips body autofocus on back-forward restoration", () => {
    expect(
      shouldAutoFocusNewNoteBody({
        isImmediateNewPage: true,
        navigationType: "back_forward",
      }),
    ).toBe(false);
  });

  it("applies one-time title selection when the generated title is still present", () => {
    expect(shouldApplyNewNoteFocusBehavior(baseOptions)).toBe(true);
  });

  it("skips once the one-time title selection has already fired", () => {
    expect(
      shouldApplyNewNoteFocusBehavior({
        ...baseOptions,
        hasSelectedTitle: true,
      }),
    ).toBe(false);
  });

  it("skips once the user has changed the title", () => {
    expect(
      shouldApplyNewNoteFocusBehavior({
        ...baseOptions,
        titleValue: "Meeting notes",
      }),
    ).toBe(false);
  });

  it("selects the generated title only on the first focus", () => {
    const input = new MockTitleInput(baseOptions.generatedTitle);
    const win = {
      performance: {
        getEntriesByType: () => [{ type: "navigate" }],
      },
    } as unknown as Window;

    installOneTimeGeneratedTitleSelection(
      input,
      {
        isImmediateNewPage: true,
        generatedTitle: baseOptions.generatedTitle,
      },
      win,
    );

    input.triggerFocus();
    input.triggerFocus();

    expect(input.selectCalls).toBe(1);
  });

  it("moves the editor cursor to the beginning for a newly created note", () => {
    const editor = new MockEditor();
    const win = {
      requestAnimationFrame: (callback: FrameRequestCallback) => {
        callback(0);
        return 1;
      },
    } as unknown as Window;

    focusEditorBody(editor, win);

    expect(editor.chainInstance.calls).toEqual([
      "focus",
      "setTextSelection:1",
      "run",
    ]);
  });
});

describe("formatting toolbar visibility", () => {
  it("starts hidden and updates button state", () => {
    const button = new MockToolbarButton();
    const toolbar = new MockToolbarRegion(true);

    setFormattingToolbarExpanded(button, toolbar, false);

    expect(toolbar.hidden).toBe(true);
    expect(button.attributes.get("aria-expanded")).toBe("false");
    expect(button.textContent).toBe("Show formatting");
  });

  it("toggles formatting visibility without touching editor content", () => {
    const button = new MockToolbarButton();
    const toolbar = new MockToolbarRegion(true);

    installFormattingToolbarToggle(button, toolbar);
    button.triggerClick();
    expect(toolbar.hidden).toBe(false);
    expect(button.attributes.get("aria-expanded")).toBe("true");
    expect(button.textContent).toBe("Hide formatting");

    button.triggerClick();
    expect(toolbar.hidden).toBe(true);
    expect(button.attributes.get("aria-expanded")).toBe("false");
    expect(button.textContent).toBe("Show formatting");
  });
});

function createKeydownEvent(key: string) {
  return {
    key,
    stopPropagation: vi.fn<() => void>(),
  };
}

describe("EditorExitGesture", () => {
  it("arms on Escape", () => {
    const gesture = new EditorExitGesture();

    gesture.handleKeydown(createKeydownEvent("Escape"));

    expect(gesture.isArmed).toBe(true);
  });

  it("consumes an armed Tab without preventDefault and clears the armed state", () => {
    const gesture = new EditorExitGesture();
    gesture.handleKeydown(createKeydownEvent("Escape"));

    const tabEvent = createKeydownEvent("Tab");
    gesture.handleKeydown(tabEvent);

    expect(gesture.isArmed).toBe(false);
    expect(tabEvent.stopPropagation).toHaveBeenCalledTimes(1);
    expect(
      (tabEvent as unknown as { preventDefault?: unknown }).preventDefault,
    ).toBeUndefined();
  });

  it("consumes an armed Shift+Tab the same way for backward focus movement", () => {
    const gesture = new EditorExitGesture();
    gesture.handleKeydown(createKeydownEvent("Escape"));

    // Shift+Tab still reports key "Tab"; shiftKey only changes native focus
    // direction, which this gesture never inspects or interferes with.
    const shiftTabEvent = createKeydownEvent("Tab");
    gesture.handleKeydown(shiftTabEvent);

    expect(gesture.isArmed).toBe(false);
    expect(shiftTabEvent.stopPropagation).toHaveBeenCalledTimes(1);
  });

  it("leaves an ordinary Tab untouched when not armed", () => {
    const gesture = new EditorExitGesture();

    const tabEvent = createKeydownEvent("Tab");
    gesture.handleKeydown(tabEvent);

    expect(gesture.isArmed).toBe(false);
    expect(tabEvent.stopPropagation).not.toHaveBeenCalled();
  });

  it("leaves an ordinary Shift+Tab untouched when not armed", () => {
    const gesture = new EditorExitGesture();

    const shiftTabEvent = createKeydownEvent("Tab");
    gesture.handleKeydown(shiftTabEvent);

    expect(gesture.isArmed).toBe(false);
    expect(shiftTabEvent.stopPropagation).not.toHaveBeenCalled();
  });

  it("stays armed while only a modifier key is pressed", () => {
    const gesture = new EditorExitGesture();
    gesture.handleKeydown(createKeydownEvent("Escape"));

    gesture.handleKeydown(createKeydownEvent("Shift"));

    expect(gesture.isArmed).toBe(true);
  });

  it("cancels the armed state on any other non-modifier key", () => {
    const gesture = new EditorExitGesture();
    gesture.handleKeydown(createKeydownEvent("Escape"));

    const letterEvent = createKeydownEvent("a");
    gesture.handleKeydown(letterEvent);

    expect(gesture.isArmed).toBe(false);
    expect(letterEvent.stopPropagation).not.toHaveBeenCalled();
  });

  it("cancels the armed state on pointer interaction", () => {
    const gesture = new EditorExitGesture();
    gesture.handleKeydown(createKeydownEvent("Escape"));

    gesture.handlePointerDown();

    expect(gesture.isArmed).toBe(false);
  });

  it("cancels the armed state on blur", () => {
    const gesture = new EditorExitGesture();
    gesture.handleKeydown(createKeydownEvent("Escape"));

    gesture.handleBlur();

    expect(gesture.isArmed).toBe(false);
  });

  it("does not modify content or force blur when Escape is pressed", () => {
    const gesture = new EditorExitGesture();
    const escapeEvent = createKeydownEvent("Escape");

    gesture.handleKeydown(escapeEvent);

    expect(escapeEvent.stopPropagation).not.toHaveBeenCalled();
    expect(gesture.isArmed).toBe(true);
  });
});

class MockEditorExitGestureTarget implements EditorExitGestureTarget {
  private keydownListener:
    | ((event: EditorExitKeydownEventLike) => void)
    | null = null;
  private pointerdownListener: (() => void) | null = null;
  private blurListener: (() => void) | null = null;

  addEventListener(
    type: "keydown" | "pointerdown" | "blur",
    listener: ((event: EditorExitKeydownEventLike) => void) | (() => void),
    options: { capture: true },
  ): void {
    expect(options).toEqual({ capture: true });
    if (type === "keydown") {
      this.keydownListener = listener as (
        event: EditorExitKeydownEventLike,
      ) => void;
    } else if (type === "pointerdown") {
      this.pointerdownListener = listener as () => void;
    } else {
      this.blurListener = listener as () => void;
    }
  }

  triggerKeydown(event: EditorExitKeydownEventLike): void {
    this.keydownListener?.(event);
  }

  triggerPointerdown(): void {
    this.pointerdownListener?.();
  }

  triggerBlur(): void {
    this.blurListener?.();
  }
}

describe("installEditorExitGesture", () => {
  it("wires a capture-phase keydown listener that arms on Escape and consumes an armed Tab", () => {
    const target = new MockEditorExitGestureTarget();
    const gesture = installEditorExitGesture(target);

    target.triggerKeydown(createKeydownEvent("Escape"));
    expect(gesture.isArmed).toBe(true);

    const tabEvent = createKeydownEvent("Tab");
    target.triggerKeydown(tabEvent);
    expect(gesture.isArmed).toBe(false);
    expect(tabEvent.stopPropagation).toHaveBeenCalledTimes(1);
  });

  it("disarms through the same target on pointerdown", () => {
    const target = new MockEditorExitGestureTarget();
    const gesture = installEditorExitGesture(target);

    target.triggerKeydown(createKeydownEvent("Escape"));
    target.triggerPointerdown();

    expect(gesture.isArmed).toBe(false);
  });

  it("disarms through the same target on blur", () => {
    const target = new MockEditorExitGestureTarget();
    const gesture = installEditorExitGesture(target);

    target.triggerKeydown(createKeydownEvent("Escape"));
    target.triggerBlur();

    expect(gesture.isArmed).toBe(false);
  });

  it("an armed Tab never reaches a downstream plain-text handler, matching real capture-phase bypass", () => {
    const target = new MockEditorExitGestureTarget();
    installEditorExitGesture(target);

    // Simulates the real DOM contract: installEditorExitGesture's listener
    // runs in the capture phase on an ancestor of the editor's contenteditable
    // node, so calling stopPropagation() there prevents the event from ever
    // reaching ProseMirror's own bubble-phase keydown handler (where
    // buildPlainTextTabResult would otherwise run).
    let plainTextHandlerCalls = 0;
    function dispatchThroughDom(key: string) {
      const event = createKeydownEvent(key);
      target.triggerKeydown(event); // capture phase: EditorExitGesture
      const wasStopped = event.stopPropagation.mock.calls.length > 0;
      // The plain-text handler only binds Tab/Shift-Tab, so only those keys
      // would ever reach it downstream; Escape reaching downstream is
      // expected and harmless (no handler binds it there).
      if (key === "Tab" && !wasStopped) {
        plainTextHandlerCalls += 1; // bubble phase: would-be ProseMirror handling
      }
    }

    dispatchThroughDom("Escape");
    dispatchThroughDom("Tab");

    expect(plainTextHandlerCalls).toBe(0);
  });

  it("an unarmed Tab does reach the downstream plain-text handler", () => {
    const target = new MockEditorExitGestureTarget();
    installEditorExitGesture(target);

    let plainTextHandlerCalls = 0;
    function dispatchThroughDom(key: string) {
      const event = createKeydownEvent(key);
      target.triggerKeydown(event);
      const wasStopped = event.stopPropagation.mock.calls.length > 0;
      if (!wasStopped) {
        plainTextHandlerCalls += 1;
      }
    }

    dispatchThroughDom("Tab");

    expect(plainTextHandlerCalls).toBe(1);
  });
});

class FakeLinkClickTarget implements LinkClickTargetLike {
  constructor(private readonly anchor: LinkAnchorLike | null) {}

  closest(): LinkAnchorLike | null {
    return this.anchor;
  }
}

class FakeLinkClickRoot implements LinkClickRootLike {
  constructor(private readonly containedAnchors: LinkAnchorLike[]) {}

  contains(node: unknown): boolean {
    return (
      node !== null && this.containedAnchors.includes(node as LinkAnchorLike)
    );
  }
}

function extensionContext(editor: unknown) {
  return {
    editor,
    options: LinkOpenGesture.options,
    storage: LinkOpenGesture.storage,
    name: LinkOpenGesture.name,
    type: null,
    parent: null,
  } as never;
}

describe("openValidatedLink", () => {
  it("opens an https link with _blank and noopener,noreferrer", () => {
    const openFn = vi.fn();

    const opened = openValidatedLink("https://example.com", openFn);

    expect(opened).toBe(true);
    expect(openFn).toHaveBeenCalledWith(
      "https://example.com",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("opens a mailto link", () => {
    const openFn = vi.fn();

    const opened = openValidatedLink("mailto:person@example.com", openFn);

    expect(opened).toBe(true);
    expect(openFn).toHaveBeenCalledWith(
      "mailto:person@example.com",
      "_blank",
      "noopener,noreferrer",
    );
  });

  it("does not open a disallowed protocol", () => {
    const openFn = vi.fn();

    expect(openValidatedLink("javascript:alert(1)", openFn)).toBe(false);
    expect(openValidatedLink("ftp://example.com", openFn)).toBe(false);
    expect(openFn).not.toHaveBeenCalled();
  });

  it("does not open a missing or empty href", () => {
    const openFn = vi.fn();

    expect(openValidatedLink(null, openFn)).toBe(false);
    expect(openValidatedLink(undefined, openFn)).toBe(false);
    expect(openValidatedLink("", openFn)).toBe(false);
    expect(openFn).not.toHaveBeenCalled();
  });
});

describe("resolveModifierClickLinkHref", () => {
  const anchor: LinkAnchorLike = { href: "https://example.com" };
  const root = new FakeLinkClickRoot([anchor]);

  it("resolves the href for a Ctrl+Click on an editor link", () => {
    const href = resolveModifierClickLinkHref(
      {
        button: 0,
        ctrlKey: true,
        metaKey: false,
        target: new FakeLinkClickTarget(anchor),
      },
      root,
    );

    expect(href).toBe("https://example.com");
  });

  it("resolves the href for a Cmd+Click (metaKey) on an editor link", () => {
    const href = resolveModifierClickLinkHref(
      {
        button: 0,
        ctrlKey: false,
        metaKey: true,
        target: new FakeLinkClickTarget(anchor),
      },
      root,
    );

    expect(href).toBe("https://example.com");
  });

  it("returns null for a normal click with no modifier", () => {
    const href = resolveModifierClickLinkHref(
      {
        button: 0,
        ctrlKey: false,
        metaKey: false,
        target: new FakeLinkClickTarget(anchor),
      },
      root,
    );

    expect(href).toBeNull();
  });

  it("returns null for a modifier click outside any link", () => {
    const href = resolveModifierClickLinkHref(
      {
        button: 0,
        ctrlKey: true,
        metaKey: false,
        target: new FakeLinkClickTarget(null),
      },
      root,
    );

    expect(href).toBeNull();
  });

  it("returns null for a link anchor outside the editor root", () => {
    const outsideAnchor: LinkAnchorLike = {
      href: "https://outside.example.com",
    };

    const href = resolveModifierClickLinkHref(
      {
        button: 0,
        ctrlKey: true,
        metaKey: false,
        target: new FakeLinkClickTarget(outsideAnchor),
      },
      root,
    );

    expect(href).toBeNull();
  });

  it("returns null for a non-primary-button click even with a modifier held", () => {
    const href = resolveModifierClickLinkHref(
      {
        button: 1,
        ctrlKey: true,
        metaKey: false,
        target: new FakeLinkClickTarget(anchor),
      },
      root,
    );

    expect(href).toBeNull();
  });
});

describe("getActiveLinkHref", () => {
  const schema = getSchema(buildEditorExtensions());

  it("returns the href when the cursor is inside an active link mark", () => {
    const doc = schema.nodeFromJSON({
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [
            {
              type: "text",
              text: "click me",
              marks: [{ type: "link", attrs: { href: "https://example.com" } }],
            },
          ],
        },
      ],
    });
    const state = EditorState.create({
      schema,
      doc,
      selection: TextSelection.create(doc, 3),
    });

    expect(getActiveLinkHref(state)).toBe("https://example.com");
  });

  it("returns null when there is no active link", () => {
    const doc = schema.nodeFromJSON({
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "plain text" }] },
      ],
    });
    const state = EditorState.create({
      schema,
      doc,
      selection: TextSelection.create(doc, 3),
    });

    expect(getActiveLinkHref(state)).toBeNull();
  });
});

describe("LinkOpenGesture", () => {
  it("binds only Mod-Alt-Enter, leaving Mod-Enter (hard break) untouched", () => {
    const shortcuts = LinkOpenGesture.config.addKeyboardShortcuts?.call(
      extensionContext({}),
    );

    expect(Object.keys(shortcuts ?? {})).toEqual(["Mod-Alt-Enter"]);
  });

  it("opens the active link on Mod-Alt-Enter and returns true", () => {
    const schema = getSchema(buildEditorExtensions());
    const doc = schema.nodeFromJSON({
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [
            {
              type: "text",
              text: "click me",
              marks: [{ type: "link", attrs: { href: "https://example.com" } }],
            },
          ],
        },
      ],
    });
    const state = EditorState.create({
      schema,
      doc,
      selection: TextSelection.create(doc, 3),
    });
    const openSpy = vi.fn();
    vi.stubGlobal("window", { open: openSpy });

    const shortcuts = LinkOpenGesture.config.addKeyboardShortcuts?.call(
      extensionContext({ state }),
    );
    const handled = shortcuts?.["Mod-Alt-Enter"]?.({} as never);

    expect(handled).toBe(true);
    expect(openSpy).toHaveBeenCalledWith(
      "https://example.com",
      "_blank",
      "noopener,noreferrer",
    );

    vi.unstubAllGlobals();
  });

  it("returns false on Mod-Alt-Enter when there is no active link", () => {
    const schema = getSchema(buildEditorExtensions());
    const doc = schema.nodeFromJSON({
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "plain" }] },
      ],
    });
    const state = EditorState.create({
      schema,
      doc,
      selection: TextSelection.create(doc, 2),
    });
    const openSpy = vi.fn();
    vi.stubGlobal("window", { open: openSpy });

    const shortcuts = LinkOpenGesture.config.addKeyboardShortcuts?.call(
      extensionContext({ state }),
    );
    const handled = shortcuts?.["Mod-Alt-Enter"]?.({} as never);

    expect(handled).toBe(false);
    expect(openSpy).not.toHaveBeenCalled();

    vi.unstubAllGlobals();
  });

  it("wires a handleClick plugin prop that opens on modifier-click and prevents default only when handled", () => {
    const openSpy = vi.fn();
    vi.stubGlobal("window", { open: openSpy });
    const plugins = LinkOpenGesture.config.addProseMirrorPlugins?.call(
      extensionContext({}),
    );
    const handleClick = plugins?.[0]?.props?.handleClick as
      | ((view: unknown, pos: number, event: unknown) => boolean)
      | undefined;
    expect(handleClick).toBeTypeOf("function");

    const anchor = { href: "https://example.com" };
    const fakeView = { dom: { contains: (node: unknown) => node === anchor } };
    const preventDefault = vi.fn();
    const event = {
      button: 0,
      ctrlKey: true,
      metaKey: false,
      target: { closest: () => anchor },
      preventDefault,
    };

    const handled = handleClick?.(fakeView, 0, event);

    expect(handled).toBe(true);
    expect(preventDefault).toHaveBeenCalledTimes(1);
    expect(openSpy).toHaveBeenCalledWith(
      "https://example.com",
      "_blank",
      "noopener,noreferrer",
    );

    vi.unstubAllGlobals();
  });

  it("does not prevent default for an ordinary click that is not handled", () => {
    const plugins = LinkOpenGesture.config.addProseMirrorPlugins?.call(
      extensionContext({}),
    );
    const handleClick = plugins?.[0]?.props?.handleClick as
      | ((view: unknown, pos: number, event: unknown) => boolean)
      | undefined;

    const anchor = { href: "https://example.com" };
    const fakeView = { dom: { contains: (node: unknown) => node === anchor } };
    const preventDefault = vi.fn();
    const event = {
      button: 0,
      ctrlKey: false,
      metaKey: false,
      target: { closest: () => anchor },
      preventDefault,
    };

    const handled = handleClick?.(fakeView, 0, event);

    expect(handled).toBe(false);
    expect(preventDefault).not.toHaveBeenCalled();
  });

  it("does not prevent default or open for a modifier click outside any link", () => {
    const plugins = LinkOpenGesture.config.addProseMirrorPlugins?.call(
      extensionContext({}),
    );
    const handleClick = plugins?.[0]?.props?.handleClick as
      | ((view: unknown, pos: number, event: unknown) => boolean)
      | undefined;

    const fakeView = { dom: { contains: () => false } };
    const preventDefault = vi.fn();
    const event = {
      button: 0,
      ctrlKey: true,
      metaKey: false,
      target: { closest: () => null },
      preventDefault,
    };

    const handled = handleClick?.(fakeView, 0, event);

    expect(handled).toBe(false);
    expect(preventDefault).not.toHaveBeenCalled();
  });

  it("does not open a link that belongs to an anchor outside the editor root", () => {
    const openSpy = vi.fn();
    vi.stubGlobal("window", { open: openSpy });
    const plugins = LinkOpenGesture.config.addProseMirrorPlugins?.call(
      extensionContext({}),
    );
    const handleClick = plugins?.[0]?.props?.handleClick as
      | ((view: unknown, pos: number, event: unknown) => boolean)
      | undefined;

    const outsideAnchor = { href: "https://outside.example.com" };
    // The editor root only "contains" links that actually live inside it.
    const fakeView = { dom: { contains: () => false } };
    const preventDefault = vi.fn();
    const event = {
      button: 0,
      ctrlKey: true,
      metaKey: false,
      target: { closest: () => outsideAnchor },
      preventDefault,
    };

    const handled = handleClick?.(fakeView, 0, event);

    expect(handled).toBe(false);
    expect(preventDefault).not.toHaveBeenCalled();
    expect(openSpy).not.toHaveBeenCalled();

    vi.unstubAllGlobals();
  });
});

describe("runToolbarCommand", () => {
  it("clears formatting with the expected command chain", () => {
    const editor = new MockEditor();

    expect(
      runToolbarCommand(
        editor,
        { command: "clearFormatting" },
        vi.fn(() => null),
      ),
    ).toBe(true);
    expect(editor.chainInstance.calls).toEqual([
      "focus",
      "unsetAllMarks",
      "clearNodes",
      "run",
    ]);
  });

  it("rejects invalid link prompts", () => {
    const editor = new MockEditor();

    expect(
      runToolbarCommand(
        editor,
        { command: "link" },
        vi.fn(() => "javascript:alert(1)"),
      ),
    ).toBe(false);
    expect(editor.chainInstance.calls).toEqual(["focus"]);
  });

  it("sets approved links", () => {
    const editor = new MockEditor();

    expect(
      runToolbarCommand(
        editor,
        { command: "link" },
        vi.fn(() => "https://example.com"),
      ),
    ).toBe(true);
    expect(editor.chainInstance.calls).toEqual([
      "focus",
      "extendMarkRange:link",
      "setLink:https://example.com",
      "run",
    ]);
  });
});

describe("createBeforeUnloadHandler", () => {
  it("warns only while the note is in an unsafe save state", () => {
    let state: "clean" | "dirty" = "clean";
    const handler = createBeforeUnloadHandler(() => state);
    const cleanEvent = {
      preventDefault: vi.fn(),
      returnValue: "",
    };

    handler(cleanEvent);
    expect(cleanEvent.preventDefault).not.toHaveBeenCalled();

    state = "dirty";
    const dirtyEvent = {
      preventDefault: vi.fn(),
      returnValue: "",
    };

    handler(dirtyEvent);
    expect(dirtyEvent.preventDefault).toHaveBeenCalledTimes(1);
    expect(dirtyEvent.returnValue).toBe("");
  });

  it("allows intentional reload actions to bypass the unload warning", () => {
    const handler = createBeforeUnloadHandler(
      () => "conflicted",
      () => true,
    );
    const event = {
      preventDefault: vi.fn(),
      returnValue: "",
    };

    handler(event);
    expect(event.preventDefault).not.toHaveBeenCalled();
  });
});

describe("copyTextWithFallback", () => {
  function createClipboardContext() {
    const textarea = {
      focus: vi.fn(),
      select: vi.fn(),
      setAttribute: vi.fn(),
      style: { left: "", opacity: "", position: "", top: "" },
      value: "",
    };
    const body = {
      appendChild: vi.fn(),
      removeChild: vi.fn(),
    };
    const documentLike: CopyDocumentLike = {
      body,
      createElement: vi.fn(() => textarea),
      execCommand: vi.fn(() => true),
    };

    return { body, documentLike, textarea };
  }

  it("uses the modern clipboard API when available", async () => {
    const navigatorLike: NavigatorLike = {
      clipboard: {
        writeText: vi.fn(() => Promise.resolve()),
      },
    };
    const { documentLike } = createClipboardContext();

    await expect(
      copyTextWithFallback("Copied text", { documentLike, navigatorLike }),
    ).resolves.toBe(true);
    expect(navigatorLike.clipboard?.writeText).toHaveBeenCalledWith(
      "Copied text",
    );
    expect(documentLike.createElement).not.toHaveBeenCalled();
  });

  it("falls back after a rejected modern clipboard request", async () => {
    const navigatorLike: NavigatorLike = {
      clipboard: {
        writeText: vi.fn(() => Promise.reject(new Error("nope"))),
      },
    };
    const { body, documentLike, textarea } = createClipboardContext();

    await expect(
      copyTextWithFallback("Copied text", { documentLike, navigatorLike }),
    ).resolves.toBe(true);
    expect(documentLike.execCommand).toHaveBeenCalledWith("copy");
    expect(body.appendChild).toHaveBeenCalledWith(textarea);
    expect(body.removeChild).toHaveBeenCalledWith(textarea);
  });

  it("falls back when the modern clipboard API is unavailable", async () => {
    const { documentLike } = createClipboardContext();

    await expect(
      copyTextWithFallback("Copied text", {
        documentLike,
        navigatorLike: {},
      }),
    ).resolves.toBe(true);
    expect(documentLike.execCommand).toHaveBeenCalledWith("copy");
  });

  it("returns false when both clipboard methods fail", async () => {
    const navigatorLike: NavigatorLike = {
      clipboard: {
        writeText: vi.fn(() => Promise.reject(new Error("nope"))),
      },
    };
    const { documentLike } = createClipboardContext();
    documentLike.execCommand = vi.fn(() => false);

    await expect(
      copyTextWithFallback("Copied text", { documentLike, navigatorLike }),
    ).resolves.toBe(false);
  });
});

describe("NoteSaveController", () => {
  it("autosaves title and body together after the debounce interval", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        title: "Project plan",
        version: 2,
        modified_at: "2026-06-29T12:00:00Z",
      }),
    ]);

    context.titleInput.value = "Project plan";
    context.editor.json = {
      type: "doc",
      content: [{ type: "paragraph", content: [{ type: "text", text: "Hi" }] }],
    };

    context.controller.handleDocumentEdited();
    expect(context.controller.getState()).toBe("dirty");
    expect(context.statusRegion.textContent).toBe("Unsaved changes");
    expect(context.scheduler.hasDelay(AUTOSAVE_DEBOUNCE_MS)).toBe(true);
    expect(context.scheduler.hasDelay(MAX_PENDING_SAVE_MS)).toBe(true);

    context.scheduler.runDelay(AUTOSAVE_DEBOUNCE_MS);
    await flushAsyncWork();

    expect(context.fetchCalls).toHaveLength(1);
    expect(context.fetchCalls[0].method).toBe("POST");
    expect(JSON.parse(context.fetchCalls[0].body ?? "{}")).toEqual({
      title: "Project plan",
      version: 1,
      body_json: {
        type: "doc",
        content: [
          { type: "paragraph", content: [{ type: "text", text: "Hi" }] },
        ],
      },
    });
    expect(context.controller.getState()).toBe("clean");
    expect(context.statusRegion.textContent).toBe("Saved");
    expect(context.versionInput.value).toBe("2");
  });

  it("omits the idle-save header on an ordinary autosave", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        title: "Generated title",
        version: 2,
        modified_at: "2026-06-29T12:00:00Z",
      }),
    ]);

    context.controller.handleDocumentEdited();
    context.scheduler.runDelay(AUTOSAVE_DEBOUNCE_MS);
    await flushAsyncWork();

    expect(context.fetchCalls).toHaveLength(1);
    expect(context.fetchCalls[0].headers[IDLE_WARNING_SAVE_HEADER]).toBe(
      undefined,
    );
  });

  it("omits the idle-save header on an ordinary flushPendingSave() (e.g. Print)", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        title: "Generated title",
        version: 2,
        modified_at: "2026-06-29T12:00:00Z",
      }),
    ]);

    context.controller.handleDocumentEdited();
    await context.controller.flushPendingSave();
    await flushAsyncWork();

    expect(context.fetchCalls).toHaveLength(1);
    expect(context.fetchCalls[0].headers[IDLE_WARNING_SAVE_HEADER]).toBe(
      undefined,
    );
  });

  it("adds the idle-save header only when flushPendingSave is called with suppressIdleActivity", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        title: "Generated title",
        version: 2,
        modified_at: "2026-06-29T12:00:00Z",
      }),
    ]);

    context.controller.handleDocumentEdited();
    await context.controller.flushPendingSave({ suppressIdleActivity: true });
    await flushAsyncWork();

    expect(context.fetchCalls).toHaveLength(1);
    expect(context.fetchCalls[0].headers[IDLE_WARNING_SAVE_HEADER]).toBe("1");
  });

  it("calls onTitleSaved with the saved title after a successful save", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        title: "Project plan",
        version: 2,
        modified_at: "2026-06-29T12:00:00Z",
      }),
    ]);

    context.titleInput.value = "Project plan";
    context.controller.handleDocumentEdited();
    context.scheduler.runDelay(AUTOSAVE_DEBOUNCE_MS);
    await flushAsyncWork();

    expect(context.onTitleSaved).toHaveBeenCalledTimes(1);
    expect(context.onTitleSaved).toHaveBeenCalledWith("Project plan");
  });

  it("queues the latest snapshot while a save is already in flight", async () => {
    const firstSave = createDeferredFetch();
    const secondSave = createDeferredFetch();
    const context = createSaveControllerContext([firstSave, secondSave]);

    context.titleInput.value = "Draft one";
    context.editor.json = { type: "doc", content: [] };
    const firstSavePromise = context.controller.flushPendingSave();
    await flushAsyncWork();

    context.titleInput.value = "Draft two";
    context.editor.json = {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Second" }] },
      ],
    };
    context.controller.handleDocumentEdited();
    expect(context.controller.getState()).toBe("dirty_during_save");

    firstSave.resolve(
      createResponse(200, {
        ok: true,
        title: "Draft one",
        version: 2,
        modified_at: "2026-06-29T12:00:00Z",
      }),
    );
    await flushAsyncWork();

    expect(context.fetchCalls).toHaveLength(2);
    expect(JSON.parse(context.fetchCalls[1].body ?? "{}")).toEqual({
      title: "Draft two",
      version: 2,
      body_json: {
        type: "doc",
        content: [
          { type: "paragraph", content: [{ type: "text", text: "Second" }] },
        ],
      },
    });

    secondSave.resolve(
      createResponse(200, {
        ok: true,
        title: "Draft two",
        version: 3,
        modified_at: "2026-06-29T12:00:02Z",
      }),
    );
    await firstSavePromise;
    await flushAsyncWork();

    expect(context.controller.getState()).toBe("clean");
    expect(context.versionInput.value).toBe("3");
  });

  it("retries once after a transient server failure", async () => {
    const context = createSaveControllerContext([
      createResponse(500, { ok: false, error: "server_error" }),
      createResponse(200, {
        ok: true,
        title: "Recovered note",
        version: 2,
        modified_at: "2026-06-29T12:00:04Z",
      }),
    ]);

    context.titleInput.value = "Recovered note";
    context.controller.handleDocumentEdited();
    context.scheduler.runDelay(AUTOSAVE_DEBOUNCE_MS);
    await flushAsyncWork();

    expect(context.controller.getState()).toBe("failed");
    expect(context.noticeText.textContent).toBe(
      "Save failed. RidgeNote will retry once shortly.",
    );
    expect(context.scheduler.hasDelay(RETRY_DELAY_MS)).toBe(true);
    expect(context.statusRegion.attributes.get("data-tone")).toBe("yellow");
    expect(context.statusRegion.attributes.get("title")).toBe(
      "Retrying save shortly",
    );

    context.scheduler.runDelay(RETRY_DELAY_MS);
    await flushAsyncWork();

    expect(context.fetchCalls).toHaveLength(2);
    expect(context.controller.getState()).toBe("clean");
    expect(context.versionInput.value).toBe("2");
  });

  it("keeps all print instances synchronized and allows either instance to print when current", async () => {
    const context = createSaveControllerContext([]);

    expect(context.printLinks).toHaveLength(2);
    expect(context.printLinks[0].attributes.get("aria-disabled")).toBe("false");
    expect(context.printLinks[1].attributes.get("aria-disabled")).toBe("false");

    context.printLinks[1].triggerClick();

    expect(context.printTabs[0].assignedUrl).toBe("/notes/1/print/");
    expect(context.locationAssign).not.toHaveBeenCalled();
  });

  it("blocks direct and overflow print equally while the note has unsaved changes", async () => {
    const context = createSaveControllerContext([
      createResponse(400, {
        ok: false,
        errors: {
          title: [{ code: "invalid", message: "Title is invalid" }],
        },
      }),
    ]);

    context.titleInput.value = "Unsaved";
    context.controller.handleDocumentEdited();

    expect(context.printLinks[0].attributes.get("aria-disabled")).toBe("true");
    expect(context.printLinks[1].attributes.get("aria-disabled")).toBe("true");

    // Either instance routes through the same gated flow -- exercise
    // the overflow instance here (the direct instance is covered by
    // the dedicated save/print tests above).
    const overflowPrevented = context.printLinks[1].triggerClick();
    await flushAsyncWork(20);

    expect(overflowPrevented).toBe(true);
    expect(context.locationAssign).not.toHaveBeenCalled();
    expect(context.printTabs[0].assignedUrl).toBeNull();
    expect(context.printTabs[0].closed).toBe(true);
    expect(context.noticeText.textContent).toBe(
      "Printing is available only when this note is saved and current.",
    );
  });

  it("keeps all export instances synchronized and allows either instance to navigate when current", async () => {
    const context = createSaveControllerContext([]);

    expect(context.exportLinks).toHaveLength(2);
    expect(context.exportLinks[0].attributes.get("aria-disabled")).toBe(
      "false",
    );
    expect(context.exportLinks[1].attributes.get("aria-disabled")).toBe(
      "false",
    );

    const directPrevented = context.exportLinks[0].triggerClick();
    const overflowPrevented = context.exportLinks[1].triggerClick();

    expect(directPrevented).toBe(false);
    expect(overflowPrevented).toBe(false);
    expect(context.noticeRegion.hidden).toBe(true);
  });

  it("blocks every export instance when a newer remote version exists", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Updated elsewhere",
          body_json: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [{ type: "text", text: "Server body" }],
              },
            ],
          },
          editor_schema_version: 1,
          version: 3,
          modified_at: "2026-06-29T12:10:00Z",
        },
      }),
    ]);

    context.titleInput.value = "Local draft";
    context.editor.json = {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Mine" }] },
      ],
    };
    context.controller.handleDocumentEdited();
    context.windowEvents.triggerFocus();
    await flushAsyncWork();

    expect(context.exportLinks[0].attributes.get("aria-disabled")).toBe("true");
    expect(context.exportLinks[1].attributes.get("aria-disabled")).toBe("true");
    expect(context.exportLinks[0].triggerClick()).toBe(true);
    expect(context.exportLinks[1].triggerClick()).toBe(true);
    expect(context.noticeText.textContent).toBe(
      "Export JSON is unavailable until this note is saved and current.",
    );
  });

  it("blocks every export instance during a true conflict", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    await context.controller.flushPendingSave();
    await flushAsyncWork();

    expect(context.exportLinks[0].attributes.get("aria-disabled")).toBe("true");
    expect(context.exportLinks[1].attributes.get("aria-disabled")).toBe("true");
    expect(context.exportLinks[0].triggerClick()).toBe(true);
    expect(context.exportLinks[1].triggerClick()).toBe(true);
    expect(context.noticeText.textContent).toBe(
      "Export JSON is unavailable while this note is in conflict. Copy local draft or reload latest first.",
    );
  });

  it("silently applies newer server content when the local editor is clean", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Updated elsewhere",
          body_json: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [{ type: "text", text: "Server body" }],
              },
            ],
          },
          editor_schema_version: 1,
          version: 4,
          modified_at: "2026-06-29T12:10:00Z",
        },
      }),
    ]);

    context.windowEvents.triggerFocus();
    await flushAsyncWork();

    expect(context.fetchCalls).toHaveLength(1);
    expect(context.fetchCalls[0].method).toBe("GET");
    expect(context.fetchCalls[0].input).toBe("/notes/1/freshness/?version=1");
    expect(context.titleInput.value).toBe("Updated elsewhere");
    expect(context.editor.json).toEqual({
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [{ type: "text", text: "Server body" }],
        },
      ],
    });
    expect(context.versionInput.value).toBe("4");
    expect(context.controller.getState()).toBe("clean");
    expect(context.statusRegion.attributes.get("data-tone")).toBe("green");
    expect(
      context.fetchCalls.filter((call) => call.method === "POST"),
    ).toHaveLength(0);
    expect(context.scheduler.hasDelay(AUTOSAVE_DEBOUNCE_MS)).toBe(false);
  });

  it("ignores a stale freshness response after a same-tab autosave leaves the editor clean", async () => {
    const firstFreshness = createDeferredFetch();
    const context = createSaveControllerContext([
      firstFreshness,
      createResponse(200, {
        ok: true,
        title: "Local save",
        version: 2,
        modified_at: "2026-06-29T12:12:00Z",
      }),
    ]);

    context.windowEvents.triggerFocus();
    await flushAsyncWork();

    context.titleInput.value = "Local save";
    context.editor.json = {
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [{ type: "text", text: "Local body" }],
        },
      ],
    };
    context.controller.handleDocumentEdited();
    await context.controller.flushPendingSave();
    await flushAsyncWork();

    firstFreshness.resolve(
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Stale fresh response",
          body_json: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [{ type: "text", text: "Stale" }],
              },
            ],
          },
          editor_schema_version: 1,
          version: 2,
          modified_at: "2026-06-29T12:11:00Z",
        },
      }),
    );
    await flushAsyncWork();

    expect(context.titleInput.value).toBe("Local save");
    expect(context.editor.json).toEqual({
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [{ type: "text", text: "Local body" }],
        },
      ],
    });
    expect(context.noticeRegion.hidden).toBe(true);
    expect(context.noticeActions.hidden).toBe(true);
    expect(context.controller.getState()).toBe("clean");
    expect(context.statusRegion.attributes.get("data-tone")).toBe("green");
  });

  it("saves a Shift+Enter hard break after a silent remote refresh without a false conflict", async () => {
    const staleSaveRequest = createDeferredFetch();
    const context = createSaveControllerContext([
      staleSaveRequest,
      createResponse(200, {
        ok: true,
        title: "Test Line 1",
        version: 3,
        modified_at: "2026-06-29T12:12:30Z",
      }),
    ]);

    context.titleInput.value = "Draft before refresh";
    context.editor.json = {
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [{ type: "text", text: "Draft before refresh" }],
        },
      ],
    };
    context.controller.handleDocumentEdited();
    const savePromise = context.controller.flushPendingSave();
    await flushAsyncWork();

    (
      context.controller as unknown as {
        applyRemoteState(note: {
          body_json: unknown;
          editor_schema_version: number;
          modified_at: string;
          title: string;
          version: number;
        }): void;
      }
    ).applyRemoteState({
      body_json: {
        type: "doc",
        content: [
          {
            type: "paragraph",
            content: [{ type: "text", text: "Test Line 1" }],
          },
        ],
      },
      editor_schema_version: 1,
      modified_at: "2026-06-29T12:12:00Z",
      title: "Test Line 1",
      version: 2,
    });

    context.titleInput.value = "Test Line 1";
    context.editor.json = {
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [
            { type: "text", text: "Test Line 1" },
            { type: "hardBreak" },
            { type: "text", text: "Test Line 2" },
          ],
        },
      ],
    };
    context.controller.handleDocumentEdited();

    staleSaveRequest.resolve(
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 2,
        current_modified_at: "2026-06-29T12:12:00Z",
      }),
    );
    await savePromise;
    await flushAsyncWork();

    expect(context.controller.getState()).toBe("dirty");
    expect(context.versionInput.value).toBe("2");
    expect(context.copyLocalButton.hidden).toBe(true);
    expect(context.reloadButton.hidden).toBe(true);
    expect(context.noticeActions.hidden).toBe(true);

    context.scheduler.runDelay(AUTOSAVE_DEBOUNCE_MS);
    await flushAsyncWork();

    expect(context.controller.getState()).toBe("clean");
    expect(context.versionInput.value).toBe("3");
    expect(context.editor.json).toEqual({
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [
            { type: "text", text: "Test Line 1" },
            { type: "hardBreak" },
            { type: "text", text: "Test Line 2" },
          ],
        },
      ],
    });
  });

  it("ignores a stale freshness response after a same-tab autosave succeeds and the user keeps typing", async () => {
    const firstFreshness = createDeferredFetch();
    const context = createSaveControllerContext([
      firstFreshness,
      createResponse(200, {
        ok: true,
        title: "Saved locally",
        version: 2,
        modified_at: "2026-06-29T12:12:00Z",
      }),
    ]);

    context.windowEvents.triggerFocus();
    await flushAsyncWork();

    context.titleInput.value = "Saved locally";
    context.editor.json = {
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [{ type: "text", text: "First body" }],
        },
      ],
    };
    context.controller.handleDocumentEdited();
    await context.controller.flushPendingSave();
    await flushAsyncWork();

    context.titleInput.value = "Saved locally plus more";
    context.editor.json = {
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [{ type: "text", text: "Second body" }],
        },
      ],
    };
    context.controller.handleDocumentEdited();

    firstFreshness.resolve(
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Stale fresh response",
          body_json: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [{ type: "text", text: "Stale" }],
              },
            ],
          },
          editor_schema_version: 1,
          version: 2,
          modified_at: "2026-06-29T12:11:00Z",
        },
      }),
    );
    await flushAsyncWork();

    expect(context.titleInput.value).toBe("Saved locally plus more");
    expect(context.editor.json).toEqual({
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [{ type: "text", text: "Second body" }],
        },
      ],
    });
    expect(context.noticeRegion.hidden).toBe(true);
    expect(context.noticeActions.hidden).toBe(true);
    expect(context.reloadButton.hidden).toBe(true);
    expect(context.copyLocalButton.hidden).toBe(true);
    expect(context.controller.getState()).toBe("dirty");
    expect(context.statusRegion.attributes.get("data-tone")).toBe("yellow");
  });

  it("shows conflict actions only during a true conflict", async () => {
    const cleanContext = createSaveControllerContext([]);

    expect(cleanContext.noticeRegion.hidden).toBe(true);
    expect(cleanContext.noticeActions.hidden).toBe(true);
    expect(cleanContext.reloadButton.hidden).toBe(true);
    expect(cleanContext.copyLocalButton.hidden).toBe(true);

    const staleContext = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Updated elsewhere",
          body_json: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [{ type: "text", text: "Server body" }],
              },
            ],
          },
          editor_schema_version: 1,
          version: 3,
          modified_at: "2026-06-29T12:10:00Z",
        },
      }),
    ]);
    staleContext.titleInput.value = "Local draft";
    staleContext.editor.json = {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Mine" }] },
      ],
    };
    staleContext.controller.handleDocumentEdited();
    staleContext.windowEvents.triggerFocus();
    await flushAsyncWork();

    expect(staleContext.controller.getState()).toBe("dirty");
    expect(staleContext.noticeRegion.hidden).toBe(false);
    expect(staleContext.noticeActions.hidden).toBe(true);
    expect(staleContext.reloadButton.hidden).toBe(true);
    expect(staleContext.copyLocalButton.hidden).toBe(true);

    const saveRequest = createDeferredFetch();
    const savingContext = createSaveControllerContext([saveRequest]);
    savingContext.titleInput.value = "Saving now";
    savingContext.controller.handleDocumentEdited();
    const savingPromise = savingContext.controller.flushPendingSave();
    await flushAsyncWork();

    expect(savingContext.controller.getState()).toBe("saving");
    expect(savingContext.noticeRegion.hidden).toBe(true);
    expect(savingContext.noticeActions.hidden).toBe(true);
    expect(savingContext.reloadButton.hidden).toBe(true);
    expect(savingContext.copyLocalButton.hidden).toBe(true);

    saveRequest.resolve(
      createResponse(200, {
        ok: true,
        title: "Saving now",
        version: 2,
        modified_at: "2026-06-29T12:15:00Z",
      }),
    );
    await savingPromise;
    await flushAsyncWork();

    const failedContext = createSaveControllerContext([
      createResponse(400, {
        ok: false,
        errors: {
          title: [{ code: "required", message: "Title is required." }],
        },
      }),
    ]);
    failedContext.titleInput.value = "";
    await failedContext.controller.flushPendingSave();
    await flushAsyncWork();

    expect(failedContext.controller.getState()).toBe("failed");
    expect(failedContext.noticeRegion.hidden).toBe(false);
    expect(failedContext.noticeActions.hidden).toBe(true);
    expect(failedContext.reloadButton.hidden).toBe(true);
    expect(failedContext.copyLocalButton.hidden).toBe(true);

    const conflictContext = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);
    await conflictContext.controller.flushPendingSave();
    await flushAsyncWork();

    expect(conflictContext.controller.getState()).toBe("conflicted");
    expect(conflictContext.noticeRegion.hidden).toBe(false);
    expect(conflictContext.noticeActions.hidden).toBe(false);
    expect(conflictContext.reloadButton.hidden).toBe(false);
    expect(conflictContext.copyLocalButton.hidden).toBe(false);
  });

  it("does not overwrite dirty local content when a newer server version is detected", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Updated elsewhere",
          body_json: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [{ type: "text", text: "Server body" }],
              },
            ],
          },
          editor_schema_version: 1,
          version: 3,
          modified_at: "2026-06-29T12:10:00Z",
        },
      }),
    ]);

    context.titleInput.value = "Local draft";
    context.editor.json = {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Mine" }] },
      ],
    };
    context.controller.handleDocumentEdited();

    context.windowEvents.triggerFocus();
    await flushAsyncWork();

    expect(context.titleInput.value).toBe("Local draft");
    expect(context.editor.json).toEqual({
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Mine" }] },
      ],
    });
    expect(context.noticeRegion.hidden).toBe(false);
    expect(context.noticeText.textContent).toContain(
      "newer version of this note exists",
    );
    expect(context.statusRegion.attributes.get("data-tone")).toBe("red");
  });

  it("does not apply a freshness response if the user starts typing while the request is in flight", async () => {
    const freshnessRequest = createDeferredFetch();
    const context = createSaveControllerContext([freshnessRequest]);

    context.windowEvents.triggerFocus();
    await flushAsyncWork();

    context.titleInput.value = "Local edit";
    context.editor.json = {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Mine" }] },
      ],
    };
    context.controller.handleDocumentEdited();

    freshnessRequest.resolve(
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Server title",
          body_json: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [{ type: "text", text: "Server body" }],
              },
            ],
          },
          editor_schema_version: 1,
          version: 2,
          modified_at: "2026-06-29T12:10:00Z",
        },
      }),
    );
    await flushAsyncWork();

    expect(context.titleInput.value).toBe("Local edit");
    expect(context.editor.json).toEqual({
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Mine" }] },
      ],
    });
    expect(context.noticeText.textContent).toContain("local work is preserved");
  });

  it("coalesces focus, visibility, and periodic freshness triggers while a check is in flight", async () => {
    const freshnessRequest = createDeferredFetch();
    const context = createSaveControllerContext([freshnessRequest]);

    context.windowEvents.triggerFocus();
    context.documentEvents.triggerVisibility("visible");
    context.scheduler.runDelay(FRESHNESS_CHECK_INTERVAL_MS);
    await flushAsyncWork();

    expect(context.fetchCalls).toHaveLength(1);
    expect(context.fetchCalls[0].method).toBe("GET");

    freshnessRequest.resolve(
      createResponse(200, {
        ok: true,
        has_update: false,
        version: 1,
        modified_at: "2026-06-29T12:10:00Z",
      }),
    );
    await flushAsyncWork();
  });

  it("runs periodic freshness checks while the page stays active", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        has_update: false,
        version: 1,
        modified_at: "2026-06-29T12:10:00Z",
      }),
    ]);

    context.scheduler.runDelay(FRESHNESS_CHECK_INTERVAL_MS);
    await flushAsyncWork();

    expect(context.fetchCalls).toHaveLength(1);
    expect(context.fetchCalls[0].input).toBe("/notes/1/freshness/?version=1");
  });

  it("shows a terminal conflict state with read-only local content and disabled controls", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    context.titleInput.value = "My local note";
    context.editor.json = {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Local" }] },
      ],
    };

    await context.controller.flushPendingSave();
    await flushAsyncWork();

    expect(context.controller.getState()).toBe("conflicted");
    expect(context.titleInput.readOnly).toBe(true);
    expect(context.editor.editable).toBe(false);
    expect(context.saveButton.disabled).toBe(true);
    expect(context.toolbarToggle.disabled).toBe(true);
    expect(context.toolbar.hidden).toBe(true);
    expect(context.reloadButton.hidden).toBe(false);
    expect(context.copyLocalButton.hidden).toBe(false);
    expect(context.noticeText.textContent).toContain("saved a newer version");
    expect(context.statusRegion.attributes.get("data-tone")).toBe("red");
  });

  it("reload latest bypasses the unload warning during conflict recovery", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    await context.controller.flushPendingSave();
    await flushAsyncWork();

    context.reloadButton.triggerClick();
    const event = {
      preventDefault: vi.fn(),
      returnValue: "",
    };
    context.windowEvents.beforeUnloadListener?.(event);

    expect(context.locationAssign).toHaveBeenCalledWith("/notes/1/");
    expect(event.preventDefault).not.toHaveBeenCalled();
  });

  it("clears conflict recovery controls immediately when reload latest is chosen", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    await context.controller.flushPendingSave();
    await flushAsyncWork();

    context.reloadButton.triggerClick();

    expect(context.noticeRegion.hidden).toBe(true);
    expect(context.noticeActions.hidden).toBe(true);
    expect(context.reloadButton.hidden).toBe(true);
    expect(context.copyLocalButton.hidden).toBe(true);
    expect(context.form.attributes.get("data-note-conflicted")).toBe("false");
  });

  it("shows temporary Copied feedback after a successful modern clipboard copy", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);
    const navigatorLike: NavigatorLike = {
      clipboard: {
        writeText: vi.fn(() => Promise.resolve()),
      },
    };
    const documentLike: CopyDocumentLike = {
      body: { appendChild: vi.fn(), removeChild: vi.fn() },
      createElement: vi.fn(),
      execCommand: vi.fn(),
    };

    context.copyTextFn.mockImplementationOnce((text: string) =>
      copyTextWithFallback(text, { documentLike, navigatorLike }),
    );

    await context.controller.flushPendingSave();
    await flushAsyncWork();
    context.copyLocalButton.triggerClick();
    await flushAsyncWork();

    expect(context.copyLocalButton.textContent).toBe("Copied");
    context.scheduler.runDelay(2000);
    expect(context.copyLocalButton.textContent).toBe("Copy local draft");
  });

  it("keeps the copy button width state stable while Copied is shown", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    await context.controller.flushPendingSave();
    await flushAsyncWork();

    expect(context.copyLocalButton.attributes.get("data-copy-state")).toBe(
      "ready",
    );
    context.copyLocalButton.triggerClick();
    await flushAsyncWork();

    expect(context.copyLocalButton.textContent).toBe("Copied");
    expect(context.copyLocalButton.attributes.get("data-copy-state")).toBe(
      "copied",
    );

    context.scheduler.runDelay(2000);

    expect(context.copyLocalButton.textContent).toBe("Copy local draft");
    expect(context.copyLocalButton.attributes.get("data-copy-state")).toBe(
      "ready",
    );
  });

  it("shows temporary Copied feedback after a successful fallback copy", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);
    const textarea = {
      focus: vi.fn(),
      select: vi.fn(),
      setAttribute: vi.fn(),
      style: { left: "", opacity: "", position: "", top: "" },
      value: "",
    };
    const documentLike: CopyDocumentLike = {
      body: {
        appendChild: vi.fn(),
        removeChild: vi.fn(),
      },
      createElement: vi.fn(() => textarea),
      execCommand: vi.fn(() => true),
    };

    context.copyTextFn.mockImplementationOnce((text: string) =>
      copyTextWithFallback(text, { documentLike, navigatorLike: {} }),
    );

    await context.controller.flushPendingSave();
    await flushAsyncWork();
    context.copyLocalButton.triggerClick();
    await flushAsyncWork();

    expect(context.copyLocalButton.textContent).toBe("Copied");
    context.scheduler.runDelay(2000);
    expect(context.copyLocalButton.textContent).toBe("Copy local draft");
  });

  it("shows a fallback notice when copy local text fails", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    context.copyTextFn.mockRejectedValueOnce(
      new Error("Clipboard unavailable"),
    );

    await context.controller.flushPendingSave();
    await flushAsyncWork();
    context.copyLocalButton.triggerClick();
    await flushAsyncWork();

    expect(context.copyLocalButton.textContent).toBe("Copy local draft");
    expect(context.noticeText.textContent).toBe(
      "Copy local draft failed. Select and copy the preserved draft manually.",
    );
    expect(context.reloadButton.hidden).toBe(false);
    expect(context.copyLocalButton.hidden).toBe(false);
  });

  it("shows a fallback notice when copy local text reports false", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    context.copyTextFn.mockResolvedValueOnce(false);

    await context.controller.flushPendingSave();
    await flushAsyncWork();
    context.copyLocalButton.triggerClick();
    await flushAsyncWork();

    expect(context.copyLocalButton.textContent).toBe("Copy local draft");
    expect(context.noticeText.textContent).toBe(
      "Copy local draft failed. Select and copy the preserved draft manually.",
    );
  });

  it("replaces any existing copy-success timer when clicked again", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    await context.controller.flushPendingSave();
    await flushAsyncWork();
    context.copyLocalButton.triggerClick();
    await flushAsyncWork();
    expect(context.scheduler.countDelay(2000)).toBe(1);

    context.copyLocalButton.triggerClick();
    await flushAsyncWork();

    expect(context.copyLocalButton.textContent).toBe("Copied");
    expect(context.scheduler.countDelay(2000)).toBe(1);
    context.scheduler.runDelay(2000);
    expect(context.copyLocalButton.textContent).toBe("Copy local draft");
  });

  it("restores the normal copy label when conflict state ends", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    await context.controller.flushPendingSave();
    await flushAsyncWork();
    context.copyLocalButton.triggerClick();
    await flushAsyncWork();

    expect(context.copyLocalButton.textContent).toBe("Copied");

    (
      context.controller as unknown as {
        applyRemoteState(note: {
          body_json: unknown;
          editor_schema_version: number;
          modified_at: string;
          title: string;
          version: number;
        }): void;
      }
    ).applyRemoteState({
      body_json: { type: "doc", content: [] },
      editor_schema_version: 1,
      modified_at: "2026-06-29T12:01:00Z",
      title: "Latest title",
      version: 5,
    });

    expect(context.copyLocalButton.textContent).toBe("Copy local draft");
  });

  it("can copy local text during conflict recovery", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    context.titleInput.value = "Local title";
    context.editor.json = {
      type: "doc",
      content: [
        {
          type: "paragraph",
          content: [
            { type: "text", text: "Line one" },
            { type: "hardBreak" },
            { type: "text", text: "Line two" },
          ],
        },
      ],
    };

    await context.controller.flushPendingSave();
    await flushAsyncWork();
    context.copyLocalButton.triggerClick();
    await flushAsyncWork();

    expect(context.copyTextFn).toHaveBeenCalledWith(
      "Local title\n\nLine one\nLine two",
    );
  });

  it("keeps the editor editable during a remote-newer warning and only enters conflict after a rejected save", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Updated elsewhere",
          body_json: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [{ type: "text", text: "Server body" }],
              },
            ],
          },
          editor_schema_version: 1,
          version: 3,
          modified_at: "2026-06-29T12:10:00Z",
        },
      }),
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 3,
        current_modified_at: "2026-06-29T12:11:00Z",
      }),
    ]);

    context.titleInput.value = "Local draft";
    context.editor.json = {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Mine" }] },
      ],
    };
    context.controller.handleDocumentEdited();
    context.windowEvents.triggerFocus();
    await flushAsyncWork();

    expect(context.controller.getState()).toBe("dirty");
    expect(context.titleInput.readOnly).toBe(false);
    expect(context.editor.editable).toBe(true);
    expect(context.reloadButton.hidden).toBe(true);
    expect(context.copyLocalButton.hidden).toBe(true);

    await context.controller.flushPendingSave();
    await flushAsyncWork();

    expect(context.controller.getState()).toBe("conflicted");
    expect(context.titleInput.readOnly).toBe(true);
    expect(context.editor.editable).toBe(false);
    expect(context.titleInput.value).toBe("Local draft");
    expect(context.editor.json).toEqual({
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Mine" }] },
      ],
    });
    expect(context.reloadButton.hidden).toBe(false);
    expect(context.copyLocalButton.hidden).toBe(false);
  });

  it("opens and navigates the tab in one synchronous step when already saved and current", async () => {
    const context = createSaveControllerContext([]);

    await context.controller.handlePrintRequested();

    expect(context.openBlankTab).toHaveBeenCalledTimes(1);
    expect(context.printTabs[0].assignedUrl).toBe("/notes/1/print/");
    expect(context.printTabs[0].closed).toBe(false);
    expect(context.locationAssign).not.toHaveBeenCalled();
  });

  it("opens the blank tab synchronously, before the triggered save resolves", async () => {
    const saveRequest = createDeferredFetch();
    const context = createSaveControllerContext([saveRequest]);

    context.titleInput.value = "Unsaved";
    context.controller.handleDocumentEdited();
    const requestPromise = context.controller.handlePrintRequested();

    // The tab must exist before the save's fetch has resolved -- opened
    // as a direct, synchronous response to the click, not after some
    // later microtask, so browsers don't treat it as a blocked popup.
    await flushAsyncWork();
    expect(context.openBlankTab).toHaveBeenCalledTimes(1);
    expect(context.printTabs[0].assignedUrl).toBeNull();
    expect(context.printTabs[0].closed).toBe(false);

    saveRequest.resolve(
      createResponse(200, {
        ok: true,
        title: "Unsaved",
        version: 2,
        modified_at: "2026-06-29T12:00:00Z",
      }),
    );
    await requestPromise;

    expect(context.printTabs[0].assignedUrl).toBe("/notes/1/print/");
  });

  it("saves the note first, then navigates the already-open tab, when the note is dirty", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        title: "Unsaved",
        version: 2,
        modified_at: "2026-06-29T12:00:00Z",
      }),
    ]);

    context.titleInput.value = "Unsaved";
    context.controller.handleDocumentEdited();
    await context.controller.handlePrintRequested();

    expect(context.fetchCalls).toHaveLength(1);
    expect(context.openBlankTab).toHaveBeenCalledTimes(1);
    expect(context.printTabs[0].assignedUrl).toBe("/notes/1/print/");
    expect(context.printTabs[0].closed).toBe(false);
    expect(context.locationAssign).not.toHaveBeenCalled();
  });

  it("closes the waiting tab and blocks print when the triggered save fails", async () => {
    const context = createSaveControllerContext([
      createResponse(400, {
        ok: false,
        errors: {
          title: [{ code: "invalid", message: "Title is invalid" }],
        },
      }),
    ]);

    context.titleInput.value = "Unsaved";
    context.controller.handleDocumentEdited();
    await context.controller.handlePrintRequested();

    expect(context.controller.getState()).toBe("failed");
    expect(context.printTabs[0].assignedUrl).toBeNull();
    expect(context.printTabs[0].closed).toBe(true);
    expect(context.locationAssign).not.toHaveBeenCalled();
    expect(context.noticeText.textContent).toBe(
      "Printing is available only when this note is saved and current.",
    );
  });

  it("blocks print without opening a tab during a true conflict", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    await context.controller.flushPendingSave();
    await flushAsyncWork();
    expect(context.controller.getState()).toBe("conflicted");

    await context.controller.handlePrintRequested();

    expect(context.openBlankTab).not.toHaveBeenCalled();
    expect(context.locationAssign).not.toHaveBeenCalled();
    expect(context.noticeText.textContent).toBe(
      "Printing is available only when this note is saved and current.",
    );
    expect(context.reloadButton.hidden).toBe(false);
    expect(context.copyLocalButton.hidden).toBe(false);
  });

  it("blocks print while a newer remote version warning is active", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Updated elsewhere",
          body_json: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [{ type: "text", text: "Server body" }],
              },
            ],
          },
          editor_schema_version: 1,
          version: 3,
          modified_at: "2026-06-29T12:10:00Z",
        },
      }),
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 3,
        current_modified_at: "2026-06-29T12:10:00Z",
      }),
    ]);

    context.titleInput.value = "Local draft";
    context.editor.json = {
      type: "doc",
      content: [
        { type: "paragraph", content: [{ type: "text", text: "Mine" }] },
      ],
    };
    context.controller.handleDocumentEdited();
    context.windowEvents.triggerFocus();
    await flushAsyncWork();
    await context.controller.handlePrintRequested();

    expect(context.locationAssign).not.toHaveBeenCalled();
    expect(context.printTabs[0].assignedUrl).toBeNull();
    expect(context.printTabs[0].closed).toBe(true);
    expect(context.noticeText.textContent).toBe(
      "Printing is available only when this note is saved and current.",
    );
  });

  it("allows print only when the note is saved and current", async () => {
    const context = createSaveControllerContext([]);

    await context.controller.handlePrintRequested();

    expect(context.printTabs[0].assignedUrl).toBe("/notes/1/print/");
  });

  it("blocks export JSON while a newer remote version warning is active", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Updated elsewhere",
          body_json: {
            type: "doc",
            content: [
              {
                type: "paragraph",
                content: [{ type: "text", text: "Server body" }],
              },
            ],
          },
          editor_schema_version: 1,
          version: 3,
          modified_at: "2026-06-29T12:10:00Z",
        },
      }),
    ]);

    context.titleInput.value = "Local draft";
    context.controller.handleDocumentEdited();
    context.windowEvents.triggerFocus();
    await flushAsyncWork();

    expect(context.exportLink.triggerClick()).toBe(true);
    expect(context.noticeText.textContent).toBe(
      "Export JSON is unavailable until this note is saved and current.",
    );
  });

  it("blocks export JSON during true conflict and keeps recovery controls visible", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-06-29T12:00:05Z",
      }),
    ]);

    await context.controller.flushPendingSave();
    await flushAsyncWork();

    expect(context.exportLink.triggerClick()).toBe(true);
    expect(context.noticeText.textContent).toBe(
      "Export JSON is unavailable while this note is in conflict. Copy local draft or reload latest first.",
    );
    expect(context.reloadButton.hidden).toBe(false);
    expect(context.copyLocalButton.hidden).toBe(false);
  });

  it("retries the save when print is requested from an already-failed state, and blocks again if that retry also fails", async () => {
    const brokenResponse = () =>
      createResponse(400, {
        ok: false,
        errors: {
          title: [{ code: "invalid", message: "Title is invalid" }],
        },
      });
    const context = createSaveControllerContext([
      brokenResponse(),
      brokenResponse(),
    ]);

    context.titleInput.value = "Broken note";
    await context.controller.flushPendingSave();
    await flushAsyncWork();

    expect(context.controller.getState()).toBe("failed");
    await context.controller.handlePrintRequested();

    expect(context.locationAssign).not.toHaveBeenCalled();
    // The first fetch is the setup `flushPendingSave()` above; the
    // second is print's own retry attempt -- not print silently
    // giving up without ever trying.
    expect(context.fetchCalls).toHaveLength(2);
    expect(context.printTabs[0].assignedUrl).toBeNull();
    expect(context.printTabs[0].closed).toBe(true);
    expect(context.noticeText.textContent).toBe(
      "Printing is available only when this note is saved and current.",
    );
  });

  it("warns before unrelated unsafe navigation through workspace links", () => {
    const context = createSaveControllerContext([]);

    context.titleInput.value = "Unsaved";
    context.controller.handleDocumentEdited();

    expect(context.navLink.triggerClick()).toBe(true);
    expect(context.confirmFn).toHaveBeenCalledTimes(1);
    expect(context.exportLink.triggerClick()).toBe(true);
    expect(context.confirmFn).toHaveBeenCalledTimes(2);
  });

  it("registers beforeunload protection while the controller is active", () => {
    const context = createSaveControllerContext([]);
    const event = {
      preventDefault: vi.fn(),
      returnValue: "",
    };

    context.windowEvents.beforeUnloadListener?.(event);
    expect(event.preventDefault).not.toHaveBeenCalled();

    context.titleInput.value = "Unsaved";
    context.controller.handleDocumentEdited();
    context.windowEvents.beforeUnloadListener?.(event);
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
  });
});

describe("save-state presentation kind (data-status-kind)", () => {
  it("starts as saved when the note is clean and current", () => {
    const context = createSaveControllerContext([]);
    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "saved",
    );
  });

  it("becomes dirty after an edit", () => {
    const context = createSaveControllerContext([]);
    context.controller.handleDocumentEdited();
    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "dirty",
    );
  });

  it("becomes saving while a save is in flight with no new edits", async () => {
    const saveRequest = createDeferredFetch();
    const context = createSaveControllerContext([saveRequest]);
    context.titleInput.value = "In flight";
    context.controller.handleDocumentEdited();
    void context.controller.flushPendingSave();
    await flushAsyncWork();
    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "saving",
    );
    saveRequest.resolve(
      createResponse(200, {
        ok: true,
        title: "In flight",
        version: 2,
        modified_at: "2026-07-01T10:00:00Z",
      }),
    );
    await flushAsyncWork();
  });

  it("shows dirty kind when new edits arrive while a save is in flight", async () => {
    const saveRequest = createDeferredFetch();
    const context = createSaveControllerContext([saveRequest]);
    context.titleInput.value = "First";
    context.controller.handleDocumentEdited();
    void context.controller.flushPendingSave();
    await flushAsyncWork();

    context.titleInput.value = "Second";
    context.controller.handleDocumentEdited();

    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "dirty",
    );
    saveRequest.resolve(
      createResponse(200, {
        ok: true,
        title: "First",
        version: 2,
        modified_at: "2026-07-01T10:00:00Z",
      }),
    );
    await flushAsyncWork();
  });

  it("becomes retry when a save fails with a retry scheduled", async () => {
    const context = createSaveControllerContext([
      createResponse(500, { ok: false }),
    ]);
    context.controller.handleDocumentEdited();
    context.scheduler.runDelay(AUTOSAVE_DEBOUNCE_MS);
    await flushAsyncWork();
    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "retry",
    );
  });

  it("becomes failed after a non-retriable save failure", async () => {
    const context = createSaveControllerContext([
      createResponse(400, {
        ok: false,
        errors: {
          title: [{ code: "required", message: "Title is required." }],
        },
      }),
    ]);
    context.titleInput.value = "";
    await context.controller.flushPendingSave();
    await flushAsyncWork();
    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "failed",
    );
  });

  it("becomes conflicted after a true conflict response", async () => {
    const context = createSaveControllerContext([
      createResponse(409, {
        ok: false,
        error: "conflict",
        current_version: 4,
        current_modified_at: "2026-07-01T10:00:00Z",
      }),
    ]);
    await context.controller.flushPendingSave();
    await flushAsyncWork();
    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "conflicted",
    );
  });

  it("shows remote-newer kind when a newer remote version is detected while dirty", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        has_update: true,
        note: {
          title: "Updated elsewhere",
          body_json: { type: "doc", content: [] },
          editor_schema_version: 1,
          version: 3,
          modified_at: "2026-07-01T10:00:00Z",
        },
      }),
    ]);
    context.titleInput.value = "Local draft";
    context.controller.handleDocumentEdited();
    context.windowEvents.triggerFocus();
    await flushAsyncWork();
    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "remote-newer",
    );
  });

  it("returns to saved after a successful autosave resolves a dirty state", async () => {
    const context = createSaveControllerContext([
      createResponse(200, {
        ok: true,
        title: "Recovered",
        version: 2,
        modified_at: "2026-07-01T10:00:00Z",
      }),
    ]);
    context.controller.handleDocumentEdited();
    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "dirty",
    );
    context.scheduler.runDelay(AUTOSAVE_DEBOUNCE_MS);
    await flushAsyncWork();
    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "saved",
    );
  });

  it("shows saved-unconfirmed with Saved label and yellow tone when freshness is unknown while clean", async () => {
    const freshnessRequest = createDeferredFetch();
    const context = createSaveControllerContext([freshnessRequest]);

    context.windowEvents.triggerFocus();
    await flushAsyncWork();

    freshnessRequest.reject(new Error("network error"));
    await flushAsyncWork();

    expect(context.controller.getState()).toBe("clean");
    expect(context.statusRegion.textContent).toBe("Saved");
    expect(context.statusRegion.attributes.get("data-tone")).toBe("yellow");
    expect(context.statusRegion.attributes.get("data-status-kind")).toBe(
      "saved-unconfirmed",
    );
  });
});
