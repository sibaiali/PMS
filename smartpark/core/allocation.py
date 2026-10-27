"""Explainable Parking Space Recommendation Algorithm and Baseline Comparator (Section 10)."""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field
from typing import Any

from smartpark.config.settings import AllocationWeights
from smartpark.core.models import (
    Customer,
    ParkingSpace,
    Reservation,
    SpaceStatus,
    SpaceType,
    Vehicle,
)


@dataclass
class SpaceCandidateScore:
    """Detailed score breakdown and explanation for a single candidate parking space."""

    space: ParkingSpace
    total_score: float
    eligible: bool
    rejection_reason: str | None = None
    sub_scores: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)


@dataclass
class AllocationComparisonResult:
    """Measurable comparison between SmartPark Explainable Allocator and Baseline."""

    smart_choice: SpaceCandidateScore | None
    baseline_choice: ParkingSpace | None
    smart_walking_distance_m: float
    baseline_walking_distance_m: float
    walking_distance_saved_m: float
    ev_requirement_satisfied_smart: bool
    ev_requirement_satisfied_baseline: bool
    accessible_requirement_satisfied_smart: bool
    accessible_requirement_satisfied_baseline: bool
    explanation_summary: str
    ranked_candidates: list[SpaceCandidateScore] = field(default_factory=list)


class IntelligentSpaceAllocator:
    """Ranks candidate spaces using deterministic, explainable multi-criteria scoring."""

    def __init__(self, weights: AllocationWeights | None = None) -> None:
        self.weights = weights or AllocationWeights()

    def evaluate_space(
        self,
        space: ParkingSpace,
        customer: Customer,
        vehicle: Vehicle,
        all_spaces: list[ParkingSpace],
        active_reservations: list[Reservation],
        at_time: datetime.datetime,
        target_reservation_id: str | None = None,
    ) -> SpaceCandidateScore:
        # 1. Hard eligibility constraints
        if space.status != SpaceStatus.AVAILABLE:
            return SpaceCandidateScore(
                space=space,
                total_score=-1000.0,
                eligible=False,
                rejection_reason=f"Space status is {space.status.value}.",
            )

        if space.space_type == SpaceType.EMERGENCY_RESERVED:
            return SpaceCandidateScore(
                space=space,
                total_score=-1000.0,
                eligible=False,
                rejection_reason="Emergency-route reserved space cannot be allocated to standard vehicles.",
            )

        # Vehicle size compatibility
        if vehicle.size.rank > space.max_size.rank:
            return SpaceCandidateScore(
                space=space,
                total_score=-1000.0,
                eligible=False,
                rejection_reason=f"Vehicle size {vehicle.size.value} exceeds space max size {space.max_size.value}.",
            )

        # Accessible space protection: non-eligible customers cannot take accessible spaces
        if space.is_accessible and not customer.requires_accessible:
            return SpaceCandidateScore(
                space=space,
                total_score=-1000.0,
                eligible=False,
                rejection_reason="Accessible space is protected for customers with accessibility permits.",
            )

        # Check reservation conflicts within [at_time, at_time + 2 hours] default window
        window_end = at_time + datetime.timedelta(hours=2)
        reserved_for_this_user = False
        for res in active_reservations:
            if res.status != "ACTIVE":
                continue
            if res.space_id == space.space_id and res.overlaps(at_time, window_end):
                if res.customer_id == customer.customer_id and (
                    target_reservation_id is None or res.reservation_id == target_reservation_id
                ):
                    reserved_for_this_user = True
                else:
                    return SpaceCandidateScore(
                        space=space,
                        total_score=-1000.0,
                        eligible=False,
                        rejection_reason=f"Reserved by another customer ({res.reservation_id}) during arrival window.",
                    )

        # 2. Multi-criteria scoring (base 100 points)
        sub_scores: dict[str, float] = {"base": 100.0}
        reasons: list[str] = [f"Space {space.code} ({space.zone}) is available and size-compatible ({space.max_size.value})."]

        # Walking distance score (closer to elevator/pedestrian exit is better)
        walk_penalty = -space.walking_distance_m * self.weights.walking_distance_weight
        sub_scores["walking_distance"] = round(walk_penalty, 2)

        # Exit distance score
        exit_penalty = -space.exit_distance_m * self.weights.exit_distance_weight
        sub_scores["exit_distance"] = round(exit_penalty, 2)

        # Zone congestion penalty (ratio of occupied spaces in the same zone)
        zone_spaces = [s for s in all_spaces if s.zone == space.zone]
        zone_occupied = sum(1 for s in zone_spaces if s.status == SpaceStatus.OCCUPIED)
        zone_congestion_ratio = zone_occupied / max(1, len(zone_spaces))
        congestion_penalty = -(zone_congestion_ratio * 100.0) * self.weights.congestion_weight
        sub_scores["zone_congestion"] = round(congestion_penalty, 2)
        if zone_congestion_ratio < 0.4:
            reasons.append(f"Low traffic congestion in Zone {space.zone} ({zone_occupied}/{len(zone_spaces)} occupied).")

        # Tightest size fit bonus (avoid wasting LARGE spaces on COMPACT cars when COMPACT is available)
        size_diff = space.max_size.rank - vehicle.size.rank
        size_fit_score = (2 - size_diff) * 10.0 * self.weights.size_fit_weight
        sub_scores["size_fit"] = round(size_fit_score, 2)
        if size_diff == 0:
            reasons.append(f"Exact vehicle size match ({vehicle.size.value}).")

        # Safety margin / blind corner evaluation
        if space.near_blind_corner:
            safety_score = -self.weights.blind_corner_penalty * (1.0 + self.weights.safety_margin_weight)
            reasons.append("Penalized due to proximity to blind corner.")
        else:
            safety_score = 10.0 * self.weights.safety_margin_weight
            reasons.append("Clear sightlines (away from blind corners).")
        sub_scores["safety_margin"] = round(safety_score, 2)

        # EV Charging alignment
        if vehicle.requires_ev_charging:
            if space.has_charger:
                sub_scores["ev_charging"] = self.weights.ev_match_bonus
                reasons.append("Includes requested EV charging station.")
            else:
                sub_scores["ev_charging"] = -self.weights.ev_match_bonus
                reasons.append("No EV charger available at this space.")
        elif space.has_charger:
            # Slight penalty to preserve EV chargers for EVs when non-charger spaces exist
            sub_scores["ev_charging"] = -8.0
            reasons.append("Preserves EV charger priority when standard bays are free.")

        # Accessible space alignment
        if customer.requires_accessible:
            if space.is_accessible:
                sub_scores["accessibility"] = self.weights.accessible_match_bonus
                reasons.append("Dedicated accessible bay near barrier-free pedestrian route.")
            else:
                sub_scores["accessibility"] = -20.0

        # Pre-booked reservation alignment
        if reserved_for_this_user:
            sub_scores["reservation_bonus"] = self.weights.reservation_match_bonus
            reasons.append("Matches customer's active pre-booked reservation.")

        total_score = round(sum(sub_scores.values()), 2)
        return SpaceCandidateScore(
            space=space,
            total_score=total_score,
            eligible=True,
            sub_scores=sub_scores,
            reasons=reasons,
        )

    def recommend(
        self,
        spaces: list[ParkingSpace],
        customer: Customer,
        vehicle: Vehicle,
        active_reservations: list[Reservation],
        at_time: datetime.datetime | None = None,
        target_reservation_id: str | None = None,
    ) -> AllocationComparisonResult:
        """Rank all candidate spaces and compare against a simple first-available baseline."""
        now = at_time or datetime.datetime.now(datetime.timezone.utc)
        if now.tzinfo is None:
            now = now.replace(tzinfo=datetime.timezone.utc)

        evaluated = [
            self.evaluate_space(
                space=s,
                customer=customer,
                vehicle=vehicle,
                all_spaces=spaces,
                active_reservations=active_reservations,
                at_time=now,
                target_reservation_id=target_reservation_id,
            )
            for s in spaces
        ]

        eligible_candidates = [c for c in evaluated if c.eligible]
        # Deterministic tie-breaking: highest total_score, then shortest walking distance, then lowest space_id
        eligible_candidates.sort(
            key=lambda c: (-c.total_score, c.space.walking_distance_m, c.space.space_id)
        )

        smart_choice = eligible_candidates[0] if eligible_candidates else None

        # Simple baseline: first available space by space_id that isn't emergency-reserved or size-incompatible
        baseline_candidates = [
            c.space for c in evaluated if c.eligible
        ]
        baseline_candidates.sort(key=lambda s: s.space_id)
        baseline_choice = baseline_candidates[0] if baseline_candidates else None

        smart_walk = smart_choice.space.walking_distance_m if smart_choice else 0.0
        base_walk = baseline_choice.walking_distance_m if baseline_choice else 0.0

        if smart_choice is not None:
            explanation_summary = (
                f"Selected {smart_choice.space.code} (score {smart_choice.total_score:.1f}): "
                + " ".join(smart_choice.reasons)
            )
        else:
            explanation_summary = "No eligible parking space available matching vehicle and facility constraints."

        return AllocationComparisonResult(
            smart_choice=smart_choice,
            baseline_choice=baseline_choice,
            smart_walking_distance_m=smart_walk,
            baseline_walking_distance_m=base_walk,
            walking_distance_saved_m=round(base_walk - smart_walk, 2),
            ev_requirement_satisfied_smart=bool(
                smart_choice and (not vehicle.requires_ev_charging or smart_choice.space.has_charger)
            ),
            ev_requirement_satisfied_baseline=bool(
                baseline_choice and (not vehicle.requires_ev_charging or baseline_choice.has_charger)
            ),
            accessible_requirement_satisfied_smart=bool(
                smart_choice and (not customer.requires_accessible or smart_choice.space.is_accessible)
            ),
            accessible_requirement_satisfied_baseline=bool(
                baseline_choice and (not customer.requires_accessible or baseline_choice.is_accessible)
            ),
            explanation_summary=explanation_summary,
            ranked_candidates=eligible_candidates + [c for c in evaluated if not c.eligible],
        )

    def benchmark_against_baseline(
        self,
        spaces: list[ParkingSpace],
        requests: list[tuple[Customer, Vehicle]],
        active_reservations: list[Reservation] | None = None,
    ) -> dict[str, Any]:
        """Run a multi-vehicle allocation benchmark comparing SmartPark vs Simple Baseline."""
        sim_spaces_smart = [
            ParkingSpace(**{**s.__dict__}) for s in spaces
        ]
        sim_spaces_base = [
            ParkingSpace(**{**s.__dict__}) for s in spaces
        ]
        reservations = active_reservations or []
        now = datetime.datetime.now(datetime.timezone.utc)

        smart_walks: list[float] = []
        base_walks: list[float] = []
        smart_ev_hits = 0
        base_ev_hits = 0
        ev_requests = 0

        for cust, veh in requests:
            if veh.requires_ev_charging:
                ev_requests += 1

            res_smart = self.recommend(sim_spaces_smart, cust, veh, reservations, at_time=now)
            if res_smart.smart_choice:
                chosen = res_smart.smart_choice.space
                chosen.status = SpaceStatus.OCCUPIED
                smart_walks.append(chosen.walking_distance_m)
                if veh.requires_ev_charging and chosen.has_charger:
                    smart_ev_hits += 1

            # Baseline simply picks lowest space_id among eligible
            res_base = self.recommend(sim_spaces_base, cust, veh, reservations, at_time=now)
            if res_base.baseline_choice:
                chosen_b = res_base.baseline_choice
                chosen_b.status = SpaceStatus.OCCUPIED
                base_walks.append(chosen_b.walking_distance_m)
                if veh.requires_ev_charging and chosen_b.has_charger:
                    base_ev_hits += 1

        avg_smart_walk = sum(smart_walks) / max(1, len(smart_walks))
        avg_base_walk = sum(base_walks) / max(1, len(base_walks))
        return {
            "requests_evaluated": len(requests),
            "avg_walking_distance_smart_m": round(avg_smart_walk, 2),
            "avg_walking_distance_baseline_m": round(avg_base_walk, 2),
            "avg_walking_distance_reduction_m": round(avg_base_walk - avg_smart_walk, 2),
            "ev_match_rate_smart": round(smart_ev_hits / max(1, ev_requests), 3),
            "ev_match_rate_baseline": round(base_ev_hits / max(1, ev_requests), 3),
        }
