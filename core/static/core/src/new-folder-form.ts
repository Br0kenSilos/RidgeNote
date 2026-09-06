const NEW_FOLDER_FORM_SELECTOR = "[data-new-folder-form]";
const FIELD_ERROR_SELECTOR = ".tree-nav__popover-field-error";

interface NewFolderCreateResponse {
  ok: boolean;
  redirect_url?: string;
  error?: string;
  name?: string;
}

interface NewFolderFetchLike {
  fetch: typeof fetch;
}

interface NewFolderLocationLike {
  location: { assign(url: string): void };
}

function getFormElements(form: HTMLFormElement) {
  const input = form.querySelector<HTMLInputElement>('input[name="name"]');
  const errorEl = form.querySelector<HTMLElement>(FIELD_ERROR_SELECTOR);
  return { input, errorEl };
}

function buildFormBody(form: HTMLFormElement): FormData {
  // Built by hand from the two fields the folder-create route actually
  // reads, rather than `new FormData(form)`: that constructor overload
  // reflects a real, attached HTMLFormElement's field set, which is more
  // than this narrow submission needs and awkward to fake in tests.
  const body = new FormData();
  const csrfInput = form.querySelector<HTMLInputElement>(
    'input[name="csrfmiddlewaretoken"]',
  );
  const nameInput = form.querySelector<HTMLInputElement>('input[name="name"]');
  if (csrfInput) {
    body.append("csrfmiddlewaretoken", csrfInput.value);
  }
  body.append("name", nameInput?.value ?? "");
  return body;
}

function showFieldError(form: HTMLFormElement, message: string): void {
  const { input, errorEl } = getFormElements(form);
  if (errorEl) {
    errorEl.textContent = message;
    errorEl.hidden = false;
  }
  if (input) {
    input.setAttribute("aria-invalid", "true");
    input.focus();
    input.select();
  }
}

function clearFieldError(form: HTMLFormElement): void {
  const { input, errorEl } = getFormElements(form);
  if (errorEl) {
    errorEl.textContent = "";
    errorEl.hidden = true;
  }
  input?.removeAttribute("aria-invalid");
}

/**
 * Wires the New Folder popover's form to submit asynchronously instead of
 * via a full page navigation. A full-page round trip was the actual root
 * cause of the popover appearing to "jump": the server re-renders both the
 * wide-tree and narrow-drawer copies with the New Folder `<details>`
 * already `open` (since the failure needs to reopen whichever popover the
 * user submitted from), but the CSS-fallback panel position only becomes
 * correctly anchored once `tree-context-menu.ts` floats it in response to a
 * real 'toggle' event -- which never fires for a server-rendered `open`
 * attribute. Submitting via `fetch` and updating this exact form in place
 * on failure sidesteps that entirely: no navigation, no server-rendered
 * `open` state, no second copy ever enters play.
 *
 * Only this one popover's form is intercepted; every other form (New note,
 * Move note, folder rename, etc.) is untouched and keeps posting normally.
 */
export function initNewFolderFormsDocument(
  doc: Document = document,
  windowLike: NewFolderLocationLike &
    NewFolderFetchLike = window as unknown as NewFolderLocationLike &
    NewFolderFetchLike,
): boolean {
  const forms = Array.from(
    doc.querySelectorAll<HTMLFormElement>(NEW_FOLDER_FORM_SELECTOR),
  );
  if (forms.length === 0) {
    return false;
  }

  for (const form of forms) {
    if (form.dataset.newFolderFormInitialized === "true") {
      continue;
    }

    form.addEventListener("submit", (event) => {
      event.preventDefault();
      clearFieldError(form);

      const formData = buildFormBody(form);
      windowLike
        .fetch(form.action, {
          method: "POST",
          headers: { Accept: "application/json" },
          body: formData,
        })
        .then((response) => response.json() as Promise<NewFolderCreateResponse>)
        .then((data) => {
          if (data.ok && data.redirect_url) {
            windowLike.location.assign(data.redirect_url);
            return;
          }
          showFieldError(form, data.error ?? "Folder name is required.");
        })
        .catch(() => {
          // Network/parse failure: fall back to a real submission so the
          // user isn't stuck with a form that silently does nothing.
          form.submit();
        });
    });

    form.dataset.newFolderFormInitialized = "true";
  }

  return true;
}
