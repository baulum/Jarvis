"""Anti-ban circuit breaker: recognise a platform roadblock and stop the account until Sir looked at it.

Recognised (visible page text, address, HTTP status of the platform's responses): captcha, security checkpoint,
phone/2FA verification, rate limit, HTTP 429, "unusual activity", account restrictions, and a login page where a
session was expected. Nothing here tries to get past such a page: the adapter raises ``ManualReviewError``, the
service trips the breaker (``UptikStore.trip``), blocks the account's open posts and notifies Sir. Only Sir closes it
again (``resume`` in the app) – there is no timer and no automatic retry.

During the login flow these pages are expected and solved by Sir in the visible window; the detector is not used
there.
"""

from __future__ import annotations

from dataclasses import dataclass

from jarvis.uptik.model import ManualReviewError


@dataclass(frozen=True, slots=True)
class BlockSignal:
    code: str
    label: str
    evidence: str  # the indicator that matched – never page content

    def error(self) -> ManualReviewError:
        return ManualReviewError(f"{self.label} – Konto angehalten, bitte manuell prüfen", code=self.code)


_TEXT: list[tuple[str, str, tuple[str, ...]]] = [
    (
        "captcha",
        "Captcha erkannt",
        (
            "captcha",
            "verify you are human",
            "verify you're human",
            "bestätige, dass du ein mensch bist",
            "drag the slider",
            "drag the puzzle",
            "schieberegler",
            "select all images",
            "i'm not a robot",
            "ich bin kein roboter",
        ),
    ),
    (
        "checkpoint",
        "Sicherheitsprüfung der Plattform",
        (
            "security check",
            "sicherheitsüberprüfung",
            "sicherheitscheck",
            "confirm it's you",
            "verify it's you",
            "bestätige, dass du es bist",
            "bestätige deine identität",
            "confirm your identity",
            "suspicious login",
            "verdächtige anmeldung",
            "we detected an unusual login",
            "help us confirm",
        ),
    ),
    (
        "verification",
        "Telefon- oder Zwei-Faktor-Bestätigung verlangt",
        (
            "two-factor",
            "2-step verification",
            "2-stufige bestätigung",
            "zwei-faktor",
            "zweistufige",
            "enter the code we sent",
            "gib den code ein",
            "security code",
            "sicherheitscode",
            "bestätigungscode",
            "verification code",
            "confirm your phone",
            "add your phone number",
            "telefonnummer bestätigen",
            "handynummer bestätigen",
        ),
    ),
    (
        "rate_limit",
        "Ratenlimit der Plattform",
        (
            "too many requests",
            "too many attempts",
            "rate limit",
            "zu viele anfragen",
            "zu viele versuche",
            "you're going too fast",
            "you are going too fast",
            "slow down",
            "action blocked",
            "aktion blockiert",
            "try again later",
            "versuche es später erneut",
            "bitte versuche es später noch einmal",
            "daily upload limit",
            "upload limit reached",
            "upload-limit",
        ),
    ),
    (
        "unusual_activity",
        "Plattform meldet ungewöhnliche Aktivität",
        (
            "unusual activity",
            "ungewöhnliche aktivität",
            "suspicious activity",
            "verdächtige aktivität",
            "we restrict certain activity",
            "wir schränken bestimmte aktivitäten ein",
            "temporarily blocked",
            "vorübergehend gesperrt",
            "account has been suspended",
            "account suspended",
            "konto wurde gesperrt",
            "dein konto ist gesperrt",
            "community guidelines violation",
            "account is restricted",
            "konto ist eingeschränkt",
            "automated behavior",
            "automatisiertes verhalten",
        ),
    ),
]

_URL: list[tuple[str, str, tuple[str, ...]]] = [
    ("captcha", "Captcha erkannt", ("captcha", "/sorry/")),
    (
        "checkpoint",
        "Sicherheitsprüfung der Plattform",
        ("/challenge", "/checkpoint", "/signin/v2/challenge", "/signin/challenge", "/accounts/suspended", "/disabled"),
    ),
]


def detect(url: str, text: str, statuses: list[int] | None = None) -> BlockSignal | None:
    """The first roadblock found in address, HTTP statuses or visible text (lower-cased comparison)."""
    if statuses and 429 in statuses:
        return BlockSignal("http_429", "Plattform antwortet mit HTTP 429 (zu viele Anfragen)", "HTTP 429")
    low_url = (url or "").lower()
    for code, label, needles in _URL:
        for n in needles:
            if n in low_url:
                return BlockSignal(code, label, n)
    low = (text or "").lower()
    for code, label, needles in _TEXT:
        for n in needles:
            if n in low:
                return BlockSignal(code, label, n)
    return None


LOGIN_REQUIRED = BlockSignal("login_required", "Anmeldung abgelaufen oder fehlt", "login page")
