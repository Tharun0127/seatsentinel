import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export default function BookingsPage() {
  const bookings = useQuery({ queryKey: ["my-bookings"], queryFn: api.myBookings });

  if (bookings.isPending) return <p className="text-slate-500">Loading...</p>;
  if (bookings.isError) return <p className="text-rose-600">Could not load your bookings.</p>;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">My bookings</h1>
      {bookings.data.length === 0 && <p className="text-slate-500">No bookings yet.</p>}
      <ul className="space-y-3">
        {bookings.data.map((b) => (
          <li key={b.reference} className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <p className="font-semibold">{b.event_name}</p>
                <p className="text-sm text-slate-500">{new Date(b.starts_at).toLocaleString()}</p>
              </div>
              <p className="text-lg font-bold">${b.amount}</p>
            </div>
            <p className="mt-2 text-sm">Seats: {b.seats.join(", ")}</p>
            <p className="mt-1 font-mono text-xs text-slate-400">
              {b.reference} · booked {new Date(b.booked_at).toLocaleString()} · strategy {b.strategy}
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}
