import { useState } from "react";
import { Navigate, NavLink, Route, Routes, useNavigate } from "react-router-dom";
import { getToken, setToken } from "./api";
import LoginPage from "./pages/LoginPage";
import EventsPage from "./pages/EventsPage";
import SeatMapPage from "./pages/SeatMapPage";
import CheckoutPage from "./pages/CheckoutPage";
import BookingsPage from "./pages/BookingsPage";

function navClass({ isActive }: { isActive: boolean }) {
  return `rounded-md px-3 py-1.5 text-sm font-medium ${
    isActive ? "bg-slate-900 text-white" : "text-slate-600 hover:bg-slate-200"
  }`;
}

export default function App() {
  const [loggedIn, setLoggedIn] = useState(Boolean(getToken()));
  const navigate = useNavigate();

  function logout() {
    setToken(null);
    setLoggedIn(false);
    navigate("/login");
  }

  const requireLogin = (page: JSX.Element) => (loggedIn ? page : <Navigate to="/login" replace />);

  return (
    <div className="min-h-screen">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-5xl items-center justify-between px-4 py-3">
          <NavLink to="/" className="text-lg font-bold tracking-tight">
            Seat<span className="text-indigo-600">Sentinel</span>
          </NavLink>
          {loggedIn && (
            <nav className="flex items-center gap-1">
              <NavLink to="/" end className={navClass}>
                Events
              </NavLink>
              <NavLink to="/bookings" className={navClass}>
                My bookings
              </NavLink>
              <button onClick={logout} className="ml-2 text-sm text-slate-500 hover:text-slate-900">
                Log out
              </button>
            </nav>
          )}
        </div>
      </header>
      <main className="mx-auto max-w-5xl px-4 py-6">
        <Routes>
          <Route path="/login" element={<LoginPage onLogin={() => setLoggedIn(true)} />} />
          <Route path="/" element={requireLogin(<EventsPage />)} />
          <Route path="/events/:id" element={requireLogin(<SeatMapPage />)} />
          <Route path="/checkout" element={requireLogin(<CheckoutPage />)} />
          <Route path="/bookings" element={requireLogin(<BookingsPage />)} />
        </Routes>
      </main>
    </div>
  );
}
