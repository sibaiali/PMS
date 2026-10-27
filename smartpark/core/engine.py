# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Core Parking Engine coordinating customers, vehicles, spaces, reservations, and concurrent allocation."""

from __future__ import annotations

import datetime
import hashlib
import re
import threading
import uuid
from decimal import Decimal

from smartpark.config.settings import SmartParkConfig
from smartpark.core.allocation import AllocationComparisonResult, IntelligentSpaceAllocator
from smartpark.core.models import (
    AccountStatus,
    Customer,
    ParkingSession,
    ParkingSpace,
    PaymentStatus,
    Reservation,
    SessionStatus,
    SpaceStatus,
    SpaceType,
    StaffRole,
    Vehicle,
    VehicleSize,
)
from smartpark.database.db import DatabaseManager


def normalize_license_plate(plate: str) -> str:
    cleaned = re.sub(r"\s+", " ", plate.strip().upper())
    if not cleaned or len(cleaned) < 2:
        raise ValueError("License plate must be at least 2 characters.")
    return cleaned


def hash_license_plate(plate: str, salt: str = "smartpark-v2") -> str:
    normalized = normalize_license_plate(plate)
    return hashlib.sha256(f"{salt}:{normalized}".encode("utf-8")).hexdigest()


def mask_license_plate(plate: str) -> str:
    normalized = normalize_license_plate(plate)
    if len(normalized) <= 4:
        return normalized[0] + "***"
    return f"{normalized[:2]}***{normalized[-2:]}"


class ParkingEngine:
    """GUI-independent core parking engine backed by SQLite transactions."""

    def __init__(
        self,
        config: SmartParkConfig | None = None,
        db: DatabaseManager | None = None,
    ) -> None:
        self.config = config or SmartParkConfig()
        self.db = db or DatabaseManager(self.config.database_path)
        self.allocator = IntelligentSpaceAllocator(self.config.allocation_weights)
        self._allocation_lock = threading.RLock()

        if not self.db.list_spaces():
            self.seed_default_facility_spaces()

    def seed_default_facility_spaces(self) -> list[ParkingSpace]:
        """Initialize a realistic 16-space multi-zone garage layout matching the simulation map."""
        default_specs = [
            # Zone A (Near Pedestrian Lobby & Accessible / EV Bays)
            (1, "A-01", "A", 3, 2, SpaceType.ACCESSIBLE, VehicleSize.LARGE, False, True, 8.0, 34.0, False),
            (2, "A-02", "A", 5, 2, SpaceType.ACCESSIBLE, VehicleSize.LARGE, True, True, 10.0, 32.0, False),
            (3, "A-03", "A", 7, 2, SpaceType.EV_CHARGING, VehicleSize.STANDARD, True, False, 14.0, 28.0, False),
            (4, "A-04", "A", 9, 2, SpaceType.EV_CHARGING, VehicleSize.LARGE, True, False, 16.0, 26.0, False),
            (5, "A-05", "A", 11, 2, SpaceType.COMPACT, VehicleSize.COMPACT, False, False, 19.0, 23.0, False),
            (6, "A-06", "A", 13, 2, SpaceType.STANDARD, VehicleSize.STANDARD, False, False, 22.0, 20.0, True),
            # Zone B (Central Standard & Large Bays)
            (7, "B-01", "B", 3, 6, SpaceType.STANDARD, VehicleSize.STANDARD, False, False, 18.0, 28.0, False),
            (8, "B-02", "B", 5, 6, SpaceType.STANDARD, VehicleSize.STANDARD, False, False, 20.0, 26.0, False),
            (9, "B-03", "B", 7, 6, SpaceType.LARGE, VehicleSize.SUV, False, False, 23.0, 24.0, False),
            (10, "B-04", "B", 9, 6, SpaceType.LARGE, VehicleSize.SUV, True, False, 25.0, 22.0, False),
            (11, "B-05", "B", 11, 6, SpaceType.COMPACT, VehicleSize.COMPACT, False, False, 28.0, 19.0, False),
            (12, "B-06", "B", 13, 6, SpaceType.STANDARD, VehicleSize.STANDARD, False, False, 31.0, 16.0, True),
            # Zone C (Exit Corridor & Emergency Reserved Bay)
            (13, "C-01", "C", 3, 10, SpaceType.STANDARD, VehicleSize.STANDARD, False, False, 30.0, 18.0, False),
            (14, "C-02", "C", 5, 10, SpaceType.LARGE, VehicleSize.SUV, False, False, 32.0, 15.0, False),
            (15, "C-03", "C", 7, 10, SpaceType.COMPACT, VehicleSize.COMPACT, False, False, 35.0, 12.0, False),
            (16, "E-99", "C", 13, 10, SpaceType.EMERGENCY_RESERVED, VehicleSize.SUV, False, False, 12.0, 10.0, False),
        ]
        spaces: list[ParkingSpace] = []
        for (
            sid,
            code,
            zone,
            gx,
            gy,
            stype,
            msize,
            charger,
            acc,
            walk_d,
            exit_d,
            blind,
        ) in default_specs:
            sp = ParkingSpace(
                space_id=sid,
                code=code,
                zone=zone,
                grid_x=gx,
                grid_y=gy,
                space_type=stype,
                max_size=msize,
                has_charger=charger,
                is_accessible=acc,
                walking_distance_m=walk_d,
                exit_distance_m=exit_d,
                near_blind_corner=blind,
                status=SpaceStatus.AVAILABLE,
            )
            self.db.save_space(sp)
            spaces.append(sp)
        return spaces

    def register_customer(
        self,
        name: str,
        email: str,
        phone: str,
        requires_accessible: bool = False,
        has_subscription: bool = False,
        prepaid_balance: Decimal = Decimal("50.00"),
        quiet_hours_start: int | None = 22,
        quiet_hours_end: int | None = 7,
        trusted_contact: str | None = None,
    ) -> Customer:
        if not name or not name.strip():
            raise ValueError("Customer name is required.")
        if not email or "@" not in email:
            raise ValueError("Valid email address is required.")
        customer = Customer(
            customer_id=f"CUST-{uuid.uuid4().hex[:8].upper()}",
            public_alias=f"MBR-{uuid.uuid4().hex[:6].upper()}",
            name=name.strip(),
            email=email.strip().lower(),
            phone=phone.strip(),
            status=AccountStatus.ACTIVE,
            requires_accessible=requires_accessible,
            has_subscription=has_subscription,
            prepaid_balance=Decimal(str(prepaid_balance)),
            quiet_hours_start=quiet_hours_start,
            quiet_hours_end=quiet_hours_end,
            trusted_contact=trusted_contact,
        )
        self.db.save_customer(customer)
        self.db.record_audit(
            actor_id=customer.customer_id,
            actor_role="CUSTOMER",
            action="REGISTER_CUSTOMER",
            target_entity=customer.customer_id,
            details={"public_alias": customer.public_alias, "accessible": requires_accessible},
        )
        return customer

    def register_vehicle(
        self,
        customer_id: str,
        license_plate: str,
        size: VehicleSize = VehicleSize.STANDARD,
        requires_ev_charging: bool = False,
        enroll_digital_key: bool = True,
    ) -> Vehicle:
        customer = self.db.get_customer(customer_id)
        if customer is None or customer.status != AccountStatus.ACTIVE:
            raise ValueError("Customer account not found or not active.")

        normalized = normalize_license_plate(license_plate)
        plate_hash = hash_license_plate(normalized)
        existing = self.db.get_vehicle_by_plate_hash(plate_hash)
        if existing is not None:
            raise ValueError(f"Vehicle with plate {normalized} is already registered.")

        vehicle = Vehicle(
            vehicle_id=f"VEH-{uuid.uuid4().hex[:8].upper()}",
            customer_id=customer_id,
            public_vehicle_token=f"PVT-{uuid.uuid4().hex[:10].upper()}",
            license_plate_hash=plate_hash,
            license_plate_masked=mask_license_plate(normalized),
            license_plate_raw=normalized,
            size=size,
            requires_ev_charging=requires_ev_charging,
            enrolled_digital_key_id=f"DKEY-{uuid.uuid4().hex[:10].upper()}" if enroll_digital_key else None,
            enrolled_rfid_tag=f"RFID-{uuid.uuid4().hex[:8].upper()}",
        )
        self.db.save_vehicle(vehicle)
        self.db.record_audit(
            actor_id=customer_id,
            actor_role="CUSTOMER",
            action="REGISTER_VEHICLE",
            target_entity=vehicle.vehicle_id,
            details={
                "public_vehicle_token": vehicle.public_vehicle_token,
                "masked_plate": vehicle.license_plate_masked,
                "size": vehicle.size.value,
                "ev": vehicle.requires_ev_charging,
            },
        )
        return vehicle

    def create_reservation(
        self,
        customer_id: str,
        vehicle_id: str,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
        preferred_space_id: int | None = None,
        prepaid_authorized: bool = True,
    ) -> Reservation:
        if start_time.tzinfo is None:
            start_time = start_time.replace(tzinfo=datetime.timezone.utc)
        if end_time.tzinfo is None:
            end_time = end_time.replace(tzinfo=datetime.timezone.utc)
        if end_time <= start_time:
            raise ValueError("Reservation end_time must be strictly after start_time.")

        with self._allocation_lock, self.db.transaction():
            customer = self.db.get_customer(customer_id)
            vehicle = self.db.get_vehicle_by_id(vehicle_id)
            if customer is None or vehicle is None or vehicle.customer_id != customer_id:
                raise ValueError("Invalid customer/vehicle association for reservation.")

            active_reservations = self.db.list_reservations(active_only=True)
            # Check if the same vehicle already has an overlapping reservation
            for res in active_reservations:
                if res.vehicle_id == vehicle_id and res.overlaps(start_time, end_time):
                    raise ValueError("Vehicle already has an overlapping active reservation.")

            spaces = self.db.list_spaces()
            chosen_space_id: int | None = None

            if preferred_space_id is not None:
                space = self.db.get_space(preferred_space_id)
                if space is None or space.space_type == SpaceType.EMERGENCY_RESERVED:
                    raise ValueError("Selected space is not available for reservation.")
                if vehicle.size.rank > space.max_size.rank:
                    raise ValueError("Vehicle size exceeds preferred space maximum size.")
                if space.is_accessible and not customer.requires_accessible:
                    raise ValueError("Accessible space requires an accessibility permit.")
                for res in active_reservations:
                    if res.space_id == preferred_space_id and res.overlaps(start_time, end_time):
                        raise ValueError(f"Space {space.code} already has a conflicting reservation.")
                chosen_space_id = preferred_space_id
            else:
                # Find best space without interval conflict
                non_conflicting_spaces = [
                    s
                    for s in spaces
                    if s.space_type != SpaceType.EMERGENCY_RESERVED
                    and s.status != SpaceStatus.OUT_OF_SERVICE
                    and not any(
                        r.space_id == s.space_id and r.overlaps(start_time, end_time)
                        for r in active_reservations
                    )
                ]
                # Temporarily treat spaces as AVAILABLE for future reservation window scoring
                scoring_copies = [
                    ParkingSpace(**{**s.__dict__, "status": SpaceStatus.AVAILABLE})
                    for s in non_conflicting_spaces
                ]
                rec = self.allocator.recommend(
                    spaces=scoring_copies,
                    customer=customer,
                    vehicle=vehicle,
                    active_reservations=[],
                    at_time=start_time,
                )
                if rec.smart_choice is None:
                    raise ValueError("No compatible parking space available for the requested reservation window.")
                chosen_space_id = rec.smart_choice.space.space_id

            reservation = Reservation(
                reservation_id=f"RES-{uuid.uuid4().hex[:8].upper()}",
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                space_id=chosen_space_id,
                start_time=start_time,
                end_time=end_time,
                requires_accessible=customer.requires_accessible,
                requires_ev_charging=vehicle.requires_ev_charging,
                prepaid_authorized=prepaid_authorized,
                status="ACTIVE",
            )
            self.db.save_reservation(reservation)
            return reservation

    def allocate_space_and_start_session(
        self,
        customer_id: str,
        vehicle_id: str,
        entry_time: datetime.datetime | None = None,
        reservation_id: str | None = None,
    ) -> tuple[ParkingSession, AllocationComparisonResult]:
        """Atomically recommend and allocate a space, preventing concurrent double-allocation."""
        now = entry_time or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)

        with self._allocation_lock, self.db.transaction():
            customer = self.db.get_customer(customer_id)
            vehicle = self.db.get_vehicle_by_id(vehicle_id)
            if customer is None or customer.status != AccountStatus.ACTIVE:
                raise ValueError("Customer account is not active.")
            if vehicle is None or vehicle.customer_id != customer_id:
                raise ValueError("Vehicle is not enrolled for this customer.")

            existing_session = self.db.get_active_session_for_vehicle(vehicle_id)
            if existing_session is not None:
                raise ValueError(f"Vehicle already has an active parking session ({existing_session.session_id}).")

            spaces = self.db.list_spaces()
            active_reservations = self.db.list_reservations(active_only=True)

            comparison = self.allocator.recommend(
                spaces=spaces,
                customer=customer,
                vehicle=vehicle,
                active_reservations=active_reservations,
                at_time=now,
                target_reservation_id=reservation_id,
            )
            if comparison.smart_choice is None:
                raise RuntimeError("FACILITY_FULL_OR_NO_COMPATIBLE_SPACE")

            chosen_space = comparison.smart_choice.space
            session = ParkingSession(
                session_id=f"SES-{uuid.uuid4().hex[:8].upper()}",
                customer_id=customer_id,
                vehicle_id=vehicle_id,
                space_id=chosen_space.space_id,
                public_badge_id=f"BDG-{uuid.uuid4().hex[:10].upper()}",
                entry_time=now,
                reservation_id=reservation_id,
                status=SessionStatus.ACTIVE,
                shield_armed=True,
                payment_status=PaymentStatus.PENDING,
                is_simulated=self.config.simulation_mode,
            )
            chosen_space.status = SpaceStatus.OCCUPIED
            chosen_space.current_session_id = session.session_id

            self.db.save_session(session)
            self.db.save_space(chosen_space)
            return session, comparison

    def release_space_for_session(
        self,
        session_id: str,
        exit_time: datetime.datetime | None = None,
    ) -> ParkingSession:
        now = exit_time or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)

        with self._allocation_lock, self.db.transaction():
            session = self.db.get_session(session_id)
            if session is None:
                raise ValueError(f"Session {session_id} not found.")
            space = self.db.get_space(session.space_id)
            if space is not None:
                space.status = SpaceStatus.AVAILABLE
                space.current_session_id = None
                self.db.save_space(space)

            session.exit_time = now
            session.shield_armed = False
            session.status = SessionStatus.COMPLETED
            if session.reservation_id:
                for res in self.db.list_reservations(active_only=True):
                    if res.reservation_id == session.reservation_id:
                        res.status = "COMPLETED"
                        self.db.save_reservation(res)
            self.db.save_session(session)
            return session

    def set_space_status(
        self,
        space_id: int,
        status: SpaceStatus,
        actor_id: str,
        actor_role: StaffRole,
    ) -> ParkingSpace:
        if actor_role not in (StaffRole.OPERATOR, StaffRole.SECURITY_OFFICER, StaffRole.ADMIN):
            raise PermissionError("Role is not permitted to modify parking space operational status.")
        space = self.db.get_space(space_id)
        if space is None:
            raise ValueError(f"Space {space_id} not found.")
        space.status = status
        self.db.save_space(space)
        self.db.record_audit(
            actor_id=actor_id,
            actor_role=actor_role,
            action="SET_SPACE_STATUS",
            target_entity=space.code,
            details={"new_status": status.value},
        )
        return space
