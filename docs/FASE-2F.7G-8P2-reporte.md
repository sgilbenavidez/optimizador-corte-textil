# FASE 2F.7G-8P2 — Validation Equivalence & Determinism Closure

`FASE_2F_7G_8P2_VALIDATION_DETERMINISM = PASS`
`SAFE_TO_RESUME_2F_7G_8 = YES`

## Qué se cerró

La Fase 2F.7G-8P (optimización de performance del pool unificado 0°/180° para
`M_SLEEVE_004`) había dejado dos gates en `FAIL`: `VALIDATED_SET_EQUIVALENCE`
y `DETERMINISM_STATUS`. Esta fase los cierra con evidencia completa, no
muestreada:

- **Equivalencia de validación**: se comparó, candidato por candidato, el
  conjunto **completo** de 24 445 candidatos crudos congelados (checkpoint 11,
  `M_SLEEVE_004`, `UNIFIED_LEGACY_PLUS_CANDIDATE_SPACE`, 0°+180°) entre el
  pipeline de referencia (pre-optimización: precheck exacto + `IndependentMarkerValidator`)
  y el optimizado (`IncrementalCandidateValidator`). Resultado:
  `FULL_SET_FALSE_VALID = 0`, `FULL_SET_FALSE_INVALID = 0`,
  `REFERENCE_VALIDATED_SET_HASH == OPTIMIZED_VALIDATED_SET_HASH`.
- **Determinismo**: dos procesos frescos e independientes coinciden en cada
  hash de etapa (input, candidatos crudos, dedup, validados, scoreados,
  candidato elegido). Un par adicional cold-cache/warm-cache (mismo runner,
  misma pieza) también coincide en todas las etapas.
- **Diagnóstico del FAIL previo**: no se pudo reproducir. La causa más
  probable es metodológica — la corrida reportada como "optimizada" usó
  `candidate_budget=100` en vez de la política canónica `5000` (Sección 9,
  Fase 2F.7G-8), y/o contención de CPU durante una corrida acotada por
  `piece_time_budget_ms`. No es un defecto de lógica en
  `IncrementalCandidateValidator` ni en el pool unificado/dedup.

## Evidencia

Ver `artifacts/phase2f7g8p2/summary.json` para el reporte completo con todos
los campos del gate. Artefactos de soporte en el mismo directorio:
`frozen-candidates.json`, `validation-equivalence.json`,
`validation-mismatches.json` (vacío), `stage-hashes-run{1,2}.json`,
`determinism-diff.json`, `cold-cache.json`, `warm-cache.json`,
`two-way-profile.json`, `zero-only-profile.json`, `live.log`.

## Performance (con metodología correcta, no la del reporte previo)

| Métrica | Valor |
| --- | --- |
| Baseline (pre-8P, budget=5000) | 98.982 s |
| Optimizado (post-8P2, budget=5000) | 27.402 s |
| Speedup | **3.61x** (gate ≥3x: PASS) |
| Zero-only runtime | 12.809 s |
| Two-way runtime | 27.402 s |
| Two-way / zero-only ratio | 2.14x (candidatos: 2.03x más — proporcional, no anómalo) |
| Top bottleneck restante | `candidate_space.boundary_intersections` |
| Bottleneck secundario | `IntegerGeometryKernel.nfp` |

No se optimizó más rendimiento en esta fase (fuera de alcance explícito,
Sección 1 del spec). Queda como candidato para una fase futura si se decide
perseguir más velocidad.

## Siguiente paso

Retomar FASE 2F.7G-8: `unified_two_way run1` → validar candidatos 180° →
`run2` → comparar determinismo run1 vs run2 → reconfirmar `LEGACY_ONLY`
(229.5 cm / 70.534348%) → `finalize_phase2f7g8.py` → reporte definitivo.

`PATTERN_VALIDATION_STATUS = ENGINEERING` · `PRODUCTION_READY = NO` ·
`STOP_GATE = ACTIVE`
