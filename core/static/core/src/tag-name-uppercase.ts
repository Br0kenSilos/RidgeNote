const TAG_NAME_INPUT_SELECTOR = "[data-tag-name-input]";
const TAG_NAME_LENGTH_WARNING_SELECTOR = "[data-tag-name-length-warning]";
const TAG_NAME_LENGTH_WARNING_ID = "note-tag-length-warning";
const MAX_TAG_NAME_LENGTH = 40;
const TAG_NAME_LENGTH_MESSAGE = `Tag name must be ${MAX_TAG_NAME_LENGTH} characters or fewer.`;

export interface TagNameUppercaseInputLike {
  value: string;
  selectionStart: number | null;
  selectionEnd: number | null;
  addEventListener(type: "input", listener: () => void): void;
  dataset: { tagNameUppercaseInitialized?: string };
  setAttribute(name: string, value: string): void;
  removeAttribute(name: string): void;
}

export interface TagNameLengthWarningElementLike {
  hidden: boolean;
  textContent: string;
}

export interface TagNameUppercaseDocumentLike {
  querySelector<T>(selector: string): T | null;
}

/** Pure transform, exported for direct unit testing. */
export function uppercaseTagNameValue(value: string): string {
  return value.toUpperCase();
}

/**
 * The same conceptual length the server will
 * ultimately check -- outer whitespace trimmed, repeated internal
 * whitespace collapsed to one space -- mirroring
 * `normalize_tag_name()`'s own whitespace handling (not a re-validation
 * of characters; this module never duplicates the server's
 * control/bidi-character rejection, only the length rule it directly
 * causes visible, immediate feedback for). Exported for direct unit
 * testing.
 */
export function normalizedTagNameLength(value: string): number {
  return value.trim().replace(/\s+/g, " ").length;
}

/**
 * Live-while-typing uppercase for the Add Tag
 * input, purely a display convenience -- the server (`normalize_tag_name()`)
 * remains the sole authority on the actual stored name; this never
 * validates characters, trims, or collapses whitespace in the value
 * itself, and submitting the form always re-normalizes server-side
 * regardless of what this transform did. A single `input` listener
 * (fired for both typed characters and pasted content alike, since
 * paste already updates `.value` before `input` fires) keeps typing
 * and paste behavior identical with no separate paste handler needed.
 * Cursor position is explicitly restored after reassigning `.value`,
 * since a naive reassignment can otherwise jump some browsers' caret to
 * the end of the field.
 *
 * The same listener also drives immediate, non-authoritative
 * over-length feedback: if a permanently-present (template-rendered,
 * normally `hidden`) warning element matching
 * `[data-tag-name-length-warning]` exists, it is shown/hidden and its
 * text set/cleared based on `normalizedTagNameLength()` exceeding
 * `MAX_TAG_NAME_LENGTH` -- the exact same message and length rule the
 * server enforces. Never truncates the input, never auto-submits; a
 * value that is still too long when submitted is rejected by the
 * server exactly as before, this is purely earlier feedback.
 */
export function initTagNameUppercaseDocument(
  doc: TagNameUppercaseDocumentLike = typeof document !== "undefined"
    ? (document as unknown as TagNameUppercaseDocumentLike)
    : { querySelector: () => null },
): boolean {
  const input = doc.querySelector<TagNameUppercaseInputLike>(
    TAG_NAME_INPUT_SELECTOR,
  );
  if (!input) {
    return false;
  }
  if (input.dataset.tagNameUppercaseInitialized === "true") {
    return false;
  }
  input.dataset.tagNameUppercaseInitialized = "true";

  const lengthWarning = doc.querySelector<TagNameLengthWarningElementLike>(
    TAG_NAME_LENGTH_WARNING_SELECTOR,
  );

  input.addEventListener("input", () => {
    const before = input.value;
    const after = uppercaseTagNameValue(before);
    if (after !== before) {
      const selectionStart = input.selectionStart;
      const selectionEnd = input.selectionEnd;
      input.value = after;
      if (selectionStart !== null) {
        input.selectionStart = selectionStart;
      }
      if (selectionEnd !== null) {
        input.selectionEnd = selectionEnd;
      }
    }

    if (!lengthWarning) {
      return;
    }
    const tooLong = normalizedTagNameLength(after) > MAX_TAG_NAME_LENGTH;
    lengthWarning.hidden = !tooLong;
    lengthWarning.textContent = tooLong ? TAG_NAME_LENGTH_MESSAGE : "";
    if (tooLong) {
      input.setAttribute("aria-invalid", "true");
      input.setAttribute("aria-describedby", TAG_NAME_LENGTH_WARNING_ID);
    } else {
      input.removeAttribute("aria-invalid");
      input.removeAttribute("aria-describedby");
    }
  });

  return true;
}
