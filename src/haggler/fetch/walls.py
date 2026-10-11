"""Walls on pages that loaded (2xx after the redirects): a sign-in or account-verification page
the request ended on, or a bot challenge served with 200. HttpFetcher only raises BlockedError
for a wall with a 4xx/503 status (client.classify_status); these tests let the bundles probe, the
bundle watcher and the re-check of bundle findings treat the 2xx ones as unreadable too, never as
the page's content ("the value changed"). Nothing here evades a wall: it only names it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from urllib.parse import urlsplit

from haggler.fetch.client import looks_like_bot_wall

# Visible text shorter than this is most likely painted by JavaScript.
THIN_TEXT_CHARS = 400
# Sign-in paths, and account-verification walls (Mercado Libre sends some pages to
# /gz/account-verification?go=...): either way the page is behind the provider's account.
LOGIN_PATH = re.compile(
    r"(?:^|/)(?:login|log-in|signin|sign-in|sign_in|logon|auth|authorize|oauth2?|sso|servicelogin"
    r"|lgz|account-verification)(?:$|[/?._-])",
    re.IGNORECASE,
)
# Hosts that only serve sign-in (accounts.google.com, idmsa.apple.com, login.microsoftonline...).
LOGIN_HOST = re.compile(r"^(?:login|signin|accounts?|auth|sso|idmsa|appleid)\.", re.IGNORECASE)
# Response headers that mark a challenge by themselves on a 2xx page ("server" only feeds the
# Akamai deny-page test, which also needs its text). A bare `x-datadome` is not one: DataDome
# sends it on ordinary responses too.
CHALLENGE_HEADERS = frozenset({"cf-mitigated", "x-amzn-waf-action", "server"})


def is_login_url(url: str) -> bool:
    parts = urlsplit(url)
    return bool(LOGIN_HOST.match(parts.hostname or "") or LOGIN_PATH.search(parts.path))


def login_wall(url: str, final_url: str) -> bool:
    """The final URL is a sign-in page the requested one was not (a login wall)."""
    return is_login_url(final_url) and not is_login_url(url)


def challenge_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """The response headers page_wall reads (lower-cased names), the only ones worth keeping."""
    return {k.lower(): v for k, v in headers.items() if k.lower() in CHALLENGE_HEADERS}


def page_wall(headers: Mapping[str, str], title: str, lines: list[str], thin: bool) -> str | None:
    """The bot wall a 2xx page is: by a definite challenge header, or, on a thin page, by a
    marker in its title or visible text. Never by script sources or attributes: ordinary pages
    load reCAPTCHA, DataDome or Cloudflare's bot-management scripts."""
    return looks_like_bot_wall(
        challenge_headers(headers), "\n".join([title, *lines]) if thin else ""
    )
