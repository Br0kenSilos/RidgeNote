const TAG_NAME_INPUT_SELECTOR = "#note-tag-name";
const TAG_ERROR_SELECTOR = ".note-tags__error";

export interface TagErrorDismissElementLike {
  removeAttribute(name: string): void;
}

export interface TagErrorInputLike extends TagErrorDismissElementLike {
  addEventListener(type: "input", listener: () => void): void;
  dataset: { tagErrorDismissInitialized?: string };
}

export interface TagErrorParagraphLike {
  remove(): void;
}

export interface TagErrorDismissDocumentLike {
  querySelector<T extends TagErrorDismissElementLike>(
    selector: string,
  ): T | null;
}

/**
 * A duplicate-tag (or blank-
 * name) error is rendered server-side and stays on screen until the next
 * full page load, even after the user has already started correcting the
 * tag-name input that caused it -- reading as a permanently enlarged
 * metadata row rather than a transient validation state. This clears the
 * *rendered* error and the input's `aria-invalid`/`aria-describedby` the
 * moment the user changes the field at all, with no network request --
 * purely a client-side dismissal of stale UI. The server remains the only
 * authority on whether a given name is actually valid/duplicate: nothing
 * here validates anything, and submitting again re-runs the exact same
 * server-side check, which will re-render the same error if the value is
 * still a duplicate (or still blank). The attempted value and focus are
 * both left completely alone -- only the error paragraph and the two
 * `aria-*` attributes are touched, and only in the one event where they
 * change: this listener is intentionally not `{ once: true }`, since a
 * user could restore the exact duplicate value first, then edit past it,
 * and each of those is a real "the user changed the field" moment even if
 * `.note-tags__error` is already gone by the second one (removal is a
 * no-op on a null query the second time).
 */
export function initTagErrorDismissDocument(
  doc: TagErrorDismissDocumentLike = typeof document !== "undefined"
    ? (document as unknown as TagErrorDismissDocumentLike)
    : { querySelector: () => null },
): boolean {
  const input = doc.querySelector<TagErrorInputLike>(TAG_NAME_INPUT_SELECTOR);
  if (!input) {
    return false;
  }
  if (input.dataset.tagErrorDismissInitialized === "true") {
    return false;
  }
  input.dataset.tagErrorDismissInitialized = "true";

  input.addEventListener("input", () => {
    const error = doc.querySelector<
      TagErrorParagraphLike & TagErrorDismissElementLike
    >(TAG_ERROR_SELECTOR);
    if (!error) {
      return;
    }
    error.remove();
    input.removeAttribute("aria-invalid");
    input.removeAttribute("aria-describedby");
  });

  return true;
}
