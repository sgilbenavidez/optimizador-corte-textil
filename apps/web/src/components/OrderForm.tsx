import { FormEvent, useMemo, useState } from "react";
import type { GarmentSize } from "../types";

type Props = {
  sizes: GarmentSize[];
  submitting: boolean;
  onSubmit: (demand: Array<{ size_code: string; quantity: number }>) => void;
};

export function OrderForm({ sizes, submitting, onSubmit }: Props) {
  const [values, setValues] = useState<Record<string, string>>({});
  const [error, setError] = useState<string | null>(null);

  const parsed = useMemo(
    () =>
      sizes.map((size) => {
        const raw = values[size.code] ?? "";
        const numeric = raw === "" ? 0 : Number(raw);
        return { size_code: size.code, quantity: numeric };
      }),
    [sizes, values],
  );
  const validNumbers = parsed.every((line) => Number.isInteger(line.quantity) && line.quantity >= 0);
  const total = validNumbers ? parsed.reduce((sum, line) => sum + line.quantity, 0) : 0;

  function submit(event: FormEvent) {
    event.preventDefault();
    if (!validNumbers) {
      setError("Usa únicamente cantidades enteras iguales o mayores que cero.");
      return;
    }
    if (total === 0) {
      setError("Ingresa al menos una talla con cantidad mayor que cero.");
      return;
    }
    setError(null);
    onSubmit(parsed.filter((line) => line.quantity > 0));
  }

  return (
    <form onSubmit={submit} className="order-form" noValidate>
      <div className="section-heading">
        <div>
          <p className="eyebrow">Pedido</p>
          <h2>Cantidades por talla</h2>
        </div>
        <p className="keyboard-hint">Tab o Enter para avanzar</p>
      </div>

      <div className="size-grid" role="group" aria-label="Cantidades por talla">
        {sizes.map((size) => (
          <label className="size-field" key={size.id}>
            <span>{size.label}</span>
            <input
              aria-label={`Cantidad talla ${size.label}`}
              type="number"
              inputMode="numeric"
              min="0"
              step="1"
              placeholder="0"
              value={values[size.code] ?? ""}
              onFocus={(event) => event.currentTarget.select()}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                const fields = Array.from(
                  event.currentTarget.form?.querySelectorAll<HTMLInputElement>('input[type="number"]') ?? [],
                );
                const nextField = fields[fields.indexOf(event.currentTarget) + 1];
                if (nextField) nextField.focus();
                else event.currentTarget.form?.querySelector<HTMLButtonElement>('button[type="submit"]')?.focus();
              }}
              onChange={(event) => {
                setValues((current) => ({ ...current, [size.code]: event.target.value }));
                setError(null);
              }}
            />
          </label>
        ))}
      </div>

      {error && <p className="form-error" role="alert">{error}</p>}

      <div className="order-footer">
        <div className="total-block" aria-live="polite">
          <span>Total prendas</span>
          <strong>{total}</strong>
        </div>
        <button className="primary-button" type="submit" disabled={submitting || sizes.length === 0}>
          {submitting ? "Iniciando optimización…" : "Optimizar corte"}
        </button>
      </div>
      <p className="phase-note">Se guardará la orden y se iniciará una corrida reproducible de geometría y planificación.</p>
    </form>
  );
}
