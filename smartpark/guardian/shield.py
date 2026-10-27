"""SmartPark Guardian Shield — Explainable Vehicle Security Monitoring & Correlation Engine (Section 5)."""

from __future__ import annotations

import datetime
import json
import uuid
from typing import Any

from smartpark.config.settings import SmartParkConfig
from smartpark.core.models import (
    NotificationRecord,
    SecurityIncidentRecord,
    SensorEventRecord,
    SensorEventType,
    SeverityLevel,
)
from smartpark.database.db import DatabaseManager
from smartpark.notifications.service import NotificationService


class InvalidSensorMessageError(ValueError):
    """Raised when a sensor payload is malformed, missing required fields, or uses an unknown event type."""


class GuardianShieldEngine:
    """Evaluates parking-session sensor telemetry using deterministic, explainable security rules."""

    LIMITATIONS_STATEMENT = (
        "SIMULATED / TELEMETRY LIMITATION: Assessment is derived from connected sensor telemetry "
        "within the garage session. Software cannot identify individuals, verify physical intent, "
        "or guarantee physical vehicle protection."
    )

    def __init__(
        self,
        config: SmartParkConfig,
        db: DatabaseManager,
        notification_service: NotificationService,
    ) -> None:
        self.config = config
        self.db = db
        self.notifications = notification_service
        self._recent_fingerprints: dict[str, datetime.datetime] = {}

    def ingest_sensor_event(
        self,
        session_id: str,
        event_type: SensorEventType | str,
        source_authenticated: bool = True,
        sensor_health_ok: bool = True,
        payload: dict[str, Any] | None = None,
        at_time: datetime.datetime | None = None,
    ) -> tuple[SensorEventRecord, SecurityIncidentRecord, NotificationRecord | None]:
        """Validate, persist, correlate, and assess a sensor event on a protected parking session."""
        if not session_id or not isinstance(session_id, str):
            raise InvalidSensorMessageError("Sensor message requires a non-empty string session_id.")

        try:
            etype = (
                event_type
                if isinstance(event_type, SensorEventType)
                else SensorEventType(str(event_type))
            )
        except ValueError as exc:
            raise InvalidSensorMessageError(f"Unsupported sensor event_type: {event_type}") from exc

        if payload is not None and not isinstance(payload, dict):
            raise InvalidSensorMessageError("Sensor event payload must be a dictionary.")

        session = self.db.get_session(session_id)
        if session is None:
            raise InvalidSensorMessageError(f"Session {session_id} does not exist.")

        now = at_time or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)

        event = SensorEventRecord(
            event_id=f"EVT-{uuid.uuid4().hex[:10].upper()}",
            session_id=session.session_id,
            vehicle_id=session.vehicle_id,
            event_type=etype,
            source_authenticated=source_authenticated,
            sensor_health_ok=sensor_health_ok,
            payload=payload or {},
            is_simulated=self.config.simulation_mode,
            timestamp=now,
        )
        self.db.save_sensor_event(event)

        incident = self._assess_event(session.session_id, session.vehicle_id, event, now)
        self.db.save_security_incident(incident)

        customer = self.db.get_customer(session.customer_id)
        notification: NotificationRecord | None = None
        if customer is not None:
            notification = self.notifications.notify_incident(customer, incident, at_time=now)

        return event, incident, notification

    def _assess_event(
        self,
        session_id: str,
        vehicle_id: str,
        event: SensorEventRecord,
        now: datetime.datetime,
    ) -> SecurityIncidentRecord:
        # Check duplicate sensor event within 10s suppression window
        fp = f"{session_id}:{event.event_type.value}:{json.dumps(event.payload, sort_keys=True)}"
        prev_time = self._recent_fingerprints.get(fp)
        self._recent_fingerprints[fp] = now
        if prev_time is not None and (now - prev_time).total_seconds() < 10.0:
            return SecurityIncidentRecord(
                incident_id=f"INC-{uuid.uuid4().hex[:10].upper()}",
                session_id=session_id,
                vehicle_id=vehicle_id,
                severity=SeverityLevel.INFORMATIONAL,
                classification="SENSOR_EVENT",
                triggered_rules=["R_DUPLICATE_EVENT_SUPPRESSED"],
                received_event_ids=[event.event_id],
                source_authenticated=event.source_authenticated,
                is_expected=True,
                confidence=0.99,
                limitations_note=self.LIMITATIONS_STATEMENT,
                recommended_response="No action needed; duplicate sensor frame suppressed.",
                explanation=f"Duplicate {event.event_type.value} frame received within 10s window and suppressed.",
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )

        # Rule 1: Unauthenticated telemetry source cannot trigger vehicle alarms
        if not event.source_authenticated:
            return SecurityIncidentRecord(
                incident_id=f"INC-{uuid.uuid4().hex[:10].upper()}",
                session_id=session_id,
                vehicle_id=vehicle_id,
                severity=SeverityLevel.INFORMATIONAL,
                classification="SENSOR_EVENT",
                triggered_rules=["R_UNAUTHENTICATED_SOURCE_REJECTED"],
                received_event_ids=[event.event_id],
                source_authenticated=False,
                is_expected=False,
                confidence=0.20,
                limitations_note=self.LIMITATIONS_STATEMENT,
                recommended_response="Verify sensor gateway credentials; do not alarm customer on unauthenticated input.",
                explanation=(
                    f"Received {event.event_type.value} from an unauthenticated source. "
                    "Rejected from alarm correlation to prevent spoofing and false positives."
                ),
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )

        # Rule 2: Sensor health / offline telemetry
        if not event.sensor_health_ok or event.event_type == SensorEventType.SENSOR_OFFLINE:
            return SecurityIncidentRecord(
                incident_id=f"INC-{uuid.uuid4().hex[:10].upper()}",
                session_id=session_id,
                vehicle_id=vehicle_id,
                severity=SeverityLevel.ATTENTION_REQUIRED,
                classification="SENSOR_EVENT",
                triggered_rules=["R_SENSOR_HEALTH_DEGRADED"],
                received_event_ids=[event.event_id],
                source_authenticated=True,
                is_expected=False,
                confidence=0.60,
                limitations_note=self.LIMITATIONS_STATEMENT,
                recommended_response="Check vehicle telemetry link status in the app; security monitoring has reduced visibility.",
                explanation="Vehicle or bay sensor reported degraded health or lost heartbeat.",
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )

        # Rule 3: Ethical Anti-Surveillance Rule — Pedestrian proximity alone is NEVER an accusation or alarm!
        if event.event_type == SensorEventType.PEDESTRIAN_PROXIMITY:
            return SecurityIncidentRecord(
                incident_id=f"INC-{uuid.uuid4().hex[:10].upper()}",
                session_id=session_id,
                vehicle_id=vehicle_id,
                severity=SeverityLevel.INFORMATIONAL,
                classification="SENSOR_EVENT",
                triggered_rules=["R_ETHICS_NO_PROXIMITY_ACCUSATION"],
                received_event_ids=[event.event_id],
                source_authenticated=True,
                is_expected=True,
                confidence=0.95,
                limitations_note=self.LIMITATIONS_STATEMENT,
                recommended_response="No action required. Ordinary pedestrian movement in a parking facility is normal.",
                explanation=(
                    "Pedestrian proximity detected near bay. Per SmartPark ethical policy, proximity or "
                    "presence alone never triggers an accusation or security alert."
                ),
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )

        # Fetch recent authenticated events in the last 3 minutes for correlation
        window_start = now - datetime.timedelta(minutes=3)
        recent_events = [
            e
            for e in self.db.list_sensor_events(session_id)
            if e.source_authenticated and window_start <= e.timestamp <= now
        ]
        recent_types = {e.event_type for e in recent_events}
        recent_ids = [e.event_id for e in recent_events]

        # Rule 4: Authorized owner activity (AUTHORIZED_KEY_UNLOCK present in window)
        if (
            event.event_type
            in (
                SensorEventType.AUTHORIZED_KEY_UNLOCK,
                SensorEventType.DOOR_LOCK,
                SensorEventType.DOOR_UNLOCK,
                SensorEventType.SENSOR_HEARTBEAT,
            )
            or SensorEventType.AUTHORIZED_KEY_UNLOCK in recent_types
        ):
            return SecurityIncidentRecord(
                incident_id=f"INC-{uuid.uuid4().hex[:10].upper()}",
                session_id=session_id,
                vehicle_id=vehicle_id,
                severity=SeverityLevel.INFORMATIONAL,
                classification="SENSOR_EVENT",
                triggered_rules=["R_AUTHORIZED_OWNER_ACTIVITY"],
                received_event_ids=recent_ids or [event.event_id],
                source_authenticated=True,
                is_expected=True,
                confidence=0.97,
                limitations_note=self.LIMITATIONS_STATEMENT,
                recommended_response="No action needed; activity matches authenticated owner digital key.",
                explanation=f"Authenticated {event.event_type.value} correlated with authorized user session.",
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )

        # Rule 5: Single minor vibration -> Routine SENSOR_EVENT (avoid false-positive alarm from passing truck)
        if event.event_type == SensorEventType.VIBRATION_MINOR:
            minor_vib_count = sum(
                1 for e in recent_events if e.event_type == SensorEventType.VIBRATION_MINOR
            )
            if minor_vib_count < 3:
                return SecurityIncidentRecord(
                    incident_id=f"INC-{uuid.uuid4().hex[:10].upper()}",
                    session_id=session_id,
                    vehicle_id=vehicle_id,
                    severity=SeverityLevel.INFORMATIONAL,
                    classification="SENSOR_EVENT",
                    triggered_rules=["R_SINGLE_MINOR_VIBRATION_DAMPENED"],
                    received_event_ids=[event.event_id],
                    source_authenticated=True,
                    is_expected=True,
                    confidence=0.85,
                    limitations_note=self.LIMITATIONS_STATEMENT,
                    recommended_response="Logged silently; isolated minor vibration is typically ambient garage acoustics.",
                    explanation="Isolated minor vibration recorded without corroborating tamper indicators.",
                    is_simulated=self.config.simulation_mode,
                    timestamp=now,
                )
            # 3+ minor vibrations within 3 minutes -> SUSPICIOUS_PATTERN
            return SecurityIncidentRecord(
                incident_id=f"INC-{uuid.uuid4().hex[:10].upper()}",
                session_id=session_id,
                vehicle_id=vehicle_id,
                severity=SeverityLevel.ATTENTION_REQUIRED,
                classification="SUSPICIOUS_PATTERN",
                triggered_rules=["R_REPEATED_VIBRATION_PATTERN"],
                received_event_ids=recent_ids,
                source_authenticated=True,
                is_expected=False,
                confidence=0.76,
                limitations_note=self.LIMITATIONS_STATEMENT,
                recommended_response="Review vehicle status in SmartPark App or request a courtesy patrol check.",
                explanation=f"Detected {minor_vib_count} repeated vibration events within 3 minutes without an authorized key unlock.",
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )

        # Rule 6: Corroborated Multi-Sensor High-Priority Alarm (CONFIRMED_ALARM)
        corroborating_critical_types = {
            SensorEventType.IMPACT_HEAVY,
            SensorEventType.GLASS_TAMPER,
            SensorEventType.ALARM_TRIGGERED,
            SensorEventType.VEHICLE_MOVEMENT,
            SensorEventType.DOOR_OPEN,
        }
        matched_critical = recent_types.intersection(corroborating_critical_types)
        if (
            event.event_type == SensorEventType.ALARM_TRIGGERED
            or len(matched_critical) >= 2
            or event.event_type == SensorEventType.GLASS_TAMPER
        ):
            rules = ["R_CORROBORATED_MULTI_SENSOR_ALARM"]
            if event.event_type == SensorEventType.ALARM_TRIGGERED:
                rules.append("R_OEM_ALARM_SIGNAL")
            return SecurityIncidentRecord(
                incident_id=f"INC-{uuid.uuid4().hex[:10].upper()}",
                session_id=session_id,
                vehicle_id=vehicle_id,
                severity=SeverityLevel.HIGH_PRIORITY,
                classification="CONFIRMED_ALARM",
                triggered_rules=rules,
                received_event_ids=recent_ids,
                source_authenticated=True,
                is_expected=False,
                confidence=0.93,
                limitations_note=self.LIMITATIONS_STATEMENT,
                recommended_response=(
                    "SAFE NEXT ACTION: Do not approach a potential incident alone. "
                    "Use the SmartPark App to alert on-site facility security and verify vehicle status."
                ),
                explanation=(
                    "Corroborated high-priority security alarm: multiple unauthorized physical telemetry "
                    f"indicators ({', '.join(sorted(t.value for t in matched_critical))}) detected on armed session."
                ),
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )

        # Rule 7: Single unexpected physical event (e.g., isolated DOOR_OPEN, IMPACT_HEAVY, or VEHICLE_MOVEMENT)
        return SecurityIncidentRecord(
            incident_id=f"INC-{uuid.uuid4().hex[:10].upper()}",
            session_id=session_id,
            vehicle_id=vehicle_id,
            severity=SeverityLevel.ATTENTION_REQUIRED,
            classification="SUSPICIOUS_PATTERN",
            triggered_rules=[f"R_UNEXPECTED_{event.event_type.value}"],
            received_event_ids=recent_ids,
            source_authenticated=True,
            is_expected=False,
            confidence=0.80,
            limitations_note=self.LIMITATIONS_STATEMENT,
            recommended_response="Check vehicle lock/door state in the app or request staff verification.",
            explanation=f"Unexpected {event.event_type.value} received while Guardian Shield was armed with no digital key unlock.",
            is_simulated=self.config.simulation_mode,
            timestamp=now,
        )
