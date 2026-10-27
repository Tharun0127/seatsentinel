import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { api } from "../api";

export default function EventsPage() {
  const events = useQuery({ queryKey: ["events"], queryFn: api.events });

  if (events.isPending) return <p className="text-slate-500">Loading events...</p>;
  if (events.isError) return <p className="text-rose-600">Could not load events.</p>;

  return (
    <div className="space-y-4">
      <h1 className="text-2xl font-bold">Upcoming events</h1>
      <ul className="grid gap-4 md:grid-cols-3">
        {events.data.map((event) => (
          <li key={event.id}>
            <Link
              to={`/events/${event.id}`}
              className="block h-full rounded-xl border border-slate-200 bg-white p-5 shadow-sm transition hover:border-indigo-400"
            >
              <p className="font-semibold">{event.name}</p>
              <p className="text-sm text-slate-500">{event.venue}</p>
              <p className="mt-2 text-sm">{new Date(event.starts_at).toLocaleString()}</p>
              <p className="mt-3 text-sm text-slate-600">
                {event.seats_total - event.seats_booked} of {event.seats_total} seats left
              </p>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
