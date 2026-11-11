import { Ban, Check, Clock, Hourglass, ShieldAlert, X, type LucideIcon } from "lucide-react";

export type Explanation = { title: string; action: string; who: "recipient" | "sender" | "us" | "nobody" | string };
export type Recipient = {
  address: string; status: string; dsn: string | null; reply: string | null; relay_host: string | null; relay_ip: string | null;
  transport: string | null; delay: number | null; attempts: number; updated: string | null; explanation: Explanation | null;
};
export type Message = {
  queue_id: string; first_seen: string; last_seen: string; direction: "inbound" | "outbound" | null; status: string;
  sender: string | null; client_host: string | null; client_ip: string | null; sasl_user: string | null; message_id: string | null;
  size: number | null; spam_score: number | null; spam_action: string | null; is_bounce: boolean; recipients: Recipient[];
  explanation: Explanation | null;
};
export type RouteNode = { kind: "source" | "filter" | "destination"; name: string | null; ip: string | null; detail: string | null; status: string | null };
export type Detail = Message & {
  symbols: { name: string; score: number }[]; route: RouteNode[]; parent: string | null; bounces: string[]; held: boolean;
  quarantine_actions: { action: string; learned: string | null; user_email: string; at: string }[];
};
export type Stats = { hours: number; total: number; inbound: number; outbound: number; by_status: Record<string, number>; quarantined_now: number };

type Tone = "ok" | "warn" | "bad" | "info" | "mute";
const MESSAGE: Record<string, [Tone, LucideIcon]> = {
  delivered: ["ok", Check], sent: ["ok", Check],
  partly_delivered: ["warn", Check], deferred: ["warn", Clock],
  bounced: ["bad", X], expired: ["bad", X],
  quarantined: ["info", ShieldAlert],
  rejected: ["mute", Ban],
  queued: ["mute", Hourglass],
};

export function statusMeta(status: string): { tone: Tone; Icon: LucideIcon } {
  const [tone, Icon] = MESSAGE[status] ?? ["mute", Hourglass];
  return { tone, Icon };
}

export function formatSize(bytes: number | null | undefined): string {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
