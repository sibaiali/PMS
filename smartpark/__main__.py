"""CLI and GUI Entrypoint for SmartPark Guardian AI (`python -m smartpark`)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from smartpark.config.settings import SmartParkConfig
from smartpark.system import SmartParkEcosystem


def run_cli_demo(db_path: str = ":memory:", export_png: str | None = None) -> None:
    """Execute a comprehensive deterministic demonstration of all SmartPark Guardian AI modules."""
    config = SmartParkConfig(database_path=db_path)
    eco = SmartParkEcosystem(config=config)
    seed_info = eco.seed_demonstration_scenario()

    print("=" * 78)
    print("  SMARTPARK GUARDIAN AI — BARRIER-FREE INTELLIGENT PARKING DEMONSTRATION")
    print("  [MODE: SIMULATED SENSORS, ACCESS HARDWARE, DIGITAL KEYS & PAYMENTS]")
    print("=" * 78)
    print("\n1. SEEDED SCENARIO & SIMULATION SUMMARY:")
    print(json.dumps(seed_info, indent=2))

    kpis = eco.analytics.generate_facility_kpi_summary()
    print("\n2. FACILITY OPERATIONAL & SECURITY KPIs:")
    print(json.dumps(kpis, indent=2))

    forecast = eco.analytics.evaluate_demand_forecasting()
    print("\n3. TIME-AWARE DEMAND FORECASTING EVALUATION (RIDGE HARMONIC VS. BASELINE):")
    print(
        f"   Train Hours: {forecast.train_hours} | Test Hours: {forecast.test_hours}\n"
        f"   Model MAE  : {forecast.model_mae:.4f} (RMSE: {forecast.model_rmse:.4f})\n"
        f"   Baseline   : {forecast.baseline_mae:.4f} (RMSE: {forecast.baseline_rmse:.4f})\n"
        f"   Improvement: +{forecast.improvement_over_baseline_pct:.2f}% | "
        f"90% CI: +/- {1.645 * forecast.uncertainty_std:.3f}"
    )

    if export_png:
        out_file = eco.simulator.export_snapshot_png(Path(export_png))
        print(f"\n4. EXPORTED SIMULATION SNAPSHOT PNG: {out_file}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="SmartPark Guardian AI — Barrier-Free Intelligent Parking Ecosystem"
    )
    parser.add_argument(
        "--demo",
        action="store_true",
        help="Run headless deterministic demonstration and print full telemetry summary.",
    )
    parser.add_argument(
        "--db",
        default=":memory:",
        help="SQLite database path (default: ':memory:' or e.g. 'smartpark.db').",
    )
    parser.add_argument(
        "--export-png",
        default=None,
        help="Export simulated garage map snapshot to the given PNG path.",
    )
    args = parser.parse_args()

    if args.demo or args.export_png:
        run_cli_demo(db_path=args.db, export_png=args.export_png)
    else:
        from smartpark.gui.app import launch_app

        cfg = SmartParkConfig(database_path=args.db)
        launch_app(SmartParkEcosystem(cfg))


if __name__ == "__main__":
    main()
