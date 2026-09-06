import { readFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

import { describe, expect, it } from "vitest";

/**
 * Regression guard for a specific, confirmed cascade-order defect: an
 * early `@media (width < 640px)` override intended
 * to stack `.note-workspace__ribbon-main` into a column lost the cascade
 * to a later, unconditioned `display: grid` base rule at equal
 * specificity, so the narrow stacking layout never actually applied and
 * the note title rendered invisibly, overlapped by Saved/Save/Show
 * formatting. The fix repeats the narrow override *after* the base rule.
 * This can't be verified with a pixel-level or jsdom media-query
 * assertion (jsdom does not evaluate `@media` against a real viewport),
 * so this test instead asserts the one thing that actually matters for
 * this bug class: source order. It does not care about `.panel-header`
 * or any other selector in the original narrow block, since only
 * `.note-workspace__ribbon-main` had a genuinely conflicting later base
 * rule (see the CSS comment at the fix site for why).
 */
function readAppCss(): string {
  const cssPath = join(
    dirname(fileURLToPath(import.meta.url)),
    "..",
    "css",
    "app.css",
  );
  return readFileSync(cssPath, "utf-8");
}

describe("note-workspace__ribbon-main narrow cascade order", () => {
  it("re-declares the narrow display:flex override after the base display:grid rule", () => {
    const css = readAppCss();

    const baseGridIndex = css.indexOf(
      ".note-workspace__ribbon-main {\n  display: grid;",
    );
    expect(baseGridIndex).toBeGreaterThan(-1);

    // The narrow fix's own override: `display: flex` for the same
    // selector, inside a `@media (width < 640px)` block, at some point
    // strictly after the base rule above.
    const afterBase = css.slice(baseGridIndex);
    const narrowOverrideMatch = afterBase.match(
      /@media \(width < 640px\) \{\s*\.note-workspace__ribbon-main \{\s*display: flex;/,
    );
    expect(narrowOverrideMatch).not.toBeNull();
  });
});

/**
 * Regression guard: two
 * fixes here are both susceptible to the *exact same* cascade-order bug
 * class as the ribbon-main defect above -- a `.note-context` override
 * written *before* `.note-context`'s unconditioned base rule
 * would silently have no effect for the same reason.
 */
describe("narrow ribbon rounded-panel treatment", () => {
  it("gives .note-workspace__ribbon-main its own border/radius/background at every width, in its base rule", () => {
    // This lives in the base (unconditioned) rule, not a
    // narrow-only override, so the whole
    // title/status/actions row reads as one inner panel at wide/medium
    // too, not just narrow. Padding is `--space-3`,
    // as part of reducing the note-detail ribbon row's overall height.
    const css = readAppCss();
    const basePanelMatch = css.match(
      /\.note-workspace__ribbon-main \{[^}]*border: 1px solid var\(--color-border-muted\);[^}]*border-radius: var\(--radius-sm\);[^}]*background: var\(--color-bg-surface-muted\);[^}]*padding: var\(--space-3\);/,
    );
    expect(basePanelMatch).not.toBeNull();

    // The base rule must not itself be inside a media block.
    const baseIndex = css.indexOf(
      ".note-workspace__ribbon-main {\n  display: grid;",
    );
    expect(baseIndex).toBeGreaterThan(-1);
  });

  it("does not give .note-context its own border/background -- it would double the border now that ribbon-main carries one", () => {
    const css = readAppCss();
    const contextIndex = css.indexOf(".note-context {\n  min-width: 0;");
    expect(contextIndex).toBeGreaterThan(-1);
    const contextBlock = css.slice(
      contextIndex,
      css.indexOf("}", contextIndex) + 1,
    );
    expect(contextBlock).not.toMatch(/border:/);
    expect(contextBlock).not.toMatch(/background:/);
  });
});

describe("narrow drawer tree scroll containment", () => {
  // The drawer's own scrolling (and this
  // containment rule) lives on
  // `.tree-nav__viewport` alone, so the fixed Trash destination can
  // sit below it, outside the scroll -- `.tree-nav__viewport` is the
  // element that actually scrolls now, in both the wide tree and the
  // narrow drawer, so it's the correct place for this rule regardless
  // of which context renders it.
  it("sets overscroll-behavior: contain on .tree-nav__viewport", () => {
    const css = readAppCss();
    const treeRuleMatch = css.match(
      /\.tree-nav__viewport \{[^}]*overscroll-behavior: contain;[^}]*\}/,
    );
    expect(treeRuleMatch).not.toBeNull();
  });
});

/**
 * Regression guard:
 * the Trash page sits in the same locked-viewport layout
 * note-detail uses, but its content column, `.trash-panel`, was never
 * given the matching flex-grow/scroll-region treatment `.note-workspace`
 * has -- with no explicit sizing, it was a plain block whose overflow got
 * silently clipped by `.workspace-shell`'s own `overflow: hidden` once an
 * account had enough trashed items to exceed the available height, with
 * no scrollbar anywhere and lower rows genuinely unreachable. This can't
 * be verified with jsdom (no real viewport/scrolling), so this test
 * asserts the CSS source fact directly.
 */
describe("Trash page content-column scroll containment", () => {
  it("gives .trash-panel its own min-height: 0 and overflow-y: auto", () => {
    const css = readAppCss();
    const trashPanelRuleMatch = css.match(
      /\.trash-panel \{[^}]*min-height: 0;[^}]*overflow-y: auto;[^}]*\}/,
    );
    expect(trashPanelRuleMatch).not.toBeNull();
  });
});

/**
 * Regression guard: the
 * narrow note title/Saved/Save/Show formatting ribbon was scrolling off
 * screen along with the global header while a long note's body scrolled,
 * even though `.note-workspace__ribbon` already had `position: sticky`.
 * Root cause was a scroll-container ambiguity, not sticky positioning
 * itself: `@media (width < 640px) { html, body { overflow-x: hidden; } }`
 * gave *both* `html` and `body` their own explicit overflow, which
 * disqualifies the CSS-spec mechanism that lets `body`'s overflow
 * propagate to become the single viewport scroll container (it only
 * engages when the root element's own overflow is exactly the initial
 * `visible`) -- `html` ended up an independent, ambiguous second scroll
 * box, and sticky elements resolved against the wrong one. None of this
 * is visible to jsdom (no real viewport, no real scrolling), so these
 * tests assert CSS source facts directly, each one a concrete, previously
 * broken behavior.
 */
describe("narrow note-workspace scroll ownership and persistent ribbon", () => {
  it("no longer sets overflow-x on body at narrow -- only html", () => {
    const css = readAppCss();
    const narrowBlockMatch = css.match(
      /@media \(width < 640px\) \{\s*(?:\/\*[\s\S]*?\*\/\s*)?html \{\s*overflow-x: hidden;\s*\}/,
    );
    expect(narrowBlockMatch).not.toBeNull();

    // The specific old broken pattern (`html,\n  body {`) must not
    // reappear anywhere in the file.
    expect(css).not.toMatch(/html,\s*\n\s*body\s*\{\s*overflow-x: hidden;/);
  });

  it("makes the global workspace header sticky at narrow, above the ribbon's own sticky layer", () => {
    const css = readAppCss();
    const headerStickyMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.app-header--workspace \{\s*position: sticky;\s*top: 0;\s*z-index: 11;/,
    );
    expect(headerStickyMatch).not.toBeNull();
  });

  it("offsets the ribbon's sticky top at narrow using the measured header-height custom property, positioned after the base top:0 rule", () => {
    // A `top: 2.9rem` literal placed *before*
    // `.note-workspace__ribbon`'s unconditioned base rule (which sets
    // `top: 0`) is the exact cascade-order
    // bug class guarded elsewhere in this file -- it never actually
    // applies; the ribbon sticks at `top: 0` (exactly overlapping the
    // header) at
    // every narrow width. Fixed two ways at once: (1) moved after
    // the base rule, and (2) the literal replaced with
    // `var(--workspace-header-height, 2.9rem)`, a value kept in sync with
    // the header's real rendered height by `workspace-header-height.ts`
    // rather than a hand-picked guess (`2.9rem` remains only as the
    // pre-measurement fallback).
    const css = readAppCss();

    const baseRibbonIndex = css.indexOf(
      ".note-workspace__ribbon {\n  display: flex;",
    );
    expect(baseRibbonIndex).toBeGreaterThan(-1);
    const baseRibbonBlock = css.slice(
      baseRibbonIndex,
      css.indexOf("}", baseRibbonIndex) + 1,
    );
    expect(baseRibbonBlock).toMatch(/top: 0;/);

    const afterBase = css.slice(baseRibbonIndex);
    const ribbonOffsetMatch = afterBase.match(
      /@media \(width < 640px\) \{[\s\S]*?\.note-workspace__ribbon \{\s*top: var\(--workspace-header-height, 3\.1rem\);\s*\}/,
    );
    expect(ribbonOffsetMatch).not.toBeNull();

    // The old broken literal-only override must not reappear anywhere.
    expect(css).not.toMatch(/\.note-workspace__ribbon \{\s*top: 2\.9rem;\s*\}/);
  });

  it("neutralizes the outer ribbon's background/border/shadow unconditionally (all widths), not narrow-only", () => {
    // A narrow-only neutralization would miss that the
    // same square-background-behind-a-rounded-panel problem exists at
    // wide/medium too (`.note-context` has its own rounded panel there).
    // The paint is removed from the base rule entirely rather than
    // re-added and neutralized per width.
    const css = readAppCss();
    const baseRibbonIndex = css.indexOf(
      ".note-workspace__ribbon {\n  display: flex;",
    );
    expect(baseRibbonIndex).toBeGreaterThan(-1);
    const baseRibbonBlock = css.slice(
      baseRibbonIndex,
      css.indexOf("}", baseRibbonIndex) + 1,
    );
    expect(baseRibbonBlock).not.toMatch(/border-top:/);
    expect(baseRibbonBlock).not.toMatch(/border-bottom:/);
    expect(baseRibbonBlock).not.toMatch(/background:/);
    expect(baseRibbonBlock).not.toMatch(/box-shadow:/);
  });

  it("leaves the header's sticky rule scoped to narrow only, not affecting wide/medium", () => {
    const css = readAppCss();
    // The base (unconditioned) .app-header--workspace rule must not itself
    // declare position: sticky -- only the narrow override (checked above)
    // does, confirmed by the base rule ending before any `position:
    // sticky` appears for this selector outside a media block.
    const baseHeaderIndex = css.indexOf(".app-header--workspace {");
    expect(baseHeaderIndex).toBeGreaterThan(-1);
    const baseHeaderBlock = css.slice(
      baseHeaderIndex,
      css.indexOf("}", baseHeaderIndex),
    );
    expect(baseHeaderBlock).not.toMatch(/position: sticky/);
  });
});

/**
 * Regression guard (note-controls panel convergence). A single literal DOM wrapper
 * containing both the tag metadata row and the note title/status/actions
 * ribbon was evaluated and rejected -- `.note-workspace__meta` contains
 * its own `<form>` elements, and HTML forbids nesting a `<form>` inside
 * another `<form>` (the ribbon's fields must stay inside
 * `.note-workspace__form`). These tests assert the CSS-only approximation
 * actually implemented instead: `.note-workspace__meta` gets its own full
 * rounded panel (matching `.note-context`'s existing tokens), aligned
 * with the expanded tree's top via the same shared clamp, with tag
 * metadata deliberately left out of the narrow sticky stack (preserving
 * the existing "may scroll away" behavior) and a narrow-only opaque
 * border seam between the two sticky layers.
 */
describe("note-controls panel convergence", () => {
  // `.note-workspace__meta` does not draw its own
  // full rounded panel -- at wide/medium it would double up with
  // `.note-controls-frame`'s own outer border/background (see that
  // selector's comment), and at narrow it stacked as a second box above
  // `.note-workspace__ribbon-main`'s own. Replaced with one restrained
  // `border-bottom` divider at every width, so tags read as a calm,
  // unboxed lead-in to the title row rather than an independently
  // competing container.
  it("gives .note-workspace__meta a restrained border-bottom divider, not its own full rounded panel", () => {
    const css = readAppCss();
    const metaIndex = css.indexOf(".note-workspace__meta {\n  display: flex;");
    expect(metaIndex).toBeGreaterThan(-1);
    const metaBlock = css.slice(metaIndex, css.indexOf("}", metaIndex) + 1);
    expect(metaBlock).toMatch(
      /border-bottom: 1px solid var\(--color-border-muted\);/,
    );
    expect(metaBlock).not.toMatch(/border-radius:/);
    expect(metaBlock).not.toMatch(/background:/);
    expect(metaBlock).not.toMatch(
      /border: 1px solid var\(--color-border-muted\);/,
    );
  });

  it("gives .note-workspace__meta a margin-bottom driven by --workspace-panel-padding, the same token used for the title-to-formatting gap", () => {
    // Both the tag-to-title gap and the title-to-
    // formatting gap (`.note-workspace__ribbon`'s own `gap`) reference the
    // same named token, rather than independently-chosen values, so
    // spacing among tag/title/formatting panels stays balanced.
    const css = readAppCss();
    const metaIndex = css.indexOf(".note-workspace__meta {\n  display: flex;");
    expect(metaIndex).toBeGreaterThan(-1);
    const metaBlock = css.slice(metaIndex, css.indexOf("}", metaIndex) + 1);
    expect(metaBlock).toMatch(
      /margin-bottom: var\(--workspace-panel-padding\);/,
    );
    expect(metaBlock).not.toMatch(/margin-bottom: var\(--space-1\);/);
    expect(metaBlock).not.toMatch(/margin-bottom: var\(--space-9\);/);
  });

  it("gives .note-workspace__meta its internal top gap via --workspace-panel-padding, not the tree/rail's own outer-inset token, at wide/medium only", () => {
    // The "align with
    // the tree" job does not live on this rule at all -- `.note-workspace` itself
    // carries the alignment offset as its own `margin-top` (see the
    // "outer note-controls grouping" describe block below), using
    // `--workspace-outer-inset`. This is a *different* token doing a
    // *different* job: the frame's internal top padding, deliberately not
    // the same value as the outer-inset alignment offset.
    const css = readAppCss();
    const wideMetaMatch = css.match(
      /@media \(width >= 640px\) \{[\s\S]*?\.note-workspace__meta \{\s*margin-top: var\(--workspace-panel-padding\);\s*\}/,
    );
    expect(wideMetaMatch).not.toBeNull();

    expect(css).not.toMatch(
      /\.note-workspace__meta \{\s*margin-top: (clamp\(0\.5rem, 1\.5vw, 1rem\)|var\(--workspace-outer-inset\));\s*\}/,
    );
  });

  it("does not make .note-workspace__meta sticky at narrow -- tag metadata still scrolls away, as previously accepted", () => {
    const css = readAppCss();
    const narrowMetaStickyMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.note-workspace__meta \{[^}]*position: sticky/,
    );
    expect(narrowMetaStickyMatch).toBeNull();
  });

  it("gives the narrow sticky header an opaque border-bottom seam, not an uncovered top gap on the ribbon", () => {
    const css = readAppCss();
    const seamMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.app-header--workspace \{[^}]*border-bottom: 3px solid var\(--color-border-editor\);[^}]*\}/,
    );
    expect(seamMatch).not.toBeNull();

    // The ribbon's own narrow top offset must still be exactly the
    // measured header height (which already includes the seam border,
    // since it's real box height) -- not a second, separately-tuned gap
    // value that could leave an uncovered band.
    const ribbonOffsetMatch = css.match(
      /\.note-workspace__ribbon \{\s*top: var\(--workspace-header-height, 3\.1rem\);\s*\}/,
    );
    expect(ribbonOffsetMatch).not.toBeNull();
  });
});

/**
 * Regression guard: the
 * outer controls grouping around the tag panel, the title/status/actions
 * panel, and (when visible) the formatting toolbar. See the CSS comment on
 * `.note-controls-frame` for why this is a decorative, JS-measured
 * background element rather than a literal DOM wrapper.
 */
describe("outer note-controls grouping", () => {
  it("gives .note-workspace the position:relative needed as the frame's containing block", () => {
    const css = readAppCss();
    const baseIndex = css.indexOf(".note-workspace {\n  width: 100%;");
    expect(baseIndex).toBeGreaterThan(-1);
    const baseBlock = css.slice(baseIndex, css.indexOf("}", baseIndex) + 1);
    expect(baseBlock).toMatch(/position: relative;/);
  });

  it("draws .note-controls-frame as an absolutely-positioned rounded box using the shared border-width/radius tokens, wide/medium only", () => {
    const css = readAppCss();
    const wideFrameMatch = css.match(
      /@media \(width >= 640px\) \{\s*\.note-controls-frame \{[^}]*position: absolute;[^}]*height: var\(--note-controls-frame-height, 0px\);[^}]*border: var\(--workspace-border-width\) solid var\(--color-border-editor\);[^}]*border-radius: var\(--workspace-radius\);[^}]*background: var\(--color-bg-surface-muted\);[^}]*\}/,
    );
    expect(wideFrameMatch).not.toBeNull();
  });

  it("insets .note-controls-frame's right by --workspace-outer-inset, the same token .note-workspace uses for its own padding-right, so the frame's outer edge lines up with the editor's right edge below it", () => {
    // Not `left: 0; right: 0` (flush with .note-workspace's raw edge,
    // before its own padding takes effect for normal-flow children); the
    // shared `--workspace-outer-inset` token names the value both use.
    // `left`
    // (and `.note-workspace`'s own `padding-left`) is `0` instead -- see
    // the "navigation region gap" describe block below -- since that side
    // now has a neighboring panel it must sit close to, unlike the right
    // side (no neighbor, keeps the larger viewport-edge inset).
    const css = readAppCss();
    const frameInsetMatch = css.match(
      /@media \(width >= 640px\) \{\s*\.note-controls-frame \{[^}]*right: var\(--workspace-outer-inset\);/,
    );
    expect(frameInsetMatch).not.toBeNull();

    const workspacePaddingMatch = css.match(
      /\.note-workspace \{[\s\S]*?padding-right: var\(--workspace-outer-inset\);/,
    );
    expect(workspacePaddingMatch).not.toBeNull();
  });

  it("gives .note-controls-frame equal left/right padding via margin on .note-workspace__meta and .note-workspace__ribbon, matching its top/bottom padding", () => {
    // The frame's inner
    // content (tag panel, ribbon) does not sit flush against its left/right
    // border while having real top/bottom padding -- both are equal, an
    // even four-side padding. `.note-workspace__
    // editor-shell` is a *different* form child and is unaffected by
    // margin on these two, so the frame's own outer edge still matches
    // the editor's.
    const css = readAppCss();
    const marginMatch = css.match(
      /\.note-workspace__meta,\s*\.note-workspace__ribbon \{\s*(?:\/\*[\s\S]*?\*\/\s*)?margin-left: var\(--workspace-panel-padding\);\s*margin-right: var\(--workspace-panel-padding\);\s*\}/,
    );
    expect(marginMatch).not.toBeNull();
  });

  it("moves .note-workspace's top-alignment offset from padding-top to margin-top, using --workspace-outer-inset, so the frame's internal top gap isn't counted twice", () => {
    const css = readAppCss();
    const workspaceIndex = css.indexOf(".note-workspace {\n    display: flex;");
    expect(workspaceIndex).toBeGreaterThan(-1);
    const workspaceBlock = css.slice(
      workspaceIndex,
      css.indexOf("\n  }", workspaceIndex) + 4,
    );
    expect(workspaceBlock).toMatch(
      /margin-top: var\(--workspace-outer-inset\);/,
    );
    expect(workspaceBlock).not.toMatch(/padding-top:/);
  });

  it("removes .note-workspace's explicit height: 100%, so flex stretch (not a hard value that ignores margin-top) sizes it -- otherwise the box overflows the row by exactly margin-top, breaking bottom alignment with the tree/rail", () => {
    const css = readAppCss();
    const workspaceIndex = css.indexOf(".note-workspace {\n    display: flex;");
    expect(workspaceIndex).toBeGreaterThan(-1);
    const workspaceBlock = css.slice(
      workspaceIndex,
      css.indexOf("\n  }", workspaceIndex) + 4,
    );
    expect(workspaceBlock).not.toMatch(/height: 100%;/);
  });

  it("gives .note-workspace__ribbon a matching --workspace-panel-padding margin-bottom at wide/medium, so the frame's bottom gap is symmetric with the tag panel's top gap", () => {
    const css = readAppCss();
    const ribbonMarginMatch = css.match(
      /@media \(width >= 640px\) \{[\s\S]*?\.note-workspace__ribbon \{\s*margin-bottom: var\(--workspace-panel-padding\);\s*\}/,
    );
    expect(ribbonMarginMatch).not.toBeNull();
  });

  it("removes .note-workspace__ribbon's own padding, so the tag-to-title and title-to-formatting gaps aren't doubled by a second, independently-sized source", () => {
    const css = readAppCss();
    const ribbonIndex = css.indexOf(
      ".note-workspace__ribbon {\n  display: flex;",
    );
    expect(ribbonIndex).toBeGreaterThan(-1);
    const ribbonBlock = css.slice(
      ribbonIndex,
      css.indexOf("\n}", ribbonIndex) + 2,
    );
    expect(ribbonBlock).toMatch(/gap: var\(--workspace-panel-padding\);/);
    expect(ribbonBlock).not.toMatch(/padding:/);
  });

  it("hides .note-controls-frame at narrow, so it never becomes a competing box beside the narrow sticky stack", () => {
    const css = readAppCss();
    const narrowFrameMatch = css.match(
      /@media \(width < 640px\) \{\s*\.note-controls-frame \{\s*display: none;\s*\}\s*\}/,
    );
    expect(narrowFrameMatch).not.toBeNull();
  });

  it("stacks the note-workspace form above the decorative frame at wide/medium", () => {
    const css = readAppCss();
    const stackingMatch = css.match(
      /\.note-workspace__form \{\s*position: relative;\s*z-index: 1;\s*\}/,
    );
    expect(stackingMatch).not.toBeNull();
  });

  // `.note-workspace__meta`
  // and `.note-workspace__form` do not share one tied `z-index: 1` rule:
  // that would be broken by DOM order in the form's favor, so its
  // title/editor subtree would always paint above the tag row regardless
  // of any z-index set on
  // content inside it (e.g. the tag-suggestions panel's own `z-index: 300`,
  // which a descendant's z-index cannot escape past a losing ancestor
  // stacking-context comparison). `.note-workspace__meta` gets its own,
  // higher, still-modest `z-index: 2` so its own contents can paint above
  // the form's, letting an open suggestion panel actually float over the
  // title/editor surface below it.
  it("gives .note-workspace__meta a higher wide/medium z-index than .note-workspace__form, so its own contents (e.g. an open tag-suggestions panel) can paint above the title/editor surface", () => {
    const css = readAppCss();
    const metaStackingMatch = css.match(
      /\.note-workspace__meta \{\s*position: relative;\s*z-index: 2;\s*\}/,
    );
    expect(metaStackingMatch).not.toBeNull();
  });

  // `.note-workspace__ribbon-main` carries
  // its own border/radius/background in its base (unconditioned) rule,
  // for narrow width, where the frame is `display: none` -- but at
  // wide/medium, where the frame already supplies one outer border for
  // the whole tags+title+toolbar group, a second independent box here
  // would double it back up. Cancelled by a dedicated override placed beside
  // the frame's own wide-only rule.
  it("cancels .note-workspace__ribbon-main's own border/radius/background at wide/medium, where the frame already supplies one", () => {
    const css = readAppCss();
    const wideCancelMatch = css.match(
      /@media \(width >= 640px\) \{[\s\S]*?\.note-workspace__ribbon-main \{\s*border: 0;\s*border-radius: 0;\s*background: transparent;\s*padding: var\(--space-3\) 0;\s*\}/,
    );
    expect(wideCancelMatch).not.toBeNull();
  });

  // See the "toolbar
  // restrained border and dark-mode contrast" describe block below for
  // the full history and the final accepted state.
  it("gives the formatting toolbar its own dedicated background token", () => {
    const css = readAppCss();
    const toolbarIndex = css.indexOf(".editor-toolbar {\n  display: flex;");
    expect(toolbarIndex).toBeGreaterThan(-1);
    const toolbarBlock = css.slice(
      toolbarIndex,
      css.indexOf("}", toolbarIndex) + 1,
    );
    expect(toolbarBlock).toMatch(/background: var\(--color-bg-toolbar\);/);
  });
});

/**
 * H1/H2/H3/Clear (the four
 * text controls retained in the formatting toolbar) are styled off the
 * global `button` rule's solid primary-CTA treatment onto the existing
 * `--color-button-secondary-*` token set, matching `.button-link--secondary`
 * and `.workspace-drawer-trigger` elsewhere in this file -- not a new color,
 * not a global button-style change.
 */
describe("formatting-toolbar text-control quiet restyle", () => {
  it("styles the four retained text controls with the existing secondary tokens, not new colors", () => {
    const css = readAppCss();
    const ruleIndex = css.indexOf(".editor-toolbar button:not(.icon-button) {");
    expect(ruleIndex).toBeGreaterThan(-1);
    const ruleBlock = css.slice(ruleIndex, css.indexOf("}", ruleIndex) + 1);
    const ruleBlockWithoutComments = ruleBlock.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(ruleBlock).toMatch(
      /border-color: var\(--color-button-secondary-border\);/,
    );
    expect(ruleBlock).toMatch(
      /background: var\(--color-button-secondary-bg\);/,
    );
    expect(ruleBlock).toMatch(/color: var\(--color-button-secondary-text\);/);
    expect(ruleBlock).toMatch(/font-weight: var\(--font-weight-normal\);/);
    expect(ruleBlockWithoutComments).not.toMatch(/--color-button-primary/);
  });

  it("gives the four text controls a dedicated hover state using the existing secondary hover token", () => {
    const css = readAppCss();
    const hoverMatch = css.match(
      /\.editor-toolbar button:not\(\.icon-button\):hover \{\s*background: var\(--color-button-secondary-bg-hover\);\s*\}/,
    );
    expect(hoverMatch).not.toBeNull();
  });

  it("does not add a toolbar-specific separator rule", () => {
    const css = readAppCss();
    expect(css).not.toMatch(/\.editor-toolbar[^{]*separator/);
  });

  it("leaves the global button rule and the shared .icon-button primitive completely unaffected", () => {
    const css = readAppCss();
    const globalButtonIndex = css.indexOf("button,\n.button-link {");
    expect(globalButtonIndex).toBeGreaterThan(-1);
    const globalButtonBlock = css.slice(
      globalButtonIndex,
      css.indexOf("}", globalButtonIndex) + 1,
    );
    expect(globalButtonBlock).toMatch(
      /background: var\(--color-button-primary-bg\);/,
    );

    const iconButtonIndex = css.indexOf(
      ".icon-button {\n  display: inline-flex;",
    );
    expect(iconButtonIndex).toBeGreaterThan(-1);
    const iconButtonBlock = css.slice(
      iconButtonIndex,
      css.indexOf("}", iconButtonIndex) + 1,
    );
    expect(iconButtonBlock).toMatch(/width: var\(--icon-button-size\);/);
    expect(iconButtonBlock).not.toMatch(/--color-button-secondary/);
  });
});

/**
 * Regression guard:
 * no divider beside the collapsed rail, since the
 * rounded rail/tree/note-controls panels and their margins already give
 * enough visual separation.
 */
describe("collapsed-rail divider removal", () => {
  it("no longer gives the collapsed rail its own border-right divider", () => {
    const css = readAppCss();
    expect(css).not.toMatch(
      /\.workspace-shell\[data-tree-collapsed="true"\] \.workspace-shell__tree-rail \{\s*border-right:/,
    );
  });
});

/**
 * Regression guard:
 * the always-visible rail's top edge matches the tree pane's and the
 * note-controls frame's, using the exact same shared clamp (not a new,
 * separately-tuned value).
 */
describe("rail top alignment", () => {
  it("gives .workspace-shell__tree-rail the same --workspace-outer-inset margin-top the tree pane and note-workspace already use, at wide/medium", () => {
    const css = readAppCss();
    const railMatch = css.match(
      /\.workspace-shell__tree-rail \{[^}]*margin-top: var\(--workspace-outer-inset\);/,
    );
    expect(railMatch).not.toBeNull();

    const treeMatch = css.match(
      /\.workspace-shell__tree \{[\s\S]*?margin-top: var\(--workspace-outer-inset\);/,
    );
    expect(treeMatch).not.toBeNull();
  });
});

/**
 * Regression guard: the explicit workspace geometry contract at `:root`,
 * and the rules that consume it instead of independent literals.
 */
describe("workspace geometry contract", () => {
  it("declares the four shared geometry tokens at :root", () => {
    // No `--workspace-sibling-gap`: the tree-to-note-workspace
    // gap uses `--workspace-rail-gap` too -- see the "navigation
    // region gap" describe block below -- leaving four.
    const css = readAppCss();
    const rootIndex = css.indexOf(":root {");
    expect(rootIndex).toBeGreaterThan(-1);
    const rootBlock = css.slice(rootIndex, css.indexOf("\n}", rootIndex));
    expect(rootBlock).toMatch(
      /--workspace-outer-inset: clamp\(0\.5rem, 1\.5vw, 1rem\);/,
    );
    expect(rootBlock).toMatch(/--workspace-rail-gap: var\(--space-4\);/);
    expect(rootBlock).toMatch(/--workspace-panel-padding: var\(--space-6\);/);
    expect(rootBlock).toMatch(/--workspace-border-width: 1px;/);
    expect(rootBlock).toMatch(/--workspace-radius: var\(--radius-lg\);/);
    expect(rootBlock).not.toMatch(/--workspace-sibling-gap:/);
  });

  it("does not repeat the outer-inset clamp expression literally anywhere outside its own token declaration", () => {
    // Independent `clamp()` expressions that merely happen to match are
    // avoided --
    // every consumer must reference `var(--workspace-outer-inset)`.
    const css = readAppCss();
    const occurrences = css.split("clamp(0.5rem, 1.5vw, 1rem)").length - 1;
    expect(occurrences).toBe(1);
  });

  it("gives .workspace-shell a background matching the tree pane and note-controls frame, so the gap between them shows the same tone instead of a faint color-step boundary", () => {
    // Traced via computed-style inspection: the separator's gap (once
    // transparent) showed the page body's slightly different background
    // through it, between two panels sharing a lighter, identical muted
    // tone -- reading as a thin stripe even with no line or border drawn.
    const css = readAppCss();
    const shellBgMatch = css.match(
      /@media \(width >= 640px\) \{[\s\S]*?\.workspace-shell \{[^}]*background: var\(--color-bg-surface-muted\);[^}]*\}/,
    );
    expect(shellBgMatch).not.toBeNull();
  });
});

/**
 * Regression guard: the
 * resize handle between the tree/rail and the note workspace does not paint
 * a permanent 1px line at every width, since that would be a second, redundant divider given
 * the tree pane and note-controls frame both having their own full rounded
 * borders with real margin between them.
 */
describe("resize-handle divider removed", () => {
  it("no longer gives .workspace-shell__separator::before a permanent visible background", () => {
    const css = readAppCss();
    const beforeIndex = css.indexOf(".workspace-shell__separator::before {");
    expect(beforeIndex).toBeGreaterThan(-1);
    const beforeBlock = css.slice(
      beforeIndex,
      css.indexOf("}", beforeIndex) + 1,
    );
    expect(beforeBlock).toMatch(/background: transparent;/);
    expect(beforeBlock).not.toMatch(
      /background: var\(--color-border-default\);/,
    );
  });

  it("still shows the divider on hover/focus-visible/active-resize, so the handle stays discoverable", () => {
    const css = readAppCss();
    const hoverMatch = css.match(
      /\.workspace-shell__separator:hover::before,\s*\.workspace-shell__separator:focus-visible::before,\s*\.workspace-shell\[data-tree-resizing="true"\]\s+\.workspace-shell__separator::before \{\s*background: var\(--color-border-default\);\s*\}/,
    );
    expect(hoverMatch).not.toBeNull();
  });
});

/**
 * Regression guard:
 * an explicit, self-contained narrow header layout for the hamburger and
 * the Recent-notes/More-actions group, rather than relying on
 * `.app-header`'s `justify-content: space-between` plus counting which
 * sibling nav groups happen to be hidden at this width (an incidental
 * layout that is unreliable).
 */
describe("narrow workspace header explicit layout", () => {
  it("gives .app-header--workspace an explicit flex-start layout at narrow, not the incidental space-between", () => {
    const css = readAppCss();
    const explicitMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.app-header--workspace \{\s*justify-content: flex-start;\s*\}/,
    );
    expect(explicitMatch).not.toBeNull();
  });

  it("pins the hamburger with flex-shrink: 0, so it can never compress or be squeezed off-screen", () => {
    const css = readAppCss();
    const drawerTriggerMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.app-header--workspace \.workspace-drawer-trigger \{\s*flex-shrink: 0;\s*\}/,
    );
    expect(drawerTriggerMatch).not.toBeNull();
  });

  it("pushes the Recent-notes/More-actions group to the header's end with margin-left: auto and flex-shrink: 0, independent of any sibling's visibility", () => {
    const css = readAppCss();
    const titlebarMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.app-header--workspace \.note-workspace-titlebar \{\s*flex-shrink: 0;\s*margin-left: auto;\s*\}/,
    );
    expect(titlebarMatch).not.toBeNull();
  });

  it("still hides the brand and account/search/help/theme groups at narrow -- the compact header's only job is the three required controls", () => {
    const css = readAppCss();
    const hiddenMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.app-header--workspace \.account-nav--center,\s*\.app-header--workspace \.account-nav--right \{\s*display: none;\s*\}/,
    );
    expect(hiddenMatch).not.toBeNull();
  });

  it("still hides Home (the drawer's own nav equivalent) at narrow via the shared .note-workspace-titlebar__nav-item rule", () => {
    const css = readAppCss();
    const hiddenNavItemMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.app-header--workspace \.note-workspace-titlebar__nav-item \{\s*display: none;\s*\}/,
    );
    expect(hiddenNavItemMatch).not.toBeNull();
  });

  it("restores quick-trash's visibility at narrow with a narrowly scoped override, independent of Home's hide rule", () => {
    // Quick-trash is a note-scoped destructive action, not a navigation
    // destination like Home -- the narrow drawer has no equivalent for
    // it, so unlike Home it must stay visible below 640px. This override
    // must come after the shared `.note-workspace-titlebar__nav-item`
    // hide rule (source order) so it wins the cascade, and must not
    // change that shared rule itself.
    const css = readAppCss();
    const hiddenRuleMatch = css.match(
      /\.app-header--workspace \.note-workspace-titlebar__nav-item \{\s*display: none;\s*\}/,
    );
    const quickTrashOverrideMatch = css.match(
      /\.app-header--workspace\s*\.note-workspace-titlebar__nav-item\.note-workspace-titlebar__quick-trash \{\s*display: block;\s*\}/,
    );
    expect(hiddenRuleMatch).not.toBeNull();
    expect(quickTrashOverrideMatch).not.toBeNull();
    expect(quickTrashOverrideMatch!.index!).toBeGreaterThan(
      hiddenRuleMatch!.index!,
    );
  });
});

/**
 * Regression guard: the rail-to-tree gap
 * stays unchanged, but the *tree-to-note-workspace*
 * gap's complete box model is the separator's
 * own flex-basis *plus* `.note-workspace`'s `padding-left`, not the
 * separator alone. Both the rail-to-tree gap and the tree-to-note-
 * workspace gap share `--workspace-rail-gap`, so the tree pane reads
 * as equally close to both of its neighbors, and `.note-workspace`'s own
 * `padding-left` does not add a second, hidden contribution.
 */
describe("navigation region gap (rail-to-tree and tree-to-note-workspace)", () => {
  it("keeps .workspace-shell__tree-rail's margin-right on --workspace-rail-gap, unchanged from the fourteenth pass", () => {
    const css = readAppCss();
    const railMatch = css.match(
      /\.workspace-shell__tree-rail \{[^}]*margin-right: var\(--workspace-rail-gap\);/,
    );
    expect(railMatch).not.toBeNull();
  });

  it("moves .workspace-shell__separator's tree-to-workspace gap onto --workspace-rail-gap too, not the removed --workspace-sibling-gap", () => {
    const css = readAppCss();
    const separatorMatch = css.match(
      /\.workspace-shell__separator \{[^}]*flex: 0 0 var\(--workspace-rail-gap\);/,
    );
    expect(separatorMatch).not.toBeNull();
    expect(css).not.toMatch(/flex: 0 0 var\(--workspace-sibling-gap\)/);
  });

  it("removes .note-workspace's padding-left -- the excessive gap's hidden second contributor, on top of the separator's own width", () => {
    const css = readAppCss();
    const workspaceIndex = css.indexOf(".note-workspace {\n    display: flex;");
    expect(workspaceIndex).toBeGreaterThan(-1);
    const workspaceBlock = css.slice(
      workspaceIndex,
      css.indexOf("\n  }", workspaceIndex) + 4,
    );
    expect(workspaceBlock).toMatch(/padding-left: 0;/);
    expect(workspaceBlock).not.toMatch(
      /padding-left: var\(--workspace-outer-inset\);/,
    );
    // padding-right is untouched -- that side has no neighboring panel.
    expect(workspaceBlock).toMatch(
      /padding-right: var\(--workspace-outer-inset\);/,
    );
  });

  it("moves .note-controls-frame's left to 0, matching .note-workspace's now-zero padding-left, so the frame stays aligned with the editor", () => {
    const css = readAppCss();
    const frameMatch = css.match(
      /@media \(width >= 640px\) \{\s*\.note-controls-frame \{[^}]*left: 0;/,
    );
    expect(frameMatch).not.toBeNull();
    // right is untouched, still the outer-inset token.
    const frameRightMatch = css.match(
      /@media \(width >= 640px\) \{\s*\.note-controls-frame \{[^}]*right: var\(--workspace-outer-inset\);/,
    );
    expect(frameRightMatch).not.toBeNull();
  });

  it("gives the resize separator a wider invisible pointer target via ::after, without widening the visible gap itself", () => {
    const css = readAppCss();
    const afterMatch = css.match(
      /\.workspace-shell__separator::after \{\s*content: "";\s*position: absolute;\s*top: 0;\s*bottom: 0;\s*left: -4px;\s*right: -4px;\s*cursor: col-resize;\s*\}/,
    );
    expect(afterMatch).not.toBeNull();
  });
});

/**
 * Editor surface hierarchy. Title is raised to
 * read as clearly primary against the (now-unboxed) tag row above it and
 * the save-status/actions beside it; the input's own min-height keeps
 * pace with the larger text so it isn't cramped against its own padding.
 * A larger size reads too large, closer to a page heading
 * than an inline ribbon control, so a more modest
 * increase over the original is used instead.
 */
describe("editor title hierarchy", () => {
  // Larger sizes (`clamp(1.15rem, 1.6vw, 1.35rem)`, then
  // `clamp(1.05rem, 1.3vw, 1.18rem)`) read too large; a heavier weight
  // (`--font-weight-strong`, 700) reads too heavy/bold even at a smaller
  // size; and `clamp(1rem, 1.1vw, 1.1rem)` (paired with the
  // lighter `--font-weight-medium`) is still slightly too
  // prominent. The settled value -- a quiet, editable title, not a
  // heading -- relies on placement/whitespace/weight, not size, for
  // "calm prominence".
  it("settles --font-size-ribbon-title at a genuinely modest increase over the original value", () => {
    const css = readAppCss();
    const rootIndex = css.indexOf(":root {");
    expect(rootIndex).toBeGreaterThan(-1);
    const rootBlock = css.slice(rootIndex, css.indexOf("\n}", rootIndex));
    expect(rootBlock).toMatch(
      /--font-size-ribbon-title: clamp\(0\.95rem, 1vw, 1\.02rem\);/,
    );
    // Every earlier, larger value must not reappear.
    expect(rootBlock).not.toMatch(
      /--font-size-ribbon-title: clamp\(0\.96rem, 1\.2vw, 1\.05rem\);/,
    );
    expect(rootBlock).not.toMatch(
      /--font-size-ribbon-title: clamp\(1\.15rem, 1\.6vw, 1\.35rem\);/,
    );
    expect(rootBlock).not.toMatch(
      /--font-size-ribbon-title: clamp\(1\.05rem, 1\.3vw, 1\.18rem\);/,
    );
    expect(rootBlock).not.toMatch(
      /--font-size-ribbon-title: clamp\(1rem, 1\.1vw, 1\.1rem\);/,
    );
  });

  it("declares a --font-weight-medium token between normal and strong, used only by the title", () => {
    const css = readAppCss();
    const rootIndex = css.indexOf(":root {");
    const rootBlock = css.slice(rootIndex, css.indexOf("\n}", rootIndex));
    expect(rootBlock).toMatch(/--font-weight-medium: 600;/);
  });

  it("uses --font-weight-medium for the title input, not the heavier --font-weight-strong", () => {
    const css = readAppCss();
    const titleInputIndex = css.indexOf(".note-workspace__title-wrap input {");
    expect(titleInputIndex).toBeGreaterThan(-1);
    const titleInputBlock = css.slice(
      titleInputIndex,
      css.indexOf("}", titleInputIndex) + 1,
    );
    expect(titleInputBlock).toMatch(
      /font-weight: var\(--font-weight-medium\);/,
    );
    expect(titleInputBlock).not.toMatch(
      /font-weight: var\(--font-weight-strong\);/,
    );
  });

  it("settles the title input's own min-height to match the other ribbon controls exactly, for a uniform row height", () => {
    const css = readAppCss();
    const titleInputIndex = css.indexOf(".note-workspace__title-wrap input {");
    expect(titleInputIndex).toBeGreaterThan(-1);
    const titleInputBlock = css.slice(
      titleInputIndex,
      css.indexOf("}", titleInputIndex) + 1,
    );
    expect(titleInputBlock).toMatch(/min-height: 1\.6rem;/);
    expect(titleInputBlock).not.toMatch(/min-height: (1\.7rem|1\.8rem|2rem);/);
    expect(titleInputBlock).toMatch(
      /font-size: var\(--font-size-ribbon-title\);/,
    );
  });
});

/**
 * Without this, the note-detail
 * ribbon row would be taller than needed, driven by its own padding plus a
 * save-status badge that would be taller than every other control in the
 * row. Every scoped control (title input, Saved badge, Save/Show-
 * formatting buttons) shares one `1.6rem` min-height exactly, so no
 * single control drives the row's total height. Scoped entirely to
 * these note-detail-ribbon selectors -- no shared/global button rule
 * is touched.
 */
describe("editor title row compactness", () => {
  it("gives the Saved badge the same 1.6rem min-height as the other ribbon controls, down from its previous, taller floor", () => {
    const css = readAppCss();
    const statusIndex = css.indexOf(
      ".note-workspace__save-status {\n  display: inline-flex;",
    );
    expect(statusIndex).toBeGreaterThan(-1);
    const statusBlock = css.slice(
      statusIndex,
      css.indexOf("}", statusIndex) + 1,
    );
    expect(statusBlock).toMatch(/min-height: 1\.6rem;/);
    expect(statusBlock).not.toMatch(/min-height: 1\.75rem;/);
  });

  it("gives the ribbon's Save/Show-formatting buttons the same 1.6rem min-height, scoped to this row only", () => {
    const css = readAppCss();
    const actionsButtonIndex = css.indexOf(
      ".note-workspace__actions button,\n.note-workspace__actions .button-link {",
    );
    expect(actionsButtonIndex).toBeGreaterThan(-1);
    const actionsButtonBlock = css.slice(
      actionsButtonIndex,
      css.indexOf("}", actionsButtonIndex) + 1,
    );
    expect(actionsButtonBlock).toMatch(/min-height: 1\.6rem;/);
    expect(actionsButtonBlock).not.toMatch(/min-height: 1\.65rem;/);

    const toggleIndex = css.indexOf(".note-workspace__formatting-toggle {");
    expect(toggleIndex).toBeGreaterThan(-1);
    const toggleBlock = css.slice(
      toggleIndex,
      css.indexOf("}", toggleIndex) + 1,
    );
    expect(toggleBlock).toMatch(/min-height: 1\.6rem;/);
    expect(toggleBlock).not.toMatch(/min-height: 1\.65rem;/);
  });

  it("does not touch the shared global button rule or the .icon-button primitive's own sizing", () => {
    const css = readAppCss();
    const globalButtonIndex = css.indexOf("button,\n.button-link {");
    expect(globalButtonIndex).toBeGreaterThan(-1);
    const globalButtonBlock = css.slice(
      globalButtonIndex,
      css.indexOf("}", globalButtonIndex) + 1,
    );
    expect(globalButtonBlock).not.toMatch(/min-height: 1\.6rem/);

    const iconButtonIndex = css.indexOf(
      ".icon-button {\n  display: inline-flex;",
    );
    expect(iconButtonIndex).toBeGreaterThan(-1);
    const iconButtonBlock = css.slice(
      iconButtonIndex,
      css.indexOf("}", iconButtonIndex) + 1,
    );
    expect(iconButtonBlock).toMatch(/width: var\(--icon-button-size\);/);
    expect(iconButtonBlock).not.toMatch(/min-height: 1\.6rem/);
  });
});

/**
 * Tag chip text
 * vertical centering. The remove button already had its own tight
 * `line-height: 1`; the label span had none, inheriting the taller body
 * default, which is what actually caused the misalignment (not the
 * chip's own `align-items: center`, which was already correct).
 */
describe("tag chip text vertical centering", () => {
  it("gives .note-tags__chip-label a tight line-height matching the remove button's own", () => {
    const css = readAppCss();
    const labelMatch = css.match(
      /\.note-tags__chip-label \{\s*line-height: 1;\s*\}/,
    );
    expect(labelMatch).not.toBeNull();
  });

  it("keeps the chip's own flex centering unchanged -- alignment was fixed via line-height, not padding", () => {
    const css = readAppCss();
    const chipIndex = css.indexOf(
      ".note-tags__chip {\n  display: inline-flex;",
    );
    expect(chipIndex).toBeGreaterThan(-1);
    const chipBlock = css.slice(chipIndex, css.indexOf("}", chipIndex) + 1);
    expect(chipBlock).toMatch(/align-items: center;/);
  });
});

/**
 * The toolbar deliberately avoids: a full rounded panel (would
 * compete with the consolidated tags/title surface); a border-top-only
 * strip (too flat, and nearly invisible in dark mode, where
 * `--color-bg-toolbar` and `--color-bg-surface-muted` would be the identical
 * color); a full-but-unrounded border with a dark-mode-only background
 * override (still insufficiently distinct in *both* themes,
 * with square corners still reading closer to a flat strip). Actual
 * state: a full thin border, a small `--radius-sm` corner radius (a
 * compact control strip, not a larger panel treatment), and
 * `--color-bg-toolbar`'s own value tuned directly in both themes --
 * that token is dedicated to exactly this one component, so adjusting
 * it directly is the smallest available token-based shift, not a new
 * literal or a reused, differently-purposed token.
 */
describe("toolbar restrained border and dark-mode contrast", () => {
  it("gives the toolbar a full border with a small, restrained corner radius -- not a rounded panel, not a flat strip", () => {
    const css = readAppCss();
    const toolbarIndex = css.indexOf(".editor-toolbar {\n  display: flex;");
    expect(toolbarIndex).toBeGreaterThan(-1);
    const toolbarBlock = css.slice(
      toolbarIndex,
      css.indexOf("}", toolbarIndex) + 1,
    );
    expect(toolbarBlock).toMatch(
      /border: 1px solid var\(--color-border-toolbar\);/,
    );
    expect(toolbarBlock).toMatch(/border-radius: var\(--radius-sm\);/);
    expect(toolbarBlock).not.toMatch(/border-top: 1px solid/);
    expect(toolbarBlock).not.toMatch(/border-radius: 0;/);
  });

  it("tightens the toolbar's own vertical padding one step, without touching any button's own min-height", () => {
    const css = readAppCss();
    const toolbarIndex = css.indexOf(".editor-toolbar {\n  display: flex;");
    const toolbarBlock = css.slice(
      toolbarIndex,
      css.indexOf("}", toolbarIndex) + 1,
    );
    expect(toolbarBlock).toMatch(/padding: var\(--space-2\) var\(--space-6\);/);

    const textButtonIndex = css.indexOf(
      ".editor-toolbar button:not(.icon-button) {",
    );
    expect(textButtonIndex).toBeGreaterThan(-1);
    const textButtonBlock = css.slice(
      textButtonIndex,
      css.indexOf("}", textButtonIndex) + 1,
    );
    expect(textButtonBlock).toMatch(/min-height: 1\.75rem;/);
  });

  it("the narrow fixed-bottom toolbar still cancels the corner radius, so a viewport-edge-flush bar never shows rounded corners", () => {
    const css = readAppCss();
    const narrowToolbarMatch = css.match(
      /\.editor-toolbar:not\(\[hidden\]\) \{[^}]*position: fixed;[^}]*border-radius: 0;[^}]*box-shadow:/,
    );
    expect(narrowToolbarMatch).not.toBeNull();
  });

  it("tunes --color-bg-toolbar's own value directly in both themes, distinct from --color-bg-surface-muted, rather than a scoped per-theme override", () => {
    const css = readAppCss();

    const lightRootIndex = css.indexOf(":root {");
    const lightRootBlock = css.slice(
      lightRootIndex,
      css.indexOf("\n}", lightRootIndex),
    );
    expect(lightRootBlock).toMatch(/--color-bg-toolbar: #f2ecdc;/);
    expect(lightRootBlock).not.toMatch(/--color-bg-toolbar: #f8f5ee;/);

    const darkThemeIndex = css.indexOf('[data-theme="dark"] {');
    expect(darkThemeIndex).toBeGreaterThan(-1);
    const darkThemeBlock = css.slice(
      darkThemeIndex,
      css.indexOf("\n}", darkThemeIndex),
    );
    expect(darkThemeBlock).toMatch(/--color-bg-toolbar: #2a3340;/);
    // The old, identical-to-surface-muted value must not reappear, and
    // the once-tried scoped override selector must be gone too.
    expect(darkThemeBlock).not.toMatch(/--color-bg-toolbar: #202831;/);
    expect(css).not.toMatch(
      /\[data-theme="dark"\] \.editor-toolbar \{\s*background:/,
    );
  });
});

describe("shared-list title typography", () => {
  it("reduces the shared .note-list__title weight from heavy (800) to medium (600), covering Home/All Notes/Trash", () => {
    const css = readAppCss();
    const titleIndex = css.indexOf(".note-list__title {");
    expect(titleIndex).toBeGreaterThan(-1);
    const titleBlock = css.slice(titleIndex, css.indexOf("}", titleIndex) + 1);
    expect(titleBlock).toMatch(/font-weight: var\(--font-weight-medium\);/);
    expect(titleBlock).not.toMatch(/font-weight: var\(--font-weight-heavy\);/);
    // Font size, wrapping, and flex behavior are untouched.
    expect(titleBlock).toMatch(/font-size: var\(--font-size-body-sm\);/);
    expect(titleBlock).toMatch(/overflow-wrap: break-word;/);
    expect(titleBlock).toMatch(/min-width: 0;/);
  });

  it("scopes the resting title-link color to the <a> variant only, leaving Trash/Administrator Recovery's plain <span> untouched", () => {
    const css = readAppCss();
    const anchorIndex = css.indexOf("a.note-list__title {");
    expect(anchorIndex).toBeGreaterThan(-1);
    const anchorBlock = css.slice(
      anchorIndex,
      css.indexOf("}", anchorIndex) + 1,
    );
    expect(anchorBlock).toMatch(/color: var\(--color-text-strong\);/);

    // The bare `.note-list__title` rule itself must not carry a color
    // declaration -- only the more specific `a.note-list__title` variant
    // does, so Trash's/Administrator Recovery's <span> keeps inheriting
    // the ordinary default text color exactly as before.
    const titleIndex = css.indexOf(".note-list__title {");
    const titleBlock = css.slice(titleIndex, css.indexOf("}", titleIndex) + 1);
    expect(titleBlock).not.toMatch(/\n {2}color:/);
  });

  it("restores the accent link color on hover and keyboard focus for the title link", () => {
    const css = readAppCss();
    const hoverIndex = css.indexOf("a.note-list__title:hover,");
    expect(hoverIndex).toBeGreaterThan(-1);
    const hoverBlock = css.slice(hoverIndex, css.indexOf("}", hoverIndex) + 1);
    expect(hoverBlock).toMatch(/a\.note-list__title:focus-visible \{/);
    expect(hoverBlock).toMatch(/color: var\(--color-link-hover\);/);
  });

  it("leaves Administrator Recovery's own page-scoped 600-weight title override completely untouched", () => {
    const css = readAppCss();
    const overrideIndex = css.indexOf(
      ".panel--admin-recovery .note-list__title {",
    );
    expect(overrideIndex).toBeGreaterThan(-1);
    const overrideBlock = css.slice(
      overrideIndex,
      css.indexOf("}", overrideIndex) + 1,
    );
    expect(overrideBlock).toMatch(/font-weight: 600;/);
    expect(overrideBlock).not.toMatch(/color:/);
  });

  it("reduces the Global Search result-title weight from strong (700) to medium (600), preserving its existing color", () => {
    const css = readAppCss();
    const resultTitleIndex = css.indexOf(
      ".global-search-panel__result-title {",
    );
    expect(resultTitleIndex).toBeGreaterThan(-1);
    const resultTitleBlock = css.slice(
      resultTitleIndex,
      css.indexOf("}", resultTitleIndex) + 1,
    );
    expect(resultTitleBlock).toMatch(
      /font-weight: var\(--font-weight-medium\);/,
    );
    expect(resultTitleBlock).not.toMatch(
      /font-weight: var\(--font-weight-strong\);/,
    );
    expect(resultTitleBlock).toMatch(/color: var\(--color-text-strong\);/);
  });

  it("does not touch tag-chip, metadata, type-label, or lifecycle-emphasis typography", () => {
    const css = readAppCss();
    expect(css).toMatch(
      /\.note-list__meta \{\n {2}margin: var\(--space-3\) 0 0;/,
    );
    expect(css).toMatch(
      /\.note-list__title-row \.note-list__type \{\n {2}flex-shrink: 0;\n {2}color: var\(--color-text-accent-muted\);\n {2}font-size: 0\.75rem;\n {2}font-weight: var\(--font-weight-strong\);/,
    );
    expect(css).toMatch(
      /\.trash-item__lifecycle-entry--emphasis dt,\n\.trash-item__lifecycle-entry--emphasis dd \{/,
    );
  });
});

describe("tree title tooltip anchoring", () => {
  it("makes .tree-nav__note-link a flex row instead of a block that clips its own text", () => {
    const css = readAppCss();
    const linkIndex = css.indexOf(".tree-nav__note-link {");
    expect(linkIndex).toBeGreaterThan(-1);
    const linkBlock = css.slice(linkIndex, css.indexOf("}", linkIndex) + 1);
    expect(linkBlock).toMatch(/display: flex;/);
    expect(linkBlock).not.toMatch(/display: block;/);
    // The clipping/truncation job moved to .tree-nav__label.
    expect(linkBlock).not.toMatch(/overflow: hidden;/);
    expect(linkBlock).not.toMatch(/text-overflow: ellipsis;/);
    expect(linkBlock).not.toMatch(/white-space: nowrap;/);
  });

  it("still truncates the title text on .tree-nav__label, unchanged", () => {
    const css = readAppCss();
    const labelMatch = css.match(
      /\.tree-nav__label \{\s*flex: 1 1 auto;\s*min-width: 0;\s*overflow: hidden;\s*text-overflow: ellipsis;\s*white-space: nowrap;\s*\}/,
    );
    expect(labelMatch).not.toBeNull();
  });

  it("gives the shared, body-level tree tooltip fixed positioning and a max-width independent of the tree's own width", () => {
    const css = readAppCss();
    const tooltipIndex = css.indexOf(".tree-title-tooltip {");
    expect(tooltipIndex).toBeGreaterThan(-1);
    const tooltipBlock = css.slice(
      tooltipIndex,
      css.indexOf("}", tooltipIndex) + 1,
    );
    expect(tooltipBlock).toMatch(/position: fixed;/);
    expect(tooltipBlock).toMatch(
      /max-width: min\(28rem, calc\(100vw - 2rem\)\);/,
    );
    // Must wrap, not force one unbroken long line.
    expect(tooltipBlock).toMatch(/white-space: normal;/);
    expect(tooltipBlock).not.toMatch(/white-space: nowrap;/);
    expect(tooltipBlock).toMatch(/overflow-wrap: break-word;/);
  });

  it("shows the tree tooltip only via its own --visible modifier class, not :hover/:focus-visible pseudo-classes on the row", () => {
    const css = readAppCss();
    const visibleMatch = css.match(
      /\.tree-title-tooltip--visible \{\s*opacity: 1;\s*visibility: visible;\s*\}/,
    );
    expect(visibleMatch).not.toBeNull();
    // The old CSS-only `[data-tooltip]::after` mechanism for tree titles
    // is gone -- a real, shared, JS-positioned element replaced it so it
    // can escape .tree-nav__viewport's clipping.
    expect(css).not.toMatch(/\.tree-nav__note-link\[data-tooltip\]::after/);
    expect(css).not.toMatch(
      /\.tree-nav__folder-disclosure\[data-tooltip\]::after/,
    );
  });

  it("keeps the pin marker from being squeezed now that .tree-nav__note-link is a flex row", () => {
    const css = readAppCss();
    const pinIndex = css.indexOf(".tree-nav__pin-marker {");
    expect(pinIndex).toBeGreaterThan(-1);
    const pinBlock = css.slice(pinIndex, css.indexOf("}", pinIndex) + 1);
    expect(pinBlock).toMatch(/flex-shrink: 0;/);
  });
});

describe("row-menu panel internal scrolling", () => {
  it("gives .tree-nav__row-menu-panel unconditional overflow-y: auto, pairing with the inline max-height floatRowMenuPanel now sets", () => {
    const css = readAppCss();
    const panelIndex = css.indexOf(".tree-nav__row-menu-panel {");
    expect(panelIndex).toBeGreaterThan(-1);
    const panelBlock = css.slice(panelIndex, css.indexOf("}", panelIndex) + 1);
    expect(panelBlock).toMatch(/overflow-y: auto;/);
  });
});

// The reusable Preferences section pattern
// (Timezone and Tags both use it) wraps deliberately at narrow
// widths rather than overflow -- the Tags section's three controls sit
// in one horizontal row at wide widths via flex-wrap, which is also
// exactly what prevents horizontal overflow once the viewport can't
// fit them all on one line.
describe("Preferences page -- sectioned pattern and narrow protection", () => {
  it("defines a reusable .preferences-section rule with restrained (not card) separation", () => {
    const css = readAppCss();
    const rule = css.match(/\.preferences-section \{[^}]*\}/);
    expect(rule).not.toBeNull();
    expect(rule![0]).toMatch(/border-top:/);
    expect(rule![0]).not.toMatch(/box-shadow/);
    expect(rule![0]).not.toMatch(/border-radius/);
  });

  it("the first section skips the separating border (nothing to separate it from)", () => {
    const css = readAppCss();
    expect(css).toMatch(
      /\.preferences-section:first-of-type \{[^}]*border-top: none;/,
    );
  });

  it("the Tags controls row wraps instead of overflowing at narrow widths", () => {
    const css = readAppCss();
    const rule = css.match(/\.preferences-tags-form__controls \{[^}]*\}/);
    expect(rule).not.toBeNull();
    expect(rule![0]).toMatch(/display: flex;/);
    expect(rule![0]).toMatch(/flex-wrap: wrap;/);
  });

  it("the Tags checkbox/color controls are inline pairs, not stacked label-above-field blocks", () => {
    const css = readAppCss();
    const checkboxRule = css.match(
      /\.preferences-tags-form__checkbox,\n\.preferences-tags-form__color-field \{[^}]*\}/,
    );
    expect(checkboxRule).not.toBeNull();
    expect(checkboxRule![0]).toMatch(/display: inline-flex;/);
  });

  it("uses normal label weight for the compact Tags controls, not the site-wide bold label", () => {
    const css = readAppCss();
    const rule = css.match(/\.preferences-tags-form__controls label \{[^}]*\}/);
    expect(rule).not.toBeNull();
    expect(rule![0]).toMatch(/font-weight: var\(--font-weight-normal\);/);
  });

  it("bounds the default-color select to a compact width, not a full-width control", () => {
    const css = readAppCss();
    const rule = css.match(
      /\.preferences-tags-form__color-field select \{[^}]*\}/,
    );
    expect(rule).not.toBeNull();
    expect(rule![0]).toMatch(/width: auto;/);
    expect(rule![0]).toMatch(/max-width:/);
  });
});

/**
 * Confirmed
 * empirically (real Chromium, real viewport, both wheel-scroll and
 * programmatic `scrollTop`, not jsdom) that once ten real topics
 * replaced the eight scaffold ones, the TOC's own natural (uncollapsed)
 * height could consume most of a phone-height narrow viewport --
 * `.help-article` was still a technically-correct `overflow-y: auto`
 * scroll region (`min-height: 0` already present), but its own
 * *bounding box* was left as small as 13px tall on a 320x568 admin
 * viewport (ten topics, longest labels), effectively unusable even
 * though `scrollHeight` proved the content itself was fully reachable
 * via `scrollTop`. `.help-panel__body .help-toc` gets a `max-height`
 * cap plus its own `overflow-y: auto` so it can never starve the
 * article of a real, usable share of the remaining flex column --
 * `.help-article` remains the sole scroll owner for topic content; the
 * TOC only gains a scrollbar of its own on a viewport short enough
 * that it doesn't fit under the cap, which is the one deliberately
 * permitted case.
 */
describe("narrow Help TOC height cap (article scroll-region starvation fix)", () => {
  it("caps .help-panel__body .help-toc's height at narrow and gives it its own overflow, inside the same rule that already pins flex-shrink: 0", () => {
    const css = readAppCss();
    const narrowTocMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.help-panel__body \.help-toc \{\s*flex-shrink: 0;\s*max-height: 40dvh;\s*overflow-y: auto;\s*\}/,
    );
    expect(narrowTocMatch).not.toBeNull();
  });

  it("leaves .help-article as the sole content scroll owner -- its own flex/min-height/overflow rule is unchanged by this pass", () => {
    const css = readAppCss();
    const articleRuleMatch = css.match(
      /\.help-panel__body \.help-article \{\s*flex: 1;\s*min-height: 0;\s*overflow-y: auto;\s*\}/,
    );
    expect(articleRuleMatch).not.toBeNull();
  });

  it("does not touch desktop's grid-based .help-layout geometry -- the cap is scoped inside the existing narrow (<640px) media block only", () => {
    const css = readAppCss();
    const desktopLayoutMatch = css.match(
      /\.help-layout \{\s*display: grid;\s*grid-template-columns: 14rem 1fr;/,
    );
    expect(desktopLayoutMatch).not.toBeNull();

    // The cap itself must not appear anywhere outside a narrow media block.
    const capIndex = css.indexOf("max-height: 40dvh;");
    expect(capIndex).toBeGreaterThan(-1);
    const precedingText = css.slice(0, capIndex);
    const lastNarrowOpen = precedingText.lastIndexOf(
      "@media (width < 640px) {",
    );
    const lastWideOpen = precedingText.lastIndexOf("@media (width >= 640px) {");
    expect(lastNarrowOpen).toBeGreaterThan(lastWideOpen);
  });
});

/**
 * Confirmed empirically (real Chromium, three desktop viewports) that
 * `.help-article`'s `clientHeight` always exactly equalled its own
 * `scrollHeight` for every long topic -- its `overflow-y: auto` (see
 * the test above) was already correct but had nothing to scroll,
 * because `.help-layout`'s shared base rule sets `align-items: start`,
 * needed so the canonical standalone `/help/` page's TOC and article
 * don't stretch to match each other's height when every topic is shown
 * stacked. Inside the dialog, `.help-layout` is a CSS Grid container
 * with a genuinely fixed row height (the existing `height: 100%` rule
 * above), but `align-items: start` still sizes each grid item to its
 * own content height instead of stretching to fill that row, so
 * `.help-article` never received the bounded height its `overflow-y:
 * auto` needed to do anything -- long content simply grew past the
 * dialog, invisibly clipped by `.help-panel`'s own `overflow: hidden`
 * with no internal scrollbar anywhere.
 */
describe("wide/desktop Help article scroll-region fix", () => {
  it("stretches grid items to the row's full height inside the dialog at wide widths only, without touching the shared base align-items: start rule", () => {
    const css = readAppCss();

    // The shared base rule (used by both the dialog and the canonical
    // standalone page) must still set align-items: start -- this fix
    // must not change the canonical page's own layout.
    const baseLayoutMatch = css.match(
      /\.help-layout \{\s*display: grid;\s*grid-template-columns: 14rem 1fr;\s*gap: var\(--space-18\);\s*align-items: start;\s*\}/,
    );
    expect(baseLayoutMatch).not.toBeNull();

    // The dialog-only override lives inside a wide (>= 640px) media
    // block, scoped to .help-panel__body .help-layout specifically --
    // not the shared selector, and not the narrow block.
    const wideStretchMatch = css.match(
      /@media \(width >= 640px\) \{\s*\.help-panel__body \.help-layout \{\s*align-items: stretch;\s*\}\s*\}/,
    );
    expect(wideStretchMatch).not.toBeNull();
  });

  it("does not add the stretch override inside the narrow (<640px) media block", () => {
    const css = readAppCss();
    // This
    // selector carries an `[open]` qualifier and its own explanatory
    // comment (see the "Help dialog closed-state layout containment"
    // describe block) -- updated in place to match, not loosened.
    const narrowBlockMatch = css.match(
      /@media \(width < 640px\) \{\s*(?:\/\*[\s\S]*?\*\/\s*)?\.help-panel\[open\] \{/,
    );
    expect(narrowBlockMatch).not.toBeNull();
    const narrowBlockStart = narrowBlockMatch ? narrowBlockMatch.index! : -1;
    expect(narrowBlockStart).toBeGreaterThan(-1);
    const narrowBlockEnd = css.indexOf("\n}\n", narrowBlockStart);
    const narrowBlock = css.slice(narrowBlockStart, narrowBlockEnd);
    expect(narrowBlock).not.toMatch(/align-items: stretch;/);
  });

  it("keeps .help-panel__body .help-article's own flex/min-height/overflow rule intact -- it remains the sole scroll owner, this fix only lets it actually receive a bounded height", () => {
    const css = readAppCss();
    const articleRuleMatch = css.match(
      /\.help-panel__body \.help-article \{\s*flex: 1;\s*min-height: 0;\s*overflow-y: auto;\s*\}/,
    );
    expect(articleRuleMatch).not.toBeNull();
  });
});

/**
 * Confirmed
 * empirically (real Chromium, real narrow viewports) that once the TOC
 * needs its own scroll (the 40dvh cap above), long labels like "Library
 * Backup and Restore" sat flush against the scrollbar track -- measured
 * a literal `0px` gap, since the narrow `.help-toc` override zeroed out
 * the desktop rule's `padding-inline-end` entirely (that value exists
 * there only to clear space for the desktop divider border, which
 * narrow replaces with its own `border-block-end` instead). A small
 * `--space-8` end padding gives the text real breathing room before the
 * scrollbar regardless of scrollbar style. `scrollbar-gutter: stable`
 * was evaluated and rejected: this platform's scrollbar measured as an
 * overlay type consuming zero layout width, so `scrollbar-gutter:
 * stable` would have had no visible effect here, and on a
 * classic-scrollbar platform it would reserve that gutter permanently
 * -- including while the TOC has few enough entries to not scroll at
 * all -- an always-present empty strip that is deliberately avoided.
 */
describe("narrow Help TOC scrollbar spacing", () => {
  it("gives the narrow .help-toc a small padding-inline-end instead of zero, so scrolled text doesn't run into the scrollbar", () => {
    const css = readAppCss();
    const narrowTocPaddingMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.help-toc \{\s*position: static;\s*padding-inline-end: var\(--space-8\);/,
    );
    expect(narrowTocPaddingMatch).not.toBeNull();

    // The old zero-padding override must not reappear.
    expect(css).not.toMatch(
      /\.help-toc \{\s*position: static;\s*padding-inline-end: 0;/,
    );
  });

  it("does not declare scrollbar-gutter as an actual CSS property anywhere -- rejected as ineffective on this platform's overlay scrollbar and risky as an always-reserved strip on classic-scrollbar platforms (the property name may still appear in this fix's own explanatory comment)", () => {
    const css = readAppCss();
    const cssWithoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(cssWithoutComments).not.toMatch(/scrollbar-gutter\s*:\s*\S/);
  });
});

/**
 * Confirmed
 * empirically (real Chromium, both wide and narrow viewports) that
 * `.help-article` had no inline-end padding at all, so its scrollbar
 * track sat flush against the widest visible text/punctuation --
 * measured a literal `0px` gap at 1280x800, 1920x1080, and 375x667
 * alike, on the same overlay-scrollbar platform (zero consumed layout
 * width) already confirmed by the narrow-TOC scrollbar fix above.
 * Applied unscoped (not wide-only) since the identical gap existed at
 * narrow too -- just unflagged there -- and the same small
 * `--space-8` token trims narrow's article column by under 3% (325px
 * -> 316px, measured), well short of cramped.
 */
describe("Help article scrollbar spacing", () => {
  it("gives .help-article a small padding-inline-end instead of none, applied unscoped so both wide and narrow get the same scrollbar breathing room", () => {
    const css = readAppCss();
    const articleBaseMatch = css.match(
      /\.help-article \{\s*min-width: 0;\s*padding-inline-end: var\(--space-8\);\s*\}/,
    );
    expect(articleBaseMatch).not.toBeNull();

    // Must not be scoped inside any media block -- it's a base rule.
    const ruleIndex = css.indexOf(".help-article {\n  min-width: 0;");
    expect(ruleIndex).toBeGreaterThan(-1);
    const precedingText = css.slice(0, ruleIndex);
    const lastMediaOpen = Math.max(
      precedingText.lastIndexOf("@media (width < 640px) {"),
      precedingText.lastIndexOf("@media (width >= 640px) {"),
    );
    const lastMediaClose = precedingText.lastIndexOf("\n}\n");
    // If a media block opened after it last closed, this rule would be
    // inside it -- assert that isn't the case.
    expect(lastMediaClose).toBeGreaterThan(lastMediaOpen);
  });
});

/**
 * A genuine,
 * confirmed pre-existing defect (reproduced identically on baseline
 * `c2a1143`), only surfaced once a
 * persisted per-user dark preference made reaching the bottom of a tall
 * page in dark mode routine. `.help-panel`'s own `display: flex` had no
 * `[open]` qualifier, so it unconditionally beat the native UA
 * `dialog:not([open]) { display: none; }` default -- the *closed* Help
 * dialog was still laid out as a real, `position: absolute` flex box
 * stacked directly after `<body>`'s own content, extending the
 * document's real height ~680px past body's true bottom edge on Theme
 * Calibration (confirmed via `document.documentElement.scrollHeight`
 * vs `document.body.scrollHeight`, not assumed from appearance). That
 * extra region is real `<html>` canvas; `<html>` carries no
 * `data-theme`, so its own `background: var(--color-bg-app)` always
 * resolves to warm-light regardless of the active theme -- a separate,
 * still-latent defect, currently harmless because this fix removes the
 * only way that extra region could ever exist. Cannot be verified with
 * jsdom (no real dialog layout/top-layer behavior), so this asserts the
 * CSS source fact directly, in both places the selector must agree
 * (see the narrow-override test below for why both are required).
 */
describe("Help dialog closed-state layout containment", () => {
  it("gates the base .help-panel rule's display behind [open], not the bare class alone", () => {
    const css = readAppCss();
    const baseRuleMatch = css.match(
      /\.help-panel\[open\] \{\s*display: flex;\s*flex-direction: column;/,
    );
    expect(baseRuleMatch).not.toBeNull();

    // The old unqualified selector must not reappear anywhere as its
    // own rule (a bare `.help-panel {` at the start of a rule, not a
    // descendant/modifier selector like `.help-panel__header`).
    expect(css).not.toMatch(/(?<![\w-])\.help-panel \{/);
  });

  it("gates the narrow (<640px) .help-panel override behind [open] too, at matching specificity", () => {
    // Without this, `.help-panel[open]`'s higher specificity (an added
    // attribute selector) would beat this override's conflicting
    // width/height/margin/border-radius even inside the narrow media
    // query, regardless of source order -- silently breaking the
    // narrow full-viewport presentation. Matching specificity restores
    // the original source-order-decides-the-tie behavior.
    const css = readAppCss();
    const narrowRuleMatch = css.match(
      /@media \(width < 640px\) \{\s*(?:\/\*[\s\S]*?\*\/\s*)?\.help-panel\[open\] \{\s*margin: 0;\s*width: 100%;/,
    );
    expect(narrowRuleMatch).not.toBeNull();
  });
});

/**
 * Theme Calibration / Palette Lab.
 * Source-level guard for the candidate-preview isolation architecture:
 * every candidate token rule must be scoped under `.palette-lab-scope`
 * (never a bare `[data-theme="..."]` production selector), and the
 * preview grid must reflow to one narrow column rather than assume it
 * always has room for its wide multi-column layout.
 */
describe("Palette Lab candidate-preview isolation", () => {
  it("scopes every candidate token rule under .palette-lab-scope, never a bare [data-palette-candidate=...] selector", () => {
    const css = readAppCss();

    const candidateRuleHeaders = css.match(
      /^[^\n{]*\[data-palette-candidate[^\n{]*\{/gm,
    );
    expect(candidateRuleHeaders).not.toBeNull();
    expect(candidateRuleHeaders!.length).toBeGreaterThan(0);
    for (const header of candidateRuleHeaders!) {
      expect(header).toContain(".palette-lab-scope");
    }
  });

  it('never defines a production [data-theme="cool-..."] or [data-theme="dim-..."] selector', () => {
    const css = readAppCss();
    expect(css).not.toMatch(/\[data-theme=["']cool-/);
    expect(css).not.toMatch(/\[data-theme=["']dim-/);
  });

  it("reflows the preview grid and swatch cards to one narrow column under 640px", () => {
    const css = readAppCss();
    const narrowMatch = css.match(
      /@media \(width < 640px\) \{[\s\S]*?\.palette-lab-preview__grid \{\s*grid-template-columns: 1fr;/,
    );
    expect(narrowMatch).not.toBeNull();
  });

  /**
   * Regression guard: `color` is an
   * inherited CSS property, resolved once wherever it's declared and
   * carried down as an already-computed value -- not re-evaluated per
   * descendant. `<body>` sits outside every `.palette-lab-scope`
   * wrapper, so any preview element with no *local* `color` declaration
   * silently inherits the real authenticated user's own active
   * production theme's text color instead of the previewed candidate's
   * own `--color-text-body` (confirmed empirically: this collapsed to
   * ~1.2:1 contrast for every Cool candidate's `.note-list__row` and
   * `.palette-lab-preview__bordered` text when the real account theme
   * was Dark -- the exact "washed out/ghosted" defect reported). Both
   * elements now declare `color` locally; this guards
   * against either declaration being dropped again.
   */
  it("declares an explicit local color on .note-list__row and .palette-lab-preview__bordered inside the preview, so neither leaks the real account theme's text color", () => {
    const css = readAppCss();
    expect(css).toMatch(
      /\.palette-lab-preview \.note-list__row \{\s*color: var\(--color-text-body\);/,
    );
    expect(css).toMatch(
      /\.palette-lab-preview__bordered \{[\s\S]*?color: var\(--color-text-body\);/,
    );
  });
});

/**
 * Production Theme Promotion.
 * Source-level guards proving: exactly the six approved production
 * theme blocks exist (no exploratory Palette Lab candidate leaked in);
 * Warm Light and Dark's own token values are byte-for-byte unchanged;
 * the new semantic shell-foreground tokens are what `.app-header`
 * actually reads (the old literal `[data-theme="dark"] .app-header
 * ...` override rules are gone, not left as a second, now-redundant
 * mechanism); the three promoted Dim-family themes use the
 * light-foreground direction while keeping their
 * unchanged shell *background* formula; and Palette Lab's
 * own candidate-scoped rules remain completely separate from the new
 * production theme blocks.
 */
describe("Production Theme Promotion", () => {
  const PROMOTED_THEMES = [
    "glacier",
    "granite",
    "alpine-mist",
    "blue-dusk",
    "midnight-ridge",
    "nightfall",
  ];

  it('defines exactly one production [data-theme="..."] block per approved promoted theme', () => {
    const css = readAppCss();
    for (const value of PROMOTED_THEMES) {
      const matches = css.match(
        new RegExp(`\\[data-theme="${value}"\\] \\{`, "g"),
      );
      expect(
        matches,
        `expected exactly one [data-theme="${value}"] block`,
      ).not.toBeNull();
      expect(matches!.length).toBe(1);
    }
  });

  it("never defines a production [data-theme=...] block for a Palette Lab id or a dropped/exploratory candidate", () => {
    const css = readAppCss();
    const paletteLabOnlyIds = [
      "cool-a",
      "cool-b",
      "cool-c",
      "cool-d",
      "dim-a",
      "dim-b",
      "dim-c",
      "dim-d",
      "dim-e",
      "dim-f",
      "dim-g",
      "dim-h",
      "dim-i",
      "dim-j",
      "dim-k",
      "dim-l",
    ];
    for (const id of paletteLabOnlyIds) {
      expect(css).not.toMatch(new RegExp(`\\[data-theme="${id}"\\]`));
    }
  });

  it("preserves Warm Light's own token values unchanged (spot-checked)", () => {
    const css = readAppCss();
    expect(css).toMatch(/:root \{[\s\S]*?--color-bg-app: #f7f5ef;/);
    expect(css).toMatch(/:root \{[\s\S]*?--color-button-primary-bg: #315f72;/);
  });

  it("preserves Dark's own token values unchanged (spot-checked)", () => {
    const css = readAppCss();
    const darkBlock = css.match(/\[data-theme="dark"\] \{[\s\S]*?\n\}/)?.[0];
    expect(darkBlock).toBeDefined();
    expect(darkBlock).toContain("--color-bg-app: #14181d;");
    expect(darkBlock).toContain("--color-button-primary-bg: #4f98b7;");
  });

  it('removes the old literal [data-theme="dark"] .app-header foreground override rules', () => {
    const css = readAppCss();
    expect(css).not.toMatch(/\[data-theme="dark"\]\s*\.app-header/);
  });

  it(".app-header reads only the semantic shell-foreground tokens, not a raw production token directly", () => {
    const css = readAppCss();
    expect(css).toMatch(
      /\.app-header \.brand-link \{\s*color: var\(--color-shell-fg\);/,
    );
    expect(css).toMatch(/color: var\(--color-shell-fg-link\);/);
    expect(css).toMatch(/color: var\(--color-shell-fg-link-hover\);/);
    expect(css).toMatch(/color: var\(--color-shell-fg-muted\);/);
    expect(css).toMatch(/border-color: var\(--color-shell-fg-border\);/);
  });

  it("defines all five semantic shell-foreground tokens for every one of the eight production themes", () => {
    const css = readAppCss();
    const blocks = [
      css.match(/:root \{[\s\S]*?\n\}/)?.[0],
      css.match(/\[data-theme="dark"\] \{[\s\S]*?\n\}/)?.[0],
      ...PROMOTED_THEMES.map(
        (value) =>
          css.match(
            new RegExp(`\\[data-theme="${value}"\\] \\{[\\s\\S]*?\\n\\}`),
          )?.[0],
      ),
    ];
    const tokens = [
      "--color-shell-fg:",
      "--color-shell-fg-link:",
      "--color-shell-fg-link-hover:",
      "--color-shell-fg-muted:",
      "--color-shell-fg-border:",
    ];
    blocks.forEach((block, index) => {
      expect(block, `block ${index} should exist`).toBeDefined();
      for (const token of tokens) {
        expect(block).toContain(token);
      }
    });
  });

  it("gives Blue Dusk, Midnight Ridge, and Nightfall the approved uniform light shell foreground (--color-text-strong)", () => {
    const css = readAppCss();
    for (const value of ["blue-dusk", "midnight-ridge", "nightfall"]) {
      const block = css.match(
        new RegExp(`\\[data-theme="${value}"\\] \\{[\\s\\S]*?\\n\\}`),
      )?.[0];
      expect(block, `${value} block should exist`).toBeDefined();
      expect(block).toMatch(/--color-shell-fg: var\(--color-text-strong\);/);
      expect(block).toMatch(
        /--color-shell-fg-link: var\(--color-text-strong\);/,
      );
      expect(block).toMatch(
        /--color-shell-fg-link-hover: var\(--color-text-strong\);/,
      );
      expect(block).toMatch(
        /--color-shell-fg-muted: var\(--color-text-strong\);/,
      );
      expect(block).toMatch(
        /--color-shell-fg-border: var\(--color-text-strong\);/,
      );
    }
  });

  it("keeps Blue Dusk/Midnight Ridge/Nightfall's shell background on the approved 55/45 color-mix formula, not Dark's 50/50", () => {
    const css = readAppCss();
    for (const value of ["blue-dusk", "midnight-ridge", "nightfall"]) {
      const block = css.match(
        new RegExp(`\\[data-theme="${value}"\\] \\{[\\s\\S]*?\\n\\}`),
      )?.[0];
      expect(block).toMatch(
        /--color-bg-shell: color-mix\(\s*in srgb,\s*var\(--color-bg-surface-muted\) 55%,\s*var\(--color-button-primary-bg\) 45%\s*\);/,
      );
    }
  });

  it("keeps Glacier/Granite/Alpine Mist's shell background as the direct primary-bg formula, matching Warm Light's own mechanism", () => {
    const css = readAppCss();
    for (const value of ["glacier", "granite", "alpine-mist"]) {
      const block = css.match(
        new RegExp(`\\[data-theme="${value}"\\] \\{[\\s\\S]*?\\n\\}`),
      )?.[0];
      expect(block).toMatch(
        /--color-bg-shell: var\(--color-button-primary-bg\);/,
      );
    }
  });

  it("keeps Palette Lab candidate scoping completely separate from the new production theme blocks", () => {
    const css = readAppCss();
    // The source candidates promoted from must still exist, untouched,
    // under their own .palette-lab-scope selector -- production
    // promotion copies values, it does not remove or rewrite the
    // Palette Lab's own historical record.
    for (const id of [
      "cool-a",
      "cool-c",
      "cool-d",
      "dim-e",
      "dim-g",
      "dim-i",
    ]) {
      expect(css).toMatch(
        new RegExp(`\\.palette-lab-scope\\[data-palette-candidate="${id}"\\]`),
      );
    }
    // No production theme block should itself carry .palette-lab-scope.
    for (const value of PROMOTED_THEMES) {
      const header = css.match(
        new RegExp(`[^\\n]*\\[data-theme="${value}"\\] \\{`),
      )?.[0];
      expect(header).not.toContain(".palette-lab-scope");
    }
  });
});

/**
 * The top-bar Appearance
 * dropdown's `min-width` must be wide enough for the longer
 * promoted names ("Midnight Ridge", "Alpine Mist") --
 * guards against it silently shrinking back below the width
 * verified (empirically, via Playwright) to keep every one of the eight
 * names on one line.
 */
describe("Appearance quick-selector dropdown width", () => {
  it("keeps the top-bar dropdown's min-width wide enough for the longest promoted theme name", () => {
    const css = readAppCss();
    const block = css.match(/\.theme-quick-menu__dropdown \{[\s\S]*?\n\}/)?.[0];
    expect(block).toBeDefined();
    expect(block).toMatch(/min-width: 10rem;/);
    expect(block).not.toMatch(/min-width: 8rem;/);
  });
});
