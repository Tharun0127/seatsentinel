import { FormEvent, useRef, useState } from "react";
import { Link, Navigate, useLocation } from "react-router-dom";
import { api, ApiError, type Confirmation, type Seat } from "../api";

type CheckoutState = { eventId: number; eventName?: string; seats: Seat[] };

// crypto.randomUUID only exists on https/localhost; fall back elsewhere (e.g. a LAN IP).
function newKey(): string {
  if (typeof crypto.randomUUID === "function") return crypto.randomUUID();
  return `${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}-${Math.random().toString(36).slice(2)}`;
}

export default function CheckoutPage() {
  const state = useLocation().state as CheckoutState | null;
  const [card, setCard] = useState("4242 4242 4242 4242");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Confirmation | null>(null);

  // One idempotency key per step, created once and reused on every retry
  // of this checkout. Double-clicks and network retries cannot charge twice.
  const payKey = useRef(newKey());
  const confirmKey = useRef(newKey());
  const paymentId = useRef<string | null>(null);

  if (!state?.seats?.length) return <Navigate to="/" replace />;
  const seatIds = state.seats.map((s) => s.id);
  const total = state.seats.reduce((sum, s) => sum + Number(s.price), 0);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      if (!paymentId.current) {
        const payment = await api.pay(state!.eventId, seatIds, card.replace(/\s/g, "").slice(-4), payKey.current);
        paymentId.current = payment.payment_id;
      }
      setDone(await api.confirm(paymentId.current, confirmKey.current));
    } catch (err) {
      const message = (err as Error).message;
      // A 409 "still being processed" is safe to retry with the same keys.
      setError(err instanceof ApiError && err.status >= 500 ? `${message}. You can safely retry.` : message);
    } finally {
      setBusy(false);
    }
  }

  if (done) {
    return (
      <div className="mx-auto max-w-md space-y-4 rounded-xl border border-emerald-200 bg-white p-6 shadow-sm">
        <h1 className="text-2xl font-bold text-emerald-700">Booking confirmed</h1>
        <p>
          {state.eventName} · seats {state.seats.map((s) => s.row_label + s.number).join(", ")}
        </p>
        <p className="font-mono text-xs text-slate-500">Reference {done.reference}</p>
        {done.late_hold_recovered && (
          <p className="rounded bg-amber-50 p-2 text-sm text-amber-800">
            Your hold had just expired, but the seats were still free, so we booked them anyway.
          </p>
        )}
        <Link to="/bookings" className="inline-block text-indigo-600 hover:underline">
          View my bookings →
        </Link>
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-md space-y-4">
      <h1 className="text-2xl font-bold">Mock payment</h1>
      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <p className="font-semibold">{state.eventName}</p>
        <p className="text-sm text-slate-600">Seats {state.seats.map((s) => s.row_label + s.number).join(", ")}</p>
        <p className="mt-2 text-xl font-bold">${total.toFixed(2)}</p>
      </div>
      <form onSubmit={submit} className="space-y-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <label className="block text-sm font-medium">
          Card number
          <input
            className="mt-1 w-full rounded-md border border-slate-300 px-3 py-2 font-mono"
            value={card}
            onChange={(e) => setCard(e.target.value)}
            pattern="[0-9 ]{12,23}"
            required
          />
        </label>
        <div className="flex gap-3">
          <input className="w-1/2 rounded-md border border-slate-300 px-3 py-2" defaultValue="12/30" />
          <input className="w-1/2 rounded-md border border-slate-300 px-3 py-2" defaultValue="123" />
        </div>
        <p className="text-xs text-slate-500">This is a mock: no real card is charged and nothing is stored.</p>
        {error && <p className="rounded bg-rose-50 p-2 text-sm text-rose-700">{error}</p>}
        <button
          disabled={busy}
          className="w-full rounded-md bg-emerald-600 py-2 font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
        >
          {busy ? "Processing..." : `Pay $${total.toFixed(2)}`}
        </button>
      </form>
      <Link to={`/events/${state.eventId}`} className="text-sm text-indigo-600 hover:underline">
        ← Back to seat map
      </Link>
    </div>
  );
}
