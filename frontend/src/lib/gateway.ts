// Types for Domains, Relay access and Throttling (backend: /api/gateway).
export type Destination = { host: string; port: number; mx: boolean; protocol: "smtp" | "lmtp" };

export type DomainRow = {
  id: number;
  name: string;
  configured: boolean;
  destinations: Destination[];
  enabled: boolean;
  tls: "may" | "encrypt" | "verify";
  verify_recipients: boolean;
  concurrency: number | null;
  rate_delay: number | null;
  out_per_hour: number | null;
  out_per_day: number | null;
  note: string;
  users: string[];
  relay_networks: number;
};

export type Network = { id: number; cidr: string; description: string; domain: string | null; enabled: boolean; allow_broad: boolean };
export type Port = { id: number; port: number; tls: "may" | "encrypt" | "wrapper"; description: string };
export type Policy = { id?: number; domain: string; concurrency: number | null; rate_delay: number | null; note: string };
export type Defaults = {
  relay_ip_per_minute: number; relay_ip_per_hour: number; domain_per_hour: number; domain_per_day: number;
  user_per_hour: number; concurrency: number;
};
export type Throttling = { defaults: Defaults; policies: Policy[] };
export type AddressRoute = Destination & { id: number; address: string; note: string };
export type TlsPolicy = { id: number; destination: string; level: string; note: string };
export type Problem = { field: string; code: string; en: string; nl: string };
export type GatewayStatus = {
  last_apply: { at: string; ok: boolean; changed?: string[]; warnings?: string[]; step?: string; output?: string } | null;
};

export const emptyDestination = (): Destination => ({ host: "", port: 25, mx: false, protocol: "smtp" });

// "" in a number field means "use the default"
export const numOrNull = (v: string): number | null => (v.trim() === "" ? null : Number(v));
export const numStr = (v: number | null | undefined): string => (v === null || v === undefined ? "" : String(v));

export function describeDestination(d: Destination): string {
  const host = d.host.includes(":") ? `[${d.host}]` : d.host;
  return `${d.protocol === "lmtp" ? "LMTP " : ""}${host}:${d.port}${d.mx ? " (MX)" : ""}`;
}
