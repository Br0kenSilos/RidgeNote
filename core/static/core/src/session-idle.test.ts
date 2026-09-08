import { describe, expect, it, vi } from "vitest";

import {
  initSessionIdleDocument,
  type SessionIdleFetchResponseLike,
  type SessionIdleNoteEditorLike,
  type SessionIdleSchedulerLike,
  type SessionStatusPayload,
} from "./session-idle";

// -- DOM-wiring tests ---------------------------------------------------
//
// This project's Vitest suite runs without a real DOM (no jsdom
// dependency); tests supply small hand-written fakes cast with
// `as unknown as <RealType>`, matching quick-switch.test.ts's precedent
// (see global-search.test.ts's own comment for the same convention).

class ManualScheduler implements SessionIdleSchedulerLike {
  private nextId = 1;
  private tasks = new Map<number, { callback: () => void; delayMs: number }>();

  clearTimeout(handle: number): void {
    this.tasks.delete(handle);
  }

  setTimeout(callback: () => void, delayMs: number): number {
    const handle = this.nextId;
    this.nextId += 1;
    this.tasks.set(handle, { callback, delayMs });
    return handle;
  }

  hasDelay(delayMs: number): boolean {
    return [...this.tasks.values()].some((task) => task.delayMs === delayMs);
  }

  countDelay(delayMs: number): number {
    return [...this.tasks.values()].filter((task) => task.delayMs === delayMs)
      .length;
  }

  delays(): number[] {
    return [...this.tasks.values()].map((task) => task.delayMs);
  }

  /** Runs every currently-scheduled task with exactly this delay --
   * mirrors note-editor.test.ts's identical helper. */
  runDelay(delayMs: number): void {
    const handles = [...this.tasks.entries()]
      .filter(([, task]) => task.delayMs === delayMs)
      .map(([handle]) => handle);
    for (const handle of handles) {
      const task = this.tasks.get(handle);
      if (!task) {
        continue;
      }
      this.tasks.delete(handle);
      task.callback();
    }
  }
}

class FakeCountdownElement {
  textContent: string | null = null;
}

class FakeStaySignedInButton {
  private listeners: Array<() => void> = [];

  addEventListener(type: string, listener: () => void): void {
    if (type === "click") {
      this.listeners.push(listener);
    }
  }

  click(): void {
    for (const listener of this.listeners) {
      listener();
    }
  }
}

class FakeWarningBanner {
  hidden = true;
  private countdown = new FakeCountdownElement();
  private staySignedIn = new FakeStaySignedInButton();

  querySelector<T>(selector: string): T | null {
    if (selector === "[data-session-idle-warning-countdown]") {
      return this.countdown as unknown as T;
    }
    if (selector === "[data-session-stay-signed-in]") {
      return this.staySignedIn as unknown as T;
    }
    return null;
  }

  get countdownText(): string | null {
    return this.countdown.textContent;
  }

  clickStaySignedIn(): void {
    this.staySignedIn.click();
  }
}

class FakeExpiredDialog {
  open = false;
  showModalCalls = 0;

  showModal(): void {
    this.open = true;
    this.showModalCalls += 1;
  }
}

class FakeConfigElement {
  dataset: {
    sessionCsrfToken?: string;
    sessionExpiredUrl?: string;
    sessionIdleTimeoutSeconds?: string;
    sessionKeepaliveUrl?: string;
    sessionStatusUrl?: string;
    sessionWarningSeconds?: string;
  } = {};
}

class FakeDocument {
  private elements = new Map<string, unknown>();
  private listeners = new Map<string, Array<() => void>>();

  setElement(selector: string, element: unknown): void {
    this.elements.set(selector, element);
  }

  querySelector<T>(selector: string): T | null {
    return (this.elements.get(selector) as T | undefined) ?? null;
  }

  addEventListener(type: string, listener: () => void): void {
    const listeners = this.listeners.get(type) ?? [];
    listeners.push(listener);
    this.listeners.set(type, listeners);
  }

  dispatch(type: string): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener();
    }
  }
}

function buildDocument(
  options: {
    idleTimeoutSeconds?: number;
    omitConfig?: boolean;
    warningSeconds?: number;
  } = {},
): {
  doc: FakeDocument;
  unsavedExpiredDialog: FakeExpiredDialog;
  warningBanner: FakeWarningBanner;
} {
  const doc = new FakeDocument();
  if (!options.omitConfig) {
    const config = new FakeConfigElement();
    config.dataset.sessionStatusUrl = "/session/status/";
    config.dataset.sessionKeepaliveUrl = "/session/keepalive/";
    config.dataset.sessionExpiredUrl = "/session/expired/";
    config.dataset.sessionCsrfToken = "csrf-token-value";
    config.dataset.sessionIdleTimeoutSeconds = String(
      options.idleTimeoutSeconds ?? 120,
    );
    config.dataset.sessionWarningSeconds = String(options.warningSeconds ?? 30);
    doc.setElement("[data-session-idle-config]", config);
  }
  const warningBanner = new FakeWarningBanner();
  doc.setElement("#session-idle-warning", warningBanner);
  const unsavedExpiredDialog = new FakeExpiredDialog();
  doc.setElement("#session-expired-unsaved-dialog", unsavedExpiredDialog);
  return { doc, unsavedExpiredDialog, warningBanner };
}

function statusResponse(
  status: number,
  payload: SessionStatusPayload,
): Promise<SessionIdleFetchResponseLike> {
  return Promise.resolve({ status, json: async () => payload });
}

function authenticatedResponse(
  expiresAtMs: number,
): Promise<SessionIdleFetchResponseLike> {
  return statusResponse(200, {
    authenticated: true,
    expires_at: new Date(expiresAtMs).toISOString(),
  });
}

function expiredResponse(): Promise<SessionIdleFetchResponseLike> {
  return statusResponse(401, { authenticated: false });
}

async function flushAsyncWork(cycles = 4): Promise<void> {
  for (let index = 0; index < cycles; index += 1) {
    await Promise.resolve();
  }
}

describe("initSessionIdleDocument", () => {
  it("does nothing and returns false when the config element is absent", () => {
    const { doc } = buildDocument({ omitConfig: true });
    const scheduler = new ManualScheduler();

    const result = initSessionIdleDocument(doc as unknown as Document, {
      scheduler,
    });

    expect(result).toBe(false);
    expect(scheduler.countDelay(120000)).toBe(0);
  });

  it("schedules the first reconciliation at the full idle timeout", () => {
    const { doc } = buildDocument({
      idleTimeoutSeconds: 120,
      warningSeconds: 30,
    });
    const scheduler = new ManualScheduler();

    initSessionIdleDocument(doc as unknown as Document, { scheduler });

    expect(scheduler.hasDelay(120000)).toBe(true);
  });

  describe("reconciliation with the server-authoritative deadline", () => {
    it("shows the warning with the true remaining seconds when reconciling inside the warning window", async () => {
      const { doc, warningBanner } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const fetchFn = vi.fn(() => authenticatedResponse(Date.now() + 20000));

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
      });
      scheduler.runDelay(120000); // nominal warning point
      await flushAsyncWork();

      expect(warningBanner.hidden).toBe(false);
      expect(warningBanner.countdownText).toBe("20");
    });

    it("does not show the warning and reschedules the next check when reconciling outside the warning window", async () => {
      const { doc, warningBanner } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      // Real activity extended the session well past the warning window.
      const fetchFn = vi.fn(() => authenticatedResponse(Date.now() + 90000));

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
      });
      scheduler.runDelay(120000); // nominal warning point
      await flushAsyncWork();

      expect(warningBanner.hidden).toBe(true);
      // Rescheduled for (90000 - 30000) = 60000ms, not a bare 90000/120000 cycle.
      const expiryDelay = Math.max(...scheduler.delays());
      expect(expiryDelay).toBeGreaterThan(59000);
      expect(expiryDelay).toBeLessThanOrEqual(60000);
    });

    it("re-checks again at the newly-computed deadline rather than only once", async () => {
      const { doc, warningBanner } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const fetchFn = vi
        .fn()
        .mockImplementationOnce(() => authenticatedResponse(Date.now() + 90000))
        .mockImplementationOnce(() =>
          authenticatedResponse(Date.now() + 20000),
        );

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
      });
      scheduler.runDelay(120000); // first reconcile -> outside window
      await flushAsyncWork();
      expect(warningBanner.hidden).toBe(true);

      const nextDelay = Math.max(...scheduler.delays());
      scheduler.runDelay(nextDelay); // second reconcile -> inside window
      await flushAsyncWork();

      expect(fetchFn).toHaveBeenCalledTimes(2);
      expect(warningBanner.hidden).toBe(false);
      expect(warningBanner.countdownText).toBe("20");
    });

    it("hides an already-visible warning within one reconcile tick once ordinary activity extends the deadline, without logging out at the old deadline", async () => {
      const { doc, warningBanner } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const navigate = vi.fn();
      const fetchFn = vi
        .fn()
        // Nominal warning point: 20s genuinely remain -- warning shows.
        .mockImplementationOnce(() => authenticatedResponse(Date.now() + 20000))
        // The very next reconcile tick (~1s later): real activity (e.g.
        // ordinary autosave from typing) has since extended the
        // session well past the old ~20s deadline the warning was
        // still counting down against.
        .mockImplementationOnce(() =>
          authenticatedResponse(Date.now() + 90000),
        );

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
        navigate,
        noteEditor: null,
      });

      // 1. Warning is visible.
      scheduler.runDelay(120000);
      await flushAsyncWork();
      expect(warningBanner.hidden).toBe(false);
      expect(warningBanner.countdownText).toBe("20");

      // 2 & 3. Server activity extended expires_at; the next reconcile
      // (scheduled only COUNTDOWN_TICK_MS out, not the stale ~20s) picks
      // it up.
      const nextCheckDelay = Math.max(...scheduler.delays());
      expect(nextCheckDelay).toBeLessThanOrEqual(1000);
      scheduler.runDelay(nextCheckDelay);
      await flushAsyncWork();

      // 4. Warning is hidden immediately (this same tick), not left
      // stale until the old ~20s deadline.
      expect(warningBanner.hidden).toBe(true);
      expect(fetchFn).toHaveBeenCalledTimes(2);

      // 5. Next check is scheduled from the new authoritative deadline
      // (90000 - 30000 warning window = 60000), not the old one.
      const rescheduledDelay = Math.max(...scheduler.delays());
      expect(rescheduledDelay).toBeGreaterThan(59000);
      expect(rescheduledDelay).toBeLessThanOrEqual(60000);

      // 6. No logout/navigation occurred at any point, including at
      // what would have been the old, now-superseded deadline.
      expect(navigate).not.toHaveBeenCalled();
    });
  });

  describe("confirmed expiry -- normal case (clean editor)", () => {
    it("navigates to the dedicated expired page when there is no note editor", async () => {
      const { doc } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const navigate = vi.fn();
      const fetchFn = vi.fn(() => expiredResponse());

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
        navigate,
        noteEditor: null,
      });
      scheduler.runDelay(120000);
      await flushAsyncWork();

      expect(navigate).toHaveBeenCalledTimes(1);
      expect(navigate).toHaveBeenCalledWith("/session/expired/");
    });

    it("navigates to the dedicated expired page when the note editor is clean", async () => {
      const { doc } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const navigate = vi.fn();
      const fetchFn = vi.fn(() => expiredResponse());
      const noteEditor: SessionIdleNoteEditorLike = {
        getState: () => "clean",
        flushPendingSave: vi.fn(() => Promise.resolve()),
      };

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
        navigate,
        noteEditor,
      });
      scheduler.runDelay(120000);
      await flushAsyncWork();

      expect(navigate).toHaveBeenCalledWith("/session/expired/");
    });

    it("treats a session_status network failure as confirmed expiry", async () => {
      const { doc } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const navigate = vi.fn();
      const fetchFn = vi.fn(() => Promise.reject(new Error("network error")));

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
        navigate,
        noteEditor: null,
      });
      scheduler.runDelay(120000);
      await flushAsyncWork();

      expect(navigate).toHaveBeenCalledWith("/session/expired/");
    });

    it("awaits the warning-time flush before deciding it is safe to navigate", async () => {
      const { doc } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const navigate = vi.fn();
      let resolveFlush!: () => void;
      const flushPendingSave = vi.fn(
        () =>
          new Promise<void>((resolve) => {
            resolveFlush = resolve;
          }),
      );
      let stateNow: "clean" | "dirty" = "dirty";
      const noteEditor: SessionIdleNoteEditorLike = {
        getState: () => stateNow,
        flushPendingSave,
      };
      const fetchFn = vi
        .fn()
        .mockImplementationOnce(() => authenticatedResponse(Date.now() + 20000))
        .mockImplementationOnce(() => expiredResponse());

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
        navigate,
        noteEditor,
      });
      scheduler.runDelay(120000); // enters warning window -> starts flush
      await flushAsyncWork();
      const nextDelay = Math.max(...scheduler.delays());
      scheduler.runDelay(nextDelay); // confirmed-expiry check, flush still pending
      await flushAsyncWork();

      expect(navigate).not.toHaveBeenCalled();

      stateNow = "clean";
      resolveFlush();
      await flushAsyncWork();

      expect(navigate).toHaveBeenCalledWith("/session/expired/");
    });
  });

  describe("confirmed expiry -- abnormal case (unsaved content)", () => {
    it.each(["dirty", "dirty_during_save", "failed", "conflicted", "saving"])(
      "does not navigate and shows the blocking dialog when the editor state is %s",
      async (state) => {
        const { doc, unsavedExpiredDialog } = buildDocument({
          idleTimeoutSeconds: 120,
          warningSeconds: 30,
        });
        const scheduler = new ManualScheduler();
        const navigate = vi.fn();
        const fetchFn = vi.fn(() => expiredResponse());
        const noteEditor: SessionIdleNoteEditorLike = {
          getState: () => state,
          flushPendingSave: vi.fn(() => Promise.resolve()),
        };

        initSessionIdleDocument(doc as unknown as Document, {
          scheduler,
          fetchFn,
          navigate,
          noteEditor,
        });
        scheduler.runDelay(120000);
        await flushAsyncWork();

        expect(navigate).not.toHaveBeenCalled();
        expect(unsavedExpiredDialog.open).toBe(true);
        expect(unsavedExpiredDialog.showModalCalls).toBe(1);
      },
    );
  });

  describe("no local-only reset from generic DOM events (the fixed defect)", () => {
    it("does not listen for keydown at all", async () => {
      const { doc, warningBanner } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const fetchFn = vi.fn(() => authenticatedResponse(Date.now() + 20000));

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
      });
      scheduler.runDelay(120000);
      await flushAsyncWork();
      expect(warningBanner.hidden).toBe(false);

      // Previously this alone silently granted a full fresh local
      // cycle with no server request -- now there is no listener to
      // dispatch to at all, so the fetch call count below must stay
      // exactly what the reconcile schedule alone produced.
      const callsBefore = fetchFn.mock.calls.length;
      doc.dispatch("keydown");
      await flushAsyncWork();

      expect(warningBanner.hidden).toBe(false);
      expect(fetchFn).toHaveBeenCalledTimes(callsBefore);
    });

    it("does not listen for pointerdown at all (clicking the banner itself does nothing)", async () => {
      const { doc, warningBanner } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const fetchFn = vi.fn(() => authenticatedResponse(Date.now() + 20000));

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
      });
      scheduler.runDelay(120000);
      await flushAsyncWork();
      expect(warningBanner.hidden).toBe(false);

      const callsBefore = fetchFn.mock.calls.length;
      doc.dispatch("pointerdown");
      await flushAsyncWork();

      expect(warningBanner.hidden).toBe(false);
      expect(fetchFn).toHaveBeenCalledTimes(callsBefore);
    });

    it("does not reset anything on mere mouse movement", () => {
      const { doc } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();

      initSessionIdleDocument(doc as unknown as Document, { scheduler });
      const before = scheduler.delays().slice();

      // No "mousemove" listener exists to dispatch to.
      expect(scheduler.delays()).toEqual(before);
    });

    it("does not reset the schedule when background network activity happens with no accompanying user action", async () => {
      const { doc } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const fetchFn = vi.fn((_url: string) =>
        authenticatedResponse(Date.now() + 60000),
      );

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
      });
      const before = scheduler.delays().slice();

      // Simulate an unrelated background fetch elsewhere in the app
      // (e.g. the note editor's own freshness poll) -- this module
      // subscribes to no fetch/network event, so calling the injected
      // fetchFn directly, with no accompanying reconcile trigger, must
      // never reschedule anything.
      await fetchFn("/notes/1/freshness/?version=1");
      await flushAsyncWork();

      expect(scheduler.delays()).toEqual(before);
    });
  });

  describe('explicit "Stay signed in"', () => {
    it("sends a real POST with the CSRF token and dismisses the warning only once the server confirms extension", async () => {
      const { doc, warningBanner } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const fetchFn = vi
        .fn()
        .mockImplementationOnce(() => authenticatedResponse(Date.now() + 5000)) // nominal warning reconcile
        .mockImplementationOnce(() =>
          authenticatedResponse(Date.now() + 120000),
        ); // keepalive response

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
      });
      scheduler.runDelay(120000);
      await flushAsyncWork();
      expect(warningBanner.hidden).toBe(false);

      warningBanner.clickStaySignedIn();
      await flushAsyncWork();

      expect(fetchFn).toHaveBeenLastCalledWith(
        "/session/keepalive/",
        expect.objectContaining({
          method: "POST",
          headers: expect.objectContaining({
            "X-CSRFToken": "csrf-token-value",
          }),
        }),
      );
      expect(warningBanner.hidden).toBe(true);
    });

    it("treats a failed keepalive as confirmed expiry rather than silently dismissing the warning", async () => {
      const { doc, warningBanner } = buildDocument({
        idleTimeoutSeconds: 120,
        warningSeconds: 30,
      });
      const scheduler = new ManualScheduler();
      const navigate = vi.fn();
      const fetchFn = vi
        .fn()
        .mockImplementationOnce(() => authenticatedResponse(Date.now() + 5000))
        .mockImplementationOnce(() => expiredResponse());

      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
        navigate,
        noteEditor: null,
      });
      scheduler.runDelay(120000);
      await flushAsyncWork();

      warningBanner.clickStaySignedIn();
      await flushAsyncWork();

      expect(navigate).toHaveBeenCalledWith("/session/expired/");
    });
  });

  it("flushes a dirty note editor through its normal save path when the warning fires", async () => {
    const { doc } = buildDocument({
      idleTimeoutSeconds: 120,
      warningSeconds: 30,
    });
    const scheduler = new ManualScheduler();
    const flushPendingSave = vi.fn(() => Promise.resolve());
    const noteEditor: SessionIdleNoteEditorLike = {
      getState: () => "dirty",
      flushPendingSave,
    };
    const fetchFn = vi.fn(() => authenticatedResponse(Date.now() + 20000));

    initSessionIdleDocument(doc as unknown as Document, {
      scheduler,
      fetchFn,
      noteEditor,
    });
    scheduler.runDelay(120000);
    await flushAsyncWork();

    expect(flushPendingSave).toHaveBeenCalledTimes(1);
    expect(flushPendingSave).toHaveBeenCalledWith({
      suppressIdleActivity: true,
    });
  });

  it("does not flush a clean note editor when the warning fires", async () => {
    const { doc } = buildDocument({
      idleTimeoutSeconds: 120,
      warningSeconds: 30,
    });
    const scheduler = new ManualScheduler();
    const flushPendingSave = vi.fn(() => Promise.resolve());
    const noteEditor: SessionIdleNoteEditorLike = {
      getState: () => "clean",
      flushPendingSave,
    };
    const fetchFn = vi.fn(() => authenticatedResponse(Date.now() + 20000));

    initSessionIdleDocument(doc as unknown as Document, {
      scheduler,
      fetchFn,
      noteEditor,
    });
    scheduler.runDelay(120000);
    await flushAsyncWork();

    expect(flushPendingSave).not.toHaveBeenCalled();
  });

  it("does nothing when there is no note editor on the page", async () => {
    const { doc, warningBanner } = buildDocument({
      idleTimeoutSeconds: 120,
      warningSeconds: 30,
    });
    const scheduler = new ManualScheduler();
    const fetchFn = vi.fn(() => authenticatedResponse(Date.now() + 20000));

    expect(() =>
      initSessionIdleDocument(doc as unknown as Document, {
        scheduler,
        fetchFn,
        noteEditor: null,
      }),
    ).not.toThrow();
    scheduler.runDelay(120000);
    await flushAsyncWork();

    expect(warningBanner.hidden).toBe(false);
  });
});
