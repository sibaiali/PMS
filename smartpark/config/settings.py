"""Typed configuration and policy settings for SmartPark Guardian AI."""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class TariffConfig:
    """Configurable tariff and billing rules using Decimal precision."""

    currency: str = "EUR"
    timezone: str = "Europe/Berlin"
    hourly_rate: Decimal = Decimal("4.00")
    minute_rate: Decimal = Decimal("0.08")
    use_minute_granularity: bool = True
    peak_multiplier: Decimal = Decimal("1.50")
    off_peak_multiplier: Decimal = Decimal("0.85")
    peak_start_hour: int = 8
    peak_end_hour: int = 19
    grace_period_minutes: int = 15
    max_daily_fee: Decimal = Decimal("28.00")
    ev_charging_surcharge_per_hour: Decimal = Decimal("2.50")
    validated_visit_discount_pct: Decimal = Decimal("50.0")
    subscription_covered: bool = True


@dataclass(frozen=True)
class AllocationWeights:
    """Configurable weights for explainable parking space recommendation."""

    walking_distance_weight: float = 0.30
    congestion_weight: float = 0.20
    exit_distance_weight: float = 0.15
    size_fit_weight: float = 0.15
    safety_margin_weight: float = 0.20
    ev_match_bonus: float = 25.0
    accessible_match_bonus: float = 40.0
    reservation_match_bonus: float = 50.0
    blind_corner_penalty: float = 12.0


@dataclass(frozen=True)
class RetentionPolicy:
    """GDPR-aligned data retention periods in days."""

    access_logs_days: int = 30
    sensor_events_days: int = 14
    resolved_incidents_days: int = 90
    expired_tokens_days: int = 7
    billing_records_days: int = 3650  # Statutory tax retention (e.g. 10 years in DE)


@dataclass
class SmartParkConfig:
    """Master configuration for SmartPark Guardian AI."""

    facility_name: str = "SmartPark Guardian Garage — Berlin Mitte (Simulated)"
    database_path: str = ":memory:"
    simulation_mode: bool = True
    default_barrier_free: bool = True
    token_signing_secret: str = field(
        default_factory=lambda: os.environ.get(
            "SMARTPARK_TOKEN_SECRET", secrets.token_hex(32)
        )
    )
    badge_ttl_seconds: int = 300
    digital_key_challenge_ttl_seconds: int = 60
    digital_key_rate_limit_per_minute: int = 5
    duplicate_recognition_window_seconds: int = 15
    notification_cooldown_seconds: int = 120
    notification_max_per_hour: int = 10
    tariff: TariffConfig = field(default_factory=TariffConfig)
    allocation_weights: AllocationWeights = field(default_factory=AllocationWeights)
    retention_policy: RetentionPolicy = field(default_factory=RetentionPolicy)

    def to_safe_dict(self) -> dict[str, Any]:
        """Serialize configuration with secrets redacted for logging or UI display."""
        raw = asdict(self)
        raw["token_signing_secret"] = "***REDACTED***"
        for k, v in list(raw["tariff"].items()):
            if isinstance(v, Decimal):
                raw["tariff"][k] = str(v)
        return raw

    @classmethod
    def from_json_file(cls, path: str | Path) -> SmartParkConfig:
        """Load configuration from a JSON file without requiring embedded secrets."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        tariff_data = data.get("tariff", {})
        for dec_field in (
            "hourly_rate",
            "minute_rate",
            "peak_multiplier",
            "off_peak_multiplier",
            "max_daily_fee",
            "ev_charging_surcharge_per_hour",
            "validated_visit_discount_pct",
        ):
            if dec_field in tariff_data:
                tariff_data[dec_field] = Decimal(str(tariff_data[dec_field]))

        tariff = TariffConfig(**tariff_data) if tariff_data else TariffConfig()
        weights = (
            AllocationWeights(**data["allocation_weights"])
            if "allocation_weights" in data
            else AllocationWeights()
        )
        retention = (
            RetentionPolicy(**data["retention_policy"])
            if "retention_policy" in data
            else RetentionPolicy()
        )
        return cls(
            facility_name=data.get("facility_name", cls.facility_name),
            database_path=data.get("database_path", ":memory:"),
            simulation_mode=data.get("simulation_mode", True),
            default_barrier_free=data.get("default_barrier_free", True),
            badge_ttl_seconds=data.get("badge_ttl_seconds", 300),
            digital_key_challenge_ttl_seconds=data.get("digital_key_challenge_ttl_seconds", 60),
            digital_key_rate_limit_per_minute=data.get("digital_key_rate_limit_per_minute", 5),
            duplicate_recognition_window_seconds=data.get(
                "duplicate_recognition_window_seconds", 15
            ),
            notification_cooldown_seconds=data.get("notification_cooldown_seconds", 120),
            notification_max_per_hour=data.get("notification_max_per_hour", 10),
            tariff=tariff,
            allocation_weights=weights,
            retention_policy=retention,
        )
