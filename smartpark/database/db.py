"""Persistent SQLite database engine with schema migrations, foreign keys, and fail-safe controls."""

from __future__ import annotations

import contextlib
import datetime
import json
import logging
import sqlite3
import threading
import uuid
from decimal import Decimal
from typing import Any, Iterator

from smartpark.core.models import (
    AccessAuthorizationRecord,
    AccessDecisionStatus,
    AccessDirection,
    AccountStatus,
    AuditLogRecord,
    CredentialType,
    Customer,
    NotificationRecord,
    ParkingSession,
    ParkingSpace,
    PaymentMethodType,
    PaymentRecord,
    PaymentStatus,
    Reservation,
    SecurityIncidentRecord,
    SensorEventRecord,
    SensorEventType,
    SessionStatus,
    SeverityLevel,
    SpaceStatus,
    SpaceType,
    StaffRole,
    Vehicle,
    VehicleSize,
)

logger = logging.getLogger(__name__)


class DatabaseUnavailableError(RuntimeError):
    """Raised when the database is unavailable or in simulated outage mode."""


MIGRATIONS: list[tuple[int, str, str]] = [
    (
        1,
        "initial_schema",
        """
        CREATE TABLE IF NOT EXISTS customers (
            customer_id TEXT PRIMARY KEY,
            public_alias TEXT NOT NULL UNIQUE,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE,
            phone TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            requires_accessible INTEGER NOT NULL DEFAULT 0,
            has_subscription INTEGER NOT NULL DEFAULT 0,
            prepaid_balance TEXT NOT NULL DEFAULT '0.00',
            quiet_hours_start INTEGER,
            quiet_hours_end INTEGER,
            trusted_contact TEXT,
            created_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS vehicles (
            vehicle_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            public_vehicle_token TEXT NOT NULL UNIQUE,
            license_plate_hash TEXT NOT NULL UNIQUE,
            license_plate_masked TEXT NOT NULL,
            license_plate_raw TEXT NOT NULL,
            size TEXT NOT NULL DEFAULT 'STANDARD',
            requires_ev_charging INTEGER NOT NULL DEFAULT 0,
            enrolled_digital_key_id TEXT,
            enrolled_rfid_tag TEXT,
            created_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_vehicles_customer ON vehicles(customer_id);
        CREATE INDEX IF NOT EXISTS idx_vehicles_plate_hash ON vehicles(license_plate_hash);

        CREATE TABLE IF NOT EXISTS parking_spaces (
            space_id INTEGER PRIMARY KEY,
            code TEXT NOT NULL UNIQUE,
            zone TEXT NOT NULL,
            grid_x INTEGER NOT NULL,
            grid_y INTEGER NOT NULL,
            space_type TEXT NOT NULL DEFAULT 'STANDARD',
            max_size TEXT NOT NULL DEFAULT 'STANDARD',
            has_charger INTEGER NOT NULL DEFAULT 0,
            is_accessible INTEGER NOT NULL DEFAULT 0,
            walking_distance_m REAL NOT NULL DEFAULT 20.0,
            exit_distance_m REAL NOT NULL DEFAULT 30.0,
            near_blind_corner INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'AVAILABLE',
            current_session_id TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_spaces_status ON parking_spaces(status);

        CREATE TABLE IF NOT EXISTS reservations (
            reservation_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            vehicle_id TEXT NOT NULL,
            space_id INTEGER,
            start_time TEXT NOT NULL,
            end_time TEXT NOT NULL,
            requires_accessible INTEGER NOT NULL DEFAULT 0,
            requires_ev_charging INTEGER NOT NULL DEFAULT 0,
            prepaid_authorized INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            created_at TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id) ON DELETE CASCADE,
            FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id) ON DELETE CASCADE,
            FOREIGN KEY (space_id) REFERENCES parking_spaces(space_id)
        );
        CREATE INDEX IF NOT EXISTS idx_reservations_space_window
            ON reservations(space_id, status, start_time, end_time);

        CREATE TABLE IF NOT EXISTS parking_sessions (
            session_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            vehicle_id TEXT NOT NULL,
            space_id INTEGER NOT NULL,
            public_badge_id TEXT NOT NULL,
            entry_time TEXT NOT NULL,
            exit_time TEXT,
            reservation_id TEXT,
            status TEXT NOT NULL DEFAULT 'ACTIVE',
            shield_armed INTEGER NOT NULL DEFAULT 1,
            validated_visit INTEGER NOT NULL DEFAULT 0,
            fee_amount TEXT NOT NULL DEFAULT '0.00',
            payment_status TEXT NOT NULL DEFAULT 'PENDING',
            is_simulated INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id),
            FOREIGN KEY (vehicle_id) REFERENCES vehicles(vehicle_id),
            FOREIGN KEY (space_id) REFERENCES parking_spaces(space_id)
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_vehicle_status
            ON parking_sessions(vehicle_id, status);

        CREATE TABLE IF NOT EXISTS access_authorizations (
            decision_id TEXT PRIMARY KEY,
            direction TEXT NOT NULL,
            decision_status TEXT NOT NULL,
            reason TEXT NOT NULL,
            alternative_workflow TEXT,
            vehicle_id TEXT,
            customer_id TEXT,
            session_id TEXT,
            assigned_space_id INTEGER,
            credential_type TEXT NOT NULL,
            confidence REAL NOT NULL,
            is_simulated INTEGER NOT NULL DEFAULT 1,
            timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_access_timestamp ON access_authorizations(timestamp);

        CREATE TABLE IF NOT EXISTS sensor_events (
            event_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            vehicle_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            source_authenticated INTEGER NOT NULL,
            sensor_health_ok INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            is_simulated INTEGER NOT NULL DEFAULT 1,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES parking_sessions(session_id)
        );
        CREATE INDEX IF NOT EXISTS idx_sensor_session_time ON sensor_events(session_id, timestamp);

        CREATE TABLE IF NOT EXISTS security_incidents (
            incident_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            vehicle_id TEXT NOT NULL,
            severity TEXT NOT NULL,
            classification TEXT NOT NULL,
            triggered_rules_json TEXT NOT NULL,
            received_event_ids_json TEXT NOT NULL,
            source_authenticated INTEGER NOT NULL,
            is_expected INTEGER NOT NULL,
            confidence REAL NOT NULL,
            limitations_note TEXT NOT NULL,
            recommended_response TEXT NOT NULL,
            explanation TEXT NOT NULL,
            resolved INTEGER NOT NULL DEFAULT 0,
            is_simulated INTEGER NOT NULL DEFAULT 1,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES parking_sessions(session_id)
        );

        CREATE TABLE IF NOT EXISTS notifications (
            notification_id TEXT PRIMARY KEY,
            customer_id TEXT NOT NULL,
            session_id TEXT,
            incident_id TEXT,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            message TEXT NOT NULL,
            reason TEXT NOT NULL,
            confidence_limitations TEXT NOT NULL,
            safe_next_action TEXT NOT NULL,
            delivery_status TEXT NOT NULL,
            provider_name TEXT NOT NULL,
            is_simulated INTEGER NOT NULL DEFAULT 1,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );
        CREATE INDEX IF NOT EXISTS idx_notifications_customer_time
            ON notifications(customer_id, timestamp);

        CREATE TABLE IF NOT EXISTS payments (
            transaction_id TEXT PRIMARY KEY,
            idempotency_key TEXT NOT NULL UNIQUE,
            session_id TEXT NOT NULL,
            customer_id TEXT NOT NULL,
            amount TEXT NOT NULL,
            currency TEXT NOT NULL,
            method TEXT NOT NULL,
            status TEXT NOT NULL,
            invoice_number TEXT NOT NULL,
            breakdown_json TEXT NOT NULL,
            is_simulated INTEGER NOT NULL DEFAULT 1,
            timestamp TEXT NOT NULL,
            FOREIGN KEY (session_id) REFERENCES parking_sessions(session_id),
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );
        CREATE INDEX IF NOT EXISTS idx_payments_session ON payments(session_id);

        CREATE TABLE IF NOT EXISTS audit_logs (
            audit_id TEXT PRIMARY KEY,
            actor_id TEXT NOT NULL,
            actor_role TEXT NOT NULL,
            action TEXT NOT NULL,
            target_entity TEXT NOT NULL,
            details_json TEXT NOT NULL,
            is_simulated INTEGER NOT NULL DEFAULT 1,
            timestamp TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_audit_timestamp ON audit_logs(timestamp);
        """,
    ),
]


def _dt_to_iso(dt: datetime.datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt.isoformat()


def _iso_to_dt(value: str | None) -> datetime.datetime | None:
    if not value:
        return None
    dt = datetime.datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.timezone.utc)
    return dt


class DatabaseManager:
    """Thread-safe SQLite database manager with migrations, transactions, and RBAC auditing."""

    def __init__(self, database_path: str = ":memory:") -> None:
        self.database_path = database_path
        self._lock = threading.RLock()
        self._simulated_outage = False
        self._conn = sqlite3.connect(
            database_path,
            check_same_thread=False,
            isolation_level=None,  # Autocommit; we manage explicit BEGIN IMMEDIATE / COMMIT
        )
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON;")
        if database_path != ":memory:":
            self._conn.execute("PRAGMA journal_mode = WAL;")
        self._in_transaction = False
        self.apply_migrations()

    def set_simulated_outage(self, outage: bool) -> None:
        """Simulate a database outage to test fail-safe access and billing behavior."""
        with self._lock:
            self._simulated_outage = outage

    @property
    def is_outage_simulated(self) -> bool:
        return self._simulated_outage

    def _check_available(self) -> None:
        if self._simulated_outage:
            raise DatabaseUnavailableError("Simulated database outage is active.")

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Execute a block inside an atomic SQLite transaction with thread & row serialization."""
        with self._lock:
            self._check_available()
            if self._in_transaction:
                # Nested transaction uses savepoint
                sp_name = f"sp_{uuid.uuid4().hex[:8]}"
                self._conn.execute(f"SAVEPOINT {sp_name};")
                try:
                    yield self._conn
                    self._conn.execute(f"RELEASE SAVEPOINT {sp_name};")
                except Exception:
                    self._conn.execute(f"ROLLBACK TO SAVEPOINT {sp_name};")
                    self._conn.execute(f"RELEASE SAVEPOINT {sp_name};")
                    raise
            else:
                if not self._conn.in_transaction:
                    self._conn.execute("BEGIN IMMEDIATE;")
                self._in_transaction = True
                try:
                    yield self._conn
                    if self._conn.in_transaction:
                        self._conn.execute("COMMIT;")
                except Exception:
                    if self._conn.in_transaction:
                        self._conn.execute("ROLLBACK;")
                    raise
                finally:
                    self._in_transaction = False

    def apply_migrations(self) -> None:
        with self.transaction() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    name TEXT NOT NULL,
                    applied_at TEXT NOT NULL
                );
                """
            )
            applied = {
                row["version"]
                for row in conn.execute("SELECT version FROM schema_migrations;").fetchall()
            }
            for version, name, sql in MIGRATIONS:
                if version not in applied:
                    for statement in sql.split(";"):
                        stmt = statement.strip()
                        if stmt:
                            conn.execute(stmt)
                    conn.execute(
                        "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?);",
                        (version, name, _dt_to_iso(datetime.datetime.now(datetime.timezone.utc))),
                    )
                    logger.info("Applied database migration v%d (%s)", version, name)

    # ------------------------------------------------------------------
    # Audit & RBAC Helpers
    # ------------------------------------------------------------------
    def record_audit(
        self,
        actor_id: str,
        actor_role: StaffRole | str,
        action: str,
        target_entity: str,
        details: dict[str, Any],
        is_simulated: bool = True,
        timestamp: datetime.datetime | None = None,
    ) -> AuditLogRecord:
        self._check_available()
        role_str = actor_role.value if isinstance(actor_role, StaffRole) else str(actor_role)
        record = AuditLogRecord(
            audit_id=f"AUD-{uuid.uuid4().hex[:10].upper()}",
            actor_id=actor_id,
            actor_role=role_str,
            action=action,
            target_entity=target_entity,
            details=details,
            is_simulated=is_simulated,
            timestamp=timestamp or datetime.datetime.now(datetime.timezone.utc),
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO audit_logs (
                    audit_id, actor_id, actor_role, action, target_entity,
                    details_json, is_simulated, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.audit_id,
                    record.actor_id,
                    record.actor_role,
                    record.action,
                    record.target_entity,
                    json.dumps(record.details, default=str),
                    int(record.is_simulated),
                    _dt_to_iso(record.timestamp),
                ),
            )
        return record

    def list_audit_logs(self, limit: int = 100) -> list[AuditLogRecord]:
        self._check_available()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit_logs ORDER BY timestamp DESC LIMIT ?;", (limit,)
            ).fetchall()
        return [
            AuditLogRecord(
                audit_id=r["audit_id"],
                actor_id=r["actor_id"],
                actor_role=r["actor_role"],
                action=r["action"],
                target_entity=r["target_entity"],
                details=json.loads(r["details_json"]),
                is_simulated=bool(r["is_simulated"]),
                timestamp=_iso_to_dt(r["timestamp"]) or datetime.datetime.now(datetime.timezone.utc),
            )
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Customer & Vehicle Repository Methods
    # ------------------------------------------------------------------
    def save_customer(self, customer: Customer) -> Customer:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO customers (
                    customer_id, public_alias, name, email, phone, status,
                    requires_accessible, has_subscription, prepaid_balance,
                    quiet_hours_start, quiet_hours_end, trusted_contact, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(customer_id) DO UPDATE SET
                    public_alias = excluded.public_alias,
                    name = excluded.name,
                    email = excluded.email,
                    phone = excluded.phone,
                    status = excluded.status,
                    requires_accessible = excluded.requires_accessible,
                    has_subscription = excluded.has_subscription,
                    prepaid_balance = excluded.prepaid_balance,
                    quiet_hours_start = excluded.quiet_hours_start,
                    quiet_hours_end = excluded.quiet_hours_end,
                    trusted_contact = excluded.trusted_contact;
                """,
                (
                    customer.customer_id,
                    customer.public_alias,
                    customer.name,
                    customer.email,
                    customer.phone,
                    customer.status.value,
                    int(customer.requires_accessible),
                    int(customer.has_subscription),
                    str(customer.prepaid_balance),
                    customer.quiet_hours_start,
                    customer.quiet_hours_end,
                    customer.trusted_contact,
                    _dt_to_iso(customer.created_at),
                ),
            )
        return customer

    def get_customer(self, customer_id: str) -> Customer | None:
        self._check_available()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM customers WHERE customer_id = ?;", (customer_id,)
            ).fetchone()
        return self._row_to_customer(row) if row else None

    def list_customers(self) -> list[Customer]:
        self._check_available()
        with self._lock:
            rows = self._conn.execute("SELECT * FROM customers ORDER BY created_at ASC;").fetchall()
        return [self._row_to_customer(r) for r in rows]

    def _row_to_customer(self, row: sqlite3.Row) -> Customer:
        return Customer(
            customer_id=row["customer_id"],
            public_alias=row["public_alias"],
            name=row["name"],
            email=row["email"],
            phone=row["phone"],
            status=AccountStatus(row["status"]),
            requires_accessible=bool(row["requires_accessible"]),
            has_subscription=bool(row["has_subscription"]),
            prepaid_balance=Decimal(row["prepaid_balance"]),
            quiet_hours_start=row["quiet_hours_start"],
            quiet_hours_end=row["quiet_hours_end"],
            trusted_contact=row["trusted_contact"],
            created_at=_iso_to_dt(row["created_at"]) or datetime.datetime.now(datetime.timezone.utc),
        )

    def save_vehicle(self, vehicle: Vehicle) -> Vehicle:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO vehicles (
                    vehicle_id, customer_id, public_vehicle_token, license_plate_hash,
                    license_plate_masked, license_plate_raw, size, requires_ev_charging,
                    enrolled_digital_key_id, enrolled_rfid_tag, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    vehicle.vehicle_id,
                    vehicle.customer_id,
                    vehicle.public_vehicle_token,
                    vehicle.license_plate_hash,
                    vehicle.license_plate_masked,
                    vehicle.license_plate_raw,
                    vehicle.size.value,
                    int(vehicle.requires_ev_charging),
                    vehicle.enrolled_digital_key_id,
                    vehicle.enrolled_rfid_tag,
                    _dt_to_iso(vehicle.created_at),
                ),
            )
        return vehicle

    def get_vehicle_by_id(self, vehicle_id: str) -> Vehicle | None:
        self._check_available()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM vehicles WHERE vehicle_id = ?;", (vehicle_id,)
            ).fetchone()
        return self._row_to_vehicle(row) if row else None

    def get_vehicle_by_plate_hash(self, plate_hash: str) -> Vehicle | None:
        self._check_available()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM vehicles WHERE license_plate_hash = ?;", (plate_hash,)
            ).fetchone()
        return self._row_to_vehicle(row) if row else None

    def get_vehicle_by_public_token(self, token: str) -> Vehicle | None:
        self._check_available()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM vehicles WHERE public_vehicle_token = ?;", (token,)
            ).fetchone()
        return self._row_to_vehicle(row) if row else None

    def list_vehicles(self, customer_id: str | None = None) -> list[Vehicle]:
        self._check_available()
        with self._lock:
            if customer_id:
                rows = self._conn.execute(
                    "SELECT * FROM vehicles WHERE customer_id = ? ORDER BY created_at ASC;",
                    (customer_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM vehicles ORDER BY created_at ASC;"
                ).fetchall()
        return [self._row_to_vehicle(r) for r in rows]

    def _row_to_vehicle(self, row: sqlite3.Row) -> Vehicle:
        return Vehicle(
            vehicle_id=row["vehicle_id"],
            customer_id=row["customer_id"],
            public_vehicle_token=row["public_vehicle_token"],
            license_plate_hash=row["license_plate_hash"],
            license_plate_masked=row["license_plate_masked"],
            license_plate_raw=row["license_plate_raw"],
            size=VehicleSize(row["size"]),
            requires_ev_charging=bool(row["requires_ev_charging"]),
            enrolled_digital_key_id=row["enrolled_digital_key_id"],
            enrolled_rfid_tag=row["enrolled_rfid_tag"],
            created_at=_iso_to_dt(row["created_at"]) or datetime.datetime.now(datetime.timezone.utc),
        )

    # ------------------------------------------------------------------
    # Parking Spaces & Reservations
    # ------------------------------------------------------------------
    def save_space(self, space: ParkingSpace) -> ParkingSpace:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO parking_spaces (
                    space_id, code, zone, grid_x, grid_y, space_type, max_size,
                    has_charger, is_accessible, walking_distance_m, exit_distance_m,
                    near_blind_corner, status, current_session_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(space_id) DO UPDATE SET
                    code = excluded.code,
                    zone = excluded.zone,
                    grid_x = excluded.grid_x,
                    grid_y = excluded.grid_y,
                    space_type = excluded.space_type,
                    max_size = excluded.max_size,
                    has_charger = excluded.has_charger,
                    is_accessible = excluded.is_accessible,
                    walking_distance_m = excluded.walking_distance_m,
                    exit_distance_m = excluded.exit_distance_m,
                    near_blind_corner = excluded.near_blind_corner,
                    status = excluded.status,
                    current_session_id = excluded.current_session_id;
                """,
                (
                    space.space_id,
                    space.code,
                    space.zone,
                    space.grid_x,
                    space.grid_y,
                    space.space_type.value,
                    space.max_size.value,
                    int(space.has_charger),
                    int(space.is_accessible),
                    space.walking_distance_m,
                    space.exit_distance_m,
                    int(space.near_blind_corner),
                    space.status.value,
                    space.current_session_id,
                ),
            )
        return space

    def get_space(self, space_id: int) -> ParkingSpace | None:
        self._check_available()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM parking_spaces WHERE space_id = ?;", (space_id,)
            ).fetchone()
        return self._row_to_space(row) if row else None

    def list_spaces(self) -> list[ParkingSpace]:
        self._check_available()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM parking_spaces ORDER BY space_id ASC;"
            ).fetchall()
        return [self._row_to_space(r) for r in rows]

    def _row_to_space(self, row: sqlite3.Row) -> ParkingSpace:
        return ParkingSpace(
            space_id=row["space_id"],
            code=row["code"],
            zone=row["zone"],
            grid_x=row["grid_x"],
            grid_y=row["grid_y"],
            space_type=SpaceType(row["space_type"]),
            max_size=VehicleSize(row["max_size"]),
            has_charger=bool(row["has_charger"]),
            is_accessible=bool(row["is_accessible"]),
            walking_distance_m=float(row["walking_distance_m"]),
            exit_distance_m=float(row["exit_distance_m"]),
            near_blind_corner=bool(row["near_blind_corner"]),
            status=SpaceStatus(row["status"]),
            current_session_id=row["current_session_id"],
        )

    def save_reservation(self, reservation: Reservation) -> Reservation:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO reservations (
                    reservation_id, customer_id, vehicle_id, space_id,
                    start_time, end_time, requires_accessible, requires_ev_charging,
                    prepaid_authorized, status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(reservation_id) DO UPDATE SET
                    space_id = excluded.space_id,
                    start_time = excluded.start_time,
                    end_time = excluded.end_time,
                    prepaid_authorized = excluded.prepaid_authorized,
                    status = excluded.status;
                """,
                (
                    reservation.reservation_id,
                    reservation.customer_id,
                    reservation.vehicle_id,
                    reservation.space_id,
                    _dt_to_iso(reservation.start_time),
                    _dt_to_iso(reservation.end_time),
                    int(reservation.requires_accessible),
                    int(reservation.requires_ev_charging),
                    int(reservation.prepaid_authorized),
                    reservation.status,
                    _dt_to_iso(reservation.created_at),
                ),
            )
        return reservation

    def list_reservations(self, active_only: bool = False) -> list[Reservation]:
        self._check_available()
        with self._lock:
            if active_only:
                rows = self._conn.execute(
                    "SELECT * FROM reservations WHERE status = 'ACTIVE' ORDER BY start_time ASC;"
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM reservations ORDER BY start_time ASC;"
                ).fetchall()
        return [self._row_to_reservation(r) for r in rows]

    def _row_to_reservation(self, row: sqlite3.Row) -> Reservation:
        return Reservation(
            reservation_id=row["reservation_id"],
            customer_id=row["customer_id"],
            vehicle_id=row["vehicle_id"],
            space_id=row["space_id"],
            start_time=_iso_to_dt(row["start_time"]) or datetime.datetime.now(datetime.timezone.utc),
            end_time=_iso_to_dt(row["end_time"]) or datetime.datetime.now(datetime.timezone.utc),
            requires_accessible=bool(row["requires_accessible"]),
            requires_ev_charging=bool(row["requires_ev_charging"]),
            prepaid_authorized=bool(row["prepaid_authorized"]),
            status=row["status"],
            created_at=_iso_to_dt(row["created_at"]) or datetime.datetime.now(datetime.timezone.utc),
        )

    # ------------------------------------------------------------------
    # Parking Sessions
    # ------------------------------------------------------------------
    def save_session(self, session: ParkingSession) -> ParkingSession:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO parking_sessions (
                    session_id, customer_id, vehicle_id, space_id, public_badge_id,
                    entry_time, exit_time, reservation_id, status, shield_armed,
                    validated_visit, fee_amount, payment_status, is_simulated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    space_id = excluded.space_id,
                    public_badge_id = excluded.public_badge_id,
                    entry_time = excluded.entry_time,
                    exit_time = excluded.exit_time,
                    reservation_id = excluded.reservation_id,
                    status = excluded.status,
                    shield_armed = excluded.shield_armed,
                    validated_visit = excluded.validated_visit,
                    fee_amount = excluded.fee_amount,
                    payment_status = excluded.payment_status;
                """,
                (
                    session.session_id,
                    session.customer_id,
                    session.vehicle_id,
                    session.space_id,
                    session.public_badge_id,
                    _dt_to_iso(session.entry_time),
                    _dt_to_iso(session.exit_time),
                    session.reservation_id,
                    session.status.value,
                    int(session.shield_armed),
                    int(session.validated_visit),
                    str(session.fee_amount),
                    session.payment_status.value,
                    int(session.is_simulated),
                ),
            )
        return session

    def get_session(self, session_id: str) -> ParkingSession | None:
        self._check_available()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM parking_sessions WHERE session_id = ?;", (session_id,)
            ).fetchone()
        return self._row_to_session(row) if row else None

    def get_active_session_for_vehicle(self, vehicle_id: str) -> ParkingSession | None:
        self._check_available()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM parking_sessions
                WHERE vehicle_id = ? AND status IN ('ACTIVE', 'CHECKOUT_PENDING')
                ORDER BY entry_time DESC LIMIT 1;
                """,
                (vehicle_id,),
            ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, status: SessionStatus | None = None) -> list[ParkingSession]:
        self._check_available()
        with self._lock:
            if status:
                rows = self._conn.execute(
                    "SELECT * FROM parking_sessions WHERE status = ? ORDER BY entry_time DESC;",
                    (status.value,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM parking_sessions ORDER BY entry_time DESC;"
                ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def _row_to_session(self, row: sqlite3.Row) -> ParkingSession:
        return ParkingSession(
            session_id=row["session_id"],
            customer_id=row["customer_id"],
            vehicle_id=row["vehicle_id"],
            space_id=row["space_id"],
            public_badge_id=row["public_badge_id"],
            entry_time=_iso_to_dt(row["entry_time"]) or datetime.datetime.now(datetime.timezone.utc),
            exit_time=_iso_to_dt(row["exit_time"]),
            reservation_id=row["reservation_id"],
            status=SessionStatus(row["status"]),
            shield_armed=bool(row["shield_armed"]),
            validated_visit=bool(row["validated_visit"]),
            fee_amount=Decimal(row["fee_amount"]),
            payment_status=PaymentStatus(row["payment_status"]),
            is_simulated=bool(row["is_simulated"]),
        )

    # ------------------------------------------------------------------
    # Access Decisions, Sensor Events, Incidents, Notifications, Payments
    # ------------------------------------------------------------------
    def save_access_decision(self, record: AccessAuthorizationRecord) -> AccessAuthorizationRecord:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO access_authorizations (
                    decision_id, direction, decision_status, reason,
                    alternative_workflow, vehicle_id, customer_id, session_id,
                    assigned_space_id, credential_type, confidence, is_simulated, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    record.decision_id,
                    record.direction.value,
                    record.decision_status.value,
                    record.reason,
                    record.alternative_workflow,
                    record.vehicle_id,
                    record.customer_id,
                    record.session_id,
                    record.assigned_space_id,
                    record.credential_type.value,
                    record.confidence,
                    int(record.is_simulated),
                    _dt_to_iso(record.timestamp),
                ),
            )
        return record

    def list_access_decisions(self, limit: int = 100) -> list[AccessAuthorizationRecord]:
        self._check_available()
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM access_authorizations ORDER BY timestamp DESC LIMIT ?;",
                (limit,),
            ).fetchall()
        return [
            AccessAuthorizationRecord(
                decision_id=r["decision_id"],
                direction=AccessDirection(r["direction"]),
                decision_status=AccessDecisionStatus(r["decision_status"]),
                reason=r["reason"],
                alternative_workflow=r["alternative_workflow"],
                vehicle_id=r["vehicle_id"],
                customer_id=r["customer_id"],
                session_id=r["session_id"],
                assigned_space_id=r["assigned_space_id"],
                credential_type=CredentialType(r["credential_type"]),
                confidence=float(r["confidence"]),
                is_simulated=bool(r["is_simulated"]),
                timestamp=_iso_to_dt(r["timestamp"]) or datetime.datetime.now(datetime.timezone.utc),
            )
            for r in rows
        ]

    def save_sensor_event(self, event: SensorEventRecord) -> SensorEventRecord:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO sensor_events (
                    event_id, session_id, vehicle_id, event_type,
                    source_authenticated, sensor_health_ok, payload_json,
                    is_simulated, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    event.event_id,
                    event.session_id,
                    event.vehicle_id,
                    event.event_type.value,
                    int(event.source_authenticated),
                    int(event.sensor_health_ok),
                    json.dumps(event.payload, default=str),
                    int(event.is_simulated),
                    _dt_to_iso(event.timestamp),
                ),
            )
        return event

    def list_sensor_events(self, session_id: str | None = None) -> list[SensorEventRecord]:
        self._check_available()
        with self._lock:
            if session_id:
                rows = self._conn.execute(
                    "SELECT * FROM sensor_events WHERE session_id = ? ORDER BY timestamp ASC;",
                    (session_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM sensor_events ORDER BY timestamp DESC;"
                ).fetchall()
        return [
            SensorEventRecord(
                event_id=r["event_id"],
                session_id=r["session_id"],
                vehicle_id=r["vehicle_id"],
                event_type=SensorEventType(r["event_type"]),
                source_authenticated=bool(r["source_authenticated"]),
                sensor_health_ok=bool(r["sensor_health_ok"]),
                payload=json.loads(r["payload_json"]),
                is_simulated=bool(r["is_simulated"]),
                timestamp=_iso_to_dt(r["timestamp"]) or datetime.datetime.now(datetime.timezone.utc),
            )
            for r in rows
        ]

    def save_security_incident(self, incident: SecurityIncidentRecord) -> SecurityIncidentRecord:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO security_incidents (
                    incident_id, session_id, vehicle_id, severity, classification,
                    triggered_rules_json, received_event_ids_json, source_authenticated,
                    is_expected, confidence, limitations_note, recommended_response,
                    explanation, resolved, is_simulated, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(incident_id) DO UPDATE SET
                    severity = excluded.severity,
                    classification = excluded.classification,
                    triggered_rules_json = excluded.triggered_rules_json,
                    received_event_ids_json = excluded.received_event_ids_json,
                    resolved = excluded.resolved;
                """,
                (
                    incident.incident_id,
                    incident.session_id,
                    incident.vehicle_id,
                    incident.severity.value,
                    incident.classification,
                    json.dumps(incident.triggered_rules),
                    json.dumps(incident.received_event_ids),
                    int(incident.source_authenticated),
                    int(incident.is_expected),
                    incident.confidence,
                    incident.limitations_note,
                    incident.recommended_response,
                    incident.explanation,
                    int(incident.resolved),
                    int(incident.is_simulated),
                    _dt_to_iso(incident.timestamp),
                ),
            )
        return incident

    def list_security_incidents(self, session_id: str | None = None) -> list[SecurityIncidentRecord]:
        self._check_available()
        with self._lock:
            if session_id:
                rows = self._conn.execute(
                    "SELECT * FROM security_incidents WHERE session_id = ? ORDER BY timestamp DESC;",
                    (session_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM security_incidents ORDER BY timestamp DESC;"
                ).fetchall()
        return [
            SecurityIncidentRecord(
                incident_id=r["incident_id"],
                session_id=r["session_id"],
                vehicle_id=r["vehicle_id"],
                severity=SeverityLevel(r["severity"]),
                classification=r["classification"],
                triggered_rules=json.loads(r["triggered_rules_json"]),
                received_event_ids=json.loads(r["received_event_ids_json"]),
                source_authenticated=bool(r["source_authenticated"]),
                is_expected=bool(r["is_expected"]),
                confidence=float(r["confidence"]),
                limitations_note=r["limitations_note"],
                recommended_response=r["recommended_response"],
                explanation=r["explanation"],
                resolved=bool(r["resolved"]),
                is_simulated=bool(r["is_simulated"]),
                timestamp=_iso_to_dt(r["timestamp"]) or datetime.datetime.now(datetime.timezone.utc),
            )
            for r in rows
        ]

    def save_notification(self, notification: NotificationRecord) -> NotificationRecord:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO notifications (
                    notification_id, customer_id, session_id, incident_id, severity,
                    title, message, reason, confidence_limitations, safe_next_action,
                    delivery_status, provider_name, is_simulated, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                """,
                (
                    notification.notification_id,
                    notification.customer_id,
                    notification.session_id,
                    notification.incident_id,
                    notification.severity.value,
                    notification.title,
                    notification.message,
                    notification.reason,
                    notification.confidence_limitations,
                    notification.safe_next_action,
                    notification.delivery_status,
                    notification.provider_name,
                    int(notification.is_simulated),
                    _dt_to_iso(notification.timestamp),
                ),
            )
        return notification

    def list_notifications(self, customer_id: str | None = None) -> list[NotificationRecord]:
        self._check_available()
        with self._lock:
            if customer_id:
                rows = self._conn.execute(
                    "SELECT * FROM notifications WHERE customer_id = ? ORDER BY timestamp DESC;",
                    (customer_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM notifications ORDER BY timestamp DESC;"
                ).fetchall()
        return [
            NotificationRecord(
                notification_id=r["notification_id"],
                customer_id=r["customer_id"],
                session_id=r["session_id"],
                incident_id=r["incident_id"],
                severity=SeverityLevel(r["severity"]),
                title=r["title"],
                message=r["message"],
                reason=r["reason"],
                confidence_limitations=r["confidence_limitations"],
                safe_next_action=r["safe_next_action"],
                delivery_status=r["delivery_status"],
                provider_name=r["provider_name"],
                is_simulated=bool(r["is_simulated"]),
                timestamp=_iso_to_dt(r["timestamp"]) or datetime.datetime.now(datetime.timezone.utc),
            )
            for r in rows
        ]

    def save_payment(self, payment: PaymentRecord) -> PaymentRecord:
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT INTO payments (
                    transaction_id, idempotency_key, session_id, customer_id,
                    amount, currency, method, status, invoice_number,
                    breakdown_json, is_simulated, timestamp
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(transaction_id) DO UPDATE SET
                    amount = excluded.amount,
                    method = excluded.method,
                    status = excluded.status,
                    breakdown_json = excluded.breakdown_json;
                """,
                (
                    payment.transaction_id,
                    payment.idempotency_key,
                    payment.session_id,
                    payment.customer_id,
                    str(payment.amount),
                    payment.currency,
                    payment.method.value,
                    payment.status.value,
                    payment.invoice_number,
                    json.dumps(payment.breakdown),
                    int(payment.is_simulated),
                    _dt_to_iso(payment.timestamp),
                ),
            )
        return payment

    def get_payment_by_idempotency_key(self, idempotency_key: str) -> PaymentRecord | None:
        self._check_available()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM payments WHERE idempotency_key = ?;", (idempotency_key,)
            ).fetchone()
        return self._row_to_payment(row) if row else None

    def get_payment(self, transaction_id: str) -> PaymentRecord | None:
        self._check_available()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM payments WHERE transaction_id = ?;", (transaction_id,)
            ).fetchone()
        return self._row_to_payment(row) if row else None

    def list_payments(self, session_id: str | None = None) -> list[PaymentRecord]:
        self._check_available()
        with self._lock:
            if session_id:
                rows = self._conn.execute(
                    "SELECT * FROM payments WHERE session_id = ? ORDER BY timestamp DESC;",
                    (session_id,),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM payments ORDER BY timestamp DESC;"
                ).fetchall()
        return [self._row_to_payment(r) for r in rows]

    def _row_to_payment(self, row: sqlite3.Row) -> PaymentRecord:
        return PaymentRecord(
            transaction_id=row["transaction_id"],
            idempotency_key=row["idempotency_key"],
            session_id=row["session_id"],
            customer_id=row["customer_id"],
            amount=Decimal(row["amount"]),
            currency=row["currency"],
            method=PaymentMethodType(row["method"]),
            status=PaymentStatus(row["status"]),
            invoice_number=row["invoice_number"],
            breakdown=json.loads(row["breakdown_json"]),
            is_simulated=bool(row["is_simulated"]),
            timestamp=_iso_to_dt(row["timestamp"]) or datetime.datetime.now(datetime.timezone.utc),
        )

    # ------------------------------------------------------------------
    # Privacy & Data Retention Enforcement (GDPR / Section 12)
    # ------------------------------------------------------------------
    def purge_expired_records(
        self,
        reference_time: datetime.datetime,
        access_logs_days: int,
        sensor_events_days: int,
        actor_id: str = "SYSTEM_RETENTION_DAEMON",
        actor_role: StaffRole = StaffRole.ADMIN,
    ) -> dict[str, int]:
        """Delete access logs and sensor events older than the configured retention period."""
        if actor_role not in (StaffRole.ADMIN, StaffRole.SECURITY_OFFICER):
            raise PermissionError("Insufficient permissions to execute data retention purge.")
        if reference_time.tzinfo is None:
            reference_time = reference_time.replace(tzinfo=datetime.timezone.utc)

        access_cutoff = _dt_to_iso(reference_time - datetime.timedelta(days=access_logs_days))
        sensor_cutoff = _dt_to_iso(reference_time - datetime.timedelta(days=sensor_events_days))

        with self.transaction() as conn:
            cur_access = conn.execute(
                "DELETE FROM access_authorizations WHERE timestamp < ?;", (access_cutoff,)
            )
            cur_sensor = conn.execute(
                "DELETE FROM sensor_events WHERE timestamp < ?;", (sensor_cutoff,)
            )
            deleted = {
                "access_authorizations_deleted": cur_access.rowcount,
                "sensor_events_deleted": cur_sensor.rowcount,
            }

        self.record_audit(
            actor_id=actor_id,
            actor_role=actor_role,
            action="DATA_RETENTION_PURGE",
            target_entity="access_authorizations,sensor_events",
            details=deleted,
            timestamp=reference_time,
        )
        return deleted

    def anonymize_and_delete_customer(
        self,
        customer_id: str,
        actor_id: str,
        actor_role: StaffRole,
    ) -> bool:
        """GDPR right-to-erasure workflow: scrub PII while retaining statutory tax ledger totals."""
        if actor_role != StaffRole.ADMIN:
            raise PermissionError("Only ADMIN role can execute account PII erasure.")
        customer = self.get_customer(customer_id)
        if customer is None:
            return False

        active_sessions = [
            s for s in self.list_sessions(SessionStatus.ACTIVE) if s.customer_id == customer_id
        ]
        if active_sessions:
            raise ValueError("Cannot delete customer account while a parking session is active.")

        redacted_token = f"REDACTED-{uuid.uuid4().hex[:8].upper()}"
        with self.transaction() as conn:
            conn.execute(
                """
                UPDATE customers
                SET name = '[REDACTED]',
                    email = ?,
                    phone = '[REDACTED]',
                    trusted_contact = NULL,
                    status = ?
                WHERE customer_id = ?;
                """,
                (f"{redacted_token}@deleted.invalid", AccountStatus.DELETED.value, customer_id),
            )
            conn.execute(
                """
                UPDATE vehicles
                SET license_plate_raw = '[REDACTED]',
                    license_plate_masked = '[REDACTED]',
                    license_plate_hash = ?,
                    enrolled_digital_key_id = NULL,
                    enrolled_rfid_tag = NULL
                WHERE customer_id = ?;
                """,
                (f"HASH-{redacted_token}", customer_id),
            )

        self.record_audit(
            actor_id=actor_id,
            actor_role=actor_role,
            action="GDPR_CUSTOMER_ERASURE",
            target_entity=customer_id,
            details={"redacted_token": redacted_token},
        )
        return True
