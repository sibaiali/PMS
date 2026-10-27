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
