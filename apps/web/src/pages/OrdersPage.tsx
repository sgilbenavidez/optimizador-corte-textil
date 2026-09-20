import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";

export function OrdersPage() {
  const [page, setPage] = useState(1);
  const orders = useQuery({ queryKey: ["orders", page], queryFn: () => api.listOrders(page) });
  return <main className="workspace">
    <header className="page-header"><div><p className="eyebrow">Historial</p><h1>Órdenes de producción</h1><p className="intro">Reabre una orden y consulta cada corrida sin perder sus resultados anteriores.</p></div><Link className="primary-button button-link" to="/">Nueva orden</Link></header>
    {orders.isLoading && <div className="loading-card">Cargando órdenes…</div>}
    {orders.error && <div className="load-error" role="alert">No fue posible cargar el historial. <button onClick={() => orders.refetch()}>Reintentar</button></div>}
    {orders.data?.items.length === 0 && <div className="empty-state"><h2>No hay órdenes todavía</h2><Link to="/">Crear la primera orden</Link></div>}
    {orders.data && orders.data.items.length > 0 && <div className="orders-table-wrap"><table className="orders-table"><thead><tr><th>Orden</th><th>Fecha</th><th>Modelo</th><th>Total prendas</th><th>Último run</th><th>Estado</th><th>Resultado</th></tr></thead><tbody>
      {orders.data.items.map((order) => <tr key={order.id}><th><Link to={`/orders/${order.id}`}>{order.id.slice(0, 8)}</Link></th><td>{new Date(order.created_at).toLocaleString("es-CO")}</td><td>{order.garment_model.display_name}</td><td>{order.total_quantity}</td><td>{order.latest_run ? <Link to={`/optimization-runs/${order.latest_run.id}`}>{order.latest_run.id.slice(0, 8)}</Link> : "—"}</td><td><span className={`status-pill status-${order.latest_run?.status ?? "EMPTY"}`}>{order.latest_run?.status ?? "SIN CORRIDAS"}</span></td><td>{order.latest_run?.result_available ? "Disponible" : "—"}</td></tr>)}
    </tbody></table></div>}
    {orders.data && orders.data.pages > 1 && <nav className="pagination" aria-label="Paginación"><button disabled={page === 1} onClick={() => setPage(page - 1)}>Anterior</button><span>Página {page} de {orders.data.pages}</span><button disabled={page === orders.data.pages} onClick={() => setPage(page + 1)}>Siguiente</button></nav>}
  </main>;
}
