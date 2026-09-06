// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";

import { initSetupMethodToggleDocument } from "./setup-method-toggle";

function buildDom(checkedValue: "invitation" | "temporary_password"): void {
  document.body.innerHTML = `
    <form data-setup-method-form>
      <div class="form-field form-field--radio-inline">
        <label>
          <input type="radio" name="setup_method" value="invitation"
            ${checkedValue === "invitation" ? "checked" : ""}>
          Invitation
        </label>
        <label>
          <input type="radio" name="setup_method" value="temporary_password"
            ${checkedValue === "temporary_password" ? "checked" : ""}>
          Set password
        </label>
      </div>
      <div class="form-field" data-password-field-row>
        <label for="id_password1">Password</label>
        <input type="password" id="id_password1" name="password1">
      </div>
      <div class="form-field" data-password-field-row>
        <label for="id_password2">Confirm password</label>
        <input type="password" id="id_password2" name="password2">
      </div>
      <div class="form-field form-field--checkbox" data-password-field-row>
        <div class="checkbox-control">
          <input type="checkbox" id="id_require_password_change" name="require_password_change" checked>
          <label for="id_require_password_change">Require password change on first sign-in</label>
        </div>
      </div>
      <div class="form-field form-field--checkbox" data-invitation-field-row>
        <div class="checkbox-control">
          <input type="checkbox" id="id_send_invitation" name="send_invitation" checked>
          <label for="id_send_invitation">Send invitation by email</label>
        </div>
      </div>
    </form>
  `;
}

describe("initSetupMethodToggleDocument", () => {
  afterEach(() => {
    document.body.innerHTML = "";
  });

  it("hides the password rows on load when Invitation is selected", () => {
    buildDom("invitation");

    initSetupMethodToggleDocument(document);

    const rows = document.querySelectorAll("[data-password-field-row]");
    rows.forEach((row) => {
      expect(row.classList.contains("form-field--hidden")).toBe(true);
    });
  });

  it("shows the password rows on load when Temporary password is selected", () => {
    buildDom("temporary_password");

    initSetupMethodToggleDocument(document);

    const rows = document.querySelectorAll("[data-password-field-row]");
    rows.forEach((row) => {
      expect(row.classList.contains("form-field--hidden")).toBe(false);
    });
  });

  it("toggles visibility when the radio selection changes", () => {
    buildDom("invitation");
    initSetupMethodToggleDocument(document);

    const temporaryRadio = document.querySelector<HTMLInputElement>(
      'input[value="temporary_password"]',
    )!;
    temporaryRadio.checked = true;
    temporaryRadio.dispatchEvent(new Event("change", { bubbles: true }));

    document.querySelectorAll("[data-password-field-row]").forEach((row) => {
      expect(row.classList.contains("form-field--hidden")).toBe(false);
    });

    const invitationRadio = document.querySelector<HTMLInputElement>(
      'input[value="invitation"]',
    )!;
    invitationRadio.checked = true;
    invitationRadio.dispatchEvent(new Event("change", { bubbles: true }));

    document.querySelectorAll("[data-password-field-row]").forEach((row) => {
      expect(row.classList.contains("form-field--hidden")).toBe(true);
    });
  });

  it("clears password field values when hiding after switching away from Set password", () => {
    buildDom("temporary_password");
    initSetupMethodToggleDocument(document);

    const password1 = document.getElementById(
      "id_password1",
    ) as HTMLInputElement;
    const password2 = document.getElementById(
      "id_password2",
    ) as HTMLInputElement;
    password1.value = "hunter2";
    password2.value = "hunter2";

    const invitationRadio = document.querySelector<HTMLInputElement>(
      'input[value="invitation"]',
    )!;
    invitationRadio.checked = true;
    invitationRadio.dispatchEvent(new Event("change", { bubbles: true }));

    expect(password1.value).toBe("");
    expect(password2.value).toBe("");
  });

  it("hides and shows the Require password change row alongside the password rows", () => {
    buildDom("invitation");
    initSetupMethodToggleDocument(document);

    const checkboxRow = document
      .getElementById("id_require_password_change")!
      .closest("[data-password-field-row]")!;
    expect(checkboxRow.classList.contains("form-field--hidden")).toBe(true);

    const setPasswordRadio = document.querySelector<HTMLInputElement>(
      'input[value="temporary_password"]',
    )!;
    setPasswordRadio.checked = true;
    setPasswordRadio.dispatchEvent(new Event("change", { bubbles: true }));

    expect(checkboxRow.classList.contains("form-field--hidden")).toBe(false);
  });

  it("does not reset the Require password change checkbox state when hiding it", () => {
    buildDom("temporary_password");
    initSetupMethodToggleDocument(document);

    const checkbox = document.getElementById(
      "id_require_password_change",
    ) as HTMLInputElement;
    expect(checkbox.checked).toBe(true);

    const invitationRadio = document.querySelector<HTMLInputElement>(
      'input[value="invitation"]',
    )!;
    invitationRadio.checked = true;
    invitationRadio.dispatchEvent(new Event("change", { bubbles: true }));

    expect(checkbox.checked).toBe(true);
  });

  it("does nothing when no matching form is present", () => {
    document.body.innerHTML = "<div>no form here</div>";

    expect(() => initSetupMethodToggleDocument(document)).not.toThrow();
  });

  it("shows the Send invitation by email row on load when Invitation is selected", () => {
    buildDom("invitation");

    initSetupMethodToggleDocument(document);

    const row = document
      .getElementById("id_send_invitation")!
      .closest("[data-invitation-field-row]")!;
    expect(row.classList.contains("form-field--hidden")).toBe(false);
  });

  it("hides the Send invitation by email row on load when Set password is selected", () => {
    buildDom("temporary_password");

    initSetupMethodToggleDocument(document);

    const row = document
      .getElementById("id_send_invitation")!
      .closest("[data-invitation-field-row]")!;
    expect(row.classList.contains("form-field--hidden")).toBe(true);
  });

  it("toggles the Send invitation by email row inversely to the password rows", () => {
    buildDom("invitation");
    initSetupMethodToggleDocument(document);

    const sendRow = document
      .getElementById("id_send_invitation")!
      .closest("[data-invitation-field-row]")!;
    const passwordRow = document.querySelector("[data-password-field-row]")!;

    const setPasswordRadio = document.querySelector<HTMLInputElement>(
      'input[value="temporary_password"]',
    )!;
    setPasswordRadio.checked = true;
    setPasswordRadio.dispatchEvent(new Event("change", { bubbles: true }));

    expect(sendRow.classList.contains("form-field--hidden")).toBe(true);
    expect(passwordRow.classList.contains("form-field--hidden")).toBe(false);

    const invitationRadio = document.querySelector<HTMLInputElement>(
      'input[value="invitation"]',
    )!;
    invitationRadio.checked = true;
    invitationRadio.dispatchEvent(new Event("change", { bubbles: true }));

    expect(sendRow.classList.contains("form-field--hidden")).toBe(false);
    expect(passwordRow.classList.contains("form-field--hidden")).toBe(true);
  });
});
