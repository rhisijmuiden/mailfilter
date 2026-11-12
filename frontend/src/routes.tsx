// Page routes. Each feature adds its page here (and its menu entry in nav.ts).
import type { ReactNode } from "react";
import type { Me } from "./lib/auth";
import Account from "./pages/Account";
import Backups from "./pages/Backups";
import CustomRules from "./pages/CustomRules";
import Dashboard from "./pages/Dashboard";
import Dmarc from "./pages/Dmarc";
import Domains from "./pages/Domains";
import Dns from "./pages/Dns";
import MailSending from "./pages/MailSending";
import ImportPmg from "./pages/ImportPmg";
import Lists from "./pages/Lists";
import Quarantine from "./pages/Quarantine";
import QuarantineReport from "./pages/QuarantineReport";
import RelayAccess from "./pages/RelayAccess";
import Throttling from "./pages/Throttling";
import Trace from "./pages/Trace";
import Users from "./pages/Users";

export type AppRoute = { path: string; element: (me: Me) => ReactNode; adminOnly?: boolean };

export const ROUTES: AppRoute[] = [
  { path: "/", element: (me) => <Dashboard me={me} /> },
  { path: "/trace", element: () => <Trace /> },
  { path: "/quarantine", element: (me) => <Quarantine me={me} /> },
  { path: "/dmarc", element: (me) => <Dmarc me={me} /> },
  { path: "/quarantine/report", element: (me) => <QuarantineReport me={me} /> },
  { path: "/lists", element: (me) => <Lists me={me} /> },
  { path: "/account", element: (me) => <Account me={me} /> },
  { path: "/domains", element: (me) => <Domains me={me} /> },
  { path: "/relay", element: (me) => <RelayAccess me={me} /> },
  { path: "/rules", element: () => <CustomRules /> },
  { path: "/throttling", element: () => <Throttling />, adminOnly: true },
  { path: "/users", element: () => <Users />, adminOnly: true },
  { path: "/dns", element: () => <Dns />, adminOnly: true },
  { path: "/system/mail", element: () => <MailSending />, adminOnly: true },
  { path: "/system/backups", element: () => <Backups />, adminOnly: true },
  { path: "/system/import-pmg", element: () => <ImportPmg />, adminOnly: true },
];
