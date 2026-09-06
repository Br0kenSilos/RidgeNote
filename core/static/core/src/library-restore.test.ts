import { afterEach, describe, expect, it, vi } from "vitest";

import {
  initLibraryRestoreDocument,
  sanitizeDisplayFilename,
} from "./library-restore";

// ---------------------------------------------------------------------------
// sanitizeDisplayFilename
// ---------------------------------------------------------------------------

describe("sanitizeDisplayFilename", () => {
  it("returns an ordinary filename unchanged", () => {
    expect(sanitizeDisplayFilename("my-backup.zip")).toBe("my-backup.zip");
  });

  it("strips a Windows-style path down to the basename", () => {
    expect(
      sanitizeDisplayFilename("C:\\Users\\me\\Downloads\\backup.zip"),
    ).toBe("backup.zip");
  });

  it("strips a POSIX-style path down to the basename", () => {
    expect(sanitizeDisplayFilename("/home/me/backup.zip")).toBe("backup.zip");
  });

  it("strips ASCII control characters", () => {
    expect(sanitizeDisplayFilename("backup\u0007\u001f.zip")).toBe(
      "backup.zip",
    );
  });

  it("strips the DEL character", () => {
    expect(sanitizeDisplayFilename("backup\u007f.zip")).toBe("backup.zip");
  });

  it("strips Unicode bidi/format control characters", () => {
    expect(
      sanitizeDisplayFilename(
        "backup\u202E\u202A\u2066\u2069\u200E\u200F\u061C\uFEFF.zip",
      ),
    ).toBe("backup.zip");
  });

  it("normalizes embedded tabs and newlines to spaces", () => {
    expect(sanitizeDisplayFilename("back\tup\nname\r.zip")).toBe(
      "back up name .zip",
    );
  });

  it("trims leading/trailing whitespace produced by normalization", () => {
    expect(sanitizeDisplayFilename("  backup.zip  ")).toBe("backup.zip");
  });

  it("truncates very long filenames and appends an ellipsis", () => {
    const longName = `${"a".repeat(250)}.zip`;
    const result = sanitizeDisplayFilename(longName, 200);
    expect(result.length).toBe(201);
    expect(result.endsWith("…")).toBe(true);
  });

  it("does not truncate a filename at exactly the max length", () => {
    const name = "a".repeat(200);
    expect(sanitizeDisplayFilename(name, 200)).toBe(name);
  });
});

// ---------------------------------------------------------------------------
// initLibraryRestoreDocument
// ---------------------------------------------------------------------------

type Handler = (event?: unknown) => void;

function makeListenerTarget() {
  const handlers: Record<string, Handler[]> = {};
  return {
    addEventListener: vi.fn((type: string, h: Handler) => {
      (handlers[type] ??= []).push(h);
    }),
    fire(type: string, event?: unknown) {
      (handlers[type] ?? []).forEach((h) => h(event));
    },
  };
}

function makeButton() {
  return {
    ...makeListenerTarget(),
    focus: vi.fn(),
    disabled: false,
    textContent: "",
  };
}

function makeTextEl() {
  return { textContent: "" };
}

// Mirrors real DOM `.textContent`'s behavior of concatenating every
// descendant text node -- production code now builds the error message
// out of text nodes plus one `<strong>` element (never `innerHTML`), so
// this mock's own `textContent` getter-equivalent must reflect that same
// concatenation for existing assertions to keep meaning what they say.
function makeErrorRegion() {
  const region = {
    hidden: true,
    textContent: "",
    replaceChildren: vi.fn((...nodes: { textContent: string }[]) => {
      region.textContent = nodes.map((n) => n.textContent).join("");
    }),
  };
  return region;
}

function makeContainer() {
  const listener = makeListenerTarget();
  const children: { __validated?: boolean }[] = [];
  return {
    ...listener,
    hidden: false,
    replaceChildren: vi.fn((...nodes: { __validated?: boolean }[]) => {
      children.length = 0;
      children.push(...nodes);
    }),
    querySelector: vi.fn((sel: string) => {
      if (sel === '[data-library-restore-validated="true"]') {
        return children.some((c) => c.__validated) ? {} : null;
      }
      return null;
    }),
    _children: children,
  };
}

function makeFileInput() {
  const listener = makeListenerTarget();
  return {
    ...listener,
    files: [] as { name: string }[],
    value: "",
  };
}

function makeDialog() {
  const listener = makeListenerTarget();
  const dialog = {
    ...listener,
    open: false,
    showModal: vi.fn(function (this: { open: boolean }) {
      this.open = true;
    }),
    close: vi.fn(function (this: { open: boolean; fire: (t: string) => void }) {
      this.open = false;
      this.fire("close");
    }),
  };
  return dialog;
}

function makeForm(dataset: Record<string, string>) {
  const listener = makeListenerTarget();
  const csrfInput = { value: "csrf-token-value" };
  return {
    ...listener,
    dataset,
    action: "",
    querySelector: vi.fn((sel: string) =>
      sel === 'input[name="csrfmiddlewaretoken"]' ? csrfInput : null,
    ),
  };
}

function setup(
  opts: {
    dataset?: Record<string, string>;
    missing?: string[];
  } = {},
) {
  const { dataset = {}, missing = [] } = opts;

  const root = {};
  const form = makeForm({
    validateUrl: "/library/restore/preview/",
    restoreUrl: "/library/restore/",
    homeUrl: "/",
    ...dataset,
  });
  const fileInput = makeFileInput();
  const validateButton = makeButton();
  const initialInstructions = { hidden: false };
  const validatedInstructions = { hidden: true };
  const validatedFilenameEl = {
    textContent: "",
    title: "",
    removeAttribute: vi.fn(function (this: { title: string }, name: string) {
      if (name === "title") this.title = "";
    }),
  };
  const resultRegion = makeContainer();
  const decision = { hidden: true };
  const decisionContinue = makeButton();
  const decisionCancel = makeButton();
  const dialog = makeDialog();
  const filenameEl = makeTextEl();
  const dialogBody = { hidden: false };
  const areYouSure = { hidden: false };
  const confirmYes = makeButton();
  const confirmNo = makeButton();
  const phraseSection = { hidden: true };
  const phraseInput = {
    ...makeListenerTarget(),
    value: "",
    focus: vi.fn(),
    disabled: false,
  };
  const submitButton = makeButton();
  submitButton.disabled = true;
  const phraseCancel = makeButton();
  const dialogError = makeErrorRegion();
  const successRegion = { hidden: true };
  const closeButton = makeButton();

  const registry: Record<string, unknown> = {
    "[data-library-restore-root]": root,
    "[data-library-restore-form]": form,
    "[data-library-restore-file-input]": fileInput,
    "[data-library-restore-validate-button]": validateButton,
    "[data-library-restore-initial-instructions]": initialInstructions,
    "[data-library-restore-validated-instructions]": validatedInstructions,
    "[data-library-restore-validated-filename]": validatedFilenameEl,
    "[data-library-restore-result-region]": resultRegion,
    "[data-library-restore-decision]": decision,
    "[data-library-restore-decision-continue]": decisionContinue,
    "[data-library-restore-decision-cancel]": decisionCancel,
    "[data-library-restore-dialog]": dialog,
    "[data-library-restore-filename]": filenameEl,
    "[data-library-restore-dialog-body]": dialogBody,
    "[data-library-restore-are-you-sure]": areYouSure,
    "[data-library-restore-confirm-yes]": confirmYes,
    "[data-library-restore-confirm-no]": confirmNo,
    "[data-library-restore-phrase-section]": phraseSection,
    "[data-library-restore-phrase-input]": phraseInput,
    "[data-library-restore-submit]": submitButton,
    "[data-library-restore-phrase-cancel]": phraseCancel,
    "[data-library-restore-dialog-error]": dialogError,
    "[data-library-restore-success]": successRegion,
    "[data-library-restore-close]": closeButton,
  };

  for (const key of missing) {
    registry[key] = null;
  }

  const doc = {
    querySelector: vi.fn((sel: string) => registry[sel] ?? null),
    createTextNode: vi.fn((text: string) => ({ textContent: text })),
    createElement: vi.fn((tagName: string) => ({
      tagName: tagName.toUpperCase(),
      textContent: "",
    })),
  };

  const navigate = vi.fn();

  // Real `FormData` always converts/wraps a non-Blob value on `.append()`,
  // so a real File's identity can't survive a real round trip in this
  // Node test environment -- this stub stands in for the seam where
  // production code calls `new FormData(form)`, and separately stashes
  // the exact file reference it was given (via closure over `fileInput`)
  // on a non-standard property so tests can assert on File *identity*
  // without depending on FormData's own (de)serialization behavior.
  const buildFormData = vi.fn((): FormData => {
    const fd = new FormData();
    Object.defineProperty(fd, "__testFile", {
      value: fileInput.files?.[0],
      enumerable: false,
    });
    // Faithfully mirrors real `FormData(form)` semantics for the one
    // property this regression suite cares about: a *disabled* form
    // control is not a "successful control" and is silently excluded. If
    // production code ever regresses back to disabling `phraseInput`
    // before calling this, the captured value below reverts to
    // `undefined`, exactly reproducing the real browser defect.
    Object.defineProperty(fd, "__testConfirmation", {
      value: phraseInput.disabled ? undefined : phraseInput.value,
      enumerable: false,
    });
    return fd;
  });

  const result = initLibraryRestoreDocument(
    doc as unknown as Document,
    navigate,
    buildFormData as unknown as (form: HTMLFormElement) => FormData,
  );

  return {
    result,
    navigate,
    buildFormData,
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

const originalFetch = globalThis.fetch;
const originalDOMParser = globalThis.DOMParser;

afterEach(() => {
  globalThis.fetch = originalFetch;
  globalThis.DOMParser = originalDOMParser;
  vi.restoreAllMocks();
});

function stubFetchAndParser(opts: { validated: boolean }) {
  const fetchMock = vi.fn(() =>
    Promise.resolve({ text: () => Promise.resolve("<html></html>") }),
  );
  globalThis.fetch = fetchMock as unknown as typeof fetch;

  const fakeParsedRegion = {
    childNodes: [{ __validated: opts.validated }],
  };
  const fakeParsedDoc = {
    querySelector: vi.fn((sel: string) =>
      sel === "[data-library-restore-result-region]" ? fakeParsedRegion : null,
    ),
  };
  class FakeDOMParser {
    parseFromString() {
      return fakeParsedDoc as unknown as Document;
    }
  }
  globalThis.DOMParser = FakeDOMParser as unknown as typeof DOMParser;
  return fetchMock;
}

function stubRestoreFetch(payload: unknown) {
  const fetchMock = vi.fn(() =>
    Promise.resolve({ json: () => Promise.resolve(payload) }),
  );
  globalThis.fetch = fetchMock as unknown as typeof fetch;
  return fetchMock;
}

async function validateAndOpenDialog(
  env: ReturnType<typeof setup>,
  file: { name: string } = { name: "backup.zip" },
) {
  env.fileInput.files = [file] as unknown as File[];
  stubFetchAndParser({ validated: true });
  env.validateButton.fire("click");
  await vi.waitFor(() => expect(env.decision.hidden).toBe(false));
  env.decisionContinue.fire("click");
  return file;
}

function revealPhraseStage(env: ReturnType<typeof setup>) {
  env.confirmYes.fire("click");
  env.phraseInput.value = "REPLACE MY CURRENT LIBRARY";
  env.phraseInput.fire("input");
}

// Asserts the confirmation phrase was rendered as a real `<strong>`
// element among the nodes handed to `replaceChildren` -- not merely
// that the concatenated text happens to contain it. This is what
// proves the phrase is actually emphasized DOM structure (built via
// `createElement`/`createTextNode`, never a raw markup string assigned
// through `innerHTML`), for the blank/wrong/old-phrase cases alike.
function expectPhraseEmphasized(dialogError: {
  replaceChildren: ReturnType<typeof vi.fn>;
}): void {
  const lastCall = dialogError.replaceChildren.mock.calls.at(-1) as
    | { tagName?: string; textContent: string }[]
    | undefined;
  expect(lastCall).toBeDefined();
  const strongNode = lastCall?.find((node) => node.tagName === "STRONG");
  expect(strongNode).toBeDefined();
  expect(strongNode?.textContent).toBe("REPLACE MY CURRENT LIBRARY");
}

describe("initLibraryRestoreDocument – missing prerequisites", () => {
  it("returns false when the root element is absent", () => {
    const { result } = setup({ missing: ["[data-library-restore-root]"] });
    expect(result).toBe(false);
  });

  it("returns false when a required dialog part is absent", () => {
    const { result } = setup({
      missing: ["[data-library-restore-phrase-input]"],
    });
    expect(result).toBe(false);
  });

  it("returns false when the phrase-cancel button is absent", () => {
    const { result } = setup({
      missing: ["[data-library-restore-phrase-cancel]"],
    });
    expect(result).toBe(false);
  });

  it("returns false when the success region is absent", () => {
    const { result } = setup({
      missing: ["[data-library-restore-success]"],
    });
    expect(result).toBe(false);
  });

  it("returns false when the validated-instructions block is absent", () => {
    const { result } = setup({
      missing: ["[data-library-restore-validated-instructions]"],
    });
    expect(result).toBe(false);
  });

  it("returns true when every required element is present", () => {
    const { result } = setup();
    expect(result).toBe(true);
  });
});

describe("initLibraryRestoreDocument – file selection invalidation", () => {
  it("clears the result region and hides the decision prompt when the file input changes", () => {
    const { fileInput, resultRegion, decision } = setup();
    decision.hidden = false;
    fileInput.fire("change");
    expect(resultRegion.replaceChildren).toHaveBeenCalled();
    expect(decision.hidden).toBe(true);
  });
});

describe("initLibraryRestoreDocument – validation flow", () => {
  it("does nothing if no file is selected", () => {
    const { validateButton, resultRegion } = setup();
    const fetchMock = stubFetchAndParser({ validated: true });
    validateButton.fire("click");
    expect(fetchMock).not.toHaveBeenCalled();
    expect(resultRegion.replaceChildren).not.toHaveBeenCalled();
  });

  it("posts the selected file via FormData to the validate URL with the CSRF header", async () => {
    const { fileInput, validateButton, form } = setup();
    fileInput.files = [{ name: "backup.zip" }] as unknown as File[];
    const fetchMock = stubFetchAndParser({ validated: true });

    validateButton.fire("click");
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalled());

    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(form.dataset.validateUrl);
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["X-CSRFToken"]).toBe(
      "csrf-token-value",
    );
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("reveals the decision prompt on a validated response", async () => {
    const { fileInput, validateButton, decision } = setup();
    fileInput.files = [{ name: "backup.zip" }] as unknown as File[];
    stubFetchAndParser({ validated: true });

    validateButton.fire("click");
    await vi.waitFor(() => expect(decision.hidden).toBe(false));
  });

  it("keeps the decision prompt hidden on a failed validation response", async () => {
    const { fileInput, validateButton, decision, resultRegion } = setup();
    fileInput.files = [{ name: "backup.zip" }] as unknown as File[];
    stubFetchAndParser({ validated: false });

    validateButton.fire("click");
    await vi.waitFor(() =>
      expect(resultRegion.replaceChildren).toHaveBeenCalled(),
    );
    expect(decision.hidden).toBe(true);
  });

  it("ignores a stale validation response after the file selection changed mid-flight", async () => {
    const { fileInput, validateButton, decision } = setup();
    const originalFile = { name: "backup.zip" };
    fileInput.files = [originalFile] as unknown as File[];
    stubFetchAndParser({ validated: true });

    validateButton.fire("click");
    // Simulate the user swapping files before the fetch resolves.
    fileInput.files = [{ name: "other.zip" }] as unknown as File[];

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(decision.hidden).toBe(true);
  });
});

describe("initLibraryRestoreDocument – top-of-page instructions toggle", () => {
  it("starts with the initial instructions visible and the validated block hidden", () => {
    const { initialInstructions, validatedInstructions } = setup();
    expect(initialInstructions.hidden).toBe(false);
    expect(validatedInstructions.hidden).toBe(true);
  });

  it("switches to the validated block and shows the sanitized filename on a successful validation", async () => {
    const env = setup();
    env.fileInput.files = [{ name: "My Backup.zip" }] as unknown as File[];
    stubFetchAndParser({ validated: true });

    env.validateButton.fire("click");
    await vi.waitFor(() => expect(env.decision.hidden).toBe(false));

    expect(env.initialInstructions.hidden).toBe(true);
    expect(env.validatedInstructions.hidden).toBe(false);
    expect(env.validatedFilenameEl.textContent).toBe("My Backup.zip");
    expect(env.validatedFilenameEl.title).toBe("My Backup.zip");
  });

  it("keeps the initial instructions visible on a failed validation", async () => {
    const env = setup();
    env.fileInput.files = [{ name: "backup.zip" }] as unknown as File[];
    stubFetchAndParser({ validated: false });

    env.validateButton.fire("click");
    await vi.waitFor(() =>
      expect(env.resultRegion.replaceChildren).toHaveBeenCalled(),
    );

    expect(env.initialInstructions.hidden).toBe(false);
    expect(env.validatedInstructions.hidden).toBe(true);
  });

  it("reverts to the initial instructions when the file selection changes after a successful validation", async () => {
    const env = setup();
    env.fileInput.files = [{ name: "backup.zip" }] as unknown as File[];
    stubFetchAndParser({ validated: true });
    env.validateButton.fire("click");
    await vi.waitFor(() =>
      expect(env.validatedInstructions.hidden).toBe(false),
    );

    env.fileInput.fire("change");

    expect(env.initialInstructions.hidden).toBe(false);
    expect(env.validatedInstructions.hidden).toBe(true);
    expect(env.validatedFilenameEl.textContent).toBe("");
  });

  it("reverts to the initial instructions on preview-level Cancel", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmNo.fire("click");
    expect(env.validatedInstructions.hidden).toBe(false);

    env.decisionCancel.fire("click");

    expect(env.initialInstructions.hidden).toBe(false);
    expect(env.validatedInstructions.hidden).toBe(true);
  });
});

describe("initLibraryRestoreDocument – decision and dialog flow", () => {
  it("opens the dialog and shows the sanitized filename on decision Yes", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    expect(env.dialog.showModal).toHaveBeenCalledOnce();
    expect(env.filenameEl.textContent).toBe("backup.zip");
  });

  it("does not open the dialog if no validated file exists yet", () => {
    const env = setup();
    env.decisionContinue.fire("click");
    expect(env.dialog.showModal).not.toHaveBeenCalled();
  });

  it("reveals the phrase section only after the dialog's own Are-you-sure Yes", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    expect(env.phraseSection.hidden).toBe(true);
    env.confirmYes.fire("click");
    expect(env.phraseSection.hidden).toBe(false);
    expect(env.areYouSure.hidden).toBe(true);
  });

  it("closes the dialog on modal No ('Do you wish to proceed?')", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmNo.fire("click");
    expect(env.dialog.close).toHaveBeenCalledOnce();
  });

  it("modal No performs no mutation, keeps the validated preview and selected File, and returns focus to Continue", async () => {
    const env = setup();
    const file = await validateAndOpenDialog(env);
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    env.confirmNo.fire("click");

    expect(fetchMock).not.toHaveBeenCalled();
    expect(env.fileInput.files?.[0]).toBe(file);
    expect(env.decisionContinue.focus).toHaveBeenCalledOnce();
    // Preview-level Continue/Cancel remain available -- the decision
    // prompt itself was never touched by a modal-level dismissal.
    expect(env.decision.hidden).toBe(false);
  });

  it("closes the dialog on a backdrop click (pre-success)", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.dialog.fire("click", { target: env.dialog });
    expect(env.dialog.close).toHaveBeenCalledOnce();
  });

  it("does not close the dialog on a click inside its own content", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.dialog.fire("click", { target: {} });
    expect(env.dialog.close).not.toHaveBeenCalled();
  });

  it("resets the phrase section and disables submit when the dialog closes normally (pre-success)", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmYes.fire("click");
    env.phraseInput.value = "REPLACE MY CURRENT LIBRARY";
    env.submitButton.disabled = false;

    env.dialog.close();

    expect(env.areYouSure.hidden).toBe(false);
    expect(env.phraseSection.hidden).toBe(true);
    expect(env.phraseInput.value).toBe("");
    expect(env.submitButton.disabled).toBe(true);
  });

  it("pre-success Escape (a plain close()) still resets normally rather than navigating", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.dialog.close();
    expect(env.navigate).not.toHaveBeenCalled();
    expect(env.areYouSure.hidden).toBe(false);
  });
});

describe("initLibraryRestoreDocument – preview-level Cancel", () => {
  it("invalidates the validated preview and hides the decision prompt", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmNo.fire("click"); // back out of the dialog first, as the UI would require

    env.decisionCancel.fire("click");

    expect(env.decision.hidden).toBe(true);
    expect(env.resultRegion.replaceChildren).toHaveBeenCalled();
  });

  it("clears the selected File so the page returns to its original choose-file state", async () => {
    const env = setup();
    env.fileInput.files = [{ name: "backup.zip" }] as unknown as File[];
    stubFetchAndParser({ validated: true });
    env.validateButton.fire("click");
    await vi.waitFor(() => expect(env.decision.hidden).toBe(false));

    env.decisionCancel.fire("click");

    expect(env.fileInput.value).toBe("");
  });

  it("removes restore eligibility -- Continue no longer opens the dialog for the old selection", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmNo.fire("click");
    env.decisionCancel.fire("click");
    env.dialog.showModal.mockClear();

    env.decisionContinue.fire("click");

    expect(env.dialog.showModal).not.toHaveBeenCalled();
  });

  it("performs no mutation (no fetch)", async () => {
    const env = setup();
    env.fileInput.files = [{ name: "backup.zip" }] as unknown as File[];
    stubFetchAndParser({ validated: true });
    env.validateButton.fire("click");
    await vi.waitFor(() => expect(env.decision.hidden).toBe(false));
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    env.decisionCancel.fire("click");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("closes an open dialog defensively, then returns focus to Validate", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    // Dialog is left open here to prove Cancel defensively closes it too.

    env.decisionCancel.fire("click");

    expect(env.dialog.close).toHaveBeenCalledOnce();
    expect(env.validateButton.focus).toHaveBeenCalledOnce();
  });
});

describe("initLibraryRestoreDocument – submit button availability", () => {
  // The submit button is not kept disabled until the typed phrase matches
  // exactly: that
  // convenience gating would be a real blocking
  // defect -- a disabled `type="submit"` button never dispatches
  // a `submit` event (click or Enter), so a wrong or old phrase could
  // never even reach `fetch()`, and the dialog silently showed nothing.
  // The button must now be available regardless of what's currently
  // typed, so every non-blank attempt reaches the server and its real
  // response is what the user sees.
  it("is enabled as soon as the phrase stage is revealed, regardless of phrase content", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    expect(env.submitButton.disabled).toBe(true); // not yet revealed

    env.confirmYes.fire("click");

    expect(env.submitButton.disabled).toBe(false);
  });

  it("stays enabled while the user types a phrase that does not match", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmYes.fire("click");

    env.phraseInput.value = "not the right phrase";
    env.phraseInput.fire("input");

    expect(env.submitButton.disabled).toBe(false);
  });
});

describe("initLibraryRestoreDocument – phrase-stage Cancel", () => {
  it("returns to the Are-you-sure stage without closing the dialog", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);

    env.phraseCancel.fire("click");

    expect(env.dialog.close).not.toHaveBeenCalled();
    expect(env.areYouSure.hidden).toBe(false);
    expect(env.phraseSection.hidden).toBe(true);
  });

  it("clears the phrase input and disables submit", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    expect(env.submitButton.disabled).toBe(false);

    env.phraseCancel.fire("click");

    expect(env.phraseInput.value).toBe("");
    expect(env.submitButton.disabled).toBe(true);
  });

  it("performs no fetch and no mutation", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    env.phraseCancel.fire("click");

    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("keeps the validated file selection intact", async () => {
    const env = setup();
    const file = await validateAndOpenDialog(env);
    revealPhraseStage(env);

    env.phraseCancel.fire("click");

    // The dialog can still be reopened directly against the same
    // validated file -- Cancel never touched `validated`/`validatedFile`.
    env.decisionContinue.fire("click");
    expect(env.filenameEl.textContent).toBe(file.name);
  });

  it("returns focus to the Are-you-sure Yes control", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);

    env.phraseCancel.fire("click");

    expect(env.confirmYes.focus).toHaveBeenCalledOnce();
  });
});

describe("initLibraryRestoreDocument – final restore submission", () => {
  it("blocks submission when nothing has been validated", () => {
    const { form } = setup();
    const event = { preventDefault: vi.fn() };
    form.fire("submit", event);
    expect(event.preventDefault).toHaveBeenCalledOnce();
  });

  it("blocks submission when the selected file no longer matches the validated file", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);

    // A different File object with the same visible name -- identity must
    // be checked, not just presence.
    env.fileInput.files = [{ name: "backup.zip" }] as unknown as File[];

    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;
    const event = { preventDefault: vi.fn() };
    env.form.fire("submit", event);

    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("submits via fetch (not native navigation) once validated with the same file", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    const fetchMock = stubRestoreFetch({ ok: true, redirect_url: "/" });

    const event = { preventDefault: vi.fn() };
    env.form.fire("submit", event);

    expect(event.preventDefault).toHaveBeenCalledOnce();
    expect(fetchMock).toHaveBeenCalledOnce();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe(env.form.dataset.restoreUrl);
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
  });

  it("sends the same File object in the final FormData", async () => {
    const env = setup();
    const file = await validateAndOpenDialog(env);
    revealPhraseStage(env);
    const fetchMock = stubRestoreFetch({ ok: true, redirect_url: "/" });

    env.form.fire("submit", { preventDefault: vi.fn() });

    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    const body = init.body as unknown as { __testFile: unknown };
    expect(body.__testFile).toBe(file);
  });

  it("captures the confirmation phrase into the FormData even though the UI enters busy state on submit (regression: FormData must be built before phraseInput is disabled)", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    expect(env.phraseInput.value).toBe("REPLACE MY CURRENT LIBRARY");
    const fetchMock = stubRestoreFetch({ ok: true, redirect_url: "/" });

    env.form.fire("submit", { preventDefault: vi.fn() });

    // Busy state (which disables phraseInput) is active by the time the
    // fetch has been issued -- proving the capture had to happen first,
    // not merely that the value happened to survive.
    expect(env.phraseInput.disabled).toBe(true);
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    const body = init.body as unknown as { __testConfirmation: unknown };
    expect(body.__testConfirmation).toBe("REPLACE MY CURRENT LIBRARY");
  });

  it("sends the Accept: application/json and CSRF headers", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    const fetchMock = stubRestoreFetch({ ok: true, redirect_url: "/" });

    env.form.fire("submit", { preventDefault: vi.fn() });

    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    const headers = init.headers as Record<string, string>;
    expect(headers["Accept"]).toBe("application/json");
    expect(headers["X-CSRFToken"]).toBe("csrf-token-value");
  });
});

describe("initLibraryRestoreDocument – double-submit prevention", () => {
  it("disables the submit and cancel controls immediately on submit", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    stubRestoreFetch({ ok: true, redirect_url: "/" });

    env.form.fire("submit", { preventDefault: vi.fn() });

    expect(env.submitButton.disabled).toBe(true);
    expect(env.phraseCancel.disabled).toBe(true);
  });

  it("blocks a second submit while the first fetch is in flight", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);

    let resolveFetch: (value: unknown) => void = () => {};
    const pending = new Promise((resolve) => {
      resolveFetch = resolve;
    });
    const fetchMock = vi.fn(() => pending);
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    env.form.fire("submit", { preventDefault: vi.fn() });
    env.form.fire("submit", { preventDefault: vi.fn() });

    expect(fetchMock).toHaveBeenCalledOnce();
    resolveFetch({
      json: () => Promise.resolve({ ok: true, redirect_url: "/" }),
    });
  });

  it("re-enables controls after an expected error response", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    stubRestoreFetch({ ok: false, error: "Wrong phrase." });

    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(env.submitButton.disabled).toBe(false));

    expect(env.phraseCancel.disabled).toBe(false);
  });

  it("never re-enables the destructive controls after success", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    stubRestoreFetch({ ok: true, redirect_url: "/" });

    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(env.dialogBody.hidden).toBe(true));

    expect(env.submitButton.disabled).toBe(true);
  });
});

describe("initLibraryRestoreDocument – expected-error response", () => {
  it("keeps the dialog open and shows the safe error message", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    stubRestoreFetch({
      ok: false,
      error: "Type REPLACE MY CURRENT LIBRARY exactly to confirm.",
    });

    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(env.dialogError.hidden).toBe(false));

    expect(env.dialog.close).not.toHaveBeenCalled();
    expect(env.dialogError.textContent).toBe(
      "Type REPLACE MY CURRENT LIBRARY exactly to confirm.",
    );
    expect(env.dialogBody.hidden).toBe(false);
    expect(env.successRegion.hidden).toBe(true);
  });

  it("does not duplicate error markup on a repeated failed attempt", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    stubRestoreFetch({ ok: false, error: "First error." });
    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() =>
      expect(env.dialogError.textContent).toBe("First error."),
    );

    stubRestoreFetch({ ok: false, error: "Second error." });
    env.phraseInput.value = "REPLACE MY CURRENT LIBRARY";
    env.phraseInput.fire("input");
    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() =>
      expect(env.dialogError.textContent).toBe("Second error."),
    );
  });

  it("a network failure also shows a safe error and re-enables controls", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    globalThis.fetch = vi.fn(() =>
      Promise.reject(new Error("network down")),
    ) as unknown as typeof fetch;

    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(env.dialogError.hidden).toBe(false));
    expect(env.submitButton.disabled).toBe(false);
  });

  it("blank phrase: shows a client-side error without ever calling fetch", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmYes.fire("click");
    // Left blank -- never typed into.
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    env.form.fire("submit", { preventDefault: vi.fn() });

    expect(fetchMock).not.toHaveBeenCalled();
    expect(env.dialogError.hidden).toBe(false);
    expect(env.dialogError.textContent).toBe(
      "Type REPLACE MY CURRENT LIBRARY exactly to confirm.",
    );
    expect(env.dialog.close).not.toHaveBeenCalled();
    expectPhraseEmphasized(env.dialogError);
  });

  it("blank phrase (whitespace only): also short-circuits client-side", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmYes.fire("click");
    env.phraseInput.value = "   ";
    env.phraseInput.fire("input");
    const fetchMock = vi.fn();
    globalThis.fetch = fetchMock as unknown as typeof fetch;

    env.form.fire("submit", { preventDefault: vi.fn() });

    expect(fetchMock).not.toHaveBeenCalled();
    expect(env.dialogError.hidden).toBe(false);
  });

  it("wrong (non-blank) phrase: reaches the server and shows its exact safe error", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmYes.fire("click");
    env.phraseInput.value = "definitely not it";
    env.phraseInput.fire("input");
    const fetchMock = stubRestoreFetch({
      ok: false,
      error: "Type REPLACE MY CURRENT LIBRARY exactly to confirm.",
    });

    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(env.dialogError.hidden).toBe(false));

    expect(env.dialogError.textContent).toBe(
      "Type REPLACE MY CURRENT LIBRARY exactly to confirm.",
    );
    expectPhraseEmphasized(env.dialogError);
  });

  it("old (pre-correction) phrase: reaches the server and shows its exact safe error", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmYes.fire("click");
    env.phraseInput.value = "REPLACE MY LIBRARY";
    env.phraseInput.fire("input");
    const fetchMock = stubRestoreFetch({
      ok: false,
      error: "Type REPLACE MY CURRENT LIBRARY exactly to confirm.",
    });

    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    await vi.waitFor(() => expect(env.dialogError.hidden).toBe(false));

    expect(env.dialogError.textContent).toBe(
      "Type REPLACE MY CURRENT LIBRARY exactly to confirm.",
    );
    expectPhraseEmphasized(env.dialogError);
  });

  it("retry after an error succeeds: correcting the phrase and resubmitting reaches the server again and clears the error", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    env.confirmYes.fire("click");
    env.phraseInput.value = "wrong";
    env.phraseInput.fire("input");
    stubRestoreFetch({ ok: false, error: "Wrong phrase." });
    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(env.dialogError.hidden).toBe(false));
    expect(env.submitButton.disabled).toBe(false);

    env.phraseInput.value = "REPLACE MY CURRENT LIBRARY";
    env.phraseInput.fire("input");
    // Clears as soon as the user starts correcting, before even resubmitting.
    expect(env.dialogError.hidden).toBe(true);

    const fetchMock = stubRestoreFetch({ ok: true, redirect_url: "/" });
    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(env.dialogBody.hidden).toBe(true));

    expect(fetchMock).toHaveBeenCalledOnce();
    expect(env.successRegion.hidden).toBe(false);
  });

  it("a successful exact-phrase restore does not display any stale error", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    stubRestoreFetch({ ok: true, redirect_url: "/" });

    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(env.dialogBody.hidden).toBe(true));

    // dialogError lives inside dialogBody, which is now hidden in its
    // entirety -- no error text is left displayed anywhere in the
    // success state.
    expect(env.dialogError.hidden).toBe(true);
    expect(env.dialogError.textContent).toBe("");
  });
});

describe("initLibraryRestoreDocument – success state", () => {
  async function submitAndSucceed(
    env: ReturnType<typeof setup>,
    redirectUrl = "/",
  ) {
    stubRestoreFetch({ ok: true, redirect_url: redirectUrl });
    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(env.dialogBody.hidden).toBe(true));
  }

  it("hides all destructive controls and shows the success region", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    await submitAndSucceed(env);

    expect(env.dialogBody.hidden).toBe(true);
    expect(env.successRegion.hidden).toBe(false);
  });

  it("does not navigate immediately on success", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    await submitAndSucceed(env);

    expect(env.navigate).not.toHaveBeenCalled();
  });

  it("Close navigates to the response's redirect_url", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    await submitAndSucceed(env, "/home-for-this-user/");

    env.closeButton.fire("click");

    expect(env.navigate).toHaveBeenCalledWith("/home-for-this-user/");
  });

  it("Escape (dialog close()) after success also navigates instead of resetting", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    await submitAndSucceed(env, "/home-for-this-user/");

    env.dialog.close();

    expect(env.navigate).toHaveBeenCalledWith("/home-for-this-user/");
  });

  it("a backdrop click after success also navigates", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    await submitAndSucceed(env, "/home-for-this-user/");

    env.dialog.fire("click", { target: env.dialog });

    expect(env.navigate).toHaveBeenCalledWith("/home-for-this-user/");
  });

  it("falls back to the form's home URL if redirect_url is missing", async () => {
    const env = setup({ dataset: { homeUrl: "/fallback-home/" } });
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    stubRestoreFetch({ ok: true });
    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(env.dialogBody.hidden).toBe(true));

    env.closeButton.fire("click");

    expect(env.navigate).toHaveBeenCalledWith("/fallback-home/");
  });

  it("no second restore fetch can be issued once success state is entered", async () => {
    const env = setup();
    await validateAndOpenDialog(env);
    revealPhraseStage(env);
    const fetchMock = stubRestoreFetch({ ok: true, redirect_url: "/" });
    env.form.fire("submit", { preventDefault: vi.fn() });
    await vi.waitFor(() => expect(env.dialogBody.hidden).toBe(true));

    // The submit button remains disabled/hidden-within-dialogBody, but even
    // a direct re-fire of the form's submit event must not re-enter the
    // fetch path a second time while already submitting/succeeded.
    env.form.fire("submit", { preventDefault: vi.fn() });

    expect(fetchMock).toHaveBeenCalledOnce();
  });
});
