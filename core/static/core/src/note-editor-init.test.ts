// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import { initNoteEditorDocument } from "./note-editor";

/**
 * Regression coverage: the note-action overflow (Print/Export/Download/
 * Duplicate/Open in new tab) lives in the note-workspace title-bar action
 * group, outside `<form data-note-form>`.
 * `[data-note-print-link]`/`[data-note-export-link]` are therefore not
 * reachable via `form.querySelectorAll(...)` -- looking them up that way
 * would silently return an empty list, tripping the
 * `printLinks.length === 0 || exportLinks.length === 0` early-return guard
 * and aborting Tiptap mount entirely, with no console error, leaving the
 * note body rendered but never clickable, selectable, or typable. This
 * suite builds the actual current page shape (links outside the form) and
 * proves the editor mounts successfully.
 */
function buildDocument(options: { linksInsideForm: boolean }): Document {
  document.body.innerHTML = `
    <nav class="account-nav account-nav--left note-workspace-titlebar">
      ${
        options.linksInsideForm
          ? ""
          : `
            <a data-note-print-link href="/notes/1/print/">Print</a>
            <a data-note-export-link href="/notes/1/export/">Export JSON</a>
          `
      }
    </nav>
    <form data-note-form
          data-note-title-input-id="id_title"
          data-note-generated-title="Untitled"
          data-note-is-immediate-new-page="false"
          data-note-autosave-url="/notes/1/autosave/"
          data-note-freshness-url="/notes/1/freshness/"
          data-note-print-url="/notes/1/print/"
          data-note-detail-url="/notes/1/">
      <input type="hidden" name="csrfmiddlewaretoken" value="test-token">
      <input type="hidden" name="version" value="1">
      <input type="text" id="id_title" value="Untitled">
      <input type="hidden" id="id_body_json" value="">
      <button type="submit" data-note-save-button>Save</button>
      <span data-note-save-status></span>
      <button type="button" data-note-toolbar-toggle aria-controls="toolbar"></button>
      <div data-note-toolbar hidden></div>
      ${
        options.linksInsideForm
          ? `
            <a data-note-print-link href="/notes/1/print/">Print</a>
            <a data-note-export-link href="/notes/1/export/">Export JSON</a>
          `
          : ""
      }
      <div data-note-save-notice hidden>
        <p data-note-save-notice-text></p>
        <div data-note-notice-actions hidden>
          <button type="button" data-note-copy-local hidden>Copy local draft</button>
          <button type="button" data-note-reload-latest hidden>Reload latest</button>
        </div>
      </div>
      <div data-note-editor-root
           data-note-input-id="id_body_json"
           data-note-document-id="note-document-1"
           data-note-schema-version="1"></div>
      <script type="application/json" id="note-document-1">${JSON.stringify({
        type: "doc",
        content: [{ type: "paragraph" }],
      })}</script>
    </form>
  `;
  return document;
}

describe("initNoteEditorDocument", () => {
  it("mounts the editor when print/export links live in the title-bar action group, outside the form", () => {
    const doc = buildDocument({ linksInsideForm: false });

    initNoteEditorDocument(doc);

    const root = doc.querySelector<HTMLElement>("[data-note-editor-root]");
    expect(root?.dataset.initialized).toBe("true");
    expect(root?.querySelector(".ProseMirror")).not.toBeNull();
  });

  it("still mounts the editor when print/export links live inside the form (back-compat)", () => {
    const doc = buildDocument({ linksInsideForm: true });

    initNoteEditorDocument(doc);

    const root = doc.querySelector<HTMLElement>("[data-note-editor-root]");
    expect(root?.dataset.initialized).toBe("true");
    expect(root?.querySelector(".ProseMirror")).not.toBeNull();
  });

  it("does not throw and leaves the editor unmounted when required elements are missing entirely", () => {
    document.body.innerHTML = `
      <div data-note-editor-root
           data-note-input-id="id_body_json"
           data-note-document-id="note-document-1"
           data-note-schema-version="1"></div>
    `;

    expect(() => initNoteEditorDocument(document)).not.toThrow();
    const root = document.querySelector<HTMLElement>("[data-note-editor-root]");
    expect(root?.dataset.initialized).not.toBe("true");
  });
});

/**
 * Regression coverage for nested-SVG click-delegation. The toolbar's real
 * markup nests an inline `<svg>` (with its own
 * child `<path>`/`<line>` elements) inside each converted icon button; a
 * click landing anywhere inside that nested content must still resolve to
 * the enclosing `<button>` and dispatch its `data-note-command`, exactly as
 * a click on the button's own background already did before conversion.
 */
function buildToolbarDocument(): Document {
  document.body.innerHTML = `
    <form data-note-form
          data-note-title-input-id="id_title"
          data-note-generated-title="Untitled"
          data-note-is-immediate-new-page="false"
          data-note-autosave-url="/notes/1/autosave/"
          data-note-freshness-url="/notes/1/freshness/"
          data-note-print-url="/notes/1/print/"
          data-note-detail-url="/notes/1/">
      <input type="hidden" name="csrfmiddlewaretoken" value="test-token">
      <input type="hidden" name="version" value="1">
      <input type="text" id="id_title" value="Untitled">
      <input type="hidden" id="id_body_json" value="">
      <button type="submit" data-note-save-button>Save</button>
      <span data-note-save-status></span>
      <button type="button" data-note-toolbar-toggle aria-controls="toolbar"></button>
      <a data-note-print-link href="/notes/1/print/">Print</a>
      <a data-note-export-link href="/notes/1/export/">Export JSON</a>
      <div data-note-toolbar hidden>
        <button type="button" class="icon-button" data-note-command="bulletList" aria-label="Bulleted list" data-tooltip="Bulleted list">
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M8 5h13"/></svg>
        </button>
        <button type="button" class="icon-button" data-note-command="undo" aria-label="Undo" data-tooltip="Undo" disabled>
          <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false"><path d="M9 14 4 9l5-5"/></svg>
        </button>
        <button type="button" data-note-command="heading" data-note-level="1">H1</button>
      </div>
      <div data-note-save-notice hidden>
        <p data-note-save-notice-text></p>
        <div data-note-notice-actions hidden>
          <button type="button" data-note-copy-local hidden>Copy local draft</button>
          <button type="button" data-note-reload-latest hidden>Reload latest</button>
        </div>
      </div>
      <div data-note-editor-root
           data-note-input-id="id_body_json"
           data-note-document-id="note-document-1"
           data-note-schema-version="1"></div>
      <script type="application/json" id="note-document-1">${JSON.stringify({
        type: "doc",
        content: [{ type: "paragraph" }],
      })}</script>
    </form>
  `;
  return document;
}

describe("toolbar delegated click handling (nested SVG)", () => {
  it("dispatches the command when the click lands on the button's own background", () => {
    const doc = buildToolbarDocument();
    initNoteEditorDocument(doc);
    const root = doc.querySelector<HTMLElement>("[data-note-editor-root]");
    const button = doc.querySelector<HTMLButtonElement>(
      '[data-note-command="bulletList"]',
    )!;

    button.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(root?.querySelector(".ProseMirror ul")).not.toBeNull();
  });

  it("dispatches the command when the click lands on the button's nested svg", () => {
    const doc = buildToolbarDocument();
    initNoteEditorDocument(doc);
    const root = doc.querySelector<HTMLElement>("[data-note-editor-root]");
    const svg = doc.querySelector<SVGElement>(
      '[data-note-command="bulletList"] svg',
    )!;

    svg.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(root?.querySelector(".ProseMirror ul")).not.toBeNull();
  });

  it("dispatches the command when the click lands on the button's nested svg path", () => {
    const doc = buildToolbarDocument();
    initNoteEditorDocument(doc);
    const root = doc.querySelector<HTMLElement>("[data-note-editor-root]");
    const path = doc.querySelector<SVGElement>(
      '[data-note-command="bulletList"] path',
    )!;

    path.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(root?.querySelector(".ProseMirror ul")).not.toBeNull();
  });

  it("does not dispatch a duplicate command for one click", () => {
    const doc = buildToolbarDocument();
    initNoteEditorDocument(doc);
    const root = doc.querySelector<HTMLElement>("[data-note-editor-root]");
    const path = doc.querySelector<SVGElement>(
      '[data-note-command="bulletList"] path',
    )!;

    path.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(root?.querySelectorAll(".ProseMirror ul").length).toBe(1);
  });

  it("does not dispatch when a disabled button's nested svg is clicked", () => {
    const doc = buildToolbarDocument();
    initNoteEditorDocument(doc);
    const root = doc.querySelector<HTMLElement>("[data-note-editor-root]");
    const bulletButton = doc.querySelector<HTMLButtonElement>(
      '[data-note-command="bulletList"]',
    )!;
    bulletButton.disabled = true;
    const svg = bulletButton.querySelector<SVGElement>("svg")!;

    svg.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(root?.querySelector(".ProseMirror ul")).toBeNull();
  });

  it("does not dispatch for a click on an unrelated toolbar child that is not a button", () => {
    const doc = buildToolbarDocument();
    initNoteEditorDocument(doc);
    const root = doc.querySelector<HTMLElement>("[data-note-editor-root]");
    const toolbar = doc.querySelector<HTMLElement>("[data-note-toolbar]")!;

    toolbar.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(root?.querySelector(".ProseMirror ul")).toBeNull();
  });

  it("still activates a text control (H1) unaffected by the icon-button conversion", () => {
    const doc = buildToolbarDocument();
    initNoteEditorDocument(doc);
    const root = doc.querySelector<HTMLElement>("[data-note-editor-root]");
    const heading = doc.querySelector<HTMLButtonElement>(
      '[data-note-command="heading"]',
    )!;

    heading.dispatchEvent(new MouseEvent("click", { bubbles: true }));

    expect(root?.querySelector(".ProseMirror h1")).not.toBeNull();
  });
});
