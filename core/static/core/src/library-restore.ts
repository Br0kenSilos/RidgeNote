const ROOT_SELECTOR = "[data-library-restore-root]";

const CONFIRMATION_PHRASE = "REPLACE MY CURRENT LIBRARY";
const RESTORE_BUTTON_IDLE_TEXT = "Restore library";
const RESTORE_BUTTON_BUSY_TEXT = "Restoring…";

// Strips characters that could make a filename lie about itself when
// rendered: ASCII control characters (U+0000-U+001F, U+007F), Unicode
// bidi/format control characters that can visually reorder or hide part of
// the string (U+202A-U+202E, U+2066-U+2069, U+200E, U+200F, U+061C), and
// the byte-order-mark (U+FEFF). Tabs/newlines are normalized to a single
// space rather than stripped, so multi-line filenames still read as one
// line instead of silently losing a word boundary. Basename-only (a
// filename can carry path separators the browser never stripped, and this
// value is for display, never for path or archive-identity use). Result is
// always rendered via `textContent`, never `innerHTML`.
export function sanitizeDisplayFilename(
  rawName: string,
  maxLength = 200,
): string {
  const basename = rawName.split(/[/\\]/).pop() ?? "";
  const normalized = basename.replace(/[\t\n\r]/g, " ");
  const stripped = normalized
    // eslint-disable-next-line no-control-regex -- deliberately stripping ASCII control chars.
    .replace(/[\u0000-\u001F\u007F]/g, "")
    .replace(/[\u200E\u200F\u061C\u202A-\u202E\u2066-\u2069\uFEFF]/g, "");
  const trimmed = stripped.trim();
  if (trimmed.length <= maxLength) return trimmed;
  return `${trimmed.slice(0, maxLength)}…`;
}

interface Elements {
  root: HTMLElement;
  form: HTMLFormElement;
  fileInput: HTMLInputElement;
  validateButton: HTMLButtonElement;
  initialInstructions: HTMLElement;
  validatedInstructions: HTMLElement;
  validatedFilenameEl: HTMLElement;
  resultRegion: HTMLElement;
  decision: HTMLElement;
  decisionContinue: HTMLElement;
  decisionCancel: HTMLElement;
  dialog: HTMLDialogElement;
  filenameEl: HTMLElement;
  dialogBody: HTMLElement;
  areYouSure: HTMLElement;
  confirmYes: HTMLElement;
  confirmNo: HTMLElement;
  phraseSection: HTMLElement;
  phraseInput: HTMLInputElement;
  submitButton: HTMLButtonElement;
  phraseCancel: HTMLButtonElement;
  dialogError: HTMLElement;
  successRegion: HTMLElement;
  closeButton: HTMLElement;
}

function queryElements(doc: Document): Elements | null {
  const root = doc.querySelector<HTMLElement>(ROOT_SELECTOR);
  if (!root) return null;

  const form = doc.querySelector<HTMLFormElement>(
    "[data-library-restore-form]",
  );
  const fileInput = doc.querySelector<HTMLInputElement>(
    "[data-library-restore-file-input]",
  );
  const validateButton = doc.querySelector<HTMLButtonElement>(
    "[data-library-restore-validate-button]",
  );
  const initialInstructions = doc.querySelector<HTMLElement>(
    "[data-library-restore-initial-instructions]",
  );
  const validatedInstructions = doc.querySelector<HTMLElement>(
    "[data-library-restore-validated-instructions]",
  );
  const validatedFilenameEl = doc.querySelector<HTMLElement>(
    "[data-library-restore-validated-filename]",
  );
  const resultRegion = doc.querySelector<HTMLElement>(
    "[data-library-restore-result-region]",
  );
  const decision = doc.querySelector<HTMLElement>(
    "[data-library-restore-decision]",
  );
  const decisionContinue = doc.querySelector<HTMLElement>(
    "[data-library-restore-decision-continue]",
  );
  const decisionCancel = doc.querySelector<HTMLElement>(
    "[data-library-restore-decision-cancel]",
  );
  const dialog = doc.querySelector<HTMLDialogElement>(
    "[data-library-restore-dialog]",
  );
  const filenameEl = doc.querySelector<HTMLElement>(
    "[data-library-restore-filename]",
  );
  const dialogBody = doc.querySelector<HTMLElement>(
    "[data-library-restore-dialog-body]",
  );
  const areYouSure = doc.querySelector<HTMLElement>(
    "[data-library-restore-are-you-sure]",
  );
  const confirmYes = doc.querySelector<HTMLElement>(
    "[data-library-restore-confirm-yes]",
  );
  const confirmNo = doc.querySelector<HTMLElement>(
    "[data-library-restore-confirm-no]",
  );
  const phraseSection = doc.querySelector<HTMLElement>(
    "[data-library-restore-phrase-section]",
  );
  const phraseInput = doc.querySelector<HTMLInputElement>(
    "[data-library-restore-phrase-input]",
  );
  const submitButton = doc.querySelector<HTMLButtonElement>(
    "[data-library-restore-submit]",
  );
  const phraseCancel = doc.querySelector<HTMLButtonElement>(
    "[data-library-restore-phrase-cancel]",
  );
  const dialogError = doc.querySelector<HTMLElement>(
    "[data-library-restore-dialog-error]",
  );
  const successRegion = doc.querySelector<HTMLElement>(
    "[data-library-restore-success]",
  );
  const closeButton = doc.querySelector<HTMLElement>(
    "[data-library-restore-close]",
  );

  if (
    !form ||
    !fileInput ||
    !validateButton ||
    !initialInstructions ||
    !validatedInstructions ||
    !validatedFilenameEl ||
    !resultRegion ||
    !decision ||
    !decisionContinue ||
    !decisionCancel ||
    !dialog ||
    !filenameEl ||
    !dialogBody ||
    !areYouSure ||
    !confirmYes ||
    !confirmNo ||
    !phraseSection ||
    !phraseInput ||
    !submitButton ||
    !phraseCancel ||
    !dialogError ||
    !successRegion ||
    !closeButton
  ) {
    return null;
  }

  return {
    root,
    form,
    fileInput,
    validateButton,
    initialInstructions,
    validatedInstructions,
    validatedFilenameEl,
    resultRegion,
    decision,
    decisionContinue,
    decisionCancel,
    dialog,
    filenameEl,
    dialogBody,
    areYouSure,
    confirmYes,
    confirmNo,
    phraseSection,
    phraseInput,
    submitButton,
    phraseCancel,
    dialogError,
    successRegion,
    closeButton,
  };
}

interface RestoreJsonResponse {
  ok: boolean;
  redirect_url?: string;
  error?: string;
}

/**
 * A single-selection, browser-held-file
 * flow. The `<input type=file>` selected once on this page is the same
 * File object used both for the async validation request below and,
 * later, for the real destructive restore POST -- the browser retains a
 * selected File across arbitrary JS/fetch activity until the user
 * explicitly reselects, the input's `value` is cleared, or the page
 * navigates, so there is never a second file chooser and never a
 * server-side staging copy.
 *
 * Validation reuses the existing `library_backup_restore_preview` POST
 * endpoint verbatim (no duplicated ZIP/manifest logic in JS): the
 * response is an ordinary HTML page, from which only the
 * `[data-library-restore-result-region]` fragment is extracted (via
 * DOMParser) and swapped into this page's own matching region. The
 * outer `<form>` and its file input are never replaced by this
 * swap -- replacing the live input node would silently drop the user's
 * File selection.
 *
 * The final destructive restore is also `fetch()`-based rather than an ordinary
 * navigation: the same `FormData(form)` -- carrying the identical File
 * object, the phrase input (associated via `form="library-restore-form"`),
 * and the CSRF token -- is POSTed with `Accept: application/json`. The
 * server (`notes/views.py`'s `library_backup_restore`) negotiates on
 * that header using the same `_wants_json_response()` helper already
 * used elsewhere in this project, and performs the exact same
 * validation/transaction/audit work regardless of response shape -- the
 * frontend's own preview state is never trusted; the server fully
 * revalidates everything from the submitted File. On success the dialog
 * transforms in place into a completed state instead of navigating
 * immediately, so the user sees direct confirmation before `Close`
 * sends them to Home.
 *
 * All validation/decision state is intentionally held only as in-memory
 * JS variables and transient DOM state -- nothing is written to
 * localStorage, sessionStorage, IndexedDB, or cookies, and no hidden
 * serialized copy of the manifest is kept; state is only ever as fresh
 * as the last successful validation of the currently-selected File.
 */
export function initLibraryRestoreDocument(
  doc: Document = document,
  navigate: (url: string) => void = (url) => {
    window.location.href = url;
  },
  buildFormData: (form: HTMLFormElement) => FormData = (f) => new FormData(f),
): boolean {
  const els = queryElements(doc);
  if (!els) return false;

  const {
    form,
    fileInput,
    validateButton,
    initialInstructions,
    validatedInstructions,
    validatedFilenameEl,
    resultRegion,
    decision,
    decisionContinue,
    decisionCancel,
    dialog,
    filenameEl,
    dialogBody,
    areYouSure,
    confirmYes,
    confirmNo,
    phraseSection,
    phraseInput,
    submitButton,
    phraseCancel,
    dialogError,
    successRegion,
    closeButton,
  } = els;

  const validateUrl = form.dataset.validateUrl ?? "";
  const restoreUrl = form.dataset.restoreUrl ?? "";
  const homeUrl = form.dataset.homeUrl ?? "/";
  const csrfToken =
    form.querySelector<HTMLInputElement>('input[name="csrfmiddlewaretoken"]')
      ?.value ?? "";

  // The one File object this entire flow is built around -- captured at
  // validation time and re-checked for identity (not just presence)
  // immediately before the final restore submission.
  let validatedFile: File | null = null;
  let validated = false;
  let restoreFocusTarget: HTMLElement | null = null;
  // True only once a successful restore JSON response has been received --
  // gates the dialog's `close` handler (Escape/backdrop/Close all route
  // through it) into navigating instead of resetting, so there is never a
  // path back onto the now-stale pre-restore preview page.
  let succeeded = false;
  let successRedirectUrl = homeUrl;
  let submitting = false;

  function resetValidationState(): void {
    validatedFile = null;
    validated = false;
    decision.hidden = true;
    resultRegion.replaceChildren();
    // The top-of-page
    // instructional text visibly tracks validation state too, not
    // just the result region further down -- reverting to the initial
    // choose-file copy happens for both a file-input change and a
    // preview Cancel.
    initialInstructions.hidden = false;
    validatedInstructions.hidden = true;
    validatedFilenameEl.textContent = "";
    validatedFilenameEl.removeAttribute("title");
  }

  function clearDialogError(): void {
    dialogError.replaceChildren();
    dialogError.hidden = true;
  }

  function showDialogError(message: string): void {
    // Replaces the node's children every time -- never appends on top of
    // the previous attempt, so a repeated failed attempt never
    // accumulates duplicate error markup. Built entirely from text
    // nodes and one `<strong>` element (never `innerHTML`, never a raw
    // markup string): if `message` contains the confirmation phrase --
    // true for the blank/wrong/old-phrase cases alike, whether the text
    // originated client-side or came verbatim from the server's own
    // safe JSON error string -- that exact substring is wrapped in a
    // real `<strong>` element; the rest of the message is plain text on
    // either side of it. A message that doesn't contain the phrase (any
    // other safe server error) renders as plain text, unchanged.
    const phrase = CONFIRMATION_PHRASE;
    const index = message.indexOf(phrase);
    const nodes: (Text | HTMLElement)[] = [];
    if (index === -1) {
      nodes.push(doc.createTextNode(message));
    } else {
      const before = message.slice(0, index);
      const after = message.slice(index + phrase.length);
      if (before) nodes.push(doc.createTextNode(before));
      const strong = doc.createElement("strong");
      strong.textContent = phrase;
      nodes.push(strong);
      if (after) nodes.push(doc.createTextNode(after));
    }
    dialogError.replaceChildren(...nodes);
    dialogError.hidden = false;
  }

  function setBusy(isBusy: boolean): void {
    submitting = isBusy;
    // Re-enabling on `setBusy(false)` no longer depends on the current
    // phrase value matching -- the button stays available for a retry
    // with any non-blank value, so the server's real error is what the
    // user sees on every subsequent attempt too, not just the first.
    submitButton.disabled = isBusy;
    phraseCancel.disabled = isBusy;
    phraseInput.disabled = isBusy;
    submitButton.textContent = isBusy
      ? RESTORE_BUTTON_BUSY_TEXT
      : RESTORE_BUTTON_IDLE_TEXT;
  }

  function resetDialog(): void {
    areYouSure.hidden = false;
    phraseSection.hidden = true;
    phraseInput.value = "";
    phraseInput.disabled = false;
    submitButton.disabled = true;
    submitButton.textContent = RESTORE_BUTTON_IDLE_TEXT;
    phraseCancel.disabled = false;
    clearDialogError();
    dialogBody.hidden = false;
    successRegion.hidden = true;
    submitting = false;
    succeeded = false;
    successRedirectUrl = homeUrl;
  }

  fileInput.addEventListener("change", () => {
    resetValidationState();
  });

  validateButton.addEventListener("click", () => {
    const files = fileInput.files;
    if (!files || files.length !== 1) return;
    const file = files[0];

    resetValidationState();

    const formData = new FormData();
    formData.append("csrfmiddlewaretoken", csrfToken);
    formData.append("backup_file", file);

    fetch(validateUrl, {
      method: "POST",
      body: formData,
      headers: { "X-CSRFToken": csrfToken },
    })
      .then((response) => response.text())
      .then((html) => {
        // Only the current file selection's own validation result is ever
        // applied -- if the user changed the file while this request was
        // in flight, `resetValidationState()` (fired by the `change`
        // listener above) already cleared `validatedFile`/`validated`,
        // and this stale response must not resurrect them.
        if (fileInput.files?.[0] !== file) return;

        const parsed = new DOMParser().parseFromString(html, "text/html");
        const newRegion = parsed.querySelector(
          "[data-library-restore-result-region]",
        );
        if (!newRegion) return;

        resultRegion.replaceChildren(...Array.from(newRegion.childNodes));

        const isValidated =
          resultRegion.querySelector(
            '[data-library-restore-validated="true"]',
          ) !== null;
        if (isValidated) {
          validatedFile = file;
          validated = true;
          decision.hidden = false;

          const sanitizedName = sanitizeDisplayFilename(file.name);
          validatedFilenameEl.textContent = sanitizedName;
          validatedFilenameEl.title = sanitizedName;
          initialInstructions.hidden = true;
          validatedInstructions.hidden = false;
        }
      })
      .catch(() => {
        // Network/parse failure: leave the result region empty rather than
        // guessing at partial content; the user can simply retry.
      });
  });

  decisionContinue.addEventListener("click", () => {
    if (!validated || !validatedFile) return;
    resetDialog();
    filenameEl.textContent = sanitizeDisplayFilename(validatedFile.name);
    restoreFocusTarget = decisionContinue;
    dialog.showModal();
  });

  // Preview-level Cancel: unlike
  // a bare "No" that just hides the decision prompt and leaves
  // a dead-end validated page behind, this fully invalidates the
  // validated preview and clears the selected File, returning the page to
  // its original choose-file/validate state -- "I don't want to proceed
  // with this backup at all," not merely "not right now."
  decisionCancel.addEventListener("click", () => {
    if (dialog.open) {
      dialog.close();
    }
    resetValidationState();
    fileInput.value = "";
    validateButton.focus();
  });

  confirmYes.addEventListener("click", () => {
    areYouSure.hidden = true;
    phraseSection.hidden = false;
    // The submit button is not disabled until the typed phrase matches:
    // a disabled `type="submit"` button
    // never dispatches a `submit` event (neither on click nor on Enter),
    // so a wrong or old phrase could never even reach `fetch()`, let
    // alone the server's real safe error message. The button is
    // enabled as soon as the phrase stage is shown; only a genuinely
    // blank value is still short-circuited, client-side, below.
    submitButton.disabled = false;
    phraseInput.focus();
  });

  confirmNo.addEventListener("click", () => {
    dialog.close();
  });

  // Final phrase-stage Cancel:
  // steps back to the `Are you sure?` stage without closing the dialog,
  // performing no mutation, and leaving the validated preview and the
  // selected File completely untouched. Distinct from confirmNo, which
  // still closes the whole dialog.
  phraseCancel.addEventListener("click", () => {
    areYouSure.hidden = false;
    phraseSection.hidden = true;
    phraseInput.value = "";
    submitButton.disabled = true;
    submitButton.textContent = RESTORE_BUTTON_IDLE_TEXT;
    clearDialogError();
    confirmYes.focus();
  });

  phraseInput.addEventListener("input", () => {
    // Clears a previously shown error as soon as the user starts
    // correcting their entry, rather than leaving stale error text
    // visible next to a value that no longer produced it.
    if (submitting) return;
    if (!dialogError.hidden) {
      clearDialogError();
    }
  });

  dialog.addEventListener("click", (event) => {
    if (event.target === dialog) {
      dialog.close();
    }
  });

  // Single point of control for every way the dialog can close: the
  // explicit Close button (success state), backdrop click, and native
  // Escape (cancel -> close) all route through this one handler. Once a
  // successful restore has been confirmed, every one of those paths
  // navigates to Home instead of resetting -- there is deliberately no
  // way to dismiss a completed dialog back onto the stale pre-restore
  // preview page underneath it.
  dialog.addEventListener("close", () => {
    if (succeeded) {
      navigate(successRedirectUrl);
      return;
    }
    resetDialog();
    restoreFocusTarget?.focus();
    restoreFocusTarget = null;
  });

  closeButton.addEventListener("click", () => {
    dialog.close();
  });

  form.addEventListener("submit", (event) => {
    // This handler only ever governs the final destructive restore
    // submission (routed to `restoreUrl`); the validate step above never
    // submits the form itself, it only ever performs its own `fetch()`.
    event.preventDefault();

    const files = fileInput.files;
    const sameFile = files?.length === 1 && files[0] === validatedFile;
    if (!validated || !sameFile || submitting) {
      return;
    }

    // A genuinely blank phrase is caught client-side -- this is the one
    // case that can be determined with total certainty before ever
    // asking the server, so it skips the round trip entirely. The
    // server's own confirmation-phrase check remains fully authoritative
    // and independently rejects a blank value on every path that reaches
    // it (including this one, if this check were ever bypassed) -- this
    // is a UX shortcut, not a weakening of server-side validation. Any
    // non-blank value, right or wrong, always goes to the server below so
    // its real safe error message (or success) is what the user sees.
    if (phraseInput.value.trim() === "") {
      showDialogError(`Type ${CONFIRMATION_PHRASE} exactly to confirm.`);
      return;
    }

    clearDialogError();

    // Captured before setBusy(true): setBusy() disables phraseInput, and a
    // disabled form control is not a "successful control" per the HTML
    // spec -- FormData(form) silently omits it. Building the FormData
    // first, then disabling controls, ensures the user's actual typed
    // phrase is what gets sent.
    const formData = buildFormData(form);

    setBusy(true);

    fetch(restoreUrl, {
      method: "POST",
      body: formData,
      headers: {
        Accept: "application/json",
        "X-CSRFToken": csrfToken,
      },
    })
      .then(async (response) => {
        const payload = (await response.json()) as RestoreJsonResponse;
        if (payload.ok) {
          succeeded = true;
          successRedirectUrl = payload.redirect_url || homeUrl;
          dialogBody.hidden = true;
          successRegion.hidden = false;
          return;
        }
        setBusy(false);
        showDialogError(
          payload.error ||
            "RidgeNote could not restore the library. Please try again.",
        );
      })
      .catch(() => {
        setBusy(false);
        showDialogError(
          "RidgeNote could not restore the library. Please try again.",
        );
      });
  });

  return true;
}
