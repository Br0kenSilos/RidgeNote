import { Editor, Extension } from "@tiptap/core";
import Link from "@tiptap/extension-link";
import Underline from "@tiptap/extension-underline";
import type { Node as ProseMirrorNode, ResolvedPos } from "@tiptap/pm/model";
import type { EditorState, Transaction } from "@tiptap/pm/state";
import { Plugin, PluginKey, TextSelection } from "@tiptap/pm/state";
import StarterKit from "@tiptap/starter-kit";
import { parseNoteIdFromDetailUrl, syncTreeNoteTitle } from "./tree-title-sync";

export const SUPPORTED_EDITOR_SCHEMA_VERSION = 1;
export const AUTOSAVE_DEBOUNCE_MS = 1200;
export const MAX_PENDING_SAVE_MS = 10000;
export const RETRY_DELAY_MS = 5000;
export const FRESHNESS_CHECK_INTERVAL_MS = 25000;

const LINK_REL = "noopener noreferrer nofollow";
const LINK_TARGET = "_blank";
const FORMATTING_TOGGLE_LABEL_SHOW = "Show formatting";
const FORMATTING_TOGGLE_LABEL_HIDE = "Hide formatting";
const UNSAFE_NAVIGATION_MESSAGE =
  "You have unsaved or unresolved note changes. Leave this page anyway?";
const CONFLICT_NOTICE =
  "Another tab or device saved a newer version of this note. Your local changes are preserved here in read-only mode until you reload the latest version.";
const REMOTE_NEWER_NOTICE =
  "A newer version of this note exists on another tab or device. Your local work is preserved here and will not be overwritten automatically.";
const SESSION_EXPIRED_NOTICE =
  "Your session expired or is no longer valid. Sign in again before saving this note.";
const SAVE_FAILED_NOTICE = "Save failed. RidgeNote will retry once shortly.";
const SAVE_FAILED_PERSISTENT_NOTICE =
  "Save failed. Keep this tab open and use Save after the problem is resolved.";
const PRINT_BLOCKED_NOTICE =
  "Printing is available only when this note is saved and current.";
const EXPORT_BLOCKED_NOTICE =
  "Export JSON is unavailable until this note is saved and current.";
const COPY_LOCAL_FAILED_NOTICE =
  "Copy local draft failed. Select and copy the preserved draft manually.";
const COPY_LOCAL_BUTTON_LABEL = "Copy local draft";
const COPY_LOCAL_COPIED_LABEL = "Copied";
const COPY_LOCAL_FEEDBACK_MS = 2000;
const UNSUPPORTED_SCHEMA_NOTICE =
  "This note is no longer editable in this RidgeNote version. Reload the latest page state.";
const UPDATED_FROM_ANOTHER_SESSION_DETAIL = "Updated from another session";

type StatusTone = "green" | "yellow" | "red";
type FreshnessState = "current" | "unknown" | "remote_newer";

export type ToolbarCommand =
  | "bold"
  | "italic"
  | "underline"
  | "heading"
  | "bulletList"
  | "orderedList"
  | "link"
  | "clearFormatting"
  | "undo"
  | "redo";

export type NoteSaveState =
  | "clean"
  | "dirty"
  | "saving"
  | "dirty_during_save"
  | "failed"
  | "conflicted";

export interface ToolbarAction {
  command: ToolbarCommand;
  level?: number;
}

export interface EditorChain {
  focus(): EditorChain;
  setTextSelection(position: number): EditorChain;
  toggleBold(): EditorChain;
  toggleItalic(): EditorChain;
  toggleUnderline(): EditorChain;
  toggleHeading(attrs: { level: number }): EditorChain;
  toggleBulletList(): EditorChain;
  toggleOrderedList(): EditorChain;
  extendMarkRange(mark: string): EditorChain;
  setLink(attrs: { href: string }): EditorChain;
  unsetLink(): EditorChain;
  unsetAllMarks(): EditorChain;
  clearNodes(): EditorChain;
  undo(): EditorChain;
  redo(): EditorChain;
  run(): boolean;
}

export interface EditorLike {
  chain(): EditorChain;
  destroy(): void;
  getJSON(): unknown;
  setEditable(editable: boolean): void;
}

export interface NewNoteBodyFocusOptions {
  isImmediateNewPage: boolean;
  navigationType?: string;
}

export interface NewNoteFocusOptions extends NewNoteBodyFocusOptions {
  generatedTitle: string;
  titleValue: string;
  hasSelectedTitle: boolean;
}

export interface TitleInputLike {
  addEventListener(type: "focus" | "input", listener: () => void): void;
  select(): void;
  value: string;
  readOnly: boolean;
}

export interface ToolbarToggleButtonLike {
  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void;
  setAttribute(name: string, value: string): void;
  textContent: string | null;
  disabled?: boolean;
}

export interface ToolbarRegionLike {
  hidden: boolean;
}

export interface SaveButtonLike {
  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void;
  disabled: boolean;
}

export interface NoticeRegionLike {
  hidden: boolean;
  setAttribute(name: string, value: string): void;
}

export interface NoticeTextLike {
  textContent: string | null;
}

export interface NoticeActionsLike {
  hidden: boolean;
}

export interface StatusRegionLike {
  textContent: string | null;
  setAttribute(name: string, value: string): void;
}

export interface ActionLinkLike {
  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void;
  href: string;
  setAttribute(name: string, value: string): void;
}

export interface ReloadButtonLike {
  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void;
  hidden: boolean;
}

export interface CopyButtonLike {
  addEventListener(
    type: "click",
    listener: (event: { preventDefault(): void }) => void,
  ): void;
  hidden: boolean;
  disabled?: boolean;
  textContent: string | null;
  setAttribute(name: string, value: string): void;
}

export interface HiddenInputLike {
  value: string;
}

export interface FormLike {
  addEventListener(
    type: "submit",
    listener: (event: { preventDefault(): void }) => void,
  ): void;
  setAttribute(name: string, value: string): void;
}

export interface NavGuardTargetLike {
  addEventListener(
    type: "click" | "submit",
    listener: (event: { preventDefault(): void }) => void,
  ): void;
}

export interface BeforeUnloadEventLike {
  preventDefault(): void;
  returnValue: string;
}

export interface SchedulerLike {
  clearTimeout(handle: number): void;
  setTimeout(callback: () => void, delayMs: number): number;
}

export interface FetchResponseLike {
  json(): Promise<AutosaveResponse | FreshnessResponse>;
  status: number;
}

export interface SaveSnapshot {
  bodyJson: unknown;
  title: string;
}

/** See `NoteSaveController.flushPendingSave()`'s own comment. */
export interface FlushPendingSaveOptions {
  suppressIdleActivity?: boolean;
}

/** Header added to the autosave request only when
 * `suppressIdleActivity` is set -- checked (not enforced) by the
 * backend in `accounts.services.should_refresh_session_activity`. */
export const IDLE_WARNING_SAVE_HEADER = "X-RidgeNote-Session-Idle-Save";

export interface SaveSuccessResponse {
  modified_at: string;
  ok: true;
  title: string;
  version: number;
}

export interface SaveFailureResponse {
  current_modified_at?: string;
  current_version?: number;
  error?: string;
  errors?: Record<string, Array<{ code: string; message: string }>>;
  ok: false;
}

export interface RemoteNoteState {
  body_json: unknown;
  editor_schema_version: number;
  modified_at: string;
  title: string;
  version: number;
}

export interface FreshnessCurrentResponse {
  has_update: false;
  modified_at: string;
  ok: true;
  version: number;
}

export interface FreshnessUpdatedResponse {
  has_update: true;
  note: RemoteNoteState;
  ok: true;
}

export interface FreshnessFailureResponse {
  error?: string;
  errors?: Record<string, Array<{ code: string; message: string }>>;
  ok: false;
}

export type AutosaveResponse = SaveFailureResponse | SaveSuccessResponse;
export type FreshnessResponse =
  | FreshnessCurrentResponse
  | FreshnessUpdatedResponse
  | FreshnessFailureResponse;

type PromptFn = (message: string, defaultValue?: string) => string | null;
type FetchFn = (
  input: string,
  init: {
    body?: string;
    headers: Record<string, string>;
    method: "GET" | "POST";
  },
) => Promise<FetchResponseLike>;
type ConfirmFn = (message: string) => boolean;
type CopyTextFn = (text: string) => Promise<boolean> | boolean;
type ReplaceEditorDocumentFn = (bodyJson: unknown) => void;
type LocationAssignFn = (url: string) => void;
export interface PrintTabLike {
  location: { assign: (url: string) => void };
  close: () => void;
}
type OpenBlankTabFn = () => PrintTabLike | null;
type BeforeUnloadListener = (event: BeforeUnloadEventLike) => void;
type WindowEventsLike = {
  addEventListener(
    type: "beforeunload" | "focus",
    listener: BeforeUnloadListener | (() => void),
  ): void;
};
type DocumentEventsLike = {
  addEventListener(type: "visibilitychange", listener: () => void): void;
  visibilityState?: string;
};

export interface ClipboardWriterLike {
  writeText(text: string): Promise<void>;
}

export interface NavigatorLike {
  clipboard?: ClipboardWriterLike;
}

export interface TemporaryTextAreaLike {
  focus(): void;
  select(): void;
  setAttribute(name: string, value: string): void;
  style: {
    left: string;
    opacity: string;
    position: string;
    top: string;
  };
  value: string;
}

export interface CopyDocumentLike {
  body?: {
    appendChild(node: TemporaryTextAreaLike): void;
    removeChild(node: TemporaryTextAreaLike): void;
  };
  createElement(tagName: "textarea"): TemporaryTextAreaLike;
  execCommand?(command: string): boolean;
}

export interface NoteSaveControllerOptions {
  autosaveUrl: string;
  copyLocalButton: CopyButtonLike;
  copyTextFn?: CopyTextFn;
  csrfToken: string;
  detailUrl: string;
  documentEvents?: DocumentEventsLike;
  editor: EditorLike;
  exportLinks: ActionLinkLike[];
  fetchFn: FetchFn;
  form: FormLike;
  freshnessUrl: string;
  hiddenBodyInput: HiddenInputLike;
  locationAssign: LocationAssignFn;
  navigationGuardTargets: NavGuardTargetLike[];
  noticeActions: NoticeActionsLike;
  noticeRegion: NoticeRegionLike;
  noticeText: NoticeTextLike;
  onTitleSaved?: (title: string) => void;
  openBlankTab: OpenBlankTabFn;
  printLinks: ActionLinkLike[];
  printUrl: string;
  reloadButton: ReloadButtonLike;
  replaceEditorDocument: ReplaceEditorDocumentFn;
  saveButton: SaveButtonLike;
  scheduler: SchedulerLike;
  statusRegion: StatusRegionLike;
  titleInput: TitleInputLike;
  toolbar: ToolbarRegionLike;
  toolbarToggle: ToolbarToggleButtonLike;
  versionInput: HiddenInputLike;
  windowEvents?: WindowEventsLike;
  confirmFn?: ConfirmFn;
}

export function canInitializeNoteEditor(schemaVersion: number): boolean {
  return schemaVersion === SUPPORTED_EDITOR_SCHEMA_VERSION;
}

/**
 * Syntactic-only URL gate for autolink candidates (paste rules, autolink
 * plugin). linkifyjs's bracket-balancing scanner can produce a "URL" whose
 * text is not actually a valid absolute URL (e.g. from pasted
 * `[http://x](http://x)`-shaped plain text); rejecting those before a mark
 * is created is cheaper and safer than trapping them after the fact.
 */
function isSyntacticallyValidUrl(url: string): boolean {
  try {
    new URL(url);
    return true;
  } catch {
    return false;
  }
}

export function normalizeAllowedLinkHref(rawHref: string): string | null {
  const href = rawHref.trim();
  if (!href || href.startsWith("//")) {
    return null;
  }

  try {
    const parsed = new URL(href);
    if (!["http:", "https:", "mailto:"].includes(parsed.protocol)) {
      return null;
    }
    if (
      (parsed.protocol === "http:" || parsed.protocol === "https:") &&
      !parsed.hostname
    ) {
      return null;
    }
    if (parsed.protocol === "mailto:" && !parsed.pathname) {
      return null;
    }
    return href;
  } catch {
    return null;
  }
}

export function buildEditorExtensions() {
  return [
    StarterKit.configure({
      blockquote: false,
      code: false,
      codeBlock: false,
      dropcursor: false,
      gapcursor: false,
      hardBreak: {},
      horizontalRule: false,
      strike: false,
      heading: { levels: [1, 2, 3] },
      link: false,
      underline: false,
    }),
    PlainTextTab,
    LinkOpenGesture,
    Underline,
    Link.configure({
      autolink: false,
      linkOnPaste: false,
      openOnClick: false,
      isAllowedUri: (url, context) =>
        context.defaultValidate(url) && isSyntacticallyValidUrl(url),
      HTMLAttributes: {
        rel: LINK_REL,
        target: LINK_TARGET,
      },
    }),
  ];
}

const PLAIN_TEXT_TAB_SPACES = "  ";
const PLAIN_TEXT_TAB_MAX_SPACES = 2;

export interface PlainTextBlockRange {
  node: ProseMirrorNode;
  pos: number;
}

export type PlainTextTabResult =
  | { type: "bail" }
  | { type: "noop" }
  | { type: "apply"; transaction: Transaction };

function isPositionInsideListItem($pos: ResolvedPos): boolean {
  for (let depth = $pos.depth; depth >= 0; depth -= 1) {
    if ($pos.node(depth).type.name === "listItem") {
      return true;
    }
  }
  return false;
}

/**
 * Collects every textblock (paragraph/heading) touched by [from, to]. Returns
 * null if the range contains a list item anywhere, so callers never have to
 * partially edit content that straddles a list.
 */
function collectPlainTextBlocks(
  doc: ProseMirrorNode,
  from: number,
  to: number,
): PlainTextBlockRange[] | null {
  const blocks: PlainTextBlockRange[] = [];
  let sawListItem = false;

  doc.nodesBetween(from, to, (node, pos) => {
    if (node.type.name === "listItem") {
      sawListItem = true;
      return false;
    }
    if (node.isTextblock) {
      blocks.push({ node, pos });
      return false;
    }
    return true;
  });

  if (sawListItem || blocks.length === 0) {
    return null;
  }

  return blocks;
}

function countSpacesBefore(
  parent: ProseMirrorNode,
  offset: number,
  max: number,
): number {
  const start = Math.max(0, offset - max);
  const text = parent.textBetween(start, offset);
  let count = 0;
  for (let i = text.length - 1; i >= 0 && count < max; i -= 1) {
    if (text[i] !== " ") {
      break;
    }
    count += 1;
  }
  return count;
}

function countLeadingSpaces(node: ProseMirrorNode, max: number): number {
  const text = node.textBetween(0, Math.min(max, node.content.size));
  let count = 0;
  for (let i = 0; i < text.length && count < max; i += 1) {
    if (text[i] !== " ") {
      break;
    }
    count += 1;
  }
  return count;
}

function buildInsertAtCursorTransaction(
  state: EditorState,
  pos: number,
): Transaction {
  const { tr, schema, doc } = state;
  const marks = doc.resolve(pos).marks();
  tr.insert(pos, schema.text(PLAIN_TEXT_TAB_SPACES, marks));
  tr.setSelection(TextSelection.create(tr.doc, tr.mapping.map(pos, 1)));
  return tr;
}

function buildRemoveBeforeCursorTransaction(
  state: EditorState,
  $pos: ResolvedPos,
): Transaction | null {
  const removeCount = countSpacesBefore(
    $pos.parent,
    $pos.parentOffset,
    PLAIN_TEXT_TAB_MAX_SPACES,
  );
  if (removeCount === 0) {
    return null;
  }
  const { tr } = state;
  tr.delete($pos.pos - removeCount, $pos.pos);
  tr.setSelection(TextSelection.create(tr.doc, tr.mapping.map($pos.pos)));
  return tr;
}

function buildSingleSelectionInsertTransaction(
  state: EditorState,
  from: number,
  to: number,
): Transaction {
  const { tr, schema, doc } = state;
  const marks = doc.resolve(from).marks();
  tr.insert(from, schema.text(PLAIN_TEXT_TAB_SPACES, marks));
  const mappedFrom = tr.mapping.map(from, 1);
  const mappedTo = tr.mapping.map(to, -1);
  tr.setSelection(TextSelection.create(tr.doc, mappedFrom, mappedTo));
  return tr;
}

function buildSingleSelectionRemoveTransaction(
  state: EditorState,
  $from: ResolvedPos,
  to: number,
): Transaction | null {
  const removeCount = countSpacesBefore(
    $from.parent,
    $from.parentOffset,
    PLAIN_TEXT_TAB_MAX_SPACES,
  );
  if (removeCount === 0) {
    return null;
  }
  const { tr } = state;
  tr.delete($from.pos - removeCount, $from.pos);
  const mappedFrom = tr.mapping.map($from.pos, 1);
  const mappedTo = tr.mapping.map(to, -1);
  tr.setSelection(TextSelection.create(tr.doc, mappedFrom, mappedTo));
  return tr;
}

function buildMultiBlockInsertTransaction(
  state: EditorState,
  blocks: PlainTextBlockRange[],
  from: number,
  to: number,
): Transaction {
  const { tr, schema, doc } = state;
  const insertions = blocks.map((block) => ({
    pos: block.pos + 1,
    marks: doc.resolve(block.pos + 1).marks(),
  }));

  for (let i = insertions.length - 1; i >= 0; i -= 1) {
    const insertion = insertions[i];
    tr.insert(
      insertion.pos,
      schema.text(PLAIN_TEXT_TAB_SPACES, insertion.marks),
    );
  }

  const mappedFrom = tr.mapping.map(from, 1);
  const mappedTo = tr.mapping.map(to, -1);
  tr.setSelection(TextSelection.create(tr.doc, mappedFrom, mappedTo));
  return tr;
}

function buildMultiBlockRemoveTransaction(
  state: EditorState,
  blocks: PlainTextBlockRange[],
  from: number,
  to: number,
): Transaction | null {
  const removals = blocks
    .map((block) => {
      const contentStart = block.pos + 1;
      const removeCount = countLeadingSpaces(
        block.node,
        PLAIN_TEXT_TAB_MAX_SPACES,
      );
      return removeCount > 0
        ? { start: contentStart, end: contentStart + removeCount }
        : null;
    })
    .filter(
      (removal): removal is { start: number; end: number } => removal !== null,
    );

  if (removals.length === 0) {
    return null;
  }

  const { tr } = state;
  for (let i = removals.length - 1; i >= 0; i -= 1) {
    tr.delete(removals[i].start, removals[i].end);
  }

  const mappedFrom = tr.mapping.map(from, 1);
  const mappedTo = tr.mapping.map(to, -1);
  tr.setSelection(TextSelection.create(tr.doc, mappedFrom, mappedTo));
  return tr;
}

/**
 * Builds the plain-text Tab/Shift+Tab result for the current selection.
 * Returns "bail" whenever any part of the selection touches a list item (or
 * the selection isn't a plain TextSelection), so ListItem's own sink/lift
 * keymap and native fallback behavior are never duplicated or overridden.
 * Returns "noop" when the operation is well-defined but has nothing to do
 * (e.g. Shift+Tab with no preceding spaces) so the keypress still counts as
 * handled and focus stays in the editor.
 */
export function buildPlainTextTabResult(
  state: EditorState,
  direction: "insert" | "remove",
): PlainTextTabResult {
  const { selection } = state;
  if (!(selection instanceof TextSelection)) {
    return { type: "bail" };
  }

  const { $from, $to, from, to, empty } = selection;
  if (isPositionInsideListItem($from) || isPositionInsideListItem($to)) {
    return { type: "bail" };
  }

  if (empty) {
    const transaction =
      direction === "insert"
        ? buildInsertAtCursorTransaction(state, from)
        : buildRemoveBeforeCursorTransaction(state, $from);
    return transaction ? { type: "apply", transaction } : { type: "noop" };
  }

  const blocks = collectPlainTextBlocks(state.doc, from, to);
  if (blocks === null) {
    return { type: "bail" };
  }

  const transaction =
    blocks.length === 1
      ? direction === "insert"
        ? buildSingleSelectionInsertTransaction(state, from, to)
        : buildSingleSelectionRemoveTransaction(state, $from, to)
      : direction === "insert"
        ? buildMultiBlockInsertTransaction(state, blocks, from, to)
        : buildMultiBlockRemoveTransaction(state, blocks, from, to);

  return transaction ? { type: "apply", transaction } : { type: "noop" };
}

export const PlainTextTab = Extension.create({
  name: "plainTextTab",

  addKeyboardShortcuts() {
    const run = (direction: "insert" | "remove") => (): boolean => {
      const result = buildPlainTextTabResult(this.editor.state, direction);
      if (result.type === "bail") {
        return false;
      }
      if (result.type === "apply") {
        this.editor.view.dispatch(result.transaction);
      }
      return true;
    };

    return {
      Tab: run("insert"),
      "Shift-Tab": run("remove"),
    };
  },
});

const LINK_OPEN_TARGET = "_blank";
const LINK_OPEN_WINDOW_FEATURES = "noopener,noreferrer";

export type LinkOpenerFn = (
  href: string,
  target: string,
  features: string,
) => void;

const defaultLinkOpener: LinkOpenerFn = (href, target, features) => {
  window.open(href, target, features);
};

/**
 * Revalidates href through normalizeAllowedLinkHref immediately before
 * opening, so the mouse and keyboard link-opening gestures can never drift
 * from RidgeNote's http/https/mailto-only link policy.
 */
export function openValidatedLink(
  href: string | null | undefined,
  openFn: LinkOpenerFn = defaultLinkOpener,
): boolean {
  if (!href) {
    return false;
  }
  const safeHref = normalizeAllowedLinkHref(href);
  if (!safeHref) {
    return false;
  }
  openFn(safeHref, LINK_OPEN_TARGET, LINK_OPEN_WINDOW_FEATURES);
  return true;
}

export interface LinkAnchorLike {
  href: string;
}

export interface LinkClickTargetLike {
  closest(selector: "a"): LinkAnchorLike | null;
}

export interface LinkClickRootLike {
  contains(node: unknown): boolean;
}

export interface LinkClickEventLike {
  button: number;
  ctrlKey: boolean;
  metaKey: boolean;
  target: LinkClickTargetLike | null;
}

/**
 * Resolves the href of an editor-internal link for a Ctrl+Click (Windows/
 * Linux) or Cmd+Click (macOS) gesture. Returns null for a normal click, a
 * non-primary-button click, a click outside any link, or a link outside the
 * given editor root.
 */
export function resolveModifierClickLinkHref(
  event: LinkClickEventLike,
  root: LinkClickRootLike,
): string | null {
  if (event.button !== 0 || !(event.ctrlKey || event.metaKey)) {
    return null;
  }
  const anchor = event.target?.closest("a") ?? null;
  if (!anchor || !root.contains(anchor)) {
    return null;
  }
  return anchor.href || null;
}

/**
 * Returns the href of the link mark active at the current selection, or
 * null if the selection isn't uniformly inside a single link.
 */
export function getActiveLinkHref(state: EditorState): string | null {
  const linkType = state.schema.marks.link;
  if (!linkType) {
    return null;
  }
  const { $from, $to, empty } = state.selection;
  const marks = empty ? $from.marks() : $from.marksAcross($to);
  if (!marks) {
    return null;
  }
  const href = linkType.isInSet(marks)?.attrs.href;
  return typeof href === "string" ? href : null;
}

/**
 * Adds the two link-opening gestures RidgeNote's Link configuration
 * intentionally doesn't provide (openOnClick/autolink/linkOnPaste all stay
 * disabled): Ctrl/Cmd+Click on an editor-internal link, and the
 * Mod-Alt-Enter keyboard equivalent. Both share openValidatedLink so they
 * can never diverge on validation, target, or window features.
 */
export const LinkOpenGesture = Extension.create({
  name: "linkOpenGesture",

  addProseMirrorPlugins() {
    return [
      new Plugin({
        key: new PluginKey("linkOpenGesture"),
        props: {
          handleClick: (view, _pos, event) => {
            const target = event.target as HTMLElement | null;
            const href = resolveModifierClickLinkHref(
              {
                button: event.button,
                ctrlKey: event.ctrlKey,
                metaKey: event.metaKey,
                target,
              },
              view.dom,
            );
            const opened = openValidatedLink(href);
            if (opened) {
              event.preventDefault();
            }
            return opened;
          },
        },
      }),
    ];
  },

  addKeyboardShortcuts() {
    return {
      "Mod-Alt-Enter": () =>
        openValidatedLink(getActiveLinkHref(this.editor.state)),
    };
  },
});

export function runToolbarCommand(
  editor: EditorLike,
  action: ToolbarAction,
  promptFn: PromptFn,
): boolean {
  const chain = editor.chain().focus();

  switch (action.command) {
    case "bold":
      return chain.toggleBold().run();
    case "italic":
      return chain.toggleItalic().run();
    case "underline":
      return chain.toggleUnderline().run();
    case "heading":
      if (![1, 2, 3].includes(action.level ?? 0)) {
        return false;
      }
      return chain.toggleHeading({ level: action.level as number }).run();
    case "bulletList":
      return chain.toggleBulletList().run();
    case "orderedList":
      return chain.toggleOrderedList().run();
    case "clearFormatting":
      return chain.unsetAllMarks().clearNodes().run();
    case "undo":
      return chain.undo().run();
    case "redo":
      return chain.redo().run();
    case "link": {
      const response = promptFn("Enter a link URL", "https://");
      if (response === null) {
        return false;
      }
      if (!response.trim()) {
        return chain.extendMarkRange("link").unsetLink().run();
      }
      const href = normalizeAllowedLinkHref(response);
      if (!href) {
        return false;
      }
      return chain.extendMarkRange("link").setLink({ href }).run();
    }
  }
}

export function getNavigationType(win: Window): string | undefined {
  const navigationEntries = win.performance?.getEntriesByType?.("navigation");
  const navigationEntry = navigationEntries?.[0] as
    | PerformanceNavigationTiming
    | undefined;
  return navigationEntry?.type;
}

export function shouldAutoFocusNewNoteBody(
  options: NewNoteBodyFocusOptions,
): boolean {
  return (
    options.isImmediateNewPage && options.navigationType !== "back_forward"
  );
}

export function shouldApplyNewNoteFocusBehavior(
  options: NewNoteFocusOptions,
): boolean {
  return (
    shouldAutoFocusNewNoteBody(options) &&
    !options.hasSelectedTitle &&
    options.titleValue === options.generatedTitle
  );
}

export function installOneTimeGeneratedTitleSelection(
  titleInput: TitleInputLike,
  options: Omit<NewNoteFocusOptions, "titleValue" | "hasSelectedTitle">,
  win: Window = window,
): void {
  let hasSelectedTitle = false;

  titleInput.addEventListener("focus", () => {
    if (
      !shouldApplyNewNoteFocusBehavior({
        ...options,
        titleValue: titleInput.value,
        hasSelectedTitle,
        navigationType: getNavigationType(win),
      })
    ) {
      return;
    }

    titleInput.select();
    hasSelectedTitle = true;
  });
}

export function setFormattingToolbarExpanded(
  toggleButton: ToolbarToggleButtonLike,
  toolbar: ToolbarRegionLike,
  expanded: boolean,
): void {
  toolbar.hidden = !expanded;
  toggleButton.setAttribute("aria-expanded", expanded ? "true" : "false");
  toggleButton.textContent = expanded
    ? FORMATTING_TOGGLE_LABEL_HIDE
    : FORMATTING_TOGGLE_LABEL_SHOW;
}

export function installFormattingToolbarToggle(
  toggleButton: ToolbarToggleButtonLike,
  toolbar: ToolbarRegionLike,
): void {
  setFormattingToolbarExpanded(toggleButton, toolbar, !toolbar.hidden);
  toggleButton.addEventListener("click", (event) => {
    event.preventDefault();
    setFormattingToolbarExpanded(toggleButton, toolbar, toolbar.hidden);
  });
}

export function focusEditorBody(
  editor: EditorLike,
  win: Window = window,
): void {
  win.requestAnimationFrame(() => {
    editor.chain().focus().setTextSelection(1).run();
  });
}

const EDITOR_EXIT_MODIFIER_KEYS = new Set([
  "Shift",
  "Control",
  "Alt",
  "Meta",
  "AltGraph",
  "CapsLock",
]);

export interface EditorExitKeydownEventLike {
  key: string;
  stopPropagation(): void;
}

export interface EditorExitGestureTarget {
  addEventListener(
    type: "keydown",
    listener: (event: EditorExitKeydownEventLike) => void,
    options: { capture: true },
  ): void;
  addEventListener(
    type: "pointerdown" | "blur",
    listener: () => void,
    options: { capture: true },
  ): void;
}

/**
 * Tracks the one-shot Escape-then-Tab editor-exit gesture. Escape arms the
 * gesture; the next Tab or Shift+Tab then bypasses Tiptap's ListItem keymap
 * (via stopPropagation, never preventDefault) so the browser's native focus
 * movement runs unopposed. Any other key, pointer interaction, or blur
 * disarms it without side effects.
 */
export class EditorExitGesture {
  private armed = false;

  get isArmed(): boolean {
    return this.armed;
  }

  handleKeydown(event: EditorExitKeydownEventLike): void {
    if (event.key === "Escape") {
      this.armed = true;
      return;
    }

    if (event.key === "Tab") {
      if (this.armed) {
        this.armed = false;
        event.stopPropagation();
      }
      return;
    }

    if (!EDITOR_EXIT_MODIFIER_KEYS.has(event.key)) {
      this.armed = false;
    }
  }

  handlePointerDown(): void {
    this.armed = false;
  }

  handleBlur(): void {
    this.armed = false;
  }
}

export function installEditorExitGesture(
  target: EditorExitGestureTarget,
  gesture: EditorExitGesture = new EditorExitGesture(),
): EditorExitGesture {
  target.addEventListener("keydown", (event) => gesture.handleKeydown(event), {
    capture: true,
  });
  target.addEventListener("pointerdown", () => gesture.handlePointerDown(), {
    capture: true,
  });
  target.addEventListener("blur", () => gesture.handleBlur(), {
    capture: true,
  });
  return gesture;
}

export function isUnsafeSaveState(state: NoteSaveState): boolean {
  return state !== "clean";
}

export function saveStateLabel(state: NoteSaveState): string {
  switch (state) {
    case "clean":
      return "Saved";
    case "dirty":
    case "dirty_during_save":
      return "Unsaved changes";
    case "saving":
      return "Saving…";
    case "failed":
      return "Save failed";
    case "conflicted":
      return "Conflict detected";
  }
}

export function buildAutosaveRequestPayload(
  snapshot: SaveSnapshot,
  version: number,
): { body_json: unknown; title: string; version: number } {
  return {
    body_json: snapshot.bodyJson,
    title: snapshot.title,
    version,
  };
}

export function shouldScheduleRetry(status: number): boolean {
  return status >= 500;
}

export function createBeforeUnloadHandler(
  getState: () => NoteSaveState,
  shouldBypass: () => boolean = () => false,
): BeforeUnloadListener {
  return (event) => {
    if (shouldBypass() || !isUnsafeSaveState(getState())) {
      return;
    }
    event.preventDefault();
    event.returnValue = "";
  };
}

export async function copyTextWithFallback(
  text: string,
  options: {
    documentLike?: CopyDocumentLike;
    navigatorLike?: NavigatorLike;
  } = {},
): Promise<boolean> {
  const clipboard = options.navigatorLike?.clipboard;
  if (clipboard?.writeText) {
    try {
      await clipboard.writeText(text);
      return true;
    } catch {
      // Fall through to the legacy copy path when secure-context clipboard access fails.
    }
  }

  const documentLike = options.documentLike;
  if (!documentLike?.body || !documentLike.execCommand) {
    return false;
  }

  const textarea = documentLike.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "readonly");
  textarea.setAttribute("aria-hidden", "true");
  textarea.style.position = "fixed";
  textarea.style.top = "0";
  textarea.style.left = "-9999px";
  textarea.style.opacity = "0";
  documentLike.body.appendChild(textarea);

  try {
    textarea.focus();
    textarea.select();
    return documentLike.execCommand("copy") === true;
  } finally {
    documentLike.body.removeChild(textarea);
  }
}

function firstErrorMessage(
  errors: Record<string, Array<{ code: string; message: string }>> | undefined,
): string {
  if (!errors) {
    return SAVE_FAILED_PERSISTENT_NOTICE;
  }
  for (const fieldErrors of Object.values(errors)) {
    const firstError = fieldErrors[0];
    if (firstError?.message) {
      return firstError.message;
    }
  }
  return SAVE_FAILED_PERSISTENT_NOTICE;
}

export function documentPlainText(documentJson: unknown): string {
  const lines = nodeToLines(documentJson);
  return lines.join("\n\n").trim();
}

function inlineContentToText(content: unknown): string {
  if (!Array.isArray(content)) {
    return "";
  }

  let text = "";
  for (const child of content) {
    if (!child || typeof child !== "object") {
      continue;
    }
    const node = child as {
      content?: unknown;
      text?: string;
      type?: string;
    };
    if (node.type === "text") {
      text += node.text ?? "";
      continue;
    }
    if (node.type === "hardBreak") {
      text += "\n";
      continue;
    }
    if (node.content) {
      text += inlineContentToText(node.content);
    }
  }
  return text;
}

function nodeToLines(node: unknown): string[] {
  if (!node || typeof node !== "object") {
    return [];
  }

  const record = node as {
    content?: unknown;
    type?: string;
  };
  const content = Array.isArray(record.content) ? record.content : [];

  switch (record.type) {
    case "doc":
      return content.flatMap((child) => nodeToLines(child));
    case "paragraph":
    case "heading": {
      const text = inlineContentToText(content).trimEnd();
      return text ? [text] : [];
    }
    case "bulletList":
    case "orderedList":
      return content.flatMap((child) => nodeToLines(child));
    case "listItem":
      return content.flatMap((child) => nodeToLines(child));
    default:
      return [];
  }
}

export class NoteSaveController {
  private beforeUnloadBypass = false;
  private beforeUnloadHandler: BeforeUnloadListener;
  private currentVersion: number;
  private debounceTimer: number | null = null;
  private freshnessState: FreshnessState = "current";
  private freshnessTimer: number | null = null;
  private inflightFreshnessPromise: Promise<void> | null = null;
  private inflightSavePromise: Promise<void> | null = null;
  private latestQueuedSnapshot: SaveSnapshot | null = null;
  private maxTimer: number | null = null;
  private pendingPrint = false;
  private retryTimer: number | null = null;
  private copyFeedbackTimer: number | null = null;
  private state: NoteSaveState = "clean";
  private statusDetailOverride: string | null = null;
  private statusDetailTimer: number | null = null;

  constructor(private readonly options: NoteSaveControllerOptions) {
    this.currentVersion = Number(options.versionInput.value || "0");
    this.beforeUnloadHandler = createBeforeUnloadHandler(
      () => this.state,
      () => this.beforeUnloadBypass,
    );
    this.resetCopyLocalButtonLabel();
    this.renderState();
    this.installEventHandlers();
    this.scheduleNextFreshnessCheck();
  }

  getState(): NoteSaveState {
    return this.state;
  }

  /**
   * `options.suppressIdleActivity` is set only by the session-idle
   * warning's own flush-if-dirty call (see session-idle.ts) -- it adds
   * a header the backend treats as *not* activity (see
   * `accounts.services.should_refresh_session_activity`), so a save
   * that happens to occur right at the idle-warning threshold cannot
   * itself silently extend the session. Every other caller (Print,
   * ordinary autosave) omits it, leaving today's behavior -- a normal
   * save legitimately refreshing the session -- completely unchanged.
   */
  async flushPendingSave(options: FlushPendingSaveOptions = {}): Promise<void> {
    if (this.state === "conflicted") {
      return;
    }
    this.pendingPrint = false;
    this.cancelRetryTimer();
    await this.requestSaveNow(options);
  }

  handleDocumentEdited(): void {
    if (this.state === "conflicted") {
      return;
    }
    this.updateHiddenBodyValue();
    this.clearStatusDetailOverride();
    if (this.state === "saving") {
      this.latestQueuedSnapshot = this.captureSnapshot();
      this.setState("dirty_during_save");
      this.cancelRetryTimer();
      return;
    }

    this.cancelRetryTimer();
    this.setState("dirty");
    this.scheduleAutosave();
  }

  async handlePrintRequested(): Promise<void> {
    // A true conflict is never auto-resolved on Print's behalf -- the
    // existing unsaved-editor guard for this state is preserved exactly:
    // no save attempt, no tab opened, straight to the blocked notice.
    if (this.state === "conflicted") {
      this.showNotice(PRINT_BLOCKED_NOTICE, {
        showReload: true,
        showCopyLocal: true,
        tone: "error",
      });
      return;
    }

    if (this.state === "clean" && this.freshnessState === "current") {
      // Already safe to print -- open and navigate in one synchronous
      // step, still a direct response to the click.
      this.options.openBlankTab()?.location.assign(this.options.printUrl);
      return;
    }

    // A save is needed first (dirty, mid-save, or previously failed).
    // Open the destination tab *synchronously*, before the `await`
    // below -- opening it only after a save resolves would no longer
    // read as a direct user gesture to most browsers' popup-blocker
    // heuristics. It stays blank until we know printing is actually
    // safe.
    const tab = this.options.openBlankTab();
    await this.flushPendingSave();

    if (this.state === "clean" && this.freshnessState === "current") {
      tab?.location.assign(this.options.printUrl);
      return;
    }

    // The save failed, hit a conflict, or the note is no longer
    // current -- never navigate the waiting tab to what would be
    // stale content. `getState()` (rather than `this.state` directly)
    // sidesteps a TypeScript narrowing quirk: it still believes
    // `this.state` excludes "conflicted" here, carried over from the
    // early-return above, even though `await this.flushPendingSave()`
    // may well have changed it since.
    tab?.close();
    const stateAfterSave = this.getState();
    this.showNotice(PRINT_BLOCKED_NOTICE, {
      showReload: stateAfterSave === "conflicted",
      showCopyLocal: stateAfterSave === "conflicted",
      tone: stateAfterSave === "conflicted" ? "error" : "warning",
    });
  }

  handleUnsafeNavigation(): boolean {
    if (!isUnsafeSaveState(this.state)) {
      return true;
    }
    return this.options.confirmFn
      ? this.options.confirmFn(UNSAFE_NAVIGATION_MESSAGE)
      : true;
  }

  private applyFreshnessResponse(
    response: FreshnessCurrentResponse | FreshnessUpdatedResponse,
    requestedVersion: number,
  ): void {
    if (this.currentVersion !== requestedVersion) {
      return;
    }

    if (!response.has_update) {
      this.freshnessState = "current";
      this.renderState();
      return;
    }

    const remoteNote = response.note;
    if (remoteNote.version <= this.currentVersion) {
      return;
    }
    if (remoteNote.editor_schema_version !== SUPPORTED_EDITOR_SCHEMA_VERSION) {
      this.applyReadOnly(true);
      this.showNotice(UNSUPPORTED_SCHEMA_NOTICE, {
        showReload: false,
        showCopyLocal: false,
        tone: "error",
      });
      this.setState("failed");
      return;
    }

    if (this.state === "clean" && !this.inflightSavePromise) {
      this.applyRemoteState(remoteNote);
      return;
    }

    this.markRemoteNewerWhileUnsafe();
  }

  private applyReadOnly(readOnly: boolean): void {
    this.options.titleInput.readOnly = readOnly;
    this.options.editor.setEditable(!readOnly);
    this.options.saveButton.disabled = readOnly;
    this.options.toolbarToggle.disabled = readOnly;
    if (readOnly) {
      this.options.toolbar.hidden = true;
      this.options.toolbarToggle.setAttribute("aria-expanded", "false");
    }
    if (this.options.copyLocalButton.disabled !== undefined) {
      this.options.copyLocalButton.disabled = false;
    }
  }

  private applyRemoteState(remoteNote: RemoteNoteState): void {
    this.clearAutosaveTimers();
    this.cancelRetryTimer();
    this.latestQueuedSnapshot = null;
    this.pendingPrint = false;
    this.applyReadOnly(false);
    this.options.titleInput.value = remoteNote.title;
    this.options.versionInput.value = String(remoteNote.version);
    this.options.replaceEditorDocument(remoteNote.body_json);
    this.options.hiddenBodyInput.value = JSON.stringify(remoteNote.body_json);
    this.currentVersion = remoteNote.version;
    this.freshnessState = "current";
    this.setState("clean");
    this.setTemporaryStatusDetail(UPDATED_FROM_ANOTHER_SESSION_DETAIL);
  }

  private canTreatConflictAsStale(currentVersion: number | undefined): boolean {
    return (
      typeof currentVersion === "number" &&
      currentVersion <= this.currentVersion
    );
  }

  private async copyLocalText(): Promise<void> {
    const bodyText = documentPlainText(this.options.editor.getJSON());
    const text = [this.options.titleInput.value.trim(), bodyText]
      .filter(Boolean)
      .join("\n\n");

    let copied = false;
    try {
      copied = Boolean(await this.options.copyTextFn?.(text));
    } catch {
      copied = false;
    }

    if (copied) {
      this.showCopyLocalSuccess();
      return;
    }

    this.showNotice(COPY_LOCAL_FAILED_NOTICE, {
      showReload: true,
      showCopyLocal: true,
      tone: "warning",
    });
  }

  private captureSnapshot(): SaveSnapshot {
    const snapshot = {
      bodyJson: this.options.editor.getJSON(),
      title: this.options.titleInput.value,
    };
    this.options.hiddenBodyInput.value = JSON.stringify(snapshot.bodyJson);
    return snapshot;
  }

  private cancelRetryTimer(): void {
    if (this.retryTimer !== null) {
      this.options.scheduler.clearTimeout(this.retryTimer);
      this.retryTimer = null;
    }
  }

  private clearCopyFeedbackTimer(): void {
    if (this.copyFeedbackTimer !== null) {
      this.options.scheduler.clearTimeout(this.copyFeedbackTimer);
      this.copyFeedbackTimer = null;
    }
  }

  private clearAutosaveTimers(): void {
    if (this.debounceTimer !== null) {
      this.options.scheduler.clearTimeout(this.debounceTimer);
      this.debounceTimer = null;
    }
    if (this.maxTimer !== null) {
      this.options.scheduler.clearTimeout(this.maxTimer);
      this.maxTimer = null;
    }
  }

  private clearStatusDetailOverride(): void {
    if (this.statusDetailTimer !== null) {
      this.options.scheduler.clearTimeout(this.statusDetailTimer);
      this.statusDetailTimer = null;
    }
    this.statusDetailOverride = null;
  }

  private hideNotice(): void {
    this.resetCopyLocalButtonLabel();
    this.options.noticeRegion.hidden = true;
    this.options.noticeActions.hidden = true;
    this.options.noticeText.textContent = "";
    this.options.reloadButton.hidden = true;
    this.options.copyLocalButton.hidden = true;
    this.options.noticeRegion.setAttribute("data-tone", "info");
  }

  private hideRecoveryControls(): void {
    this.resetCopyLocalButtonLabel();
    this.options.noticeActions.hidden = true;
    this.options.reloadButton.hidden = true;
    this.options.copyLocalButton.hidden = true;
  }

  private installEventHandlers(): void {
    this.options.titleInput.addEventListener("input", () => {
      this.handleDocumentEdited();
    });
    this.options.saveButton.addEventListener("click", (event) => {
      event.preventDefault();
      void this.requestSaveNow();
    });
    this.options.form.addEventListener("submit", (event) => {
      event.preventDefault();
      void this.requestSaveNow();
    });
    for (const printLink of this.options.printLinks) {
      printLink.addEventListener("click", (event) => {
        event.preventDefault();
        void this.handlePrintRequested();
      });
    }
    for (const exportLink of this.options.exportLinks) {
      exportLink.addEventListener("click", (event) => {
        if (this.state === "conflicted") {
          event.preventDefault();
          this.showNotice(
            "Export JSON is unavailable while this note is in conflict. Copy local draft or reload latest first.",
            {
              showReload: true,
              showCopyLocal: true,
              tone: "error",
            },
          );
          return;
        }
        if (this.freshnessState === "remote_newer") {
          event.preventDefault();
          this.showNotice(EXPORT_BLOCKED_NOTICE, {
            showReload: false,
            showCopyLocal: false,
            tone: "warning",
          });
          return;
        }
        if (!this.handleUnsafeNavigation()) {
          event.preventDefault();
        }
      });
    }
    this.options.reloadButton.addEventListener("click", (event) => {
      event.preventDefault();
      this.beforeUnloadBypass = true;
      this.hideNotice();
      this.options.form.setAttribute("data-note-conflicted", "false");
      this.options.locationAssign(this.options.detailUrl);
    });
    this.options.copyLocalButton.addEventListener("click", (event) => {
      event.preventDefault();
      void this.copyLocalText();
    });
    for (const target of this.options.navigationGuardTargets) {
      target.addEventListener("click", (event) => {
        if (!this.handleUnsafeNavigation()) {
          event.preventDefault();
        }
      });
      target.addEventListener("submit", (event) => {
        if (!this.handleUnsafeNavigation()) {
          event.preventDefault();
        }
      });
    }
    this.options.windowEvents?.addEventListener(
      "beforeunload",
      this.beforeUnloadHandler,
    );
    this.options.windowEvents?.addEventListener("focus", () => {
      void this.requestFreshnessCheck();
    });
    this.options.documentEvents?.addEventListener("visibilitychange", () => {
      if (this.options.documentEvents?.visibilityState === "visible") {
        void this.requestFreshnessCheck();
      }
    });
  }

  private isDocumentVisible(): boolean {
    return this.options.documentEvents?.visibilityState !== "hidden";
  }

  private markFreshnessUnknown(): void {
    if (this.freshnessState === "remote_newer") {
      return;
    }
    this.freshnessState = "unknown";
    this.renderState();
  }

  private markRemoteNewerWhileUnsafe(): void {
    this.freshnessState = "remote_newer";
    this.showNotice(REMOTE_NEWER_NOTICE, {
      showReload: false,
      showCopyLocal: false,
      tone: "warning",
    });
    this.renderState();
  }

  private async requestFreshnessCheck(): Promise<void> {
    if (
      this.inflightFreshnessPromise ||
      !this.isDocumentVisible() ||
      this.state === "conflicted"
    ) {
      return this.inflightFreshnessPromise ?? Promise.resolve();
    }

    const requestedVersion = this.currentVersion;
    this.inflightFreshnessPromise = this.sendFreshnessRequest(requestedVersion)
      .then((response) => {
        this.applyFreshnessResponse(response, requestedVersion);
      })
      .catch((error: unknown) => {
        const status =
          typeof error === "object" && error !== null && "status" in error
            ? Number((error as { status: number }).status)
            : 0;
        const payload =
          typeof error === "object" && error !== null && "payload" in error
            ? (error as { payload?: FreshnessResponse }).payload
            : undefined;

        if (status === 401) {
          this.showNotice(SESSION_EXPIRED_NOTICE, {
            showReload: false,
            showCopyLocal: false,
            tone: "error",
          });
          this.setState("failed");
          return;
        }

        if (
          status === 409 &&
          payload &&
          "error" in payload &&
          payload.error === "unsupported_schema"
        ) {
          this.applyReadOnly(true);
          this.showNotice(UNSUPPORTED_SCHEMA_NOTICE, {
            showReload: false,
            showCopyLocal: false,
            tone: "error",
          });
          this.setState("failed");
          return;
        }

        this.markFreshnessUnknown();
      })
      .finally(() => {
        this.inflightFreshnessPromise = null;
      });

    return this.inflightFreshnessPromise;
  }

  private async requestSaveNow(
    options: FlushPendingSaveOptions = {},
  ): Promise<void> {
    if (this.state === "conflicted") {
      return;
    }

    const snapshot = this.captureSnapshot();
    if (this.inflightSavePromise) {
      this.latestQueuedSnapshot = snapshot;
      this.setState("dirty_during_save");
      return this.inflightSavePromise;
    }

    this.clearAutosaveTimers();
    return this.startSave(snapshot, this.currentVersion, options);
  }

  private renderState(): void {
    const label = saveStateLabel(this.state);
    this.options.statusRegion.textContent = label;
    this.options.statusRegion.setAttribute(
      "aria-live",
      this.state === "failed" || this.state === "conflicted"
        ? "assertive"
        : "polite",
    );
    const tone = this.statusTone();
    this.options.statusRegion.setAttribute("data-tone", tone);
    const detail = this.statusDetailMessage();
    this.options.statusRegion.setAttribute("title", detail);
    this.options.statusRegion.setAttribute("aria-label", `${label}. ${detail}`);
    this.options.statusRegion.setAttribute(
      "data-status-kind",
      this.statusKind(),
    );
    this.options.form.setAttribute(
      "data-note-conflicted",
      this.state === "conflicted" ? "true" : "false",
    );
    for (const printLink of this.options.printLinks) {
      printLink.setAttribute(
        "aria-disabled",
        this.state === "clean" && this.freshnessState === "current"
          ? "false"
          : "true",
      );
    }
    for (const exportLink of this.options.exportLinks) {
      exportLink.setAttribute(
        "aria-disabled",
        this.state === "conflicted" || this.freshnessState === "remote_newer"
          ? "true"
          : "false",
      );
    }
    if (this.state !== "conflicted") {
      this.hideRecoveryControls();
    }
    if (this.state === "clean" && this.freshnessState !== "remote_newer") {
      this.hideNotice();
    }
  }

  private scheduleAutosave(): void {
    if (!["dirty", "failed"].includes(this.state)) {
      return;
    }
    if (this.debounceTimer !== null) {
      this.options.scheduler.clearTimeout(this.debounceTimer);
    }
    this.debounceTimer = this.options.scheduler.setTimeout(() => {
      void this.requestSaveNow();
    }, AUTOSAVE_DEBOUNCE_MS);
    if (this.maxTimer === null) {
      this.maxTimer = this.options.scheduler.setTimeout(() => {
        void this.requestSaveNow();
      }, MAX_PENDING_SAVE_MS);
    }
  }

  private scheduleNextFreshnessCheck(): void {
    if (this.freshnessTimer !== null) {
      this.options.scheduler.clearTimeout(this.freshnessTimer);
    }
    this.freshnessTimer = this.options.scheduler.setTimeout(() => {
      void this.requestFreshnessCheck().finally(() => {
        this.scheduleNextFreshnessCheck();
      });
    }, FRESHNESS_CHECK_INTERVAL_MS);
  }

  private sendFreshnessRequest(
    version: number,
  ): Promise<FreshnessCurrentResponse | FreshnessUpdatedResponse> {
    return this.options
      .fetchFn(`${this.options.freshnessUrl}?version=${version}`, {
        method: "GET",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": this.options.csrfToken,
        },
      })
      .then(async (response) => {
        const payload = (await response.json()) as FreshnessResponse;
        if (response.status >= 400 || !payload.ok) {
          throw { status: response.status, payload };
        }
        return payload;
      });
  }

  private async sendSaveRequest(
    snapshot: SaveSnapshot,
    version: number,
    options: FlushPendingSaveOptions = {},
  ): Promise<AutosaveResponse> {
    const headers: Record<string, string> = {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRFToken": this.options.csrfToken,
    };
    if (options.suppressIdleActivity) {
      headers[IDLE_WARNING_SAVE_HEADER] = "1";
    }
    const response = await this.options.fetchFn(this.options.autosaveUrl, {
      method: "POST",
      body: JSON.stringify(buildAutosaveRequestPayload(snapshot, version)),
      headers,
    });
    const payload = (await response.json()) as AutosaveResponse;
    if (response.status >= 500 || response.status === 401) {
      throw { status: response.status, payload };
    }
    return payload;
  }

  private setState(state: NoteSaveState): void {
    this.state = state;
    this.renderState();
  }

  private setTemporaryStatusDetail(message: string): void {
    this.clearStatusDetailOverride();
    this.statusDetailOverride = message;
    this.renderState();
    this.statusDetailTimer = this.options.scheduler.setTimeout(() => {
      this.statusDetailTimer = null;
      this.statusDetailOverride = null;
      this.renderState();
    }, 4000);
  }

  private resetCopyLocalButtonLabel(): void {
    this.clearCopyFeedbackTimer();
    this.options.copyLocalButton.textContent = COPY_LOCAL_BUTTON_LABEL;
    this.options.copyLocalButton.setAttribute("data-copy-state", "ready");
  }

  private showCopyLocalSuccess(): void {
    this.clearCopyFeedbackTimer();
    this.options.copyLocalButton.textContent = COPY_LOCAL_COPIED_LABEL;
    this.options.copyLocalButton.setAttribute("data-copy-state", "copied");
    this.copyFeedbackTimer = this.options.scheduler.setTimeout(() => {
      this.copyFeedbackTimer = null;
      this.resetCopyLocalButtonLabel();
    }, COPY_LOCAL_FEEDBACK_MS);
  }

  private showNotice(
    message: string,
    options: {
      showCopyLocal: boolean;
      showReload: boolean;
      tone: "error" | "warning" | "info";
    },
  ): void {
    this.options.noticeRegion.hidden = false;
    this.options.noticeRegion.setAttribute("data-tone", options.tone);
    this.options.noticeText.textContent = message;
    const showRecoveryControls =
      this.state === "conflicted" &&
      options.showReload &&
      options.showCopyLocal;
    this.options.noticeActions.hidden = !showRecoveryControls;
    this.options.reloadButton.hidden = !showRecoveryControls;
    this.options.copyLocalButton.hidden = !showRecoveryControls;
    if (!showRecoveryControls) {
      this.resetCopyLocalButtonLabel();
    }
  }

  private startQueuedSave(): Promise<void> {
    if (!this.latestQueuedSnapshot) {
      if (this.pendingPrint && this.state === "clean") {
        this.pendingPrint = false;
        this.options.locationAssign(this.options.printUrl);
      }
      return Promise.resolve();
    }

    const snapshot = this.latestQueuedSnapshot;
    this.latestQueuedSnapshot = null;
    return this.startSave(snapshot, this.currentVersion);
  }

  private async startSave(
    snapshot: SaveSnapshot,
    version: number,
    options: FlushPendingSaveOptions = {},
  ): Promise<void> {
    this.clearAutosaveTimers();
    this.setState("saving");
    this.hideNotice();

    const runSave = async (
      attemptSnapshot: SaveSnapshot,
      attemptVersion: number,
      allowRetry: boolean,
    ): Promise<void> => {
      this.inflightSavePromise = this.sendSaveRequest(
        attemptSnapshot,
        attemptVersion,
        options,
      )
        .then(async (response) => {
          if (response.ok) {
            this.currentVersion = response.version;
            this.options.versionInput.value = String(response.version);
            this.options.titleInput.value = response.title;
            this.options.onTitleSaved?.(response.title);
            this.options.hiddenBodyInput.value = JSON.stringify(
              attemptSnapshot.bodyJson,
            );
            this.freshnessState = "current";
            this.setState(
              this.latestQueuedSnapshot ? "dirty_during_save" : "clean",
            );
            if (this.state === "dirty_during_save") {
              await this.startQueuedSave();
            } else if (this.pendingPrint) {
              this.pendingPrint = false;
              this.options.locationAssign(this.options.printUrl);
            }
            return;
          }

          if (response.error === "conflict") {
            if (this.canTreatConflictAsStale(response.current_version)) {
              this.freshnessState = "current";
              this.applyReadOnly(false);
              this.hideNotice();
              const nextState = this.latestQueuedSnapshot
                ? "dirty_during_save"
                : ["dirty", "failed"].includes(this.state)
                  ? "dirty"
                  : "clean";
              this.setState(nextState);
              if (this.latestQueuedSnapshot) {
                await this.startQueuedSave();
              }
              return;
            }
            this.latestQueuedSnapshot = null;
            this.pendingPrint = false;
            this.freshnessState = "remote_newer";
            this.applyReadOnly(true);
            this.setState("conflicted");
            this.showNotice(CONFLICT_NOTICE, {
              showReload: true,
              showCopyLocal: true,
              tone: "error",
            });
            return;
          }

          if (response.error === "unsupported_schema") {
            this.applyReadOnly(true);
            this.pendingPrint = false;
            this.showNotice(UNSUPPORTED_SCHEMA_NOTICE, {
              showReload: false,
              showCopyLocal: false,
              tone: "error",
            });
            this.setState("failed");
            return;
          }

          if (response.errors) {
            this.pendingPrint = false;
            this.showNotice(firstErrorMessage(response.errors), {
              showReload: false,
              showCopyLocal: false,
              tone: "warning",
            });
            this.setState("failed");
            return;
          }

          this.pendingPrint = false;
          this.showNotice(SAVE_FAILED_PERSISTENT_NOTICE, {
            showReload: false,
            showCopyLocal: false,
            tone: "warning",
          });
          this.setState("failed");
        })
        .catch(async (error: unknown) => {
          const status =
            typeof error === "object" && error !== null && "status" in error
              ? Number((error as { status: number }).status)
              : 0;
          if (status === 401) {
            this.pendingPrint = false;
            this.showNotice(SESSION_EXPIRED_NOTICE, {
              showReload: false,
              showCopyLocal: false,
              tone: "error",
            });
            this.setState("failed");
            return;
          }
          if (allowRetry && (status === 0 || shouldScheduleRetry(status))) {
            this.showNotice(SAVE_FAILED_NOTICE, {
              showReload: false,
              showCopyLocal: false,
              tone: "warning",
            });
            this.retryTimer = this.options.scheduler.setTimeout(() => {
              this.retryTimer = null;
              void runSave(attemptSnapshot, attemptVersion, false);
            }, RETRY_DELAY_MS);
            this.setState("failed");
            return;
          }
          this.pendingPrint = false;
          this.showNotice(SAVE_FAILED_PERSISTENT_NOTICE, {
            showReload: false,
            showCopyLocal: false,
            tone: "warning",
          });
          this.setState("failed");
        })
        .finally(() => {
          this.inflightSavePromise = null;
        });

      await this.inflightSavePromise;
    };

    await runSave(snapshot, version, true);
  }

  private statusDetailMessage(): string {
    if (this.statusDetailOverride) {
      return this.statusDetailOverride;
    }
    if (this.freshnessState === "remote_newer") {
      return "Newer version exists on another session";
    }
    if (this.state === "clean") {
      return this.freshnessState === "unknown"
        ? "Saved, but unable to confirm whether this note is current"
        : "Saved and current";
    }
    if (this.state === "failed") {
      return this.retryTimer !== null
        ? "Retrying save shortly"
        : "Save failed, the session expired, or currentness could not be confirmed";
    }
    if (this.state === "conflicted") {
      return "Another session saved a newer version. Reload latest or copy local draft.";
    }
    if (this.state === "saving") {
      return "Saving and checking currentness";
    }
    if (this.state === "dirty_during_save") {
      return "Saving now with newer local edits still pending";
    }
    return this.freshnessState === "unknown"
      ? "Unsaved changes and temporarily unable to confirm currentness"
      : "Unsaved changes";
  }

  private statusTone(): StatusTone {
    if (this.state === "conflicted") {
      return "red";
    }
    if (this.state === "failed") {
      return this.retryTimer !== null ? "yellow" : "red";
    }
    if (this.freshnessState === "remote_newer") {
      return "red";
    }
    if (this.state === "clean" && this.freshnessState === "current") {
      return "green";
    }
    return "yellow";
  }

  private statusKind(): string {
    if (this.state === "conflicted") {
      return "conflicted";
    }
    if (this.state === "failed") {
      return this.retryTimer !== null ? "retry" : "failed";
    }
    if (this.freshnessState === "remote_newer") {
      return "remote-newer";
    }
    if (this.state === "clean") {
      return this.freshnessState === "unknown" ? "saved-unconfirmed" : "saved";
    }
    if (this.state === "saving") {
      return "saving";
    }
    return "dirty";
  }

  private updateHiddenBodyValue(): void {
    this.options.hiddenBodyInput.value = JSON.stringify(
      this.options.editor.getJSON(),
    );
  }
}

export function initNoteEditorDocument(
  doc: Document = document,
): NoteSaveController | null {
  const root = doc.querySelector<HTMLElement>("[data-note-editor-root]");
  if (!root || root.dataset.initialized === "true") {
    return null;
  }

  const inputId = root.dataset.noteInputId;
  const documentId = root.dataset.noteDocumentId;
  const schemaVersion = Number(root.dataset.noteSchemaVersion ?? "0");
  if (!inputId || !documentId || !canInitializeNoteEditor(schemaVersion)) {
    return null;
  }

  const hiddenInput = doc.getElementById(inputId) as HTMLInputElement | null;
  const documentNode = doc.getElementById(documentId);
  const form = root.closest("form");
  const toolbar = form?.querySelector<HTMLElement>("[data-note-toolbar]");
  const toolbarToggle = form?.querySelector<HTMLButtonElement>(
    "[data-note-toolbar-toggle]",
  );
  const versionInput = form?.querySelector<HTMLInputElement>(
    'input[name="version"]',
  );
  const saveButton = form?.querySelector<HTMLButtonElement>(
    "[data-note-save-button]",
  );
  const statusRegion = form?.querySelector<HTMLElement>(
    "[data-note-save-status]",
  );
  // The note-action overflow
  // (Print/Export/Download/Duplicate/Open in new tab) lives in the note-workspace
  // title-bar action group, outside `<form data-note-form>` -- these two are
  // scoped to the whole document rather than the form for that reason, unlike
  // every other element below, which remains inside the form.
  const printLinks = [
    ...doc.querySelectorAll<HTMLAnchorElement>("[data-note-print-link]"),
  ];
  const exportLinks = [
    ...doc.querySelectorAll<HTMLAnchorElement>("[data-note-export-link]"),
  ];
  const noticeRegion = form?.querySelector<HTMLElement>(
    "[data-note-save-notice]",
  );
  const noticeActions = form?.querySelector<HTMLElement>(
    "[data-note-notice-actions]",
  );
  const noticeText = form?.querySelector<HTMLElement>(
    "[data-note-save-notice-text]",
  );
  const reloadButton = form?.querySelector<HTMLButtonElement>(
    "[data-note-reload-latest]",
  );
  const copyLocalButton = form?.querySelector<HTMLButtonElement>(
    "[data-note-copy-local]",
  );
  if (
    !hiddenInput ||
    !documentNode ||
    !toolbar ||
    !toolbarToggle ||
    !(form instanceof HTMLFormElement) ||
    !versionInput ||
    !saveButton ||
    !statusRegion ||
    printLinks.length === 0 ||
    exportLinks.length === 0 ||
    !noticeRegion ||
    !noticeActions ||
    !noticeText ||
    !reloadButton ||
    !copyLocalButton
  ) {
    return null;
  }

  const initialDocument = JSON.parse(documentNode.textContent ?? "{}");
  const editor = new Editor({
    element: root,
    content: initialDocument,
    extensions: buildEditorExtensions(),
    onUpdate: () => controller.handleDocumentEdited(),
  });

  const titleInputId = form.dataset.noteTitleInputId;
  const titleInput = titleInputId
    ? (doc.getElementById(titleInputId) as HTMLInputElement | null)
    : null;
  const isImmediateNewPage = form.dataset.noteIsImmediateNewPage === "true";
  const generatedTitle = form.dataset.noteGeneratedTitle ?? "";
  const autosaveUrl = form.dataset.noteAutosaveUrl;
  const freshnessUrl = form.dataset.noteFreshnessUrl;
  const printUrl = form.dataset.notePrintUrl;
  const detailUrl = form.dataset.noteDetailUrl;
  const csrfToken =
    form.querySelector<HTMLInputElement>('input[name="csrfmiddlewaretoken"]')
      ?.value ?? "";

  if (
    !titleInput ||
    !autosaveUrl ||
    !freshnessUrl ||
    !printUrl ||
    !detailUrl ||
    !csrfToken
  ) {
    editor.destroy();
    return null;
  }

  installFormattingToolbarToggle(toolbarToggle, toolbar);
  installEditorExitGesture(root);

  installOneTimeGeneratedTitleSelection(titleInput, {
    isImmediateNewPage,
    generatedTitle,
  });

  if (
    shouldAutoFocusNewNoteBody({
      isImmediateNewPage,
      navigationType: getNavigationType(window),
    })
  ) {
    focusEditorBody(editor, window);
  }

  const navigationGuardTargets: NavGuardTargetLike[] = [
    ...doc.querySelectorAll<HTMLElement>(".account-nav a"),
    ...form.querySelectorAll<HTMLElement>(
      "a[href]:not([data-note-print-link]):not([data-note-export-link]):not([data-note-download-link]):not([data-note-download-markdown-link])",
    ),
  ];
  const logoutForm = doc.querySelector<HTMLFormElement>(".logout-form");
  if (logoutForm) {
    navigationGuardTargets.push(logoutForm);
  }

  const currentNoteId = parseNoteIdFromDetailUrl(detailUrl);

  const controller = new NoteSaveController({
    autosaveUrl,
    copyLocalButton,
    copyTextFn: (text) =>
      copyTextWithFallback(text, {
        documentLike: document as unknown as CopyDocumentLike,
        navigatorLike: window.navigator as NavigatorLike,
      }),
    csrfToken,
    detailUrl,
    documentEvents: doc,
    editor,
    exportLinks,
    fetchFn: (input, init) =>
      window.fetch(input, init) as Promise<FetchResponseLike>,
    form,
    freshnessUrl,
    hiddenBodyInput: hiddenInput,
    locationAssign: (url) => window.location.assign(url),
    navigationGuardTargets,
    noticeActions,
    noticeRegion,
    noticeText,
    onTitleSaved: currentNoteId
      ? (title) => syncTreeNoteTitle(doc, currentNoteId, title)
      : undefined,
    openBlankTab: () => window.open("", "_blank"),
    printLinks,
    printUrl,
    reloadButton,
    replaceEditorDocument: (bodyJson) => {
      (
        editor as unknown as {
          commands: {
            setContent(content: unknown, emitUpdate?: boolean): void;
          };
        }
      ).commands.setContent(bodyJson, false);
    },
    saveButton,
    scheduler: {
      clearTimeout: window.clearTimeout.bind(window),
      setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
    },
    statusRegion,
    titleInput,
    toolbar,
    toolbarToggle,
    versionInput,
    windowEvents: window,
    confirmFn: (message) => window.confirm(message),
  });

  toolbar.addEventListener("click", (event) => {
    // A click landing on an icon button's
    // nested `<svg>` (or one of its child paths/lines) makes `event.target`
    // that inner element, not the `<button>` itself -- `closest("button")`
    // resolves up to the actual button regardless of which descendant node
    // was hit; a direct `instanceof HTMLButtonElement`
    // check would silently miss the click and never dispatch the command.
    const target = event.target;
    if (!(target instanceof Element)) {
      return;
    }
    const button = target.closest("button");
    if (!button || !toolbar.contains(button) || button.disabled) {
      return;
    }
    const command = button.dataset.noteCommand as ToolbarCommand | undefined;
    if (!command) {
      return;
    }
    event.preventDefault();
    const level = Number(button.dataset.noteLevel ?? "0");
    runToolbarCommand(editor, { command, level }, (message, defaultValue) =>
      window.prompt(message, defaultValue),
    );
  });

  root.dataset.initialized = "true";
  return controller;
}
