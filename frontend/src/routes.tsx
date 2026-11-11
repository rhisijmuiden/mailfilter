// Page routes. Each feature adds its page here (and its menu entry in nav.ts).
import type { ReactNode } from "react";
import type { Me } from "./lib/auth";
import Account from "./pages/Account";
import Dashboard from "./pages/Dashboard";
import Quarantine from "./pages/Quarantine";
import Trace from "./pages/Trace";
import Users from "./pages/Users";

export type AppRoute = { path: string; element: (me: Me) => ReactNode; adminOnly?: boolean };

export const ROUTES: AppRoute[] = [
  { path: "/", element: (me) => <Dashboard me={me} /> },
  { path: "/trace", element: () => <Trace /> },
  { path: "/quarantine", element: (me) => <Quarantine me={me} /> },
  { path: "/account", element: (me) => <Account me={me} /> },
  { path: "/users", element: () => <Users />, adminOnly: true },
];
