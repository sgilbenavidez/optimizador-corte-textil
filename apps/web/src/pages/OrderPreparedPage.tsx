import { useMutation, useQuery } from "@tanstack/react-query";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api } from "../api";

export function OrderPreparedPage() {
  const { orderId = "" } = useParams();
  const navigate = useNavigate();
  const orderQuery = useQuery({ queryKey: ["order", orderId], queryFn: () => api.getOrder(orderId), enabled: Boolean(orderId) });
  const runs = useQuery({ queryKey: ["order-runs", orderId], queryFn: () => api.listOrderRuns(orderId), enabled: Boolean(orderId) });
  const optimize = useMutation({ mutationFn: () => api.createOptimizationRun(orderId, {}, crypto.randomUUID()), onSuccess: (run) => navigate(`/optimization-runs/${run.id}`) });
  if (orderQuery.isLoading) return <main className="workspace"><div className="loading-card">Consultando orden guardada…</div></main>;
  if (orderQuery.error || !orderQuery.data) return <main className="workspace"><div className="load-error">No fue posible consultar la orden.</div></main>;
  const order = orderQuery.data;
  return <main className="workspace">
    <header className="page-header"><div><p className="eyebrow">Orden {order.id.slice(0, 8)}</p><h1>{order.garment_model.display_name}</h1><p className="intro">Versión {order.garment_model.version_code} · {order.total_quantity} prendas · {new Date(order.created_at).toLocaleString("es-CO")}</p></div><button className="primary-button" disabled={optimize.isPending} onClick={() => optimize.mutate()}>{optimize.isPending ? "Creando corrida…" : "Nueva optimización"}</button></header>
    <div className="warning-banner"><span aria-hidden="true">!</span>Patrón experimental de ingeniería — no validado para producción.</div>
    <section className="order-detail-grid"><div className="prepared-card"><h2>Demanda</h2><div className="saved-demand">{order.demand.map((line) => <div key={line.size_code} className={line.quantity ? "has-demand" : ""}><span>{line.size_code}</span><strong>{line.quantity}</strong></div>)}</div></div>
      <aside className="config-card"><h2>Recursos congelados</h2><dl><div><dt>Tela</dt><dd>{order.fabric_configuration.display_name}</dd></div><div><dt>Ancho útil</dt><dd>{order.fabric_configuration.usable_width_cm} cm</dd></div><div><dt>Mesa</dt><dd>{order.cutting_table_configuration.display_name}</dd></div><div><dt>Largo útil</dt><dd>{order.cutting_table_configuration.usable_length_cm / 100} m</dd></div><div><dt>Patrón</dt><dd>ENGINEERING</dd></div></dl></aside></section>
    <section className="runs-panel"><div className="section-heading"><div><p className="eyebrow">Historial inmutable</p><h2>Corridas asociadas</h2></div><span>{runs.data?.total ?? 0} corridas</span></div>
      {runs.isLoading && <div className="loading-card">Cargando corridas…</div>}{runs.error && <div className="load-error">No fue posible cargar las corridas. <button onClick={() => runs.refetch()}>Reintentar</button></div>}
      {runs.data?.items.length === 0 && <div className="empty-state">Esta orden todavía no tiene corridas.</div>}
      <ol className="run-history">{runs.data?.items.slice().reverse().map((run, index) => <li key={run.id}><div><strong>Run #{index + 1}</strong><code>{run.id}</code></div><span>{new Date(run.created_at).toLocaleString("es-CO")}</span><span className={`status-pill status-${run.status}`}>{run.status}</span><Link to={`/optimization-runs/${run.id}`}>{run.best_solution_available ? "Ver resultado" : "Abrir corrida"}</Link></li>)}</ol>
    </section>
    {optimize.error && <p className="form-error" role="alert">{optimize.error.message}</p>}
  </main>;
}
