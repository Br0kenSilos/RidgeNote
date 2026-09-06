export const TREE_FILTER_ACTIVE_TARGET_CLASS =
  "tree-nav__folder-row--drop-active";

export interface TreeFilterNoteInfo {
  title: string;
}

export interface TreeFilterGroupInfo {
  title: string;
  notes: TreeFilterNoteInfo[];
}

export interface TreeFilterTreeInfo {
  groups: TreeFilterGroupInfo[];
}

export interface TreeFilterGroupVisibility {
  visible: boolean;
  noteVisibility: boolean[];
}

export interface TreeFilterResult {
  groups: TreeFilterGroupVisibility[];
  noMatches: boolean;
}

function normalize(text: string): string {
  return text.trim().toLowerCase();
}

/**
 * Pure tree-title matching: no DOM, fully unit-testable. A group (a folder
 * or the Unfiled section -- both share the same title-plus-notes shape)
 * stays visible when its own title matches, showing every note inside it,
 * or when at least one child note matches, in which case only matching
 * notes stay visible and non-matching siblings hide.
 */
export function computeTreeFilterResult(
  tree: TreeFilterTreeInfo,
  filterText: string,
): TreeFilterResult {
  const needle = normalize(filterText);

  if (needle === "") {
    return {
      groups: tree.groups.map((group) => ({
        visible: true,
        noteVisibility: group.notes.map(() => true),
      })),
      noMatches: false,
    };
  }

  const groups = tree.groups.map((group) => {
    const titleMatches = normalize(group.title).includes(needle);
    const noteMatchFlags = group.notes.map((note) =>
      normalize(note.title).includes(needle),
    );
    if (titleMatches) {
      return { visible: true, noteVisibility: group.notes.map(() => true) };
    }
    if (noteMatchFlags.some(Boolean)) {
      return { visible: true, noteVisibility: noteMatchFlags };
    }
    return { visible: false, noteVisibility: group.notes.map(() => false) };
  });

  return {
    groups,
    noMatches: !groups.some((group) => group.visible),
  };
}

interface TreeFilterGroupElements {
  details: HTMLDetailsElement | null;
  element: HTMLElement;
  noteElements: HTMLElement[];
  titleText: string;
}

/**
 * Remembers each group's open/closed state from just before a filtering
 * session began, so clearing the filter can restore it exactly -- rather
 * than leaving every match-forced-open folder permanently open. Callers
 * should keep one instance per tree-nav container (never shared between
 * the wide tree and narrow drawer copies) and reuse it across every
 * keystroke for that container's lifetime.
 */
export type TreeFilterOpenStateMap = WeakMap<HTMLDetailsElement, boolean>;

function readGroupElements(container: Element): TreeFilterGroupElements[] {
  const groupLis = Array.from(
    container.querySelectorAll<HTMLElement>(
      ":scope > .tree-nav__viewport > .tree-nav__list > li",
    ),
  );

  // Two group shapes coexist here: the Unfiled group nests its
  // <summary class="tree-nav__folder-row"> and <ul class="tree-nav__notes">
  // directly inside a single <details>. Real folders instead keep the
  // disclosure <details>
  // and the folder three-dot menu as two siblings inside a wrapping
  // <div class="tree-nav__folder-row">, with the notes <ul> (now
  // "tree-nav__folder-notes") pulled out as a sibling of that row so the
  // menu trigger stays reachable while the folder is collapsed. Both shapes
  // are handled here rather than only one, since Unfiled deliberately
  // keeps its own original structure.
  return groupLis.map((element) => {
    const details = element.querySelector<HTMLDetailsElement>(
      "details.tree-nav__folder-details",
    );
    const titleEl = element.querySelector<HTMLElement>(
      "details.tree-nav__folder-details > summary .tree-nav__label",
    );
    const noteElements = Array.from(
      element.querySelectorAll<HTMLElement>(
        [
          ":scope > ul.tree-nav__folder-notes > li.tree-nav__note-item",
          ":scope > details.tree-nav__folder-details > ul.tree-nav__notes > li.tree-nav__note-item",
        ].join(", "),
      ),
    );
    return {
      details,
      element,
      noteElements,
      titleText: titleEl?.textContent ?? "",
    };
  });
}

function readNoteTitle(noteEl: HTMLElement): string {
  return (
    noteEl.querySelector<HTMLElement>(".tree-nav__note-link .tree-nav__label")
      ?.textContent ?? ""
  );
}

/**
 * Applies the filter to one `.tree-nav` container's already-rendered DOM.
 * Each container (wide tree, narrow drawer) is filtered fully independently
 * -- no shared or synced state between copies.
 *
 * `openState` remembers each group's pre-filter open/closed state so a
 * folder revealed by a match (either its own title or a child note)
 * actually opens instead of merely losing its `hidden` attribute, and so
 * clearing the filter restores the tree to how it looked before filtering
 * began rather than leaving every matched folder permanently open. Pass the
 * same map instance across every keystroke for one container; never share
 * it between the wide tree and narrow drawer copies.
 */
export function applyTreeFilterToContainer(
  container: Element,
  filterText: string,
  openState: TreeFilterOpenStateMap = new WeakMap(),
): void {
  const groupElements = readGroupElements(container);
  const tree: TreeFilterTreeInfo = {
    groups: groupElements.map((group) => ({
      title: group.titleText,
      notes: group.noteElements.map((noteEl) => ({
        title: readNoteTitle(noteEl),
      })),
    })),
  };

  const result = computeTreeFilterResult(tree, filterText);
  const isFiltering = filterText.trim() !== "";

  groupElements.forEach((group, groupIndex) => {
    const visibility = result.groups[groupIndex];
    group.element.hidden = !visibility.visible;
    if (!visibility.visible) {
      const row = group.element.querySelector<HTMLElement>(
        ".tree-nav__folder-row",
      );
      row?.classList.remove(TREE_FILTER_ACTIVE_TARGET_CLASS);
    }
    group.noteElements.forEach((noteEl, noteIndex) => {
      const noteVisible = visibility.noteVisibility[noteIndex] ?? true;
      noteEl.hidden = !noteVisible;
    });

    if (group.details) {
      if (isFiltering) {
        if (!openState.has(group.details)) {
          openState.set(group.details, group.details.open);
        }
        if (visibility.visible) {
          group.details.open = true;
        }
      } else if (openState.has(group.details)) {
        group.details.open = openState.get(group.details) ?? false;
        openState.delete(group.details);
      }
    }
  });

  const emptyMessage = container.querySelector<HTMLElement>(
    "[data-tree-filter-empty]",
  );
  if (emptyMessage) {
    emptyMessage.hidden = !result.noMatches;
  }
}

/**
 * Wires the filter input in every `.tree-nav` container found in `doc`.
 * Each container's input only ever affects its own container, so the wide
 * tree and narrow drawer copies never share or sync filter state.
 */
export function initTreeFilterDocument(doc: Document = document): boolean {
  let initializedAny = false;
  const containers = Array.from(doc.querySelectorAll<HTMLElement>(".tree-nav"));

  for (const container of containers) {
    if (container.dataset.filterInitialized === "true") {
      continue;
    }
    const input = container.querySelector<HTMLInputElement>(
      "[data-tree-filter-input]",
    );
    if (!input) {
      continue;
    }

    const openState: TreeFilterOpenStateMap = new WeakMap();
    input.addEventListener("input", () => {
      applyTreeFilterToContainer(container, input.value, openState);
    });

    container.dataset.filterInitialized = "true";
    initializedAny = true;
  }

  return initializedAny;
}
