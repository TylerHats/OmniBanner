"""Authentication, password hashing, JWT tokens, TOTP/backup codes, rate limiting, and OIDC."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from urllib.parse import urlencode

import bcrypt
import httpx
import jwt
import pyotp
from jwt import InvalidTokenError, PyJWK

from config_store import get_secret_key

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 1440  # 24 hours
MFA_CHALLENGE_EXPIRE_MINUTES = 5
OIDC_TX_EXPIRE_MINUTES = 10

_DISCOVERY_CACHE: dict[str, tuple[float, dict[str, Any]]] = {}
_JWKS_CACHE: dict[str, tuple[float, list[dict[str, Any]]]] = {}
_CACHE_TTL_SECONDS = 600


def _prehash_password(password: str) -> bytes:
    """Pre-hash password with SHA-256 + base64 (44 bytes) to avoid bcrypt 72-byte limit."""
    digest = hashlib.sha256(password.encode("utf-8")).digest()
    return base64.b64encode(digest)


def get_password_hash(password: str) -> str:
    """Hash a password using bcrypt with SHA-256 pre-hashing."""
    prehashed = _prehash_password(password)
    return bcrypt.hashpw(prehashed, bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: Optional[str]) -> bool:
    """Verify password against stored bcrypt hash, supporting legacy un-prehashed bcrypt."""
    if not plain_password or not hashed_password:
        return False
    hash_bytes = hashed_password.encode("utf-8")
    try:
        if bcrypt.checkpw(_prehash_password(plain_password), hash_bytes):
            return True
    except (ValueError, TypeError):
        pass
    try:
        raw_truncated = plain_password.encode("utf-8")[:72]
        return bcrypt.checkpw(raw_truncated, hash_bytes)
    except (ValueError, TypeError):
        return False


def create_access_token(
    user_id: int,
    username: str,
    role: str = "admin",
    expires_delta: Optional[timedelta] = None,
) -> tuple[str, str, datetime]:
    """Create signed JWT access token and return (token, jti, expires_at_utc_naive)."""
    now = datetime.now(timezone.utc)
    delta = expires_delta or timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    expire = now + delta
    jti = secrets.token_urlsafe(24)
    payload = {
        "sub": username,
        "uid": user_id,
        "role": role,
        "type": "access",
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    token = jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)
    return token, jti, expire.replace(tzinfo=None)


def create_mfa_challenge_token(user_id: int, username: str) -> str:
    """Create a short-lived signed token for MFA step-2 verification."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=MFA_CHALLENGE_EXPIRE_MINUTES)
    payload = {
        "sub": username,
        "uid": user_id,
        "type": "mfa_challenge",
        "jti": secrets.token_urlsafe(16),
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)


def create_oidc_tx_token(
    provider_slug: str,
    state: str,
    nonce: str,
    code_verifier: str,
    next_url: str = "/dashboard",
) -> str:
    """Create a signed cookie token storing OIDC PKCE/state/nonce transaction parameters."""
    now = datetime.now(timezone.utc)
    expire = now + timedelta(minutes=OIDC_TX_EXPIRE_MINUTES)
    payload = {
        "type": "oidc_tx",
        "provider_slug": provider_slug,
        "state": state,
        "nonce": nonce,
        "code_verifier": code_verifier,
        "next_url": next_url,
        "iat": int(now.timestamp()),
        "exp": int(expire.timestamp()),
    }
    return jwt.encode(payload, get_secret_key(), algorithm=ALGORITHM)


def decode_token(token: Optional[str], expected_type: Optional[str] = None) -> Optional[dict[str, Any]]:
    """Decode and verify a signed JWT token."""
    if not token:
        return None
    try:
        payload = jwt.decode(token, get_secret_key(), algorithms=[ALGORITHM])
    except InvalidTokenError:
        return None
    if expected_type and payload.get("type") != expected_type:
        return None
    return payload


def decode_access_token(token: Optional[str]) -> Optional[dict[str, Any]]:
    """Decode an access token (tolerating legacy tokens without a 'type' claim)."""
    payload = decode_token(token)
    if not payload:
        return None
    token_type = payload.get("type")
    if token_type is not None and token_type != "access":
        return None
    return payload


def decode_oidc_tx_token(token: Optional[str]) -> Optional[dict[str, Any]]:
    """Decode an OIDC transaction cookie."""
    return decode_token(token, expected_type="oidc_tx")


def generate_pkce_pair() -> tuple[str, str]:
    """Generate RFC 7636 PKCE (code_verifier, S256 code_challenge)."""
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def hash_token(raw_token: str) -> str:
    """Hash opaque pairing/site/webhook tokens with SHA-256."""
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


# --- TOTP & Backup Code Operations ---


def generate_totp_secret() -> str:
    return pyotp.random_base32()


def get_totp_uri(secret: str, username: str, issuer_name: str = "OmniBanner") -> str:
    return pyotp.totp.TOTP(secret).provisioning_uri(name=username, issuer_name=issuer_name)


def verify_totp_code(secret: Optional[str], code: Optional[str]) -> bool:
    if not secret or not code:
        return False
    normalized = code.strip().replace(" ", "").replace("-", "")
    if not normalized.isdigit() or len(normalized) != 6:
        return False
    return bool(pyotp.TOTP(secret).verify(normalized, valid_window=1))


def generate_backup_codes(count: int = 8) -> tuple[list[str], str]:
    """Generate one-time recovery backup codes and return (plaintext_list, json_hashes)."""
    plain_codes: list[str] = []
    hashes: list[str] = []
    for _ in range(count):
        part1 = secrets.token_hex(2).upper()
        part2 = secrets.token_hex(2).upper()
        code = f"{part1}-{part2}"
        plain_codes.append(code)
        hashes.append(get_password_hash(code.replace("-", "")))
    return plain_codes, json.dumps(hashes)


def consume_backup_code(hashed_json: Optional[str], code: Optional[str]) -> tuple[bool, str]:
    """Verify and consume a single backup code; return (matched, updated_json)."""
    if not hashed_json or not code:
        return False, hashed_json or "[]"
    try:
        hashes: list[str] = json.loads(hashed_json)
    except (ValueError, TypeError):
        return False, "[]"

    normalized = code.strip().replace("-", "").replace(" ", "").upper()
    if len(normalized) < 6:
        return False, hashed_json

    for idx, h in enumerate(hashes):
        if verify_password(normalized, h):
            remaining = hashes[:idx] + hashes[idx + 1 :]
            return True, json.dumps(remaining)
    return False, hashed_json


# --- In-memory Sliding Window Rate Limiter ---


class RateLimiter:
    """Thread-safe in-memory sliding-window rate limiter."""

    def __init__(self, max_requests: int = 10, window_seconds: int = 60) -> None:
        self.max_requests = max_requests
        self.window_seconds = window_seconds
        self._buckets: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        with self._lock:
            timestamps = [t for t in self._buckets.get(key, []) if t > cutoff]
            if len(timestamps) >= self.max_requests:
                self._buckets[key] = timestamps
                return False
            timestamps.append(now)
            self._buckets[key] = timestamps
            return True

    def reset(self) -> None:
        with self._lock:
            self._buckets.clear()


# --- OIDC Operations ---


def _httpx_verify_arg(verify_tls: bool = True, ca_bundle_path: str = "") -> Any:
    if not verify_tls:
        return False
    ca_path = (ca_bundle_path or "").strip()
    if ca_path:
        return ca_path
    return True


async def discover_oidc_endpoints(
    issuer_url: str,
    verify_tls: bool = True,
    ca_bundle_path: str = "",
    force_refresh: bool = False,
) -> Optional[dict[str, Any]]:
    """Fetch and cache OIDC .well-known/openid-configuration document."""
    base = (issuer_url or "").strip().rstrip("/")
    if not base:
        return None
    discovery_url = (
        base
        if base.endswith("/.well-known/openid-configuration")
        else f"{base}/.well-known/openid-configuration"
    )
    cache_key = f"{discovery_url}|{verify_tls}|{ca_bundle_path}"
    now = time.monotonic()
    if not force_refresh and cache_key in _DISCOVERY_CACHE:
        cached_at, payload = _DISCOVERY_CACHE[cache_key]
        if now - cached_at < _CACHE_TTL_SECONDS:
            return payload

    verify = _httpx_verify_arg(verify_tls, ca_bundle_path)
    async with httpx.AsyncClient(verify=verify, follow_redirects=True) as client:
        try:
            response = await client.get(discovery_url, timeout=10.0)
            if response.status_code == 200:
                payload = response.json()
                _DISCOVERY_CACHE[cache_key] = (now, payload)
                return payload
        except Exception as exc:
            print(f"OIDC Discovery failed for {discovery_url}: {exc}")
    return None


def build_oidc_authorization_url(
    discovery: dict[str, Any],
    client_id: str,
    redirect_uri: str,
    scopes: str,
    state: str,
    nonce: str,
    code_challenge: str,
) -> str:
    """Build URL-encoded OIDC authorization redirect URL with PKCE S256."""
    auth_endpoint = discovery["authorization_endpoint"]
    query = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes or "openid profile email",
            "state": state,
            "nonce": nonce,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
    )
    sep = "&" if "?" in auth_endpoint else "?"
    return f"{auth_endpoint}{sep}{query}"


async def exchange_oidc_code(
    token_endpoint: str,
    client_id: str,
    client_secret: str,
    code: str,
    redirect_uri: str,
    code_verifier: str = "",
    verify_tls: bool = True,
    ca_bundle_path: str = "",
) -> Optional[dict[str, Any]]:
    """Exchange OIDC authorization code (with PKCE verifier) for tokens."""
    data: dict[str, str] = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
    }
    if client_secret:
        data["client_secret"] = client_secret
    if code_verifier:
        data["code_verifier"] = code_verifier

    verify = _httpx_verify_arg(verify_tls, ca_bundle_path)
    async with httpx.AsyncClient(verify=verify) as client:
        try:
            response = await client.post(token_endpoint, data=data, timeout=10.0)
            if response.status_code == 200:
                return response.json()
            print(f"OIDC Token Exchange failed status {response.status_code}: {response.text}")
        except Exception as exc:
            print(f"OIDC Token Exchange HTTP error: {exc}")
    return None


async def fetch_jwks(
    jwks_uri: str,
    verify_tls: bool = True,
    ca_bundle_path: str = "",
    force_refresh: bool = False,
) -> list[dict[str, Any]]:
    """Fetch and cache JWKS public keys from the IdP."""
    if not jwks_uri:
        return []
    cache_key = f"{jwks_uri}|{verify_tls}|{ca_bundle_path}"
    now = time.monotonic()
    if not force_refresh and cache_key in _JWKS_CACHE:
        cached_at, keys = _JWKS_CACHE[cache_key]
        if now - cached_at < _CACHE_TTL_SECONDS:
            return keys

    verify = _httpx_verify_arg(verify_tls, ca_bundle_path)
    async with httpx.AsyncClient(verify=verify) as client:
        try:
            response = await client.get(jwks_uri, timeout=10.0)
            if response.status_code == 200:
                keys = response.json().get("keys", [])
                _JWKS_CACHE[cache_key] = (now, keys)
                return keys
        except Exception as exc:
            print(f"OIDC JWKS fetch failed for {jwks_uri}: {exc}")
    return []


async def verify_id_token(
    id_token: str,
    discovery: dict[str, Any],
    client_id: str,
    expected_nonce: str,
    verify_tls: bool = True,
    ca_bundle_path: str = "",
) -> Optional[dict[str, Any]]:
    """Validate OIDC ID token signature via JWKS plus iss, aud, exp, and nonce."""
    if not id_token:
        return None
    jwks_uri = discovery.get("jwks_uri")
    if not jwks_uri:
        return None

    try:
        unverified_header = jwt.get_unverified_header(id_token)
    except InvalidTokenError:
        return None

    kid = unverified_header.get("kid")
    alg = unverified_header.get("alg") or "RS256"
    allowed_algs = discovery.get("id_token_signing_alg_values_supported") or [
        "RS256",
        "RS384",
        "RS512",
        "ES256",
        "ES384",
        "PS256",
    ]
    if alg not in allowed_algs:
        return None

    keys = await fetch_jwks(jwks_uri, verify_tls=verify_tls, ca_bundle_path=ca_bundle_path)
    candidates = [k for k in keys if not kid or k.get("kid") == kid]
    if not candidates and kid:
        keys = await fetch_jwks(
            jwks_uri, verify_tls=verify_tls, ca_bundle_path=ca_bundle_path, force_refresh=True
        )
        candidates = [k for k in keys if k.get("kid") == kid]
    if not candidates:
        candidates = keys

    expected_issuer = discovery.get("issuer")
    for key_dict in candidates:
        try:
            jwk = PyJWK(key_dict)
            claims = jwt.decode(
                id_token,
                jwk.key,
                algorithms=[alg],
                audience=client_id,
                issuer=expected_issuer,
                options={"require": ["exp", "sub"]},
            )
            token_nonce = claims.get("nonce")
            if expected_nonce:
                if not token_nonce or not hmac.compare_digest(str(token_nonce), str(expected_nonce)):
                    return None
            return claims
        except InvalidTokenError:
            continue
        except Exception:
            continue
    return None


async def fetch_oidc_userinfo(
    userinfo_endpoint: str,
    access_token: str,
    verify_tls: bool = True,
    ca_bundle_path: str = "",
) -> Optional[dict[str, Any]]:
    """Fetch userinfo claims using access token."""
    if not userinfo_endpoint or not access_token:
        return None
    headers = {"Authorization": f"Bearer {access_token}"}
    verify = _httpx_verify_arg(verify_tls, ca_bundle_path)
    async with httpx.AsyncClient(verify=verify) as client:
        try:
            response = await client.get(userinfo_endpoint, headers=headers, timeout=10.0)
            if response.status_code == 200:
                return response.json()
        except Exception as exc:
            print(f"OIDC Userinfo fetch error: {exc}")
    return None


def _parse_csv_set(raw: Optional[str]) -> set[str]:
    if not raw:
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def extract_claims_groups(claims: dict[str, Any], groups_claim: str = "groups") -> set[str]:
    """Extract normalized lowercase group names from OIDC ID/userinfo claims."""
    raw = claims.get(groups_claim or "groups")
    if not raw:
        return set()
    if isinstance(raw, list):
        return {str(g).strip().lower() for g in raw if str(g).strip()}
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
        return {p.lower() for p in parts}
    return set()


def resolve_oidc_role(claims: dict[str, Any], provider: Any) -> Optional[str]:
    """Map IdP groups to OmniBanner role ('admin', 'editor', 'viewer') or None if denied."""
    admin_groups = _parse_csv_set(getattr(provider, "admin_groups", ""))
    editor_groups = _parse_csv_set(getattr(provider, "editor_groups", ""))
    viewer_groups = _parse_csv_set(getattr(provider, "viewer_groups", ""))
    groups_claim = getattr(provider, "groups_claim", "groups") or "groups"
    require_match = bool(getattr(provider, "require_group_match", False))

    user_groups = extract_claims_groups(claims, groups_claim)

    if admin_groups and (user_groups & admin_groups):
        return "admin"
    if editor_groups and (user_groups & editor_groups):
        return "editor"
    if viewer_groups and (user_groups & viewer_groups):
        return "viewer"

    any_mapping_defined = bool(admin_groups or editor_groups or viewer_groups)
    if require_match or any_mapping_defined:
        if require_match:
            return None
        return None if not user_groups and require_match else "viewer"

    return "admin"


async def test_oidc_provider(
    issuer_url: str,
    verify_tls: bool = True,
    ca_bundle_path: str = "",
) -> dict[str, Any]:
    """Test OIDC discovery and JWKS reachability and return diagnostics."""
    discovery = await discover_oidc_endpoints(
        issuer_url, verify_tls=verify_tls, ca_bundle_path=ca_bundle_path, force_refresh=True
    )
    if not discovery:
        return {"success": False, "detail": "Failed to fetch .well-known/openid-configuration"}

    jwks_uri = discovery.get("jwks_uri", "")
    keys = await fetch_jwks(jwks_uri, verify_tls=verify_tls, ca_bundle_path=ca_bundle_path, force_refresh=True)
    return {
        "success": True,
        "issuer": discovery.get("issuer"),
        "authorization_endpoint": discovery.get("authorization_endpoint"),
        "token_endpoint": discovery.get("token_endpoint"),
        "userinfo_endpoint": discovery.get("userinfo_endpoint"),
        "end_session_endpoint": discovery.get("end_session_endpoint"),
        "jwks_uri": jwks_uri,
        "jwks_key_count": len(keys),
        "pkce_methods": discovery.get("code_challenge_methods_supported", []),
    }
