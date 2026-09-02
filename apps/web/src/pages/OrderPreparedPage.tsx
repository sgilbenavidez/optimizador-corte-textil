import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../api";

export function OrderPreparedPage() {
  const { orderId = "" } = useParams();
  const orderQuery = useQuery({ queryKey: ["order", orderId], queryFn: () => api.getOrder(orderId), enabled: Boolean(orderId) });

  if (orderQuery.isLoading) return <main className="workspace"><div className="loading-card">Consultando orden guardada…</div></main>;
  if (orderQuery.error || !orderQuery.data) return <main className="workspace"><div className="load-error">No fue posible consultar la orden.</div></main>;

  const order = orderQuery.data;
  return (
    <main className="workspace prepared-workspace">
      <section className="prepared-card">
        <div className="success-mark" aria-hidden="true">✓</div>
        <p className="eyebrow">Orden guardada</p>
        <h1>Orden preparada para optimización</h1>
        <p className="intro">La demanda y sus configuraciones quedaron congeladas en un snapshot reproducible. Aún no se ejecutó el motor geométrico.</p>

        <div className="prepared-grid">
          <div>
            <span>Modelo</span>
            <strong>{order.garment_model.display_name}</strong>
            <small>{order.garment_model.version_code}</small>
          </div>
          <div>
            <span>Total prendas</span>
            <strong>{order.total_quantity}</strong>
            <small>{new Date(order.created_at).toLocaleString("es-CO")}</small>
          </div>
        </div>

        {order.garment_model.warning && <div className="warning-banner"><span aria-hidden="true">!</span>{order.garment_model.warning}</div>}

        <div className="saved-demand">
          {order.demand.map((line) => (
            <div key={line.size_code} className={line.quantity > 0 ? "has-demand" : ""}>
              <span>{line.size_code}</span><strong>{line.quantity}</strong>
            </div>
          ))}
        </div>

        <dl className="snapshot-details">
          <div><dt>Mesa</dt><dd>{order.cutting_table_configuration.physical_length_cm / 100} m · útil {order.cutting_table_configuration.usable_length_cm / 100} m</dd></div>
          <div><dt>Tela</dt><dd>{(order.fabric_configuration.physical_width_cm / 100).toFixed(2)} m · útil {(order.fabric_configuration.usable_width_cm / 100).toFixed(2)} m</dd></div>
          <div><dt>Snapshot SHA-256</dt><dd><code>{order.snapshot_hash}</code></dd></div>
        </dl>

        <div className="prepared-actions">
          <Link className="secondary-button" to="/">Crear otra orden</Link>
          <span>Sin consumo ni eficiencia calculados</span>
        </div>
      </section>
    </main>
  );
}

