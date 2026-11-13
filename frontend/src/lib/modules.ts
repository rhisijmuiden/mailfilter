// Optional modules an admin switches on and off under System > Modules.
// Must match MODULES in backend/mailfilter/modules.py.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export const MODULE_KEYS = [
  "usage", "monthly_report", "quarantine_report", "spam_reports", "dmarc", "link_protection",
  "archive", "mailtrap", "cloud", "api", "cluster", "pmg_import",
] as const;
export type ModuleKey = (typeof MODULE_KEYS)[number];

export type ModuleInfo = { key: ModuleKey; enabled: boolean; default: boolean; gateway: boolean; per_node: boolean; in_use: number };
export type ModulesOut = { enabled: Record<ModuleKey, boolean>; modules: ModuleInfo[] | null; cluster_member: boolean };

export function useModules() {
  return useQuery({ queryKey: ["modules"], queryFn: () => api.get<ModulesOut>("/modules"), staleTime: 60_000 });
}

// While loading, a module counts as on, so pages do not flash "switched off".
export function moduleOn(data: ModulesOut | undefined, key: ModuleKey | undefined): boolean {
  return !key || !data || data.enabled[key] !== false;
}
