import { useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api, type Seat } from "../api";
import { formatSeconds, useCountdown } from "../useCountdown";

// Colour per seat state. "mine" variants stand out so you can find your seats.
function seatClass(seat: Seat, selected: boolean): string {
  if (selected) return "bg-indigo-500 text-white border-indigo-600";
  if (seat.state === "booked") return seat.mine ? "bg-emerald-600 text-white" : "bg-slate-300 text-slate-500";
  if (seat.state === "held") return seat.mine ? "bg-indigo-200 border-indigo-500 text-indigo-900" : "bg-amber-200 text-amber-900";
  return "bg-white hover:bg-indigo-50 border-slate-300";
}

export default function SeatMapPage() {
  const eventId = Number(useParams().id);
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [selected, setSelected] = useState<number[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  // Poll every 2 s: other users' holds and bookings appear without a reload.
  const seats = useQuery({
    queryKey: ["seats", eventId],
    queryFn: () => api.seats(eventId),
    refetchInterval: 2000,
  });
  const event = useQuery({ queryKey: ["events"], queryFn: api.events });
  const eventInfo = event.data?.find((e) => e.id === eventId);

  const allSeats = seats.data ?? [];
  const myHolds = allSeats.filter((s) => s.state === "held" && s.mine);
  // The earliest-expiring hold decides when the whole selection is lost.
  const holdDeadline = useMemo(() => {
    const ms = myHolds.map((s) => s.hold_expires_in_ms ?? 0);
    return ms.length ? Date.now() + Math.min(...ms) : null;
    // Recompute only when the server data changes, not every render.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [seats.dataUpdatedAt]);
  const secondsLeft = useCountdown(holdDeadline);

  // If someone else takes a seat I had selected, drop it and say so.
  const lostSeats = allSeats.filter((s) => selected.includes(s.id) && s.state !== "free");
  if (lostSeats.length && !busy) {
    setSelected((current) => current.filter((id) => !lostSeats.some((s) => s.id === id)));
    setError(`Someone else just grabbed ${lostSeats.map((s) => s.row_label + s.number).join(", ")}.`);
  }

  function toggle(seat: Seat) {
    if (seat.state !== "free") return;
    setError(null);
    setSelected((current) =>
      current.includes(seat.id) ? current.filter((id) => id !== seat.id) : [...current, seat.id].slice(0, 10),
    );
  }

  async function holdSelected() {
    setBusy(true);
    setError(null);
    try {
      await api.hold(eventId, selected);
      setSelected([]);
    } catch (err) {
      // e.g. 409 "sorry, seat A5 was just taken by someone else"
      setError((err as Error).message);
    } finally {
      setBusy(false);
      queryClient.invalidateQueries({ queryKey: ["seats", eventId] });
    }
  }

  async function releaseHolds() {
    await api.release(eventId, myHolds.map((s) => s.id));
    queryClient.invalidateQueries({ queryKey: ["seats", eventId] });
  }

  function checkout() {
    navigate("/checkout", { state: { eventId, eventName: eventInfo?.name, seats: myHolds } });
  }

  if (seats.isPending) return <p className="text-slate-500">Loading seat map...</p>;
  if (seats.isError) return <p className="text-rose-600">Could not load the seat map.</p>;

  const rows = [...new Set(allSeats.map((s) => s.row_label))];
  const holdTotal = myHolds.reduce((sum, s) => sum + Number(s.price), 0);

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-bold">{eventInfo?.name ?? "Seat map"}</h1>
        <p className="text-slate-500">{eventInfo?.venue}</p>
      </div>

      {error && (
        <div className="rounded-md border border-rose-200 bg-rose-50 px-4 py-3 text-sm text-rose-700">{error}</div>
      )}

      <div className="overflow-x-auto rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <div className="mx-auto mb-4 w-2/3 rounded bg-slate-800 py-1 text-center text-xs uppercase tracking-widest text-white">
          Stage
        </div>
        <div className="space-y-1.5">
          {rows.map((row) => (
            <div key={row} className="flex items-center justify-center gap-1">
              <span className="w-5 text-center text-xs font-semibold text-slate-500">{row}</span>
              {allSeats
                .filter((s) => s.row_label === row)
                .map((seat) => (
                  <button
                    key={seat.id}
                    onClick={() => toggle(seat)}
                    disabled={seat.state !== "free"}
                    title={`${seat.row_label}${seat.number} · $${seat.price} · ${seat.state}`}
                    className={`h-7 w-7 rounded border text-[10px] font-medium transition ${seatClass(
                      seat,
                      selected.includes(seat.id),
                    )} disabled:cursor-not-allowed`}
                  >
                    {seat.number}
                  </button>
                ))}
            </div>
          ))}
        </div>
        <Legend />
      </div>

      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        {myHolds.length > 0 ? (
          <div>
            <p className="font-semibold">
              Holding {myHolds.map((s) => s.row_label + s.number).join(", ")} · ${holdTotal.toFixed(2)}
            </p>
            <p className={`text-sm ${secondsLeft !== null && secondsLeft < 60 ? "text-rose-600" : "text-slate-500"}`}>
              Your hold expires in {formatSeconds(secondsLeft ?? 0)}
            </p>
          </div>
        ) : (
          <p className="text-sm text-slate-500">
            {selected.length ? `${selected.length} seat(s) selected` : "Pick up to 10 free seats."}
          </p>
        )}
        <div className="flex gap-2">
          {selected.length > 0 && (
            <button
              onClick={holdSelected}
              disabled={busy}
              className="rounded-md bg-indigo-600 px-4 py-2 font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
            >
              Hold {selected.length} seat(s) for 5 min
            </button>
          )}
          {myHolds.length > 0 && (
            <>
              <button onClick={releaseHolds} className="rounded-md border border-slate-300 px-4 py-2 text-sm">
                Release
              </button>
              <button
                onClick={checkout}
                className="rounded-md bg-emerald-600 px-4 py-2 font-semibold text-white hover:bg-emerald-700"
              >
                Continue to payment
              </button>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

function Legend() {
  const items = [
    ["bg-white border-slate-300", "Free"],
    ["bg-indigo-500", "Selected"],
    ["bg-indigo-200 border-indigo-500", "Held by you"],
    ["bg-amber-200", "Held by someone"],
    ["bg-slate-300", "Booked"],
    ["bg-emerald-600", "Your booking"],
  ];
  return (
    <div className="mt-4 flex flex-wrap justify-center gap-4 text-xs text-slate-600">
      {items.map(([cls, label]) => (
        <span key={label} className="flex items-center gap-1.5">
          <span className={`inline-block h-3.5 w-3.5 rounded border ${cls}`} />
          {label}
        </span>
      ))}
    </div>
  );
}
