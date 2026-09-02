# Costura Óptima

Implementación acumulada hasta FASE 2E: catálogo y patrones versionados, geometría/nesting irregular validado y planificación asíncrona de pedidos con OR-Tools CP-SAT.

> El patrón incluido sigue siendo `ENGINEERING / UNVALIDATED_FOR_PRODUCTION`. Los planes usan markers geométricamente validados, pero no constituyen una aprobación industrial del patrón ni una prueba de optimalidad geométrica global.

## Inicio rápido con Docker

Requisitos: Docker Desktop con Docker Compose.

```bash
docker compose up --build
```

El servicio `api` espera PostgreSQL y Redis, ejecuta migraciones/seed/generación de patrón y levanta FastAPI. El servicio `worker` consume la cola RQ `optimization` y publica únicamente planes validados. Después:

- aplicación: <http://localhost:5173>
- inspección técnica de patrones: <http://localhost:5173/patterns>
- API: <http://localhost:8000/api/v1>
- documentación OpenAPI: <http://localhost:8000/docs>

El flujo de **Optimizar corte** crea una orden y un `OptimizationRun` (HTTP 202), muestra progreso por polling y termina en una comparación de soluciones con tendidos y SVG certificados.

Para detener:

```bash
docker compose down
```

### Si el build web falla con `ERR_PNPM_IGNORED_BUILDS`

La imagen copia `pnpm-workspace.yaml` antes de instalar dependencias; allí solo está autorizado el postinstall de `esbuild`, requerido por Vite. Si tienes una capa antigua en caché:

```bash
docker compose build --no-cache web
docker compose up -d
```

No ejecutes `pnpm approve-builds` dentro del contenedor: la política ya está versionada en el repositorio.

Para eliminar también la base local de desarrollo:

```bash
docker compose down -v
```

Este último comando elimina el volumen de PostgreSQL; úsalo únicamente cuando quieras reiniciar los datos.

## Migraciones y seed

```bash
docker compose run --rm api alembic upgrade head
docker compose run --rm api python -m costura_optima.infrastructure.seed
docker compose run --rm api python -m costura_optima.patterns.generate
```

El seed puede ejecutarse repetidamente. Conserva una sola versión de cada registro y crea:

- `T_SHIRT`;
- `TSHIRT_REGULAR_STRAIGHT_ADULT`;
- `TSHIRT-REGULAR-STRAIGHT-ADULT-v0`;
- XS, S, M, L, XL, XXL y XXXL;
- 91 medidas corporales/de prenda con provenance;
- tela jersey de 180/176 cm;
- mesa de 800/700 cm y 30 capas.
- perfil versionado de parámetros geométricos; las coordenadas se generan con el comando explícito, no con el seed.

Para exportar los SVG de revisión, añada `--artifacts-dir` al comando de generación. En desarrollo local puede usar `../../artifacts/patterns` desde `apps/api`.

## Pruebas

Con Docker:

```bash
make test
```

O por aplicación:

```bash
docker compose run --rm api pytest
docker compose run --rm web pnpm test:run
```

El backend cubre además geometría, hashes, generación/poda de candidatos, optimalidad matemática sobre fixtures pequeños, perfiles lexicográficos, dominancia, política exacta, validator independiente, API e idempotencia. El frontend cubre orden, patrón, nesting y resultados de planificación. Ambas suites pueden ejecutarse dentro de sus imágenes.

Los casos obligatorios A–E (también en modo exacto) se pueden repetir contra el stack activo con:

```bash
python apps/api/scripts/run_phase2e_cases.py --exact --output artifacts/phase2e/cases-a-e.json
```

## Desarrollo sin Docker

### API

Desde `apps/api`:

```bash
python -m venv .venv
.venv/Scripts/activate
pip install -e ".[test]"
set DATABASE_URL=postgresql+psycopg://costura:costura@localhost:5432/costura_optima
alembic upgrade head
python -m costura_optima.infrastructure.seed
uvicorn costura_optima.main:app --reload
```

En PowerShell use `$env:DATABASE_URL = "..."` en lugar de `set`.

### Web

Desde `apps/web`:

```bash
corepack enable
pnpm install
pnpm dev
```

Para apuntar a otra API configure `VITE_API_URL`.

## Arquitectura implementada

```text
apps/web                    React + TypeScript + Vite + TanStack Query
apps/api/src/costura_optima
  api                       rutas HTTP y manejo de errores
  application               casos de uso y DTOs
  domain                    reglas puras y contratos de engines
  worker                    tareas RQ, cola Redis y recuperación por lease
  patterns                  Bézier, discretización, validación, versionado y exportación SVG
  infrastructure            SQLAlchemy, repositorios y seed
apps/api/alembic            migraciones
docs                        decisiones y reportes de fase
```

Los endpoints no contienen reglas de negocio. `OrderService` crea el snapshot; `GeometryEngine` produce artifacts certificados; `CandidateCompositionGenerator` poda el espacio; `ProductionPlanner` solo consume longitudes validadas; `IndependentProductionPlanValidator` recomputa el plan antes de publicarlo.

## Garantía histórica

Al crear una orden se persisten:

- IDs exactos de modelo, versión de patrón, tela y mesa;
- snapshot completo del modelo/version, tallas y medidas;
- snapshot de recursos y política aprobada;
- hash SHA-256 del JSON canónico;
- demanda normalizada con solo cantidades positivas.

Las consultas reconstruyen todas las tallas desde el snapshot. Cambiar el catálogo vivo después no modifica la orden histórica.

## Límite de alcance

No se afirma optimalidad geométrica global ni estado production-ready. NFP continúa parcial; no hay DXF/Optitex, multi-roll avanzado, inventario, ERP, múltiples referencias ni múltiples telas/colores por orden. La FASE 2F no está autorizada.
