// DMARC reports: who sends mail as your domains, whether it passes DMARC, and
// the next safe step towards p=reject. Reports arrive by mail at
// dmarc-reports@<hostname>; admins can also upload a report file.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeft, Check, ChevronDown, ChevronRight, Cloud, Copy, Info, ShieldCheck, Upload, X } from "lucide-react";
import { Fragment, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { BarList, Legend, StackedColumns, type Series } from "../components/charts";
import { Card, Drawer, ErrorText, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { ApiError, api, qs } from "../lib/api";
import type { Me } from "../lib/auth";
import { PublishModal, type PublishKind } from "./Dns";

// --- types ---------------------------------------------------------------------

type DomainRow = {
  domain: string; messages: number; passed: number; failed: number; enforced: number; reports: number; reporters: number;
  last_report: string | null; policy: string | null; pct: number | null;
};
type Overview = { days: number; rua: string; domains: DomainRow[]; totals: { messages: number; passed: number; failed: number; domains_with_reports: number } };
type Ip = { ip: string; host: string; messages: number; passed: number; dkim_pass: number; spf_pass: number; quarantined: number; rejected: number; dkim_domains: string[]; spf_domains: string[] };
type Sender = { name: string; kind: "own" | "known" | "host" | "unknown"; verdict: "ok" | "minor" | "fix" | "suspect"; messages: number; passed: number; failed: number; dkim_pass: number; spf_pass: number; forwarded: number; ips: Ip[]; more_ips: number };
type Advice = { code: string; level: "ok" | "info" | "warn" | "bad"; params: Record<string, string | number>; action: { kind: PublishKind; policy?: string } | null };
type ReportSummary = { id: number; domain: string; org_name: string; org_email: string; report_id: string; begin: string; end: string; p: string; pct: number | null; messages: number; source: string };
type Detail = {
  domain: string; days: number; rua: string; admin: boolean; cloudflare: boolean; record: string | null; record_error: string | null;
  tags: Record<string, string> | null; has_rua: boolean;
  totals: { messages: number; passed: number; failed: number; dkim_pass: number; spf_pass: number; enforced: number };
  advice: Advice; series: { t: string; passed: number; failed: number }[]; senders: Sender[];
  reporters: { name: string; reports: number; messages: number }[]; reports: ReportSummary[];
};
type ReportRow = { source_ip: string; source_host: string; sender: string; count: number; disposition: string; dkim: string; spf: string; header_from: string; envelope_from: string; dkim_auth: { domain: string; selector: string; result: string }[]; spf_auth: { domain: string; scope: string; result: string }[]; reasons: { type: string; comment: string }[] };
type ReportFull = ReportSummary & { rows: ReportRow[]; adkim: string; aspf: string; sp: string };
type Setup = {
  rua: string; hostname: string; cloudflare: boolean; checks: { address: boolean | null; auth: boolean | null };
  log: { at: string; source: string; sender: string; subject: string; status: string; detail: string; domains: string[]; reports: number }[];
};

const DAYS = [7, 30, 90] as const;
const pct = (a: number, b: number) => (b ? Math.round((a / b) * 100) : null);

// --- small parts -------------------------------------------------------------------

function Rate({ passed, total }: { passed: number; total: number }) {
  const p = pct(passed, total);
  if (p === null) return <span className="muted">–</span>;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 8, minWidth: 120 }}>
      <div className="bar" style={{ flex: 1 }}><i className={p >= 98 ? "" : p >= 80 ? "w" : "b"} style={{ width: `${Math.max(2, p)}%` }} /></div>
      <b className="num" style={{ fontSize: 12.5, width: 38, textAlign: "right" }}>{p}%</b>
    </div>
  );
}

function PolicyPill({ p, pct: pc }: { p: string | null | undefined; pct?: number | string | null }) {
  const { t } = useTranslation();
  if (!p) return <span className="status s-mute">{t("dmarc.policy_unknown")}</span>;
  const cls = p === "reject" ? "s-ok" : p === "quarantine" ? "s-info" : "s-warn";
  const partial = pc !== null && pc !== undefined && String(pc) !== "100";
  return <span className={`status ${cls}`}>{p === "reject" ? <ShieldCheck /> : <Info />}p={p}{partial ? ` (${pc}%)` : ""}</span>;
}

const VERDICT: Record<Sender["verdict"], string> = { ok: "s-ok", minor: "s-info", fix: "s-bad", suspect: "s-mute" };

function CopyBtn({ text }: { text: string }) {
  const { t } = useTranslation();
  const toast = useToast();
  return (
    <button type="button" className="btn small ghost" onClick={() => navigator.clipboard?.writeText(text).then(() => toast(t("common.copied")))}>
      <Copy /> {t("common.copy")}
    </button>
  );
}

function Kpi({ label, value, sub }: { label: string; value: ReactNode; sub?: ReactNode }) {
  return (
    <div className="card kpi">
      <span className="label">{label}</span>
      <span className="value">{value}</span>
      {sub && <span className="delta">{sub}</span>}
    </div>
  );
}

function DaysSeg({ days, setDays }: { days: number; setDays: (d: number) => void }) {
  const { t } = useTranslation();
  return (
    <div className="seg" role="group" aria-label={t("dmarc.period")}>
      {DAYS.map((d) => <button key={d} type="button" aria-pressed={days === d} onClick={() => setDays(d)}>{t("dmarc.days", { count: d })}</button>)}
    </div>
  );
}

// --- page ------------------------------------------------------------------------------

export default function Dmarc({ me }: { me: Me }) {
  const [params, setParams] = useSearchParams();
  const domain = params.get("domain");
  const days = DAYS.includes(Number(params.get("days")) as 7) ? Number(params.get("days")) : 30;
  const set = (k: string, v: string | null) => setParams((p) => { if (v === null) p.delete(k); else p.set(k, v); return p; });
  const setDays = (d: number) => set("days", d === 30 ? null : String(d));
  return domain
    ? <DomainView domain={domain} days={days} setDays={setDays} onBack={() => set("domain", null)} />
    : <OverviewView me={me} days={days} setDays={setDays} open={(d) => set("domain", d)} />;
}

function OverviewView({ me, days, setDays, open }: { me: Me; days: number; setDays: (d: number) => void; open: (d: string) => void }) {
  const { t, i18n } = useTranslation();
  const admin = me.role === "admin";
  const q = useQuery({ queryKey: ["dmarc", "overview", days], queryFn: () => api.get<Overview>(`/dmarc/overview${qs({ days })}`), placeholderData: keepPreviousData });
  const [publish, setPublish] = useState<string | null>(null);
  const d = q.data;
  return (
    <>
      <PageHead title={t("dmarc.title")} sub={t(admin ? "dmarc.sub_admin" : "dmarc.sub_customer")} actions={<DaysSeg days={days} setDays={setDays} />} />
      {admin && <SetupCard />}
      {!d ? <Loading /> : (
        <div className={q.isPlaceholderData ? "refetching" : undefined} style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="grid kpis">
            <Kpi label={t("dmarc.k_messages")} value={d.totals.messages.toLocaleString()} sub={t("dmarc.k_messages_sub", { count: d.totals.domains_with_reports })} />
            <Kpi label={t("dmarc.k_pass")} value={pct(d.totals.passed, d.totals.messages) === null ? "–" : `${pct(d.totals.passed, d.totals.messages)}%`} sub={t("dmarc.k_pass_sub")} />
            <Kpi label={t("dmarc.k_fail")} value={d.totals.failed.toLocaleString()} sub={t("dmarc.k_fail_sub")} />
            <Kpi label={t("dmarc.k_domains")} value={d.domains.length} sub={t("dmarc.k_domains_sub", { count: d.domains.filter((x) => !x.reports).length })} />
          </div>
          <Card title={t("dmarc.domains")} flush>
            {!d.domains.length ? <div className="empty">{t("dmarc.no_domains")}</div> : (
              <div className="tbl-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("dmarc.domain")}</th><th>{t("dmarc.policy")}</th><th style={{ textAlign: "right" }}>{t("dmarc.messages")}</th>
                      <th>{t("dmarc.pass_rate")}</th><th style={{ textAlign: "right" }}>{t("dmarc.failed")}</th><th>{t("dmarc.last_report")}</th><th />
                    </tr>
                  </thead>
                  <tbody>
                    {d.domains.map((x) => (
                      <tr key={x.domain} style={{ cursor: "pointer" }} onClick={() => open(x.domain)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && open(x.domain)}>
                        <td><div className="cell-main">{x.domain}</div>{x.reports > 0 && <div className="cell-sub">{t("dmarc.reports_from", { count: x.reports, reporters: x.reporters })}</div>}</td>
                        <td>{x.reports ? <PolicyPill p={x.policy} pct={x.pct} /> : <span className="muted">–</span>}</td>
                        <td className="num" style={{ textAlign: "right" }}>{x.messages.toLocaleString()}</td>
                        <td>{x.reports ? <Rate passed={x.passed} total={x.messages} /> : (
                          <span className="muted" style={{ fontSize: 12.5 }}>{t("dmarc.no_reports_yet")}</span>
                        )}</td>
                        <td className="num" style={{ textAlign: "right", color: x.failed ? "var(--bad)" : undefined }}>{x.reports ? x.failed.toLocaleString() : ""}</td>
                        <td className="nowrap">
                          {x.last_report ? formatDate(x.last_report, i18n.language, false) : admin ? (
                            <button className="btn small" type="button" onClick={(e) => { e.stopPropagation(); setPublish(x.domain); }}><Cloud /> {t("dmarc.setup_reports")}</button>
                          ) : <span className="muted">–</span>}
                        </td>
                        <td style={{ width: 24 }}><ChevronRight style={{ width: 16, height: 16, color: "var(--ink-muted)" }} aria-hidden /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </Card>
        </div>
      )}
      {publish && <PublishModal kind="dmarc_rua" domain={publish} onClose={() => setPublish(null)} onDone={() => q.refetch()} />}
    </>
  );
}

// --- setup (admin) -------------------------------------------------------------------------

async function uploadReport(file: File) {
  const res = await fetch(`/api/dmarc/upload${qs({ name: file.name })}`, {
    method: "POST", credentials: "same-origin",
    headers: { "X-Requested-With": "mailfilter", "Content-Type": "application/octet-stream" }, body: file,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data?.detail === "string" ? data.detail : "error");
  return data as { status: string; detail: string; domains: string[]; reports: number };
}

function Check3({ v }: { v: boolean | null }) {
  const { t } = useTranslation();
  if (v === null) return <span className="status s-mute"><Info />{t("dmarc.check_unknown")}</span>;
  return <span className={`status ${v ? "s-ok" : "s-warn"}`}>{v ? <Check /> : <X />}{t(v ? "dmarc.check_ok" : "dmarc.check_missing")}</span>;
}

function SetupCard() {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["dmarc", "setup"], queryFn: () => api.get<Setup>("/dmarc/setup") });
  const [auth, setAuth] = useState(false);
  const [open, setOpen] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const up = useMutation({
    mutationFn: uploadReport,
    onSuccess: (r) => {
      toast(t(`dmarc.upload_${r.status}`, { count: r.reports, defaultValue: r.status }));
      qc.invalidateQueries({ queryKey: ["dmarc"] });
    },
  });
  const s = q.data;
  if (!s) return null;
  const ready = s.checks.address !== false && s.checks.auth !== false;
  return (
    <Card
      title={t("dmarc.setup_title")}
      right={
        <div style={{ display: "flex", gap: 8 }}>
          <input ref={input} type="file" accept=".xml,.gz,.zip,.eml" hidden onChange={(e) => { const f = e.target.files?.[0]; if (f) up.mutate(f); e.target.value = ""; }} />
          <button className="btn small" type="button" disabled={up.isPending} onClick={() => input.current?.click()}><Upload /> {t("dmarc.upload")}</button>
          <button className="btn small ghost" type="button" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? <ChevronDown /> : <ChevronRight />} {t("dmarc.received")}</button>
        </div>
      }
    >
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div className="dns">
          <span className="muted">{t("dmarc.rua_address")}</span>
          <code>mailto:{s.rua}</code>
          <CopyBtn text={`mailto:${s.rua}`} />
        </div>
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>{t("dmarc.rua_help")}</p>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "center", fontSize: 13 }}>
          <span>{t("dmarc.check_address", { host: s.hostname })} <Check3 v={s.checks.address} /></span>
          <span>{t("dmarc.check_auth")} <Check3 v={s.checks.auth} /></span>
          {s.checks.auth === false && (s.cloudflare
            ? <button className="btn small primary" type="button" onClick={() => setAuth(true)}><Cloud /> {t("dmarc.publish_auth")}</button>
            : <span className="muted">{t("dmarc.auth_manual", { name: `*._report._dmarc.${s.hostname}` })}</span>)}
        </div>
        {!ready && <div className="callout warn"><Info /><span>{t("dmarc.setup_incomplete")}</span></div>}
        <ErrorText error={up.error} />
        {open && (!s.log.length ? <div className="empty">{t("dmarc.nothing_received")}</div> : (
          <div className="tbl-wrap">
            <table>
              <thead><tr><th>{t("dmarc.received_at")}</th><th>{t("dmarc.from")}</th><th>{t("dmarc.domain")}</th><th>{t("dmarc.result")}</th></tr></thead>
              <tbody>
                {s.log.map((x, i) => (
                  <tr key={i}>
                    <td className="nowrap">{formatDate(x.at, i18n.language)}</td>
                    <td><div className="cell-main" style={{ wordBreak: "break-all" }}>{x.sender || "–"}</div><div className="cell-sub">{x.subject}</div></td>
                    <td>{x.domains.join(", ") || "–"}</td>
                    <td>
                      <span className={`status ${x.status === "ok" ? "s-ok" : x.status === "error" ? "s-bad" : "s-mute"}`}>{t(`dmarc.log.${x.status}`, { defaultValue: x.status })}</span>
                      {x.detail && <div className="cell-sub mono" style={{ wordBreak: "break-all" }}>{x.detail}</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ))}
      </div>
      {auth && <PublishModal kind="dmarc_auth" domain={s.hostname} onClose={() => setAuth(false)} onDone={() => q.refetch()} />}
    </Card>
  );
}

// --- one domain -----------------------------------------------------------------------------

const ROAD = ["none", "quarantine", "reject"] as const;

function Road({ p }: { p: string }) {
  const { t } = useTranslation();
  const at = Math.max(0, ROAD.indexOf(p as (typeof ROAD)[number]));
  return (
    <ol style={{ display: "grid", gridTemplateColumns: "repeat(3, minmax(0,1fr))", gap: 8, listStyle: "none", margin: 0, padding: 0 }} aria-label={t("dmarc.road")}>
      {ROAD.map((step, i) => (
        <li key={step} aria-current={i === at ? "step" : undefined}
          style={{ border: `${i === at ? 2 : 1}px solid ${i === at ? "var(--accent)" : "var(--line)"}`, borderRadius: "var(--r-md)", padding: "10px 12px", background: i < at ? "var(--surface-sunk)" : "var(--surface)" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 700, fontSize: 13 }}>
            {i < at ? <Check style={{ width: 15, height: 15, color: "var(--ok)" }} aria-hidden /> : <span className="num muted">{i + 1}</span>}
            p={step}
          </div>
          <div className="muted" style={{ fontSize: 12 }}>{t(`dmarc.road_${step}`)}</div>
        </li>
      ))}
    </ol>
  );
}

function AdviceCard({ d, onDone }: { d: Detail; onDone: () => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const a = d.advice;
  const params = { ...a.params, rua: `mailto:${d.rua}`, domain: d.domain };
  const color = a.level === "ok" ? "var(--ok)" : a.level === "bad" ? "var(--bad)" : a.level === "warn" ? "var(--warn)" : "var(--info)";
  const needsDns = ["no_record", "no_rua", "ready_quarantine", "ready_reject", "raise_pct"].includes(a.code);
  return (
    <Card title={t("dmarc.next_step")}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <Road p={(d.tags?.p ?? "none").toLowerCase()} />
        <div className="verdict" style={{ background: "var(--surface-sunk)", color: "var(--ink)" }}>
          {a.level === "ok" ? <ShieldCheck style={{ color }} aria-hidden /> : <Info style={{ color }} aria-hidden />}
          <div style={{ minWidth: 0 }}>
            <b>{t(`dmarc.advice.${a.code}`, params)}</b>
            <span style={{ fontSize: 13.5 }}>{t(`dmarc.advice.${a.code}_sub`, params)}</span>
          </div>
        </div>
        {a.action ? (
          <div><button className="btn primary" type="button" onClick={() => setOpen(true)}><Cloud /> {t(`dmarc.action.${a.action.kind}`, { policy: a.action.policy })}</button></div>
        ) : needsDns && (
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>{t(d.admin ? "dmarc.manual_admin" : "dmarc.manual_customer")}</p>
        )}
        <div>
          <div className="muted" style={{ fontSize: 12, marginBottom: 4 }}>{t("dmarc.current_record", { name: `_dmarc.${d.domain}` })}</div>
          {d.record ? <code className="mono" style={{ display: "block", fontSize: 12, background: "var(--surface-sunk)", padding: "8px 10px", borderRadius: "var(--r-sm)", wordBreak: "break-all" }}>{d.record}</code>
            : <span className="muted" style={{ fontSize: 13 }}>{t(d.record_error === "dns_error" ? "dmarc.record_dns_error" : "dmarc.record_none")}</span>}
        </div>
      </div>
      {open && a.action && <PublishModal kind={a.action.kind} domain={d.domain} value={a.action.policy} onClose={() => setOpen(false)} onDone={onDone} />}
    </Card>
  );
}

function SenderRows({ s, total }: { s: Sender; total: number }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <Fragment>
      <tr style={{ cursor: "pointer" }} onClick={() => setOpen(!open)} aria-expanded={open}>
        <td>
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            {open ? <ChevronDown style={{ width: 15, height: 15 }} aria-hidden /> : <ChevronRight style={{ width: 15, height: 15 }} aria-hidden />}
            <div>
              <div className="cell-main" style={{ wordBreak: "break-all" }}>{s.name}</div>
              <div className="cell-sub">{t(`dmarc.kind.${s.kind}`)} · {t("dmarc.ip_count", { count: s.ips.length + s.more_ips })}</div>
            </div>
          </div>
        </td>
        <td><span className={`status ${VERDICT[s.verdict]}`}>{s.verdict === "ok" ? <Check /> : s.verdict === "fix" ? <X /> : <Info />}{t(`dmarc.verdict.${s.verdict}`)}</span></td>
        <td className="num" style={{ textAlign: "right" }}>{s.messages.toLocaleString()}<div className="cell-sub">{pct(s.messages, total)}%</div></td>
        <td><Rate passed={s.passed} total={s.messages} /></td>
        <td className="num" style={{ textAlign: "right" }}>{pct(s.dkim_pass, s.messages)}%</td>
        <td className="num" style={{ textAlign: "right" }}>{pct(s.spf_pass, s.messages)}%</td>
      </tr>
      {open && (
        <tr>
          <td colSpan={6} style={{ background: "var(--surface-sunk)" }}>
            <p className="muted" style={{ margin: "0 0 8px", fontSize: 12.5 }}>{t(`dmarc.verdict_help.${s.verdict}`)}</p>
            <div className="tbl-wrap">
              <table>
                <thead><tr><th>{t("dmarc.ip")}</th><th style={{ textAlign: "right" }}>{t("dmarc.messages")}</th><th>{t("dmarc.dkim_signed")}</th><th>{t("dmarc.spf_checked")}</th><th>{t("dmarc.result")}</th></tr></thead>
                <tbody>
                  {s.ips.map((ip) => (
                    <tr key={ip.ip}>
                      <td><div className="mono" style={{ fontSize: 12 }}>{ip.ip}</div><div className="cell-sub" style={{ wordBreak: "break-all" }}>{ip.host || t("dmarc.no_ptr")}</div></td>
                      <td className="num" style={{ textAlign: "right" }}>{ip.messages.toLocaleString()}</td>
                      <td className="mono" style={{ fontSize: 11.5, wordBreak: "break-all" }}>{ip.dkim_domains.join(", ") || "–"}</td>
                      <td className="mono" style={{ fontSize: 11.5, wordBreak: "break-all" }}>{ip.spf_domains.join(", ") || "–"}</td>
                      <td className="nowrap" style={{ fontSize: 12.5 }}>
                        <Rate passed={ip.passed} total={ip.messages} />
                        {(ip.quarantined > 0 || ip.rejected > 0) && <div className="cell-sub">{t("dmarc.enforced_counts", { q: ip.quarantined, r: ip.rejected })}</div>}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            {s.more_ips > 0 && <p className="muted" style={{ margin: "8px 0 0", fontSize: 12 }}>{t("dmarc.more_ips", { count: s.more_ips })}</p>}
          </td>
        </tr>
      )}
    </Fragment>
  );
}

function DomainView({ domain, days, setDays, onBack }: { domain: string; days: number; setDays: (d: number) => void; onBack: () => void }) {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: ["dmarc", "domain", domain, days], queryFn: () => api.get<Detail>(`/dmarc/domain/${encodeURIComponent(domain)}${qs({ days })}`), placeholderData: keepPreviousData });
  const [report, setReport] = useState<number | null>(null);
  const [table, setTable] = useState(false);
  const d = q.data;
  const locale = i18n.language === "nl" ? "nl-NL" : "en-GB";
  const series: Series[] = [
    { key: "passed", label: t("dmarc.c_passed"), color: "var(--chart-3)" },
    { key: "failed", label: t("dmarc.c_failed"), color: "var(--chart-2)" },
  ];
  const fmtDay = (iso: string, long: boolean) =>
    new Date(`${iso}T12:00:00Z`).toLocaleDateString(locale, long ? { weekday: "short", day: "numeric", month: "long" } : { day: "numeric", month: "short" });
  const p = (a: number, b: number) => (pct(a, b) === null ? "–" : `${pct(a, b)}%`);
  return (
    <>
      <div><button className="btn small ghost" type="button" onClick={onBack}><ArrowLeft /> {t("dmarc.all_domains")}</button></div>
      <PageHead title={domain} sub={t("dmarc.domain_sub")} actions={<DaysSeg days={days} setDays={setDays} />} />
      {q.error ? <ErrorText error={q.error} /> : !d ? <Loading /> : (
        <div className={q.isPlaceholderData ? "refetching" : undefined} style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <AdviceCard d={d} onDone={() => q.refetch()} />
          <div className="grid kpis">
            <Kpi label={t("dmarc.k_messages")} value={d.totals.messages.toLocaleString()} sub={t("dmarc.k_reporters", { count: d.reporters.length })} />
            <Kpi label={t("dmarc.k_pass")} value={p(d.totals.passed, d.totals.messages)} sub={t("dmarc.k_failed_n", { count: d.totals.failed })} />
            <Kpi label={t("dmarc.k_dkim")} value={p(d.totals.dkim_pass, d.totals.messages)} sub={t("dmarc.k_dkim_sub")} />
            <Kpi label={t("dmarc.k_spf")} value={p(d.totals.spf_pass, d.totals.messages)} sub={t("dmarc.k_spf_sub")} />
          </div>
          <Card title={t("dmarc.chart")} right={<button className="btn small" onClick={() => setTable(!table)} aria-pressed={table}>{table ? t("dashboard.show_chart") : t("dashboard.show_table")}</button>}>
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <Legend series={series} />
              {table ? (
                <div className="tbl-wrap" style={{ maxHeight: 300, overflowY: "auto" }}>
                  <table>
                    <thead><tr><th>{t("dmarc.day")}</th>{series.map((s) => <th key={s.key} style={{ textAlign: "right" }}>{s.label}</th>)}</tr></thead>
                    <tbody>{d.series.map((b) => <tr key={b.t}><td className="nowrap">{fmtDay(b.t, true)}</td><td className="num" style={{ textAlign: "right" }}>{b.passed}</td><td className="num" style={{ textAlign: "right" }}>{b.failed}</td></tr>)}</tbody>
                  </table>
                </div>
              ) : (
                <StackedColumns height={240} data={d.series} series={series} label={t("dmarc.chart")}
                  xLabel={(b) => fmtDay(b.t, true)} xTick={(b, i) => (i % Math.max(1, Math.round(days / 6)) === 0 ? fmtDay(b.t, false) : null)} />
              )}
              <p className="muted" style={{ margin: 0, fontSize: 12 }}>{t("dmarc.chart_note")}</p>
            </div>
          </Card>
          <Card title={t("dmarc.senders")} flush>
            {!d.senders.length ? <div className="empty">{t(d.has_rua ? "dmarc.no_data" : "dmarc.no_data_rua")}</div> : (
              <div className="tbl-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>{t("dmarc.sender")}</th><th>{t("dmarc.verdict_h")}</th><th style={{ textAlign: "right" }}>{t("dmarc.messages")}</th>
                      <th>{t("dmarc.dmarc_pass")}</th><th style={{ textAlign: "right" }}>DKIM</th><th style={{ textAlign: "right" }}>SPF</th>
                    </tr>
                  </thead>
                  <tbody>{d.senders.map((s) => <SenderRows key={s.name} s={s} total={d.totals.messages} />)}</tbody>
                </table>
              </div>
            )}
          </Card>
          <div className="grid halves">
            <Card title={t("dmarc.reporters")}>
              <BarList items={d.reporters.map((r) => ({ name: r.name, count: r.messages }))} color="var(--chart-1)" empty={t("dmarc.no_data")} />
            </Card>
            <Card title={t("dmarc.reports")} flush>
              {!d.reports.length ? <div className="empty">{t("dmarc.no_data")}</div> : (
                <div className="tbl-wrap" style={{ maxHeight: 360, overflowY: "auto" }}>
                  <table>
                    <tbody>
                      {d.reports.map((r) => (
                        <tr key={r.id} style={{ cursor: "pointer" }} onClick={() => setReport(r.id)}>
                          <td><div className="cell-main">{r.org_name}</div><div className="cell-sub">{formatDate(r.begin, i18n.language, false)} – {formatDate(r.end, i18n.language, false)}</div></td>
                          <td className="num" style={{ textAlign: "right" }}>{r.messages.toLocaleString()}</td>
                          <td style={{ width: 24 }}><ChevronRight style={{ width: 16, height: 16, color: "var(--ink-muted)" }} aria-hidden /></td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </Card>
          </div>
        </div>
      )}
      {report !== null && <ReportDrawer id={report} onClose={() => setReport(null)} />}
    </>
  );
}

function ReportDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: ["dmarc", "report", id], queryFn: () => api.get<ReportFull>(`/dmarc/report/${id}`) });
  const r = q.data;
  const res = (v: string) => <span className={`status ${v === "pass" ? "s-ok" : "s-bad"}`} style={{ padding: "1px 7px" }}>{v || "–"}</span>;
  return (
    <Drawer title={r ? r.org_name : t("common.loading")} sub={r && `${formatDate(r.begin, i18n.language)} – ${formatDate(r.end, i18n.language)}`} onClose={onClose}>
      {q.error ? <ErrorText error={q.error} /> : !r ? <Loading /> : (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <dl className="kv">
            <dt>{t("dmarc.domain")}</dt><dd>{r.domain}</dd>
            <dt>{t("dmarc.report_id")}</dt><dd className="mono" style={{ fontSize: 12 }}>{r.report_id}</dd>
            <dt>{t("dmarc.reporter_email")}</dt><dd>{r.org_email || "–"}</dd>
            <dt>{t("dmarc.policy_seen")}</dt><dd><PolicyPill p={r.p} pct={r.pct} /></dd>
            <dt>{t("dmarc.messages")}</dt><dd>{r.messages.toLocaleString()}</dd>
          </dl>
          {r.rows.map((x, i) => (
            <div key={i} style={{ border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "baseline" }}>
                <div style={{ minWidth: 0 }}>
                  <b>{x.sender}</b>
                  <div className="cell-sub mono" style={{ wordBreak: "break-all" }}>{x.source_ip}{x.source_host ? ` · ${x.source_host}` : ""}</div>
                </div>
                <b className="num">{x.count.toLocaleString()}</b>
              </div>
              <div style={{ display: "flex", gap: 10, flexWrap: "wrap", fontSize: 12.5, alignItems: "center" }}>
                <span>DKIM {res(x.dkim)}</span><span>SPF {res(x.spf)}</span>
                <span className="muted">{t("dmarc.disposition")}: {t(`dmarc.disp.${x.disposition}`, { defaultValue: x.disposition })}</span>
              </div>
              <dl className="kv" style={{ gridTemplateColumns: "120px minmax(0,1fr)", fontSize: 12.5 }}>
                <dt>From</dt><dd>{x.header_from || "–"}</dd>
                <dt>{t("dmarc.envelope_from")}</dt><dd>{x.envelope_from || "–"}</dd>
                <dt>{t("dmarc.dkim_signed")}</dt><dd className="mono" style={{ fontSize: 11.5 }}>{x.dkim_auth.map((a) => `${a.domain}${a.selector ? ` (${a.selector})` : ""}: ${a.result}`).join(", ") || "–"}</dd>
                <dt>{t("dmarc.spf_checked")}</dt><dd className="mono" style={{ fontSize: 11.5 }}>{x.spf_auth.map((a) => `${a.domain}: ${a.result}`).join(", ") || "–"}</dd>
                {x.reasons.length > 0 && <><dt>{t("dmarc.reason")}</dt><dd>{x.reasons.map((rs) => t(`dmarc.reasons.${rs.type}`, { defaultValue: rs.type }) + (rs.comment ? ` (${rs.comment})` : "")).join(", ")}</dd></>}
              </dl>
            </div>
          ))}
        </div>
      )}
    </Drawer>
  );
}
