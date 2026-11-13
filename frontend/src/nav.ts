// The left menu. Each feature adds its entries here; `group` decides the section.
import type { LucideIcon } from "lucide-react";
import { Archive, Braces, Building2, Flag, FileChartColumn, FlaskConical, AtSign, BellRing, Boxes, Cloud, DatabaseBackup, FileInput, Gauge, Globe, KeyRound, LayoutDashboard, ListChecks, MailCheck, MousePointerClick, Network, Receipt, ScrollText, Search, Send, ShieldAlert, ShieldCheck, SlidersHorizontal, Users } from "lucide-react";

export type NavGroup = "overview" | "mail" | "config" | "system";

export type NavItem = {
  to: string;
  label: string; // i18n key
  icon: LucideIcon;
  group: NavGroup;
  adminOnly?: boolean;
  // Shown to reseller logins too (they only get these entries).
  reseller?: boolean;
  // Optional count shown next to the entry; see BADGES in components/Layout.tsx.
  badge?: "quarantine" | "mailtrap";
  // Optional module: only shown when the module says so (see MODULES in components/Layout.tsx).
  module?: "mailtrap";
};

export const GROUPS: NavGroup[] = ["overview", "mail", "config", "system"];

export const NAV: NavItem[] = [
  { to: "/", label: "nav.dashboard", icon: LayoutDashboard, group: "overview" },
  { to: "/reports/monthly", label: "nav.monthly_report", icon: FileChartColumn, group: "overview" },
  { to: "/usage", label: "nav.usage", icon: Receipt, group: "overview", reseller: true },
  { to: "/trace", label: "nav.trace", icon: Search, group: "mail" },
  { to: "/quarantine", label: "nav.quarantine", icon: ShieldAlert, group: "mail", badge: "quarantine" },
  { to: "/mailtrap", label: "nav.mailtrap", icon: FlaskConical, group: "mail", badge: "mailtrap", module: "mailtrap" },
  { to: "/links", label: "nav.links", icon: MousePointerClick, group: "mail" },
  { to: "/archive", label: "nav.archive", icon: Archive, group: "mail" },
  { to: "/lists", label: "nav.lists", icon: ListChecks, group: "mail" },
  { to: "/spam-reports", label: "nav.spam_reports", icon: Flag, group: "mail" },
  { to: "/quarantine/report", label: "nav.quarantine_report", icon: MailCheck, group: "mail" },
  { to: "/dmarc", label: "nav.dmarc", icon: ShieldCheck, group: "mail" },
  { to: "/domains", label: "nav.domains", icon: AtSign, group: "config" },
  { to: "/relay", label: "nav.relay", icon: Network, group: "config" },
  { to: "/cloud", label: "nav.cloud", icon: Cloud, group: "config" },
  { to: "/rules", label: "nav.custom_rules", icon: SlidersHorizontal, group: "config" },
  { to: "/throttling", label: "nav.throttling", icon: Gauge, group: "config", adminOnly: true },
  { to: "/dns", label: "nav.dns", icon: Globe, group: "config", adminOnly: true },
  { to: "/users", label: "nav.users", icon: Users, group: "system", adminOnly: true },
  { to: "/billing", label: "nav.billing", icon: Building2, group: "system", adminOnly: true },
  { to: "/system/security", label: "nav.security", icon: KeyRound, group: "system", adminOnly: true },
  { to: "/audit", label: "nav.audit", icon: ScrollText, group: "system" },
  { to: "/system/api", label: "nav.api", icon: Braces, group: "system" },
  { to: "/system/mail", label: "nav.mail_sending", icon: Send, group: "system", adminOnly: true },
  { to: "/system/notifications", label: "nav.notifications", icon: BellRing, group: "system", adminOnly: true },
  { to: "/system/cluster", label: "nav.cluster", icon: Boxes, group: "system", adminOnly: true },
  { to: "/system/backups", label: "nav.backups", icon: DatabaseBackup, group: "system", adminOnly: true },
  { to: "/system/import-pmg", label: "nav.import_pmg", icon: FileInput, group: "system", adminOnly: true },
];
