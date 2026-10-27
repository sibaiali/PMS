"""Barrier-Free Arrival and Departure Authorization Engine with Fail-Safe Controls (Section 4)."""

from __future__ import annotations

import datetime
import uuid
from dataclasses import dataclass
from typing import Any

from smartpark.billing.engine import BillingEngine
from smartpark.config.settings import SmartParkConfig
from smartpark.core.allocation import AllocationComparisonResult
from smartpark.core.engine import ParkingEngine, hash_license_plate, normalize_license_plate
from smartpark.core.models import (
    AccessAuthorizationRecord,
    AccessDecisionStatus,
    AccessDirection,
    AccountStatus,
    CredentialType,
    ParkingSession,
    PaymentMethodType,
    PaymentRecord,
    PaymentStatus,
    StaffRole,
)
from smartpark.database.db import DatabaseUnavailableError
from smartpark.security.tokens import PrivacyBadgeCredential, PrivacyBadgeManager


@dataclass
class ArrivalDecisionResult:
    """Comprehensive result of a barrier-free arrival evaluation."""

    record: AccessAuthorizationRecord
    session: ParkingSession | None = None
    allocation: AllocationComparisonResult | None = None
    badge: PrivacyBadgeCredential | None = None
    guidance_message: str = ""


@dataclass
class DepartureDecisionResult:
    """Comprehensive result of a barrier-free departure evaluation."""

    record: AccessAuthorizationRecord
    session: ParkingSession | None = None
    payment: PaymentRecord | None = None
    receipt_text: str | None = None
    egress_message: str = ""


class BarrierFreeAccessEngine:
    """Manages digital arrival and departure authorization with explicit fail-safe behavior."""

    def __init__(
        self,
        config: SmartParkConfig,
        parking_engine: ParkingEngine,
        billing_engine: BillingEngine,
        badge_manager: PrivacyBadgeManager,
    ) -> None:
        self.config = config
        self.parking = parking_engine
        self.billing = billing_engine
        self.badges = badge_manager
        self.network_outage: bool = False
        self.sensor_failure: bool = False
        self._recent_events: dict[str, datetime.datetime] = {}

    def _is_duplicate_event(
        self,
        key: str,
        now: datetime.datetime,
    ) -> bool:
        window = datetime.timedelta(seconds=self.config.duplicate_recognition_window_seconds)
        prev = self._recent_events.get(key)
        if prev is not None and (now - prev) < window:
            return True
        self._recent_events[key] = now
        return False

    def evaluate_arrival(
        self,
        license_plate: str | None = None,
        public_vehicle_token: str | None = None,
        credential_type: CredentialType = CredentialType.ENROLLED_LPR_SIM,
        secondary_credential_verified: bool = True,
        confidence: float = 0.96,
        emergency_vehicle: bool = False,
        at_time: datetime.datetime | None = None,
    ) -> ArrivalDecisionResult:
        """Evaluate barrier-free vehicle arrival with strict fail-safe and ownership checks."""
        now = at_time or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)

        # 1. Emergency vehicle priority access (never blocked)
        if emergency_vehicle:
            rec = AccessAuthorizationRecord(
                decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                direction=AccessDirection.ENTRY,
                decision_status=AccessDecisionStatus.EMERGENCY_OVERRIDE,
                reason="Emergency vehicle priority entry granted; emergency lane E-99 reserved.",
                alternative_workflow="Clear emergency route immediately.",
                vehicle_id=None,
                customer_id=None,
                session_id=None,
                assigned_space_id=16,
                credential_type=CredentialType.STAFF_OVERRIDE,
                confidence=1.0,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            try:
                self.parking.db.save_access_decision(rec)
            except DatabaseUnavailableError:
                pass
            return ArrivalDecisionResult(
                record=rec,
                guidance_message="EMERGENCY ACCESS GRANTED: Proceed via protected emergency lane to Bay E-99.",
            )

        # 2. Sensor failure check
        if self.sensor_failure:
            rec = AccessAuthorizationRecord(
                decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                direction=AccessDirection.ENTRY,
                decision_status=AccessDecisionStatus.MANUAL_VERIFICATION_REQUIRED,
                reason="Entrance optical/telemetry sensor degraded or offline.",
                alternative_workflow="Use SmartPark App QR check-in or contact staff via entrance intercom.",
                vehicle_id=None,
                customer_id=None,
                session_id=None,
                assigned_space_id=None,
                credential_type=credential_type,
                confidence=0.0,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            try:
                self.parking.db.save_access_decision(rec)
            except DatabaseUnavailableError:
                pass
            return ArrivalDecisionResult(
                record=rec,
                guidance_message="SENSOR FAULT: Automatic entry paused. Please scan your App QR badge or request staff assistance.",
            )

        # 3. Network or Database outage fail-safe: NEVER automatically open a secure entrance on server/DB outage
        if self.network_outage or self.parking.db.is_outage_simulated:
            outage_type = "Network" if self.network_outage else "Database"
            rec = AccessAuthorizationRecord(
                decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                direction=AccessDirection.ENTRY,
                decision_status=AccessDecisionStatus.FAILSAFE_HOLD,
                reason=f"{outage_type} outage detected: entrance held closed per fail-secure entry policy.",
                alternative_workflow="Present offline-verifiable signed QR badge or request staff manual override.",
                vehicle_id=None,
                customer_id=None,
                session_id=None,
                assigned_space_id=None,
                credential_type=credential_type,
                confidence=confidence,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            try:
                self.parking.db.save_access_decision(rec)
            except DatabaseUnavailableError:
                pass
            return ArrivalDecisionResult(
                record=rec,
                guidance_message=f"{outage_type.upper()} FAIL-SAFE HOLD: Entrance is not auto-opened during outage. Use offline QR or staff override.",
            )

        # 4. Duplicate recognition suppression
        dedup_id = public_vehicle_token or (license_plate.strip().upper() if license_plate else "UNKNOWN")
        if self._is_duplicate_event(f"ENTRY:{dedup_id}", now):
            rec = AccessAuthorizationRecord(
                decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                direction=AccessDirection.ENTRY,
                decision_status=AccessDecisionStatus.DUPLICATE_SUPPRESSED,
                reason=f"Duplicate recognition event for '{dedup_id}' within {self.config.duplicate_recognition_window_seconds}s window.",
                alternative_workflow="Ignored duplicate camera/beacon frame.",
                vehicle_id=None,
                customer_id=None,
                session_id=None,
                assigned_space_id=None,
                credential_type=credential_type,
                confidence=confidence,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            try:
                self.parking.db.save_access_decision(rec)
            except DatabaseUnavailableError:
                pass
            return ArrivalDecisionResult(
                record=rec,
                guidance_message="Duplicate recognition frame suppressed.",
            )

        # 5. Lookup enrolled vehicle & customer (handling database failure gracefully)
        try:
            vehicle = None
            if public_vehicle_token:
                vehicle = self.parking.db.get_vehicle_by_public_token(public_vehicle_token)
            elif license_plate:
                normalized = normalize_license_plate(license_plate)
                plate_hash = hash_license_plate(normalized)
                vehicle = self.parking.db.get_vehicle_by_plate_hash(plate_hash)

            if vehicle is None:
                rec = AccessAuthorizationRecord(
                    decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                    direction=AccessDirection.ENTRY,
                    decision_status=AccessDecisionStatus.DENIED,
                    reason="Unrecognized vehicle: no enrolled authorization relationship found.",
                    alternative_workflow="Direct to courtesy pull-aside bay for instant app enrollment or staff assistance.",
                    vehicle_id=None,
                    customer_id=None,
                    session_id=None,
                    assigned_space_id=None,
                    credential_type=credential_type,
                    confidence=confidence,
                    is_simulated=self.config.simulation_mode,
                    timestamp=now,
                )
                self.parking.db.save_access_decision(rec)
                return ArrivalDecisionResult(
                    record=rec,
                    guidance_message="ACCESS DENIED: Vehicle not enrolled. Please pull into the courtesy bay to register via SmartPark App.",
                )

            customer = self.parking.db.get_customer(vehicle.customer_id)
            if customer is None or customer.status != AccountStatus.ACTIVE:
                rec = AccessAuthorizationRecord(
                    decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                    direction=AccessDirection.ENTRY,
                    decision_status=AccessDecisionStatus.DENIED,
                    reason="Associated customer account is suspended or inactive.",
                    alternative_workflow="Contact customer support or settle outstanding account balance in the app.",
                    vehicle_id=vehicle.vehicle_id,
                    customer_id=vehicle.customer_id,
                    session_id=None,
                    assigned_space_id=None,
                    credential_type=credential_type,
                    confidence=confidence,
                    is_simulated=self.config.simulation_mode,
                    timestamp=now,
                )
                self.parking.db.save_access_decision(rec)
                return ArrivalDecisionResult(
                    record=rec,
                    guidance_message="ACCESS DENIED: Account inactive. Please check your SmartPark App status.",
                )

            # 6. Never treat a detected license plate alone as proof of ownership!
            # Require either an active reservation window OR a verified secondary credential
            # (e.g. BLE digital key handshake, RFID tag, or app authorization) AND confidence >= 0.85.
            active_reservations = [
                r
                for r in self.parking.db.list_reservations(active_only=True)
                if r.vehicle_id == vehicle.vehicle_id
                and (r.start_time - datetime.timedelta(minutes=30)) <= now <= r.end_time
            ]
            matched_reservation = active_reservations[0] if active_reservations else None

            if confidence < 0.85 or (
                credential_type == CredentialType.ENROLLED_LPR_SIM
                and not secondary_credential_verified
                and matched_reservation is None
            ):
                rec = AccessAuthorizationRecord(
                    decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                    direction=AccessDirection.ENTRY,
                    decision_status=AccessDecisionStatus.MANUAL_VERIFICATION_REQUIRED,
                    reason=(
                        "Detected license plate alone is not treated as proof of ownership "
                        f"(confidence={confidence:.2f}, secondary_credential={secondary_credential_verified})."
                    ),
                    alternative_workflow="Confirm arrival in SmartPark App (Signed Badge / BLE Key) or via staff console.",
                    vehicle_id=vehicle.vehicle_id,
                    customer_id=customer.customer_id,
                    session_id=None,
                    assigned_space_id=None,
                    credential_type=credential_type,
                    confidence=confidence,
                    is_simulated=self.config.simulation_mode,
                    timestamp=now,
                )
                self.parking.db.save_access_decision(rec)
                return ArrivalDecisionResult(
                    record=rec,
                    guidance_message="VERIFICATION REQUIRED: Plate reading requires App/BLE confirmation or active reservation.",
                )

            # 7. Allocate space and create active session
            try:
                session, comparison = self.parking.allocate_space_and_start_session(
                    customer_id=customer.customer_id,
                    vehicle_id=vehicle.vehicle_id,
                    entry_time=now,
                    reservation_id=matched_reservation.reservation_id if matched_reservation else None,
                )
            except RuntimeError as exc:
                if "FACILITY_FULL" in str(exc):
                    rec = AccessAuthorizationRecord(
                        decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                        direction=AccessDirection.ENTRY,
                        decision_status=AccessDecisionStatus.DENIED,
                        reason="Parking facility has no compatible available spaces.",
                        alternative_workflow="Display digital 'GARAGE FULL' sign and route to partner garage.",
                        vehicle_id=vehicle.vehicle_id,
                        customer_id=customer.customer_id,
                        session_id=None,
                        assigned_space_id=None,
                        credential_type=credential_type,
                        confidence=confidence,
                        is_simulated=self.config.simulation_mode,
                        timestamp=now,
                    )
                    self.parking.db.save_access_decision(rec)
                    return ArrivalDecisionResult(
                        record=rec,
                        guidance_message="FACILITY FULL: No compatible parking bay available at this time.",
                    )
                raise

            space = self.parking.db.get_space(session.space_id)
            space_code = space.code if space else f"SP-{session.space_id}"
            badge = self.badges.issue_badge(
                session=session,
                vehicle=vehicle,
                space_code=space_code,
                now=now,
            )

            rec = AccessAuthorizationRecord(
                decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                direction=AccessDirection.ENTRY,
                decision_status=AccessDecisionStatus.GRANTED,
                reason=f"Authorized barrier-free entry for {vehicle.public_vehicle_token}; allocated {space_code}.",
                alternative_workflow=None,
                vehicle_id=vehicle.vehicle_id,
                customer_id=customer.customer_id,
                session_id=session.session_id,
                assigned_space_id=session.space_id,
                credential_type=credential_type,
                confidence=confidence,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            self.parking.db.save_access_decision(rec)
            return ArrivalDecisionResult(
                record=rec,
                session=session,
                allocation=comparison,
                badge=badge,
                guidance_message=(
                    f"BARRIER-FREE ENTRY GRANTED: Welcome {customer.public_alias}. "
                    f"Proceed smoothly to Space {space_code} (Zone {space.zone if space else 'A'})."
                ),
            )

        except DatabaseUnavailableError:
            # Fail-safe on DB failure: do NOT open entrance blindly; never trap anyone
            rec = AccessAuthorizationRecord(
                decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                direction=AccessDirection.ENTRY,
                decision_status=AccessDecisionStatus.FAILSAFE_HOLD,
                reason="Database unavailable during entry evaluation; entrance held in fail-secure state.",
                alternative_workflow="Wait for staff manual verification or use emergency egress lane to exit.",
                vehicle_id=None,
                customer_id=None,
                session_id=None,
                assigned_space_id=None,
                credential_type=credential_type,
                confidence=confidence,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            return ArrivalDecisionResult(
                record=rec,
                guidance_message="DATABASE FAIL-SAFE HOLD: Entry paused safely. Staff notified.",
            )

    def evaluate_departure(
        self,
        session_id: str | None = None,
        license_plate: str | None = None,
        public_vehicle_token: str | None = None,
        payment_method: PaymentMethodType | None = None,
        emergency_egress: bool = False,
        at_time: datetime.datetime | None = None,
    ) -> DepartureDecisionResult:
        """Evaluate barrier-free vehicle departure; never trap a vehicle or person on outage."""
        now = at_time or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)

        if emergency_egress or self.network_outage:
            reason = (
                "Emergency egress override active — unconditional safe exit."
                if emergency_egress
                else "Network outage during exit: safe egress granted with deferred billing reconciliation (anti-entrapment policy)."
            )
            rec = AccessAuthorizationRecord(
                decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                direction=AccessDirection.EXIT,
                decision_status=AccessDecisionStatus.EMERGENCY_OVERRIDE,
                reason=reason,
                alternative_workflow="Audit log recorded for deferred billing reconciliation.",
                vehicle_id=None,
                customer_id=None,
                session_id=session_id,
                assigned_space_id=None,
                credential_type=CredentialType.STAFF_OVERRIDE,
                confidence=1.0,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            try:
                self.parking.db.save_access_decision(rec)
            except DatabaseUnavailableError:
                pass
            return DepartureDecisionResult(
                record=rec,
                egress_message="SAFE EGRESS AUTHORIZED: Vehicles and persons are never trapped during outages or emergencies.",
            )

        try:
            session: ParkingSession | None = None
            if session_id:
                session = self.parking.db.get_session(session_id)
            elif public_vehicle_token:
                veh = self.parking.db.get_vehicle_by_public_token(public_vehicle_token)
                if veh:
                    session = self.parking.db.get_active_session_for_vehicle(veh.vehicle_id)
            elif license_plate:
                plate_hash = hash_license_plate(normalize_license_plate(license_plate))
                veh = self.parking.db.get_vehicle_by_plate_hash(plate_hash)
                if veh:
                    session = self.parking.db.get_active_session_for_vehicle(veh.vehicle_id)

            if session is None:
                rec = AccessAuthorizationRecord(
                    decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                    direction=AccessDirection.EXIT,
                    decision_status=AccessDecisionStatus.MANUAL_VERIFICATION_REQUIRED,
                    reason="No active parking session found for departing vehicle.",
                    alternative_workflow="Scan digital badge at exit kiosk or allow staff-audited safe exit.",
                    vehicle_id=None,
                    customer_id=None,
                    session_id=None,
                    assigned_space_id=None,
                    credential_type=CredentialType.ENROLLED_LPR_SIM,
                    confidence=0.80,
                    is_simulated=self.config.simulation_mode,
                    timestamp=now,
                )
                self.parking.db.save_access_decision(rec)
                return DepartureDecisionResult(
                    record=rec,
                    egress_message="EXIT VERIFICATION: Session not matched automatically; present App Badge or use exit terminal.",
                )

            payment = self.billing.process_session_payment(
                session=session,
                exit_time=now,
                method=payment_method,
            )

            # Even if payment provider is in outage (status == PENDING), we release the vehicle
            # safely and record deferred invoice settlement so the exit is never blocked!
            if payment.status in (PaymentStatus.SUCCEEDED, PaymentStatus.PENDING):
                completed_session = self.parking.release_space_for_session(
                    session_id=session.session_id,
                    exit_time=now,
                )
                # Revoke/hide temporary privacy badge automatically upon departure
                self.badges.revoke_badge_token(
                    completed_session.session_id,
                    actor_id="DEPARTURE_ENGINE",
                    actor_role=StaffRole.OPERATOR,
                )
                receipt_text = self.billing.generate_receipt_text(payment)
                reason = (
                    f"Barrier-free departure authorized. Fee {payment.amount} {payment.currency} ({payment.status.value})."
                )
                rec = AccessAuthorizationRecord(
                    decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                    direction=AccessDirection.EXIT,
                    decision_status=AccessDecisionStatus.GRANTED,
                    reason=reason,
                    alternative_workflow=(
                        "Deferred invoice settlement scheduled."
                        if payment.status == PaymentStatus.PENDING
                        else None
                    ),
                    vehicle_id=completed_session.vehicle_id,
                    customer_id=completed_session.customer_id,
                    session_id=completed_session.session_id,
                    assigned_space_id=completed_session.space_id,
                    credential_type=CredentialType.ENROLLED_LPR_SIM,
                    confidence=0.98,
                    is_simulated=self.config.simulation_mode,
                    timestamp=now,
                )
                self.parking.db.save_access_decision(rec)
                return DepartureDecisionResult(
                    record=rec,
                    session=completed_session,
                    payment=payment,
                    receipt_text=receipt_text,
                    egress_message=f"BARRIER-FREE DEPARTURE GRANTED: Safe travels! Receipt {payment.invoice_number} issued.",
                )

            # Payment explicitly declined: offer exit terminal / secondary method without trapping
            rec = AccessAuthorizationRecord(
                decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                direction=AccessDirection.EXIT,
                decision_status=AccessDecisionStatus.MANUAL_VERIFICATION_REQUIRED,
                reason=f"Payment declined ({payment.amount} {payment.currency}); alternative payment or deferred invoice required.",
                alternative_workflow="Tap secondary payment method in App, use exit terminal, or sign deferred invoice for safe egress.",
                vehicle_id=session.vehicle_id,
                customer_id=session.customer_id,
                session_id=session.session_id,
                assigned_space_id=session.space_id,
                credential_type=CredentialType.ENROLLED_LPR_SIM,
                confidence=0.98,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            self.parking.db.save_access_decision(rec)
            return DepartureDecisionResult(
                record=rec,
                session=session,
                payment=payment,
                receipt_text=self.billing.generate_receipt_text(payment),
                egress_message="PAYMENT ACTION REQUIRED: Primary payment declined. Use secondary method or deferred invoice at exit.",
            )

        except DatabaseUnavailableError:
            # Never trap a vehicle on database failure!
            rec = AccessAuthorizationRecord(
                decision_id=f"ACC-{uuid.uuid4().hex[:10].upper()}",
                direction=AccessDirection.EXIT,
                decision_status=AccessDecisionStatus.EMERGENCY_OVERRIDE,
                reason="Database unavailable during departure: safe egress authorized to prevent entrapment.",
                alternative_workflow="Reconcile session logs when database recovers.",
                vehicle_id=None,
                customer_id=None,
                session_id=session_id,
                assigned_space_id=None,
                credential_type=CredentialType.STAFF_OVERRIDE,
                confidence=1.0,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            return DepartureDecisionResult(
                record=rec,
                egress_message="DATABASE FAIL-SAFE EGRESS: Safe exit granted immediately; billing reconciled post-recovery.",
            )
