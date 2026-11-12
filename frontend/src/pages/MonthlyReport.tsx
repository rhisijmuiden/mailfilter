import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Legend, StackedColumns, type Series } from "../components/charts";
import { Card, ErrorText, Field, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import type { Me } from "../lib/auth";

type Stats = {
  received: number; delivered: number; spam: number; virus: number; refused: number; other: number;
  outbound: number; outbound_blocked: number; stopped: number; stopped_percent: number;
};
type Report = {
  month: string; current: boolean; stored: boolean; customer: number | null; title: string;
  total: Stats; previous: Stats; domains: { name: string; stats: Stats }[];
  customers: { id: number; name: string; email: string; stats: Stats }[];
  history: (Stats & { month: string })[];
};
type Config = { enabled: boolean; day: number; hour: number; admin_summary: boolean };
type Recipient = { id: number; name: string; email: string; language: string; domains: string[]; enabled: boolean; last_month: string | null; sent_at: string | null };
type Settings = { config: Config | null; recipients: Recipient[]; me_enabled: boolean; last_run: string | null; timezone: string };

const ym = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;

function lastMonths(n: number): string[] {
  const now = new Date();
  return Array.from({ length: n }, (_, i) => ym(new Date(now.getFullYear(), now.getMonth() - i, 1)));
}

function Delta({ now, prev }: { now: number; prev: number }) {
  const { t } = useTranslation();
  if (!prev) return <span>{now ? t("monthly.new") : "–"}</span>;
  const pct = Math.round(((now - prev) / prev) * 100);
  return <span>{t("monthly.vs_prev", { change: `${pct > 0 ? "+" : ""}${pct}%` })}</span>;
}

export default function MonthlyReport({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const admin = me.role === "admin";
  const months = useMemo(() => lastMonths(13), []);
  const [month, setMonth] = useState(months[1]);
  const [customer, setCustomer] = useState<number | null>(null);
  const locale = i18n.language === "nl" ? "nl-NL" : "en-GB";
  const monthName = (m: string, short = false) =>
    new Date(`${m}-15T12:00:00Z`).toLocaleDateString(locale, short ? { month: "short" } : { month: "long", year: "numeric" });
  const n = (v: number) => v.toLocaleString(locale);

  const report = useQuery({
    queryKey: ["monthly", month, customer],
    queryFn: () => api.get<Report>(`/monthly-report${qs({ month, customer })}`),
    placeholderData: keepPreviousData,
  });
  const settings = useQuery({ queryKey: ["monthly-settings"], queryFn: () => api.get<Settings>("/monthly-report/settings") });
  const [cfg, setCfg] = useState<Config | null>(null);
  useEffect(() => {
    if (settings.data?.config) setCfg(settings.data.config);
  }, [settings.data]);

  const save = useMutation({
    mutationFn: (body: Partial<{ config: Config; users: { id: number; enabled: boolean }[]; me_enabled: boolean }>) =>
      api.put<Settings>("/monthly-report/settings", body),
    onSuccess: (data) => {
      qc.setQueryData(["monthly-settings"], data);
      toast(t("common.saved"));
    },
  });
  const example = useMutation({
    mutationFn: () => api.post<{ sent_to: string }>("/monthly-report/example", { month, customer }),
    onSuccess: (r) => toast(t("monthly.example_sent", { to: r.sent_to })),
  });

  const series: Series[] = [
    { key: "delivered", label: t("monthly.delivered"), color: "var(--chart-1)" },
    { key: "spam", label: t("monthly.spam"), color: "var(--chart-2)" },
    { key: "virus", label: t("monthly.virus"), color: "var(--chart-5)" },
    { key: "rest", label: t("monthly.refused_other"), color: "var(--chart-3)" },
  ];
  const r = report.data;
  const s = settings.data;
  const kpis = r && [
    { label: t("monthly.received"), value: n(r.total.received), sub: <Delta now={r.total.received} prev={r.previous.received} /> },
    { label: t("monthly.spam"), value: n(r.total.spam), sub: <Delta now={r.total.spam} prev={r.previous.spam} /> },
    { label: t("monthly.virus"), value: n(r.total.virus), sub: <Delta now={r.total.virus} prev={r.previous.virus} /> },
    { label: t("monthly.stopped_pct"), value: `${r.total.stopped_percent.toLocaleString(locale)}%`, sub: t("monthly.stopped_pct_sub") },
  ];
  type TableRow = { id: number | null; name: string; email: string | null; stats: Stats };
  const rows: TableRow[] = !r ? [] : r.customers.length
    ? r.customers.map((c) => ({ id: c.id, name: c.name, email: c.email, stats: c.stats }))
    : r.domains.map((d) => ({ id: null, name: d.name, email: null, stats: d.stats }));
  const hours = Array.from({ length: 24 }, (_, h) => h);
  const days = Array.from({ length: 28 }, (_, d) => d + 1);

  return (
    <>
      <PageHead
        title={t("monthly.title")}
        sub={t("monthly.sub")}
        actions={
          <button className="btn" disabled={example.isPending || !r} onClick={() => example.mutate()}>
            <Send /> {example.isPending ? t("monthly.sending") : t("monthly.example")}
          </button>
        }
      />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <select className="select" style={{ width: "auto" }} aria-label={t("monthly.month")} value={month} onChange={(e) => setMonth(e.target.value)}>
          {months.map((m, i) => (
            <option key={m} value={m}>{monthName(m)}{i === 0 ? ` (${t("monthly.so_far")})` : ""}</option>
          ))}
        </select>
        {admin && (
          <select className="select" style={{ width: "auto" }} aria-label={t("monthly.customer")} value={customer ?? ""}
            onChange={(e) => setCustomer(e.target.value ? +e.target.value : null)}>
            <option value="">{t("monthly.all_customers")}</option>
            {s?.recipients.map((c) => <option key={c.id} value={c.id}>{c.name || c.email}</option>)}
          </select>
        )}
      </div>
      <ErrorText error={report.error || example.error || save.error} />
      {!r ? (
        <Loading />
      ) : (
        <div className={report.isPlaceholderData ? "refetching" : undefined} style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {r.current && <p className="muted" style={{ margin: 0 }}>{t("monthly.current_note")}</p>}
          <div className="grid kpis">
            {kpis!.map((k) => (
              <div key={k.label} className="card kpi">
                <span className="label">{k.label}</span>
                <span className="value">{k.value}</span>
                <span className="delta">{k.sub}</span>
              </div>
            ))}
          </div>
          <div className="grid two">
            <Card title={t("monthly.history")}>
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <Legend series={series} />
                <StackedColumns
                  height={260}
                  data={r.history.map((h) => ({ ...h, rest: h.refused + h.other }))}
                  series={series}
                  label={t("monthly.history")}
                  xLabel={(h) => monthName(h.month)}
                  xTick={(h) => monthName(h.month, true)}
                />
              </div>
            </Card>
            <Card title={t("monthly.details")} flush>
              <div className="tbl-wrap">
                <table>
                  <tbody>
                    {(["delivered", "refused", "other", "outbound", "outbound_blocked"] as const).map((k) => (
                      <tr key={k}>
                        <td>{t(`monthly.${k}`)}</td>
                        <td className="num" style={{ textAlign: "right" }}>{n(r.total[k])}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </Card>
          </div>
          {rows.length > 0 && (
            <Card title={r.customers.length ? t("monthly.per_customer") : t("monthly.per_domain")} flush>
              <div className="tbl-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{r.customers.length ? t("monthly.customer") : t("monthly.domain")}</th>
                      <th style={{ textAlign: "right" }}>{t("monthly.received")}</th>
                      <th style={{ textAlign: "right" }}>{t("monthly.spam")}</th>
                      <th style={{ textAlign: "right" }}>{t("monthly.virus")}</th>
                      <th style={{ textAlign: "right" }}>{t("monthly.outbound")}</th>
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((row) => {
                      const id = row.id;
                      return (
                        <tr key={row.name + (id ?? "")} className={id ? "click" : undefined} tabIndex={id ? 0 : undefined}
                          onClick={() => id && setCustomer(id)} onKeyDown={(e) => id && e.key === "Enter" && setCustomer(id)}>
                          <td>
                            <div className="cell-main">{row.name}</div>
                            {row.email && row.email !== row.name && <div className="cell-sub">{row.email}</div>}
                          </td>
                          <td className="num" style={{ textAlign: "right" }}>{n(row.stats.received)}</td>
                          <td className="num" style={{ textAlign: "right" }}>{n(row.stats.spam)}</td>
                          <td className="num" style={{ textAlign: "right" }}>{n(row.stats.virus)}</td>
                          <td className="num" style={{ textAlign: "right" }}>{n(row.stats.outbound)}</td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
              </div>
            </Card>
          )}
        </div>
      )}

      {s && (
        <div className="grid two">
          {admin && cfg ? (
            <Card title={t("monthly.schedule")}>
              <form className="form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate({ config: cfg }); }}>
                <label className="check full">
                  <input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} />
                  <span>
                    <b style={{ display: "block" }}>{t("monthly.enabled")}</b>
                    <span className="muted" style={{ fontSize: 12.5 }}>{t("monthly.enabled_hint")}</span>
                  </span>
                </label>
                <Field label={t("monthly.day")}>
                  <select className="select" value={cfg.day} onChange={(e) => setCfg({ ...cfg, day: +e.target.value })}>
                    {days.map((d) => <option key={d} value={d}>{d}</option>)}
                  </select>
                </Field>
                <Field label={t("monthly.hour")} hint={t("monthly.hour_hint", { tz: s.timezone })}>
                  <select className="select" value={cfg.hour} onChange={(e) => setCfg({ ...cfg, hour: +e.target.value })}>
                    {hours.map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
                  </select>
                </Field>
                <label className="check full">
                  <input type="checkbox" checked={cfg.admin_summary} onChange={(e) => setCfg({ ...cfg, admin_summary: e.target.checked })} />
                  <span>
                    <b style={{ display: "block" }}>{t("monthly.admin_summary")}</b>
                    <span className="muted" style={{ fontSize: 12.5 }}>{t("monthly.admin_summary_hint")}</span>
                  </span>
                </label>
                <div className="full" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                  <button className="btn primary" disabled={save.isPending}>{t("common.save")}</button>
                  <span className="muted" style={{ fontSize: 12.5 }}>
                    {s.last_run ? t("monthly.last_run", { month: monthName(s.last_run) }) : t("monthly.never_sent")}
                  </span>
                </div>
              </form>
            </Card>
          ) : (
            <Card title={t("monthly.my_mail")}>
              <label className="check">
                <input type="checkbox" checked={s.me_enabled} onChange={(e) => save.mutate({ me_enabled: e.target.checked })} />
                <span>
                  <b style={{ display: "block" }}>{t("monthly.me_enabled")}</b>
                  <span className="muted" style={{ fontSize: 12.5 }}>{t("monthly.me_enabled_hint", { email: me.email })}</span>
                </span>
              </label>
            </Card>
          )}
          {admin && (
            <Card title={t("monthly.recipients")} right={<span className="muted">{t("monthly.recipients_sub")}</span>} flush>
              {!s.recipients.length ? (
                <div className="empty">{t("monthly.no_customers")}</div>
              ) : (
                <div>
                  {s.recipients.map((c) => (
                    <div key={c.id} className="list-row" style={{ padding: "10px 16px" }}>
                      <span className="grow">
                        <span className="cell-main">{c.name || c.email}</span>
                        <span className="cell-sub" style={{ display: "block" }}>
                          {c.domains.join(", ") || t("monthly.no_domains")}
                          {c.sent_at && ` · ${t("monthly.sent_at", { when: formatDate(c.sent_at, i18n.language) })}`}
                        </span>
                      </span>
                      <label className="switch" title={c.enabled ? t("relay.on") : t("relay.off")}>
                        <input type="checkbox" checked={c.enabled} aria-label={t("monthly.toggle", { name: c.name || c.email })}
                          onChange={() => save.mutate({ users: [{ id: c.id, enabled: !c.enabled }] })} />
                        <span />
                      </label>
                    </div>
                  ))}
                </div>
              )}
            </Card>
          )}
        </div>
      )}
    </>
  );
}
