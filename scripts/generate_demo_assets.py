# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Generate visual simulation PNG snapshots for documentation and GitHub showcase."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from smartpark.system import SmartParkEcosystem


def main() -> None:
    eco = SmartParkEcosystem()
    eco.seed_demonstration_scenario()
    assets_dir = Path(__file__).resolve().parent.parent / "docs" / "assets"
    assets_dir.mkdir(parents=True, exist_ok=True)

    # Frame 3: Active A* navigation & pedestrian crosswalk yielding
    if len(eco.simulator.replay_history) >= 3:
        eco.simulator.export_snapshot_png(
            assets_dir / "simulation_yielding_snapshot.png",
            snapshot=eco.simulator.replay_history[2],
        )

    # Final Frame: Vehicles parked and obstacle reroutes completed
    eco.simulator.export_snapshot_png(
        assets_dir / "simulation_snapshot.png",
        snapshot=eco.simulator.replay_history[-1],
    )
    print(f"Generated simulation snapshots in {assets_dir}")


if __name__ == "__main__":
    main()
