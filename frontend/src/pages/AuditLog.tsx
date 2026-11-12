// Audit log: who changed what and when. Admins see everything; customers see
// their own actions and the entries about their own domains.
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronLeft, ChevronRight, Download, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { DataTable, type Column } from "../components/DataTable";
import { Card, PageHead, formatDate } from "../components/ui";
import { api, qs } from "../lib/api";
import type { Me } from "../lib/auth";

type Entry = {
  id: number; at: string; user_id: number | null; user_email: string | null; user_name: string | null;
  action: string; target: string; domain: string | null; detail: Record<string, unknown>; ip: string | null;
};
type Page = { items: Entry[]; total: number; page: number; page_size: number };
type Filters = { actions: string[]; users: { id: number; email: string | null }[]; domains: string[] };

const PAGE_SIZE = 50;
const PERIODS = ["1", "7", "30", "90", "all"] as const;

// Action groups for the filter, e.g. "dns.publish.spf" -> "dns", "quarantine_released" -> "quarantine".
const GROUPS: [string, RegExp][] = [
  ["login", /^(login|password_changed)/],
  ["2fa", /^2fa_|^security\./],
  ["user", /^user_/],
  ["quarantine", /^quarantine_/],
  ["sender", /^sender_|^abuse_/],
  ["domain", /^domain_|^address_route_|^tls_policy_|^gateway_/],
  ["relay", /^relay_|^smtp_account_/],
  ["throttling", /^throttling_/],
  ["dns", /^dns\.|^cloudflare\.|^dkim_/],
  ["backup", /^backup\./],
  ["system", /^system\.|^settings\./],
  ["pmg_import", /^pmg_import\./],
  ["dmarc", /^dmarc\./],
  ["rules", /^custom_rule_/],
  ["cluster", /^cluster\./],
  ["lists", /^list_/],
  ["mailtrap", /^mailtrap_/],
];

function groupOf(action: string): string {
  return GROUPS.find(([, re]) => re.test(action))?.[0] ?? "other";
}

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

export function actionLabel(t: (k: string, o?: Record<string, unknown>) => string, action: string): string {
  const key = action.replace(/\./g, "_");
  const generic = action.replace(/[._]/g, " ");
  if (action.startsWith("dns.publish.")) return t("audit.actions.dns_publish", { kind: action.slice(12).toUpperCase() });
  return t(`audit.actions.${key}`, { defaultValue: generic.charAt(0).toUpperCase() + generic.slice(1) });
}

function value(v: unknown): string {
  if (v === null || v === undefined) return "–";
  if (typeof v === "boolean") return v ? "✓" : "✗";
  if (Array.isArray(v)) return v.map(value).join(", ") || "–";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}

function since(period: string): string | undefined {
  if (period === "all") return undefined;
  return new Date(Date.now() - Number(period) * 86400_000).toISOString();
}

export default function AuditLog({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const [group, setGroup] = useState("");
  const [userId, setUserId] = useState("");
  const [domain, setDomain] = useState("");
  const [period, setPeriod] = useState<string>("30");
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [dq, group, userId, domain, period]);

  const filters = useQuery({ queryKey: ["audit-filters"], queryFn: () => api.get<Filters>("/audit/filters") });
  const actions = filters.data?.actions ?? [];
  // A group filter becomes an exact action when it covers one action only.
  const groupActions = group ? actions.filter((a) => groupOf(a) === group) : [];
  const actionParam = group ? groupActions.join(",") || "__none__" : undefined;
  const params = {
    q: dq || undefined,
    user_id: userId || undefined,
    domain: domain || undefined,
    since: since(period),
  };
  const list = useQuery({
    queryKey: ["audit", params, actionParam, page],
    queryFn: () => api.get<Page>(`/audit${qs({ ...params, action: actionParam, page, page_size: PAGE_SIZE })}`),
    placeholderData: keepPreviousData,
  });
  const groups = Array.from(new Set(actions.map(groupOf)));
  const pages = list.data ? Math.max(1, Math.ceil(list.data.total / PAGE_SIZE)) : 1;
  const exportHref = `/api/audit/export.csv${qs({ ...params, action: actionParam })}`;

  const tone = (action: string) => (/failed/.test(action) ? "s-bad" : /deleted|removed|disabled|reset|paused/.test(action) ? "s-warn" : "s-mute");
  const columns: Column<Entry>[] = [
    { key: "time", label: t("audit.time"), sort: (e) => e.at, nowrap: true, mobile: "aside",
      render: (e) => <span className="num">{formatDate(e.at, i18n.language)}</span> },
    { key: "who", label: t("audit.who"), sort: (e) => e.user_name || e.user_email || "",
      render: (e) => (
        <>
          <div className="cell-main">{e.user_name || e.user_email || t("audit.system")}</div>
          <div className="cell-sub">
            {e.user_name && e.user_email ? e.user_email : ""}
            {e.ip && <span className="mono">{e.user_email ? " · " : ""}{e.ip}</span>}
          </div>
        </>
      ) },
    { key: "what", label: t("audit.what"), sort: (e) => actionLabel(t, e.action), mobile: "title",
      render: (e) => (
        <>
          <div className="cell-main">
            <span className={`status ${tone(e.action)}`} style={{ paddingLeft: 9 }}>{actionLabel(t, e.action)}</span>
          </div>
          <div className="cell-sub" style={{ wordBreak: "break-all", marginTop: 4 }}>
            {e.target}
            {e.domain && e.domain !== e.target && !e.target.endsWith("@" + e.domain) && (
              <span className="tag" style={{ marginLeft: 6 }}>{e.domain}</span>
            )}
          </div>
        </>
      ) },
    { key: "details", label: t("audit.details"),
      render: (e) =>
        Object.keys(e.detail).length === 0 ? (
          <span className="muted">–</span>
        ) : (
          <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
            {Object.entries(e.detail).map(([k, v]) => (
              <span key={k} className="tag" style={{ whiteSpace: "normal", wordBreak: "break-word" }}>
                <span className="muted">{t(`audit.fields.${k}`, { defaultValue: k.replace(/_/g, " ") })}:</span> {value(v)}
              </span>
            ))}
          </div>
        ) },
  ];

  return (
    <>
      <PageHead
        title={t("audit.title")}
        sub={me.role === "admin" ? t("audit.sub_admin") : t("audit.sub_customer")}
        actions={
          <>
            <a className="btn" href={exportHref} download>
              <Download /> {t("audit.export")}
            </a>
            <button className="btn" onClick={() => list.refetch()}>
              <RefreshCw /> {t("common.refresh")}
            </button>
          </>
        }
      />
      <Card flush>
        <DataTable
          id="audit"
          rows={list.data?.items}
          rowKey={(e) => e.id}
          loading={list.isLoading}
          columns={columns}
          search={{ value: q, onChange: setQ, placeholder: t("audit.search") }}
          tools={
            <>
              <select className="select inline" value={group} onChange={(e) => setGroup(e.target.value)} aria-label={t("audit.action")}>
                <option value="">{t("audit.all_actions")}</option>
                {groups.map((g) => (
                  <option key={g} value={g}>{t(`audit.groups.${g}`)}</option>
                ))}
              </select>
              {me.role === "admin" && (
                <select className="select inline" value={userId} onChange={(e) => setUserId(e.target.value)} aria-label={t("audit.user")}>
                  <option value="">{t("audit.all_users")}</option>
                  {filters.data?.users.map((u) => (
                    <option key={u.id} value={u.id}>{u.email}</option>
                  ))}
                </select>
              )}
              {(filters.data?.domains.length ?? 0) > 0 && (
                <select className="select inline" value={domain} onChange={(e) => setDomain(e.target.value)} aria-label={t("audit.domain")}>
                  <option value="">{t("audit.all_domains")}</option>
                  {filters.data?.domains.map((d) => (
                    <option key={d} value={d}>{d}</option>
                  ))}
                </select>
              )}
              <select className="select inline" value={period} onChange={(e) => setPeriod(e.target.value)} aria-label={t("audit.period")}>
                {PERIODS.map((p) => (
                  <option key={p} value={p}>{t(`audit.period_${p}`)}</option>
                ))}
              </select>
            </>
          }
          empty={t("audit.empty")}
          footer={
            list.data && list.data.total > 0 && (
              <>
                <span className="muted">{t("audit.count", { total: list.data.total, page, pages })}</span>
                <div style={{ display: "flex", gap: 6 }}>
                  <button className="btn small" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label={t("audit.prev")}>
                    <ChevronLeft />
                  </button>
                  <button className="btn small" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label={t("audit.next")}>
                    <ChevronRight />
                  </button>
                </div>
              </>
            )
          }
        />
      </Card>
    </>
  );
}

