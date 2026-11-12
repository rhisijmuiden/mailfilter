// Page routes. Each feature adds its page here (and its menu entry in nav.ts).
// Pages load on first visit (code splitting), so the first screen stays small.
import { lazy, type ReactNode } from "react";
import type { Me } from "./lib/auth";

const Account = lazy(() => import("./pages/Account"));
const AuditLog = lazy(() => import("./pages/AuditLog"));
const Backups = lazy(() => import("./pages/Backups"));
const Cluster = lazy(() => import("./pages/Cluster"));
const CustomRules = lazy(() => import("./pages/CustomRules"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Dmarc = lazy(() => import("./pages/Dmarc"));
const Domains = lazy(() => import("./pages/Domains"));
const Dns = lazy(() => import("./pages/Dns"));
const Mailtrap = lazy(() => import("./pages/Mailtrap"));
const MailSending = lazy(() => import("./pages/MailSending"));
const Notifications = lazy(() => import("./pages/Notifications"));
const ImportPmg = lazy(() => import("./pages/ImportPmg"));
const Lists = lazy(() => import("./pages/Lists"));
const Quarantine = lazy(() => import("./pages/Quarantine"));
const QuarantineReport = lazy(() => import("./pages/QuarantineReport"));
const RelayAccess = lazy(() => import("./pages/RelayAccess"));
const Security = lazy(() => import("./pages/Security"));
const Throttling = lazy(() => import("./pages/Throttling"));
const Trace = lazy(() => import("./pages/Trace"));
const Users = lazy(() => import("./pages/Users"));

export type AppRoute = { path: string; element: (me: Me) => ReactNode; adminOnly?: boolean };

export const ROUTES: AppRoute[] = [
  { path: "/", element: (me) => <Dashboard me={me} /> },
  { path: "/trace", element: () => <Trace /> },
  { path: "/quarantine", element: (me) => <Quarantine me={me} /> },
  { path: "/dmarc", element: (me) => <Dmarc me={me} /> },
  { path: "/quarantine/report", element: (me) => <QuarantineReport me={me} /> },
  { path: "/mailtrap", element: (me) => <Mailtrap me={me} /> },
  { path: "/lists", element: (me) => <Lists me={me} /> },
  { path: "/account", element: (me) => <Account me={me} /> },
  { path: "/domains", element: (me) => <Domains me={me} /> },
  { path: "/relay", element: (me) => <RelayAccess me={me} /> },
  { path: "/rules", element: () => <CustomRules /> },
  { path: "/throttling", element: () => <Throttling />, adminOnly: true },
  { path: "/users", element: () => <Users />, adminOnly: true },
  { path: "/dns", element: () => <Dns />, adminOnly: true },
  { path: "/system/mail", element: () => <MailSending />, adminOnly: true },
  { path: "/system/notifications", element: () => <Notifications />, adminOnly: true },
  { path: "/system/cluster", element: () => <Cluster />, adminOnly: true },
  { path: "/system/backups", element: () => <Backups />, adminOnly: true },
  { path: "/audit", element: (me) => <AuditLog me={me} /> },
  { path: "/system/security", element: (me) => <Security me={me} />, adminOnly: true },
  { path: "/system/import-pmg", element: () => <ImportPmg />, adminOnly: true },
];
