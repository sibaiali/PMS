# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Privacy-First Parking Badge with HMAC-signed expiring tokens, screenshot replay protection, and revocation."""

from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json
import uuid
from dataclasses import dataclass
from typing import Any

from smartpark.config.settings import SmartParkConfig
from smartpark.core.models import ParkingSession, SessionStatus, StaffRole, Vehicle
from smartpark.database.db import DatabaseManager


@dataclass
class PrivacyBadgeCredential:
    """Ephemeral, privacy-preserving digital parking badge credential."""

    token_jti: str
    session_id: str
    public_badge_id: str
    public_vehicle_token: str
    space_code: str
    issued_at: datetime.datetime
    expires_at: datetime.datetime
    rolling_window_slot: int
    signed_token: str
    qr_ascii_preview: str
    visible: bool
    privacy_notice: str = (
        "SIMULATED PRIVACY BADGE: Contains zero PII and no raw license plate. "
        "Auto-expires to prevent screenshot reuse."
    )


class PrivacyBadgeManager:
    """Issues, verifies, and revokes signed temporary parking badges (Section 8)."""

    def __init__(self, config: SmartParkConfig, db: DatabaseManager) -> None:
        self.config = config
        self.db = db
        self._revoked_jtis: set[str] = set()
        self._revoked_sessions: set[str] = set()
        self._presented_nonces: dict[str, datetime.datetime] = {}

    def _compute_signature(self, payload_b64: str) -> str:
        secret = self.config.token_signing_secret.encode("utf-8")
        return hmac.new(secret, payload_b64.encode("ascii"), hashlib.sha256).hexdigest()

    def _rolling_slot(self, at_time: datetime.datetime) -> int:
        # 30-second TOTP-like rolling slot to invalidate static screenshots
        return int(at_time.timestamp()) // 30

    def issue_badge(
        self,
        session: ParkingSession,
        vehicle: Vehicle,
        space_code: str,
        now: datetime.datetime | None = None,
        ttl_seconds: int | None = None,
    ) -> PrivacyBadgeCredential:
        """Issue a short-lived signed badge containing only public tokens (no PII)."""
        current = now or datetime.datetime.now(datetime.timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=datetime.timezone.utc)

        if session.status not in (SessionStatus.ACTIVE, SessionStatus.CHECKOUT_PENDING):
            raise ValueError("Privacy badge is only available while a parking session is active.")
        if session.session_id in self._revoked_sessions:
            raise ValueError("Badge issuance blocked: session credential has been revoked.")

        ttl = ttl_seconds if ttl_seconds is not None else self.config.badge_ttl_seconds
        expires = current + datetime.timedelta(seconds=ttl)
        jti = f"JTI-{uuid.uuid4().hex[:12].upper()}"
        slot = self._rolling_slot(current)

        payload = {
            "jti": jti,
            "sid": session.session_id,
            "bid": session.public_badge_id,
            "pvt": vehicle.public_vehicle_token,
            "spc": space_code,
            "iat": int(current.timestamp()),
            "exp": int(expires.timestamp()),
            "slot": slot,
            "sim": True,
        }
        payload_bytes = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        payload_b64 = base64.urlsafe_b64encode(payload_bytes).decode("ascii").rstrip("=")
        sig = self._compute_signature(payload_b64)
        signed_token = f"{payload_b64}.{sig}"

        qr_preview = self._render_qr_ascii(signed_token)
        return PrivacyBadgeCredential(
            token_jti=jti,
            session_id=session.session_id,
            public_badge_id=session.public_badge_id,
            public_vehicle_token=vehicle.public_vehicle_token,
            space_code=space_code,
            issued_at=current,
            expires_at=expires,
            rolling_window_slot=slot,
            signed_token=signed_token,
            qr_ascii_preview=qr_preview,
            visible=True,
        )

    def verify_badge_token(
        self,
        signed_token: str,
        now: datetime.datetime | None = None,
        consume_single_use: bool = False,
        enforce_rolling_window: bool = False,
    ) -> tuple[bool, str, dict[str, Any] | None]:
        """Perform server-side cryptographic, expiry, revocation, and session status checks."""
        current = now or datetime.datetime.now(datetime.timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=datetime.timezone.utc)

        if "." not in signed_token:
            return False, "Malformed token structure.", None

        payload_b64, provided_sig = signed_token.split(".", 1)
        expected_sig = self._compute_signature(payload_b64)
        if not hmac.compare_digest(provided_sig, expected_sig):
            return False, "Invalid cryptographic signature.", None

        try:
            padded = payload_b64 + "=" * (-len(payload_b64) % 4)
            payload = json.loads(base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8"))
        except Exception:
            return False, "Corrupted token payload.", None

        jti = payload.get("jti", "")
        session_id = payload.get("sid", "")
        exp_ts = int(payload.get("exp", 0))

        if jti in self._revoked_jtis or session_id in self._revoked_sessions:
            return False, "Token has been revoked.", payload

        if int(current.timestamp()) > exp_ts:
            return False, "Token has expired.", payload

        if enforce_rolling_window:
            current_slot = self._rolling_slot(current)
            if abs(current_slot - int(payload.get("slot", -999))) > 1:
                return (
                    False,
                    "Static screenshot rejected: rolling window slot is stale.",
                    payload,
                )

        if consume_single_use:
            if jti in self._presented_nonces:
                return False, "Replay detected: single-use token already presented.", payload
            self._presented_nonces[jti] = current

        # Server-side session verification
        session = self.db.get_session(session_id)
        if session is None or session.status not in (
            SessionStatus.ACTIVE,
            SessionStatus.CHECKOUT_PENDING,
        ):
            return False, "Associated parking session is no longer active.", payload

        return True, "Valid active privacy badge.", payload

    def revoke_badge_token(
        self,
        jti_or_session_id: str,
        actor_id: str = "SYSTEM",
        actor_role: StaffRole | str = StaffRole.SECURITY_OFFICER,
    ) -> None:
        if jti_or_session_id.startswith("JTI-"):
            self._revoked_jtis.add(jti_or_session_id)
        else:
            self._revoked_sessions.add(jti_or_session_id)
        self.db.record_audit(
            actor_id=actor_id,
            actor_role=actor_role,
            action="REVOKE_PRIVACY_BADGE",
            target_entity=jti_or_session_id,
            details={"revoked": True},
        )

    def should_display_badge(self, session: ParkingSession | None) -> bool:
        """Return True only when the session is active and not revoked."""
        if session is None:
            return False
        if session.session_id in self._revoked_sessions:
            return False
        return session.status in (SessionStatus.ACTIVE, SessionStatus.CHECKOUT_PENDING)

    @staticmethod
    def _render_qr_ascii(data: str, size: int = 15) -> str:
        """Generate a deterministic, compact QR-style matrix representation for UI display."""
        digest = hashlib.sha256(data.encode("utf-8")).digest()
        rows: list[str] = []
        for r in range(size):
            line_chars: list[str] = []
            for c in range(size):
                # Finder-like corners
                if (r < 3 and c < 3) or (r < 3 and c >= size - 3) or (r >= size - 3 and c < 3):
                    line_chars.append("██")
                else:
                    byte_idx = (r * size + c) % len(digest)
                    bit = (digest[byte_idx] >> ((r + c) % 8)) & 1
                    line_chars.append("██" if bit else "  ")
            rows.append("".join(line_chars))
        return "\n".join(rows)
