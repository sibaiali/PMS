# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Live Python HTTP & JSON API Server for the SmartPark Guardian AI Modern Web Command Center."""

from __future__ import annotations

import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from smartpark.core.models import CredentialType, SensorEventType, SessionStatus, VehicleSize
from smartpark.system import SmartParkEcosystem


ROOT_DIR = Path(__file__).resolve().parent.parent


class SmartParkWebHandler(SimpleHTTPRequestHandler):
    """Serves the modern UI web application and exposes live Python backend API endpoints."""

    ecosystem: SmartParkEcosystem

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(ROOT_DIR), **kwargs)

    def log_message(self, format: str, *args: Any) -> None:
        # Keep console clean
        pass

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, default=str).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/state":
            eco = self.ecosystem
            kpis = eco.analytics.generate_facility_kpi_summary()
            forecast = eco.analytics.evaluate_demand_forecasting()
            spaces = [
                {
                    "space_id": s.space_id,
                    "code": s.code,
                    "zone": s.zone,
                    "grid_x": s.grid_x,
                    "grid_y": s.grid_y,
                    "space_type": s.space_type.value,
                    "max_size": s.max_size.value,
                    "has_charger": s.has_charger,
                    "is_accessible": s.is_accessible,
                    "walking_distance_m": s.walking_distance_m,
                    "exit_distance_m": s.exit_distance_m,
                    "near_blind_corner": s.near_blind_corner,
                    "status": s.status.value,
                }
                for s in eco.db.list_spaces()
            ]
            incidents = [
                {
                    "incident_id": i.incident_id,
                    "session_id": i.session_id,
                    "severity": i.severity.value,
                    "classification": i.classification,
                    "rules": i.triggered_rules,
                    "confidence": i.confidence,
                    "explanation": i.explanation,
                    "recommended_response": i.recommended_response,
                    "timestamp": i.timestamp.isoformat(),
                }
                for i in eco.db.list_security_incidents()[:15]
            ]
            self._send_json({
                "engine": "Python 3.12 SmartParkEcosystem (SQLite WAL)",
                "author": "Ali Sibai",
                "copyright": "© 2026 Ali Sibai. All Rights Reserved (§ 69a UrhG).",
                "kpis": kpis,
                "spaces": spaces,
                "incidents": incidents,
                "forecast": {
                    "model_mae": forecast.model_mae,
                    "model_rmse": forecast.model_rmse,
                    "baseline_mae": forecast.baseline_mae,
                    "baseline_rmse": forecast.baseline_rmse,
                    "improvement_pct": forecast.improvement_over_baseline_pct,
                    "uncertainty_std": forecast.uncertainty_std,
                    "next_24h": forecast.next_24h_predictions,
                },
            })
            return

        return super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
        data = json.loads(raw) if raw else {}
        eco = self.ecosystem

        if parsed.path == "/api/arrive":
            plate = data.get("license_plate", "B-SP 2026")
            cred_str = data.get("credential", "BLE_DIGITAL_KEY_SIM")
            sec_ok = bool(data.get("secondary_verified", True))
            emergency = cred_str == "EMERGENCY_OVERRIDE"
            cred = (
                CredentialType.STAFF_OVERRIDE
                if emergency
                else CredentialType(cred_str)
            )
            # Ensure vehicle is registered if it's one of the demo/custom vehicles
            if not emergency and plate != "XX-UN 999":
                existing_plates = {v.license_plate_raw for v in eco.db.list_vehicles()}
                if plate.strip().upper() not in existing_plates:
                    c = eco.parking.register_customer(
                        name=f"Driver {plate}",
                        email=f"{plate.lower().replace(' ', '').replace('-', '')}@smartpark.de",
                        phone="+4917000000",
                        requires_accessible=("ACC" in plate or "EW" in plate),
                    )
                    eco.parking.register_vehicle(
                        customer_id=c.customer_id,
                        license_plate=plate,
                        size=VehicleSize.STANDARD,
                        requires_ev_charging=("EV" in plate or "SP" in plate),
                    )
            # Reset duplicate window for interactive testing
            eco.access._recent_events.clear()
            res = eco.access.evaluate_arrival(
                license_plate=plate if not emergency else None,
                credential_type=cred,
                secondary_credential_verified=sec_ok,
                emergency_vehicle=emergency,
            )
            space_obj = (
                eco.db.get_space(res.record.assigned_space_id)
                if res.record.assigned_space_id
                else None
            )
            self._send_json({
                "decision_status": res.record.decision_status.value,
                "reason": res.record.reason,
                "guidance_message": res.guidance_message,
                "alternative_workflow": res.record.alternative_workflow,
                "assigned_space": {
                    "space_id": space_obj.space_id,
                    "code": space_obj.code,
                    "grid_x": space_obj.grid_x,
                    "grid_y": space_obj.grid_y,
                }
                if space_obj
                else None,
                "allocation_summary": res.allocation.explanation_summary
                if res.allocation
                else None,
            })
            return

        if parsed.path == "/api/depart":
            active = eco.db.list_sessions(SessionStatus.ACTIVE)
            if not active:
                self._send_json({
                    "decision_status": "NO_ACTIVE_SESSION",
                    "egress_message": "No active parking session to check out.",
                })
                return
            target_session = active[0]
            dep = eco.access.evaluate_departure(
                session_id=target_session.session_id,
                at_time=target_session.entry_time + datetime.timedelta(minutes=75),
            )
            self._send_json({
                "decision_status": dep.record.decision_status.value,
                "egress_message": dep.egress_message,
                "receipt_text": dep.receipt_text,
                "released_space_id": target_session.space_id,
                "amount": str(dep.payment.amount) if dep.payment else "0.00",
            })
            return

        if parsed.path == "/api/sensor":
            active = eco.db.list_sessions(SessionStatus.ACTIVE)
            if not active:
                self._send_json({"error": "No active session to monitor."}, status=400)
                return
            sid = active[0].session_id
            ev_key = data.get("event_type", "PEDESTRIAN_PROXIMITY")
            eco.guardian._recent_fingerprints.clear()
            if ev_key == "CORROBORATED_IMPACT_ALARM":
                eco.guardian.ingest_sensor_event(sid, SensorEventType.IMPACT_HEAVY, True, True, {"g": 2.8})
                _, inc, ntf = eco.guardian.ingest_sensor_event(
                    sid, SensorEventType.ALARM_TRIGGERED, True, True, {"zone": "perimeter"}
                )
            elif ev_key == "UNAUTHENTICATED_SPOOF":
                _, inc, ntf = eco.guardian.ingest_sensor_event(
                    sid, SensorEventType.ALARM_TRIGGERED, source_authenticated=False
                )
            elif ev_key == "DOOR_OPEN_UNAUTH":
                _, inc, ntf = eco.guardian.ingest_sensor_event(
                    sid, SensorEventType.DOOR_OPEN, source_authenticated=True
                )
            else:
                _, inc, ntf = eco.guardian.ingest_sensor_event(
                    sid, SensorEventType(ev_key), source_authenticated=True
                )
            self._send_json({
                "incident_id": inc.incident_id,
                "severity": inc.severity.value,
                "classification": inc.classification,
                "rules": inc.triggered_rules,
                "confidence": inc.confidence,
                "explanation": inc.explanation,
                "recommended_response": inc.recommended_response,
                "notification_status": ntf.delivery_status if ntf else "NONE",
            })
            return

        self._send_json({"error": "Unknown endpoint"}, status=404)


def run_web_server(port: int = 8080) -> None:
    eco = SmartParkEcosystem()
    eco.seed_demonstration_scenario()
    SmartParkWebHandler.ecosystem = eco
    server = ThreadingHTTPServer(("127.0.0.1", port), SmartParkWebHandler)
    print(f"SmartPark Guardian AI Python Web Command Center running at http://localhost:{port}/")
    server.serve_forever()


if __name__ == "__main__":
    run_web_server(8080)
