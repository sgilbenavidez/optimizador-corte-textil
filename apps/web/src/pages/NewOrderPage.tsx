import { useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import { OrderForm } from "../components/OrderForm";

export class OptimizationIntent {
  private key: string | null = null;
  private orderId: string | null = null;
  private pendingOrder: Promise<string> | null = null;
  idempotencyKey() { this.key ??= crypto.randomUUID(); return this.key; }
  getOrderId(create: () => Promise<{ id: string }>) {
    if (this.orderId) return Promise.resolve(this.orderId);
    this.pendingOrder ??= create().then((order) => {
      this.orderId = order.id;
      return order.id;
    }).finally(() => { this.pendingOrder = null; });
    return this.pendingOrder;
  }
}

export function NewOrderPage() {
  const navigate = useNavigate();
  const [versionId, setVersionId] = useState("");
  const modelsQuery = useQuery({ queryKey: ["garment-models"], queryFn: api.listGarmentModels });
  const fabricsQuery = useQuery({ queryKey: ["fabrics"], queryFn: api.listFabrics });
  const tablesQuery = useQuery({ queryKey: ["tables"], queryFn: api.listTables });
  const versions = useMemo(() => modelsQuery.data?.flatMap((model) => model.versions) ?? [], [modelsQuery.data]);

  useEffect(() => {
    if (!versionId && versions[0]) setVersionId(versions[0].id);
  }, [versionId, versions]);

  const sizesQuery = useQuery({
    queryKey: ["sizes", versionId],
    queryFn: () => api.listSizes(versionId),
    enabled: Boolean(versionId),
  });
  const selectedVersion = versions.find((version) => version.id === versionId);
  const fabric = fabricsQuery.data?.[0];
  const table = tablesQuery.data?.[0];
  const intentRef = useRef(new OptimizationIntent());
  const optimize = useMutation({
    mutationFn: async (payload: unknown) => {
      const orderId = await intentRef.current.getOrderId(() => api.createOrder(payload));
      return api.createOptimizationRun(orderId, {}, intentRef.current.idempotencyKey());
    },
    onSuccess: (run) => navigate(`/optimization-runs/${run.id}`),
  });

  const loading = modelsQuery.isLoading || fabricsQuery.isLoading || tablesQuery.isLoading || sizesQuery.isLoading;
  const loadError = modelsQuery.error ?? fabricsQuery.error ?? tablesQuery.error ?? sizesQuery.error;

  return (
    <main className="workspace">
      <header className="page-header">
        <div>
          <p className="eyebrow">Nueva orden</p>
          <h1>Prepara el corte con datos versionados</h1>
          <p className="intro">Define la demanda por talla. La orden conservará exactamente el modelo, la tela y la mesa seleccionados.</p>
        </div>
        <span className="phase-badge">FASE 2A + 2C</span>
      </header>

      {loadError && <div className="load-error" role="alert">No fue posible cargar el catálogo. {loadError.message}</div>}
      {loading && <div className="loading-card">Cargando catálogo versionado…</div>}

      {!loading && selectedVersion && fabric && table && (
        <div className="order-layout">
          <section className="main-card">
            <div className="model-picker">
              <label htmlFor="model-version">Modelo</label>
              <select id="model-version" value={versionId} onChange={(event) => setVersionId(event.target.value)}>
                {versions.map((version) => (
                  <option key={version.id} value={version.id}>{version.display_name} · {version.version_code}</option>
                ))}
              </select>
              {selectedVersion.warning && <div className="warning-banner"><span aria-hidden="true">!</span>{selectedVersion.warning}</div>}
            </div>

            <OrderForm
              sizes={sizesQuery.data ?? []}
              submitting={optimize.isPending}
              onSubmit={(demand) => optimize.mutate({
                garment_model_version_id: versionId,
                fabric_configuration_id: fabric.id,
                cutting_table_configuration_id: table.id,
                demand,
              })}
            />
            {optimize.error && <p className="form-error" role="alert">{optimize.error.message}</p>}
          </section>

          <aside className="config-card" aria-label="Configuración de corte">
            <p className="eyebrow">Configuración</p>
            <h2>Recursos del tendido</h2>
            <dl>
              <div><dt>Mesa física</dt><dd>{table.physical_length_cm / 100} m</dd></div>
              <div><dt>Largo útil</dt><dd>{table.usable_length_cm / 100} m</dd></div>
              <div><dt>Ancho tela</dt><dd>{(fabric.physical_width_cm / 100).toFixed(2)} m</dd></div>
              <div><dt>Ancho útil</dt><dd>{(fabric.usable_width_cm / 100).toFixed(2)} m</dd></div>
              <div><dt>Capas máximas</dt><dd>{table.max_layers}</dd></div>
              <div><dt>Separación</dt><dd>{fabric.piece_clearance_cm} cm</dd></div>
            </dl>
            <div className="config-footnote">
              <span>Tela no direccional</span>
              <span>Open width · face one way</span>
            </div>
          </aside>
        </div>
      )}
    </main>
  );
}
