# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Automated tests for Milestone 1: Legacy `pms.py` bug fixes and compatibility."""

from __future__ import annotations

import datetime
import unittest
from decimal import Decimal
from unittest.mock import patch

import pms


class TestMilestone1LegacyPMS(unittest.TestCase):
    """Verifies that all bugs identified in the original `pms.py` are resolved."""

    def test_user_and_vehicle_registration_and_duplicate_protection(self) -> None:
        lot = pms.ParkingLot(capacity=3)
        user = lot.register_user(name="Ali Sibai", email="ali@example.com", phone="+49123456")
        v1 = user.add_vehicle("b-sp 2026")
        self.assertEqual(v1.license_plate, "B-SP 2026")
        with self.assertRaises(ValueError):
            user.add_vehicle("B-SP 2026")
        with self.assertRaises(ValueError):
            user.add_vehicle("   ")

    def test_dynamic_pricing_no_longer_crashes_on_timedelta(self) -> None:
        pricing = pms.DynamicPricing(base_rate=Decimal("5.00"), peak_multiplier=Decimal("2.00"))
        duration = datetime.timedelta(hours=2)
        # Original pms.py raised AttributeError: 'datetime.timedelta' object has no attribute 'start'
        off_peak_fee = pricing.calculate_fee(
            duration,
            entry_time=datetime.datetime(2026, 10, 27, 19, 0),
            exit_time=datetime.datetime(2026, 10, 27, 21, 0),
        )
        self.assertEqual(off_peak_fee, Decimal("10.00"))

        peak_fee = pricing.calculate_fee(
            duration,
            entry_time=datetime.datetime(2026, 10, 27, 10, 0),
            exit_time=datetime.datetime(2026, 10, 27, 12, 0),
        )
        self.assertEqual(peak_fee, Decimal("20.00"))

        # Even without entry_time/exit_time, passing pure timedelta must not crash
        plain_fee = pricing.calculate_fee(duration)
        self.assertEqual(plain_fee, Decimal("10.00"))

    def test_vacate_space_prevents_negative_occupied_count_and_records_revenue(self) -> None:
        lot = pms.ParkingLot(capacity=2)
        # Vacating an already vacant space must not decrement occupied_spaces below 0
        lot.vacate_space(1)
        self.assertEqual(lot.get_occupied_spaces(), 0)
        self.assertEqual(lot.get_available_spaces(), 2)

        user = lot.register_user("Lena", "lena@example.com", "0170111222")
        user.add_vehicle("M-EV 100")

        t_in = datetime.datetime(2026, 10, 27, 10, 0)
        t_out = datetime.datetime(2026, 10, 27, 11, 30)
        space = lot.park_vehicle("Lena", entry_time=t_in)
        self.assertIsNotNone(space)
        self.assertEqual(lot.get_occupied_spaces(), 1)

        # Occupancy report without arguments works (used by CLI option 8)
        analytics = pms.AnalyticsEngine(lot)
        occ_report = analytics.generate_occupancy_report1()
        self.assertEqual(len(occ_report), 1)
        self.assertEqual(occ_report[0]["license_plate"], "M-EV 100")

        payment = lot.vacate_space(space.space_id, exit_time=t_out)
        self.assertIsNotNone(payment)
        self.assertEqual(payment.amount, Decimal("15.00"))  # 1.5h * 5.00 * 2 (peak)
        self.assertEqual(lot.get_occupied_spaces(), 0)

        # Revenue report after vacating space accurately reflects completed session
        self.assertEqual(analytics.generate_revenue_report(), Decimal("15.00"))
        self.assertEqual(analytics.generate_revenue_report1(), Decimal("15.00"))

    def test_reservation_conflict_and_protection(self) -> None:
        lot = pms.ParkingLot(capacity=1)
        u1 = lot.register_user("User1", "u1@example.com", "111")
        u2 = lot.register_user("User2", "u2@example.com", "222")
        u1.add_vehicle("HH-AA 1")
        u2.add_vehicle("HH-BB 2")

        res1 = lot.make_reservation(
            u1,
            datetime.datetime(2026, 10, 27, 12, 0),
            datetime.datetime(2026, 10, 27, 14, 0),
        )
        self.assertIsNotNone(res1)

        # Overlapping reservation on the only space must fail
        res2 = lot.make_reservation(
            u2,
            datetime.datetime(2026, 10, 27, 13, 0),
            datetime.datetime(2026, 10, 27, 15, 0),
        )
        self.assertIsNone(res2)

        # User2 attempting to park during User1's reservation window is denied
        parked_u2 = lot.park_vehicle("User2", entry_time=datetime.datetime(2026, 10, 27, 12, 30))
        self.assertIsNone(parked_u2)

        # User1 can park in their own reserved space
        parked_u1 = lot.park_vehicle("User1", entry_time=datetime.datetime(2026, 10, 27, 12, 30))
        self.assertIsNotNone(parked_u1)

    def test_cli_menu_end_to_end(self) -> None:
        inputs = [
            "2",                   # Capacity
            "1",                   # Register user
            "Clara",               # Name
            "clara@example.com",   # Email
            "0151000",             # Phone
            "2",                   # Add vehicle
            "Clara",               # User name
            "B-CL 99",             # Plate
            "3",                   # Park vehicle
            "Clara",               # User name
            "8",                   # Occupancy report
            "4",                   # Vacate space
            "1",                   # Space ID 1
            "9",                   # Revenue report
            "0",                   # Exit
        ]
        with patch("builtins.input", side_effect=inputs):
            pms.main()


if __name__ == "__main__":
    unittest.main()
