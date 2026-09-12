# FASE 2F.7F-7 — Clearance Boundary Model

## Contract

`CandidateSpace` uses Pyclipper integer polygons to construct **topology**:
`IFP - polygonal FORBIDDEN`. Its coordinates are marker-space reference-point
coordinates and are passed unchanged to `transform_piece`.

It does not certify a published placement. `CandidatePosition` is a
`RAW_GEOMETRIC_CANDIDATE`; only `CandidateValidator.filter`, whose injected
callback runs `IndependentMarkerValidator` over the complete marker, can emit a
`VALIDATED_CANDIDATE`.

Physical clearance remains `500` units / `0.5 cm`. The authoritative predicate
is `distance < 500 => invalid`; `distance >= 500 => valid`. Validator retains
its existing numeric comparison epsilon of `1e-7` units. No clearance inflation
was introduced.

## POLYGONAL_NFP_APPROXIMATION

Pyclipper `JT_ROUND` offsets discretise arcs to integer segments. Therefore a
polygonal NFP is not the exact Euclidean forbidden region. The selected current
arc tolerance is `5.0` integer units. It is an intentionally useful inner
approximation for candidate generation: it may propose a too-close point, which
the validator rejects, rather than silently remove legal placements.

The remaining integer-coordinate quantization floor prevents a persistent
sub-unit exact circular boundary even as arc tolerance is reduced.

| Fixture class | tolerance | vertices | minimum distance | max negative error | max positive error |
| --- | ---: | ---: | ---: | ---: | ---: |
| rectangle/rectangle | 5.0 | 28 | 499.524774 | -0.475226 | +0.152977 |
| rectangle/rectangle | 2.0 | 40 | 499.364596 | -0.635404 | +0.435810 |
| rectangle/rectangle | 1.0 | 52 | 499.520770 | -0.479230 | +0.104989 |
| rectangle/concave | 5.0 | 36 | 499.524774 | -0.475226 | +0.152977 |
| concave/concave | 5.0 | 44 | 499.524774 | -0.475226 | +0.152977 |

Lower tested tolerances increased vertex count but did not materially improve
the negative error under integer quantization; tolerance 2.0 was worse.

## Boundary repair

`repair_candidate_to_clearance` is restricted to a single active contact. It
derives an outward vector from Shapely nearest points, enumerates integer moves
by increasing Euclidean displacement, and accepts the first move with legal
distance to every obstacle. Its limit is derived as measured maximum boundary
error plus a one-unit integer quantization margin. It never uses a fixed 10,
100, or 500-unit displacement. A multi-contact candidate returns
`REPAIR_REQUIRES_MULTI_CONSTRAINT`; a material deficit returns
`NON_APPROXIMATION_CLEARANCE_FAILURE`.

The retained counterexample `(9501,14969)` is raw-feasible but fails exact
clearance (`499.962 < 500`). It repairs minimally to `(9500,14969)`, one unit
away, and then passes complete marker validation.

## Evidence

Host Windows and Docker/Linux both pass the analytic and boundary-model suites
(`18 passed`). This verifies identical validated/rejected decisions and the
counterexample behavior; raw path byte equality is not required.
