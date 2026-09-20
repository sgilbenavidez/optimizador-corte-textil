"""Authoritative, auditable transform policy for marker nesting.

Lay face handling, marker direction, fabric directionality and grainline are
separate constraints.  The resolver is deliberately pure so the nesting engine
and independent validator can apply exactly the same legal transform matrix.
"""

from __future__ import annotations

from dataclasses import dataclass


STRAIGHT_GRAIN_TWO_WAY = "STRAIGHT_GRAIN_TWO_WAY"
STRAIGHT_GRAIN_ONE_WAY = "STRAIGHT_GRAIN_ONE_WAY"
CROSS_GRAIN_ALLOWED = "CROSS_GRAIN_ALLOWED"
BIAS_ALLOWED = "BIAS_ALLOWED"
FREE_ORIENTATION = "FREE_ORIENTATION"


@dataclass(frozen=True)
class EffectiveTransformResolver:
    fabric_directionality: str = "NON_DIRECTIONAL"
    marker_direction_policy: str = "TWO_WAY"
    lay_face_mode: str = "FACE_ONE_WAY"
    transform_lab_mode: bool = False

    def resolve(self, configured: tuple[int, ...], grainline_policy: str) -> tuple[int, ...]:
        configured_set = set(configured)
        grainline = self._grainline_rotations(grainline_policy)
        fabric = {0} if self.fabric_directionality == "ONE_WAY_PRINT" else {0, 180, 90, 270}
        marker = {0} if self.marker_direction_policy == "ONE_WAY" else {0, 180, 90, 270}
        lab = {0, 90, 180, 270} if self.transform_lab_mode else {0, 180, 90, 270}
        return tuple(sorted(configured_set & grainline & fabric & marker & lab))

    @staticmethod
    def _grainline_rotations(policy: str) -> set[int]:
        if policy == STRAIGHT_GRAIN_ONE_WAY:
            return {0}
        if policy == STRAIGHT_GRAIN_TWO_WAY:
            return {0, 180}
        if policy == CROSS_GRAIN_ALLOWED:
            return {0, 90, 180, 270}
        if policy in {BIAS_ALLOWED, FREE_ORIENTATION}:
            return {0, 90, 180, 270}
        # Unknown grainline policy is fail-closed.
        return {0}

