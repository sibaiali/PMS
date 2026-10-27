# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Legacy-compatible Smart Parking Management System entrypoint (Milestone 1).

This module preserves the original classes and interactive CLI menu from the
initial `pms.py` repository while fixing critical runtime bugs identified in
Milestone 1:
1. `DynamicPricing.calculate_fee` no longer crashes with `AttributeError` on
   `datetime.timedelta.start` / `.end` and uses `Decimal` arithmetic internally.
2. `AnalyticsEngine.generate_occupancy_report1` and `generate_revenue_report1`
   support optional `start_time` / `end_time` parameters and include
   `license_plate` so `main()` options 8 and 9 no longer raise `TypeError` or
   `KeyError`.
3. `ParkingLot.vacate_space` only decrements `occupied_spaces` when a space was
   actually occupied, records the completed session and payment, and prevents
   negative occupancy counts.
4. `AnalyticsEngine` revenue reports include completed parking sessions that
   have already vacated their space.
5. `Reservation` validates `end_time > start_time` and detects overlapping
   reservation intervals on a space, and `park_vehicle` respects active
   reservations belonging to other users.
6. Domain methods (`add_vehicle`, `register_user`, `park_vehicle`,
   `make_reservation`) accept direct parameters for programmatic/tested usage
   while falling back to interactive `input()` when called without arguments.
"""

from __future__ import annotations

import datetime
import sys
from decimal import Decimal, ROUND_HALF_UP
from typing import Any


class Vehicle:
    """Represents a customer vehicle."""

    def __init__(self, license_plate: str, owner: User) -> None:
        cleaned = license_plate.strip().upper()
        if not cleaned:
            raise ValueError("License plate cannot be empty.")
        self.license_plate: str = cleaned
        self.owner: User = owner
        self.entry_time: datetime.datetime | None = None
        self.exit_time: datetime.datetime | None = None


class User:
    """Represents a registered parking customer."""

    def __init__(self, name: str, email: str, phone: str) -> None:
        if not name or not name.strip():
            raise ValueError("User name cannot be empty.")
        self.name: str = name.strip()
        self.email: str = email.strip()
        self.phone: str = phone.strip()
        self.vehicles: list[Vehicle] = []

    def add_vehicle(self, license_plate: str | None = None) -> Vehicle:
        if license_plate is None:
            license_plate = input("Enter your vehicle's license plate number: ")
        cleaned = license_plate.strip().upper()
        if any(v.license_plate == cleaned for v in self.vehicles):
            raise ValueError(f"Vehicle {cleaned} is already registered to {self.name}.")
        vehicle = Vehicle(cleaned, self)
        self.vehicles.append(vehicle)
        print(f"Vehicle with license plate {vehicle.license_plate} has been added for {self.name}.")
        return vehicle


class Reservation:
    """Represents a time-bounded parking space reservation."""

    def __init__(
        self,
        user2: User,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> None:
        if end_time <= start_time:
            raise ValueError("Reservation end_time must be after start_time.")
        self.user: User = user2
        self.start_time: datetime.datetime = start_time
        self.end_time: datetime.datetime = end_time
        self.payment: Payment | None = None

    def overlaps(self, start_time: datetime.datetime, end_time: datetime.datetime) -> bool:
        """Return True if [start_time, end_time) overlaps this reservation."""
        return start_time < self.end_time and end_time > self.start_time


class ParkingSpace:
    """Represents a single parking space in the facility."""

    def __init__(self, space_id: int) -> None:
        self.space_id: int = space_id
        self.occupied: bool = False
        self.vehicle: Vehicle | None = None
        self.reservation: Reservation | None = None
        self.reservations: list[Reservation] = []

    def is_reserved_for_other(
        self,
        user: User,
        at_time: datetime.datetime | None = None,
    ) -> bool:
        check_time = at_time or datetime.datetime.now()
        active_res = list(self.reservations)
        if self.reservation and self.reservation not in active_res:
            active_res.append(self.reservation)
        for res in active_res:
            if res.start_time <= check_time <= res.end_time and res.user != user:
                return True
        return False

    def has_reservation_conflict(
        self,
        start_time: datetime.datetime,
        end_time: datetime.datetime,
    ) -> bool:
        active_res = list(self.reservations)
        if self.reservation and self.reservation not in active_res:
            active_res.append(self.reservation)
        return any(res.overlaps(start_time, end_time) for res in active_res)

    def occupy(
        self,
        vehicle: Vehicle,
        entry_time: datetime.datetime | None = None,
    ) -> bool:
        if not self.occupied:
            self.occupied = True
            self.vehicle = vehicle
            vehicle.entry_time = entry_time or datetime.datetime.now()
            vehicle.exit_time = None
            print(f"Space {self.space_id} occupied by {vehicle.license_plate}")
            return True
        print(f"Space {self.space_id} is already occupied.")
        return False

    def vacate(self, exit_time: datetime.datetime | None = None) -> Vehicle | None:
        if self.occupied and self.vehicle is not None:
            vacated_vehicle = self.vehicle
            vacated_vehicle.exit_time = exit_time or datetime.datetime.now()
            print(f"Space {self.space_id} vacated by {vacated_vehicle.license_plate}")
            self.occupied = False
            self.vehicle = None
            return vacated_vehicle
        print(f"Space {self.space_id} is already vacant.")
        return None

    def reserve(self, reservation: Reservation) -> bool:
        if self.occupied:
            print(f"Space {self.space_id} is already occupied and cannot be reserved.")
            return False
        if self.has_reservation_conflict(reservation.start_time, reservation.end_time):
            print(f"Space {self.space_id} already has a conflicting reservation.")
            return False
        self.reservation = reservation
        self.reservations.append(reservation)
        print(f"Space {self.space_id} reserved for {reservation.user.name}")
        return True


class DynamicPricing:
    """Calculates parking fees with peak-hour awareness and Decimal precision."""

    def __init__(
        self,
        base_rate: Decimal | float | int = Decimal("5.00"),
        peak_multiplier: Decimal | float | int = Decimal("2.00"),
        peak_hours: list[tuple[int, int]] | None = None,
    ) -> None:
        self.base_rate: Decimal = Decimal(str(base_rate))
        self.peak_multiplier: Decimal = Decimal(str(peak_multiplier))
        self.peak_hours: list[tuple[int, int]] = peak_hours or [(9, 17)]

    def calculate_fee(
        self,
        duration: datetime.timedelta,
        entry_time: datetime.datetime | None = None,
        exit_time: datetime.datetime | None = None,
    ) -> Decimal:
        total_seconds = max(0.0, duration.total_seconds())
        total_hours = Decimal(str(total_seconds)) / Decimal("3600")
        fee = total_hours * self.base_rate

        # Check peak hours safely if entry_time / exit_time are provided or attached
        start_dt = entry_time or getattr(duration, "start", None)
        end_dt = exit_time or getattr(duration, "end", None)
        if start_dt is not None and end_dt is not None:
            for start_hour, end_hour in self.peak_hours:
                if start_dt.hour < end_hour and end_dt.hour >= start_hour:
                    fee *= self.peak_multiplier
                    break

        return fee.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


class Payment:
    """Represents a completed parking fee transaction."""

    def __init__(
        self,
        amount: Decimal | float,
        timestamp: datetime.datetime | None = None,
        status: str = "Completed",
    ) -> None:
        self.amount: Decimal = Decimal(str(amount)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        self.timestamp: datetime.datetime = timestamp or datetime.datetime.now()
        self.status: str = status


class ParkingLot:
    """Manages parking spaces, users, reservations, sessions, and payments."""

    def __init__(self, capacity: int) -> None:
        if capacity <= 0:
            raise ValueError("Parking lot capacity must be greater than zero.")
        self.capacity: int = capacity
        self.spaces: list[ParkingSpace] = [ParkingSpace(i) for i in range(1, capacity + 1)]
        self.occupied_spaces: int = 0
        self.users: list[User] = []
        self.pricing_model: DynamicPricing = DynamicPricing()
        self.completed_sessions: list[dict[str, Any]] = []
        self.payments: list[Payment] = []

    def park_vehicle(
        self,
        user_name: str | None = None,
        license_plate: str | None = None,
        entry_time: datetime.datetime | None = None,
    ) -> ParkingSpace | None:
        if not self.users:
            print("No registered users found. Please register first.")
            return None

        if user_name is None:
            user_name = input("Enter your name: ")
        user3 = next((u for u in self.users if u.name == user_name), None)

        if not user3:
            print("User not found. Please register first.")
            return None

        if not user3.vehicles:
            print("You have no vehicles associated with your account. Please add a vehicle first.")
            return None

        if license_plate is not None:
            cleaned = license_plate.strip().upper()
            vehicle = next((v for v in user3.vehicles if v.license_plate == cleaned), None)
            if vehicle is None:
                print(f"Vehicle {cleaned} not found for user {user3.name}.")
                return None
        else:
            vehicle = user3.vehicles[0]

        # Prevent parking the same vehicle in multiple spaces simultaneously
        if any(s.occupied and s.vehicle and s.vehicle.license_plate == vehicle.license_plate for s in self.spaces):
            print(f"Vehicle {vehicle.license_plate} is already parked.")
            return None

        now = entry_time or datetime.datetime.now()

        # First prefer a space explicitly reserved by this user, then any unreserved vacant space
        candidate_spaces = [
            s for s in self.spaces
            if not s.occupied and not s.is_reserved_for_other(user3, now)
        ]
        candidate_spaces.sort(
            key=lambda s: 0 if (s.reservation and s.reservation.user == user3) else 1
        )

        for space in candidate_spaces:
            if space.occupy(vehicle, entry_time=now):
                self.occupied_spaces = sum(1 for s in self.spaces if s.occupied)
                return space

        print("Parking lot is full.")
        return None

    def vacate_space(
        self,
        space_id: int,
        exit_time: datetime.datetime | None = None,
    ) -> Payment | None:
        if not (1 <= space_id <= self.capacity):
            raise ValueError(f"Invalid space_id {space_id}; must be between 1 and {self.capacity}.")
        space = self.spaces[space_id - 1]
        if not space.occupied or space.vehicle is None:
            space.vacate(exit_time=exit_time)
            return None

        entry_dt = space.vehicle.entry_time
        vacated_vehicle = space.vacate(exit_time=exit_time)
        self.occupied_spaces = sum(1 for s in self.spaces if s.occupied)

        if vacated_vehicle is not None and entry_dt is not None and vacated_vehicle.exit_time is not None:
            payment = self.process_payment(vacated_vehicle)
            self.completed_sessions.append({
                "space_id": space.space_id,
                "license_plate": vacated_vehicle.license_plate,
                "entry_time": entry_dt,
                "exit_time": vacated_vehicle.exit_time,
                "amount": payment.amount,
            })
            return payment
        return None

    def get_available_spaces(self) -> int:
        return self.capacity - self. get_occupied_spaces()

    def get_occupied_spaces(self) -> int:
        self.occupied_spaces = sum(1 for s in self.spaces if s.occupied)
        return self.occupied_spaces

    def register_user(
        self,
        name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
    ) -> User:
        if name is None:
            name = input("Enter your name: ")
        if email is None:
            email = input("Enter your email: ")
        if phone is None:
            phone = input("Enter your phone number: ")
        user2 = User(name, email, phone)
        self.users.append(user2)
        print(f"{user2.name} has been registered as a user.")
        return user2

    def make_reservation(
        self,
        user2: User,
        start_time: datetime.datetime | str | None = None,
        end_time: datetime.datetime | str | None = None,
    ) -> Reservation | None:
        if start_time is None:
            start_time = input("Enter the start time for your reservation (YYYY-MM-DD HH:MM): ")
        if end_time is None:
            end_time = input("Enter the end time for your reservation (YYYY-MM-DD HH:MM): ")
        if isinstance(start_time, str):
            start_time = datetime.datetime.strptime(start_time, "%Y-%m-%d %H:%M")
        if isinstance(end_time, str):
            end_time = datetime.datetime.strptime(end_time, "%Y-%m-%d %H:%M")

        reservation = Reservation(user2, start_time, end_time)
        for space in self.spaces:
            if not space.occupied and not space.has_reservation_conflict(start_time, end_time):
                space.reserve(reservation)
                return reservation
        print("No available spaces for reservation.")
        return None

    def process_payment(self, vehicle: Vehicle) -> Payment:
        if vehicle.entry_time is None or vehicle.exit_time is None:
            raise ValueError("Vehicle must have both entry_time and exit_time to process payment.")
        duration = vehicle.exit_time - vehicle.entry_time
        amount = self.pricing_model.calculate_fee(
            duration,
            entry_time=vehicle.entry_time,
            exit_time=vehicle.exit_time,
        )
        payment = Payment(amount)
        self.payments.append(payment)
        print(f"Payment of {amount} processed for {vehicle.license_plate}")
        return payment


class AnalyticsEngine:
    """Generates occupancy and revenue reports across active and completed sessions."""

    def __init__(self, parking_lot: ParkingLot) -> None:
        self.parking_lot: ParkingLot = parking_lot

    def generate_occupancy_report(self) -> list[dict[str, Any]]:
        occupancy_data: list[dict[str, Any]] = []
        for space in self.parking_lot.spaces:
            if space.vehicle:
                occupancy_data.append({
                    "space_id": space.space_id,
                    "license_plate": space.vehicle.license_plate,
                    "entry_time": space.vehicle.entry_time,
                    "exit_time": space.vehicle.exit_time,
                })
        return occupancy_data

    def generate_revenue_report(self) -> Decimal:
        revenue = sum((s["amount"] for s in self.parking_lot.completed_sessions), Decimal("0.00"))
        for space in self.parking_lot.spaces:
            if space.vehicle and space.vehicle.exit_time and space.vehicle.entry_time:
                duration = space.vehicle.exit_time - space.vehicle.entry_time
                amount = self.parking_lot.pricing_model.calculate_fee(
                    duration,
                    entry_time=space.vehicle.entry_time,
                    exit_time=space.vehicle.exit_time,
                )
                revenue += amount
        return revenue

    def generate_occupancy_report1(
        self,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
    ) -> list[dict[str, Any]]:
        occupancy_data: list[dict[str, Any]] = []
        for space in self.parking_lot.spaces:
            if space.vehicle and space.vehicle.entry_time:
                entry_time = space.vehicle.entry_time
                exit_time = space.vehicle.exit_time or datetime.datetime.now()
                if (start_time is None or entry_time >= start_time) and (
                    end_time is None or exit_time <= end_time
                ):
                    occupancy_data.append({
                        "space_id": space.space_id,
                        "license_plate": space.vehicle.license_plate,
                        "entry_time": entry_time,
                        "exit_time": exit_time,
                    })
        return occupancy_data

    def generate_revenue_report1(
        self,
        start_time: datetime.datetime | None = None,
        end_time: datetime.datetime | None = None,
    ) -> Decimal:
        revenue = Decimal("0.00")
        for session in self.parking_lot.completed_sessions:
            exit_dt = session["exit_time"]
            if (start_time is None or exit_dt >= start_time) and (
                end_time is None or exit_dt <= end_time
            ):
                revenue += session["amount"]
        for space in self.parking_lot.spaces:
            if space.vehicle and space.vehicle.entry_time and space.vehicle.exit_time:
                entry_time = space.vehicle.entry_time
                exit_time = space.vehicle.exit_time
                if (start_time is None or exit_time >= start_time) and (
                    end_time is None or exit_time <= end_time
                ):
                    duration = exit_time - entry_time
                    amount = self.parking_lot.pricing_model.calculate_fee(
                        duration,
                        entry_time=entry_time,
                        exit_time=exit_time,
                    )
                    revenue += amount
        return revenue


def main() -> None:
    if "--gui" in sys.argv:
        from smartpark.gui.app import launch_app
        launch_app()
        return

    print("Welcome to the Parking Management System!")
    while True:
        try:
            capacity = int(input("Enter the capacity of the parking lot: "))
            if capacity > 0:
                break
            print("Capacity must be a positive integer.")
        except ValueError:
            print("Invalid input. Please enter a positive integer.")

    parking_lot = ParkingLot(capacity)

    while True:
        print("\nSelect an option:")
        print("1. Register as a user")
        print("2. Add a vehicle")
        print("3. Park a vehicle")
        print("4. Vacate a parking space")
        print("5. Make a reservation")
        print("6. View available spaces")
        print("7. View occupied spaces")
        print("8. Generate occupancy report")
        print("9. Generate revenue report")
        print("0. Exit")

        try:
            choice = int(input("Enter your choice: "))
        except ValueError:
            print("Invalid choice. Please enter a number between 0 and 9.")
            continue

        if choice == 1:
            parking_lot.register_user()
        elif choice == 2:
            if not parking_lot.users:
                print("No registered users found. Please register first.")
            else:
                user_name = input("Enter your name: ")
                selected_user = next((u for u in parking_lot.users if u.name == user_name), None)
                if selected_user:
                    try:
                        selected_user.add_vehicle()
                    except ValueError as exc:
                        print(f"Error: {exc}")
                else:
                    print("User not found. Please register first.")
        elif choice == 3:
            parking_lot.park_vehicle()
        elif choice == 4:
            while True:
                try:
                    space_id = int(input("Enter the space ID to vacate: "))
                    break
                except ValueError:
                    print("Invalid input. Please enter a valid space ID (integer).")

            if 1 <= space_id <= capacity:
                parking_lot.vacate_space(space_id)
            else:
                print("Invalid space ID. Please enter a space ID between 1 and", capacity)

        elif choice == 5:
            if not parking_lot.users:
                print("No registered users found. Please register first.")
            else:
                user_name = input("Enter your name: ")
                selected_user = next((u for u in parking_lot.users if u.name == user_name), None)
                if selected_user:
                    try:
                        parking_lot.make_reservation(selected_user)
                    except ValueError as exc:
                        print(f"Error: {exc}")
                else:
                    print("User not found. Please register first.")
        elif choice == 6:
            print("Available spaces:", parking_lot.get_available_spaces())
        elif choice == 7:
            print("Occupied spaces:", parking_lot.get_occupied_spaces())
        elif choice == 8:
            analytics_engine = AnalyticsEngine(parking_lot)
            occupancy_report = analytics_engine.generate_occupancy_report1()
            print("\nOccupancy Report:")
            for data in occupancy_report:
                print(
                    f"Space ID: {data['space_id']}, License Plate: {data['license_plate']}, Entry Time: {data['entry_time']}"
                )
        elif choice == 9:
            analytics_engine = AnalyticsEngine(parking_lot)
            revenue = analytics_engine.generate_revenue_report1()
            print(f"\nRevenue Report:\nTotal Revenue: {revenue}")

        elif choice == 0:
            break


if __name__ == "__main__":
    main()
