# COSTURA ÓPTIMA — estado consolidado de continuidad

**Corte de información:** 2026-09-12  
**Revisión inspeccionada:** `e4cbf6cc9aac688b799345c2526b973e3ecc77a2` (`FASE 2F.7H`)  
**Árbol de trabajo antes de crear este documento:** sin cambios versionados; `git diff --check` sin errores.  
**Regla de lectura:** este documento separa código integrado, evidencia histórica versionada y diseño. Un `PASS` de una fase no convierte el patrón en apto para producción.

## Veredicto ejecutivo

El producto MVP de planificación de corte está **implementado e integrado**: catálogo y patrón versionados, órdenes con snapshots, geometría de markers, planificación de tendidos, API/UI, worker asíncrono, persistencia PostgreSQL y observabilidad. La evidencia histórica muestra pruebas de backend, frontend, Docker, recuperación, concurrencia, modo offline y casos A–E.

La línea experimental de nesting irregular también tiene código y artefactos recientes: pool unificado 0°/180°, presupuesto equitativo por orientación y búsqueda global `destroy/repair`. Su mejor corrida M2+L1 bajó el largo de `229.500 cm` a `227.483 cm` (eficiencia `71.159748%`). **No está conectada al flujo API/worker de producción**: hoy sólo se invoca desde scripts de fase y trabaja sobre el fixture M2+L1.

El estado global sigue siendo **ingeniería, no producción**. El patrón permanece `ENGINEERING / UNVALIDATED_FOR_PRODUCTION`; `PRODUCTION_READY = NO` y el `STOP_GATE` continúa activo.

## Estado por capacidad

| Capacidad | Estado real | Prueba/evidencia disponible | Límite o decisión pendiente |
| --- | --- | --- | --- |
| Arquitectura modular API + worker + PostgreSQL + Redis/RQ + web React | Implementada | Mapa arquitectónico, reportes 2A–2F y verificaciones Docker históricas | El mapa fue anclado en `5f6965b`; no incluye aún el componente experimental 2F.7H. |
| Catálogo, medidas, patrones y versionado | Implementada | Migraciones, seed idempotente, API/UI y FASE 2B | Patrón de camiseta sólo de ingeniería; faltan validación física/industrial y aprobación de tallaje. |
| Órdenes y trazabilidad histórica | Implementada | Snapshots, hashes SHA-256, consultas históricas y caso B real sobre PostgreSQL | No sustituye control de acceso ni gobierno de datos. |
| Geometry Engine y marker certificado | Implementado con limitación conocida | FASE 2D: 58 pruebas backend, 7 frontend, Docker/Linux; validador independiente | NFP cóncavo completo no existe; usa BLF determinista + colisión poligonal exacta y candidatos/NFP parciales. |
| Planificación de producción y CP-SAT | Implementada | FASE 2E/2E.1: A–E, perfiles, auditoría y validador de plan | No afirma optimalidad global de geometría ni del plan real industrial. |
| Optimización anytime local | Implementada | FASE 2F.5: heuristic, CP-SAT opcional/híbrido, incumbent, timeout/early success, offline smoke | Resultado depende de catálogo/markers de ingeniería. |
| Worker, estados, recuperación y publicación transaccional | Implementada | FASE 2F: retries, leases, recovery, concurrencia, health, métricas y Docker | Requiere ensayo operacional prolongado antes de producción. |
| UI de órdenes, progreso, resultados, mapa de corte | Implementada | FASE 2F/2F.1: navegación, incumbent y SVG/mapas certificados | No es una autorización industrial del plan. |
| Perfil `MAX_ORDER_PER_CUT` | Implementado | FASE 2F.4: benchmark de 97 prendas, 4 cortes, validador independiente | Sigue bajo el stop gate del patrón. |
| Pool 0°/180° con presupuesto equitativo | Implementado y validado en fase | 2F.7G-8.1: equivalencia completa, no starvation, determinismo y validador | El greedy unificado aún quedó peor que el incumbent legado en M2+L1 (`235.064 cm` vs `229.500 cm`). |
| Búsqueda global irregular ALNS | Parcialmente implementada como experimento de fase | 2F.7H: preflight host/Docker, validador final, repetición determinista y artefactos | No integrada a API/worker; fixture M2+L1; no checkpoint/resume; alcance de operadores y beam deliberadamente reducido. |
| Integraciones industriales, DXF/Optitex, ERP, inventario, multi-roll, multi-tela/color | Sólo diseñado / fuera de alcance MVP | README y FASE 0 | Requiere definición de producto e integración. |
| Autenticación, autorización y endurecimiento para exposición pública | Sólo diseñado / fuera de alcance MVP | README y FASE 2F | Bloqueador para exposición pública. |

## Qué fue probado

### Evidencia histórica versionada

- **2A–2C:** imágenes API/Web, 17 pruebas backend y 4 frontend de entonces, TypeScript/Vite, migraciones PostgreSQL/SQLite, seed idempotente, stack Docker y round-trip HTTP real del caso B.
- **2D:** 58 pruebas backend y 7 frontend, fixtures degenerados e infactibles, determinismo y hashes Windows/Docker. El validator independiente pasó.
- **2E y 2E.1:** A–E en perfiles bounded/exact, regresiones, idempotencia E2E, validación independiente de plan, recuperación de timeout, dominancia y verificación Docker.
- **2F/2F.1:** recuperación de jobs, concurrencia con workers, health/metrics/logs, publicación transaccional, UX de resultados y mapa de corte.
- **2F.4:** benchmark de 97 prendas y suite específica; el resultado informado es 4 cortes, 77.0225 m y 66.580942% de eficiencia.
- **2F.5:** benchmark de 216 prendas en motores heuristic, CP-SAT e híbrido; smoke offline con API, worker, web, PostgreSQL y Redis en red interna.
- **2F.7G-8P2:** equivalencia completa de validación sobre 24,445 candidatos congelados; cero falsos válidos/inválidos; hashes de dos procesos y de cold/warm cache iguales; 3.61× de speedup en el caso perfilado.
- **2F.7G-8.1:** 10,000 candidatos evaluados por orientación (0° y 180°), sin starvation; 451 aceptados a 180°, 445 a 0°.
- **2F.7H:** preflight host/Docker con layout hash idéntico; pequeño repetido y medio con validación final `VALIDATED`; la corrida media (40 iteraciones, 1,153.706 s) produjo `227.483 cm`, mejor que el incumbent legado por `2.017 cm` / `0.6254 pp`.

### Precisión sobre la verificación actual

Se intentó una reejecución de la suite backend actual con `pytest apps/api/tests -q`, pero no concluyó dentro de la ventana de verificación de esta consolidación; por tanto no hay resultado final que pueda declararse como prueba actual completa. Los `PASS` anteriores se mantienen como **evidencia histórica**, no se presentan como una reejecución completa hoy. La suite tampoco contiene archivos de prueba dedicados a `global_nesting_search.py`; la evidencia de 2F.7H se produce mediante sus scripts y artefactos versionados.

## Trabajo parcial y deuda concreta

### Geometría y calidad industrial

1. El NFP para concavidades/casos generales sigue parcial. La aceptación de un placement se protege con geometría exacta y el validador independiente, pero no hay un NFP completo que sustituya el fallback.
2. La validación de patrones, tallaje, holguras, costuras y comportamiento material es de ingeniería. Hace falta muestra/población objetivo, revisión de patronista y corte físico antes de cualquier uso productivo.
3. No existe garantía de optimalidad geométrica global.

### Búsqueda global 2F.7H

El módulo `domain/global_nesting_search.py` existe, pero es una primera versión acotada:

- Implementa tres operadores de destrucción: `RANDOM_K_REMOVAL`, `TAIL_REMOVAL` y `WORST_CONTRIBUTOR_REMOVAL`.
- Implementa dos órdenes de reinserción: `LARGEST_FIRST` y `ORIGINAL_ORDER`.
- Usa `top_k=1` y `beam_width=1` en la evidencia guardada.
- No implementa `REGION`, `SLEEVE_CLUSTER`, `SMALL_PIECE`, órdenes restantes de reinserción, adaptación de pesos, compactación local, estudio de ablación ni checkpoint/resume en disco.
- El runner guarda checkpoints de completado greedy, pero la búsqueda global no los reanuda; su resumen declara `CHECKPOINT_RESUME_STATUS = NOT_IMPLEMENTED`.
- El artefacto marca `GLOBAL_SEARCH_PROVEN_USEFUL = NO` porque el 71.16% supera el baseline pero no llega al umbral preferido de 75%; el benchmark S3×3 + XXL×5 no está autorizado.

Además, el resultado de 2F.7H protege el incumbent legado externamente; no carga sus placements como estado inicial. Esto evita regresión en el reporte, pero no equivale a integrar y comparar ambos layouts dentro de una misma ejecución operativa.

## Diseño pendiente o explícitamente fuera de alcance

- NFP/IFP completo y robusto para todos los casos de producción.
- Validación industrial de patrón y datos de tallaje.
- Autenticación, autorización, gestión de usuarios y hardening público.
- DXF/Optitex u otras integraciones CAD/CAM; ERP/MES; inventario; múltiples rollos, referencias, telas o colores por orden.
- Aprobación operativa de las configuraciones, monitorización prolongada y política de soporte/backup.

## Punto exacto de continuación recomendado

La siguiente fase debe ser **cerrar la línea experimental 2F.7H antes de integrarla al producto**. No conviene activar el buscador global en jobs reales ni cambiar el mapa de arquitectura todavía.

1. **Reproducir y cerrar la prueba actual.** Dejar terminar `pytest apps/api/tests -q`, registrar resultado, duración y cualquier fallo. Ejecutar además frontend/Docker si se pretende renovar los PASS de integración.
2. **Convertir 2F.7H en una fase verificable por tests.** Añadir pruebas unitarias para operadores, reparación, no-regresión, validación final, semilla/determinismo y reanudación cuando exista. Hoy la cobertura es evidencia de scripts, no una prueba de regresión de la suite.
3. **Completar evidencia de eficacia.** Ejecutar al menos una segunda semilla/configuración productiva que reproduzca la ganancia; sólo entonces decidir si se autoriza el benchmark S3×3 + XXL×5. Mantener el baseline de 229.500 cm como comparación real, no sólo como floor externo.
4. **Decidir alcance funcional.** Si la búsqueda global resulta reproduciblemente útil, integrar de forma explícita en `PlanningCoordinator`/worker bajo feature flag, con presupuesto, cancelación, métricas, artifactos y fallback al incumbent validado. Éste sí es un cambio arquitectónico: actualizar el mapa con baseline/candidate/compare y validarlo.
5. **Implementar checkpoint/resume real de búsqueda** antes de ejecutarla dentro de una corrida recuperable; reutilizar el contrato de recovery existente sólo si puede reproducir hash/seed/estado de búsqueda.
6. **No levantar el stop gate.** Después de la mejora algorítmica, abordar patrón físico/tallaje y seguridad/operación. Ninguna ganancia de nesting reemplaza esos bloqueadores de producción.

## Archivos de entrada para retomar

- Arquitectura e índice de evidencia: `docs/architecture/costura-optima.architecture.json` (anclado a `5f6965b`; contrastar con HEAD antes de modificar arquitectura).
- Estado operacional integrado: `docs/FASE-2F-reporte.md` y `README.md`.
- Cierre de equivalencia/determinismo: `docs/FASE-2F.7G-8P2-reporte.md` y `artifacts/phase2f7g8p2/summary.json`.
- Resultado de presupuesto orientado: `artifacts/phase2f7g8_1/summary.json`.
- Búsqueda global actual: `apps/api/src/costura_optima/domain/global_nesting_search.py`, `apps/api/scripts/run_phase2f7h_global_search.py` y `artifacts/phase2f7h/summary.json`.

## Estado final que debe asumirse al abrir la siguiente sesión

`PRODUCT_MVP_INTEGRATED = YES`  
`GLOBAL_SEARCH_EXPERIMENT_IMPLEMENTED = YES`  
`GLOBAL_SEARCH_PRODUCT_INTEGRATED = NO`  
`CHECKPOINT_RESUME_FOR_GLOBAL_SEARCH = NO`  
`PATTERN_VALIDATION_STATUS = ENGINEERING`  
`PRODUCTION_READY = NO`  
`STOP_GATE = ACTIVE`
