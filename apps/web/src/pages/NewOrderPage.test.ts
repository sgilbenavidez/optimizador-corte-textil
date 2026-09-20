import { expect, test, vi } from "vitest";
import { OptimizationIntent } from "./NewOrderPage";

test("reuses order and Idempotency-Key for retries of one user intent", async () => {
  const randomUUID = vi.spyOn(crypto, "randomUUID").mockReturnValue("11111111-1111-4111-8111-111111111111");
  const intent = new OptimizationIntent();
  expect(intent.idempotencyKey()).toBe(intent.idempotencyKey());
  const createOrder = vi.fn().mockResolvedValue({ id: "same-order" });
  const [firstOrder, retryOrder] = await Promise.all([
    intent.getOrderId(createOrder), intent.getOrderId(createOrder),
  ]);
  expect(firstOrder).toBe("same-order");
  expect(retryOrder).toBe("same-order");
  expect(await intent.getOrderId(createOrder)).toBe("same-order");
  expect(createOrder).toHaveBeenCalledTimes(1);
  expect(randomUUID).toHaveBeenCalledTimes(1);
  randomUUID.mockRestore();
});
