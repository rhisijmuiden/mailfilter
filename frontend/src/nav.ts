// The left menu. Each feature adds its entries here; `group` decides the section.
import type { LucideIcon } from "lucide-react";
import { LayoutDashboard, Search, ShieldAlert, Users } from "lucide-react";

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
  { to: "/users", label: "nav.users", icon: Users, group: "system", adminOnly: true },
];
