# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""SmartPark Guardian AI — 12-Screen Restrained Desktop Application (Section 13)."""

from __future__ import annotations

import datetime
from decimal import Decimal
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any

from smartpark.core.models import (
    CredentialType,
    SensorEventType,
    SessionStatus,
    StaffRole,
    VehicleSize,
)
from smartpark.routing.grid_map import CellType
from smartpark.system import SmartParkEcosystem


THEMES: dict[str, dict[str, str]] = {
    "dark": {
        "bg": "#12161F",
        "surface": "#1B2230",
        "fg": "#E8EEF5",
        "muted": "#9AA8BC",
        "accent": "#3B82F6",
        "success": "#22C55E",
        "warning": "#F59E0B",
        "danger": "#EF4444",
        "canvas_bg": "#161B26",
    },
    "light": {
        "bg": "#F4F6F9",
        "surface": "#FFFFFF",
        "fg": "#111827",
        "muted": "#4B5563",
        "accent": "#2563EB",
        "success": "#15803D",
        "warning": "#B45309",
        "danger": "#B91C1C",
        "canvas_bg": "#E5E7EB",
    },
}


class SmartParkDesktopApp:
    """Restrained, high-contrast 12-screen Tkinter management and customer demonstration suite."""

    def __init__(self, root: tk.Tk, ecosystem: SmartParkEcosystem | None = None) -> None:
        self.root = root
        self.eco = ecosystem or SmartParkEcosystem()
        self.eco.seed_demonstration_scenario()

        self.theme_mode = "dark"
        self.current_role = StaffRole.ADMIN
        self.sim_playing = False
        self.replay_index = max(0, len(self.eco.simulator.replay_history) - 1)
        self._last_challenge_id: str | None = None

        self.root.title("SmartPark Guardian AI — Barrier-Free Intelligent Parking Ecosystem")
        self.root.geometry("1360x860")
        self.root.minsize(1120, 740)

        self.style = ttk.Style(self.root)
        if "clam" in self.style.theme_names():
            self.style.theme_use("clam")

        self._build_header()
        self.notebook = ttk.Notebook(self.root)
        self.notebook.pack(fill=tk.BOTH, expand=True, padx=14, pady=(4, 4))

        self.footer_frame = ttk.Frame(self.root, padding=(14, 6))
        self.footer_frame.pack(fill=tk.X, side=tk.BOTTOM)
        ttk.Label(
            self.footer_frame,
            text="© 2026 Ali Sibai — All Rights Reserved / Alle Rechte vorbehalten (§ 69a UrhG) | Exclusive Intellectual Property of Ali Sibai (github.com/sibaiali/PMS)",
            font=("Segoe UI", 8, "bold"),
        ).pack(side=tk.LEFT)

        # Build all 12 required screens
        self.tab_map = ttk.Frame(self.notebook, padding=12)
        self.tab_reg = ttk.Frame(self.notebook, padding=12)
        self.tab_arrival = ttk.Frame(self.notebook, padding=12)
        self.tab_alloc = ttk.Frame(self.notebook, padding=12)
        self.tab_checkout = ttk.Frame(self.notebook, padding=12)
        self.tab_guardian = ttk.Frame(self.notebook, padding=12)
        self.tab_incidents = ttk.Frame(self.notebook, padding=12)
        self.tab_notifications = ttk.Frame(self.notebook, padding=12)
        self.tab_badge = ttk.Frame(self.notebook, padding=12)
        self.tab_analytics = ttk.Frame(self.notebook, padding=12)
        self.tab_health = ttk.Frame(self.notebook, padding=12)
        self.tab_settings = ttk.Frame(self.notebook, padding=12)

        screens = [
            (self.tab_map, "1. Live Garage Map"),
            (self.tab_reg, "2. Vehicle Registration"),
            (self.tab_arrival, "3. Barrier-Free Arrival"),
            (self.tab_alloc, "4. Space Recommendations"),
            (self.tab_checkout, "5. Session & Checkout"),
            (self.tab_guardian, "6. Guardian Shield & Key"),
            (self.tab_incidents, "7. Incident History"),
            (self.tab_notifications, "8. Notifications"),
            (self.tab_badge, "9. Privacy Badge"),
            (self.tab_analytics, "10. Analytics & AI"),
            (self.tab_health, "11. Health & Audit Logs"),
            (self.tab_settings, "12. Settings & Permissions"),
        ]
        for frame, title in screens:
            self.notebook.add(frame, text=title)

        self._init_tab_1_map()
        self._init_tab_2_registration()
        self._init_tab_3_arrival()
        self._init_tab_4_recommendations()
        self._init_tab_5_checkout()
        self._init_tab_6_guardian()
        self._init_tab_7_incidents()
        self._init_tab_8_notifications()
        self._init_tab_9_badge()
        self._init_tab_10_analytics()
        self._init_tab_11_health()
        self._init_tab_12_settings()

        self.apply_theme()
        self.refresh_all_views()

    def _build_header(self) -> None:
        self.header_frame = ttk.Frame(self.root, padding=(14, 10))
        self.header_frame.pack(fill=tk.X)

        title_lbl = ttk.Label(
            self.header_frame,
            text="SMARTPARK GUARDIAN AI",
            font=("Segoe UI", 15, "bold"),
        )
        title_lbl.pack(side=tk.LEFT)

        sim_badge = ttk.Label(
            self.header_frame,
            text="  [SIMULATION MODE — HARDWARE, SENSORS & PAYMENTS CLEARLY SIMULATED]  ",
            font=("Segoe UI", 9, "bold"),
        )
        sim_badge.pack(side=tk.LEFT, padx=16)

        ttk.Button(
            self.header_frame,
            text="Toggle Dark / Light",
            command=self.toggle_theme,
        ).pack(side=tk.RIGHT, padx=4)

        ttk.Button(
            self.header_frame,
            text="Refresh All Screens",
            command=self.refresh_all_views,
        ).pack(side=tk.RIGHT, padx=4)

        self.role_status_var = tk.StringVar(value=f"Active Role: {self.current_role.value}")
        ttk.Label(
            self.header_frame,
            textvariable=self.role_status_var,
            font=("Segoe UI", 10, "bold"),
        ).pack(side=tk.RIGHT, padx=12)

    def apply_theme(self) -> None:
        t = THEMES[self.theme_mode]
        self.root.configure(bg=t["bg"])
        self.style.configure(".", background=t["bg"], foreground=t["fg"], font=("Segoe UI", 10))
        self.style.configure("TFrame", background=t["bg"])
        self.style.configure("TLabel", background=t["bg"], foreground=t["fg"])
        self.style.configure(
            "TButton",
            background=t["surface"],
            foreground=t["fg"],
            padding=(10, 5),
        )
        self.style.map("TButton", background=[("active", t["accent"])])
        self.style.configure(
            "TNotebook",
            background=t["bg"],
            tabmargins=[2, 4, 2, 0],
        )
        self.style.configure(
            "TNotebook.Tab",
            background=t["surface"],
            foreground=t["fg"],
            padding=[10, 6],
            font=("Segoe UI", 9, "bold"),
        )
        self.style.map(
            "TNotebook.Tab",
            background=[("selected", t["accent"])],
            foreground=[("selected", "#FFFFFF")],
        )
        self.style.configure(
            "Treeview",
            background=t["surface"],
            fieldbackground=t["surface"],
            foreground=t["fg"],
            rowheight=26,
        )
        self.style.configure(
            "Treeview.Heading",
            background=t["bg"],
            foreground=t["fg"],
            font=("Segoe UI", 9, "bold"),
        )
        if hasattr(self, "map_canvas"):
            self.map_canvas.configure(bg=t["canvas_bg"])
            self._draw_garage_canvas()

    def toggle_theme(self) -> None:
        self.theme_mode = "light" if self.theme_mode == "dark" else "dark"
        self.apply_theme()

    # ------------------------------------------------------------------
    # 1. Live Garage Map & Accident Prevention Simulation
    # ------------------------------------------------------------------
    def _init_tab_1_map(self) -> None:
        ctrl = ttk.Frame(self.tab_map)
        ctrl.pack(fill=tk.X, pady=(0, 8))

        ttk.Button(ctrl, text="Step Simulation (+1 Tick)", command=self._sim_step).pack(side=tk.LEFT, padx=4)
        self.play_btn = ttk.Button(ctrl, text="Play Animation", command=self._sim_toggle_play)
        self.play_btn.pack(side=tk.LEFT, padx=4)
        ttk.Button(ctrl, text="Reset & Run Demo Scenario", command=self._sim_reset_demo).pack(side=tk.LEFT, padx=4)
        ttk.Button(ctrl, text="Toggle Obstacle at (5,4)", command=self._sim_toggle_obstacle).pack(side=tk.LEFT, padx=4)

        self.sim_status_var = tk.StringVar(value="Tick: 0 | Collisions: 0 | Near-Misses Prevented: 0")
        ttk.Label(ctrl, textvariable=self.sim_status_var, font=("Segoe UI", 10, "bold")).pack(
            side=tk.RIGHT, padx=8
        )

        body = ttk.Frame(self.tab_map)
        body.pack(fill=tk.BOTH, expand=True)

        self.map_canvas = tk.Canvas(body, width=780, height=580, highlightthickness=1)
        self.map_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        right_panel = ttk.Frame(body, width=360)
        right_panel.pack(side=tk.RIGHT, fill=tk.Y, padx=(12, 0))

        ttk.Label(right_panel, text="Replay Frame Selector:", font=("Segoe UI", 10, "bold")).pack(anchor=tk.W)
        self.replay_scale = tk.Scale(
            right_panel,
            from_=0,
            to=max(0, len(self.eco.simulator.replay_history) - 1),
            orient=tk.HORIZONTAL,
            command=self._on_replay_scrub,
        )
        self.replay_scale.pack(fill=tk.X, pady=(2, 10))

        ttk.Label(
            right_panel,
            text="Near-Miss & Accident Prevention Log:",
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor=tk.W)
        self.near_miss_text = tk.Text(right_panel, width=44, height=24, font=("Consolas", 9))
        self.near_miss_text.pack(fill=tk.BOTH, expand=True, pady=4)

    def _sim_step(self) -> None:
        self.eco.simulator.step_tick()
        self.replay_index = len(self.eco.simulator.replay_history) - 1
        self.replay_scale.configure(to=max(0, self.replay_index))
        self.replay_scale.set(self.replay_index)
        self._draw_garage_canvas()

    def _sim_toggle_play(self) -> None:
        self.sim_playing = not self.sim_playing
        self.play_btn.configure(text="Pause Animation" if self.sim_playing else "Play Animation")
        if self.sim_playing:
            self._sim_loop()

    def _sim_loop(self) -> None:
        if not self.sim_playing:
            return
        self._sim_step()
        self.root.after(450, self._sim_loop)

    def _sim_reset_demo(self) -> None:
        self.sim_playing = False
        self.play_btn.configure(text="Play Animation")
        self.eco.simulator.run_deterministic_demo_scenario(steps=16)
        self.replay_index = len(self.eco.simulator.replay_history) - 1
        self.replay_scale.configure(to=max(0, self.replay_index))
        self.replay_scale.set(self.replay_index)
        self._draw_garage_canvas()

    def _sim_toggle_obstacle(self) -> None:
        currently_blocked = (5, 4) in self.eco.grid_map.blocked_cells
        self.eco.grid_map.set_cell_blocked(5, 4, not currently_blocked)
        self._sim_step()

    def _on_replay_scrub(self, val: str) -> None:
        self.replay_index = int(float(val))
        self._draw_garage_canvas()

    def _draw_garage_canvas(self) -> None:
        self.map_canvas.delete("all")
        history = self.eco.simulator.replay_history
        if not history:
            return
        idx = min(max(0, self.replay_index), len(history) - 1)
        snap = history[idx]

        cell_px = 45
        color_map = {
            CellType.LANE: "#2A3241",
            CellType.PARKING_BAY: "#1E4E68",
            CellType.ACCESSIBLE_BAY: "#226493",
            CellType.EV_BAY: "#1A6B54",
            CellType.EMERGENCY_BAY: "#7F2935",
            CellType.PEDESTRIAN_PATH: "#3F4859",
            CellType.PEDESTRIAN_CROSSING: "#647087",
            CellType.BLIND_CORNER: "#855B22",
            CellType.ENTRANCE: "#247A53",
            CellType.EXIT: "#59438E",
            CellType.EMERGENCY_ROUTE: "#5C2A35",
            CellType.OBSTACLE: "#12151C",
        }
        space_lookup = {coord: s for s in self.eco.db.list_spaces() for coord in [(s.grid_x, s.grid_y)]}

        for (gx, gy), ctype in self.eco.grid_map.grid.items():
            x0, y0 = gx * cell_px + 10, gy * cell_px + 10
            x1, y1 = x0 + cell_px - 2, y0 + cell_px - 2
            fill = color_map.get(ctype, "#2A3241")
            if (gx, gy) in snap.blocked_cells:
                fill = "#B91C1C"
            sp = space_lookup.get((gx, gy))
            if sp and sp.status.value == "OCCUPIED":
                fill = "#15803D"
            self.map_canvas.create_rectangle(x0, y0, x1, y1, fill=fill, outline="#1C222D")
            if sp:
                self.map_canvas.create_text(
                    x0 + 21, y0 + 14, text=sp.code, fill="#E8EEF5", font=("Segoe UI", 8, "bold")
                )
            elif ctype == CellType.PEDESTRIAN_CROSSING:
                self.map_canvas.create_text(
                    x0 + 21, y0 + 21, text="X-WALK", fill="#E8EEF5", font=("Segoe UI", 7)
                )
            elif ctype == CellType.BLIND_CORNER:
                self.map_canvas.create_text(
                    x0 + 21, y0 + 21, text="BLIND", fill="#FDE68A", font=("Segoe UI", 7, "bold")
                )

        # Draw A* planned vehicle trajectories
        for veh in snap.vehicles:
            if veh.path and veh.state != "EXITED":
                pts: list[int] = [veh.pos[0] * cell_px + 32, veh.pos[1] * cell_px + 32]
                for px, py in veh.path:
                    pts.extend([px * cell_px + 32, py * cell_px + 32])
                if len(pts) >= 4:
                    self.map_canvas.create_line(*pts, fill="#60A5FA", width=2, dash=(4, 2))

        # Draw vehicles
        for veh in snap.vehicles:
            if veh.state == "EXITED":
                continue
            vx, vy = veh.pos
            x0, y0 = vx * cell_px + 16, vy * cell_px + 16
            x1, y1 = x0 + cell_px - 14, y0 + cell_px - 14
            vfill = (
                "#F59E0B"
                if veh.state == "YIELDING_SAFETY"
                else "#22C55E"
                if veh.state == "PARKED"
                else "#3B82F6"
            )
            self.map_canvas.create_rectangle(x0, y0, x1, y1, fill=vfill, outline="#FFFFFF", width=2)
            self.map_canvas.create_text(
                (x0 + x1) // 2,
                (y0 + y1) // 2,
                text=veh.vehicle_id[-2:],
                fill="#0F172A",
                font=("Segoe UI", 8, "bold"),
            )

        # Draw pedestrians
        for ped in snap.pedestrians:
            if ped.state == "ARRIVED":
                continue
            px, py = ped.pos
            cx, cy = px * cell_px + 32, py * cell_px + 32
            self.map_canvas.create_oval(cx - 10, cy - 10, cx + 10, cy + 10, fill="#FACC15", outline="#FFFFFF", width=2)
            self.map_canvas.create_text(cx, cy, text="P", fill="#111827", font=("Segoe UI", 8, "bold"))

        self.sim_status_var.set(
            f"Replay Frame: {idx + 1}/{len(history)} (Tick #{snap.tick}) | "
            f"Queue: {snap.queue_length} | Near-Misses Prevented: {len(self.eco.simulator.near_misses)} | "
            f"Collisions: {self.eco.simulator.collision_count} [SIMULATED]"
        )

        self.near_miss_text.delete("1.0", tk.END)
        self.near_miss_text.insert(
            tk.END,
            "SAFETY ENGINE MODEL NOTE:\n"
            "Stopping distance uses conservative indoor concrete friction (mu=0.55).\n"
            "Does NOT validate real vehicle braking hardware.\n"
            + "-" * 42
            + "\n",
        )
        for nm in reversed(self.eco.simulator.near_misses[-15:]):
            self.near_miss_text.insert(
                tk.END,
                f"[Tick {nm.tick}] {nm.preventive_action}\n"
                f"  Loc: ({nm.grid_x},{nm.grid_y}) {nm.cell_type.value}\n"
                f"  Entities: {nm.vehicle_id} vs {nm.other_entity_id}\n"
                f"  Separation: {nm.separation_distance_m}m (Stop req: {nm.required_stopping_distance_m}m)\n\n",
            )

    # ------------------------------------------------------------------
    # 2. Vehicle & Customer Registration
    # ------------------------------------------------------------------
    def _init_tab_2_registration(self) -> None:
        top = ttk.Frame(self.tab_reg)
        top.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(top, text="Name:").grid(row=0, column=0, sticky=tk.W, padx=4, pady=4)
        self.reg_name = ttk.Entry(top, width=22)
        self.reg_name.grid(row=0, column=1, padx=4, pady=4)

        ttk.Label(top, text="Email:").grid(row=0, column=2, sticky=tk.W, padx=4, pady=4)
        self.reg_email = ttk.Entry(top, width=24)
        self.reg_email.grid(row=0, column=3, padx=4, pady=4)

        ttk.Label(top, text="Phone:").grid(row=0, column=4, sticky=tk.W, padx=4, pady=4)
        self.reg_phone = ttk.Entry(top, width=18)
        self.reg_phone.grid(row=0, column=5, padx=4, pady=4)

        self.reg_acc_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Accessible Permit", variable=self.reg_acc_var).grid(
            row=0, column=6, padx=6
        )

        ttk.Label(top, text="License Plate:").grid(row=1, column=0, sticky=tk.W, padx=4, pady=4)
        self.reg_plate = ttk.Entry(top, width=22)
        self.reg_plate.grid(row=1, column=1, padx=4, pady=4)

        ttk.Label(top, text="Vehicle Size:").grid(row=1, column=2, sticky=tk.W, padx=4, pady=4)
        self.reg_size = ttk.Combobox(
            top, values=[s.value for s in VehicleSize], state="readonly", width=18
        )
        self.reg_size.set(VehicleSize.STANDARD.value)
        self.reg_size.grid(row=1, column=3, padx=4, pady=4)

        self.reg_ev_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(top, text="Requires EV Charging", variable=self.reg_ev_var).grid(
            row=1, column=4, columnspan=2, sticky=tk.W, padx=4
        )

        ttk.Button(
            top,
            text="Enroll Customer & Vehicle",
            command=self._handle_enroll_customer_vehicle,
        ).grid(row=1, column=6, padx=6, pady=4)

        cols = ("customer_id", "public_alias", "vehicle_id", "public_token", "masked_plate", "size", "ev", "digital_key")
        self.reg_tree = ttk.Treeview(self.tab_reg, columns=cols, show="headings", height=18)
        for c in cols:
            self.reg_tree.heading(c, text=c.replace("_", " ").title())
            self.reg_tree.column(c, width=140)
        self.reg_tree.pack(fill=tk.BOTH, expand=True)

    def _handle_enroll_customer_vehicle(self) -> None:
        try:
            cust = self.eco.parking.register_customer(
                name=self.reg_name.get(),
                email=self.reg_email.get(),
                phone=self.reg_phone.get() or "+49 170 0000000",
                requires_accessible=self.reg_acc_var.get(),
            )
            self.eco.parking.register_vehicle(
                customer_id=cust.customer_id,
                license_plate=self.reg_plate.get(),
                size=VehicleSize(self.reg_size.get()),
                requires_ev_charging=self.reg_ev_var.get(),
            )
            self.refresh_all_views()
            messagebox.showinfo("Enrollment Complete", f"Enrolled {cust.public_alias} with privacy-separated vehicle token.")
        except Exception as exc:
            messagebox.showerror("Enrollment Error", str(exc))

    # ------------------------------------------------------------------
    # 3. Barrier-Free Arrival Simulation
    # ------------------------------------------------------------------
    def _init_tab_3_arrival(self) -> None:
        frm = ttk.Frame(self.tab_arrival)
        frm.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(frm, text="Candidate License Plate:").pack(side=tk.LEFT, padx=4)
        self.arr_plate = ttk.Entry(frm, width=18)
        self.arr_plate.insert(0, "HH-MV 88")
        self.arr_plate.pack(side=tk.LEFT, padx=4)

        ttk.Label(frm, text="Credential:").pack(side=tk.LEFT, padx=4)
        self.arr_cred = ttk.Combobox(
            frm, values=[c.value for c in CredentialType], state="readonly", width=22
        )
        self.arr_cred.set(CredentialType.BLE_DIGITAL_KEY_SIM.value)
        self.arr_cred.pack(side=tk.LEFT, padx=4)

        self.arr_sec_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(frm, text="Secondary Credential Verified", variable=self.arr_sec_var).pack(
            side=tk.LEFT, padx=6
        )

        ttk.Button(
            frm,
            text="Simulate Barrier-Free Arrival",
            command=self._handle_simulate_arrival,
        ).pack(side=tk.LEFT, padx=8)

        ttk.Button(
            frm,
            text="Simulate Emergency Vehicle Entry",
            command=lambda: self._handle_simulate_arrival(emergency=True),
        ).pack(side=tk.LEFT, padx=4)

        self.arr_output = tk.Text(self.tab_arrival, height=24, font=("Consolas", 10))
        self.arr_output.pack(fill=tk.BOTH, expand=True)

    def _handle_simulate_arrival(self, emergency: bool = False) -> None:
        res = self.eco.access.evaluate_arrival(
            license_plate=self.arr_plate.get() if not emergency else None,
            credential_type=CredentialType(self.arr_cred.get()),
            secondary_credential_verified=self.arr_sec_var.get(),
            confidence=0.97,
            emergency_vehicle=emergency,
        )
        self.arr_output.insert(
            tk.END,
            f"[{res.record.timestamp.strftime('%H:%M:%S')}] Decision: {res.record.decision_status.value}\n"
            f"  Guidance : {res.guidance_message}\n"
            f"  Reason   : {res.record.reason}\n"
            f"  Fallback : {res.record.alternative_workflow or 'None (Barrier-Free Flow)'}\n"
            + ("-" * 72)
            + "\n",
        )
        self.arr_output.see(tk.END)
        self.refresh_all_views()

    # ------------------------------------------------------------------
    # 4. Intelligent Space Recommendations
    # ------------------------------------------------------------------
    def _init_tab_4_recommendations(self) -> None:
        top = ttk.Frame(self.tab_alloc)
        top.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(top, text="Evaluate Vehicle ID:").pack(side=tk.LEFT, padx=4)
        self.alloc_veh_combo = ttk.Combobox(top, state="readonly", width=28)
        self.alloc_veh_combo.pack(side=tk.LEFT, padx=4)

        ttk.Button(
            top,
            text="Run Explainable Recommendation vs. Baseline",
            command=self._handle_run_recommendation,
        ).pack(side=tk.LEFT, padx=8)

        self.alloc_text = tk.Text(self.tab_alloc, height=25, font=("Consolas", 10))
        self.alloc_text.pack(fill=tk.BOTH, expand=True)

    def _handle_run_recommendation(self) -> None:
        vehicles = self.eco.db.list_vehicles()
        if not vehicles:
            return
        sel = self.alloc_veh_combo.get()
        veh = next((v for v in vehicles if v.vehicle_id in sel), vehicles[0])
        cust = self.eco.db.get_customer(veh.customer_id)
        if not cust:
            return
        comp = self.eco.parking.allocator.recommend(
            spaces=self.eco.db.list_spaces(),
            customer=cust,
            vehicle=veh,
            active_reservations=self.eco.db.list_reservations(active_only=True),
        )
        self.alloc_text.delete("1.0", tk.END)
        self.alloc_text.insert(
            tk.END,
            f"=== EXPLAINABLE ALLOCATION REPORT FOR {veh.public_vehicle_token} ({veh.size.value}, EV={veh.requires_ev_charging}) ===\n"
            f"Summary: {comp.explanation_summary}\n"
            f"Smart Choice Walking Distance    : {comp.smart_walking_distance_m} m\n"
            f"Baseline Choice Walking Distance : {comp.baseline_walking_distance_m} m\n"
            f"Walking Distance Saved           : {comp.walking_distance_saved_m} m\n"
            + ("=" * 82)
            + "\n\nRANKED CANDIDATES:\n",
        )
        for c in comp.ranked_candidates:
            status_tag = f"SCORE {c.total_score:6.2f}" if c.eligible else f"INELIGIBLE ({c.rejection_reason})"
            self.alloc_text.insert(
                tk.END,
                f"• {c.space.code} (Zone {c.space.zone}, Max={c.space.max_size.value}, EV={c.space.has_charger}, Acc={c.space.is_accessible}): {status_tag}\n",
            )
            if c.eligible:
                self.alloc_text.insert(tk.END, f"    Sub-scores: {c.sub_scores}\n")
                self.alloc_text.insert(tk.END, f"    Reasons   : {' | '.join(c.reasons)}\n")

    # ------------------------------------------------------------------
    # 5. Parking Session and Checkout
    # ------------------------------------------------------------------
    def _init_tab_5_checkout(self) -> None:
        top = ttk.Frame(self.tab_checkout)
        top.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(top, text="Session ID:").pack(side=tk.LEFT, padx=4)
        self.chk_session_combo = ttk.Combobox(top, state="readonly", width=26)
        self.chk_session_combo.pack(side=tk.LEFT, padx=4)

        ttk.Button(
            top,
            text="Simulate Barrier-Free Departure & Checkout",
            command=self._handle_departure_checkout,
        ).pack(side=tk.LEFT, padx=8)

        self.chk_text = tk.Text(self.tab_checkout, height=24, font=("Consolas", 10))
        self.chk_text.pack(fill=tk.BOTH, expand=True)

    def _handle_departure_checkout(self) -> None:
        sid = self.chk_session_combo.get().split(" ")[0]
        if not sid:
            return
        res = self.eco.access.evaluate_departure(session_id=sid)
        self.chk_text.delete("1.0", tk.END)
        self.chk_text.insert(tk.END, f"{res.egress_message}\n\n")
        if res.receipt_text:
            self.chk_text.insert(tk.END, res.receipt_text + "\n")
        self.refresh_all_views()

    # ------------------------------------------------------------------
    # 6. Guardian Shield & Walk-Up Digital Key
    # ------------------------------------------------------------------
    def _init_tab_6_guardian(self) -> None:
        top = ttk.Frame(self.tab_guardian)
        top.pack(fill=tk.X, pady=(0, 8))

        ttk.Label(top, text="Protected Session:").pack(side=tk.LEFT, padx=4)
        self.grd_session_combo = ttk.Combobox(top, state="readonly", width=22)
        self.grd_session_combo.pack(side=tk.LEFT, padx=4)

        ttk.Label(top, text="Simulated Sensor Event:").pack(side=tk.LEFT, padx=4)
        self.grd_event_combo = ttk.Combobox(
            top, values=[e.value for e in SensorEventType], state="readonly", width=24
        )
        self.grd_event_combo.set(SensorEventType.PEDESTRIAN_PROXIMITY.value)
        self.grd_event_combo.pack(side=tk.LEFT, padx=4)

        self.grd_auth_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(top, text="Authenticated Sensor Source", variable=self.grd_auth_var).pack(
            side=tk.LEFT, padx=6
        )

        ttk.Button(top, text="Inject Sensor Event", command=self._handle_inject_sensor).pack(
            side=tk.LEFT, padx=6
        )

        key_bar = ttk.Frame(self.tab_guardian)
        key_bar.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(key_bar, text="Walk-Up Digital Key Prototype:", font=("Segoe UI", 10, "bold")).pack(
            side=tk.LEFT, padx=4
        )
        ttk.Button(
            key_bar,
            text="1. Simulate BLE Walk-Up Proximity (1.5m)",
            command=self._handle_ble_proximity,
        ).pack(side=tk.LEFT, padx=6)
        ttk.Button(
            key_bar,
            text="2. Confirm Biometric & Unlock Vehicle",
            command=self._handle_confirm_digital_key,
        ).pack(side=tk.LEFT, padx=6)

        self.grd_text = tk.Text(self.tab_guardian, height=22, font=("Consolas", 10))
        self.grd_text.pack(fill=tk.BOTH, expand=True)

    def _handle_inject_sensor(self) -> None:
        sid = self.grd_session_combo.get().split(" ")[0]
        if not sid:
            return
        evt, inc, ntf = self.eco.guardian.ingest_sensor_event(
            session_id=sid,
            event_type=SensorEventType(self.grd_event_combo.get()),
            source_authenticated=self.grd_auth_var.get(),
            payload={"simulated_via_gui": True},
        )
        self.grd_text.insert(
            tk.END,
            f"=== GUARDIAN SHIELD ASSESSMENT ({inc.incident_id}) ===\n"
            f"  Event Received   : {evt.event_type.value} (Authenticated={evt.source_authenticated})\n"
            f"  Classification   : {inc.classification} | Severity: {inc.severity.value}\n"
            f"  Expected Activity: {inc.is_expected} | Confidence: {inc.confidence:.2f}\n"
            f"  Triggered Rules  : {', '.join(inc.triggered_rules)}\n"
            f"  Explanation      : {inc.explanation}\n"
            f"  Recommended Resp : {inc.recommended_response}\n"
            f"  Notification     : {ntf.delivery_status if ntf else 'None'}\n\n",
        )
        self.grd_text.see(tk.END)
        self.refresh_all_views()

    def _handle_ble_proximity(self) -> None:
        vehicles = self.eco.db.list_vehicles()
        if not vehicles:
            return
        v = vehicles[0]
        ch = self.eco.digital_keys.detect_ble_proximity_and_issue_challenge(
            customer_id=v.customer_id,
            vehicle_id=v.vehicle_id,
            proximity_distance_m=1.5,
            requested_action="UNLOCK",
        )
        self._last_challenge_id = ch.challenge_id
        self.grd_text.insert(
            tk.END,
            f"[WALK-UP KEY] Proximity (1.5m) detected for {v.public_vehicle_token}.\n"
            f"  NOTE: Proximity alone NEVER unlocks the vehicle.\n"
            f"  Challenge Issued: {ch.challenge_id} (Nonce={ch.nonce[:10]}..., Expires={ch.expires_at.strftime('%H:%M:%S UTC')})\n\n",
        )
        self.grd_text.see(tk.END)

    def _handle_confirm_digital_key(self) -> None:
        if not self._last_challenge_id:
            messagebox.showwarning("Digital Key", "Trigger BLE Walk-Up Proximity first to obtain a challenge.")
            return
        ch = self.eco.digital_keys._challenges.get(self._last_challenge_id)
        if not ch:
            return
        try:
            sig = self.eco.digital_keys.sign_user_confirmation(ch, "BIOMETRIC_OK")
            out = self.eco.digital_keys.confirm_and_execute(
                challenge_id=ch.challenge_id,
                nonce=ch.nonce,
                user_confirmed=True,
                confirmation_signature=sig,
            )
            self.grd_text.insert(
                tk.END,
                f"[WALK-UP KEY CONFIRMED] {out['message']}\n  Verified Success: {out['verified_success']}\n\n",
            )
            self.grd_text.see(tk.END)
            self.refresh_all_views()
        except Exception as exc:
            messagebox.showerror("Digital Key Error", str(exc))

    # ------------------------------------------------------------------
    # 7. Incident History
    # ------------------------------------------------------------------
    def _init_tab_7_incidents(self) -> None:
        cols = ("incident_id", "session_id", "severity", "classification", "rules", "confidence", "explanation")
        self.inc_tree = ttk.Treeview(self.tab_incidents, columns=cols, show="headings", height=22)
        for c in cols:
            self.inc_tree.heading(c, text=c.replace("_", " ").title())
            self.inc_tree.column(c, width=165 if c != "explanation" else 340)
        self.inc_tree.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # 8. Intelligent Push Notifications
    # ------------------------------------------------------------------
    def _init_tab_8_notifications(self) -> None:
        cols = ("notification_id", "customer_id", "severity", "delivery_status", "title", "safe_next_action")
        self.ntf_tree = ttk.Treeview(self.tab_notifications, columns=cols, show="headings", height=22)
        for c in cols:
            self.ntf_tree.heading(c, text=c.replace("_", " ").title())
            self.ntf_tree.column(c, width=180 if c != "safe_next_action" else 340)
        self.ntf_tree.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # 9. Privacy-First Digital Parking Badge
    # ------------------------------------------------------------------
    def _init_tab_9_badge(self) -> None:
        top = ttk.Frame(self.tab_badge)
        top.pack(fill=tk.X, pady=(0, 8))

        ttk.Button(
            top,
            text="Generate Temporary Signed QR Badge for Active Session",
            command=self._handle_generate_badge,
        ).pack(side=tk.LEFT, padx=4)

        ttk.Button(
            top,
            text="Revoke Active Session Badge",
            command=self._handle_revoke_badge,
        ).pack(side=tk.LEFT, padx=8)

        self.badge_text = tk.Text(self.tab_badge, height=25, font=("Consolas", 10))
        self.badge_text.pack(fill=tk.BOTH, expand=True)

    def _handle_generate_badge(self) -> None:
        active = self.eco.db.list_sessions(SessionStatus.ACTIVE)
        self.badge_text.delete("1.0", tk.END)
        if not active:
            self.badge_text.insert(tk.END, "No active parking session — Privacy Badge automatically hidden.\n")
            return
        ses = active[0]
        veh = self.eco.db.get_vehicle_by_id(ses.vehicle_id)
        sp = self.eco.db.get_space(ses.space_id)
        if not veh or not sp:
            return
        try:
            cred = self.eco.badges.issue_badge(ses, veh, sp.code)
            valid, msg, _ = self.eco.badges.verify_badge_token(cred.signed_token)
            self.badge_text.insert(
                tk.END,
                f"=== PRIVACY-FIRST DIGITAL PARKING BADGE ===\n"
                f"Notice              : {cred.privacy_notice}\n"
                f"Badge ID            : {cred.public_badge_id}\n"
                f"Public Vehicle Token: {cred.public_vehicle_token} (Customer PII & Plate Hidden)\n"
                f"Assigned Bay        : {cred.space_code}\n"
                f"Expires At (UTC)    : {cred.expires_at.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                f"Rolling Slot        : {cred.rolling_window_slot} (Anti-Screenshot Replay Protection)\n"
                f"Server Verification : {valid} — {msg}\n\n"
                f"TEMPORARY QR MATRIX (SIMULATED):\n{cred.qr_ascii_preview}\n",
            )
        except Exception as exc:
            self.badge_text.insert(tk.END, f"Badge Hidden / Blocked: {exc}\n")

    def _handle_revoke_badge(self) -> None:
        active = self.eco.db.list_sessions(SessionStatus.ACTIVE)
        if not active:
            return
        self.eco.badges.revoke_badge_token(active[0].session_id, actor_id="GUI_USER", actor_role=self.current_role)
        self._handle_generate_badge()
        self.refresh_all_views()

    # ------------------------------------------------------------------
    # 10. Analytics & Evaluated Demand Forecasting
    # ------------------------------------------------------------------
    def _init_tab_10_analytics(self) -> None:
        self.ana_text = tk.Text(self.tab_analytics, height=28, font=("Consolas", 10))
        self.ana_text.pack(fill=tk.BOTH, expand=True)

    # ------------------------------------------------------------------
    # 11. System Health, Fail-Safe Controls & Audit Logs
    # ------------------------------------------------------------------
    def _init_tab_11_health(self) -> None:
        top = ttk.Frame(self.tab_health)
        top.pack(fill=tk.X, pady=(0, 8))

        self.net_outage_var = tk.BooleanVar(value=False)
        self.sensor_fail_var = tk.BooleanVar(value=False)
        self.pay_outage_var = tk.BooleanVar(value=False)

        ttk.Checkbutton(
            top,
            text="Simulate Network Outage (Fail-Secure Entry / Safe Egress)",
            variable=self.net_outage_var,
            command=self._sync_failsafe_toggles,
        ).pack(side=tk.LEFT, padx=6)

        ttk.Checkbutton(
            top,
            text="Simulate Entrance Sensor Fault",
            variable=self.sensor_fail_var,
            command=self._sync_failsafe_toggles,
        ).pack(side=tk.LEFT, padx=6)

        ttk.Checkbutton(
            top,
            text="Simulate Payment Gateway Outage",
            variable=self.pay_outage_var,
            command=self._sync_failsafe_toggles,
        ).pack(side=tk.LEFT, padx=6)

        cols = ("audit_id", "timestamp", "actor_id", "actor_role", "action", "target_entity", "details")
        self.aud_tree = ttk.Treeview(self.tab_health, columns=cols, show="headings", height=22)
        for c in cols:
            self.aud_tree.heading(c, text=c.replace("_", " ").title())
            self.aud_tree.column(c, width=160 if c != "details" else 340)
        self.aud_tree.pack(fill=tk.BOTH, expand=True)

    def _sync_failsafe_toggles(self) -> None:
        self.eco.access.network_outage = self.net_outage_var.get()
        self.eco.access.sensor_failure = self.sensor_fail_var.get()
        self.eco.payment_provider.provider_outage = self.pay_outage_var.get()

    # ------------------------------------------------------------------
    # 12. Settings, RBAC Permissions & Privacy Data Protection
    # ------------------------------------------------------------------
    def _init_tab_12_settings(self) -> None:
        frm = ttk.Frame(self.tab_settings)
        frm.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(frm, text="Active Staff Role (RBAC):").pack(side=tk.LEFT, padx=4)
        self.role_combo = ttk.Combobox(
            frm, values=[r.value for r in StaffRole], state="readonly", width=22
        )
        self.role_combo.set(self.current_role.value)
        self.role_combo.pack(side=tk.LEFT, padx=4)
        ttk.Button(frm, text="Apply Role", command=self._handle_change_role).pack(side=tk.LEFT, padx=6)

        ttk.Button(
            frm,
            text="Execute GDPR Data Retention Purge",
            command=self._handle_retention_purge,
        ).pack(side=tk.LEFT, padx=8)

        self.cfg_text = tk.Text(self.tab_settings, height=24, font=("Consolas", 10))
        self.cfg_text.pack(fill=tk.BOTH, expand=True)

    def _handle_change_role(self) -> None:
        self.current_role = StaffRole(self.role_combo.get())
        self.role_status_var.set(f"Active Role: {self.current_role.value}")

    def _handle_retention_purge(self) -> None:
        try:
            deleted = self.eco.db.purge_expired_records(
                reference_time=datetime.datetime.now(datetime.timezone.utc),
                access_logs_days=self.eco.config.retention_policy.access_logs_days,
                sensor_events_days=self.eco.config.retention_policy.sensor_events_days,
                actor_id="GUI_STAFF_OPERATOR",
                actor_role=self.current_role,
            )
            self.refresh_all_views()
            messagebox.showinfo("Retention Purge Executed", f"Purge result: {deleted}")
        except Exception as exc:
            messagebox.showerror("Permission Denied", str(exc))

    # ------------------------------------------------------------------
    # Master Refresh Across All 12 Screens
    # ------------------------------------------------------------------
    def refresh_all_views(self) -> None:
        self._draw_garage_canvas()

        # Tab 2: Vehicles & Customers
        for item in self.reg_tree.get_children():
            self.reg_tree.delete(item)
        customers = {c.customer_id: c for c in self.eco.db.list_customers()}
        vehicles = self.eco.db.list_vehicles()
        for v in vehicles:
            c = customers.get(v.customer_id)
            self.reg_tree.insert(
                "",
                tk.END,
                values=(
                    v.customer_id,
                    c.public_alias if c else "N/A",
                    v.vehicle_id,
                    v.public_vehicle_token,
                    v.license_plate_masked,
                    v.size.value,
                    v.requires_ev_charging,
                    v.enrolled_digital_key_id or "None",
                ),
            )

        # Tab 4 & 5 & 6 Combos
        veh_labels = [f"{v.vehicle_id} ({v.public_vehicle_token} / {v.license_plate_masked})" for v in vehicles]
        self.alloc_veh_combo["values"] = veh_labels
        if veh_labels and not self.alloc_veh_combo.get():
            self.alloc_veh_combo.set(veh_labels[0])

        active_sessions = self.eco.db.list_sessions(SessionStatus.ACTIVE)
        ses_labels = [f"{s.session_id} (Space {s.space_id}, Veh {s.vehicle_id})" for s in active_sessions]
        self.chk_session_combo["values"] = ses_labels
        self.grd_session_combo["values"] = ses_labels
        if ses_labels:
            self.chk_session_combo.set(ses_labels[0])
            self.grd_session_combo.set(ses_labels[0])

        # Tab 7: Incidents
        for item in self.inc_tree.get_children():
            self.inc_tree.delete(item)
        for inc in self.eco.db.list_security_incidents():
            self.inc_tree.insert(
                "",
                tk.END,
                values=(
                    inc.incident_id,
                    inc.session_id,
                    inc.severity.value,
                    inc.classification,
                    ", ".join(inc.triggered_rules),
                    f"{inc.confidence:.2f}",
                    inc.explanation,
                ),
            )

        # Tab 8: Notifications
        for item in self.ntf_tree.get_children():
            self.ntf_tree.delete(item)
        for n in self.eco.db.list_notifications():
            self.ntf_tree.insert(
                "",
                tk.END,
                values=(
                    n.notification_id,
                    n.customer_id,
                    n.severity.value,
                    n.delivery_status,
                    n.title,
                    n.safe_next_action,
                ),
            )

        # Tab 9: Badge
        self._handle_generate_badge()

        # Tab 10: Analytics & AI Forecasting
        kpis = self.eco.analytics.generate_facility_kpi_summary()
        forecast = self.eco.analytics.evaluate_demand_forecasting()
        self.ana_text.delete("1.0", tk.END)
        self.ana_text.insert(
            tk.END,
            f"=== FACILITY OPERATIONAL & SECURITY ANALYTICS [{kpis['data_source_label']}] ===\n"
            f"  Total Spaces / Occupied      : {kpis['total_spaces']} / {kpis['occupied_spaces']} ({kpis['occupancy_pct']}%)\n"
            f"  Total Sessions               : {kpis['total_sessions']} (Avg Completed Duration: {kpis['avg_parking_duration_min']} min)\n"
            f"  Settled Revenue              : {kpis['settled_revenue_eur']} EUR (Refunded: {kpis['refunded_total_eur']} EUR)\n"
            f"  Access Exception Rate        : {kpis['access_exception_rate']} ({kpis['access_exception_or_denial_count']}/{kpis['access_events_total']})\n"
            f"  False-Positive Dampened      : {kpis['false_positive_dampened_count']} / {kpis['security_incidents_total']} incidents\n"
            f"  Cooldown-Suppressed Alerts   : {kpis['notifications_suppressed_by_cooldown']}\n"
            f"  Near-Miss Hotspots           : {kpis['near_miss_hotspots']}\n\n"
            f"=== TIME-AWARE DEMAND FORECASTING EVALUATION ===\n"
            f"  Methodology Note             : {forecast.methodology_note}\n"
            f"  Chronological Split          : {forecast.train_hours} train hours -> {forecast.test_hours} future test hours\n"
            f"  Harmonic Autoregressive Model: MAE = {forecast.model_mae:.4f} | RMSE = {forecast.model_rmse:.4f}\n"
            f"  Seasonal Naive-24h Baseline  : MAE = {forecast.baseline_mae:.4f} | RMSE = {forecast.baseline_rmse:.4f}\n"
            f"  MAE Improvement vs Baseline  : +{forecast.improvement_over_baseline_pct:.2f}%\n"
            f"  Uncertainty Residual Std     : +/- {forecast.uncertainty_std:.4f} (90% CI = +/- {1.645 * forecast.uncertainty_std:.3f})\n",
        )

        # Tab 11: Audit Logs
        for item in self.aud_tree.get_children():
            self.aud_tree.delete(item)
        for aud in self.eco.db.list_audit_logs(limit=80):
            self.aud_tree.insert(
                "",
                tk.END,
                values=(
                    aud.audit_id,
                    aud.timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    aud.actor_id,
                    aud.actor_role,
                    aud.action,
                    aud.target_entity,
                    str(aud.details),
                ),
            )

        # Tab 12: Safe Config Dump
        import json

        self.cfg_text.delete("1.0", tk.END)
        self.cfg_text.insert(
            tk.END,
            "=== REDACTED CONFIGURATION & RETENTION POLICY ===\n"
            + json.dumps(self.eco.config.to_safe_dict(), indent=2),
        )


def launch_app(ecosystem: SmartParkEcosystem | None = None) -> None:
    root = tk.Tk()
    SmartParkDesktopApp(root, ecosystem=ecosystem)
    root.mainloop()


if __name__ == "__main__":
    launch_app()
