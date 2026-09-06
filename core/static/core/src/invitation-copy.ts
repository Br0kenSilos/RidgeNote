import {
  copyTextWithFallback,
  type CopyDocumentLike,
  type NavigatorLike,
} from "./note-editor";

const TRIGGER_SELECTOR = "[data-copy-invitation-trigger]";
const COPIED_LABEL = "Copied";
const RESET_DELAY_MS = 1500;

/**
 * The "Copy link" button beside a one-time
 * invitation URL. The readonly source field is always present and
 * always manually selectable/copyable without JavaScript; this wiring
 * is a progressive-enhancement convenience only, reusing
 * `note-editor.ts`'s existing clipboard-with-fallback helper rather
 * than a second, divergent implementation.
 */
export function initInvitationCopyDocument(doc: Document = document): void {
  const triggers = doc.querySelectorAll<HTMLButtonElement>(TRIGGER_SELECTOR);
  triggers.forEach((trigger) => {
    const targetId = trigger.dataset.copyTarget;
    if (!targetId) {
      return;
    }
    const source = doc.getElementById(targetId) as HTMLInputElement | null;
    if (!source) {
      return;
    }
    const originalLabel = trigger.textContent ?? "Copy";
    let resetTimer: ReturnType<typeof setTimeout> | undefined;
    trigger.addEventListener("click", () => {
      source.select();
      void copyTextWithFallback(source.value, {
        documentLike: doc as unknown as CopyDocumentLike,
        navigatorLike: window.navigator as NavigatorLike,
      }).then((copied) => {
        if (!copied) {
          return;
        }
        trigger.textContent = COPIED_LABEL;
        if (resetTimer) {
          clearTimeout(resetTimer);
        }
        resetTimer = setTimeout(() => {
          trigger.textContent = originalLabel;
        }, RESET_DELAY_MS);
      });
    });
  });
}
