"""Comprehensive automated test suite for SmartPark Guardian AI (Section 16, Milestones 2–9)."""

from __future__ import annotations

import concurrent.futures
import datetime
from decimal import Decimal
import unittest
from zoneinfo import ZoneInfo

from smartpark.config.settings import SmartParkConfig
from smartpark.core.models import (
    AccessDecisionStatus,
    AccountStatus,
    CredentialType,
    PaymentMethodType,
    PaymentStatus,
    SensorEventType,
    SessionStatus,
    SeverityLevel,
    SpaceStatus,
    StaffRole,
    VehicleSize,
)
from smartpark.guardian.shield import InvalidSensorMessageError
from smartpark.routing.grid_map import calculate_safe_stopping_distance
from smartpark.system import SmartParkEcosystem


class TestSmartParkGuardianEcosystem(unittest.TestCase):
    """Tests all 18+ functional, security, safety, billing, and privacy requirements."""

    def setUp(self) -> None:
        self.config = SmartParkConfig(database_path=":memory:")
        self.eco = SmartParkEcosystem(self.config)

    # 1. User and vehicle registration
    def test_01_user_and_vehicle_registration(self) -> None:
        cust = self.eco.parking.register_customer(
            name="Hannah Meyer",
            email="hannah@example.de",
            phone="+4915111111",
            requires_accessible=True,
        )
        self.assertTrue(cust.customer_id.startswith("CUST-"))
        self.assertTrue(cust.public_alias.startswith("MBR-"))

        veh = self.eco.parking.register_vehicle(
            customer_id=cust.customer_id,
            license_plate="b-hm 2026",
            size=VehicleSize.LARGE,
            requires_ev_charging=True,
        )
        self.assertEqual(veh.license_plate_raw, "B-HM 2026")
        self.assertNotIn("B-HM 2026", veh.public_vehicle_token)
        self.assertIn("***", veh.license_plate_masked)

    # 2. Duplicate vehicles and duplicate events
    def test_02_duplicate_vehicles_and_duplicate_events(self) -> None:
        cust = self.eco.parking.register_customer("Max", "max@example.de", "12345")
        self.eco.parking.register_vehicle(cust.customer_id, "M-MX 100")

        # Duplicate vehicle registration rejected
        with self.assertRaises(ValueError):
            self.eco.parking.register_vehicle(cust.customer_id, "m-mx  100")

        # Duplicate arrival recognition event within window is suppressed
        t0 = datetime.datetime(2026, 10, 27, 9, 0, tzinfo=datetime.timezone.utc)
        first = self.eco.access.evaluate_arrival("M-MX 100", at_time=t0)
        self.assertEqual(first.record.decision_status, AccessDecisionStatus.GRANTED)

        duplicate = self.eco.access.evaluate_arrival(
            "M-MX 100",
            at_time=t0 + datetime.timedelta(seconds=5),
        )
        self.assertEqual(duplicate.record.decision_status, AccessDecisionStatus.DUPLICATE_SUPPRESSED)

        # Duplicate sensor event within 10s is suppressed as INFORMATIONAL
        assert first.session is not None
        _, inc1, _ = self.eco.guardian.ingest_sensor_event(
            session_id=first.session.session_id,
            event_type=SensorEventType.DOOR_OPEN,
            payload={"door": "driver"},
            at_time=t0 + datetime.timedelta(minutes=5),
        )
        self.assertEqual(inc1.severity, SeverityLevel.ATTENTION_REQUIRED)

        _, inc_dup, _ = self.eco.guardian.ingest_sensor_event(
            session_id=first.session.session_id,
            event_type=SensorEventType.DOOR_OPEN,
            payload={"door": "driver"},
            at_time=t0 + datetime.timedelta(minutes=5, seconds=3),
        )
        self.assertEqual(inc_dup.severity, SeverityLevel.INFORMATIONAL)
        self.assertIn("R_DUPLICATE_EVENT_SUPPRESSED", inc_dup.triggered_rules)

    # 3. Entry and exit authorization & license plate never treated as sole proof of ownership
    def test_03_entry_and_exit_authorization_and_plate_ownership_rule(self) -> None:
        cust = self.eco.parking.register_customer("Sophie", "sophie@example.de", "999")
        self.eco.parking.register_vehicle(cust.customer_id, "F-SO 77")

        # Unrecognized vehicle is denied
        unrec = self.eco.access.evaluate_arrival("XX-ZZ 999")
        self.assertEqual(unrec.record.decision_status, AccessDecisionStatus.DENIED)

        # Enrolled plate WITHOUT secondary credential and WITHOUT reservation requires verification
        t0 = datetime.datetime(2026, 10, 27, 10, 0, tzinfo=datetime.timezone.utc)
        unverified_plate = self.eco.access.evaluate_arrival(
            license_plate="F-SO 77",
            credential_type=CredentialType.ENROLLED_LPR_SIM,
            secondary_credential_verified=False,
            at_time=t0,
        )
        self.assertEqual(
            unverified_plate.record.decision_status,
            AccessDecisionStatus.MANUAL_VERIFICATION_REQUIRED,
        )

        # Advance past duplicate window; with secondary credential verified, entry is granted
        verified = self.eco.access.evaluate_arrival(
            license_plate="F-SO 77",
            credential_type=CredentialType.BLE_DIGITAL_KEY_SIM,
            secondary_credential_verified=True,
            at_time=t0 + datetime.timedelta(seconds=30),
        )
        self.assertEqual(verified.record.decision_status, AccessDecisionStatus.GRANTED)
        self.assertIsNotNone(verified.session)

        # Departure settles session and releases space
        dep = self.eco.access.evaluate_departure(
            session_id=verified.session.session_id,
            at_time=t0 + datetime.timedelta(hours=1),
        )
        self.assertEqual(dep.record.decision_status, AccessDecisionStatus.GRANTED)
        self.assertEqual(dep.payment.status, PaymentStatus.SUCCEEDED)
        space = self.eco.db.get_space(verified.session.space_id)
        self.assertEqual(space.status, SpaceStatus.AVAILABLE)

    # 4. Full parking facilities
    def test_04_full_parking_facility_rejection(self) -> None:
        # Mark all non-emergency spaces as OCCUPIED except 1 compact space
        for sp in self.eco.db.list_spaces():
            if sp.space_id != 5:  # Space 5 is COMPACT
                sp.status = SpaceStatus.OCCUPIED
                self.eco.db.save_space(sp)

        c1 = self.eco.parking.register_customer("C1", "c1@example.de", "1")
        self.eco.parking.register_vehicle(c1.customer_id, "B-C1 1", size=VehicleSize.COMPACT)

        c2 = self.eco.parking.register_customer("C2", "c2@example.de", "2")
        self.eco.parking.register_vehicle(c2.customer_id, "B-C2 2", size=VehicleSize.COMPACT)

        arr1 = self.eco.access.evaluate_arrival("B-C1 1")
        self.assertEqual(arr1.record.decision_status, AccessDecisionStatus.GRANTED)

        # Now facility is completely full
        arr2 = self.eco.access.evaluate_arrival("B-C2 2")
        self.assertEqual(arr2.record.decision_status, AccessDecisionStatus.DENIED)
        self.assertIn("FACILITY FULL", arr2.guidance_message)

    # 5. Reservation conflicts
    def test_05_reservation_interval_conflicts(self) -> None:
        c1 = self.eco.parking.register_customer("R1", "r1@example.de", "1")
        v1 = self.eco.parking.register_vehicle(c1.customer_id, "K-R1 10")
        c2 = self.eco.parking.register_customer("R2", "r2@example.de", "2")
        v2 = self.eco.parking.register_vehicle(c2.customer_id, "K-R2 20")

        t_start = datetime.datetime(2026, 10, 28, 10, 0, tzinfo=datetime.timezone.utc)
        t_end = datetime.datetime(2026, 10, 28, 12, 0, tzinfo=datetime.timezone.utc)

        res1 = self.eco.parking.create_reservation(
            customer_id=c1.customer_id,
            vehicle_id=v1.vehicle_id,
            start_time=t_start,
            end_time=t_end,
            preferred_space_id=7,
        )
        self.assertEqual(res1.space_id, 7)

        # Conflicting reservation on the same space 7 during overlapping window [11:00, 13:00] must fail
        with self.assertRaises(ValueError):
            self.eco.parking.create_reservation(
                customer_id=c2.customer_id,
                vehicle_id=v2.vehicle_id,
                start_time=t_start + datetime.timedelta(hours=1),
                end_time=t_end + datetime.timedelta(hours=1),
                preferred_space_id=7,
            )

        # Non-overlapping reservation on space 7 [12:00, 14:00] succeeds
        res2 = self.eco.parking.create_reservation(
            customer_id=c2.customer_id,
            vehicle_id=v2.vehicle_id,
            start_time=t_end,
            end_time=t_end + datetime.timedelta(hours=2),
            preferred_space_id=7,
        )
        self.assertEqual(res2.space_id, 7)

    # 6. Concurrent space allocation
    def test_06_concurrent_space_allocation_no_double_booking(self) -> None:
        # Leave only 2 available standard spaces (Space 7 and Space 8)
        for sp in self.eco.db.list_spaces():
            if sp.space_id not in (7, 8):
                sp.status = SpaceStatus.OCCUPIED
                self.eco.db.save_space(sp)

        customers_vehicles = []
        for i in range(6):
            c = self.eco.parking.register_customer(f"User{i}", f"u{i}@example.de", f"00{i}")
            v = self.eco.parking.register_vehicle(c.customer_id, f"S-CC {100 + i}")
            customers_vehicles.append((c.customer_id, v.vehicle_id))

        def try_allocate(pair: tuple[str, str]) -> int | None:
            try:
                ses, _ = self.eco.parking.allocate_space_and_start_session(pair[0], pair[1])
                return ses.space_id
            except RuntimeError:
                return None

        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            results = list(pool.map(try_allocate, customers_vehicles))

        allocated_spaces = [r for r in results if r is not None]
        # Exactly 2 threads must succeed and receive distinct spaces (7 and 8)
        self.assertEqual(sorted(allocated_spaces), [7, 8])

    # 7. Pricing boundaries and daylight-saving transitions
    def test_07_pricing_boundaries_and_dst_transitions(self) -> None:
        # Grace period boundary: 15 minutes -> 0.00 EUR, 16 minutes -> billed
        t0 = datetime.datetime(2026, 10, 27, 10, 0, tzinfo=datetime.timezone.utc)
        q_grace = self.eco.billing.calculate_fee_quote(t0, t0 + datetime.timedelta(minutes=15))
        self.assertTrue(q_grace.grace_period_applied)
        self.assertEqual(q_grace.final_amount, Decimal("0.00"))

        q_16m = self.eco.billing.calculate_fee_quote(t0, t0 + datetime.timedelta(minutes=16))
        self.assertFalse(q_16m.grace_period_applied)
        self.assertGreater(q_16m.final_amount, Decimal("0.00"))

        # Maximum daily cap boundary: 20 hours at peak/off-peak is capped at max_daily_fee (28.00 EUR)
        q_cap = self.eco.billing.calculate_fee_quote(t0, t0 + datetime.timedelta(hours=20))
        self.assertTrue(q_cap.daily_cap_applied)
        self.assertEqual(q_cap.final_amount, Decimal("28.00"))

        # Daylight Saving Time (Fall-Back in Europe/Berlin: Sunday 2026-10-25)
        # 01:30 CEST (UTC+2) to 03:30 CET (UTC+1) is physically 3 hours (180 minutes), not 2 hours!
        tz_berlin = ZoneInfo("Europe/Berlin")
        entry_local = datetime.datetime(2026, 10, 25, 1, 30, tzinfo=tz_berlin)
        exit_local = entry_local.astimezone(datetime.timezone.utc) + datetime.timedelta(hours=3)
        q_dst = self.eco.billing.calculate_fee_quote(entry_local, exit_local)
        self.assertEqual(q_dst.duration_minutes, 180)

    # 8. Failed and repeated payments (idempotency)
    def test_08_failed_and_repeated_idempotent_payments(self) -> None:
        cust = self.eco.parking.register_customer(
            "Payer", "payer@example.de", "111", prepaid_balance=Decimal("0.00")
        )
        self.eco.parking.register_vehicle(cust.customer_id, "D-PY 10")
        t0 = datetime.datetime(2026, 10, 27, 10, 0, tzinfo=datetime.timezone.utc)
        arr = self.eco.access.evaluate_arrival("D-PY 10", at_time=t0)
        assert arr.session is not None

        # Simulate card decline
        self.eco.payment_provider.simulate_decline = True
        p_fail = self.eco.billing.process_session_payment(
            arr.session,
            exit_time=t0 + datetime.timedelta(hours=1),
            method=PaymentMethodType.SAVED_TOKEN_SIM,
            idempotency_key="IDEM-DECLINE-TEST",
        )
        self.assertEqual(p_fail.status, PaymentStatus.FAILED)

        # Fix card and settle with a new idempotency key
        self.eco.payment_provider.simulate_decline = False
        p_ok1 = self.eco.billing.process_session_payment(
            arr.session,
            exit_time=t0 + datetime.timedelta(hours=1),
            method=PaymentMethodType.SAVED_TOKEN_SIM,
            idempotency_key="IDEM-SETTLE-ONCE",
        )
        self.assertEqual(p_ok1.status, PaymentStatus.SUCCEEDED)

        # Repeated callback with the same idempotency key returns identical transaction without double-charging
        p_ok2 = self.eco.billing.process_session_payment(
            arr.session,
            exit_time=t0 + datetime.timedelta(hours=1),
            method=PaymentMethodType.SAVED_TOKEN_SIM,
            idempotency_key="IDEM-SETTLE-ONCE",
        )
        self.assertEqual(p_ok1.transaction_id, p_ok2.transaction_id)

    # 9. Refund handling
    def test_09_refund_handling_and_wallet_restoration(self) -> None:
        cust = self.eco.parking.register_customer(
            "WalletUser", "wallet@example.de", "222", prepaid_balance=Decimal("50.00")
        )
        self.eco.parking.register_vehicle(cust.customer_id, "B-WL 50")
        t0 = datetime.datetime(2026, 10, 27, 10, 0, tzinfo=datetime.timezone.utc)
        arr = self.eco.access.evaluate_arrival("B-WL 50", at_time=t0)
        dep = self.eco.access.evaluate_departure(
            session_id=arr.session.session_id,
            at_time=t0 + datetime.timedelta(hours=1),
        )
        assert dep.payment is not None
        charged = dep.payment.amount
        self.assertGreater(charged, Decimal("0.00"))
        self.assertEqual(
            self.eco.db.get_customer(cust.customer_id).prepaid_balance,
            Decimal("50.00") - charged,
        )

        # Refund restores wallet balance and marks payment REFUNDED
        refunded = self.eco.billing.refund_payment(
            dep.payment.transaction_id,
            actor_id="STAFF-01",
            actor_role=StaffRole.ADMIN,
        )
        self.assertEqual(refunded.status, PaymentStatus.REFUNDED)
        self.assertEqual(
            self.eco.db.get_customer(cust.customer_id).prepaid_balance,
            Decimal("50.00"),
        )

    # 10. Expired and revoked access tokens & screenshot replay protection
    def test_10_expired_revoked_and_screenshot_replay_tokens(self) -> None:
        cust = self.eco.parking.register_customer("TokenUser", "tok@example.de", "333")
        veh = self.eco.parking.register_vehicle(cust.customer_id, "M-TK 99")
        t0 = datetime.datetime(2026, 10, 27, 12, 0, tzinfo=datetime.timezone.utc)
        arr = self.eco.access.evaluate_arrival("M-TK 99", at_time=t0)
        assert arr.badge is not None

        # Valid within TTL
        ok, _, _ = self.eco.badges.verify_badge_token(
            arr.badge.signed_token,
            now=t0 + datetime.timedelta(seconds=20),
            enforce_rolling_window=True,
        )
        self.assertTrue(ok)

        # Static screenshot presented 2 minutes later fails rolling 30s window check
        ok_stale_slot, msg_slot, _ = self.eco.badges.verify_badge_token(
            arr.badge.signed_token,
            now=t0 + datetime.timedelta(seconds=120),
            enforce_rolling_window=True,
        )
        self.assertFalse(ok_stale_slot)
        self.assertIn("screenshot", msg_slot.lower())

        # Expired after TTL (300s)
        ok_exp, msg_exp, _ = self.eco.badges.verify_badge_token(
            arr.badge.signed_token,
            now=t0 + datetime.timedelta(seconds=400),
        )
        self.assertFalse(ok_exp)
        self.assertIn("expired", msg_exp.lower())

        # Explicit revocation
        self.eco.badges.revoke_badge_token(arr.badge.token_jti)
        ok_rev, msg_rev, _ = self.eco.badges.verify_badge_token(
            arr.badge.signed_token,
            now=t0 + datetime.timedelta(seconds=10),
        )
        self.assertFalse(ok_rev)
        self.assertIn("revoked", msg_rev.lower())

    # 11. Authenticated Walk-Up Digital Key security
    def test_11_walk_up_digital_key_security(self) -> None:
        cust = self.eco.parking.register_customer("KeyUser", "key@example.de", "444")
        veh = self.eco.parking.register_vehicle(cust.customer_id, "B-DK 77")
        t0 = datetime.datetime(2026, 10, 27, 12, 0, tzinfo=datetime.timezone.utc)

        challenge = self.eco.digital_keys.detect_ble_proximity_and_issue_challenge(
            customer_id=cust.customer_id,
            vehicle_id=veh.vehicle_id,
            proximity_distance_m=1.8,
            now=t0,
        )
        sig = self.eco.digital_keys.sign_user_confirmation(challenge, "BIOMETRIC_OK")

        # Proximity WITHOUT user_confirmed=True must fail
        with self.assertRaises(PermissionError):
            self.eco.digital_keys.confirm_and_execute(
                challenge_id=challenge.challenge_id,
                nonce=challenge.nonce,
                user_confirmed=False,
                confirmation_signature=sig,
                now=t0 + datetime.timedelta(seconds=5),
            )

        # Confirmed biometric unlock succeeds
        res = self.eco.digital_keys.confirm_and_execute(
            challenge_id=challenge.challenge_id,
            nonce=challenge.nonce,
            user_confirmed=True,
            confirmation_signature=sig,
            now=t0 + datetime.timedelta(seconds=5),
        )
        self.assertTrue(res["verified_success"])

        # Replay of the same challenge/nonce is blocked
        with self.assertRaises(PermissionError):
            self.eco.digital_keys.confirm_and_execute(
                challenge_id=challenge.challenge_id,
                nonce=challenge.nonce,
                user_confirmed=True,
                confirmation_signature=sig,
                now=t0 + datetime.timedelta(seconds=6),
            )

    # 12. Invalid sensor messages
    def test_12_invalid_sensor_messages_rejected(self) -> None:
        with self.assertRaises(InvalidSensorMessageError):
            self.eco.guardian.ingest_sensor_event("", SensorEventType.DOOR_OPEN)

        with self.assertRaises(InvalidSensorMessageError):
            self.eco.guardian.ingest_sensor_event("SES-NONEXISTENT", "NOT_A_REAL_EVENT")

        with self.assertRaises(InvalidSensorMessageError):
            self.eco.guardian.ingest_sensor_event(
                "SES-NONEXISTENT", SensorEventType.DOOR_OPEN, payload="bad_payload"  # type: ignore[arg-type]
            )

    # 13. Notification cooldowns and quiet hours
    def test_13_notification_cooldowns_and_quiet_hours(self) -> None:
        cust = self.eco.parking.register_customer(
            "QuietUser",
            "quiet@example.de",
            "555",
            quiet_hours_start=22,
            quiet_hours_end=7,
        )
        self.eco.parking.register_vehicle(cust.customer_id, "B-QH 11")
        t_day = datetime.datetime(2026, 10, 27, 14, 0, tzinfo=datetime.timezone.utc)
        arr = self.eco.access.evaluate_arrival("B-QH 11", at_time=t_day)
        assert arr.session is not None

        # First unexpected DOOR_OPEN at 14:00 delivers push notification
        _, _, ntf1 = self.eco.guardian.ingest_sensor_event(
            session_id=arr.session.session_id,
            event_type=SensorEventType.DOOR_OPEN,
            payload={"seq": 1},
            at_time=t_day,
        )
        self.assertEqual(ntf1.delivery_status, "DELIVERED_SIM")

        # Second unexpected event 20s later (within 120s cooldown) is suppressed by cooldown
        _, _, ntf2 = self.eco.guardian.ingest_sensor_event(
            session_id=arr.session.session_id,
            event_type=SensorEventType.DOOR_OPEN,
            payload={"seq": 2},
            at_time=t_day + datetime.timedelta(seconds=20),
        )
        self.assertEqual(ntf2.delivery_status, "SUPPRESSED_COOLDOWN")

        # Attention-required event during quiet hours (23:30) is queued silently
        t_night = datetime.datetime(2026, 10, 27, 23, 30, tzinfo=datetime.timezone.utc)
        _, _, ntf_quiet = self.eco.guardian.ingest_sensor_event(
            session_id=arr.session.session_id,
            event_type=SensorEventType.DOOR_OPEN,
            payload={"seq": 3},
            at_time=t_night,
        )
        self.assertEqual(ntf_quiet.delivery_status, "QUEUED_QUIET_HOURS")

        # High-priority confirmed alarm (ALARM_TRIGGERED) breaks through quiet hours
        _, _, ntf_crit = self.eco.guardian.ingest_sensor_event(
            session_id=arr.session.session_id,
            event_type=SensorEventType.ALARM_TRIGGERED,
            payload={"seq": 4},
            at_time=t_night + datetime.timedelta(minutes=5),
        )
        self.assertEqual(ntf_crit.delivery_status, "DELIVERED_SIM")

    # 14. False-positive security events & ethical pedestrian rule
    def test_14_false_positive_security_and_ethical_pedestrian_rule(self) -> None:
        cust = self.eco.parking.register_customer("EthicsUser", "eth@example.de", "666")
        self.eco.parking.register_vehicle(cust.customer_id, "B-ET 42")
        arr = self.eco.access.evaluate_arrival("B-ET 42")
        assert arr.session is not None

        # Pedestrian proximity alone must NEVER trigger an alarm or accusation
        _, inc_ped, ntf_ped = self.eco.guardian.ingest_sensor_event(
            session_id=arr.session.session_id,
            event_type=SensorEventType.PEDESTRIAN_PROXIMITY,
            payload={"distance_m": 0.8},
        )
        self.assertEqual(inc_ped.severity, SeverityLevel.INFORMATIONAL)
        self.assertEqual(inc_ped.classification, "SENSOR_EVENT")
        self.assertIn("R_ETHICS_NO_PROXIMITY_ACCUSATION", inc_ped.triggered_rules)
        self.assertEqual(ntf_ped.delivery_status, "SILENT_HISTORY_ONLY")

        # Unauthenticated spoofed alarm event is rejected from triggering high-priority alert
        _, inc_unauth, _ = self.eco.guardian.ingest_sensor_event(
            session_id=arr.session.session_id,
            event_type=SensorEventType.ALARM_TRIGGERED,
            source_authenticated=False,
        )
        self.assertEqual(inc_unauth.severity, SeverityLevel.INFORMATIONAL)
        self.assertIn("R_UNAUTHENTICATED_SOURCE_REJECTED", inc_unauth.triggered_rules)

    # 15. Collision detection, A* pathfinding, and deterministic simulation scenario
    def test_15_collision_detection_astar_and_deterministic_simulation(self) -> None:
        stop_res = calculate_safe_stopping_distance(speed_mps=4.0)
        self.assertGreater(stop_res.total_stopping_distance_m, 6.0)
        self.assertIn("SIMULATION MODEL ONLY", stop_res.disclaimer)

        summary = self.eco.simulator.run_deterministic_demo_scenario(steps=16)
        self.assertEqual(summary["total_collisions"], 0)
        self.assertGreater(summary["near_misses_prevented"], 0)
        self.assertGreater(summary["reroutes_executed"], 0)
        self.assertEqual(summary["parked_vehicles"], 3)

    # 16. Network and database failures (fail-secure entry & anti-entrapment safe egress)
    def test_16_network_and_database_failures_failsafe(self) -> None:
        cust = self.eco.parking.register_customer("FailSafe", "fs@example.de", "777")
        self.eco.parking.register_vehicle(cust.customer_id, "B-FS 10")
        arr_ok = self.eco.access.evaluate_arrival("B-FS 10")
        assert arr_ok.session is not None

        # Simulate network outage: Entry is held closed (FAILSAFE_HOLD), Exit grants safe egress!
        self.eco.access.network_outage = True
        arr_hold = self.eco.access.evaluate_arrival("B-FS 10")
        self.assertEqual(arr_hold.record.decision_status, AccessDecisionStatus.FAILSAFE_HOLD)

        dep_safe = self.eco.access.evaluate_departure(session_id=arr_ok.session.session_id)
        self.assertEqual(dep_safe.record.decision_status, AccessDecisionStatus.EMERGENCY_OVERRIDE)
        self.eco.access.network_outage = False

        # Simulate database outage: Entry is held closed, Exit grants safe egress!
        self.eco.db.set_simulated_outage(True)
        arr_db_hold = self.eco.access.evaluate_arrival("B-FS 10")
        self.assertEqual(arr_db_hold.record.decision_status, AccessDecisionStatus.FAILSAFE_HOLD)

        dep_db_safe = self.eco.access.evaluate_departure(session_id=arr_ok.session.session_id)
        self.assertEqual(dep_db_safe.record.decision_status, AccessDecisionStatus.EMERGENCY_OVERRIDE)
        self.eco.db.set_simulated_outage(False)

    # 17. Permission enforcement (RBAC)
    def test_17_rbac_permission_enforcement(self) -> None:
        # VIEWER cannot change space operational status or purge retention records
        with self.assertRaises(PermissionError):
            self.eco.parking.set_space_status(
                1, SpaceStatus.OUT_OF_SERVICE, actor_id="V1", actor_role=StaffRole.VIEWER
            )
        with self.assertRaises(PermissionError):
            self.eco.db.purge_expired_records(
                datetime.datetime.now(datetime.timezone.utc),
                access_logs_days=30,
                sensor_events_days=14,
                actor_id="V1",
                actor_role=StaffRole.VIEWER,
            )

        # OPERATOR can change space status
        sp = self.eco.parking.set_space_status(
            1, SpaceStatus.OUT_OF_SERVICE, actor_id="OP1", actor_role=StaffRole.OPERATOR
        )
        self.assertEqual(sp.status, SpaceStatus.OUT_OF_SERVICE)

    # 18. Data-retention behavior & GDPR right-to-erasure
    def test_18_data_retention_and_gdpr_erasure(self) -> None:
        cust = self.eco.parking.register_customer("GDPRUser", "gdpr@example.de", "888")
        self.eco.parking.register_vehicle(cust.customer_id, "B-GD 01")

        # Create an old access decision 45 days ago
        old_time = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=45)
        self.eco.access.evaluate_arrival("UNKNOWN-OLD", at_time=old_time)

        purge_res = self.eco.db.purge_expired_records(
            reference_time=datetime.datetime.now(datetime.timezone.utc),
            access_logs_days=30,
            sensor_events_days=14,
            actor_role=StaffRole.ADMIN,
        )
        self.assertGreaterEqual(purge_res["access_authorizations_deleted"], 1)

        # GDPR erasure scrubs PII
        erased = self.eco.db.anonymize_and_delete_customer(
            cust.customer_id, actor_id="ADM-1", actor_role=StaffRole.ADMIN
        )
        self.assertTrue(erased)
        scrubbed = self.eco.db.get_customer(cust.customer_id)
        self.assertEqual(scrubbed.name, "[REDACTED]")
        self.assertEqual(scrubbed.status, AccountStatus.DELETED)

    # 19. Explainable space allocation benchmark & time-aware demand forecasting
    def test_19_explainable_allocation_and_demand_forecasting(self) -> None:
        requests = []
        for i in range(6):
            c = self.eco.parking.register_customer(
                f"Bench{i}", f"b{i}@example.de", f"90{i}", requires_accessible=(i == 0)
            )
            v = self.eco.parking.register_vehicle(
                c.customer_id, f"B-BM {10 + i}", requires_ev_charging=(i in (1, 2))
            )
            requests.append((c, v))

        bench = self.eco.parking.allocator.benchmark_against_baseline(
            self.eco.db.list_spaces(), requests
        )
        self.assertGreaterEqual(bench["ev_match_rate_smart"], bench["ev_match_rate_baseline"])

        forecast = self.eco.analytics.evaluate_demand_forecasting()
        self.assertGreater(forecast.improvement_over_baseline_pct, 0.0)
        self.assertEqual(len(forecast.next_24h_predictions), 24)

    # 20. 12-Screen Desktop GUI initialization & theme toggle
    def test_20_gui_12_screens_initialization(self) -> None:
        import tkinter as tk
        from smartpark.gui.app import SmartParkDesktopApp

        root = tk.Tk()
        root.withdraw()
        try:
            app = SmartParkDesktopApp(root, ecosystem=self.eco)
            self.assertEqual(len(app.notebook.tabs()), 12)
            app.toggle_theme()
            self.assertEqual(app.theme_mode, "light")
            app._sim_step()
            app._handle_run_recommendation()
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
