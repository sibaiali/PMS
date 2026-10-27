"""Deterministic Multi-Agent Garage Simulator with Replay, Queueing, and PNG/Visual Rendering (Milestone 4 & 7)."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from smartpark.routing.grid_map import (
    CellType,
    GarageGridMap,
    NearMissEvent,
    calculate_safe_stopping_distance,
)


@dataclass
class SimVehicleEntity:
    """Moving or parked vehicle entity inside the simulated garage."""

    vehicle_id: str
    public_token: str
    pos: tuple[int, int]
    goal: tuple[int, int]
    target_space_id: int
    speed_mps: float = 3.8  # ~13.7 km/h indoor cruising speed
    state: str = "NAVIGATING_TO_BAY"  # QUEUED, NAVIGATING_TO_BAY, YIELDING_SAFETY, PARKED, DEPARTING, EXITED
    is_emergency: bool = False
    path: list[tuple[int, int]] = field(default_factory=list)
    yield_ticks: int = 0
    reroute_count: int = 0


@dataclass
class SimPedestrianEntity:
    """Moving pedestrian walking along walkways and crossings."""

    pedestrian_id: str
    pos: tuple[int, int]
    goal: tuple[int, int]
    speed_mps: float = 1.3
    state: str = "WALKING"
    path: list[tuple[int, int]] = field(default_factory=list)


@dataclass
class SimulationFrameSnapshot:
    """Immutable snapshot of a single simulation tick for deterministic replay."""

    tick: int
    vehicles: list[SimVehicleEntity]
    pedestrians: list[SimPedestrianEntity]
    queue_length: int
    blocked_cells: list[tuple[int, int]]
    near_misses_on_tick: list[NearMissEvent]
    active_collisions: int


class GarageSimulator:
    """Simulates vehicles, pedestrians, queues, blocked routes, and near-miss prevention."""

    def __init__(self, grid_map: GarageGridMap | None = None) -> None:
        self.grid_map = grid_map or GarageGridMap()
        self.tick: int = 0
        self.vehicles: dict[str, SimVehicleEntity] = {}
        self.entrance_queue: list[SimVehicleEntity] = []
        self.pedestrians: dict[str, SimPedestrianEntity] = {}
        self.near_misses: list[NearMissEvent] = []
        self.replay_history: list[SimulationFrameSnapshot] = []
        self.collision_count: int = 0
        self.max_queue_length_observed: int = 0

    def spawn_vehicle(
        self,
        vehicle_id: str,
        public_token: str,
        target_space_id: int,
        start_pos: tuple[int, int] | None = None,
        speed_mps: float = 3.8,
        is_emergency: bool = False,
    ) -> SimVehicleEntity:
        start = start_pos or self.grid_map.entrance_coord
        goal = self.grid_map.space_coords.get(target_space_id, (3, 2))
        path = self.grid_map.find_vehicle_path_astar(
            start=start,
            goal=goal,
            is_emergency_vehicle=is_emergency,
        )
        # Remove current starting cell from remaining steps
        if path and path[0] == start:
            path = path[1:]

        entity = SimVehicleEntity(
            vehicle_id=vehicle_id,
            public_token=public_token,
            pos=start,
            goal=goal,
            target_space_id=target_space_id,
            speed_mps=speed_mps,
            state="QUEUED" if start == self.grid_map.entrance_coord and self._is_cell_occupied(start) else "NAVIGATING_TO_BAY",
            is_emergency=is_emergency,
            path=path,
        )
        if entity.state == "QUEUED":
            if is_emergency:
                self.entrance_queue.insert(0, entity)
            else:
                self.entrance_queue.append(entity)
            self.max_queue_length_observed = max(
                self.max_queue_length_observed, len(self.entrance_queue)
            )
        else:
            self.vehicles[vehicle_id] = entity
        return entity

    def initiate_vehicle_departure(self, vehicle_id: str) -> bool:
        veh = self.vehicles.get(vehicle_id)
        if veh is None:
            return False
        veh.goal = self.grid_map.exit_coord
        veh.state = "DEPARTING"
        veh.speed_mps = 3.5
        path = self.grid_map.find_vehicle_path_astar(
            start=veh.pos,
            goal=veh.goal,
            is_emergency_vehicle=veh.is_emergency,
        )
        if path and path[0] == veh.pos:
            path = path[1:]
        veh.path = path
        return True

    def spawn_pedestrian(
        self,
        pedestrian_id: str,
        start_pos: tuple[int, int],
        goal_pos: tuple[int, int],
    ) -> SimPedestrianEntity:
        path = self.grid_map.find_pedestrian_path_astar(start_pos, goal_pos)
        if path and path[0] == start_pos:
            path = path[1:]
        ped = SimPedestrianEntity(
            pedestrian_id=pedestrian_id,
            pos=start_pos,
            goal=goal_pos,
            path=path,
        )
        self.pedestrians[pedestrian_id] = ped
        return ped

    def _is_cell_occupied(self, coord: tuple[int, int], exclude_vid: str | None = None) -> bool:
        for vid, v in self.vehicles.items():
            if vid != exclude_vid and v.state != "EXITED" and v.pos == coord:
                return True
        return False

    def step_tick(self) -> SimulationFrameSnapshot:
        """Advance the simulation by 1 discrete tick with proactive conflict prevention."""
        self.tick += 1
        tick_near_misses: list[NearMissEvent] = []

        # 1. Admit from entrance queue if entrance cell is clear
        if self.entrance_queue and not self._is_cell_occupied(self.grid_map.entrance_coord):
            next_v = self.entrance_queue.pop(0)
            next_v.state = "NAVIGATING_TO_BAY"
            next_v.pos = self.grid_map.entrance_coord
            path = self.grid_map.find_vehicle_path_astar(
                start=next_v.pos,
                goal=next_v.goal,
                is_emergency_vehicle=next_v.is_emergency,
            )
            next_v.path = path[1:] if (path and path[0] == next_v.pos) else path
            self.vehicles[next_v.vehicle_id] = next_v

        # 2. Advance pedestrians first (pedestrians have right-of-way at crossings)
        for ped in list(self.pedestrians.values()):
            if ped.state == "ARRIVED" or not ped.path:
                ped.state = "ARRIVED"
                continue
            next_cell = ped.path[0]
            # Do not step directly onto a cell already occupied by a car
            if not self._is_cell_occupied(next_cell):
                ped.pos = ped.path.pop(0)
                if ped.pos == ped.goal or not ped.path:
                    ped.state = "ARRIVED"

        # 3. Evaluate and advance active vehicles
        ped_tuples = [
            (p.pedestrian_id, p.pos, p.path)
            for p in self.pedestrians.values()
            if p.state != "ARRIVED"
        ]

        for vid, veh in list(self.vehicles.items()):
            if veh.state in ("PARKED", "EXITED"):
                continue

            if not veh.path:
                if veh.pos == veh.goal:
                    veh.state = "EXITED" if veh.goal == self.grid_map.exit_coord else "PARKED"
                    veh.speed_mps = 0.0
                continue

            next_step = veh.path[0]

            # Check if planned path was blocked by a newly blocked cell -> dynamic reroute
            if any(c in self.grid_map.blocked_cells for c in veh.path):
                occupied = {
                    v.pos for k, v in self.vehicles.items() if k != vid and v.state == "PARKED"
                }
                new_path = self.grid_map.find_vehicle_path_astar(
                    start=veh.pos,
                    goal=veh.goal,
                    is_emergency_vehicle=veh.is_emergency,
                    occupied_coords=occupied,
                )
                if new_path and len(new_path) > 1:
                    veh.path = new_path[1:]
                    veh.reroute_count += 1
                    next_step = veh.path[0]
                else:
                    # Route completely blocked; hold position safely
                    veh.state = "YIELDING_SAFETY"
                    veh.yield_ticks += 1
                    continue

            # Predict conflict with pedestrians or other moving vehicles
            other_veh_tuples = [
                (k, v.pos, v.path)
                for k, v in self.vehicles.items()
                if k != vid and v.state not in ("PARKED", "EXITED")
            ]
            conflict = self.grid_map.predict_conflict(
                tick=self.tick,
                vehicle_id=vid,
                vehicle_pos=veh.pos,
                vehicle_next_steps=veh.path,
                vehicle_speed_mps=max(2.5, veh.speed_mps),
                pedestrians=ped_tuples,
                other_vehicles=other_veh_tuples,
            )
            if conflict is not None:
                veh.state = "YIELDING_SAFETY"
                veh.yield_ticks += 1
                self.near_misses.append(conflict)
                tick_near_misses.append(conflict)
                continue

            # Emergency-route box-junction protection: never enter an EMERGENCY_ROUTE cell
            # unless the cell beyond it is also clear
            if (
                not veh.is_emergency
                and self.grid_map.grid.get(next_step) == CellType.EMERGENCY_ROUTE
                and len(veh.path) >= 2
                and self._is_cell_occupied(veh.path[1], exclude_vid=vid)
            ):
                veh.state = "YIELDING_SAFETY"
                veh.yield_ticks += 1
                continue

            if self._is_cell_occupied(next_step, exclude_vid=vid):
                veh.state = "YIELDING_SAFETY"
                veh.yield_ticks += 1
                continue

            # Safe to advance
            veh.pos = veh.path.pop(0)
            veh.state = "DEPARTING" if veh.goal == self.grid_map.exit_coord else "NAVIGATING_TO_BAY"
            # Automatically slow down at blind corners
            if self.grid_map.grid.get(veh.pos) == CellType.BLIND_CORNER:
                veh.speed_mps = 2.0
            else:
                veh.speed_mps = 3.8

            if veh.pos == veh.goal:
                veh.state = "EXITED" if veh.goal == self.grid_map.exit_coord else "PARKED"
                veh.speed_mps = 0.0

        # 4. Verify zero physical collisions occurred on this tick
        active_positions: list[tuple[int, int]] = [
            v.pos for v in self.vehicles.values() if v.state not in ("EXITED",)
        ]
        ped_positions: list[tuple[int, int]] = [
            p.pos for p in self.pedestrians.values() if p.state != "ARRIVED"
        ]
        collisions_this_tick = (len(active_positions) - len(set(active_positions))) + sum(
            1 for ppos in ped_positions if ppos in active_positions
        )
        self.collision_count += collisions_this_tick

        snapshot = SimulationFrameSnapshot(
            tick=self.tick,
            vehicles=copy.deepcopy(list(self.vehicles.values())),
            pedestrians=copy.deepcopy(list(self.pedestrians.values())),
            queue_length=len(self.entrance_queue),
            blocked_cells=sorted(self.grid_map.blocked_cells),
            near_misses_on_tick=copy.deepcopy(tick_near_misses),
            active_collisions=collisions_this_tick,
        )
        self.replay_history.append(snapshot)
        return snapshot

    def run_deterministic_demo_scenario(self, steps: int = 18) -> dict[str, Any]:
        """Run a reproducible multi-vehicle & pedestrian safety scenario for testing and replay."""
        self.vehicles.clear()
        self.pedestrians.clear()
        self.entrance_queue.clear()
        self.near_misses.clear()
        self.replay_history.clear()
        self.tick = 0
        self.collision_count = 0

        # Spawn 3 vehicles heading to spaces A-01 (1), A-04 (4), and B-03 (9)
        self.spawn_vehicle("VEH-SIM-01", "PVT-ALPHA01", target_space_id=4, start_pos=(1, 4))
        self.spawn_vehicle("VEH-SIM-02", "PVT-BRAVO02", target_space_id=9, start_pos=(0, 4))
        self.spawn_vehicle("VEH-SIM-03", "PVT-CHARLIE", target_space_id=1, start_pos=(0, 4))

        # Spawn a pedestrian walking across the crosswalk at (2, 4) toward the lobby (1, 1)
        self.spawn_pedestrian("PED-SIM-01", start_pos=(2, 5), goal_pos=(1, 1))

        # Introduce a temporary obstacle at (5, 4) on tick 3 to trigger dynamic A* rerouting
        for step_idx in range(steps):
            if step_idx == 2:
                self.grid_map.set_cell_blocked(5, 4, True)
            self.step_tick()

        self.grid_map.set_cell_blocked(5, 4, False)
        stop_calc = calculate_safe_stopping_distance(3.8)
        return {
            "ticks_executed": self.tick,
            "total_collisions": self.collision_count,
            "near_misses_prevented": len(self.near_misses),
            "reroutes_executed": sum(v.reroute_count for v in self.vehicles.values()),
            "parked_vehicles": sum(1 for v in self.vehicles.values() if v.state == "PARKED"),
            "stopping_distance_model_m": stop_calc.total_stopping_distance_m,
            "replay_frames_recorded": len(self.replay_history),
        }

    def export_snapshot_png(
        self,
        output_path: str | Path,
        snapshot: SimulationFrameSnapshot | None = None,
    ) -> Path:
        """Render a high-contrast visual diagram of the simulated garage to a PNG file using Pillow."""
        from PIL import Image, ImageDraw

        snap = snapshot or (self.replay_history[-1] if self.replay_history else self.step_tick())
        cell_px = 48
        legend_h = 90
        img_w = self.grid_map.width * cell_px
        img_h = self.grid_map.height * cell_px + legend_h

        img = Image.new("RGB", (img_w, img_h), (24, 28, 36))
        draw = ImageDraw.Draw(img)

        palette = {
            CellType.LANE: (44, 51, 64),
            CellType.PARKING_BAY: (35, 77, 102),
            CellType.ACCESSIBLE_BAY: (38, 98, 145),
            CellType.EV_BAY: (30, 110, 85),
            CellType.EMERGENCY_BAY: (130, 45, 55),
            CellType.PEDESTRIAN_PATH: (68, 76, 92),
            CellType.PEDESTRIAN_CROSSING: (110, 118, 135),
            CellType.BLIND_CORNER: (135, 95, 35),
            CellType.ENTRANCE: (40, 130, 90),
            CellType.EXIT: (95, 75, 145),
            CellType.EMERGENCY_ROUTE: (95, 45, 55),
            CellType.OBSTACLE: (18, 20, 26),
        }

        space_by_coord = {coord: sid for sid, coord in self.grid_map.space_coords.items()}

        for (gx, gy), ctype in self.grid_map.grid.items():
            x0, y0 = gx * cell_px, gy * cell_px
            x1, y1 = x0 + cell_px - 2, y0 + cell_px - 2
            fill = palette.get(ctype, (44, 51, 64))
            if (gx, gy) in snap.blocked_cells:
                fill = (180, 50, 50)
            draw.rectangle([x0, y0, x1, y1], fill=fill, outline=(32, 37, 46))

            if (gx, gy) in space_by_coord:
                sid = space_by_coord[(gx, gy)]
                draw.text((x0 + 4, y0 + 4), f"S{sid:02d}", fill=(220, 230, 242))
            elif ctype == CellType.PEDESTRIAN_CROSSING:
                for stripe in range(x0 + 4, x1 - 4, 8):
                    draw.rectangle([stripe, y0 + 6, stripe + 3, y1 - 6], fill=(215, 220, 230))
            elif ctype == CellType.BLIND_CORNER:
                draw.text((x0 + 6, y0 + 16), "BLIND", fill=(255, 220, 140))

        # Draw planned A* paths
        for veh in snap.vehicles:
            if veh.path:
                pts = [(veh.pos[0] * cell_px + 24, veh.pos[1] * cell_px + 24)] + [
                    (px * cell_px + 24, py * cell_px + 24) for px, py in veh.path
                ]
                if len(pts) >= 2:
                    draw.line(pts, fill=(96, 175, 255), width=3)

        # Draw vehicles
        for veh in snap.vehicles:
            if veh.state == "EXITED":
                continue
            vx, vy = veh.pos
            x0, y0 = vx * cell_px + 7, vy * cell_px + 7
            x1, y1 = (vx + 1) * cell_px - 9, (vy + 1) * cell_px - 9
            v_color = (
                (245, 170, 50)
                if veh.state == "YIELDING_SAFETY"
                else (75, 200, 120)
                if veh.state == "PARKED"
                else (70, 155, 245)
            )
            draw.rectangle([x0, y0, x1, y1], fill=v_color, outline=(255, 255, 255), width=2)
            draw.text((x0 + 4, y0 + 9), veh.vehicle_id[-2:], fill=(15, 20, 28))

        # Draw pedestrians
        for ped in snap.pedestrians:
            if ped.state == "ARRIVED":
                continue
            px, py = ped.pos
            cx, cy = px * cell_px + 24, py * cell_px + 24
            draw.ellipse([cx - 10, cy - 10, cx + 10, cy + 10], fill=(255, 215, 70), outline=(255, 255, 255), width=2)
            draw.text((cx - 4, cy - 5), "P", fill=(20, 20, 20))

        # Footer status banner
        by0 = self.grid_map.height * cell_px
        draw.rectangle([0, by0, img_w, img_h], fill=(16, 20, 26))
        draw.text(
            (14, by0 + 12),
            f"SMARTPARK GUARDIAN AI — LIVE GARAGE SIMULATION (Tick #{snap.tick}) [SIMULATED DATA]",
            fill=(235, 240, 248),
        )
        draw.text(
            (14, by0 + 36),
            f"Active Vehicles: {len(snap.vehicles)} | Queue: {snap.queue_length} | "
            f"Near-Misses Prevented: {len(self.near_misses)} | Collisions: {self.collision_count}",
            fill=(165, 185, 210),
        )
        draw.text(
            (14, by0 + 60),
            "Legend: Blue=Navigating Vehicle  Amber=Yielding for Safety  Green=Parked  Yellow Circle=Pedestrian  Stripes=Crosswalk",
            fill=(135, 150, 175),
        )

        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        img.save(out, format="PNG")
        return out
