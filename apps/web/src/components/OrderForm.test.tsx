import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";
import { OrderForm } from "./OrderForm";
import type { GarmentSize } from "../types";

const sizes: GarmentSize[] = [
  { id: "size-p", code: "P", label: "P", display_order: 0, measurements: [] },
  { id: "size-g", code: "G", label: "G", display_order: 1, measurements: [] },
];

test("renders sizes supplied by the backend and updates the total", async () => {
  const user = userEvent.setup();
  render(<OrderForm sizes={sizes} submitting={false} onSubmit={vi.fn()} />);

  expect(screen.getByLabelText("Cantidad talla P")).toBeInTheDocument();
  expect(screen.getByLabelText("Cantidad talla G")).toBeInTheDocument();
  expect(screen.queryByLabelText("Cantidad talla XS")).not.toBeInTheDocument();

  await user.type(screen.getByLabelText("Cantidad talla P"), "3");
  await user.type(screen.getByLabelText("Cantidad talla G"), "2");
  expect(screen.getByText("5")).toBeInTheDocument();
});

test("rejects an empty order in the frontend", async () => {
  const user = userEvent.setup();
  const submit = vi.fn();
  render(<OrderForm sizes={sizes} submitting={false} onSubmit={submit} />);

  await user.click(screen.getByRole("button", { name: "Optimizar corte" }));
  expect(screen.getByRole("alert")).toHaveTextContent("al menos una talla");
  expect(submit).not.toHaveBeenCalled();
});

test("submits only positive integer quantities", async () => {
  const user = userEvent.setup();
  const submit = vi.fn();
  render(<OrderForm sizes={sizes} submitting={false} onSubmit={submit} />);

  await user.type(screen.getByLabelText("Cantidad talla G"), "7");
  await user.click(screen.getByRole("button", { name: "Optimizar corte" }));
  expect(submit).toHaveBeenCalledWith([{ size_code: "G", quantity: 7 }]);
});

test("Enter advances to the next size field", async () => {
  const user = userEvent.setup();
  render(<OrderForm sizes={sizes} submitting={false} onSubmit={vi.fn()} />);
  const first = screen.getByLabelText("Cantidad talla P");
  const second = screen.getByLabelText("Cantidad talla G");
  await user.click(first);
  await user.keyboard("{Enter}");
  expect(second).toHaveFocus();
});
