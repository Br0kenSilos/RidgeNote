// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { initInvitationCopyDocument } from "./invitation-copy";

function buildDom(url: string): {
  source: HTMLInputElement;
  trigger: HTMLButtonElement;
} {
  document.body.innerHTML = `
    <input type="text" id="id_new_invitation_url" value="${url}" readonly data-copy-invitation-source>
    <button type="button" data-copy-invitation-trigger data-copy-target="id_new_invitation_url">Copy</button>
  `;
  return {
    source: document.getElementById(
      "id_new_invitation_url",
    ) as HTMLInputElement,
    trigger: document.querySelector(
      "[data-copy-invitation-trigger]",
    ) as HTMLButtonElement,
  };
}

async function flushMicrotasks(): Promise<void> {
  await Promise.resolve();
  await Promise.resolve();
  await Promise.resolve();
}

describe("initInvitationCopyDocument", () => {
  afterEach(() => {
    document.body.innerHTML = "";
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("copies the readonly source field's value via navigator.clipboard", async () => {
    const { trigger } = buildDom("http://example.test/invite/abc123/");
    const writeText = vi.fn(() => Promise.resolve());
    Object.assign(navigator, { clipboard: { writeText } });

    initInvitationCopyDocument(document);
    trigger.click();
    await flushMicrotasks();

    expect(writeText).toHaveBeenCalledWith(
      "http://example.test/invite/abc123/",
    );
  });

  it("shows a temporary Copied label after a successful copy, then reverts", async () => {
    const { trigger } = buildDom("http://example.test/invite/abc123/");
    const writeText = vi.fn(() => Promise.resolve());
    Object.assign(navigator, { clipboard: { writeText } });

    initInvitationCopyDocument(document);
    trigger.click();
    await flushMicrotasks();

    expect(trigger.textContent).toBe("Copied");

    await new Promise((resolve) => setTimeout(resolve, 1600));
    expect(trigger.textContent).toBe("Copy");
  }, 5000);

  it("leaves the trigger label unchanged when the copy fails", async () => {
    const { trigger } = buildDom("http://example.test/invite/abc123/");
    Object.assign(navigator, {
      clipboard: {
        writeText: vi.fn(() => Promise.reject(new Error("nope"))),
      },
    });
    // No document.execCommand fallback available in this jsdom setup, so
    // the fallback path also fails -- copyTextWithFallback resolves false.

    initInvitationCopyDocument(document);
    trigger.click();
    await flushMicrotasks();

    expect(trigger.textContent).toBe("Copy");
  });

  it("does nothing when data-copy-target references a missing element", () => {
    document.body.innerHTML = `
      <button type="button" data-copy-invitation-trigger data-copy-target="does_not_exist">Copy</button>
    `;
    const trigger = document.querySelector(
      "[data-copy-invitation-trigger]",
    ) as HTMLButtonElement;

    expect(() => initInvitationCopyDocument(document)).not.toThrow();
    expect(() => trigger.click()).not.toThrow();
  });
});
