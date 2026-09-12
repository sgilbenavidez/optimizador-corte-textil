# FASE 2F.7F-8 — Multi-NFP Contact Proof

`CandidateSpaceEngine` now measures `contact_count` from transformed geometry:
it counts placed pieces whose exact Euclidean distance equals the contractual
clearance, rather than inferring contacts from source metadata. `boundary_intersections`
retains proper intersections and collinear overlap endpoints in integer
marker-space.

## Golden-hole

Two 10×10 cm obstacles at `(21500,10000)` and `(10000,21500)` create an
auditable two-contact B placement at `(11000,11000)`. It originates from
`NFP_NFP_INTERSECTION`, has two exact 500-unit contacts, passes the independent
validator, and is absent from the frozen legacy extrema set. It wins the
compact-length tie through real contact count. Artifacts are in
`apps/api/tests/debug/golden-hole.{json,svg}`.

## Real textile proof

The current M engineering pattern generator supplies FRONT, BACK and SLEEVE
cutlines and grainlines. A valid staggered partial marker places FRONT at
`(0,0)` and BACK at `(60000,30000)`; this is a normal two-column state, with
one body offset vertically to expose a fill region. SLEEVE is discovered at
`(58000,5001)` as a new two-contact NFP–NFP candidate, absent from legacy
extrema. It validates at both 0° and 180° under the configured no-mirror,
two-way textile policy.

Candidate-set hashes are deterministic: golden-hole
`674ceeebd47fbd0ff11e92843c202c945c41264fa267e5188532113f10a2f1aa` and
body/sleeve `ed22d32eace4c54b0221904d6ec6839d9dd204227744de77e8e2807d0ae69444`.
Host Windows and Docker/Linux run the same assertions and hashes.
