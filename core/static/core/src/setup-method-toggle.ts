const FORM_SELECTOR = "form[data-setup-method-form]";
const RADIO_SELECTOR = 'input[name="setup_method"]';
const PASSWORD_ROW_SELECTOR = "[data-password-field-row]";
// The inverse group: rows relevant only to
// Invitation mode (currently just "Send invitation by email"), hidden
// exactly when the password-row group above is shown, and vice versa.
const INVITATION_ROW_SELECTOR = "[data-invitation-field-row]";
// The form's internal `setup_method` value is form-state only, never
// persisted, never user-visible -- only the human-facing "Set password"
// label is different.
const SET_PASSWORD_VALUE = "temporary_password";
const HIDDEN_CLASS = "form-field--hidden";

/**
 * Create User's
 * password fields (and the
 * Require password change checkbox that only applies to Set password)
 * would otherwise stay visually dominant even while Invitation is selected.
 * Progressive enhancement only: server-side `UserCreateForm.clean()`
 * remains the sole authority on which fields are required, and every
 * row stays in the DOM (never `disabled`/removed) so a no-JS submission
 * behaves exactly as before. Clearing password values (not the checkbox
 * -- clearing its `value` attribute wouldn't affect `checked` and isn't
 * needed, since Invitation mode simply never reads the field, erroring
 * on neither state) when a row is hidden avoids a stale, invisible
 * password value tripping the server's "Invitation mode must not
 * include a password" validation error after switching away from Set
 * password.
 */
function applyVisibility(form: HTMLFormElement): void {
  const checked = form.querySelector<HTMLInputElement>(
    `${RADIO_SELECTOR}:checked`,
  );
  const isSetPassword = checked?.value === SET_PASSWORD_VALUE;
  form.querySelectorAll<HTMLElement>(PASSWORD_ROW_SELECTOR).forEach((row) => {
    row.classList.toggle(HIDDEN_CLASS, !isSetPassword);
    if (!isSetPassword) {
      const input = row.querySelector<HTMLInputElement>(
        'input[type="password"]',
      );
      if (input) {
        input.value = "";
      }
    }
  });
  form.querySelectorAll<HTMLElement>(INVITATION_ROW_SELECTOR).forEach((row) => {
    row.classList.toggle(HIDDEN_CLASS, isSetPassword);
  });
}

export function initSetupMethodToggleDocument(doc: Document = document): void {
  const forms = doc.querySelectorAll<HTMLFormElement>(FORM_SELECTOR);
  forms.forEach((form) => {
    applyVisibility(form);
    form.addEventListener("change", (event) => {
      if (
        event.target instanceof HTMLInputElement &&
        event.target.matches(RADIO_SELECTOR)
      ) {
        applyVisibility(form);
      }
    });
  });
}
