import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from google.auth.exceptions import GoogleAuthError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import OAuthConnection, OAuthState
from app.publishing.security import TokenCipher, TokenEncryptionError
from app.publishing.youtube_client import (
    GOOGLE_TOKEN_URI,
    YOUTUBE_SCOPES,
    PublishingProvider,
    YouTubeApiError,
)

logger = logging.getLogger(__name__)
OAUTH_STATE_TTL_MINUTES = 10


class OAuthNotConfigured(RuntimeError):
    pass


class OAuthStateError(RuntimeError):
    pass


class YouTubeOAuthService:
    def __init__(self, settings: Settings, provider: PublishingProvider, flow_factory=Flow):
        self.settings = settings
        self.provider = provider
        self.flow_factory = flow_factory

    def _client_config(self) -> dict:
        client_secret = (
            self.settings.google_client_secret.get_secret_value()
            if self.settings.google_client_secret
            else ""
        )
        if not self.settings.google_client_id or not client_secret:
            raise OAuthNotConfigured("Google OAuth client ID/secret are not configured")
        return {
            "web": {
                "client_id": self.settings.google_client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": GOOGLE_TOKEN_URI,
                "redirect_uris": [self.settings.google_oauth_redirect_uri],
            }
        }

    def _cipher(self) -> TokenCipher:
        key = (
            self.settings.oauth_token_encryption_key.get_secret_value()
            if self.settings.oauth_token_encryption_key
            else None
        )
        return TokenCipher(key)

    def authorization_url(self, session: Session) -> str:
        client_config = self._client_config()
        self._cipher()
        state = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        session.query(OAuthState).filter(
            or_(OAuthState.expires_at <= now, OAuthState.consumed_at.is_not(None))
        ).delete(synchronize_session=False)
        session.add(
            OAuthState(
                state_hash=hashlib.sha256(state.encode("utf-8")).hexdigest(),
                expires_at=now + timedelta(minutes=OAUTH_STATE_TTL_MINUTES),
            )
        )
        session.commit()
        flow = self.flow_factory.from_client_config(
            client_config,
            scopes=list(YOUTUBE_SCOPES),
            state=state,
            redirect_uri=self.settings.google_oauth_redirect_uri,
        )
        authorization_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",
        )
        return authorization_url

    def complete_callback(self, session: Session, state: str, authorization_response: str) -> OAuthConnection:
        if not state:
            raise OAuthStateError("OAuth state is missing")
        state_hash = hashlib.sha256(state.encode("utf-8")).hexdigest()
        state_row = session.get(OAuthState, state_hash)
        now = datetime.now(timezone.utc)
        if state_row is None or state_row.consumed_at is not None:
            raise OAuthStateError("OAuth state is invalid or already used")
        expires_at = state_row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at <= now:
            raise OAuthStateError("OAuth state has expired")

        client_config = self._client_config()
        cipher = self._cipher()
        flow = self.flow_factory.from_client_config(
            client_config,
            scopes=list(YOUTUBE_SCOPES),
            state=state,
            redirect_uri=self.settings.google_oauth_redirect_uri,
        )
        try:
            flow.fetch_token(authorization_response=authorization_response)
            credentials = flow.credentials
            refresh_token = credentials.refresh_token
            if not refresh_token:
                raise OAuthStateError("Google did not return a refresh token; revoke app access and authorize again")
            channel_id, channel_title = self.provider.channel_info(credentials)
        except OAuthStateError:
            raise
        except YouTubeApiError as exc:
            logger.warning("YouTube OAuth channel verification failed: %s", exc.code)
            raise OAuthStateError("Could not verify the authorized YouTube channel") from exc
        except Exception as exc:
            logger.warning("Google OAuth callback failed")
            raise OAuthStateError("Google authorization failed; check OAuth client and redirect URL configuration") from exc

        state_row.consumed_at = now
        existing = session.get(OAuthConnection, 1)
        if existing is None:
            existing = OAuthConnection(id=1, channel_id=channel_id, encrypted_refresh_token="")
            session.add(existing)
        existing.channel_id = channel_id
        existing.channel_title = channel_title
        existing.encrypted_refresh_token = cipher.encrypt(refresh_token)
        existing.scopes = list(YOUTUBE_SCOPES)
        existing.status = "CONNECTED"
        existing.connected_at = now
        existing.last_verified_at = now
        existing.revoked_at = None
        session.commit()
        return existing

    def credentials(self, session: Session) -> Credentials:
        connection = session.get(OAuthConnection, 1)
        if connection is None or connection.status != "CONNECTED" or not connection.encrypted_refresh_token:
            raise OAuthNotConfigured("YouTube channel is not connected")
        try:
            refresh_token = self._cipher().decrypt(connection.encrypted_refresh_token)
        except TokenEncryptionError:
            connection.status = "REAUTH_REQUIRED"
            session.commit()
            raise OAuthNotConfigured("Stored YouTube authorization cannot be decrypted; reconnect the channel")
        if not self.settings.google_client_id or not self.settings.google_client_secret:
            raise OAuthNotConfigured("Google OAuth client ID/secret are not configured")
        credentials = Credentials(
            token=None,
            refresh_token=refresh_token,
            token_uri=GOOGLE_TOKEN_URI,
            client_id=self.settings.google_client_id,
            client_secret=self.settings.google_client_secret.get_secret_value(),
            scopes=list(YOUTUBE_SCOPES),
        )
        try:
            credentials.refresh(self._google_request())
        except GoogleAuthError as exc:
            connection.status = "REAUTH_REQUIRED"
            session.commit()
            logger.warning("YouTube refresh token was rejected")
            raise OAuthNotConfigured("YouTube authorization expired or was revoked; reconnect the channel") from exc
        connection.last_verified_at = datetime.now(timezone.utc)
        session.commit()
        return credentials

    def disconnect(self, session: Session) -> None:
        connection = session.get(OAuthConnection, 1)
        if not connection:
            return
        try:
            token = self._cipher().decrypt(connection.encrypted_refresh_token)
            self.provider.revoke(token)
        except (TokenEncryptionError, OAuthNotConfigured):
            logger.warning("Local OAuth tokens cleared; remote revocation could not be confirmed")
        connection.encrypted_refresh_token = ""
        connection.status = "DISCONNECTED"
        connection.revoked_at = datetime.now(timezone.utc)
        session.commit()

    @staticmethod
    def _google_request():
        from google.auth.transport.requests import Request

        return Request()