"""optional generic SMTP invitation delivery.

A small, transaction-agnostic transport boundary: this module performs no
ORM writes of its own (see `accounts.services.record_invitation_sent()`
for the one audit write, made by the caller only after a successful send
is reported back here) and is only ever invoked *after* the triggering
invitation mutation's own transaction has already committed -- a mail
-delivery failure here can never roll back a domain mutation. Kept
deliberately narrow: one invitation-mail function, not a general
notification framework. A future password-reset email can reuse
`smtp_configured()`/the sender-identity/base-send mechanics this module
already establishes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

from django.conf import settings
from django.core.mail import EmailMultiAlternatives
from django.urls import reverse
from django.utils.html import escape

from accounts.models import User
from accounts.services import resolve_display_timezone

logger = logging.getLogger(__name__)

MAIL_REASON_SMTP_NOT_CONFIGURED = "smtp_not_configured"
MAIL_REASON_RECIPIENT_EMAIL_UNAVAILABLE = "recipient_email_unavailable"
MAIL_REASON_EXTERNAL_URL_UNAVAILABLE = "external_url_unavailable"
MAIL_REASON_SEND_FAILED = "send_failed"

# Admin-facing text for each reason, deliberately generic -- never a raw
# provider/exception string.
MAIL_FAILURE_MESSAGE = (
    "The invitation was created, but RidgeNote could not send the email. "
    "Copy the invitation link below and deliver it manually."
)


@dataclass(frozen=True)
class InvitationSendResult:
    sent: bool
    reason: str | None = None


def smtp_configured() -> bool:
    return bool(settings.RIDGENOTE_SMTP_CONFIGURED)


def external_invitation_url(raw_token: str) -> str:
    """The link used only for an *automatically emailed* invitation --
 never for the manual one-time Copy Link, which continues
    to use `request.build_absolute_uri()` and is untouched by this
    function. Callers must confirm `settings.RIDGENOTE_EXTERNAL_URL` is
    non-blank before calling this (see `send_invitation_email()`)."""
    path = reverse("accounts:invite_entry", kwargs={"raw_token": raw_token})
    return f"{settings.RIDGENOTE_EXTERNAL_URL}{path}"


def build_invitation_email(
    *, user: User, raw_token: str, expires_at: datetime
) -> EmailMultiAlternatives:
    invitation_url = external_invitation_url(raw_token)
    # Application timezone only -- resolve_display_timezone(None) is called
    # with an explicit None, never the invited user, because this initial
    # invitation email must never depend on a pre-setup user's stored (or
    # unset) timezone preference. A future post-setup, user-specific email
    # can pass the user instead.
    localized_expiry = expires_at.astimezone(resolve_display_timezone(None))
    expires_display = localized_expiry.strftime("%B %-d, %Y at %-I:%M %p %Z")
    subject = "You're invited to RidgeNote"
    text_body = (
        f"Hello {user.display_label},\n\n"
        "An administrator has invited you to RidgeNote. Use the link below "
        "to choose your own password and complete your account setup:\n\n"
        f"{invitation_url}\n\n"
        f"This invitation link expires on {expires_display}.\n\n"
        "If you were not expecting this invitation, you can ignore this "
        "email.\n"
    )
    html_body = (
        "<p>Hello {greeting},</p>"
        "<p>An administrator has invited you to RidgeNote. Use the link "
        "below to choose your own password and complete your account "
        "setup:</p>"
        '<p><a href="{url}">{url}</a></p>'
        "<p>This invitation link expires on {expires}.</p>"
        "<p>If you were not expecting this invitation, you can ignore "
        "this email.</p>"
    ).format(
        greeting=escape(user.display_label),
        url=escape(invitation_url),
        expires=escape(str(expires_display)),
    )
    message = EmailMultiAlternatives(subject=subject, body=text_body, to=[user.email])
    message.attach_alternative(html_body, "text/html")
    return message


def send_invitation_email(
    *, user: User, raw_token: str, expires_at: datetime
) -> InvitationSendResult:
    """The only function in this module that touches the network.
    Never raises -- every transport failure is caught here and converted
    to a categorized `InvitationSendResult`, so a mail-delivery problem
    can never propagate into a view/template as a raw exception. Logs a
    sanitized operational line on failure only: the exception's class
    name and the safe category, never `str(exc)` (which could echo
    recipient/server details), never the password, raw token, or full
    invitation URL."""
    if not smtp_configured():
        return InvitationSendResult(sent=False, reason=MAIL_REASON_SMTP_NOT_CONFIGURED)
    if not user.email:
        return InvitationSendResult(sent=False, reason=MAIL_REASON_RECIPIENT_EMAIL_UNAVAILABLE)
    if not settings.RIDGENOTE_EXTERNAL_URL:
        return InvitationSendResult(sent=False, reason=MAIL_REASON_EXTERNAL_URL_UNAVAILABLE)

    message = build_invitation_email(user=user, raw_token=raw_token, expires_at=expires_at)
    try:
        message.send(fail_silently=False)
    except Exception as exc:  # noqa: BLE001 -- deliberately broad: any transport
        # failure (smtplib.*, OSError/socket errors, ssl.SSLError, and
        # Django's own SMTPException wrappers) must be caught and
        # categorized here, never allowed to propagate. Logs only the
        # exception's class name -- never `str(exc)`, which some SMTP
        # libraries populate with server-supplied text that could echo
        # connection/credential details.
        logger.warning(
            "Invitation email send failed for user id=%s: %s",
            user.pk,
            type(exc).__name__,
        )
        return InvitationSendResult(sent=False, reason=MAIL_REASON_SEND_FAILED)
    return InvitationSendResult(sent=True)
