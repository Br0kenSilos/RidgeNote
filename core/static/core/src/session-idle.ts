/**
 * Session idle warning/expiry. Reads its configuration from
 * `[data-session-idle-config]` (rendered by `core/templates/base.html`
 * only for an authenticated request, from the same
 * `RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS`/`RIDGENOTE_SESSION_WARNING_SECONDS`
 * settings the backend itself enforces -- see
 * `accounts.context_processors.session_policy` -- so there is exactly
 * one source of truth for these numbers, never a second, independently
 * -maintained frontend constant).
 *
 * PRODUCT INVARIANT: only server-recognized activity may extend the
 * authenticated idle-session deadline. This module deliberately does
 * NOT listen for `keydown`/`pointerdown`/any other generic DOM event
 * to grant itself a fresh local deadline -- an earlier version did,
 * and that let the client-perceived deadline drift arbitrarily later
 * than the server's real one (e.g. merely clicking the warning banner
 * silently bought a full new `idleTimeoutSeconds` window with no
 * corresponding request ever reaching the backend, while the real
 * session expired on schedule underneath it). Ordinary RidgeNote
 * requests remain the sole mechanism by which real activity extends
 * the session -- typing/autosave, page navigation, saves/forms/actions
 * all already refresh `accounts.services.LAST_ACTIVITY_KEY` via
 * `SessionSecurityMiddleware`, with background polling (the note
 * editor's freshness check, `accounts:session_status` itself) already
 * excluded (see `accounts.services.should_refresh_session_activity`).
 * This module's only job is to *reconcile* its own schedule against
 * that authoritative server state at the right moments -- it never
 * decides on its own that the deadline moved.
 *
 * Reconciliation (`reconcile()`) runs at the nominal warning point,
 * then (recursively rescheduling itself) either at the nominal expiry
 * point or, once inside the warning window, every second -- always via
 * `accounts:session_status` (itself excluded from refreshing activity,
 * so checking it can never extend anything) and its authoritative
 * `expires_at` (see `accounts.services.session_expires_at`):
 * - real remaining time still outside the warning window -- nothing to
 *   show; wait until it would be.
 * - real remaining time inside the warning window -- show the warning
 *   with the true remaining seconds (flushing a dirty note editor once
 *   per streak), and re-verify again a second later. Checking this
 *   often while the warning is visible is what lets an extension from
 *   ordinary activity (typing -> autosave, navigation, a save) hide
 *   the warning within about a second of happening, instead of only
 *   at the stale, pre-computed deadline the warning first appeared
 *   with.
 * - not authenticated, or the deadline already passed -- confirmed
 *   expiry.
 *
 * The warning banner also offers an explicit "Stay signed in" button
 * (`accounts:session_keepalive`) for the one case ordinary activity
 * doesn't cover: a present user with nothing left to type, navigate,
 * or save. Unlike every other check in this module, that click is a
 * deliberate, real, server-recognized request -- the backend refreshes
 * activity for it exactly like any other normal request (no special
 * exclusion applies to it), and only the server's own confirmed new
 * `expires_at` in the response dismisses/reschedules the warning.
 * Clicking anywhere else on the banner does nothing.
 *
 * Confirmed expiry branches on the open note editor's state, awaiting
 * the warning-time flush first:
 * - no editor, or the editor is exactly `"clean"` -- nothing at risk,
 *   navigate straight to the dedicated `accounts:session_expired`
 *   page. A real page navigation tears down this entire page's JS
 *   (timers, freshness poll, `beforeunload` guard) along with it, so
 *   nothing stale survives onto the new page.
 * - any other editor state (`dirty`, `dirty_during_save`, `saving`,
 *   `failed`, `conflicted`) -- genuinely unsaved content is at risk,
 *   so this module does NOT navigate away and does NOT touch
 *   `beforeunload` in any way; it shows a blocking, in-place dialog
 *   instead, leaving the note editor's own unsaved-changes protection
 *   fully armed.
 */

export interface SessionIdleSchedulerLike {
  clearTimeout(handle: number): void;
  setTimeout(callback: () => void, delayMs: number): number;
}

export interface SessionStatusPayload {
  authenticated: boolean;
  expires_at?: string | null;
}

export interface SessionIdleFetchResponseLike {
  json(): Promise<SessionStatusPayload>;
  status: number;
}

export type SessionIdleFetchFn = (
  url: string,
  init?: { headers?: Record<string, string>; method?: string },
) => Promise<SessionIdleFetchResponseLike>;

/** The subset of `NoteSaveController` (note-editor.ts) this module
 * needs: enough to flush a genuinely dirty note through its own normal
 * save path right before the warning is shown, without depending on
 * that module's full implementation. */
export interface SessionIdleNoteEditorLike {
  flushPendingSave(options?: { suppressIdleActivity?: boolean }): Promise<void>;
  getState(): string;
}

export interface SessionIdleInitOptions {
  fetchFn?: SessionIdleFetchFn;
  navigate?: (url: string) => void;
  noteEditor?: SessionIdleNoteEditorLike | null;
  scheduler?: SessionIdleSchedulerLike;
}

const COUNTDOWN_TICK_MS = 1000;

/**
 * Wires the idle warning banner and the abnormal-case (unsaved
 * content) expired dialog for one `document`. Returns `false` (no-op)
 * when `[data-session-idle-config]` is absent -- an unauthenticated
 * page (the config element is only rendered for
 * `request.user.is_authenticated`) or malformed/missing configuration
 * -- exactly mirroring the early-return shape every other
 * `init*Document` function in this codebase uses.
 *
 * `RIDGENOTE_SESSION_IDLE_TIMEOUT_SECONDS=0` ("never expire" -- see
 * `ridgenote.settings`/`accounts.services.session_expires_at`) reaches
 * here as `idleTimeoutSeconds <= 0` below, which already returns
 * `false`: no warning banner, no expiry redirect, nothing scheduled.
 * This is the same early-return malformed/missing configuration would
 * take -- disabled is simply never treated as configured.
 */
export function initSessionIdleDocument(
  doc: Document = document,
  options: SessionIdleInitOptions = {},
): boolean {
  const config = doc.querySelector<HTMLElement>("[data-session-idle-config]");
  if (!config) {
    return false;
  }

  const idleTimeoutSeconds = Number(config.dataset.sessionIdleTimeoutSeconds);
  const warningSeconds = Number(config.dataset.sessionWarningSeconds);
  if (
    !config.dataset.sessionStatusUrl ||
    !config.dataset.sessionKeepaliveUrl ||
    !config.dataset.sessionExpiredUrl ||
    !config.dataset.sessionCsrfToken ||
    !Number.isFinite(idleTimeoutSeconds) ||
    idleTimeoutSeconds <= 0 || // includes the disabled ("never expire") case
    !Number.isFinite(warningSeconds) ||
    warningSeconds < 0 ||
    warningSeconds >= idleTimeoutSeconds
  ) {
    return false;
  }
  // Narrowed into their own `string`-typed consts: `config.dataset.*`
  // is read again below inside closures defined later in this
  // function, where TypeScript can't carry forward the truthiness
  // checks above.
  const statusUrl: string = config.dataset.sessionStatusUrl;
  const keepaliveUrl: string = config.dataset.sessionKeepaliveUrl;
  const expiredPageUrl: string = config.dataset.sessionExpiredUrl;
  const csrfToken: string = config.dataset.sessionCsrfToken;

  const warningBanner = doc.querySelector<HTMLElement>("#session-idle-warning");
  const countdownEl =
    warningBanner?.querySelector<HTMLElement>(
      "[data-session-idle-warning-countdown]",
    ) ?? null;
  const staySignedInButton =
    warningBanner?.querySelector<HTMLElement>(
      "[data-session-stay-signed-in]",
    ) ?? null;
  const unsavedExpiredDialog = doc.querySelector<HTMLDialogElement>(
    "#session-expired-unsaved-dialog",
  );

  const scheduler: SessionIdleSchedulerLike = options.scheduler ?? {
    clearTimeout: window.clearTimeout.bind(window),
    setTimeout: (callback, delayMs) => window.setTimeout(callback, delayMs),
  };
  const fetchFn: SessionIdleFetchFn =
    options.fetchFn ??
    ((url, init) =>
      window.fetch(
        url,
        init,
      ) as unknown as Promise<SessionIdleFetchResponseLike>);
  const navigate: (url: string) => void =
    options.navigate ?? ((url) => window.location.assign(url));
  const noteEditor = options.noteEditor ?? null;

  const idleTimeoutMs = idleTimeoutSeconds * 1000;
  const warningWindowMs = warningSeconds * 1000;

  let reconcileTimer: number | null = null;
  let expired = false;
  let pendingFlushPromise: Promise<void> | null = null;
  // True only while the warning is currently shown for the *current*
  // in-window streak -- lets applyRemaining() trigger the warning-time
  // flush exactly once per streak even though it now re-verifies every
  // second while inside the window (see applyRemaining()'s own
  // comment for why the re-verification frequency changed).
  let warningShown = false;

  function clearTimers(): void {
    if (reconcileTimer !== null) {
      scheduler.clearTimeout(reconcileTimer);
      reconcileTimer = null;
    }
  }

  function hideWarning(): void {
    warningShown = false;
    if (warningBanner) {
      warningBanner.hidden = true;
    }
  }

  function showWarningCountdown(remainingSeconds: number): void {
    if (!warningBanner) {
      return;
    }
    if (countdownEl) {
      countdownEl.textContent = String(Math.max(remainingSeconds, 0));
    }
    warningBanner.hidden = false;
  }

  /**
   * Flushes the open note editor's own pending save through its
   * normal, existing save path (autosave endpoint, version/conflict
   * handling, error handling -- all completely untouched) once the
   * session is confirmed inside the warning window, so genuinely
   * unsaved content is not left stranded for the remaining time before
   * expiry. `suppressIdleActivity: true` is the only special-case
   * involved -- it adds a header the backend explicitly does not treat
   * as activity, so this save cannot itself extend the deadline (the
   * server-recognized activity that matters here is ordinary,
   * unsuppressed autosave from real typing, unaffected by this flag).
   * Safe to call more than once per warning window -- a no-op once the
   * editor is clean.
   */
  async function flushNoteEditorIfDirty(): Promise<void> {
    if (!noteEditor || noteEditor.getState() === "clean") {
      return;
    }
    await noteEditor.flushPendingSave({ suppressIdleActivity: true });
  }

  function showUnsavedExpiredDialog(): void {
    if (unsavedExpiredDialog && !unsavedExpiredDialog.open) {
      unsavedExpiredDialog.showModal();
    }
  }

  /**
   * Reached only once the session is confirmed expired (a
   * non-authenticated `session_status` response, or a server deadline
   * already in the past). Awaits any still-settling warning-time
   * flush first, so the editor state read below reflects its final
   * outcome. No editor, or a `"clean"` editor, means nothing is at
   * risk -- navigate straight to the dedicated expired page (a real
   * navigation, tearing down every timer/listener on this page along
   * with it). Any other state means genuinely unsaved content exists,
   * so this deliberately does NOT navigate and does NOT touch
   * `beforeunload` -- the note editor's own unsaved-changes protection
   * stays exactly as armed as it already was.
   */
  async function handleConfirmedExpiry(): Promise<void> {
    if (expired) {
      return;
    }
    expired = true;
    clearTimers();
    hideWarning();

    if (pendingFlushPromise) {
      try {
        await pendingFlushPromise;
      } catch {
        // flushNoteEditorIfDirty()/flushPendingSave() do not reject in
        // practice (every failure branch resolves into a NoteSaveState
        // of "failed"/"conflicted" instead) -- this is a defensive
        // backstop only, so a truly unexpected rejection still falls
        // through to the state check below rather than getting stuck.
      }
    }

    const state = noteEditor?.getState();
    if (!noteEditor || state === "clean") {
      navigate(expiredPageUrl);
      return;
    }
    showUnsavedExpiredDialog();
  }

  function scheduleReconcile(delayMs: number): void {
    if (expired) {
      return;
    }
    reconcileTimer = scheduler.setTimeout(
      () => {
        void reconcile();
      },
      Math.max(delayMs, 0),
    );
  }

  /**
   * Acts on an authoritative remaining-time figure, however it was
   * obtained (a `reconcile()` check, or a successful "Stay signed in"
   * response) -- the single place that decides what the schedule
   * should be from a real server deadline. Never called with a
   * client-only assumption.
   *
   * While inside the warning window, the next check is scheduled only
   * `COUNTDOWN_TICK_MS` out (in lockstep with the visible countdown)
   * rather than the full remaining time. Ordinary activity (typing ->
   * autosave, navigation, a save) can genuinely extend the deadline at
   * any moment while the warning is already showing -- checking only
   * once, at the stale pre-computed deadline, meant such an extension
   * went undetected for up to the entire warning window, leaving a
   * visibly stale countdown ticking down against a deadline the server
   * had already moved past. Re-verifying every second instead means a
   * mid-window extension is caught, and the warning hidden, within
   * about a second of it happening.
   */
  function applyRemaining(remainingMs: number): void {
    if (expired) {
      return;
    }
    clearTimers();
    if (remainingMs > warningWindowMs) {
      // Outside the warning window -- nothing to show. Reached both
      // for a fresh cycle that hasn't entered the window yet, and for
      // a warning that was already showing but just got resynchronized
      // to an authoritative deadline that moved it back out.
      hideWarning();
      scheduleReconcile(remainingMs - warningWindowMs);
      return;
    }
    if (remainingMs > 0) {
      if (!warningShown) {
        warningShown = true;
        pendingFlushPromise = flushNoteEditorIfDirty();
      }
      showWarningCountdown(Math.ceil(remainingMs / 1000));
      scheduleReconcile(Math.min(remainingMs, COUNTDOWN_TICK_MS));
      return;
    }
    void handleConfirmedExpiry();
  }

  /**
   * The sole authority on what happens next: always asks
   * `accounts:session_status` for the real remaining time and acts on
   * that, never on an assumption carried forward from a previous
   * schedule. Runs both at the nominal warning point and (rescheduled
   * from `applyRemaining`) at the nominal expiry point.
   */
  async function reconcile(): Promise<void> {
    if (expired) {
      return;
    }
    let status = 0;
    let payload: SessionStatusPayload | null = null;
    try {
      const response = await fetchFn(statusUrl, {
        headers: { Accept: "application/json" },
      });
      status = response.status;
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (status === 200 && payload?.authenticated) {
      if (payload.expires_at) {
        const expiresAtMs = Date.parse(payload.expires_at);
        if (Number.isFinite(expiresAtMs)) {
          applyRemaining(expiresAtMs - Date.now());
          return;
        }
        // Malformed expires_at should not happen in practice (the
        // backend always sends a valid ISO-8601 value or `null`
        // alongside `authenticated: true`) -- fail open to one more
        // full check later rather than wedging the module.
        scheduleReconcile(idleTimeoutMs);
        return;
      }
      // No expiry currently tracked for this session -- should not
      // happen in practice, since even the page load that rendered
      // this module's own config already refreshed activity, but
      // there is nothing to resynchronize to here. "authenticated:
      // true" is itself real signal the session is not expired.
      scheduleReconcile(idleTimeoutMs);
      return;
    }

    await handleConfirmedExpiry();
  }

  /**
   * The explicit "Stay signed in" action -- the only click this
   * module treats as activity, and only because it is a real request
   * the backend genuinely recognizes (`accounts:session_keepalive` is
   * not on `should_refresh_session_activity`'s exclusion list, unlike
   * `session_status`). The warning is dismissed/rescheduled only after
   * the server confirms the extension via its own new `expires_at` --
   * never optimistically on the click itself.
   */
  async function onStaySignedInClick(): Promise<void> {
    if (expired) {
      return;
    }
    let status = 0;
    let payload: SessionStatusPayload | null = null;
    try {
      const response = await fetchFn(keepaliveUrl, {
        method: "POST",
        headers: {
          Accept: "application/json",
          "X-CSRFToken": csrfToken,
        },
      });
      status = response.status;
      payload = await response.json();
    } catch {
      payload = null;
    }

    if (status === 200 && payload?.authenticated && payload.expires_at) {
      const expiresAtMs = Date.parse(payload.expires_at);
      if (Number.isFinite(expiresAtMs)) {
        applyRemaining(expiresAtMs - Date.now());
        return;
      }
    }
    await handleConfirmedExpiry();
  }

  staySignedInButton?.addEventListener("click", () => {
    void onStaySignedInClick();
  });

  // The initial schedule assumes the current moment reflects the
  // activity this very page load already refreshed server-side --
  // reconcile() at that nominal point (and every point after) always
  // re-confirms against the server rather than trusting this
  // assumption to hold indefinitely.
  scheduleReconcile(idleTimeoutMs);
  return true;
}
