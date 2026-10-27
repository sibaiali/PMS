# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Unified SmartPark Guardian AI Ecosystem Facade coordinating all domain services."""

from __future__ import annotations

import datetime
from decimal import Decimal
from typing import Any

from smartpark.access.engine import BarrierFreeAccessEngine
from smartpark.analytics.engine import SmartParkAnalyticsEngine
from smartpark.billing.engine import BillingEngine, SimulatedPaymentProvider
from smartpark.config.settings import SmartParkConfig
from smartpark.core.engine import ParkingEngine
from smartpark.core.models import (
    CredentialType,
    SensorEventType,
    VehicleSize,
)
from smartpark.database.db import DatabaseManager
from smartpark.guardian.shield import GuardianShieldEngine
from smartpark.notifications.service import MockPushNotificationProvider, NotificationService
from smartpark.routing.grid_map import GarageGridMap
from smartpark.security.digital_key import SimulatedVehicleAccessAdapter, WalkUpDigitalKeyService
from smartpark.security.tokens import PrivacyBadgeManager
from smartpark.simulation.simulator import GarageSimulator


class SmartParkEcosystem:
    """Top-level application container wiring all GUI-independent domain engines."""

    def __init__(self, config: SmartParkConfig | None = None) -> None:
        self.config = config or SmartParkConfig()
        self.db = DatabaseManager(self.config.database_path)
        self.parking = ParkingEngine(config=self.config, db=self.db)
        self.payment_provider = SimulatedPaymentProvider()
        self.billing = BillingEngine(
            config=self.config,
            db=self.db,
            provider=self.payment_provider,
        )
        self.badges = PrivacyBadgeManager(config=self.config, db=self.db)
        self.vehicle_adapter = SimulatedVehicleAccessAdapter()
        self.digital_keys = WalkUpDigitalKeyService(
            config=self.config,
            db=self.db,
            adapter=self.vehicle_adapter,
        )
        self.access = BarrierFreeAccessEngine(
            config=self.config,
            parking_engine=self.parking,
            billing_engine=self.billing,
            badge_manager=self.badges,
        )
        self.push_provider = MockPushNotificationProvider()
        self.notifications = NotificationService(
            config=self.config,
            db=self.db,
            provider=self.push_provider,
        )
        self.guardian = GuardianShieldEngine(
            config=self.config,
            db=self.db,
            notification_service=self.notifications,
        )
        self.grid_map = GarageGridMap()
        self.simulator = GarageSimulator(grid_map=self.grid_map)
        self.analytics = SmartParkAnalyticsEngine(
            db=self.db,
            simulator=self.simulator,
        )

    def seed_demonstration_scenario(self) -> dict[str, Any]:
        """Populate realistic customers, vehicles, barrier-free arrivals, security events, and simulation ticks."""
        if self.db.list_customers():
            return {"status": "already_seeded"}

        now = datetime.datetime(2026, 10, 27, 10, 0, tzinfo=datetime.timezone.utc)

        # 1. Register 3 customers with diverse profiles
        c1 = self.parking.register_customer(
            name="Ali Sibai",
            email="ali.sibai@example.de",
            phone="+49 170 1002001",
            requires_accessible=False,
            has_subscription=False,
            prepaid_balance=Decimal("80.00"),
            quiet_hours_start=23,
            quiet_hours_end=6,
            trusted_contact="+49 170 9999999",
        )
        c2 = self.parking.register_customer(
            name="Dr. Elena Weber",
            email="elena.weber@example.de",
            phone="+49 171 2003002",
            requires_accessible=True,
            has_subscription=True,
            prepaid_balance=Decimal("40.00"),
        )
        c3 = self.parking.register_customer(
            name="Marcus Vogel",
            email="marcus.vogel@example.de",
            phone="+49 172 3004003",
            requires_accessible=False,
            has_subscription=False,
            prepaid_balance=Decimal("35.00"),
        )

        # 2. Register vehicles
        v1 = self.parking.register_vehicle(
            customer_id=c1.customer_id,
            license_plate="B-SP 2026",
            size=VehicleSize.STANDARD,
            requires_ev_charging=True,
        )
        v2 = self.parking.register_vehicle(
            customer_id=c2.customer_id,
            license_plate="M-EW 101",
            size=VehicleSize.LARGE,
            requires_ev_charging=False,
        )
        v3 = self.parking.register_vehicle(
            customer_id=c3.customer_id,
            license_plate="HH-MV 88",
            size=VehicleSize.COMPACT,
            requires_ev_charging=False,
        )

        # 3. Create a reservation for Dr. Elena Weber
        res_elena = self.parking.create_reservation(
            customer_id=c2.customer_id,
            vehicle_id=v2.vehicle_id,
            start_time=now - datetime.timedelta(minutes=15),
            end_time=now + datetime.timedelta(hours=3),
        )

        # 4. Barrier-free arrivals
        arr1 = self.access.evaluate_arrival(
            license_plate="B-SP 2026",
            credential_type=CredentialType.BLE_DIGITAL_KEY_SIM,
            secondary_credential_verified=True,
            confidence=0.98,
            at_time=now - datetime.timedelta(hours=2),
        )
        arr2 = self.access.evaluate_arrival(
            license_plate="M-EW 101",
            credential_type=CredentialType.ENROLLED_LPR_SIM,
            secondary_credential_verified=True,
            confidence=0.97,
            at_time=now - datetime.timedelta(minutes=45),
        )
        arr3 = self.access.evaluate_arrival(
            license_plate="HH-MV 88",
            credential_type=CredentialType.ENROLLED_LPR_SIM,
            secondary_credential_verified=True,
            confidence=0.95,
            at_time=now - datetime.timedelta(minutes=90),
        )

        # 5. Simulate Guardian Shield events on Ali's session (arr1)
        if arr1.session:
            # First a routine pedestrian walking past -> ethical rule (no false alarm)
            self.guardian.ingest_sensor_event(
                session_id=arr1.session.session_id,
                event_type=SensorEventType.PEDESTRIAN_PROXIMITY,
                source_authenticated=True,
                payload={"distance_m": 1.1, "note": "Pedestrian on adjacent walkway"},
                at_time=now - datetime.timedelta(minutes=40),
            )
            # Next a suspicious impact + glass tamper -> confirmed high-priority alarm
            self.guardian.ingest_sensor_event(
                session_id=arr1.session.session_id,
                event_type=SensorEventType.IMPACT_HEAVY,
                source_authenticated=True,
                payload={"g_force": 2.4},
                at_time=now - datetime.timedelta(minutes=20),
            )
            self.guardian.ingest_sensor_event(
                session_id=arr1.session.session_id,
                event_type=SensorEventType.ALARM_TRIGGERED,
                source_authenticated=True,
                payload={"zone": "rear_window"},
                at_time=now - datetime.timedelta(minutes=19, seconds=30),
            )

        # 6. Complete departure for Marcus Vogel (v3) to populate revenue & receipt history
        if arr3.session:
            self.access.evaluate_departure(
                session_id=arr3.session.session_id,
                at_time=now,
            )

        # 7. Run deterministic simulation ticks
        sim_summary = self.simulator.run_deterministic_demo_scenario(steps=16)

        return {
            "customers": 3,
            "vehicles": 3,
            "active_reservation": res_elena.reservation_id,
            "simulation": sim_summary,
        }
