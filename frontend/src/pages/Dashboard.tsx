import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Reason, StatusPill } from "../components/TraceBits";
import { Card, PageHead, formatDate } from "../components/ui";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";
import type { Message, Stats } from "../lib/trace";

const PROBLEMS = ["bounced", "partly_delivered", "rejected", "deferred"];

export default function Dashboard({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const admin = me.role === "admin";
  const stats = useQuery({ queryKey: ["trace-stats"], queryFn: () => api.get<Stats>("/trace/stats?hours=24"), refetchInterval: 60_000 });
  const problems = useQuery({
    queryKey: ["trace", "dashboard-problems"],
    queryFn: () => api.get<{ items: Message[] }>("/trace?status=problem&hours=24&limit=6"),
    refetchInterval: 60_000,
  });
  const s = stats.data;
  const by = s?.by_status ?? {};
  const problemCount = PROBLEMS.reduce((n, k) => n + (by[k] ?? 0), 0);
  const kpis = [
    { label: t("dashboard.k_total"), value: s?.total, sub: t("dashboard.k_split", { inbound: s?.inbound ?? 0, outbound: s?.outbound ?? 0 }), to: "/trace?hours=24" },
    { label: t("dashboard.k_delivered"), value: by.delivered ?? 0, sub: s?.total ? `${Math.round(((by.delivered ?? 0) / s.total) * 100)}%` : "", to: "/trace?hours=24&status=delivered" },
    { label: t("dashboard.k_problems"), value: problemCount, sub: t("dashboard.k_problems_sub"), to: "/trace?hours=24&status=problem" },
    { label: t("dashboard.k_quarantine"), value: s?.quarantined_now, sub: t("dashboard.k_quarantine_sub"), to: "/quarantine" },
  ];
  return (
    <>
      <PageHead
        title={t("dashboard.welcome", { name: me.name || me.email })}
        sub={admin ? t("dashboard.sub_admin") : t("dashboard.sub_customer", { domains: me.domains.join(", ") || "–" })}
      />
      <div className="grid kpis">
        {kpis.map((k) => (
          <Link key={k.label} to={k.to} className="card kpi" style={{ color: "inherit", textDecoration: "none" }}>
            <span className="label">{k.label}</span>
            <span className="value">{k.value ?? "–"}</span>
            <span className="delta">{k.sub}</span>
          </Link>
        ))}
      </div>
      <div className="grid two">
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
        <Card title={t("dashboard.next")}>
          {[
            ...(admin ? [{ to: "/users", label: t("dashboard.next_users") }] : []),
            { to: "/trace", label: t("dashboard.next_trace") },
            { to: "/quarantine", label: t("dashboard.next_quarantine") },
          ].map((s) => (
            <Link key={s.to} to={s.to} className="list-row" style={{ color: "inherit", textDecoration: "none" }}>
              <span className="grow" style={{ fontWeight: 600 }}>{s.label}</span>
              <ArrowRight size={16} aria-hidden />
            </Link>
          ))}
        </Card>
      </div>
    </>
  );
}
