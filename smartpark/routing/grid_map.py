"""Garage Grid Map, A* Pathfinding, Safe Stopping-Distance Model, and Conflict Prediction (Section 9)."""

from __future__ import annotations

import datetime
import heapq
import math
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class CellType(str, Enum):
    LANE = "LANE"
    PARKING_BAY = "PARKING_BAY"
    ACCESSIBLE_BAY = "ACCESSIBLE_BAY"
    EV_BAY = "EV_BAY"
    EMERGENCY_BAY = "EMERGENCY_BAY"
    PEDESTRIAN_PATH = "PEDESTRIAN_PATH"
    PEDESTRIAN_CROSSING = "PEDESTRIAN_CROSSING"
    BLIND_CORNER = "BLIND_CORNER"
    ENTRANCE = "ENTRANCE"
    EXIT = "EXIT"
    EMERGENCY_ROUTE = "EMERGENCY_ROUTE"
    OBSTACLE = "OBSTACLE"


@dataclass(frozen=True)
class StoppingDistanceResult:
    """Simulated kinematic stopping distance with conservative safety margin.

    NOTE: This is an educational/architectural simulation model only. It does NOT
    validate real automotive braking hardware and NEVER controls a real vehicle.
    """

    speed_mps: float
    reaction_time_s: float
    friction_coeff: float
    safety_margin_m: float
    reaction_distance_m: float
    braking_distance_m: float
    total_stopping_distance_m: float
    disclaimer: str = (
        "SIMULATION MODEL ONLY: Does not validate real vehicle braking or control real hardware."
    )


def calculate_safe_stopping_distance(
    speed_mps: float,
    reaction_time_s: float = 0.9,
    friction_coeff: float = 0.55,  # Conservative indoor concrete garage assumption (wet/dusty)
    safety_margin_m: float = 1.8,
) -> StoppingDistanceResult:
    """Calculate conservative stopping distance in meters for indoor garage speeds."""
    v = max(0.0, float(speed_mps))
    g = 9.81
    mu = max(0.1, float(friction_coeff))
    d_react = v * max(0.1, reaction_time_s)
    d_brake = (v * v) / (2.0 * mu * g)
    total = d_react + d_brake + max(0.0, safety_margin_m)
    return StoppingDistanceResult(
        speed_mps=round(v, 2),
        reaction_time_s=reaction_time_s,
        friction_coeff=mu,
        safety_margin_m=safety_margin_m,
        reaction_distance_m=round(d_react, 2),
        braking_distance_m=round(d_brake, 2),
        total_stopping_distance_m=round(total, 2),
    )


@dataclass
class NearMissEvent:
    """Recorded when predicted trajectories of a vehicle and pedestrian/vehicle intersect within safety envelope."""

    near_miss_id: str
    tick: int
    grid_x: int
    grid_y: int
    cell_type: CellType
    vehicle_id: str
    other_entity_id: str
    separation_distance_m: float
    required_stopping_distance_m: float
    preventive_action: str
    is_simulated: bool = True
    timestamp: datetime.datetime = field(
        default_factory=lambda: datetime.datetime.now(datetime.timezone.utc)
    )


class GarageGridMap:
    """16x12 grid representation of the SmartPark facility with A* routing and safety zones."""

    CELL_SIZE_METERS: float = 3.5

    def __init__(self, width: int = 16, height: int = 12) -> None:
        self.width = width
        self.height = height
        self.grid: dict[tuple[int, int], CellType] = {}
        self.space_coords: dict[int, tuple[int, int]] = {}
        self.blocked_cells: set[tuple[int, int]] = set()
        self.entrance_coord: tuple[int, int] = (0, 4)
        self.exit_coord: tuple[int, int] = (15, 8)
        self.lobby_coord: tuple[int, int] = (1, 1)
        self._build_default_map()

    def _build_default_map(self) -> None:
        # Initialize all cells as LANE first, then carve out structure
        for x in range(self.width):
            for y in range(self.height):
                self.grid[(x, y)] = CellType.LANE

        # Outer walls / structural columns
        for x in range(self.width):
            self.grid[(x, 0)] = CellType.OBSTACLE
            self.grid[(x, self.height - 1)] = CellType.OBSTACLE
        for y in range(self.height):
            self.grid[(0, y)] = CellType.OBSTACLE
            self.grid[(self.width - 1, y)] = CellType.OBSTACLE

        # Entrance and Exit portals
        self.grid[self.entrance_coord] = CellType.ENTRANCE
        self.grid[self.exit_coord] = CellType.EXIT

        # Dedicated pedestrian walkway along y=1 and x=2
        for x in range(1, self.width - 1):
            self.grid[(x, 1)] = CellType.PEDESTRIAN_PATH
        for y in range(1, self.height - 1):
            self.grid[(2, y)] = CellType.PEDESTRIAN_PATH

        # Pedestrian crossings where vehicle lanes (y=3, y=4, y=7, y=8) cross x=2 and x=8
        for cy in (3, 4, 7, 8):
            self.grid[(2, cy)] = CellType.PEDESTRIAN_CROSSING
            self.grid[(8, cy)] = CellType.PEDESTRIAN_CROSSING

        # Blind corners at turning pillars
        for bx, by in ((6, 3), (12, 3), (6, 7), (12, 7)):
            self.grid[(bx, by)] = CellType.BLIND_CORNER

        # Protected emergency route along x=14
        for y in range(2, self.height - 1):
            self.grid[(14, y)] = CellType.EMERGENCY_ROUTE

        # 16 Parking Bays matching ParkingEngine.seed_default_facility_spaces()
        bay_definitions = [
            # Zone A (y=2)
            (1, (3, 2), CellType.ACCESSIBLE_BAY),
            (2, (5, 2), CellType.ACCESSIBLE_BAY),
            (3, (7, 2), CellType.EV_BAY),
            (4, (9, 2), CellType.EV_BAY),
            (5, (11, 2), CellType.PARKING_BAY),
            (6, (13, 2), CellType.PARKING_BAY),
            # Zone B (y=6)
            (7, (3, 6), CellType.PARKING_BAY),
            (8, (5, 6), CellType.PARKING_BAY),
            (9, (7, 6), CellType.PARKING_BAY),
            (10, (9, 6), CellType.EV_BAY),
            (11, (11, 6), CellType.PARKING_BAY),
            (12, (13, 6), CellType.PARKING_BAY),
            # Zone C (y=10)
            (13, (3, 10), CellType.PARKING_BAY),
            (14, (5, 10), CellType.PARKING_BAY),
            (15, (7, 10), CellType.PARKING_BAY),
            (16, (13, 10), CellType.EMERGENCY_BAY),
        ]
        # Structural islands between bays on rows y=2, 6, 10
        for row_y in (2, 6, 10):
            for col_x in (4, 6, 10, 12):
                self.grid[(col_x, row_y)] = CellType.OBSTACLE

        for space_id, coord, ctype in bay_definitions:
            self.grid[coord] = ctype
            self.space_coords[space_id] = coord

    def set_cell_blocked(self, x: int, y: int, blocked: bool = True) -> None:
        if (x, y) in self.grid and self.grid[(x, y)] == CellType.EMERGENCY_ROUTE and blocked:
            raise ValueError("Emergency route cells are protected and cannot be permanently blocked.")
        if blocked:
            self.blocked_cells.add((x, y))
        else:
            self.blocked_cells.discard((x, y))

    def is_drivable_for_vehicle(
        self,
        coord: tuple[int, int],
        goal: tuple[int, int],
        is_emergency_vehicle: bool = False,
        occupied_coords: set[tuple[int, int]] | None = None,
    ) -> bool:
        if coord not in self.grid:
            return False
        if coord in self.blocked_cells:
            return False
        if occupied_coords and coord in occupied_coords and coord != goal:
            return False

        ctype = self.grid[coord]
        if ctype in (CellType.OBSTACLE, CellType.PEDESTRIAN_PATH):
            return False
        if ctype == CellType.EMERGENCY_BAY and not is_emergency_vehicle and coord != goal:
            return False
        if ctype in (
            CellType.PARKING_BAY,
            CellType.ACCESSIBLE_BAY,
            CellType.EV_BAY,
            CellType.EMERGENCY_BAY,
        ):
            # A vehicle may only enter a parking bay if that bay is its destination or start
            return coord == goal
        return True

    def movement_cost(
        self,
        coord: tuple[int, int],
        is_emergency_vehicle: bool = False,
    ) -> float:
        ctype = self.grid[coord]
        if ctype == CellType.BLIND_CORNER:
            return 2.4  # Penalize blind corners so A* prefers clear-sightline lanes when equivalent
        if ctype == CellType.PEDESTRIAN_CROSSING:
            return 1.8  # Slight penalty for crossing pedestrian crosswalks
        if ctype == CellType.EMERGENCY_ROUTE and not is_emergency_vehicle:
            return 4.5  # Keep standard vehicles off the emergency corridor unless no other lane exists
        return 1.0

    def find_vehicle_path_astar(
        self,
        start: tuple[int, int],
        goal: tuple[int, int],
        is_emergency_vehicle: bool = False,
        occupied_coords: set[tuple[int, int]] | None = None,
    ) -> list[tuple[int, int]]:
        """Compute optimal, safety-weighted vehicle route using A* search."""
        if start == goal:
            return [start]

        open_heap: list[tuple[float, int, tuple[int, int]]] = []
        counter = 0
        heapq.heappush(open_heap, (0.0, counter, start))
        came_from: dict[tuple[int, int], tuple[int, int]] = {}
        g_score: dict[tuple[int, int], float] = {start: 0.0}

        def heuristic(a: tuple[int, int], b: tuple[int, int]) -> float:
            return float(abs(a[0] - b[0]) + abs(a[1] - b[1]))

        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current == goal:
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                path.reverse()
                return path

            cx, cy = current
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                neighbor = (nx, ny)
                if not self.is_drivable_for_vehicle(
                    neighbor,
                    goal=goal,
                    is_emergency_vehicle=is_emergency_vehicle,
                    occupied_coords=occupied_coords,
                ):
                    continue
                tentative_g = g_score[current] + self.movement_cost(
                    neighbor, is_emergency_vehicle=is_emergency_vehicle
                )
                if tentative_g < g_score.get(neighbor, float("inf")):
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    f_score = tentative_g + heuristic(neighbor, goal)
                    counter += 1
                    heapq.heappush(open_heap, (f_score, counter, neighbor))

        return []

    def find_pedestrian_path_astar(
        self,
        start: tuple[int, int],
        goal: tuple[int, int],
    ) -> list[tuple[int, int]]:
        """Route a pedestrian along designated walkways and crosswalks."""
        if start == goal:
            return [start]

        open_heap: list[tuple[float, int, tuple[int, int]]] = [(0.0, 0, start)]
        counter = 0
        came_from: dict[tuple[int, int], tuple[int, int]] = {}
        g_score: dict[tuple[int, int], float] = {start: 0.0}

        while open_heap:
            _, _, current = heapq.heappop(open_heap)
            if current == goal:
                path = [current]
                while current in came_from:
                    current = came_from[current]
                    path.append(current)
                path.reverse()
                return path

            cx, cy = current
            for nx, ny in ((cx + 1, cy), (cx - 1, cy), (cx, cy + 1), (cx, cy - 1)):
                neighbor = (nx, ny)
                if neighbor not in self.grid:
                    continue
                ctype = self.grid[neighbor]
                if ctype == CellType.OBSTACLE and neighbor != goal:
                    continue
                # Strongly prefer PEDESTRIAN_PATH and PEDESTRIAN_CROSSING
                step_cost = (
                    1.0
                    if ctype in (CellType.PEDESTRIAN_PATH, CellType.PEDESTRIAN_CROSSING)
                    else 3.5
                )
                tentative_g = g_score[current] + step_cost
                if tentative_g < g_score.get(neighbor, float("inf")):
                    came_from[neighbor] = current
                    g_score[neighbor] = tentative_g
                    f = tentative_g + abs(nx - goal[0]) + abs(ny - goal[1])
                    counter += 1
                    heapq.heappush(open_heap, (f, counter, neighbor))
        return []

    def predict_conflict(
        self,
        tick: int,
        vehicle_id: str,
        vehicle_pos: tuple[int, int],
        vehicle_next_steps: list[tuple[int, int]],
        vehicle_speed_mps: float,
        pedestrians: list[tuple[str, tuple[int, int], list[tuple[int, int]]]],
        other_vehicles: list[tuple[str, tuple[int, int], list[tuple[int, int]]]],
    ) -> NearMissEvent | None:
        """Predict whether a vehicle's upcoming trajectory intersects a pedestrian or vehicle within stopping distance."""
        stop_info = calculate_safe_stopping_distance(vehicle_speed_mps)
        lookahead_cells = max(
            1, int(math.ceil(stop_info.total_stopping_distance_m / self.CELL_SIZE_METERS))
        )
        monitored_path = vehicle_next_steps[:lookahead_cells]
        if not monitored_path:
            return None

        # 1. Check pedestrian trajectory intersection first (highest priority)
        for ped_id, ped_pos, ped_next in pedestrians:
            ped_zone = {ped_pos} | set(ped_next[:2])
            for step_coord in monitored_path:
                dist_cells = math.hypot(step_coord[0] - ped_pos[0], step_coord[1] - ped_pos[1])
                if step_coord in ped_zone or dist_cells <= 1.2:
                    sep_m = round(
                        math.hypot(vehicle_pos[0] - ped_pos[0], vehicle_pos[1] - ped_pos[1])
                        * self.CELL_SIZE_METERS,
                        2,
                    )
                    return NearMissEvent(
                        near_miss_id=f"NM-{uuid.uuid4().hex[:8].upper()}",
                        tick=tick,
                        grid_x=step_coord[0],
                        grid_y=step_coord[1],
                        cell_type=self.grid.get(step_coord, CellType.LANE),
                        vehicle_id=vehicle_id,
                        other_entity_id=ped_id,
                        separation_distance_m=sep_m,
                        required_stopping_distance_m=stop_info.total_stopping_distance_m,
                        preventive_action="VEHICLE_YIELDED_FOR_PEDESTRIAN",
                    )

        # 2. Check vehicle-to-vehicle conflict (especially at blind corners or lane merges)
        for other_vid, other_pos, other_next in other_vehicles:
            if other_vid == vehicle_id:
                continue
            other_zone = {other_pos} | set(other_next[:1])
            for step_coord in monitored_path[:2]:
                if step_coord in other_zone:
                    sep_m = round(
                        math.hypot(vehicle_pos[0] - other_pos[0], vehicle_pos[1] - other_pos[1])
                        * self.CELL_SIZE_METERS,
                        2,
                    )
                    ctype = self.grid.get(step_coord, CellType.LANE)
                    action = (
                        "BLIND_CORNER_YIELD_AND_HOLD"
                        if ctype == CellType.BLIND_CORNER
                        else "INTER_VEHICLE_SPACING_HOLD"
                    )
                    return NearMissEvent(
                        near_miss_id=f"NM-{uuid.uuid4().hex[:8].upper()}",
                        tick=tick,
                        grid_x=step_coord[0],
                        grid_y=step_coord[1],
                        cell_type=ctype,
                        vehicle_id=vehicle_id,
                        other_entity_id=other_vid,
                        separation_distance_m=sep_m,
                        required_stopping_distance_m=stop_info.total_stopping_distance_m,
                        preventive_action=action,
                    )

        return None
