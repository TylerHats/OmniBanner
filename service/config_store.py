"""Runtime directories, key management, secret encryption, and input sanitization."""

from __future__ import annotations

import base64
import hashlib
import html
import os
import re
import secrets
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urlparse

from cryptography.fernet import Fernet, InvalidToken

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("OMNIBANNER_DATA_DIR", os.path.join(BASE_DIR, "data"))
UPLOADS_DIR = os.path.join(BASE_DIR, "static", "uploads")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(UPLOADS_DIR, exist_ok=True)

_ENC_PREFIX = "enc:"
_HEX_COLOR_RE = re.compile(r"^#([0-9a-fA-F]{3}|[0-9a-fA-F]{6})$")
_DANGEROUS_RAW_TAGS = frozenset({"script", "style", "iframe", "object", "embed", "svg", "math"})
_ALLOWED_BANNER_TAGS = frozenset({"a", "strong", "b", "em", "i", "u", "s", "code", "span", "br"})
_VOID_TAGS = frozenset({"br"})
_SAFE_URL_RE = re.compile(r"^(https?://|mailto:|/(?!/))", re.IGNORECASE)
_CTRL_SPACE_RE = re.compile(r"[\x00-\x20\x7f]+")


def get_secret_key() -> str:
    """Return signing/master secret from env or persistent local file."""
    env_key = os.environ.get("OMNIBANNER_SECRET_KEY", "").strip()
    if env_key:
        return env_key

    os.makedirs(DATA_DIR, exist_ok=True)
    key_file = os.path.join(DATA_DIR, ".secret_key")
    if os.path.exists(key_file):
        with open(key_file, "r", encoding="utf-8") as fh:
            existing = fh.read().strip()
            if existing:
                return existing

    generated = secrets.token_hex(32)
    fd = os.open(key_file, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, generated.encode("utf-8"))
    finally:
        os.close(fd)
    try:
        os.chmod(key_file, 0o600)
    except OSError:
        pass
    return generated


def _get_fernet() -> Fernet:
    digest = hashlib.sha256(get_secret_key().encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: Optional[str]) -> str:
    """Encrypt sensitive credentials at rest with Fernet."""
    if not value:
        return ""
    if value.startswith(_ENC_PREFIX):
        return value
    token = _get_fernet().encrypt(value.encode("utf-8")).decode("ascii")
    return f"{_ENC_PREFIX}{token}"


def decrypt_secret(value: Optional[str]) -> str:
    """Decrypt stored credentials; gracefully handle unencrypted legacy values."""
    if not value:
        return ""
    if not value.startswith(_ENC_PREFIX):
        return value
    raw_token = value[len(_ENC_PREFIX) :]
    try:
        return _get_fernet().decrypt(raw_token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return ""


def is_safe_url(url: Optional[str]) -> bool:
    """Return True if URL uses an allowed http/https/mailto or relative path scheme."""
    if not url:
        return False
    cleaned = _CTRL_SPACE_RE.sub("", url.strip())
    return bool(_SAFE_URL_RE.match(cleaned))


def sanitize_url(url: Optional[str], default: str = "") -> str:
    """Validate and normalize a URL or return default."""
    if not url:
        return default
    candidate = url.strip()
    if is_safe_url(candidate):
        return candidate
    return default


def sanitize_hex_color(value: Optional[str], default: str = "#8b5cf6") -> str:
    """Validate 3- or 6-digit hex CSS color."""
    if not value:
        return default
    candidate = value.strip()
    if _HEX_COLOR_RE.match(candidate):
        return candidate.lower()
    return default


def sanitize_custom_css(css: Optional[str]) -> str:
    """Strip closing style tags and dangerous CSS expressions."""
    if not css:
        return ""
    cleaned = re.sub(r"</?\s*style[^>]*>", "", css, flags=re.IGNORECASE)
    cleaned = re.sub(r"expression\s*\(", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def sanitize_csv_cell(value: Optional[str]) -> str:
    """Neutralize spreadsheet formula injection characters in CSV exports."""
    if value is None:
        return ""
    text = str(value)
    stripped = text.lstrip()
    if stripped and stripped[0] in ("=", "+", "-", "@", "\t", "\r"):
        return "'" + text
    return text


class _BannerHTMLSanitizer(HTMLParser):
    """Strict allowlist HTML sanitizer for banner text."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.out: list[str] = []
        self.open_tags: list[str] = []
        self.skip_stack: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in _DANGEROUS_RAW_TAGS:
            self.skip_stack.append(tag)
            return
        if self.skip_stack:
            return
        if tag not in _ALLOWED_BANNER_TAGS:
            return

        if tag in _VOID_TAGS:
            self.out.append(f"<{tag}>")
            return

        attr_parts: list[str] = []
        if tag == "a":
            attr_map = {k.lower(): (v or "") for k, v in attrs}
            href = attr_map.get("href", "").strip()
            if is_safe_url(href):
                attr_parts.append(f'href="{html.escape(href, quote=True)}"')
                if href.lower().startswith(("http://", "https://")):
                    attr_parts.append('target="_blank"')
                attr_parts.append('rel="noopener noreferrer"')
            title = attr_map.get("title", "").strip()
            if title:
                attr_parts.append(f'title="{html.escape(title, quote=True)}"')

        attrs_str = (" " + " ".join(attr_parts)) if attr_parts else ""
        self.out.append(f"<{tag}{attrs_str}>")
        self.open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:
        tag = tag.lower()
        if tag in _VOID_TAGS and not self.skip_stack:
            self.out.append(f"<{tag}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if self.skip_stack:
            if tag in self.skip_stack:
                while self.skip_stack:
                    popped = self.skip_stack.pop()
                    if popped == tag:
                        break
            return
        if tag not in _ALLOWED_BANNER_TAGS or tag in _VOID_TAGS:
            return
        if tag in self.open_tags:
            while self.open_tags:
                popped = self.open_tags.pop()
                self.out.append(f"</{popped}>")
                if popped == tag:
                    break

    def handle_data(self, data: str) -> None:
        if self.skip_stack:
            return
        self.out.append(html.escape(data, quote=False))

    def handle_entityref(self, name: str) -> None:
        if self.skip_stack:
            return
        if re.match(r"^[a-zA-Z0-9]+$", name):
            self.out.append(f"&{name};")

    def handle_charref(self, name: str) -> None:
        if self.skip_stack:
            return
        if re.match(r"^(x[0-9a-fA-F]+|[0-9]+)$", name):
            self.out.append(f"&#{name};")

    def get_sanitized(self) -> str:
        while self.open_tags:
            popped = self.open_tags.pop()
            self.out.append(f"</{popped}>")
        return "".join(self.out).strip()


def sanitize_banner_html(raw: Optional[str]) -> str:
    """Sanitize banner HTML against a strict allowlist of safe inline tags."""
    if not raw:
        return ""
    parser = _BannerHTMLSanitizer()
    parser.feed(str(raw))
    parser.close()
    return parser.get_sanitized()


def resolve_public_base_url(request, config_public_url: str = "") -> str:
    """Resolve canonical public base URL from config, env, or proxy headers."""
    configured = (config_public_url or "").strip().rstrip("/")
    if configured:
        return configured

    env_url = os.environ.get("OMNIBANNER_PUBLIC_URL", "").strip().rstrip("/")
    if env_url:
        return env_url

    if request is None:
        return "http://localhost:8000"

    proto = (request.headers.get("x-forwarded-proto") or request.url.scheme or "http").split(",")[0].strip()
    host = (request.headers.get("x-forwarded-host") or request.headers.get("host") or "").split(",")[0].strip()
    if host:
        return f"{proto}://{host}".rstrip("/")

    parsed = urlparse(str(request.base_url))
    return f"{parsed.scheme}://{parsed.netloc}".rstrip("/")
