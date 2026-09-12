import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { test, vi } from "vitest";
import { api } from "../api";
import { OrdersPage } from "./OrdersPage";

vi.mock("../api", () => ({ api: { listOrders: vi.fn() } }));

test("shows paginated order history and latest run result", async () => {
  vi.mocked(api.listOrders).mockResolvedValue({ items: [{ id: "order-123456", created_at: "2026-09-04T12:00:00Z",
    garment_model: { display_name: "Camiseta", version_code: "v1" }, total_quantity: 75,
    latest_run: { id: "run-123456", status: "SUCCEEDED", created_at: "2026-09-04T12:01:00Z", result_available: true } }],
    page: 1, page_size: 20, total: 1, pages: 1 } as never);
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}><MemoryRouter><OrdersPage /></MemoryRouter></QueryClientProvider>);
  expect(await screen.findByRole("heading", { name: "Órdenes de producción" })).toBeInTheDocument();
  expect(await screen.findByRole("link", { name: "order-12" })).toHaveAttribute("href", "/orders/order-123456");
  expect(screen.getByText("Disponible")).toBeInTheDocument();
});
