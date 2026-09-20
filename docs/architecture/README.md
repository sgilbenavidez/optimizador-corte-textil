# Arquitectura de COSTURA ÓPTIMA

## Propósito

Este directorio contiene el índice arquitectónico versionado del proyecto. Sirve para identificar rápidamente los límites y componentes afectados antes de abrir código. No reemplaza leer el código que se va a modificar: si existe contradicción, **CODE_WINS**.

## Archivos

- `costura-optima.architecture.json`: especificación fuente de Archify.
- `costura-optima.architecture.html`: vista interactiva, producida sólo tras una validación y entrega exitosas.
- `costura-optima.architecture.svg`: representación estática, producida sólo desde un artefacto aceptado.

La especificación actual modela Frontend, API/Application, Worker/Queue, Optimization Domain, Pattern Engineering y Persistence. El flujo principal es `Usuario → Web → FastAPI → ProductionOrder/OptimizationRun → Redis/RQ → Worker → Candidate/Geometry → Solvers → Plan Validator → PostgreSQL → Results → Web`.

## Evidencia de fuente auditada

La auditoría de este baseline verificó estas rutas del árbol de trabajo:

| Componente | Fuente |
| --- | --- |
| Web | `apps/web/src/App.tsx`, `apps/web/src/api.ts` |
| API, health y métricas | `apps/api/src/costura_optima/main.py`, `api/routes.py`, `operational.py` |
| Órdenes y runs | `application/services.py`, `application/planning_coordinator.py`, `domain/production_models.py` |
| Queue y worker | `worker/queue.py`, `worker/tasks.py`, `worker/recovery.py` |
| Candidate/geometry/markers | `domain/candidate_generator.py`, `candidate_funnel.py`, `nesting_engine.py`, `marker_validator.py` |
| Solvers y validación | `operational_heuristic_solver.py`, `production_solver.py`, `production_plan_validator.py` |
| Pattern engineering | `patterns/profile.py`, `patterns/generator.py`, `patterns/persistence.py` |
| Persistencia | `infrastructure/db_models.py`, `repositories.py`, `database.py`, `alembic/versions/` |
| Runtime y pruebas | `compose.yaml`, `apps/api/tests/`, `apps/web/src/**/*.test.*` |

Archify sólo permite enlaces `SRC` embebidos si cada ruta está fijada a un commit. Parte de esta arquitectura está presente como cambios no confirmados en el árbol actual, por lo que el JSON no incluye enlaces `SRC` aún: añadirlos antes de confirmar esos archivos falsearía el anclaje de revisión. Tras confirmar la arquitectura vigente, fije `meta.repository` al SHA completo y mueva estas rutas a `components[].sources`.

## Abrir, regenerar y validar

Desde la raíz del repositorio, mantenga el modo offline de Archify:

```powershell
$env:ARCHIFY_UPDATE_CHECK_DISABLED='1'
$archify = 'C:\Users\Goose\.agents\skills\archify\bin\archify.mjs'
node $archify validate architecture docs/architecture/costura-optima.architecture.json --quality showcase --json
node $archify deliver architecture docs/architecture/costura-optima.architecture.json docs/architecture/costura-optima.architecture.html --quality showcase --json
node $archify visual-check docs/architecture/costura-optima.architecture.html --json
```

Abra el HTML resultante en un navegador local. Para SVG, use la exportación SVG de la vista interactiva una vez entregada; no trate una salida de una validación fallida como una representación del baseline.

## Cuándo actualizar

Actualice el JSON si cambian un componente principal, dependencia, queue, datastore, solver, límite de servicio, flujo runtime, integración u ownership arquitectónico. No lo actualice para copy, CSS, tests menores o refactors internos sin efecto arquitectónico.

## Workflow pre-cambio

`READ ARCHITECTURE MAP → IDENTIFY AFFECTED COMPONENTS → READ ONLY RELEVANT SOURCE → IMPLEMENT → TEST → ARCHITECTURE CHANGED?`

Si la respuesta es sí: `UPDATE ARCHIFY JSON → VALIDATE → ARCHITECTURE DELTA`.

Para un delta, copie el JSON anterior como baseline y use el JSON editado como candidate:

```powershell
node $archify compare architecture docs/architecture/baseline.json docs/architecture/candidate.json --repo-root . --json
```

Archive el resultado Before / Delta / After junto con el cambio arquitectónico. No se requiere compare para cambios internos menores.
