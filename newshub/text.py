"""Small helpers for cleaning text and URLs."""

from __future__ import annotations

import hashlib
import html
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"\s+")

# Query parameters that only track clicks and never change the page.
_TRACKING_PARAMS = {"guccounter", "guce_referrer", "guce_referrer_sig", "cmpid", "ref", "src", "ncid", "fbclid", "gclid"}


def clean_text(value: str | None) -> str:
    """Unescape HTML entities and collapse whitespace."""
    if not value:
        return ""
    return _SPACE_RE.sub(" ", html.unescape(value)).strip()


def strip_html(value: str | None) -> str:
    if not value:
        return ""
    return clean_text(_TAG_RE.sub(" ", html.unescape(value)))


def truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    cut = value[: limit - 1].rsplit(" ", 1)[0]
    return cut + "…"


def normalize_url(url: str) -> str:
    """Lower-case the host, drop fragments, tracking parameters and trailing slashes,
    so the same article reached through slightly different links is stored once."""
    parts = urlsplit(url.strip())
    query = [
        (k, v)
        for k, v in parse_qsl(parts.query, keep_blank_values=True)
        if not k.lower().startswith("utm_") and k.lower() not in _TRACKING_PARAMS
    ]
    path = parts.path
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query), ""))


def url_hash(url: str) -> str:
    return hashlib.sha256(normalize_url(url).encode("utf-8")).hexdigest()
