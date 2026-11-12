// Spam reports from end users: people report spam (or good mail that was
// marked as spam) with Outlook's Report button or by forwarding it to
// spam@ / notspam@ on this server. Rspamd learns from it; this page shows every
// report, what happened with it, and how to set it up per mail app.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Copy, Flag, GraduationCap, Hourglass, Search, ShieldOff, ThumbsUp, Undo2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { DataTable, type Column } from "../components/DataTable";
import { Card, ErrorText, Field, PageHead, SkeletonTiles, formatDate, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import type { Me } from "../lib/auth";

type Kind = "spam" | "ham";
type Report = {
  id: number; at: string; kind: Kind; status: string; detail: string; source: string; reporter: string; verified: string;
  queue_id: string | null; sender: string; recipient: string; subject: string; score: number | null; action: string;
  learned: Kind | null; blocked: boolean; handled_by: string; can_learn: boolean; can_undo: boolean; can_block: boolean;
};
type Stats = { spam: number; ham: number; learned: number; waiting: number; refused: number };
type Options = { enabled: boolean; require_auth: boolean; auto_block: boolean; per_hour: number };
type Settings = { options: Options; spam_address: string; ham_address: string; keep_days: number };

const STATUS_CLASS: Record<string, string> = {
  learned: "s-ok", undone: "s-mute", duplicate: "s-mute", unverified: "s-warn", not_found: "s-warn", not_learned: "s-warn",
  not_customer: "s-bad", rate_limited: "s-bad", no_message: "s-bad", disabled: "s-mute",
};
const DAYS = [7, 30, 90] as const;
const CLIENTS = ["m365", "outlook", "phone", "other"] as const;

function CopyBtn({ text }: { text: string }) {
  const { t } = useTranslation();
  const toast = useToast();
  return (
    <button type="button" className="btn small ghost" onClick={() => navigator.clipboard?.writeText(text).then(() => toast(t("common.copied")))}>
      <Copy /> {t("common.copy")}
    </button>
  );
}

function KindBadge({ kind }: { kind: Kind }) {
  const { t } = useTranslation();
  return kind === "spam" ? (
    <span className="status s-bad"><Flag aria-hidden />{t("sfb.kind_spam")}</span>
  ) : (
    <span className="status s-ok"><ThumbsUp aria-hidden />{t("sfb.kind_ham")}</span>
  );
}

function HowTo({ s }: { s: Settings }) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<(typeof CLIENTS)[number]>("m365");
  const steps = t(`sfb.how_${tab}_steps`, { returnObjects: true, spam: s.spam_address, ham: s.ham_address }) as string[];
  return (
    <Card title={t("sfb.how_title")}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="grid two" style={{ gap: 12 }}>
          {([["spam", s.spam_address], ["ham", s.ham_address]] as const).map(([k, addr]) => (
            <div key={k} className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 6 }}>
              <span className="muted" style={{ fontSize: 12.5 }}>{t(`sfb.address_${k}`)}</span>
              <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                <b className="mono" style={{ wordBreak: "break-all" }}>{addr}</b>
                <CopyBtn text={addr} />
              </div>
            </div>
          ))}
        </div>
        <div className="seg" role="tablist" aria-label={t("sfb.how_title")} style={{ alignSelf: "flex-start", flexWrap: "wrap" }}>
          {CLIENTS.map((c) => (
            <button key={c} type="button" role="tab" aria-selected={tab === c} aria-pressed={tab === c} onClick={() => setTab(c)}>
              {t(`sfb.how_${c}`)}
            </button>
          ))}
        </div>
        <p className="muted" style={{ margin: 0 }}>{t(`sfb.how_${tab}_intro`)}</p>
        <ol style={{ margin: 0, paddingLeft: 22, listStyle: "decimal", display: "flex", flexDirection: "column", gap: 6 }}>
          {Array.isArray(steps) && steps.map((x, i) => <li key={i}>{x}</li>)}
        </ol>
        <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>{t("sfb.how_safety")}</p>
      </div>
    </Card>
  );
}

function SettingsCard({ s }: { s: Settings }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [o, setO] = useState<Options>(s.options);
  useEffect(() => setO(s.options), [s.options]);
  const save = useMutation({
    mutationFn: () => api.put<Settings>("/spam-reports/settings", o),
    onSuccess: (d) => qc.setQueryData(["spam-reports-settings"], d),
  });
  const check = (key: "enabled" | "require_auth" | "auto_block") => (
    <label className="check full">
      <input type="checkbox" checked={o[key]} onChange={(e) => setO({ ...o, [key]: e.target.checked })} />
      <span>
        <b style={{ display: "block" }}>{t(`sfb.opt_${key}`)}</b>
        <span className="muted" style={{ fontSize: 12.5 }}>{t(`sfb.opt_${key}_hint`)}</span>
      </span>
    </label>
  );
  return (
    <Card title={t("sfb.settings")}>
      <form className="form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        {check("enabled")}
        {check("require_auth")}
        {check("auto_block")}
        <Field label={t("sfb.opt_per_hour")} hint={t("sfb.opt_per_hour_hint")}>
          <input className="input num" type="number" min={0} max={10000} value={o.per_hour}
            onChange={(e) => setO({ ...o, per_hour: Math.max(0, +e.target.value || 0) })} />
        </Field>
        <div className="full" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
          <button className="btn primary" disabled={save.isPending}>{t("common.save")}</button>
          <span className="muted" style={{ fontSize: 12.5 }}>{t("sfb.keep_hint", { days: s.keep_days })}</span>
        </div>
        <div className="full"><ErrorText error={save.error} /></div>
      </form>
    </Card>
  );
}

export default function SpamReports({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const admin = me.role === "admin";
  const [days, setDays] = useState<(typeof DAYS)[number]>(30);
  const [kind, setKind] = useState<"" | Kind>("");
  const [status, setStatus] = useState("");
  const [q, setQ] = useState("");
  const list = useQuery({
    queryKey: ["spam-reports", days, kind, status, q],
    queryFn: () => api.get<{ items: Report[]; total: number; stats: Stats }>(`/spam-reports${qs({ days, kind, status, q })}`),
    placeholderData: keepPreviousData,
  });
  const settings = useQuery({ queryKey: ["spam-reports-settings"], queryFn: () => api.get<Settings>("/spam-reports/settings") });
  const act = useMutation({
    mutationFn: ({ id, what }: { id: number; what: "learn" | "undo" | "block" }) => api.post<Report>(`/spam-reports/${id}/${what}`),
    onSuccess: (r, v) => {
      qc.invalidateQueries({ queryKey: ["spam-reports"] });
      toast(t(`sfb.done_${v.what}`, { sender: r.sender }), "success");
    },
  });
  const st = list.data?.stats;

  const columns: Column<Report>[] = [
    { key: "at", label: t("sfb.when"), sort: (r) => r.at, nowrap: true, mobileLabel: true,
      render: (r) => <span className="num">{formatDate(r.at, i18n.language)}</span> },
    { key: "kind", label: t("sfb.kind"), sort: (r) => r.kind, mobile: "aside", render: (r) => <KindBadge kind={r.kind} /> },
    { key: "message", label: t("sfb.message"), always: true, mobile: "title", sort: (r) => r.subject,
      render: (r) => (
        <>
          <div className="cell-main" style={{ wordBreak: "break-word" }}>{r.subject || <span className="muted">{t("sfb.no_subject")}</span>}</div>
          <div className="cell-sub mono" style={{ wordBreak: "break-all" }}>{r.sender || "—"}</div>
        </>
      ) },
    { key: "reporter", label: t("sfb.reporter"), sort: (r) => r.reporter, mobileLabel: true,
      render: (r) => (
        <>
          <div className="cell-main mono" style={{ wordBreak: "break-all" }}>{r.reporter || "—"}</div>
          <div className="cell-sub">
            {t(`sfb.source_${r.source}`)}
            {r.verified ? ` · ${t("sfb.verified", { how: r.verified.toUpperCase() })}` : null}
          </div>
        </>
      ) },
    { key: "score", label: t("sfb.score"), sort: (r) => r.score ?? -999, align: "right", optional: true, mobileLabel: true,
      render: (r) => (r.score === null ? <span className="muted">—</span> : <span className="num">{r.score.toFixed(1)}</span>) },
    { key: "status", label: t("sfb.status"), sort: (r) => r.status, mobileLabel: true,
      render: (r) => (
        <>
          <span className={`status ${STATUS_CLASS[r.status] ?? "s-mute"}`} title={t(`sfb.st_${r.status}_hint`, { defaultValue: "" })}>
            {t(`sfb.st_${r.status}`, { defaultValue: r.status })}
          </span>
          <div className="cell-sub">
            {r.blocked && <span className="tag" style={{ marginRight: 6 }}><Ban aria-hidden style={{ width: 11, height: 11 }} /> {t("sfb.blocked")}</span>}
            {r.handled_by && t("sfb.handled_by", { who: r.handled_by })}
          </div>
        </>
      ) },
    { key: "actions", label: "", always: true, align: "right",
      render: (r) => actions(r), mobile: "hide" },
  ];

  function actions(r: Report) {
    return (
      <div style={{ display: "flex", gap: 4, justifyContent: "flex-end", flexWrap: "nowrap" }}>
        {r.can_learn && (
          <button className="btn small" disabled={act.isPending} onClick={() => act.mutate({ id: r.id, what: "learn" })} title={t("sfb.learn_hint")}>
            <GraduationCap /> {t("sfb.learn")}
          </button>
        )}
        {r.can_undo && (
          <button className="btn small ghost" disabled={act.isPending}
            onClick={() => window.confirm(t("sfb.undo_confirm")) && act.mutate({ id: r.id, what: "undo" })}>
            <Undo2 /> {t("sfb.undo")}
          </button>
        )}
        {r.can_block && (
          <button className="btn small ghost" disabled={act.isPending} onClick={() => act.mutate({ id: r.id, what: "block" })}
            title={t("sfb.block_hint", { sender: r.sender, mailbox: r.recipient || r.reporter })}>
            <ShieldOff /> {t("sfb.block")}
          </button>
        )}
        {r.queue_id && (
          <Link className="icon-btn" to={`/trace?q=${encodeURIComponent(r.queue_id)}&hours=720`} title={t("sfb.open_trace")} aria-label={t("sfb.open_trace")}>
            <Search />
          </Link>
        )}
      </div>
    );
  }

  const tiles = st && [
    { label: t("sfb.t_spam"), value: st.spam, icon: <Flag />, filter: () => { setKind("spam"); setStatus(""); } },
    { label: t("sfb.t_ham"), value: st.ham, icon: <ThumbsUp />, filter: () => { setKind("ham"); setStatus(""); } },
    { label: t("sfb.t_learned"), value: st.learned, icon: <GraduationCap />, filter: () => { setKind(""); setStatus("learned"); } },
    { label: t("sfb.t_waiting"), value: st.waiting, icon: <Hourglass />, filter: () => { setKind(""); setStatus(st.waiting ? "unverified" : ""); },
      sub: t("sfb.t_waiting_sub") },
  ];

  return (
    <>
      <PageHead title={t("sfb.title")} sub={t("sfb.sub")} />
      <div className="seg" role="group" aria-label={t("trace.period")} style={{ alignSelf: "flex-start" }}>
        {DAYS.map((d) => (
          <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)}>{t("sfb.days", { count: d })}</button>
        ))}
      </div>
      {!tiles ? <SkeletonTiles /> : (
        <div className="grid kpis">
          {tiles.map((k) => (
            <button key={k.label} type="button" className="card kpi" onClick={k.filter} style={{ textAlign: "left", cursor: "pointer", color: "inherit" }}>
              <span className="label" style={{ display: "flex", alignItems: "center", gap: 6 }}>{k.label}</span>
              <span className="value">{k.value.toLocaleString()}</span>
              {k.sub && <span className="delta">{k.sub}</span>}
            </button>
          ))}
        </div>
      )}
      {settings.data && !settings.data.options.enabled && (
        <div className="card" style={{ padding: 14, borderLeft: "3px solid var(--warn, #d97706)" }}>{t("sfb.off_notice")}</div>
      )}
      <ErrorText error={act.error} />
      <Card flush>
        <DataTable
          id="spam-reports"
          rows={list.data?.items}
          rowKey={(r) => r.id}
          loading={list.isLoading}
          columns={columns}
          search={{ value: q, onChange: setQ, placeholder: t("sfb.search") }}
          cardActions={(r) => actions(r)}
          tools={
            <>
              <div className="seg" role="group" aria-label={t("sfb.kind")}>
                {(["", "spam", "ham"] as const).map((k) => (
                  <button key={k || "all"} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>
                    {t(k ? `sfb.kind_${k}` : "sfb.all")}
                  </button>
                ))}
              </div>
              <select className="select" aria-label={t("sfb.status")} value={status} onChange={(e) => setStatus(e.target.value)} style={{ width: "auto" }}>
                <option value="">{t("sfb.all_statuses")}</option>
                {Object.keys(STATUS_CLASS).map((s) => <option key={s} value={s}>{t(`sfb.st_${s}`)}</option>)}
              </select>
              {list.data && <span className="muted">{t("sfb.count", { count: list.data.total })}</span>}
            </>
          }
          empty={q || kind || status ? t("trace.no_results") : t("sfb.empty")}
        />
      </Card>
      <div className="grid two">
        {settings.data && <HowTo s={settings.data} />}
        {admin && settings.data && <SettingsCard s={settings.data} />}
      </div>
    </>
  );
}
