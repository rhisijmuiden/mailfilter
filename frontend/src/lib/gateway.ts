// Types for Domains, Relay access and Throttling (backend: /api/gateway).
export type Destination = { host: string; port: number; mx: boolean; protocol: "smtp" | "lmtp" };

export type DomainKind = "transport" | "relay";

export type DomainRow = {
  id: number;
  name: string;
  configured: boolean;
  // transport: receives mail and delivers it to a mail server; relay: outbound only.
  kind: DomainKind;
  sender_lock: boolean;
  smtp_accounts: number;
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

export type SmtpAccount = { id: number; username: string; domain: string; description: string; enabled: boolean };
export type SmtpAccountCreated = SmtpAccount & { password: string };
export type DomainRecords = {
  domain: string;
  dkim: { name: string; selector: string; type: string; value: string; key_type: string } | null;
  spf_include: string;
  spf_new: string;
  dmarc_name: string;
  dmarc_suggested: string;
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

export type Hold = {
  id: number;
  kind: "ip" | "account";
  value: string;
  reason: "spam" | "volume" | "manual";
  detail: { total?: number; spam?: number; minutes?: number; usual?: number };
  automatic: boolean;
  created_at: string;
  released_at: string | null;
  released_by: string | null;
  held_messages: number;
};
export type AbuseSettings = {
  enabled: boolean; window_minutes: number; spam_min: number; spam_percent: number; volume_min: number;
  volume_factor: number; quiet_hours: number; exempt: string[]; notify: string[];
};
