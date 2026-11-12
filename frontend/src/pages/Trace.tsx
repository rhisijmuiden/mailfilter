import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Copy, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { SpamReport } from "../components/SpamReport";
import { Direction, Reason, RoutePicture, StatusPill } from "../components/TraceBits";
import { DataTable, type Column } from "../components/DataTable";
import { Card, Drawer, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import type { Detail, Message } from "../lib/trace";
import { formatSize } from "../lib/trace";

const STATUSES = ["all", "delivered", "problem", "deferred", "bounced", "quarantined", "rejected", "trapped"] as const;
const PERIODS = [1, 24, 24 * 7, 24 * 30, 24 * 90];
const PAGE = 50;

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

export default function Trace() {
  const { t, i18n } = useTranslation();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState(params.get("q") ?? "");
  const status = params.get("status") ?? "all";
  const direction = params.get("direction") ?? "all";
  const hours = Number(params.get("hours") ?? 24 * 7);
  const open = params.get("id");
  const [limit, setLimit] = useState(PAGE);
  const dq = useDebounced(q);

  const set = (k: string, v: string | null) => {
    const p = new URLSearchParams(params);
    if (v === null || v === "" || v === "all") p.delete(k);
    else p.set(k, v);
    setParams(p, { replace: k !== "id" });
  };
  useEffect(() => {
    if ((params.get("q") ?? "") !== dq) set("q", dq);
    setLimit(PAGE);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dq]);

  const list = useQuery({
    queryKey: ["trace", dq, status, direction, hours, limit],
    queryFn: () => api.get<{ items: Message[]; total: number; has_more: boolean }>(`/trace${qs({ q: dq, status, direction, hours, limit })}`),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });

  const columns: Column<Message>[] = [
    { key: "time", label: t("trace.time"), sort: (m) => m.first_seen, nowrap: true, mobile: "aside",
      render: (m) => (
        <>
          <div className="num">{formatDate(m.first_seen, i18n.language)}</div>
          <Direction direction={m.direction} />
        </>
      ) },
    { key: "from", label: t("quarantine.col_from"), sort: (m) => m.sender ?? "", mobile: "title",
      render: (m) => <div className="cell-main" style={{ wordBreak: "break-all" }}>{m.sender || t("trace.empty_sender")}</div> },
    { key: "to", label: t("quarantine.col_to"), sort: (m) => m.recipients[0]?.address ?? "",
      render: (m) => <div className="cell-sub" style={{ wordBreak: "break-all" }}>→ {m.recipients.map((r) => r.address).join(", ")}</div> },
    { key: "status", label: t("trace.status"), sort: (m) => m.status,
      render: (m) => (
        <>
          <StatusPill status={m.status} />
          <Reason explanation={m.explanation} />
        </>
      ) },
    { key: "client", label: t("quarantine.col_client"), optional: true, sort: (m) => m.client_ip ?? "",
      render: (m) => <span className="mono" style={{ wordBreak: "break-all" }}>{m.client_host || m.client_ip || "–"}</span> },
    { key: "score", label: t("trace.spam_score"), optional: true, sort: (m) => m.spam_score ?? -999, align: "right", nowrap: true, mobileLabel: true,
      render: (m) => <span className="num">{m.spam_score != null ? m.spam_score.toFixed(1) : "–"}</span> },
    { key: "size", label: t("trace.size"), optional: true, sort: (m) => m.size ?? 0, align: "right", nowrap: true, mobileLabel: true,
      render: (m) => <span className="num">{m.size != null ? formatSize(m.size) : "–"}</span> },
    { key: "qid", label: t("trace.queue_id"), optional: true, sort: (m) => m.queue_id, mobileLabel: true,
      render: (m) => <span className="mono">{m.queue_id}</span> },
  ];

  return (
    <>
      <PageHead
        title={t("trace.title")}
        sub={t("trace.sub")}
        actions={
          <button className="btn" onClick={() => list.refetch()}>
            <RefreshCw /> {t("common.refresh")}
          </button>
        }
      />
      <Card flush>
        <DataTable
          id="trace"
          rows={list.data?.items}
          rowKey={(m) => m.queue_id}
          loading={list.isLoading}
          columns={columns}
          search={{ value: q, onChange: setQ, placeholder: t("trace.search"), autoFocus: true }}
          tools={
            <>
              <select className="select inline" value={status} onChange={(e) => set("status", e.target.value)} aria-label={t("trace.status")}>
                {STATUSES.map((s) => (
                  <option key={s} value={s}>{t(`trace.filter_${s}`)}</option>
                ))}
              </select>
              <select className="select inline" value={direction} onChange={(e) => set("direction", e.target.value)} aria-label={t("trace.direction")}>
                <option value="all">{t("trace.both_directions")}</option>
                <option value="inbound">{t("trace.inbound")}</option>
                <option value="outbound">{t("trace.outbound")}</option>
              </select>
              <select className="select inline" value={hours} onChange={(e) => set("hours", e.target.value)} aria-label={t("trace.period")}>
                {PERIODS.map((h) => (
                  <option key={h} value={h}>{t(`trace.period_${h}`)}</option>
                ))}
              </select>
            </>
          }
          empty={dq || status !== "all" || direction !== "all" ? t("trace.no_results") : t("trace.empty")}
          onRowClick={(m) => set("id", m.queue_id)}
          footer={
            list.data && (
              <>
                <span className="muted">{t("trace.count", { shown: list.data.items.length, total: list.data.total })}</span>
                {list.data.has_more && (
                  <button className="btn small" onClick={() => setLimit(limit + PAGE)}>{t("trace.more")}</button>
                )}
              </>
            )
          }
        />
      </Card>
      {open && <TraceDrawer id={open} onClose={() => set("id", null)} />}
    </>
  );
}

function TraceDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const d = useQuery({ queryKey: ["trace-detail", id], queryFn: () => api.get<Detail>(`/trace/${encodeURIComponent(id)}`) });
  const m = d.data;
  const copy = (v: string) => navigator.clipboard?.writeText(v).then(() => toast(t("common.copied")));

  return (
    <Drawer
      title={m ? m.sender || t("trace.empty_sender") : t("trace.detail")}
      sub={m && <>→ {m.recipients.map((r) => r.address).join(", ")}</>}
      onClose={onClose}
    >
      {d.isLoading || !m ? (
        <Loading />
      ) : (
        <>
          <RoutePicture route={m.route} message={m} />
          <Verdict m={m} />
          {m.held && (
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Link className="btn primary" to={`/quarantine?id=${encodeURIComponent(m.queue_id)}`}>{t("trace.open_quarantine")}</Link>
            </div>
          )}
          {m.quarantine_actions.map((a, i) => (
            <div key={i} className="callout">
              <span>
                {t(`trace.q_${a.action}`, { who: a.user_email, when: formatDate(a.at, i18n.language) })}
                {a.learned && <> {t(`trace.q_learned_${a.learned}`)}</>}
              </span>
            </div>
          ))}
          <SpamReport m={m} />
          <div>
            <h3 style={{ margin: "0 0 10px", fontSize: 14 }}>{t("trace.recipients")}</h3>
            {m.recipients.map((r) => (
              <div key={r.address} className="list-row" style={{ alignItems: "flex-start" }}>
                <div className="grow">
                  <div className="cell-main" style={{ wordBreak: "break-all" }}>{r.address}</div>
                  {r.explanation && r.status !== "sent" && <Reason explanation={r.explanation} />}
                  {r.explanation && r.status !== "sent" && (
                    <span className="cell-sub">{t(`trace.who_${r.explanation.who}`, { defaultValue: "" })}</span>
                  )}
                  {r.reply && (
                    <div className="mono muted" style={{ marginTop: 4, wordBreak: "break-word" }}>
                      {r.relay_host && <>{r.relay_host}: </>}
                      {r.reply}
                    </div>
                  )}
                </div>
                <StatusPill status={r.status} recipient />
              </div>
            ))}
          </div>
          <dl className="kv">
            <dt>{t("trace.received")}</dt>
            <dd>{formatDate(m.first_seen, i18n.language)}</dd>
            <dt>{t("trace.direction")}</dt>
            <dd><Direction direction={m.direction} /></dd>
            <dt>{t("trace.queue_id")}</dt>
            <dd className="mono">
              {m.queue_id}{" "}
              <button className="btn ghost small" onClick={() => copy(m.queue_id)} aria-label={t("common.copy")}><Copy /></button>
            </dd>
            {m.message_id && (<><dt>Message-ID</dt><dd className="mono" style={{ wordBreak: "break-all" }}>{m.message_id}</dd></>)}
            {m.spam_score != null && (<><dt>{t("trace.spam_score")}</dt><dd className="num">{m.spam_score.toFixed(1)}{m.spam_action ? ` · ${m.spam_action}` : ""}</dd></>)}
            {m.size != null && (<><dt>{t("trace.size")}</dt><dd className="num">{formatSize(m.size)}</dd></>)}
            {m.sasl_user && (<><dt>{t("trace.sasl_user")}</dt><dd className="mono">{m.sasl_user}</dd></>)}
            {m.parent && (<><dt>{t("trace.bounce_of")}</dt><dd><Link className="mono" to={`/trace?id=${m.parent}`}>{m.parent}</Link></dd></>)}
            {m.bounces.length > 0 && (<><dt>{t("trace.bounces")}</dt><dd>{m.bounces.map((b) => <Link key={b} className="mono" to={`/trace?id=${b}`} style={{ marginRight: 8 }}>{b}</Link>)}</dd></>)}
          </dl>
        </>
      )}
    </Drawer>
  );
}

function Verdict({ m }: { m: Detail }) {
  const { t } = useTranslation();
  const tone = { delivered: "s-ok", partly_delivered: "s-warn", deferred: "s-warn", bounced: "s-bad", quarantined: "s-info", trapped: "s-info", rejected: "s-mute", queued: "s-mute" }[m.status] ?? "s-mute";
  const text = m.explanation ? `${m.explanation.title} ${m.explanation.action}` : t(`trace.verdict_${m.status}`, { defaultValue: "" });
  return (
    <div className={`verdict ${tone}`}>
      <div>
        <b>{t(`status.${m.status}`, { defaultValue: m.status })}</b>
        <span style={{ color: "var(--ink)" }}>{text}</span>
      </div>
    </div>
  );
}
