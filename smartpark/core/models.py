# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Domain entities, value objects, and enumerations for SmartPark Guardian AI."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class AccountStatus(str, Enum):
    ACTIVE = "ACTIVE"
    SUSPENDED = "SUSPENDED"
    DELETED = "DELETED"


class StaffRole(str, Enum):
    VIEWER = "VIEWER"
    OPERATOR = "OPERATOR"
    SECURITY_OFFICER = "SECURITY_OFFICER"
    ADMIN = "ADMIN"


class VehicleSize(str, Enum):
    COMPACT = "COMPACT"
    STANDARD = "STANDARD"
    LARGE = "LARGE"
    SUV = "SUV"

    @property
    def rank(self) -> int:
        return {
            VehicleSize.COMPACT: 1,
            VehicleSize.STANDARD: 2,
            VehicleSize.LARGE: 3,
            VehicleSize.SUV: 3,
        }[self]


class SpaceType(str, Enum):
    STANDARD = "STANDARD"
    COMPACT = "COMPACT"
    LARGE = "LARGE"
    ACCESSIBLE = "ACCESSIBLE"
    EV_CHARGING = "EV_CHARGING"
    EMERGENCY_RESERVED = "EMERGENCY_RESERVED"


class SpaceStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    OCCUPIED = "OCCUPIED"
    RESERVED = "RESERVED"
    OUT_OF_SERVICE = "OUT_OF_SERVICE"
    BLOCKED = "BLOCKED"


class SessionStatus(str, Enum):
    PENDING_ARRIVAL = "PENDING_ARRIVAL"
    ACTIVE = "ACTIVE"
    CHECKOUT_PENDING = "CHECKOUT_PENDING"
    COMPLETED = "COMPLETED"
    EXCEPTION = "EXCEPTION"
    CANCELLED = "CANCELLED"


class AccessDirection(str, Enum):
    ENTRY = "ENTRY"
    EXIT = "EXIT"


class AccessDecisionStatus(str, Enum):
    GRANTED = "GRANTED"
    DENIED = "DENIED"
    MANUAL_VERIFICATION_REQUIRED = "MANUAL_VERIFICATION_REQUIRED"
    EMERGENCY_OVERRIDE = "EMERGENCY_OVERRIDE"
    FAILSAFE_HOLD = "FAILSAFE_HOLD"
    DUPLICATE_SUPPRESSED = "DUPLICATE_SUPPRESSED"


class CredentialType(str, Enum):
    ENROLLED_LPR_SIM = "ENROLLED_LPR_SIM"
    BLE_DIGITAL_KEY_SIM = "BLE_DIGITAL_KEY_SIM"
    SIGNED_QR_BADGE = "SIGNED_QR_BADGE"
    RFID_CREDENTIAL_SIM = "RFID_CREDENTIAL_SIM"
    STAFF_OVERRIDE = "STAFF_OVERRIDE"


class PaymentStatus(str, Enum):
    PENDING = "PENDING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    REFUNDED = "REFUNDED"
    CANCELLED = "CANCELLED"
    WAIVED = "WAIVED"


class PaymentMethodType(str, Enum):
    PREPAID_WALLET = "PREPAID_WALLET"
    SUBSCRIPTION = "SUBSCRIPTION"
    VALIDATED_VISIT = "VALIDATED_VISIT"
    SAVED_TOKEN_SIM = "SAVED_TOKEN_SIM"
    EXIT_TERMINAL_SIM = "EXIT_TERMINAL_SIM"


class SeverityLevel(str, Enum):
    INFORMATIONAL = "INFORMATIONAL"
    ATTENTION_REQUIRED = "ATTENTION_REQUIRED"
    HIGH_PRIORITY = "HIGH_PRIORITY"


class SensorEventType(str, Enum):
    DOOR_OPEN = "DOOR_OPEN"
    DOOR_LOCK = "DOOR_LOCK"
    DOOR_UNLOCK = "DOOR_UNLOCK"
    VIBRATION_MINOR = "VIBRATION_MINOR"
    IMPACT_HEAVY = "IMPACT_HEAVY"
    VEHICLE_MOVEMENT = "VEHICLE_MOVEMENT"
    GLASS_TAMPER = "GLASS_TAMPER"
    AUTHORIZED_KEY_UNLOCK = "AUTHORIZED_KEY_UNLOCK"
    SESSION_STATUS_CHANGE = "SESSION_STATUS_CHANGE"
    PEDESTRIAN_PROXIMITY = "PEDESTRIAN_PROXIMITY"
    ALARM_TRIGGERED = "ALARM_TRIGGERED"
    SENSOR_HEARTBEAT = "SENSOR_HEARTBEAT"
    SENSOR_OFFLINE = "SENSOR_OFFLINE"


@dataclass
class Customer:
    """Registered customer with privacy-preserving separation from public vehicle tokens."""

    customer_id: str
    public_alias: str
    name: str
    email: str
    phone: str
    status: AccountStatus = AccountStatus.ACTIVE
    requires_accessible: bool = False
    has_subscription: bool = False
    prepaid_balance: Decimal = Decimal("0.00")
    quiet_hours_start: int | None = None  # Hour 0-23
    quiet_hours_end: int | None = None    # Hour 0-23
    trusted_contact: str | None = None
    created_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@dataclass
class Vehicle:
    """Customer vehicle separated from personal identity by `public_vehicle_token`."""

    vehicle_id: str
    customer_id: str
    public_vehicle_token: str
    license_plate_hash: str
    license_plate_masked: str
    license_plate_raw: str  # Kept only in protected domain layer; never shown on public badges
    size: VehicleSize = VehicleSize.STANDARD
    requires_ev_charging: bool = False
    enrolled_digital_key_id: str | None = None
    enrolled_rfid_tag: str | None = None
    created_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@dataclass
class ParkingSpace:
    """Physical or simulated parking space with spatial coordinates and capabilities."""

    space_id: int
    code: str
    zone: str
    grid_x: int
    grid_y: int
    space_type: SpaceType = SpaceType.STANDARD
    max_size: VehicleSize = VehicleSize.STANDARD
    has_charger: bool = False
    is_accessible: bool = False
    walking_distance_m: float = 20.0
    exit_distance_m: float = 30.0
    near_blind_corner: bool = False
    status: SpaceStatus = SpaceStatus.AVAILABLE
    current_session_id: str | None = None


@dataclass
class Reservation:
    """Pre-booked parking authorization window."""

    reservation_id: str
    customer_id: str
    vehicle_id: str
    space_id: int | None
    start_time: datetime.datetime
    end_time: datetime.datetime
    requires_accessible: bool = False
    requires_ev_charging: bool = False
    prepaid_authorized: bool = True
    status: str = "ACTIVE"
    created_at: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )

    def overlaps(self, start: datetime.datetime, end: datetime.datetime) -> bool:
        return start < self.end_time and end > self.start_time


@dataclass
class ParkingSession:
    """Active or completed barrier-free parking session."""

    session_id: str
    customer_id: str
    vehicle_id: str
    space_id: int
    public_badge_id: str
    entry_time: datetime.datetime
    exit_time: datetime.datetime | None = None
    reservation_id: str | None = None
    status: SessionStatus = SessionStatus.ACTIVE
    shield_armed: bool = True
    validated_visit: bool = False
    fee_amount: Decimal = Decimal("0.00")
    payment_status: PaymentStatus = PaymentStatus.PENDING
    is_simulated: bool = True


@dataclass
class AccessAuthorizationRecord:
    """Audited record of an entry or exit access decision."""

    decision_id: str
    direction: AccessDirection
    decision_status: AccessDecisionStatus
    reason: str
    alternative_workflow: str | None
    vehicle_id: str | None
    customer_id: str | None
    session_id: str | None
    assigned_space_id: int | None
    credential_type: CredentialType
    confidence: float
    is_simulated: bool
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@dataclass
class SensorEventRecord:
    """Telemetry or security event reported to Guardian Shield."""

    event_id: str
    session_id: str
    vehicle_id: str
    event_type: SensorEventType
    source_authenticated: bool
    sensor_health_ok: bool
    payload: dict[str, Any]
    is_simulated: bool
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@dataclass
class SecurityIncidentRecord:
    """Explainable security assessment produced by Guardian Shield."""

    incident_id: str
    session_id: str
    vehicle_id: str
    severity: SeverityLevel
    classification: str  # "SENSOR_EVENT", "SUSPICIOUS_PATTERN", or "CONFIRMED_ALARM"
    triggered_rules: list[str]
    received_event_ids: list[str]
    source_authenticated: bool
    is_expected: bool
    confidence: float
    limitations_note: str
    recommended_response: str
    explanation: str
    resolved: bool = False
    is_simulated: bool = True
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@dataclass
class NotificationRecord:
    """Customer or staff notification with delivery state and rate-limit tracking."""

    notification_id: str
    customer_id: str
    session_id: str | None
    incident_id: str | None
    severity: SeverityLevel
    title: str
    message: str
    reason: str
    confidence_limitations: str
    safe_next_action: str
    delivery_status: str  # "DELIVERED_SIM", "SUPPRESSED_COOLDOWN", "QUEUED_QUIET_HOURS", "FAILED_PROVIDER"
    provider_name: str
    is_simulated: bool = True
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@dataclass
class PaymentRecord:
    """Audited financial transaction with idempotency protection."""

    transaction_id: str
    idempotency_key: str
    session_id: str
    customer_id: str
    amount: Decimal
    currency: str
    method: PaymentMethodType
    status: PaymentStatus
    invoice_number: str
    breakdown: dict[str, str]
    is_simulated: bool = True
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


@dataclass
class AuditLogRecord:
    """Immutable audit log entry for security, access, and administrative actions."""

    audit_id: str
    actor_id: str
    actor_role: str
    action: str
    target_entity: str
    details: dict[str, Any]
    is_simulated: bool = True
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )
