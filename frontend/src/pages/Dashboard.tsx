import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { AlertTriangle, CircleCheck, Info, OctagonAlert } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import BlacklistAlert from "../components/BlacklistAlert";
import { BarList, Legend, StackedColumns, type Series } from "../components/charts";
import { Reason, StatusPill } from "../components/TraceBits";
import { Card, Loading, PageHead, formatDate } from "../components/ui";
import { api, qs } from "../lib/api";
import type { Me } from "../lib/auth";
import type { Message } from "../lib/trace";

type Counts = { total: number; inbound: number; outbound: number; delivered: number; spam: number; refused: number; delayed: number; failed: number };
type Bucket = { t: string; delivered: number; spam: number; refused: number; delayed: number; failed: number };
type Alert = { level: "critical" | "warning" | "info"; code: string; params: Record<string, string | number>; link: string | null };
type Dash = {
  range: "24h" | "30d"; timezone: string; direction: string; now: Counts; previous: Counts; series: Bucket[];
  top_senders: { name: string; count: number }[]; top_domains: { name: string; count: number }[];
  queue: Record<string, number> | null; alerts: Alert[];
};

function Delta({ now, prev, range }: { now: number; prev: number; range: string }) {
  const { t } = useTranslation();
  const vs = t(range === "24h" ? "dashboard.vs_yesterday" : "dashboard.vs_previous_month");
  if (!prev) return <span>{now ? t("dashboard.new_vs", { vs }) : "–"}</span>;
  const pct = Math.round(((now - prev) / prev) * 100);
  return <span>{`${pct > 0 ? "+" : ""}${pct}% ${vs}`}</span>;
}

export default function Dashboard({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const admin = me.role === "admin";
  const [range, setRange] = useState<"24h" | "30d">("24h");
  const [direction, setDirection] = useState<"all" | "inbound" | "outbound">("all");
  const [table, setTable] = useState(false);
  const dash = useQuery({
    queryKey: ["dashboard", range, direction],
    queryFn: () => api.get<Dash>(`/dashboard${qs({ range, direction })}`),
    refetchInterval: 60_000,
    placeholderData: keepPreviousData,
  });
  const problems = useQuery({
    queryKey: ["trace", "dashboard-problems"],
    queryFn: () => api.get<{ items: Message[] }>("/trace?status=problem&hours=24&limit=5"),
    refetchInterval: 60_000,
  });
  const d = dash.data;
  const hours = range === "24h" ? 24 : 24 * 30;
  const series: Series[] = [
    { key: "delivered", label: t("dashboard.c_delivered"), color: "var(--chart-1)" },
    { key: "spam", label: t("dashboard.c_spam"), color: "var(--chart-2)" },
    { key: "refused", label: t("dashboard.c_refused"), color: "var(--chart-3)" },
    { key: "delayed", label: t("dashboard.c_delayed"), color: "var(--chart-4)" },
    { key: "failed", label: t("dashboard.c_failed"), color: "var(--chart-5)" },
  ];
  const locale = i18n.language === "nl" ? "nl-NL" : "en-GB";
  const fmtBucket = (iso: string, long: boolean) => {
    // Buckets are hours/days in the server's time zone; label them in that zone too.
    const dt = new Date(iso);
    const timeZone = d?.timezone;
    if (range === "24h") {
      const h = dt.toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit", timeZone });
      return long ? `${dt.toLocaleDateString(locale, { weekday: "short", day: "numeric", month: "short", timeZone })} ${h}` : h;
    }
    return dt.toLocaleDateString(locale, long ? { weekday: "short", day: "numeric", month: "long", timeZone } : { day: "numeric", month: "short", timeZone });
  };
  const pct = (a: number, b: number) => (b ? `${Math.round((a / b) * 100)}%` : "–");
  const dirQ = direction === "all" ? "" : `&direction=${direction}`;

  const kpis = d && [
    { label: t("dashboard.k_total"), value: d.now.total, sub: <Delta now={d.now.total} prev={d.previous.total} range={range} />,
      note: t("dashboard.k_split", { inbound: d.now.inbound, outbound: d.now.outbound }), to: `/trace?hours=${hours}${dirQ}` },
    { label: t("dashboard.k_delivered"), value: d.now.delivered, sub: t("dashboard.k_of_total", { pct: pct(d.now.delivered, d.now.total) }),
      note: null, to: `/trace?hours=${hours}&status=delivered${dirQ}` },
    { label: t("dashboard.k_spam"), value: d.now.spam, sub: <Delta now={d.now.spam} prev={d.previous.spam} range={range} />,
      note: t("dashboard.k_of_total", { pct: pct(d.now.spam, d.now.total) }), to: `/quarantine` },
    { label: t("dashboard.k_problems"), value: d.now.delayed + d.now.failed, sub: t("dashboard.k_problems_sub"),
      note: null, to: `/trace?hours=${hours}&status=problem${dirQ}` },
  ];

  return (
    <>
      <PageHead
        title={t("dashboard.welcome", { name: me.name || me.email })}
        sub={admin ? t("dashboard.sub_admin") : t("dashboard.sub_customer", { domains: me.domains.join(", ") || "–" })}
      />
      <BlacklistAlert admin={admin} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <div className="seg" role="group" aria-label={t("trace.period")}>
          {(["24h", "30d"] as const).map((r) => (
            <button key={r} type="button" aria-pressed={range === r} onClick={() => setRange(r)}>{t(`dashboard.range_${r}`)}</button>
          ))}
        </div>
        <div className="seg" role="group" aria-label={t("trace.direction")}>
          {(["all", "inbound", "outbound"] as const).map((r) => (
            <button key={r} type="button" aria-pressed={direction === r} onClick={() => setDirection(r)}>
              {r === "all" ? t("trace.both_directions") : t(`trace.${r}`)}
            </button>
          ))}
        </div>
      </div>
      {!d ? (
        <Loading />
      ) : (
        <div className={dash.isPlaceholderData ? "refetching" : undefined} style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="grid kpis">
            {kpis!.map((k) => (
              <Link key={k.label} to={k.to} className="card kpi" style={{ color: "inherit", textDecoration: "none" }}>
                <span className="label">{k.label}</span>
                <span className="value">{k.value.toLocaleString()}</span>
                <span className="delta">{k.sub}</span>
                {k.note && <span className="delta">{k.note}</span>}
              </Link>
            ))}
          </div>
          <div className="grid two">
            <Card
              title={t(range === "24h" ? "dashboard.chart_24h" : "dashboard.chart_30d")}
              right={<button className="btn small" onClick={() => setTable(!table)} aria-pressed={table}>{table ? t("dashboard.show_chart") : t("dashboard.show_table")}</button>}
            >
              <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
                <Legend series={series} />
                {table ? (
                  <div className="tbl-wrap" style={{ maxHeight: 300, overflowY: "auto" }}>
                    <table>
                      <thead>
                        <tr><th>{t("trace.time")}</th>{series.map((s) => <th key={s.key} style={{ textAlign: "right" }}>{s.label}</th>)}</tr>
                      </thead>
                      <tbody>
                        {d.series.map((b) => (
                          <tr key={b.t}>
                            <td className="nowrap">{fmtBucket(b.t, true)}</td>
                            {series.map((s) => <td key={s.key} className="num" style={{ textAlign: "right" }}>{b[s.key as keyof Bucket]}</td>)}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                ) : (
                  <StackedColumns
                    height={280}
                    data={d.series}
                    series={series}
                    label={t(range === "24h" ? "dashboard.chart_24h" : "dashboard.chart_30d")}
                    xLabel={(b) => fmtBucket(b.t, true)}
                    xTick={(b, i) => (i % (range === "24h" ? 4 : 5) === 0 ? fmtBucket(b.t, false) : null)}
                  />
                )}
                <p className="muted" style={{ margin: 0, fontSize: 12 }}>{t("dashboard.chart_note")}</p>
              </div>
            </Card>
            <Card title={t("dashboard.alerts")} flush>
              <div style={{ padding: "4px 18px" }}>
                {d.alerts.length === 0 ? (
                  <div className="alert-row">
                    <CircleCheck style={{ color: "var(--ok)" }} aria-hidden />
                    <div className="grow"><b>{t("dashboard.all_good")}</b><span>{t("dashboard.all_good_sub")}</span></div>
                  </div>
                ) : (
                  d.alerts.map((a, i) => <AlertRow key={i} a={a} />)
                )}
              </div>
              {d.queue && (
                <div className="filters" style={{ borderBottom: 0, borderTop: "1px solid var(--line)", gap: 16 }}>
                  <span className="muted">{t("dashboard.queue")}</span>
                  {["active", "deferred", "hold"].map((q) => (
                    <span key={q}><b className="num">{d.queue![q] ?? 0}</b> <span className="muted">{t(`dashboard.q_${q}`)}</span></span>
                  ))}
                </div>
              )}
            </Card>
          </div>
          <div className="grid halves">
            <Card title={t("dashboard.top_senders")}>
              <BarList items={d.top_senders} color="var(--chart-1)" empty={t("dashboard.no_data")}
                render={(n) => <Link to={`/trace?q=${encodeURIComponent(n)}&hours=${hours}`} style={{ color: "inherit" }}>{n}</Link>} />
            </Card>
            <Card title={t("dashboard.top_domains")}>
              <BarList items={d.top_domains} color="var(--chart-1)" empty={t("dashboard.no_data")}
                render={(n) => <Link to={`/trace?q=${encodeURIComponent("@" + n)}&hours=${hours}`} style={{ color: "inherit" }}>{n}</Link>} />
            </Card>
          </div>
          <Card title={t("dashboard.problems")} right={<Link className="btn small" to="/trace?status=problem">{t("dashboard.all")}</Link>} flush>
            {!problems.data?.items.length ? (
              <div className="empty">{t("dashboard.no_problems")}</div>
            ) : (
              <div className="tbl-wrap">
                <table>
                  <tbody>
                    {problems.data.items.map((m) => (
                      <tr key={m.queue_id}>
                        <td>
                          <Link to={`/trace?id=${m.queue_id}`} style={{ color: "inherit", textDecoration: "none" }}>
                            <div className="cell-main" style={{ wordBreak: "break-all" }}>{m.recipients.map((r) => r.address).join(", ")}</div>
                            <div className="cell-sub">{formatDate(m.first_seen, i18n.language)} · {m.sender || t("trace.empty_sender")}</div>
                            <Reason explanation={m.explanation} />
                          </Link>
                        </td>
                        <td className="nowrap" style={{ textAlign: "right" }}><StatusPill status={m.status} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}
    </>
  );
}

function AlertRow({ a }: { a: Alert }) {
  const { t } = useTranslation();
  const Icon = a.level === "critical" ? OctagonAlert : a.level === "warning" ? AlertTriangle : Info;
  const color = a.level === "critical" ? "var(--bad)" : a.level === "warning" ? "var(--warn)" : "var(--info)";
  const body = (
    <>
      <Icon style={{ color }} aria-label={t(`dashboard.level_${a.level}`)} />
      <div className="grow">
        <b>{t(`dashboard.a_${a.code}`, a.params)}</b>
        <span>{t(`dashboard.a_${a.code}_sub`, a.params)}</span>
      </div>
    </>
  );
  return a.link ? <Link className="alert-row" to={a.link}>{body}</Link> : <div className="alert-row">{body}</div>;
}
