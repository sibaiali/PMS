"""Historical Parking Analytics, Synthetic Dataset Generator, and Time-Aware Demand Forecasting (Section 14)."""

from __future__ import annotations

import datetime
import math
import random
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from smartpark.core.models import (
    AccessDecisionStatus,
    PaymentStatus,
)
from smartpark.database.db import DatabaseManager
from smartpark.simulation.simulator import GarageSimulator


@dataclass
class HourlyDemandRecord:
    """Single hourly observation in the synthetic or historical occupancy dataset."""

    timestamp: datetime.datetime
    hour_of_day: int
    day_of_week: int
    is_weekend: bool
    occupancy_ratio: float
    arrivals_count: int
    avg_queue_length: float
    revenue_eur: float


@dataclass
class ForecastEvaluationReport:
    """Time-aware evaluation comparing Ridge Harmonic Regression vs. Naive Lag-24h Baseline."""

    train_hours: int
    test_hours: int
    model_mae: float
    model_rmse: float
    baseline_mae: float
    baseline_rmse: float
    improvement_over_baseline_pct: float
    uncertainty_std: float
    next_24h_predictions: list[dict[str, Any]]
    methodology_note: str = (
        "SIMULATED / SYNTHETIC EVALUATION: Trained on chronological first 80% of synthetic "
        "facility history and evaluated strictly on future 20% holdout window (no lookahead leakage). "
        "Forecasts estimate occupancy demand only and are never used for safety-critical or security decisions."
    )


class SmartParkAnalyticsEngine:
    """Computes operational KPIs, security false-positive rates, hotspot maps, and demand forecasts."""

    def __init__(
        self,
        db: DatabaseManager,
        simulator: GarageSimulator | None = None,
    ) -> None:
        self.db = db
        self.simulator = simulator

    def generate_synthetic_historical_dataset(
        self,
        days: int = 14,
        seed: int = 42,
    ) -> list[HourlyDemandRecord]:
        """Generate a deterministic, realistic synthetic hourly dataset for demonstrations."""
        rng = random.Random(seed)
        base_start = datetime.datetime(2026, 10, 1, 0, 0, tzinfo=datetime.timezone.utc)
        records: list[HourlyDemandRecord] = []

        for h in range(days * 24):
            ts = base_start + datetime.timedelta(hours=h)
            hour = ts.hour
            dow = ts.weekday()
            weekend = dow >= 5

            # Realistic bimodal weekday commuter + midday curve, flatter weekend curve
            if not weekend:
                morning_peak = 0.45 * math.exp(-((hour - 9.5) ** 2) / 6.0)
                afternoon_peak = 0.50 * math.exp(-((hour - 15.0) ** 2) / 8.0)
                base_occ = 0.15 + morning_peak + afternoon_peak
            else:
                leisure_peak = 0.55 * math.exp(-((hour - 14.0) ** 2) / 10.0)
                base_occ = 0.12 + leisure_peak

            noise = rng.gauss(0.0, 0.035)
            occ_ratio = min(1.0, max(0.05, round(base_occ + noise, 3)))
            arrivals = max(0, int(round(occ_ratio * 12 + rng.uniform(-1.5, 1.5))))
            avg_q = round(max(0.0, (occ_ratio - 0.65) * 4.5 + rng.uniform(0.0, 0.3)), 2)
            rev = round(occ_ratio * 16.0 * (5.2 if 8 <= hour < 19 else 3.1), 2)

            records.append(
                HourlyDemandRecord(
                    timestamp=ts,
                    hour_of_day=hour,
                    day_of_week=dow,
                    is_weekend=weekend,
                    occupancy_ratio=occ_ratio,
                    arrivals_count=arrivals,
                    avg_queue_length=avg_q,
                    revenue_eur=rev,
                )
            )
        return records

    @staticmethod
    def _extract_features(record: HourlyDemandRecord, lag_24_occ: float, lag_1_occ: float) -> list[float]:
        angle_24 = 2.0 * math.pi * record.hour_of_day / 24.0
        angle_12 = 4.0 * math.pi * record.hour_of_day / 24.0
        return [
            1.0,  # Bias
            math.sin(angle_24),
            math.cos(angle_24),
            math.sin(angle_12),
            math.cos(angle_12),
            1.0 if record.is_weekend else 0.0,
            lag_24_occ,
            lag_1_occ,
        ]

    def evaluate_demand_forecasting(
        self,
        dataset: list[HourlyDemandRecord] | None = None,
    ) -> ForecastEvaluationReport:
        """Fit a regularized linear harmonic-autoregressive model vs. a 24h seasonal-naive baseline."""
        data = dataset or self.generate_synthetic_historical_dataset(days=14, seed=42)
        if len(data) < 48:
            raise ValueError("Dataset must contain at least 48 hourly observations.")

        # Start at index 24 so lag-24h is well-defined without lookahead
        usable_indices = list(range(24, len(data)))
        split_point = int(len(usable_indices) * 0.8)
        train_idx = usable_indices[:split_point]
        test_idx = usable_indices[split_point:]

        # Solve Ridge Regression (X^T X + lambda I) w = X^T y in pure Python / math
        n_features = 8
        ridge_lambda = 0.5
        xtx = [[0.0 for _ in range(n_features)] for _ in range(n_features)]
        xty = [0.0 for _ in range(n_features)]

        for idx in train_idx:
            feat = self._extract_features(
                data[idx],
                lag_24_occ=data[idx - 24].occupancy_ratio,
                lag_1_occ=data[idx - 1].occupancy_ratio,
            )
            y = data[idx].occupancy_ratio
            for i in range(n_features):
                xty[i] += feat[i] * y
                for j in range(n_features):
                    xtx[i][j] += feat[i] * feat[j]

        for i in range(1, n_features):
            xtx[i][i] += ridge_lambda

        weights = self._solve_linear_system(xtx, xty)

        # Compute training residuals for uncertainty interval estimation
        train_residuals: list[float] = []
        for idx in train_idx:
            feat = self._extract_features(
                data[idx],
                lag_24_occ=data[idx - 24].occupancy_ratio,
                lag_1_occ=data[idx - 1].occupancy_ratio,
            )
            pred = min(1.0, max(0.0, sum(w * f for w, f in zip(weights, feat))))
            train_residuals.append(data[idx].occupancy_ratio - pred)

        residual_std = math.sqrt(
            sum(r * r for r in train_residuals) / max(1, len(train_residuals) - n_features)
        )

        # Time-aware evaluation on future holdout window
        model_abs_errors: list[float] = []
        model_sq_errors: list[float] = []
        base_abs_errors: list[float] = []
        base_sq_errors: list[float] = []
        next_24h_preds: list[dict[str, Any]] = []

        for idx in test_idx:
            actual = data[idx].occupancy_ratio
            lag_24 = data[idx - 24].occupancy_ratio
            lag_1 = data[idx - 1].occupancy_ratio
            feat = self._extract_features(data[idx], lag_24_occ=lag_24, lag_1_occ=lag_1)
            pred = min(1.0, max(0.0, sum(w * f for w, f in zip(weights, feat))))

            # Simple baseline: yesterday's same-hour occupancy (lag-24 naive forecast)
            base_pred = lag_24

            err_m = actual - pred
            err_b = actual - base_pred
            model_abs_errors.append(abs(err_m))
            model_sq_errors.append(err_m * err_m)
            base_abs_errors.append(abs(err_b))
            base_sq_errors.append(err_b * err_b)

            if len(next_24h_preds) < 24:
                ci_half = 1.645 * residual_std
                next_24h_preds.append({
                    "timestamp": data[idx].timestamp.isoformat(),
                    "hour": data[idx].hour_of_day,
                    "actual_occupancy": round(actual, 3),
                    "predicted_occupancy": round(pred, 3),
                    "baseline_occupancy": round(base_pred, 3),
                    "ci_90_lower": round(max(0.0, pred - ci_half), 3),
                    "ci_90_upper": round(min(1.0, pred + ci_half), 3),
                })

        model_mae = sum(model_abs_errors) / max(1, len(model_abs_errors))
        model_rmse = math.sqrt(sum(model_sq_errors) / max(1, len(model_sq_errors)))
        base_mae = sum(base_abs_errors) / max(1, len(base_abs_errors))
        base_rmse = math.sqrt(sum(base_sq_errors) / max(1, len(base_sq_errors)))
        improvement_pct = ((base_mae - model_mae) / max(1e-6, base_mae)) * 100.0

        return ForecastEvaluationReport(
            train_hours=len(train_idx),
            test_hours=len(test_idx),
            model_mae=round(model_mae, 4),
            model_rmse=round(model_rmse, 4),
            baseline_mae=round(base_mae, 4),
            baseline_rmse=round(base_rmse, 4),
            improvement_over_baseline_pct=round(improvement_pct, 2),
            uncertainty_std=round(residual_std, 4),
            next_24h_predictions=next_24h_preds,
        )

    @staticmethod
    def _solve_linear_system(a: list[list[float]], b: list[float]) -> list[float]:
        """Solve A x = b via Gaussian elimination with partial pivoting."""
        n = len(b)
        aug = [row[:] + [b[i]] for i, row in enumerate(a)]
        for col in range(n):
            pivot_row = max(range(col, n), key=lambda r: abs(aug[r][col]))
            aug[col], aug[pivot_row] = aug[pivot_row], aug[col]
            pivot = aug[col][col]
            if abs(pivot) < 1e-12:
                continue
            for j in range(col, n + 1):
                aug[col][j] /= pivot
            for r in range(n):
                if r != col:
                    factor = aug[r][col]
                    for j in range(col, n + 1):
                        aug[r][j] -= factor * aug[col][j]
        return [aug[i][n] for i in range(n)]

    def generate_facility_kpi_summary(self) -> dict[str, Any]:
        """Aggregate live operational, financial, security, and safety metrics."""
        spaces = self.db.list_spaces()
        sessions = self.db.list_sessions()
        payments = self.db.list_payments()
        incidents = self.db.list_security_incidents()
        notifications = self.db.list_notifications()
        access_records = self.db.list_access_decisions(limit=500)

        occupied_count = sum(1 for s in spaces if s.status.value == "OCCUPIED")
        total_spaces = len(spaces)
        occupancy_pct = round((occupied_count / max(1, total_spaces)) * 100.0, 1)

        completed_durations_min: list[float] = []
        for ses in sessions:
            if ses.exit_time and ses.entry_time:
                dur_min = (ses.exit_time - ses.entry_time).total_seconds() / 60.0
                if dur_min >= 0:
                    completed_durations_min.append(dur_min)
        avg_duration_min = round(
            sum(completed_durations_min) / max(1, len(completed_durations_min)), 1
        )

        settled_revenue = sum(
            (p.amount for p in payments if p.status == PaymentStatus.SUCCEEDED),
            Decimal("0.00"),
        )
        refunded_total = sum(
            (p.amount for p in payments if p.status == PaymentStatus.REFUNDED),
            Decimal("0.00"),
        )

        # Access recognition failures / manual verification rate
        access_failures = sum(
            1
            for a in access_records
            if a.decision_status
            in (
                AccessDecisionStatus.DENIED,
                AccessDecisionStatus.MANUAL_VERIFICATION_REQUIRED,
                AccessDecisionStatus.FAILSAFE_HOLD,
            )
        )
        access_failure_rate = round(access_failures / max(1, len(access_records)), 3)

        # Notification false-positive / unauthenticated rejection metrics
        unauth_or_dampened = sum(
            1
            for inc in incidents
            if "R_UNAUTHENTICATED_SOURCE_REJECTED" in inc.triggered_rules
            or "R_SINGLE_MINOR_VIBRATION_DAMPENED" in inc.triggered_rules
            or "R_ETHICS_NO_PROXIMITY_ACCUSATION" in inc.triggered_rules
        )
        suppressed_notifications = sum(
            1 for n in notifications if "SUPPRESSED" in n.delivery_status
        )

        # Near-miss hotspots from simulator
        near_miss_hotspots: dict[str, int] = {}
        queue_peak = 0
        if self.simulator is not None:
            queue_peak = self.simulator.max_queue_length_observed
            for nm in self.simulator.near_misses:
                key = f"({nm.grid_x},{nm.grid_y}) {nm.cell_type.value}"
                near_miss_hotspots[key] = near_miss_hotspots.get(key, 0) + 1

        return {
            "total_spaces": total_spaces,
            "occupied_spaces": occupied_count,
            "occupancy_pct": occupancy_pct,
            "total_sessions": len(sessions),
            "avg_parking_duration_min": avg_duration_min,
            "settled_revenue_eur": str(settled_revenue),
            "refunded_total_eur": str(refunded_total),
            "access_events_total": len(access_records),
            "access_exception_or_denial_count": access_failures,
            "access_exception_rate": access_failure_rate,
            "security_incidents_total": len(incidents),
            "false_positive_dampened_count": unauth_or_dampened,
            "notifications_suppressed_by_cooldown": suppressed_notifications,
            "peak_queue_length": queue_peak,
            "near_miss_hotspots": near_miss_hotspots,
            "data_source_label": "SIMULATED & SYNTHETIC DEMONSTRATION DATA",
        }
