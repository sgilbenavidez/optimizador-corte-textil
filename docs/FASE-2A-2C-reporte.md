# COSTURA ÓPTIMA — Reporte de FASE 2A + FASE 2C

**Fecha:** 2026-09-01  
**Alcance:** foundation, catálogo versionado y flujo de órdenes  
**Stop gate:** activo para FASE 2B/2D/2E

## Arquitectura realmente implementada

Se implementó un monorepo con dos aplicaciones:

```text
React/Vite Web → FastAPI → application services → repositories → PostgreSQL
                                      └→ snapshots JSON + SHA-256
```

El backend separa `domain`, `application`, `infrastructure` y `api`. La lógica de demanda y snapshots no está en los endpoints. No se crearon microservicios.

`GeometryEngine` y `OptimizationEngine` son interfaces `Protocol` sin dependencia de Shapely, Clipper u OR-Tools. No existe implementación ni mock que aparente optimización.

## Árbol relevante

```text
/
├── apps/
│   ├── api/
│   │   ├── alembic/versions/20260901_0001_initial.py
│   │   ├── src/costura_optima/
│   │   │   ├── api/routes.py
│   │   │   ├── application/{schemas.py,services.py}
│   │   │   ├── domain/{engines.py,enums.py,errors.py,order_rules.py}
│   │   │   └── infrastructure/{database.py,db_models.py,repositories.py,seed.py,seed_data.py}
│   │   └── tests/{test_catalog.py,test_orders.py}
│   └── web/
│       ├── src/components/OrderForm.tsx
│       ├── src/pages/{NewOrderPage.tsx,OrderPreparedPage.tsx}
│       ├── src/{api.ts,types.ts,styles.css}
│       └── src/components/OrderForm.test.tsx
├── compose.yaml
├── Makefile
├── README.md
└── docs/
```

## Migración y tablas

Migración: `20260901_0001_initial`.

Tablas principales:

- `garment_types`, `garment_models`, `garment_model_versions`;
- `size_definitions`, `measurement_values`;
- `fabric_configurations`, `cutting_table_configurations`;
- `production_orders`, `production_order_demands`.

La migración se aplicó desde una base PostgreSQL 16 vacía mediante Docker Compose. Alembic confirmó `PostgresqlImpl` y revisión `20260901_0001 (head)`. El seed se ejecutó repetidamente y conservó exactamente 1 tipo, 1 modelo, 1 versión, 7 tallas, 91 mediciones, 1 tela y 1 mesa.

## Seed

- modelo: `TSHIRT_REGULAR_STRAIGHT_ADULT`;
- versión: `TSHIRT-REGULAR-STRAIGHT-ADULT-v0`;
- lifecycle: `ENGINEERING`;
- validation: `UNVALIDATED_FOR_PRODUCTION`;
- market: `ADULT_UNISEX_STRAIGHT_COMMERCIAL`;
- IDs deterministas UUIDv5;
- provenance `ASSUMPTION` y referencia a la aprobación condicionada de FASE 0;
- medidas corporales y de prenda de las siete tallas;
- mesa 800/700 cm, máximo 30 capas;
- jersey 180/176 cm, no direccional, clearance 0.5 cm y márgenes de 2 cm.

## Endpoints

```text
GET  /api/v1/health
GET  /api/v1/garment-models
GET  /api/v1/garment-model-versions/{id}
GET  /api/v1/garment-model-versions/{id}/sizes
GET  /api/v1/fabric-configurations
GET  /api/v1/cutting-table-configurations
POST /api/v1/production-orders
GET  /api/v1/production-orders
GET  /api/v1/production-orders/{id}
```

## Frontend

La pantalla “Nueva orden” obtiene modelos, tallas, tela y mesa desde el API. Muestra el aviso experimental, configuración de corte, siete inputs dinámicos, total en tiempo real, navegación Tab/Enter y validación local.

“Optimizar corte” solo guarda la orden y abre “Orden preparada para optimización”. Esta vista vuelve a consultar el backend, muestra demanda reconstruida, versiones, recursos y hash, y declara explícitamente que no existen consumos ni eficiencias calculados.

## Snapshots

Cada orden guarda un snapshot canónico con:

- modelo/version, provenance, tallas y mediciones;
- tela, mesa y hashes de configuración;
- transformaciones aprobadas 0°/180°, no 90°, mirror false;
- sobreproducción `max(2, ceil(demand × 0.03))`;
- estrategia lexicográfica y timeout 120 s;
- estado de timeout `FEASIBLE_NOT_PROVEN_BEST`.

La lectura usa ese snapshot. Una prueba modifica después el nombre del catálogo y verifica que la orden y su hash no cambian.

## Pruebas ejecutadas

- Build de imágenes API + Web: **PASS**.
- Backend dentro de la imagen: **17 passed**.
- Frontend dentro de la imagen: **4 passed**.
- TypeScript + build Vite: **PASS**.
- Migración desde PostgreSQL 16 vacío: **PASS**.
- Migración desde base SQLite vacía para tests rápidos: **PASS**.
- Compilación offline de la migración para dialecto PostgreSQL: **PASS**.
- Seed ejecutado dos veces: **PASS**, mismos conteos.
- `docker compose config`: **PASS**.
- `docker compose up -d`: **PASS**; PostgreSQL healthy y API/Web activos.
- Health checks API + Web HTTP 200: **PASS**.
- Round-trip HTTP real del caso B sobre PostgreSQL: **PASS**; total 75, siete tallas reconstruidas y hash idéntico al volver a consultar.

### Casos A–E

| Caso | Resultado |
|---|---|
| A — S=3 | orden creada, total 3, ceros reconstruidos |
| B — S=3, M=20, L=10, XL=12, XXL=30 | orden creada, total 75 |
| C — 1 de cada talla | orden creada, total 7 |
| D — M=100 | orden creada, total 100 |
| E — XXXL=31 | orden creada, total 31 |

Los cinco casos se ejecutaron además mediante HTTP contra el PostgreSQL del stack Docker; todos retornaron 201, estado `PREPARED_FOR_OPTIMIZATION` y siete tallas reconstruidas.

EMPTY, NEGATIVE, DECIMAL, UNKNOWN y talla duplicada retornan 422. Configuración inexistente retorna 404. Modelo no habilitado retorna 422.

## Incidente Docker corregido

El primer build web fallaba con `ERR_PNPM_IGNORED_BUILDS`. La causa era el orden del Dockerfile: copiaba `package.json` y el lockfile, pero no `pnpm-workspace.yaml`, antes de `pnpm install`. Por ello la política versionada que autoriza exclusivamente el postinstall de `esbuild` no existía dentro de esa capa.

Corrección aplicada:

- copiar `pnpm-workspace.yaml` antes de instalar;
- mantener `allowBuilds.esbuild = true`;
- añadir `.dockerignore` en API y Web;
- añadir `test:run` para evitar que Vitest quede en watch dentro de Compose.

Después de la corrección ambas imágenes construyeron y el stack completo inició correctamente.

## Deuda técnica

1. La cola/worker no se agrega todavía porque no existe corrida de optimización autorizada.
2. No hay autenticación ni multiempresa, fuera del alcance aprobado.
3. La UI no incluye listado histórico avanzado; el endpoint de listado ya existe.
4. Los valores `ENGINEERING` permiten órdenes de prueba y siempre muestran el aviso de no producción.
5. FastAPI/Starlette emite una advertencia de deprecación de `TestClient` con `httpx`; no afecta las pruebas actuales, pero debe resolverse al actualizar dependencias.

## Comandos

```bash
docker compose up --build
make test
docker compose run --rm api alembic upgrade head
docker compose run --rm api python -m costura_optima.infrastructure.seed
```

## Estado

El código, las imágenes y los flujos funcionales están completos y fueron verificados sobre PostgreSQL real en Docker.

```text
FASE_2A_FOUNDATION = PASS
FASE_2C_ORDER_FLOW = PASS
STOP_GATE = ACTIVE
IMPLEMENTATION_AUTHORIZED_FOR_NEXT_PHASE = NO
```
