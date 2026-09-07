import { Link, Route, Routes } from "react-router-dom";
import { NewOrderPage } from "./pages/NewOrderPage";
import { OrderPreparedPage } from "./pages/OrderPreparedPage";
import { PatternInspectionPage } from "./pages/PatternInspectionPage";
import { NestingLabPage } from "./pages/NestingLabPage";
import { OptimizationRunPage } from "./pages/OptimizationRunPage";
import { OrdersPage } from "./pages/OrdersPage";
import { MarkerPage } from "./pages/MarkerPage";

export default function App() {
  return (
    <div className="app-shell">
      <nav className="topbar" aria-label="Navegación principal">
        <Link to="/" className="brand"><span className="brand-mark" aria-hidden="true">CO</span><span>Costura Óptima</span></Link>
        <div className="topbar-links"><Link to="/orders">Órdenes</Link><Link to="/patterns">Patrones técnicos</Link><Link to="/nesting-lab">Nesting Lab</Link><span className="product-state">Operación</span></div>
      </nav>
      <Routes>
        <Route path="/" element={<NewOrderPage />} />
        <Route path="/orders/:orderId" element={<OrderPreparedPage />} />
        <Route path="/orders" element={<OrdersPage />} />
        <Route path="/patterns" element={<PatternInspectionPage />} />
        <Route path="/nesting-lab" element={<NestingLabPage />} />
        <Route path="/optimization-runs/:runId" element={<OptimizationRunPage />} />
        <Route path="/markers/:markerHash" element={<MarkerPage />} />
      </Routes>
    </div>
  );
}
