# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Authenticated Walk-Up Digital Key Prototype with Replay & Rate-Limit Protection (Section 7)."""

from __future__ import annotations

import abc
import datetime
import hmac
import hashlib
import secrets
import uuid
from dataclasses import dataclass
from typing import Any

from smartpark.config.settings import SmartParkConfig
from smartpark.core.models import AccountStatus, StaffRole
from smartpark.database.db import DatabaseManager


class VehicleAccessAdapter(abc.ABC):
    """Authorized adapter interface for simulated or OEM-supported lock/unlock requests."""

    @abc.abstractmethod
    def execute_vehicle_command(
        self,
        vehicle_id: str,
        digital_key_id: str,
        command: str,
    ) -> tuple[bool, str]:
        """Execute 'LOCK' or 'UNLOCK' via an authorized channel and verify result."""


class SimulatedVehicleAccessAdapter(VehicleAccessAdapter):
    """Simulated vehicle lock/unlock adapter (does not control any real vehicle hardware)."""

    def __init__(self) -> None:
        self.lock_states: dict[str, bool] = {}  # vehicle_id -> is_locked (True by default)
        self.simulate_adapter_failure: bool = False

    def execute_vehicle_command(
        self,
        vehicle_id: str,
        digital_key_id: str,
        command: str,
    ) -> tuple[bool, str]:
        if self.simulate_adapter_failure:
            return False, "Simulated OEM telematics adapter timeout — state not verified."
        cmd = command.upper()
        if cmd == "UNLOCK":
            self.lock_states[vehicle_id] = False
            return True, f"SIMULATED: Vehicle {vehicle_id} unlocked via enrolled key {digital_key_id}."
        if cmd == "LOCK":
            self.lock_states[vehicle_id] = True
            return True, f"SIMULATED: Vehicle {vehicle_id} locked via enrolled key {digital_key_id}."
        return False, f"Unsupported command: {command}"


@dataclass
class WalkUpChallenge:
    """Short-lived challenge issued upon BLE proximity; requires explicit user confirmation."""

    challenge_id: str
    nonce: str
    customer_id: str
    vehicle_id: str
    digital_key_id: str
    requested_action: str
    proximity_distance_m: float
    issued_at: datetime.datetime
    expires_at: datetime.datetime
    used: bool = False


class WalkUpDigitalKeyService:
    """Implements the 7-step customer-controlled Walk-Up Digital Key workflow."""

    def __init__(
        self,
        config: SmartParkConfig,
        db: DatabaseManager,
        adapter: VehicleAccessAdapter | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.adapter: VehicleAccessAdapter = adapter or SimulatedVehicleAccessAdapter()
        self._challenges: dict[str, WalkUpChallenge] = {}
        self._used_nonces: set[str] = set()
        self._revoked_keys: set[str] = set()
        self._rate_window: dict[str, list[datetime.datetime]] = {}

    def _check_rate_limit(self, customer_id: str, now: datetime.datetime) -> bool:
        cutoff = now - datetime.timedelta(minutes=1)
        history = [t for t in self._rate_window.get(customer_id, []) if t >= cutoff]
        if len(history) >= self.config.digital_key_rate_limit_per_minute:
            self._rate_window[customer_id] = history
            return False
        history.append(now)
        self._rate_window[customer_id] = history
        return True

    def detect_ble_proximity_and_issue_challenge(
        self,
        customer_id: str,
        vehicle_id: str,
        proximity_distance_m: float,
        requested_action: str = "UNLOCK",
        now: datetime.datetime | None = None,
    ) -> WalkUpChallenge:
        """Steps 1–3: Detect proximity, verify identity/enrollment, and issue confirmation challenge.

        Proximity alone NEVER unlocks the vehicle; it only creates a short-lived challenge
        requiring explicit user confirmation.
        """
        current = now or datetime.datetime.now(datetime.timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=datetime.timezone.utc)

        if not self._check_rate_limit(customer_id, current):
            raise PermissionError("Rate limit exceeded for digital key operations.")

        if proximity_distance_m > 10.0 or proximity_distance_m < 0.0:
            raise ValueError(
                f"Device distance ({proximity_distance_m:.1f}m) is outside the valid BLE walk-up zone (0–10m)."
            )

        customer = self.db.get_customer(customer_id)
        if customer is None or customer.status != AccountStatus.ACTIVE:
            raise PermissionError("Customer identity or account status is invalid.")

        vehicle = self.db.get_vehicle_by_id(vehicle_id)
        if vehicle is None or vehicle.customer_id != customer_id:
            raise PermissionError("Vehicle is not enrolled under this customer account.")

        if not vehicle.enrolled_digital_key_id:
            raise PermissionError("Vehicle does not have an enrolled digital key.")

        if vehicle.enrolled_digital_key_id in self._revoked_keys:
            raise PermissionError("Enrolled digital key has been revoked.")

        challenge = WalkUpChallenge(
            challenge_id=f"CHK-{uuid.uuid4().hex[:10].upper()}",
            nonce=secrets.token_hex(16),
            customer_id=customer_id,
            vehicle_id=vehicle_id,
            digital_key_id=vehicle.enrolled_digital_key_id,
            requested_action=requested_action.upper(),
            proximity_distance_m=proximity_distance_m,
            issued_at=current,
            expires_at=current
            + datetime.timedelta(seconds=self.config.digital_key_challenge_ttl_seconds),
        )
        self._challenges[challenge.challenge_id] = challenge
        return challenge

    def sign_user_confirmation(self, challenge: WalkUpChallenge, device_pin_or_biometric: str) -> str:
        """Simulate platform-supported biometric/passcode confirmation signature on the customer's phone."""
        msg = f"{challenge.challenge_id}:{challenge.nonce}:{challenge.digital_key_id}:{device_pin_or_biometric}"
        return hmac.new(
            self.config.token_signing_secret.encode("utf-8"),
            msg.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def confirm_and_execute(
        self,
        challenge_id: str,
        nonce: str,
        user_confirmed: bool,
        confirmation_signature: str,
        expected_pin_or_biometric: str = "BIOMETRIC_OK",
        now: datetime.datetime | None = None,
    ) -> dict[str, Any]:
        """Steps 4–7: Require explicit confirmation, execute via adapter, verify result, and audit."""
        current = now or datetime.datetime.now(datetime.timezone.utc)
        if current.tzinfo is None:
            current = current.replace(tzinfo=datetime.timezone.utc)

        if not user_confirmed:
            raise PermissionError("Walk-up proximity alone cannot unlock vehicle: explicit user confirmation required.")

        if nonce in self._used_nonces:
            raise PermissionError("Replay attack prevented: challenge nonce has already been used.")

        challenge = self._challenges.get(challenge_id)
        if challenge is None or challenge.used:
            raise PermissionError("Invalid or already consumed digital key challenge.")

        if challenge.nonce != nonce:
            raise PermissionError("Challenge nonce mismatch.")

        if current > challenge.expires_at:
            raise PermissionError("Digital key challenge has expired.")

        if challenge.digital_key_id in self._revoked_keys:
            raise PermissionError("Digital key was revoked before execution.")

        if not self._check_rate_limit(challenge.customer_id, current):
            raise PermissionError("Rate limit exceeded for digital key operations.")

        expected_sig = self.sign_user_confirmation(challenge, expected_pin_or_biometric)
        if not hmac.compare_digest(confirmation_signature, expected_sig):
            raise PermissionError("Invalid device biometric/passcode confirmation signature.")

        # Mark single-use nonce as consumed before adapter call
        challenge.used = True
        self._used_nonces.add(nonce)

        success, adapter_message = self.adapter.execute_vehicle_command(
            vehicle_id=challenge.vehicle_id,
            digital_key_id=challenge.digital_key_id,
            command=challenge.requested_action,
        )

        outcome = {
            "challenge_id": challenge.challenge_id,
            "vehicle_id": challenge.vehicle_id,
            "customer_id": challenge.customer_id,
            "digital_key_id": challenge.digital_key_id,
            "action": challenge.requested_action,
            "verified_success": success,
            "message": adapter_message,
            "is_simulated": True,
            "timestamp": current.isoformat(),
        }
        self.db.record_audit(
            actor_id=challenge.customer_id,
            actor_role="CUSTOMER_DIGITAL_KEY",
            action=f"DIGITAL_KEY_{challenge.requested_action}",
            target_entity=challenge.vehicle_id,
            details=outcome,
            timestamp=current,
        )
        return outcome

    def revoke_digital_key(
        self,
        digital_key_id: str,
        actor_id: str,
        actor_role: StaffRole | str = StaffRole.SECURITY_OFFICER,
    ) -> None:
        self._revoked_keys.add(digital_key_id)
        self.db.record_audit(
            actor_id=actor_id,
            actor_role=actor_role,
            action="REVOKE_DIGITAL_KEY",
            target_entity=digital_key_id,
            details={"revoked": True},
        )
