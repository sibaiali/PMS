# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Routing, pathfinding, and accident-prevention safety engine package."""

from smartpark.routing.grid_map import (
    CellType,
    GarageGridMap,
    NearMissEvent,
    StoppingDistanceResult,
    calculate_safe_stopping_distance,
)

__all__ = [
    "CellType",
    "GarageGridMap",
    "NearMissEvent",
    "StoppingDistanceResult",
    "calculate_safe_stopping_distance",
]
