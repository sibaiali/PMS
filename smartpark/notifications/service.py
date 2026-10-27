# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Intelligent Push Notification Service with Cooldowns, Quiet Hours, and Mock Provider (Section 6)."""

from __future__ import annotations

import abc
import datetime
import uuid
from dataclasses import dataclass

from smartpark.config.settings import SmartParkConfig
from smartpark.core.models import (
    Customer,
    NotificationRecord,
    SecurityIncidentRecord,
    SeverityLevel,
)
from smartpark.database.db import DatabaseManager


@dataclass
class ProviderDeliveryOutcome:
    delivered: bool
    status_code: str
    provider_name: str
    detail: str


class PushNotificationProvider(abc.ABC):
    """Abstract adapter interface for push notification delivery."""

    @property
    @abc.abstractmethod
    def provider_name(self) -> str:
        """Identifier of the notification provider."""

    @abc.abstractmethod
    def send_push(
        self,
        customer: Customer,
        title: str,
        body: str,
        severity: SeverityLevel,
    ) -> ProviderDeliveryOutcome:
        """Attempt to deliver a push notification; never guarantees instant delivery."""


class MockPushNotificationProvider(PushNotificationProvider):
    """In-application mock push provider supporting offline device and outage simulation."""

    def __init__(self) -> None:
        self.provider_outage: bool = False
        self.offline_customer_ids: set[str] = set()
        self.expired_credential_customer_ids: set[str] = set()
        self.delivered_inbox: list[dict[str, str]] = []

    @property
    def provider_name(self) -> str:
        return "MockInAppPushProvider (Simulated)"

    def send_push(
        self,
        customer: Customer,
        title: str,
        body: str,
        severity: SeverityLevel,
    ) -> ProviderDeliveryOutcome:
        if self.provider_outage:
            return ProviderDeliveryOutcome(
                delivered=False,
                status_code="FAILED_PROVIDER_OUTAGE",
                provider_name=self.provider_name,
                detail="Simulated push gateway unreachable; queued for retry.",
            )
        if customer.customer_id in self.expired_credential_customer_ids:
            return ProviderDeliveryOutcome(
                delivered=False,
                status_code="FAILED_EXPIRED_PUSH_TOKEN",
                provider_name=self.provider_name,
                detail="Device push credential expired; fallback to in-app inbox.",
            )
        if customer.customer_id in self.offline_customer_ids:
            return ProviderDeliveryOutcome(
                delivered=False,
                status_code="QUEUED_DEVICE_OFFLINE",
                provider_name=self.provider_name,
                detail="Customer phone offline; stored in pending inbox.",
            )

        self.delivered_inbox.append({
            "customer_id": customer.customer_id,
            "title": title,
            "body": body,
            "severity": severity.value,
        })
        return ProviderDeliveryOutcome(
            delivered=True,
            status_code="DELIVERED_SIM",
            provider_name=self.provider_name,
            detail="Delivered to simulated in-app notification feed.",
        )


class NotificationService:
    """Manages severity-based filtering, quiet hours, cooldowns, deduplication, and escalation."""

    def __init__(
        self,
        config: SmartParkConfig,
        db: DatabaseManager,
        provider: PushNotificationProvider | None = None,
    ) -> None:
        self.config = config
        self.db = db
        self.provider: PushNotificationProvider = provider or MockPushNotificationProvider()
        self._last_sent_by_key: dict[str, datetime.datetime] = {}

    @staticmethod
    def _is_within_quiet_hours(
        hour: int,
        start_hour: int | None,
        end_hour: int | None,
    ) -> bool:
        if start_hour is None or end_hour is None:
            return False
        if start_hour == end_hour:
            return False
        if start_hour < end_hour:
            return start_hour <= hour < end_hour
        # Wraps midnight (e.g. 22:00 to 07:00)
        return hour >= start_hour or hour < end_hour

    def notify_incident(
        self,
        customer: Customer,
        incident: SecurityIncidentRecord,
        at_time: datetime.datetime | None = None,
    ) -> NotificationRecord:
        """Create and dispatch a notification according to severity, cooldown, and quiet hours."""
        now = at_time or incident.timestamp or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)

        # 1. Informational events are recorded silently in activity history (no push spam)
        if incident.severity == SeverityLevel.INFORMATIONAL:
            rec = NotificationRecord(
                notification_id=f"NTF-{uuid.uuid4().hex[:10].upper()}",
                customer_id=customer.customer_id,
                session_id=incident.session_id,
                incident_id=incident.incident_id,
                severity=incident.severity,
                title=f"Activity Log: {incident.classification}",
                message=incident.explanation,
                reason=incident.explanation,
                confidence_limitations=incident.limitations_note,
                safe_next_action=incident.recommended_response,
                delivery_status="SILENT_HISTORY_ONLY",
                provider_name=self.provider.provider_name,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            return self.db.save_notification(rec)

        # 2. Cooldown & deduplication check per (customer_id, session_id, classification, severity)
        dedup_key = f"{customer.customer_id}:{incident.session_id}:{incident.classification}:{incident.severity.value}"
        last_sent = self._last_sent_by_key.get(dedup_key)
        cooldown = datetime.timedelta(seconds=self.config.notification_cooldown_seconds)
        if last_sent is not None and (now - last_sent) < cooldown:
            rec = NotificationRecord(
                notification_id=f"NTF-{uuid.uuid4().hex[:10].upper()}",
                customer_id=customer.customer_id,
                session_id=incident.session_id,
                incident_id=incident.incident_id,
                severity=incident.severity,
                title=f"[Grouped/Suppressed] {incident.classification}",
                message=incident.explanation,
                reason="Duplicate/rapid alert suppressed during active cooldown window.",
                confidence_limitations=incident.limitations_note,
                safe_next_action=incident.recommended_response,
                delivery_status="SUPPRESSED_COOLDOWN",
                provider_name=self.provider.provider_name,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            return self.db.save_notification(rec)

        # 3. Hourly rate limit check
        one_hour_ago = now - datetime.timedelta(hours=1)
        recent_pushes = [
            n
            for n in self.db.list_notifications(customer.customer_id)
            if n.timestamp >= one_hour_ago and n.delivery_status == "DELIVERED_SIM"
        ]
        if (
            len(recent_pushes) >= self.config.notification_max_per_hour
            and incident.severity != SeverityLevel.HIGH_PRIORITY
        ):
            rec = NotificationRecord(
                notification_id=f"NTF-{uuid.uuid4().hex[:10].upper()}",
                customer_id=customer.customer_id,
                session_id=incident.session_id,
                incident_id=incident.incident_id,
                severity=incident.severity,
                title=f"[Rate-Limited] {incident.classification}",
                message=incident.explanation,
                reason="Hourly notification rate limit reached for non-critical alerts.",
                confidence_limitations=incident.limitations_note,
                safe_next_action=incident.recommended_response,
                delivery_status="SUPPRESSED_RATE_LIMIT",
                provider_name=self.provider.provider_name,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            return self.db.save_notification(rec)

        # 4. Quiet hours check (ATTENTION_REQUIRED is deferred during quiet hours; HIGH_PRIORITY breaks through)
        if incident.severity == SeverityLevel.ATTENTION_REQUIRED and self._is_within_quiet_hours(
            now.hour, customer.quiet_hours_start, customer.quiet_hours_end
        ):
            rec = NotificationRecord(
                notification_id=f"NTF-{uuid.uuid4().hex[:10].upper()}",
                customer_id=customer.customer_id,
                session_id=incident.session_id,
                incident_id=incident.incident_id,
                severity=incident.severity,
                title=f"[Quiet Hours Queued] {incident.classification}",
                message=incident.explanation,
                reason="Customer quiet hours active; non-critical alert queued silently.",
                confidence_limitations=incident.limitations_note,
                safe_next_action=incident.recommended_response,
                delivery_status="QUEUED_QUIET_HOURS",
                provider_name=self.provider.provider_name,
                is_simulated=self.config.simulation_mode,
                timestamp=now,
            )
            return self.db.save_notification(rec)

        # 5. Dispatch via push provider adapter
        title = (
            f"HIGH PRIORITY SECURITY ALERT ({incident.session_id})"
            if incident.severity == SeverityLevel.HIGH_PRIORITY
            else f"Attention Required: Vehicle Check ({incident.session_id})"
        )
        outcome = self.provider.send_push(
            customer=customer,
            title=title,
            body=incident.explanation,
            severity=incident.severity,
        )
        delivery_status = outcome.status_code
        if not outcome.delivered and incident.severity == SeverityLevel.HIGH_PRIORITY and customer.trusted_contact:
            delivery_status = f"{outcome.status_code}_ESCALATED_TO_TRUSTED_CONTACT"

        self._last_sent_by_key[dedup_key] = now
        rec = NotificationRecord(
            notification_id=f"NTF-{uuid.uuid4().hex[:10].upper()}",
            customer_id=customer.customer_id,
            session_id=incident.session_id,
            incident_id=incident.incident_id,
            severity=incident.severity,
            title=title,
            message=incident.explanation,
            reason=", ".join(incident.triggered_rules),
            confidence_limitations=incident.limitations_note,
            safe_next_action=incident.recommended_response,
            delivery_status=delivery_status,
            provider_name=self.provider.provider_name,
            is_simulated=self.config.simulation_mode,
            timestamp=now,
        )
        return self.db.save_notification(rec)
