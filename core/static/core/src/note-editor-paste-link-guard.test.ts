// @vitest-environment jsdom
import { Editor } from "@tiptap/core";
import { afterEach, describe, expect, it } from "vitest";

import { buildEditorExtensions } from "./note-editor";

/**
 * Regression coverage. Real `Editor` instances (jsdom,
 * real ProseMirror view) are used rather than calling `linkifyjs` directly,
 * so these prove what actually enters editor state via Tiptap's
 * `Link.addPasteRules()` paste-rule path -- a mechanism
 * untouched by `autolink: false` / `linkOnPaste: false`.
 *
 * Root defect: `linkifyjs`'s bracket-balancing scanner can extend a matched
 * "URL" across a trailing `](...)` sequence, producing a syntactically
 * invalid href for pasted `[URL](URL)`-shaped plain text (a Markdown link
 * whose label is itself the URL). The fix gates every autolink candidate
 * through `Link.configure({ isAllowedUri })`, composed as the extension's
 * own default protocol policy AND `new URL(candidate)` parsing successfully.
 */

let liveEditors: Editor[] = [];

function mountEditor(): Editor {
  const el = document.createElement("div");
  document.body.appendChild(el);
  const editor = new Editor({
    element: el,
    extensions: buildEditorExtensions(),
    content: { type: "doc", content: [{ type: "paragraph" }] },
  });
  liveEditors.push(editor);
  return editor;
}

afterEach(() => {
  for (const editor of liveEditors) {
    editor.destroy();
  }
  liveEditors = [];
  document.body.innerHTML = "";
});

function pasteText(editor: Editor, text: string): void {
  const event = new Event("paste", {
    bubbles: true,
    cancelable: true,
  }) as Event & {
    clipboardData?: unknown;
  };
  event.clipboardData = {
    getData: (type: string) => (type === "text/plain" ? text : ""),
    types: ["text/plain"],
    files: [],
  };
  editor.view.dom.dispatchEvent(event);
}

interface LinkMarkInfo {
  text: string;
  href: string;
}

function linkMarksIn(editor: Editor): LinkMarkInfo[] {
  const marks: LinkMarkInfo[] = [];
  editor.state.doc.descendants((node) => {
    if (!node.isText) return;
    for (const mark of node.marks) {
      if (mark.type.name === "link") {
        marks.push({ text: node.text ?? "", href: mark.attrs.href });
      }
    }
  });
  return marks;
}

describe("pasted Markdown-link autolink guard: reproduction matrix", () => {
  it("A. known failing case: [URL](URL) is not linked, and visible text is preserved exactly", () => {
    const editor = mountEditor();
    const pasted =
      "[http://caldavsynchronizer.org](http://caldavsynchronizer.org)";

    pasteText(editor, pasted);

    expect(editor.getText()).toBe(pasted);
    expect(linkMarksIn(editor)).toEqual([]);
  });

  it("B. [label](URL) with a non-URL label still links the URL correctly, no malformed href", () => {
    const editor = mountEditor();

    pasteText(editor, "[Project Homepage](http://caldavsynchronizer.org)");

    expect(editor.getText()).toBe(
      "[Project Homepage](http://caldavsynchronizer.org)",
    );
    expect(linkMarksIn(editor)).toEqual([
      {
        text: "http://caldavsynchronizer.org",
        href: "http://caldavsynchronizer.org",
      },
    ]);
  });

  it("C. bare http URL still autolinks correctly", () => {
    const editor = mountEditor();

    pasteText(editor, "http://caldavsynchronizer.org");

    expect(linkMarksIn(editor)).toEqual([
      {
        text: "http://caldavsynchronizer.org",
        href: "http://caldavsynchronizer.org",
      },
    ]);
  });

  it("D. bare https URL still autolinks correctly", () => {
    const editor = mountEditor();

    pasteText(editor, "https://example.com");

    expect(linkMarksIn(editor)).toEqual([
      { text: "https://example.com", href: "https://example.com" },
    ]);
  });

  it("E. URL followed by ordinary punctuation still links only the URL portion", () => {
    const editor = mountEditor();

    pasteText(editor, "https://example.com.");

    expect(editor.getText()).toBe("https://example.com.");
    expect(linkMarksIn(editor)).toEqual([
      { text: "https://example.com", href: "https://example.com" },
    ]);
  });

  it("F. URL inside ordinary parentheses still links correctly", () => {
    const editor = mountEditor();

    pasteText(editor, "(https://example.com)");

    expect(editor.getText()).toBe("(https://example.com)");
    expect(linkMarksIn(editor)).toEqual([
      { text: "https://example.com", href: "https://example.com" },
    ]);
  });

  it("G. two bare URLs on one line both link correctly", () => {
    const editor = mountEditor();

    pasteText(editor, "https://example.com https://openai.com");

    expect(linkMarksIn(editor)).toEqual([
      { text: "https://example.com", href: "https://example.com" },
      { text: "https://openai.com", href: "https://openai.com" },
    ]);
  });

  it("H. bracketed non-URL text is unaffected", () => {
    const editor = mountEditor();

    pasteText(editor, "[not a link]");

    expect(editor.getText()).toBe("[not a link]");
    expect(linkMarksIn(editor)).toEqual([]);
  });

  it("I. URL followed only by a trailing bracket (no second parenthesized URL) still links correctly", () => {
    const editor = mountEditor();

    pasteText(editor, "[http://caldavsynchronizer.org]");

    expect(linkMarksIn(editor)).toEqual([
      {
        text: "http://caldavsynchronizer.org",
        href: "http://caldavsynchronizer.org",
      },
    ]);
  });

  it("J. same malformed shape without the leading bracket is also rejected", () => {
    const editor = mountEditor();
    const pasted =
      "http://caldavsynchronizer.org](http://caldavsynchronizer.org)";

    pasteText(editor, pasted);

    expect(editor.getText()).toBe(pasted);
    expect(linkMarksIn(editor)).toEqual([]);
  });

  it("K. two independent [URL](URL)-shaped links on one line are both rejected, with no cross-contamination", () => {
    const editor = mountEditor();
    const pasted =
      "[http://a.com](http://a.com) and [http://b.com](http://b.com)";

    pasteText(editor, pasted);

    expect(editor.getText()).toBe(pasted);
    expect(linkMarksIn(editor)).toEqual([]);
  });

  it("L. https variant of the known failing case is also rejected", () => {
    const editor = mountEditor();
    const pasted =
      "[https://caldavsynchronizer.org](https://caldavsynchronizer.org)";

    pasteText(editor, pasted);

    expect(editor.getText()).toBe(pasted);
    expect(linkMarksIn(editor)).toEqual([]);
  });
});

describe("pasted Markdown-link autolink guard: manual link commands remain unaffected", () => {
  it("setLink still accepts an ordinary valid href", () => {
    const editor = mountEditor();
    editor.commands.insertContent("hello");
    editor.commands.selectAll();

    const applied = editor.commands.setLink({ href: "https://example.com" });

    expect(applied).toBe(true);
    expect(linkMarksIn(editor)).toEqual([
      { text: "hello", href: "https://example.com" },
    ]);
  });

  it("toggleLink still accepts an ordinary valid href", () => {
    const editor = mountEditor();
    editor.commands.insertContent("hello");
    editor.commands.selectAll();

    const applied = editor.commands.toggleLink({ href: "https://example.com" });

    expect(applied).toBe(true);
    expect(linkMarksIn(editor)).toEqual([
      { text: "hello", href: "https://example.com" },
    ]);
  });

  it("setLink rejects the same malformed href the paste guard rejects, rather than silently accepting it", () => {
    const editor = mountEditor();
    editor.commands.insertContent("hello");
    editor.commands.selectAll();

    const applied = editor.commands.setLink({
      href: "http://caldavsynchronizer.org](http://caldavsynchronizer.org)",
    });

    expect(applied).toBe(false);
    expect(linkMarksIn(editor)).toEqual([]);
  });
});
