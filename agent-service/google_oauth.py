"""Google OAuth 2.0 (web flow) for the PythonAnywhere driver.

Replaces the service-account key file entirely. The calendar is
accessed with the Google account that signed in through the app's own
/authorize flow: Ting opens the app, taps Allow once, and the refresh
token is stored server-side in Upstash KV (key ``luna:google_oauth``).
Ting never pastes a key, never handles a file, and the app never sees
a private key.

Flow (Google's web-server flow — google-auth-oauthlib):
  /authorize       -> Flow with access_type='offline' + prompt='consent'
                      (that pair is what makes Google return a refresh
                      token), redirect to accounts.google.com.
  /oauth2callback  -> verify the state value, exchange the code,
                      verify the signed-in account is the calendar
                      owner (id_token email == CALENDAR_OWNER_EMAIL),
                      store the refresh token in KV.
  calendar_connected() -> True when a refresh token is stored.

Safety:
  * Only the calendar owner's account may complete the flow. The
    scopes include ``openid`` + ``email`` so the callback can read the
    id_token and check the email before storing anything. A tester
    signing in with their own account gets a polite refusal, never a
    stored token (their credentials could not write to Ting's
    calendar anyway, and storing them would break every booking).
  * The refresh token is never stored in the browser, never in a
    file, never printed, never in a log. Only KV (server-side) and
    the bench-only LUNA_TEST_OAUTH_JSON env hook (see
    calendar_booking.py) ever hold it.
  * Google caps refresh tokens at 100 per account per client ID and
    silently invalidates the oldest when a new one is created — so
    the flow is run ONCE per install, not routinely. Revoking at
    myaccount.google.com/permissions invalidates the stored token;
    the next booking then fails closed with "not connected" and the
    driver page shows Connect again.

Requires: google-auth-oauthlib, google-auth (see requirements.txt).
"""

from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone

from google_auth_oauthlib.flow import Flow
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token as google_id_token

SCOPES = [
    "openid",
    "email",
    "https://www.googleapis.com/auth/calendar.events",
]
CALENDAR_SCOPES = ["https://www.googleapis.com/auth/calendar.events"]

OAUTH_KV_KEY = "luna:google_oauth"
STATE_KV_PREFIX = "luna:oauth_state:"
STATE_TTL_SECONDS = 600

AUTH_URI = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URI = "https://oauth2.googleapis.com/token"

CALENDAR_OWNER_EMAIL = os.environ.get(
    "CALENDAR_OWNER_EMAIL", "tinglott@gmail.com").strip().lower()


class OAuthConfigError(Exception):
    """Google OAuth client is not configured on this deployment."""


class OAuthFlowError(Exception):
    """The sign-in flow failed or was refused (safe to show the user)."""


def _client_config() -> dict:
    """OAuth web-client config from env. Raises OAuthConfigError when
    the client ID/secret have not been set on the host."""
    client_id = os.environ.get("GOOGLE_OAUTH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("GOOGLE_OAUTH_CLIENT_SECRET", "").strip()
    if not client_id or not client_secret:
        raise OAuthConfigError(
            "GOOGLE_OAUTH_CLIENT_ID / GOOGLE_OAUTH_CLIENT_SECRET are not "
            "set on this deployment — the Google OAuth client has not "
            "been created yet.")
    return {"web": {
        "client_id": client_id,
        "client_secret": client_secret,
        "auth_uri": AUTH_URI,
        "token_uri": TOKEN_URI,
    }}


def _store():
    """KV store handle (imported lazily — index.py already wires it)."""
    from kvstore import get_store
    return get_store()


def build_flow(redirect_uri: str) -> Flow:
    """A fresh OAuth Flow for this redirect URI (must match the URI
    registered on the Google OAuth client exactly).

    PKCE is OFF (autogenerate_code_verifier=False): the Flow instance
    is recreated in the callback, so an auto-generated code_verifier
    would not survive the redirect — Google would refuse the code
    exchange as a verifier mismatch. This is a confidential web client
    (client_secret authenticates it), where PKCE is optional.
    """
    return Flow.from_client_config(
        _client_config(), scopes=SCOPES, redirect_uri=redirect_uri,
        autogenerate_code_verifier=False)


def authorization_url(redirect_uri: str) -> tuple[str, str]:
    """Start the flow: returns (google_auth_url, state). The state is
    stored server-side in KV for 10 minutes so the callback can
    verify it — no state ever lives in the browser alone."""
    flow = build_flow(redirect_uri)
    state = secrets.token_urlsafe(16)
    try:
        _store().set(STATE_KV_PREFIX + state, "1", ex=STATE_TTL_SECONDS)
    except Exception:
        # KV unavailable: the flow cannot be verified later, so do not
        # start it — fail closed rather than hand out an unverifiable URL.
        raise OAuthFlowError(
            "the session store is unavailable — try again shortly.")
    auth_url, _ = flow.authorization_url(
        access_type="offline", prompt="consent", state=state)
    # state=state is load-bearing: requests-oauthlib generates its OWN
    # state when none is passed, and the callback verifies the state
    # against KV — without this the URL's state never matches the
    # stored one and every sign-in fails as "expired or already used".
    return auth_url, state


def exchange_code(redirect_uri: str, code: str, state: str) -> dict:
    """Complete the flow: verify the state, exchange the code, verify
    the signed-in account is the calendar owner, store the refresh
    token in KV. Returns the stored record (email + scopes + time).
    Raises OAuthFlowError on any refusal — never stores a token for
    the wrong account or without a refresh token."""
    try:
        seen = _store().get(STATE_KV_PREFIX + state)
    except Exception as exc:
        raise OAuthFlowError(
            "the session store is unavailable — try again shortly.") from exc
    if not seen:
        raise OAuthFlowError(
            "this sign-in link has expired or was already used — "
            "open the app and tap Connect Google Calendar again.")
    try:
        _store().delete(STATE_KV_PREFIX + state)
    except Exception:
        pass

    flow = build_flow(redirect_uri)
    try:
        flow.fetch_token(code=code)
    except Exception as exc:
        raise OAuthFlowError(
            f"Google refused the sign-in: {exc}") from exc
    creds = flow.credentials
    if not creds.refresh_token:
        raise OAuthFlowError(
            "Google did not return a refresh token. Revoke this app at "
            "myaccount.google.com/permissions and try again.")
    if not creds.id_token:
        raise OAuthFlowError(
            "Google did not return an account identity — try again.")
    try:
        payload = google_id_token.verify_oauth2_token(
            creds.id_token, google_requests.Request(), creds.client_id)
    except Exception as exc:
        raise OAuthFlowError(
            f"could not verify the signed-in Google account: {exc}") from exc
    email = str(payload.get("email") or "").strip().lower()
    if email != CALENDAR_OWNER_EMAIL:
        raise OAuthFlowError(
            "that Google account is not the calendar owner — sign in as "
            f"{CALENDAR_OWNER_EMAIL}. Nothing was connected.")
    granted = list(creds.scopes or [])
    if CALENDAR_SCOPES[0] not in granted:
        raise OAuthFlowError(
            "the Calendar permission was not granted — try again and "
            "tap Allow on the calendar permission.")
    record = {
        "refresh_token": creds.refresh_token,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "token_uri": creds.token_uri or TOKEN_URI,
        "scopes": granted,
        "email": email,
        "connected_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        _store().set_json(OAUTH_KV_KEY, record)
    except Exception as exc:
        raise OAuthFlowError(
            "the sign-in worked but the token could not be saved — "
            f"try again shortly. ({exc})") from exc
    return record


def calendar_connected() -> bool:
    """True when a refresh token is stored (never raises — a store
    failure reads as 'not connected')."""
    try:
        data = _store().get_json(OAUTH_KV_KEY)
    except Exception:
        return False
    return bool(data and data.get("refresh_token"))


def load_oauth_record() -> dict | None:
    """The stored OAuth record, or None. Used by calendar_booking.py
    (production source of truth — KV only)."""
    try:
        data = _store().get_json(OAUTH_KV_KEY)
    except Exception:
        return None
    if data and data.get("refresh_token"):
        return data
    return None


def clear_oauth_record() -> None:
    """Delete the stored token (revoked grant / disconnect). Best
    effort — never raises."""
    try:
        _store().delete(OAUTH_KV_KEY)
    except Exception:
        pass
