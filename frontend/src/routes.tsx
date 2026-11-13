// Page routes. Each feature adds its page here (and its menu entry in nav.ts).
// Pages load on first visit (code splitting), so the first screen stays small.
import { lazy, type ReactNode } from "react";
import type { Me } from "./lib/auth";
import type { ModuleKey } from "./lib/modules";

const Account = lazy(() => import("./pages/Account"));
const ApiAccess = lazy(() => import("./pages/ApiAccess"));
const Archive = lazy(() => import("./pages/Archive"));
const AuditLog = lazy(() => import("./pages/AuditLog"));
const Backups = lazy(() => import("./pages/Backups"));
const Branding = lazy(() => import("./pages/Branding"));
const CloudMail = lazy(() => import("./pages/CloudMail"));
const Cluster = lazy(() => import("./pages/Cluster"));
const CustomRules = lazy(() => import("./pages/CustomRules"));
const Dashboard = lazy(() => import("./pages/Dashboard"));
const Dmarc = lazy(() => import("./pages/Dmarc"));
const Domains = lazy(() => import("./pages/Domains"));
const Dns = lazy(() => import("./pages/Dns"));
const Modules = lazy(() => import("./pages/Modules"));
const MonthlyReport = lazy(() => import("./pages/MonthlyReport"));
const Mailtrap = lazy(() => import("./pages/Mailtrap"));
const MailSending = lazy(() => import("./pages/MailSending"));
const Notifications = lazy(() => import("./pages/Notifications"));
const ImportPmg = lazy(() => import("./pages/ImportPmg"));
const LinkClicks = lazy(() => import("./pages/LinkClicks"));
const Lists = lazy(() => import("./pages/Lists"));
const Quarantine = lazy(() => import("./pages/Quarantine"));
const QuarantineReport = lazy(() => import("./pages/QuarantineReport"));
const RelayAccess = lazy(() => import("./pages/RelayAccess"));
const SpamReports = lazy(() => import("./pages/SpamReports"));
const Security = lazy(() => import("./pages/Security"));
const Throttling = lazy(() => import("./pages/Throttling"));
const Trace = lazy(() => import("./pages/Trace"));
const Usage = lazy(() => import("./pages/Usage"));
const Users = lazy(() => import("./pages/Users"));

// reseller: also open to reseller logins, which only get these routes.
// module: shows "this module is switched off" instead while the module is off (System > Modules).
export type AppRoute = { path: string; element: (me: Me) => ReactNode; adminOnly?: boolean; reseller?: boolean; module?: ModuleKey };

export const ROUTES: AppRoute[] = [
  { path: "/", element: (me) => <Dashboard me={me} /> },
  { path: "/reports/monthly", element: (me) => <MonthlyReport me={me} />, module: "monthly_report" },
  { path: "/trace", element: () => <Trace /> },
  { path: "/quarantine", element: (me) => <Quarantine me={me} /> },
  { path: "/dmarc", element: (me) => <Dmarc me={me} />, module: "dmarc" },
  { path: "/quarantine/report", element: (me) => <QuarantineReport me={me} />, module: "quarantine_report" },
  { path: "/mailtrap", element: (me) => <Mailtrap me={me} />, module: "mailtrap" },
  { path: "/links", element: (me) => <LinkClicks me={me} />, module: "link_protection" },
  { path: "/archive", element: (me) => <Archive me={me} />, module: "archive" },
  { path: "/lists", element: (me) => <Lists me={me} /> },
  { path: "/spam-reports", element: (me) => <SpamReports me={me} />, module: "spam_reports" },
  { path: "/account", element: (me) => <Account me={me} />, reseller: true },
  { path: "/usage", element: (me) => <Usage me={me} />, reseller: true, module: "usage" },
  { path: "/billing", element: (me) => <Usage me={me} tab="billing" />, adminOnly: true, module: "usage" },
  { path: "/domains", element: (me) => <Domains me={me} /> },
  { path: "/relay", element: (me) => <RelayAccess me={me} /> },
  { path: "/cloud", element: (me) => <CloudMail me={me} />, module: "cloud" },
  { path: "/rules", element: () => <CustomRules /> },
  { path: "/throttling", element: () => <Throttling />, adminOnly: true },
  { path: "/users", element: () => <Users />, adminOnly: true },
  { path: "/dns", element: () => <Dns />, adminOnly: true },
  { path: "/system/mail", element: () => <MailSending />, adminOnly: true },
  { path: "/system/notifications", element: () => <Notifications />, adminOnly: true },
  { path: "/system/cluster", element: () => <Cluster />, adminOnly: true, module: "cluster" },
  { path: "/system/backups", element: () => <Backups />, adminOnly: true },
  { path: "/audit", element: (me) => <AuditLog me={me} /> },
  { path: "/system/api", element: (me) => <ApiAccess me={me} />, module: "api" },
  { path: "/system/modules", element: () => <Modules />, adminOnly: true },
  { path: "/system/branding", element: () => <Branding />, adminOnly: true },
  { path: "/system/security", element: (me) => <Security me={me} />, adminOnly: true },
  { path: "/system/import-pmg", element: () => <ImportPmg />, adminOnly: true, module: "pmg_import" },
];
