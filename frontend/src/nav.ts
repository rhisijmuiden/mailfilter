// The left menu. Each feature adds its entries here; `group` decides the section.
import type { LucideIcon } from "lucide-react";
import { AtSign, DatabaseBackup, FileInput, Gauge, Globe, LayoutDashboard, Network, Search, Send, ShieldAlert, ShieldCheck, SlidersHorizontal, Users } from "lucide-react";

export type NavGroup = "overview" | "mail" | "config" | "system";

export type NavItem = {
  to: string;
  label: string; // i18n key
  icon: LucideIcon;
  group: NavGroup;
  adminOnly?: boolean;
  // Optional count shown next to the entry; see BADGES in components/Layout.tsx.
  badge?: "quarantine";
};

export const GROUPS: NavGroup[] = ["overview", "mail", "config", "system"];

export const NAV: NavItem[] = [
  { to: "/", label: "nav.dashboard", icon: LayoutDashboard, group: "overview" },
  { to: "/trace", label: "nav.trace", icon: Search, group: "mail" },
  { to: "/quarantine", label: "nav.quarantine", icon: ShieldAlert, group: "mail", badge: "quarantine" },
  { to: "/dmarc", label: "nav.dmarc", icon: ShieldCheck, group: "mail" },
  { to: "/domains", label: "nav.domains", icon: AtSign, group: "config" },
  { to: "/relay", label: "nav.relay", icon: Network, group: "config" },
  { to: "/rules", label: "nav.custom_rules", icon: SlidersHorizontal, group: "config" },
  { to: "/throttling", label: "nav.throttling", icon: Gauge, group: "config", adminOnly: true },
  { to: "/dns", label: "nav.dns", icon: Globe, group: "config", adminOnly: true },
  { to: "/users", label: "nav.users", icon: Users, group: "system", adminOnly: true },
  { to: "/system/mail", label: "nav.mail_sending", icon: Send, group: "system", adminOnly: true },
  { to: "/system/backups", label: "nav.backups", icon: DatabaseBackup, group: "system", adminOnly: true },
  { to: "/system/import-pmg", label: "nav.import_pmg", icon: FileInput, group: "system", adminOnly: true },
];
