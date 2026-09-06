import { readFileSync } from "node:fs";
import { resolve } from "node:path";

import { JSDOM } from "jsdom";
import { describe, expect, it } from "vitest";

/**
 * A unit test
 * exercising only the exported TS helper functions (as
 * `tree-scroll-position.test.ts` and `tree-active-row-scroll.test.ts` do)
 * cannot catch a bug in the inline anti-flash bootstrap script itself --
 * that script is raw template markup, never imported or type-checked, and
 * its correctness depends on real HTML-parser timing that a plain
 * function call can't reproduce. An earlier version of this script
 * placed itself as the viewport's *previous sibling* and relied on
 * `document.currentScript.nextElementSibling`; every unit test for the
 * exported functions passed, yet the actual bootstrap silently no-opped
 * in every real browser, because a non-deferred `<script>` executes the
 * instant the parser reaches it -- before the parser has read any
 * markup that comes *after* it in the source, so `nextElementSibling`
 * was always `null` at that point.
 *
 * This test builds a full HTML document string and parses it via
 * `new JSDOM(html, { runScripts: "dangerously" })`, which -- like a real
 * browser's initial network-stream parse -- executes each inline
 * `<script>` synchronously the instant the parser reaches it, reproducing
 * genuine parser timing. `sessionStorage` is seeded via the `beforeParse`
 * hook, which runs after the `window`/`document` exist but *before* HTML
 * parsing (and therefore script execution) begins.
 *
 * Deliberately NOT built via `element.insertAdjacentHTML()` after the
 * document already exists: per the HTML spec (and confirmed by jsdom),
 * `<script>` elements created via `innerHTML`/`insertAdjacentHTML` are
 * marked "already started" and never execute, even with
 * `runScripts: "dangerously"` -- that only governs scripts encountered by
 * the actual document parser. An earlier draft of this test used
 * `insertAdjacentHTML` and every assertion failed, including for the
 * already-fixed current script -- a false positive that would have
 * mistakenly reported the real production fix as broken. This full-parse
 * approach is the one that actually reproduces the real page-load path.
 */

function readInlineScript(): string {
  const templatePath = resolve(
    __dirname,
    "../../../../notes/templates/notes/_tree_nav.html",
  );
  const source = readFileSync(templatePath, "utf-8");
  const match = source.match(
    /<script>\(function\(\)\{try\{var viewport=document\.currentScript\.parentElement;.*?<\/script>/,
  );
  if (!match) {
    throw new Error(
      "Could not find the scroll-restore bootstrap script in _tree_nav.html -- has it moved or changed shape?",
    );
  }
  return match[0];
}

function buildTreeNavDocument(options: {
  scope: string;
  script: string;
}): string {
  const rows = '<ul class="tree-nav__list"><li>a real note row</li></ul>';
  const viewport = `<div class="tree-nav__viewport">${rows}${options.script}</div>`;
  const treeNav = `<div class="tree-nav" data-tree-scope="${options.scope}">${viewport}</div>`;
  return `<!doctype html><html><body>${treeNav}</body></html>`;
}

// The exact script this project previously shipped:
// `document.currentScript.nextElementSibling`, placed as the
// viewport's immediate *previous* sibling in `_tree_nav.html` at that
// time. Hardcoded here (it no longer exists anywhere in the current
// codebase to import) purely so this regression test can prove, via real
// jsdom parser execution, that this exact historical shape really was
// broken -- not a hypothetical read of the parser spec.
const HISTORICAL_SIBLING_BASED_SCRIPT =
  "<script>(function(){try{var container=document.currentScript.closest(" +
  '".tree-nav");var viewport=document.currentScript.nextElementSibling;' +
  'if(!container||!viewport){return;}var key="ridgenote.tree.scrollTop."+' +
  '(container.getAttribute("data-tree-scope")==="drawer"?"drawer":"wide");' +
  "var raw=window.sessionStorage.getItem(key);var value=(raw===null||raw." +
  'trim()==="")?NaN:Number(raw);if(Number.isFinite(value)&&value>=0){' +
  "viewport.scrollTop=value;}}catch(e){}})();</script>";

function buildHistoricalSiblingPlacementDocument(scope: string): string {
  const rows = '<ul class="tree-nav__list"><li>a real note row</li></ul>';
  const viewport = `<div class="tree-nav__viewport">${rows}</div>`;
  const treeNav = `<div class="tree-nav" data-tree-scope="${scope}">${HISTORICAL_SIBLING_BASED_SCRIPT}${viewport}</div>`;
  return `<!doctype html><html><body>${treeNav}</body></html>`;
}

function parseWithSessionStorage(
  html: string,
  seed: Record<string, string>,
): JSDOM {
  return new JSDOM(html, {
    runScripts: "dangerously",
    url: "https://example.test/",
    beforeParse(window) {
      for (const [key, value] of Object.entries(seed)) {
        window.sessionStorage.setItem(key, value);
      }
    },
  });
}

function viewportOf(dom: JSDOM): { scrollTop: number } {
  return dom.window.document.querySelector(
    ".tree-nav__viewport",
  ) as unknown as { scrollTop: number };
}

describe("scroll-restore bootstrap script (real jsdom parser execution)", () => {
  it("restores the wide desktop viewport's scrollTop at its real (last-child) placement", () => {
    const dom = parseWithSessionStorage(
      buildTreeNavDocument({ scope: "tree", script: readInlineScript() }),
      { "ridgenote.tree.scrollTop.wide": "340" },
    );

    expect(viewportOf(dom).scrollTop).toBe(340);
  });

  it("restores the narrow drawer viewport's scrollTop independently under its own key", () => {
    const dom = parseWithSessionStorage(
      buildTreeNavDocument({ scope: "drawer", script: readInlineScript() }),
      { "ridgenote.tree.scrollTop.drawer": "90" },
    );

    expect(viewportOf(dom).scrollTop).toBe(90);
  });

  it("uses the same stable wide key across every page type (tree, allnotes, trash)", () => {
    // Whichever page the user navigates *to* next, the destination page's
    // desktop tree copy must read the exact same key the previous page's
    // capture wrote -- verified here with real script execution, not just
    // the exported `treeScrollStorageKeyForContainer` helper in isolation.
    for (const scope of ["tree", "allnotes", "trash"]) {
      const dom = parseWithSessionStorage(
        buildTreeNavDocument({ scope, script: readInlineScript() }),
        { "ridgenote.tree.scrollTop.wide": "215" },
      );

      expect(viewportOf(dom).scrollTop).toBe(215);
    }
  });

  it("keeps the drawer key independent even when both keys are present", () => {
    const dom = parseWithSessionStorage(
      buildTreeNavDocument({ scope: "drawer", script: readInlineScript() }),
      {
        "ridgenote.tree.scrollTop.wide": "50",
        "ridgenote.tree.scrollTop.drawer": "300",
      },
    );

    expect(viewportOf(dom).scrollTop).toBe(300);
  });

  it("does nothing (stays at 0) when no value is stored", () => {
    const dom = parseWithSessionStorage(
      buildTreeNavDocument({ scope: "tree", script: readInlineScript() }),
      {},
    );

    expect(viewportOf(dom).scrollTop).toBe(0);
  });

  it("fails safe (stays at 0) on a malformed stored value", () => {
    const dom = parseWithSessionStorage(
      buildTreeNavDocument({ scope: "tree", script: readInlineScript() }),
      { "ridgenote.tree.scrollTop.wide": "not-a-number" },
    );

    expect(viewportOf(dom).scrollTop).toBe(0);
  });

  it("fails safe (stays at 0) on a stale negative stored value", () => {
    const dom = parseWithSessionStorage(
      buildTreeNavDocument({ scope: "tree", script: readInlineScript() }),
      { "ridgenote.tree.scrollTop.wide": "-5" },
    );

    expect(viewportOf(dom).scrollTop).toBe(0);
  });

  it("regression guard: the previously-shipped nextElementSibling-based script really was broken", () => {
    // Proves the actual historical root cause with real jsdom parser
    // execution, not a hypothetical read of the parser spec: the exact
    // script this project previously shipped, at the exact
    // placement later found broken (script as the
    // viewport's previous sibling), really does find a `null` viewport
    // under genuine parser timing and leaves scrollTop at 0.
    const dom = parseWithSessionStorage(
      buildHistoricalSiblingPlacementDocument("tree"),
      { "ridgenote.tree.scrollTop.wide": "340" },
    );

    expect(viewportOf(dom).scrollTop).toBe(0);
  });

  it("confirms the current script fixes exactly the scenario the historical one failed", () => {
    // Same stored value, same starting HTML shape -- only the script's
    // own placement/lookup strategy differs -- yet the current one
    // succeeds where the historical one silently failed.
    const dom = parseWithSessionStorage(
      buildTreeNavDocument({ scope: "tree", script: readInlineScript() }),
      { "ridgenote.tree.scrollTop.wide": "340" },
    );

    expect(viewportOf(dom).scrollTop).toBe(340);
  });
});
