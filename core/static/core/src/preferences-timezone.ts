const ROOT_SELECTOR = "[data-preferences-timezone-root]";
const INPUT_SELECTOR = "[data-preferences-timezone-input]";
const DETECT_BUTTON_SELECTOR = "[data-preferences-timezone-detect]";
const STATUS_SELECTOR = "[data-preferences-timezone-status]";

const DETECTION_FAILED_MESSAGE =
  "Your browser did not report a timezone. Enter one manually.";

/**
 * Reads the browser's IANA timezone via `Intl.DateTimeFormat`. Returns
 * `null` on any failure (API unavailable, throws, or returns a
 * blank/non-string value) so callers can degrade safely -- never throws.
 */
export function detectBrowserTimezone(): string | null {
  try {
    const resolved = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (typeof resolved !== "string") return null;
    const trimmed = resolved.trim();
    return trimmed.length > 0 ? trimmed : null;
  } catch {
    return null;
  }
}

/**
 * Wires the Preferences page's `Use browser timezone`
 * button. Detection runs only on an explicit click, never on page load --
 * an existing saved value is never silently overwritten. The detected
 * value only ever populates the visible input; an explicit `Save`
 * submission is always required, and the server independently
 * re-validates whatever value is ultimately submitted. No
 * localStorage/sessionStorage/cookie use.
 */
export function initPreferencesTimezoneDocument(doc: Document): void {
  const root = doc.querySelector<HTMLElement>(ROOT_SELECTOR);
  if (!root) return;

  const input = root.querySelector<HTMLInputElement>(INPUT_SELECTOR);
  const detectButton = root.querySelector<HTMLButtonElement>(
    DETECT_BUTTON_SELECTOR,
  );
  if (!input || !detectButton) return;

  const status = root.querySelector<HTMLElement>(STATUS_SELECTOR);

  detectButton.addEventListener("click", () => {
    const detected = detectBrowserTimezone();
    if (!detected) {
      if (status) status.textContent = DETECTION_FAILED_MESSAGE;
      return;
    }
    if (status) status.textContent = "";
    input.value = detected;
    input.focus();
  });
}
