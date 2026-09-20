# COSTURA ÓPTIMA — FASE 2F.4

## Resultado

`MAX_ORDER_PER_CUT` es el perfil recomendado por defecto con nombre UI **MENOS CORTES** y subtítulo “Produce la mayor cantidad posible del pedido en cada tendido.” La implementación permanece en estado de ingeniería y no autoriza FASE 2G.

El planner CP-SAT aplica objetivos lexicográficos sin pesos: sobreproducción, cortes físicos, cobertura útil del primer corte, diseños, cambios, tela y waste. La sobreproducción no suma cobertura. Los booleanos de uso de diseño están ligados a las variables de corte y el total físico se calcula como suma de operaciones después de compactar capas equivalentes.

## Búsqueda

La generación acotada utiliza demanda original/residual, aproximaciones enteras del ratio, área, ancho, largo y capas vecinas a `floor(demand / marker_qty)`. Reserva presupuesto por separado para `DEMAND_RATIO`, `MULTI_SIZE` y `RESIDUAL_DRIVEN`; single-size permanece como fallback exacto. Se exploran los benchmarks 3, 4, 5, 6, 8, 10, 12 y 15, siempre filtrados por 176 cm útiles y 700 cm de largo.

ROUND 1 produjo un plan de 5 cortes con 60 prendas en el corte principal. ROUND 2 recibió 12 de los 24 slots globales y mejoró el plan a 4 cortes con 80 prendas en el corte principal.

## Benchmark obligatorio (97 prendas)

| Métrica | Single-size actual | MAX_ORDER_PER_CUT |
|---|---:|---:|
| Cortes físicos | 6 | 4 |
| Diseños | 6 | 4 |
| Cambios | 5 | 3 |
| Tela | 89.7915 m | 77.0225 m |
| Eficiencia | 57.112651% | 66.580942% |
| Cobertura primer corte | 30.927835% | 82.474227% |
| Mayor marker | 5 piezas | 40 piezas |
| Tiempo total | — | 106701.399 ms |

PRIMARY_SPREAD: `XS×2 · S×1 · M×2 · L×2 · XL×1`, 10 capas, 603.409 cm, 80 prendas útiles. Residual: `M=10, XL=4, XXL=3`; los cortes 2–4 existen para cubrir exactamente ese residual.

Evidencia: `artifacts/phase2f4/benchmark-order-97.json`, `artifacts/phase2f4/primary-spread-golden.png` y SVG certificado asociado.

## Verificación

- Backend: suite completa sin fallos, incluyendo 9 pruebas específicas de FASE 2F.4.
- Frontend: 12 pruebas, sin fallos; TypeScript/Vite compila.
- Docker: imágenes API/worker/web construidas, migración `20260905_0005` aplicada, health API/DB/Redis y web correctos; suite backend completa sin fallos dentro del contenedor.
- Validador independiente recalcula producción, shortage, overproduction, tela, waste, eficiencia, cortes, cobertura útil, porcentaje primario y residual.

`PATTERN_VALIDATION_STATUS = ENGINEERING`

`PRODUCTION_READY = NO`

`STOP_GATE = ACTIVE`

`IMPLEMENTATION_AUTHORIZED_FOR_PHASE_2G = NO`
