// The left menu. Each feature adds its entries here; `group` decides the section.
import type { LucideIcon } from "lucide-react";
import { LayoutDashboard, Users } from "lucide-react";

export type NavGroup = "overview" | "mail" | "config" | "system";

export type NavItem = {
  to: string;
  label: string; // i18n key
  icon: LucideIcon;
  group: NavGroup;
  adminOnly?: boolean;
  // Optional badge count, e.g. messages waiting in quarantine.
  badgeQuery?: string;
};

export const GROUPS: NavGroup[] = ["overview", "mail", "config", "system"];

export const NAV: NavItem[] = [
  { to: "/", label: "nav.dashboard", icon: LayoutDashboard, group: "overview" },
  { to: "/users", label: "nav.users", icon: Users, group: "system", adminOnly: true },
];
