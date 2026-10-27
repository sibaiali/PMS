# Copyright (c) 2026 Ali Sibai (https://alisibai.vercel.app | https://github.com/sibaiali).
# All Rights Reserved. Protected under German Urheberrechtsgesetz (UrhG) & International Copyright Treaties.
# Unauthorized copying, redistribution, plagiarism, or commercial use is strictly prohibited.

"""Core domain models, space allocation, and parking engine."""

from smartpark.core.allocation import (
    AllocationComparisonResult,
    IntelligentSpaceAllocator,
    SpaceCandidateScore,
)
from smartpark.core.engine import (
    ParkingEngine,
    hash_license_plate,
    mask_license_plate,
    normalize_license_plate,
)

__all__ = [
    "AllocationComparisonResult",
    "IntelligentSpaceAllocator",
    "ParkingEngine",
    "SpaceCandidateScore",
    "hash_license_plate",
    "mask_license_plate",
    "normalize_license_plate",
]
