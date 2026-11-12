// DNS toolbox: check a domain, MX lookup, SPF and DMARC generators, blocklist
// status, Cloudflare settings and the change log. Every write to Cloudflare is
// shown as "current / new" first and can be undone from the change log.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Check, Clock, Cloud, Copy, ExternalLink, Info, Lock, Pencil, Plus, RefreshCw, Rocket, Save, ShieldCheck, Trash2, Undo2, X } from "lucide-react";
import { Suspense, lazy, useEffect, useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Card, ErrorText, Field, Loading, Modal, PageHead, formatDate, useToast } from "../components/ui";
import { ApiError, api } from "../lib/api";

const BimiTab = lazy(() => import("./DnsBimi"));
const DkimTab = lazy(() => import("./DnsDkim"));

// --- types -----------------------------------------------------------------

export type Item = { code: string; status: string; value: string; problems: string[]; fix: string | null };
type GoLiveState = { status: "live" | "rolled_back"; time: string; actor: string; previous_mx: Rec[]; mx: Rec[] } | null;
export type CheckResult = { domain: string; items: Item[]; managed: boolean; golive: GoLiveState };
type Rec = { type: string; name: string; content: string; priority?: number | null; id?: string | null };
type Change = { action: "create" | "update" | "delete" | "none"; type: string; name: string; before: Rec | null; after: Rec | null; reason: string };
type Plan = { zone_name: string; domain: string; changes: Change[]; warnings?: string[] };
type MxRow = { priority: number; host: string };
type MxCloudflare = { domain: string; zone: string; managed: boolean; hostname: string; mx_priority: number; golive: GoLiveState; records: MxRow[] };
type Server = { hostname: string; addresses: string[]; spf_name: string; spf_include: string; domains: string[] };
export type CfStatus = { configured: boolean; status: string | null; zones: string[] };
export type PublishKind = "spf" | "spf_value" | "dkim" | "dmarc" | "server_spf" | "mx" | "mta_sts" | "tls_rpt" | "tlsa" | "dmarc_rua" | "dmarc_policy" | "dmarc_auth" | "bimi";
type Smtp = { reachable: boolean; starttls: boolean; tls_ok: boolean; error: string; cert: { matches: boolean; days_left: number | null } | null } | null;
type MxResult = {
  domain: string; null_mx: boolean; implicit: boolean; error: string;
  mx: { priority: number; host: string; error: string; addresses: { ip: string; ptr: string[]; ptr_ok: boolean; smtp: Smtp }[]; smtp_ok: boolean | null; starttls: boolean | null; ptr_ok: boolean }[];
  spf: string | null; spf_problems: string[]; dmarc: string | null; dmarc_status: string; dmarc_problems: string[];
  notes: { level: string; code: string; host: string }[];
};
type BlResult = { zone: string; name: string; status: string; codes: string[]; reason: string; delist: string; since: string | null };
export type Blacklist = {
  checked_at: string | null; listed: { ip: string; zone: string; name: string; since: string; reason: string; delist: string }[];
  addresses: { ip: string; results: BlResult[] }[]; unusable: string[];
};
type Journal = { time: string; batch: string; actor: string; domain: string; action: string; reason: string; before: Rec | null; after: Rec | null; undo?: string }[];

const TABS = ["check", "mx", "spf", "dkim", "dmarc", "tls", "bimi", "blacklist", "cloudflare", "journal"] as const;
type Tab = (typeof TABS)[number];

// --- small parts -------------------------------------------------------------

const STATUS_CLASS: Record<string, string> = { pending: "s-info", ok: "s-ok", weak: "s-warn", missing: "s-bad", bad: "s-bad", error: "s-bad", listed: "s-bad", clean: "s-ok", unusable: "s-mute", skipped: "s-mute" };

export function Pill({ s, label }: { s: string; label?: string }) {
  const { t } = useTranslation();
  const icon = s === "ok" || s === "clean" ? <Check /> : s === "weak" || s === "pending" ? <Clock /> : s === "unusable" || s === "skipped" ? <Info /> : <X />;
  return <span className={`status ${STATUS_CLASS[s] ?? "s-mute"}`}>{icon}{label ?? t(`dns.status.${s}`, { defaultValue: s })}</span>;
}

function Yes({ v }: { v: boolean | null | undefined }) {
  if (v === null || v === undefined) return <span className="muted">–</span>;
  return <span className={`status ${v ? "s-ok" : "s-bad"}`} style={{ padding: 3 }}>{v ? <Check /> : <X />}</span>;
}

export function Problems({ codes }: { codes: string[] }) {
  const { t } = useTranslation();
  if (!codes.length) return null;
  return (
    <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 13 }}>
      {codes.map((c) => {
        const [code, ...rest] = c.split(":");
        return <li key={c}>{t(`dns.problem.${code}`, { value: rest.join(":"), defaultValue: c })}</li>;
      })}
    </ul>
  );
}

export function CopyBtn({ text }: { text: string }) {
  const { t } = useTranslation();
  const toast = useToast();
  return (
    <button type="button" className="btn small ghost" onClick={() => navigator.clipboard?.writeText(text).then(() => toast(t("common.copied")))}>
      <Copy /> {t("common.copy")}
    </button>
  );
}

export function Code({ children }: { children: ReactNode }) {
  return <code className="mono" style={{ display: "block", fontSize: 12, background: "var(--surface-sunk)", padding: "8px 10px", borderRadius: "var(--r-sm)", overflowX: "auto", whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{children}</code>;
}

function DomainForm({ initial, onSubmit, placeholder, domains }: { initial: string; onSubmit: (d: string) => void; placeholder: string; domains?: string[] }) {
  const { t } = useTranslation();
  const [v, setV] = useState(initial);
  useEffect(() => setV(initial), [initial]);
  return (
    <form className="filters" style={{ padding: 0, border: 0 }} onSubmit={(e: FormEvent) => { e.preventDefault(); if (v.trim()) onSubmit(v.trim().toLowerCase()); }}>
      <input className="input" style={{ flex: "1 1 260px" }} value={v} onChange={(e) => setV(e.target.value)} placeholder={placeholder} aria-label={placeholder} list="mf-domains" autoComplete="off" spellCheck={false} />
      {domains && <datalist id="mf-domains">{domains.map((d) => <option key={d} value={d} />)}</datalist>}
      <button className="btn dark" type="submit">{t("dns.run")}</button>
    </form>
  );
}

function useServer() {
  return useQuery({ queryKey: ["dns-server"], queryFn: () => api.get<Server>("/dns/server"), staleTime: 60_000 });
}

// --- page ----------------------------------------------------------------------

export default function Dns() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const tab = (TABS as readonly string[]).includes(params.get("tab") ?? "") ? (params.get("tab") as Tab) : "check";
  const setTab = (t2: Tab) => setParams((p) => { p.set("tab", t2); return p; }, { replace: true });
  return (
    <>
      <PageHead title={t("dns.title")} sub={t("dns.sub")} />
      <Card flush>
        <div className="tabs" role="tablist">
          {TABS.map((x) => (
            <button key={x} type="button" role="tab" aria-selected={tab === x} onClick={() => setTab(x)}>{t(`dns.tab.${x}`)}</button>
          ))}
        </div>
        <div className="card-body">
          {tab === "check" && <CheckTab />}
          {tab === "mx" && <MxTab />}
          {tab === "spf" && <SpfTab />}
          {tab === "dkim" && <Suspense fallback={<Loading />}><DkimTab /></Suspense>}
          {tab === "dmarc" && <DmarcTab />}
          {tab === "tls" && <TlsTab />}
          {tab === "bimi" && <Suspense fallback={<Loading />}><BimiTab /></Suspense>}
          {tab === "blacklist" && <BlacklistTab />}
          {tab === "cloudflare" && <CloudflareTab />}
          {tab === "journal" && <JournalTab />}
        </div>
      </Card>
    </>
  );
}

// --- check domain -----------------------------------------------------------------

function CheckTab() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const server = useServer();
  const domain = params.get("domain") ?? server.data?.domains[0] ?? "";
  const q = useQuery({
    queryKey: ["dns-check", domain], queryFn: () => api.get<CheckResult>(`/dns/check/${encodeURIComponent(domain)}`), enabled: !!domain,
    // Just published: look again every 20 seconds until DNS shows it.
    refetchInterval: (qq) => (qq.state.data?.items.some((i) => i.status === "pending") ? 20_000 : false),
  });
  const [publish, setPublish] = useState<{ kind: PublishKind; value?: string } | null>(null);
  const kindFor: Record<string, PublishKind> = { spf: "spf", dkim: "dkim", dmarc: "dmarc", dane: "tlsa" };
  const tlsItems = ["mta_sts", "tls_rpt", "dane"];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <DomainForm initial={domain} domains={server.data?.domains} placeholder={t("dns.domain_ph")} onSubmit={(d) => setParams((p) => { p.set("domain", d); return p; })} />
      {!domain ? <div className="empty">{t("dns.check_empty")}</div> : q.isLoading ? <Loading /> : q.error ? <ErrorText error={q.error} /> : q.data && (
        <>
          <div className="tbl-wrap">
            <table>
              <thead><tr><th>{t("dns.col.check")}</th><th>{t("dns.col.status")}</th><th>{t("dns.col.value")}</th><th /></tr></thead>
              <tbody>
                {q.data.items.map((i) => (
                  <tr key={i.code}>
                    <td className="nowrap"><span className="cell-main">{t(`dns.item.${i.code}`)}</span></td>
                    <td className="nowrap"><Pill s={i.status} /></td>
                    <td style={{ minWidth: 260 }}>
                      {i.value ? <span className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>{i.value}</span> : <span className="muted">–</span>}
                      <Problems codes={i.problems} />
                      {i.fix && (
                        <div style={{ marginTop: 8 }}>
                          <div className="cell-sub" style={{ fontWeight: 650, marginBottom: 4 }}>{t("dns.suggested")}</div>
                          <Code>{i.fix}</Code>
                        </div>
                      )}
                    </td>
                    <td className="nowrap" style={{ textAlign: "right" }}>
                      {i.fix && <CopyBtn text={i.fix} />}
                      {tlsItems.includes(i.code) && i.status !== "ok" && (
                        <button className="btn small ghost" type="button" onClick={() => setParams((p) => { p.set("tab", "tls"); return p; })}>
                          <Lock /> {t("dns.tls.set_up")}
                        </button>
                      )}
                      {i.fix && kindFor[i.code] && (
                        <button className="btn small" type="button" onClick={() => setPublish({ kind: kindFor[i.code], value: i.code === "dmarc" ? i.fix! : undefined })}>
                          <Cloud /> {t("dns.publish")}
                        </button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div>
            <button className="btn small" type="button" onClick={() => setParams((p) => { p.set("tab", "mx"); p.set("domain", q.data!.domain); return p; })}>
              <Pencil /> {t("dns.mxe.edit_link")}
            </button>
          </div>
          {q.data.managed ? <GoLive domain={q.data.domain} /> : (
            <div className="callout"><Info /><span>{t("dns.not_managed", { domain: q.data.domain })}</span></div>
          )}
        </>
      )}
      {publish && <PublishModal kind={publish.kind} domain={domain} value={publish.value} onClose={() => setPublish(null)} onDone={() => q.refetch()} />}
    </div>
  );
}

// --- publish (plan, then apply) ------------------------------------------------------

function recText(r: Rec | null) {
  if (!r) return "–";
  return (r.type === "MX" && r.priority != null ? `${r.priority} ` : "") + r.content;
}

function PlanView({ plan }: { plan: Plan }) {
  const { t } = useTranslation();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <div className="cell-sub">{t("dns.zone")}: <b>{plan.zone_name}</b></div>
      {plan.changes.map((c, n) => (
        <div key={n} style={{ border: "1px solid var(--line)", borderRadius: "var(--r-sm)", padding: 12 }}>
          <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 8 }}>
            <span className={`status ${c.action === "none" ? "s-ok" : c.action === "delete" ? "s-bad" : "s-info"}`}>{t(`dns.action.${c.action}`)}</span>
            <span className="mono" style={{ fontSize: 12 }}>{c.type} {c.name}</span>
          </div>
          <dl className="kv" style={{ gridTemplateColumns: "110px minmax(0,1fr)" }}>
            <dt>{t("dns.current")}</dt><dd className="mono" style={{ fontSize: 12 }}>{recText(c.before)}</dd>
            {c.action !== "none" && c.action !== "delete" && <><dt>{t("dns.new")}</dt><dd className="mono" style={{ fontSize: 12, color: "var(--ok)" }}>{recText(c.after)}</dd></>}
          </dl>
        </div>
      ))}
    </div>
  );
}

export function PublishModal({ kind, domain, value, mx, onClose, onDone }: { kind: PublishKind; domain: string; value?: string; mx?: MxRow[]; onClose: () => void; onDone?: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const body = { kind, domain, value, mx };
  const plan = useQuery({ queryKey: ["dns-plan", kind, domain, value, mx], queryFn: () => api.post<Plan>("/dns/publish/plan", body), retry: false, gcTime: 0 });
  const apply = useMutation({
    mutationFn: () => api.post<{ batch: string | null }>("/dns/publish/apply", body),
    onSuccess: () => {
      toast(t("dns.published"));
      qc.invalidateQueries({ queryKey: ["dns-journal"] });
      onDone?.();
      onClose();
    },
  });
  const needed = plan.data?.changes.some((c) => c.action !== "none");
  return (
    <Modal title={t(`dns.publish_title.${kind}`)} onClose={onClose} icon={<Cloud />}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <p className="muted" style={{ margin: 0 }}>{t(`dns.publish_help.${kind}`)}</p>
        {plan.data?.warnings?.map((w) => <div key={w} className="callout warn"><Info /><span>{t(`dns.warn.${w}`, { domain })}</span></div>)}
        {plan.isLoading ? <Loading /> : plan.error ? <ErrorText error={plan.error} /> : plan.data && <PlanView plan={plan.data} />}
        <ErrorText error={apply.error} />
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
          <button className="btn" type="button" onClick={onClose}>{t("common.cancel")}</button>
          {plan.data && (needed ? (
            <button className="btn primary" type="button" disabled={apply.isPending} onClick={() => apply.mutate()}><Cloud /> {t("dns.publish_now")}</button>
          ) : <span className="status s-ok"><Check />{t("dns.nothing_to_do")}</span>)}
        </div>
      </div>
    </Modal>
  );
}

// --- go-live --------------------------------------------------------------------------

type GoLiveInfo = {
  domain: string; hostname: string; state: GoLiveState; plan: Plan | null; plan_error: string | null;
  preflight: { ok: boolean; checks: { code: string; ok: boolean; detail: string; blocking: boolean }[] };
};

function GoLive({ domain }: { domain: string }) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const q = useQuery({ queryKey: ["dns-golive", domain], queryFn: () => api.get<GoLiveInfo>(`/dns/golive/${encodeURIComponent(domain)}`) });
  const [confirm, setConfirm] = useState("");
  const [force, setForce] = useState(false);
  const go = useMutation({
    mutationFn: () => api.post(`/dns/golive/${encodeURIComponent(domain)}`, { confirm, force }),
    onSuccess: () => { toast(t("dns.gl.done_toast")); setConfirm(""); q.refetch(); },
  });
  const back = useMutation({
    mutationFn: () => api.post(`/dns/golive/${encodeURIComponent(domain)}/rollback`),
    onSuccess: () => { toast(t("dns.gl.rolled_back")); q.refetch(); },
  });
  const d = q.data;
  const live = d?.state?.status === "live";
  return (
    <section className="card" style={{ borderColor: "var(--accent)" }}>
      <div className="card-head">
        <span className="status s-info" style={{ padding: 4 }}><Rocket /></span>
        <h2>{t("dns.gl.title")}</h2>
        {live && <div className="right"><span className="status s-ok"><Check />{t("dns.gl.live")}</span></div>}
      </div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {q.isLoading || q.isFetching ? <Loading /> : q.error ? (
          <>
            <ErrorText error={q.error} />
            <div><button className="btn small" type="button" onClick={() => q.refetch()}><RefreshCw /> {t("common.refresh")}</button></div>
          </>
        ) : d && (live ? (
          <>
            <p style={{ margin: 0 }}>{t("dns.gl.live_since", { host: d.hostname, time: formatDate(d.state!.time, i18n.language), who: d.state!.actor })}</p>
            <dl className="kv">
              <dt>{t("dns.gl.old_mx")}</dt><dd className="mono">{d.state!.previous_mx.map(recText).join(", ") || "–"}</dd>
            </dl>
            <ErrorText error={back.error} />
            <div><button className="btn danger" type="button" disabled={back.isPending} onClick={() => window.confirm(t("dns.gl.rollback_confirm")) && back.mutate()}><Undo2 /> {t("dns.gl.rollback")}</button></div>
          </>
        ) : (
          <>
            <p className="cell-sub" style={{ margin: 0 }}>{t("dns.gl.help", { host: d.hostname })}</p>
            {d.state?.status === "rolled_back" && <div className="callout warn"><Info /><span>{t("dns.gl.was_rolled_back")}</span></div>}
            <div className="cell-sub" style={{ fontWeight: 650 }}>{t("dns.gl.checks")}</div>
            {d.preflight.checks.map((c) => (
              <div key={c.code} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                <span className={`status ${c.ok ? "s-ok" : c.blocking ? "s-bad" : "s-warn"}`} style={{ padding: 2 }}>{c.ok ? <Check /> : <X />}</span>
                <span>
                  {t(`dns.gl.c.${c.code}`, { host: d.hostname })}
                  {!c.blocking && !c.ok && <span className="muted"> · {t("dns.gl.warning_only")}</span>}
                  {c.detail && <span className="cell-sub mono" style={{ display: "block", fontSize: 11.5 }}>{c.detail}</span>}
                </span>
              </div>
            ))}
            {d.plan ? <PlanView plan={d.plan} /> : d.plan_error && <ErrorText error={new ApiError(409, d.plan_error)} />}
            {d.plan && (
              <>
                <Field label={t("dns.gl.type_domain", { domain })}>
                  <input className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} placeholder={domain} autoComplete="off" spellCheck={false} />
                </Field>
                {!d.preflight.ok && (
                  <label className="check"><input type="checkbox" checked={force} onChange={(e) => setForce(e.target.checked)} /><span>{t("dns.gl.force")}</span></label>
                )}
                <ErrorText error={go.error} />
                <div>
                  <button className="btn primary" type="button" disabled={go.isPending || confirm.trim().toLowerCase() !== domain || (!d.preflight.ok && !force)} onClick={() => go.mutate()}>
                    <Rocket /> {t("dns.gl.go")}
                  </button>
                </div>
              </>
            )}
          </>
        ))}
      </div>
    </section>
  );
}

// --- MX lookup ---------------------------------------------------------------------------

function MxTab() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const server = useServer();
  const domain = params.get("domain") ?? "";
  const setDomain = (d: string) => setParams((p) => { p.set("domain", d); return p; });
  const [probe, setProbe] = useState(true);
  const q = useQuery({ queryKey: ["dns-mx", domain, probe], queryFn: () => api.get<MxResult>(`/dns/mx/${encodeURIComponent(domain)}?probe=${probe}`), enabled: !!domain, retry: false });
  const d = q.data;
  const level: Record<string, string> = { bad: "s-bad", warn: "s-warn", info: "s-info" };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <p className="muted" style={{ margin: 0 }}>{t("dns.mx.help")}</p>
      <DomainForm initial={domain} placeholder={t("dns.mx.ph")} onSubmit={setDomain} domains={server.data?.domains} />
      <label className="check"><input type="checkbox" checked={probe} onChange={(e) => setProbe(e.target.checked)} /><span>{t("dns.mx.probe")}</span></label>
      {q.isFetching ? <Loading /> : q.error ? <ErrorText error={q.error} /> : d && (
        d.null_mx ? <div className="callout"><Info /><span>{t("dns.mx.null_mx")}</span></div> : (
          <>
            <div className="tbl-wrap">
              <table>
                <thead><tr><th>{t("dns.mx.prio")}</th><th>{t("dns.mx.host")}</th><th>{t("dns.mx.ips")}</th><th>{t("dns.mx.smtp")}</th><th>{t("dns.mx.tls")}</th><th>{t("dns.mx.ptr")}</th></tr></thead>
                <tbody>
                  {d.mx.map((m) => (
                    <tr key={m.host}>
                      <td className="num">{m.priority}</td>
                      <td className="mono">{m.host}</td>
                      <td className="mono cell-sub">{m.addresses.map((a) => <div key={a.ip}>{a.ip}{a.ptr.length ? <span className="muted"> → {a.ptr.join(", ")}</span> : null}</div>)}{m.error && <span className="muted">{t(`dns.note.${m.error}`, { defaultValue: m.error })}</span>}</td>
                      <td><Yes v={m.smtp_ok} /></td>
                      <td><Yes v={m.starttls} /></td>
                      <td><Yes v={m.addresses.length ? m.ptr_ok : null} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <dl className="kv">
              <dt>SPF</dt><dd><span className="mono" style={{ fontSize: 12 }}>{d.spf ?? "–"}</span><Problems codes={d.spf_problems} /></dd>
              <dt>DMARC</dt><dd><span className="mono" style={{ fontSize: 12 }}>{d.dmarc ?? "–"}</span> <Pill s={d.dmarc_status} /><Problems codes={d.dmarc_problems} /></dd>
            </dl>
            {d.notes.map((n, i) => (
              <div key={i} style={{ display: "flex", gap: 8, alignItems: "center" }}>
                <span className={`status ${level[n.level] ?? "s-mute"}`} style={{ padding: 3 }}>{n.level === "info" ? <Info /> : n.level === "warn" ? <Clock /> : <X />}</span>
                <span>{t(`dns.note.${n.code}`, { host: n.host, defaultValue: n.code })}</span>
              </div>
            ))}
          </>
        )
      )}
      {domain && <MxEditor domain={domain} onDone={() => q.refetch()} />}
    </div>
  );
}

function MxEditor({ domain, onDone }: { domain: string; onDone: () => void }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["dns-mx-cf", domain], queryFn: () => api.get<MxCloudflare>(`/dns/mx/${encodeURIComponent(domain)}/cloudflare`), retry: false });
  const [rows, setRows] = useState<MxRow[]>([]);
  const [review, setReview] = useState(false);
  useEffect(() => { if (q.data) setRows(q.data.records.length ? q.data.records : [{ priority: 10, host: "" }]); }, [q.data]);
  const d = q.data;
  const set = (n: number, patch: Partial<MxRow>) => setRows(rows.map((r, i) => (i === n ? { ...r, ...patch } : r)));
  const clean = rows.map((r) => ({ priority: r.priority, host: r.host.trim().toLowerCase() })).filter((r) => r.host);
  const changed = !!d && JSON.stringify(clean) !== JSON.stringify(d.records);
  const hasMf = !!d && clean.some((r) => r.host === d.hostname);
  return (
    <section className="card" style={{ borderColor: "var(--accent)" }}>
      <div className="card-head">
        <span className="status s-info" style={{ padding: 4 }}><Cloud /></span>
        <h2>{t("dns.mxe.title")}</h2>
        {d && <div className="right"><span className="cell-sub">{t("dns.zone")}: <b>{d.zone}</b></span></div>}
      </div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {q.isLoading ? <Loading /> : q.error ? (
          q.error instanceof ApiError && (q.error.message === "cloudflare_not_configured" || q.error.message.startsWith("zone_not_found"))
            ? <div className="callout"><Info /><span>{t(q.error.message === "cloudflare_not_configured" ? "dns.mxe.no_cloudflare" : "dns.mxe.no_zone", { domain })}</span></div>
            : <ErrorText error={q.error} />
        ) : d && (
          <>
            <p className="cell-sub" style={{ margin: 0 }}>{t("dns.mxe.help", { host: d.hostname })}</p>
            {!d.managed && <div className="callout warn"><Info /><span>{t("dns.mxe.not_managed", { domain })}</span></div>}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn small" type="button" onClick={() => setRows([{ priority: d.mx_priority, host: d.hostname }])}><Rocket /> {t("dns.mxe.only_mf")}</button>
              {!hasMf && (
                <button className="btn small" type="button" onClick={() => setRows([...rows.filter((r) => r.host.trim()), { priority: Math.max(0, ...rows.map((r) => r.priority)) + 10, host: d.hostname }])}>
                  <Plus /> {t("dns.mxe.add_mf")}
                </button>
              )}
              {changed && <button className="btn small ghost" type="button" onClick={() => setRows(d.records.length ? d.records : [{ priority: 10, host: "" }])}><Undo2 /> {t("dns.mxe.reset")}</button>}
            </div>
            <div className="tbl-wrap">
              <table>
                <thead><tr><th style={{ width: 110 }}>{t("dns.mx.prio")}</th><th>{t("dns.mx.host")}</th><th /></tr></thead>
                <tbody>
                  {rows.map((r, n) => (
                    <tr key={n}>
                      <td><input className="input inline" type="number" min={0} max={65535} value={r.priority} aria-label={t("dns.mx.prio")} onChange={(e) => set(n, { priority: Math.max(0, Math.min(65535, Number(e.target.value) || 0)) })} style={{ width: 90 }} /></td>
                      <td><input className="input inline mono" value={r.host} aria-label={t("dns.mx.host")} placeholder="mx.example.com" spellCheck={false} autoComplete="off" onChange={(e) => set(n, { host: e.target.value })} style={{ width: "100%" }} /></td>
                      <td className="nowrap" style={{ textAlign: "right" }}>
                        {r.host.trim().toLowerCase() === d.hostname && <span className="tag" style={{ marginRight: 6 }}>Mailfilter</span>}
                        <button className="btn small ghost" type="button" aria-label={t("common.delete")} disabled={rows.length < 2} onClick={() => setRows(rows.filter((_, i) => i !== n))}><Trash2 /></button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn small" type="button" disabled={rows.length >= 10} onClick={() => setRows([...rows, { priority: Math.max(0, ...rows.map((r) => r.priority)) + 10, host: "" }])}><Plus /> {t("dns.mxe.add_row")}</button>
              <span className="grow" style={{ flex: 1 }} />
              <button className="btn primary" type="button" disabled={!changed || !clean.length} onClick={() => setReview(true)}><Cloud /> {t("dns.mxe.review")}</button>
            </div>
          </>
        )}
      </div>
      {review && <PublishModal kind="mx" domain={domain} mx={clean} onClose={() => setReview(false)} onDone={() => { q.refetch(); onDone(); }} />}
    </section>
  );
}

// --- SPF generator ---------------------------------------------------------------------------

function PublishTo({ kind, value }: { kind: PublishKind; value: string }) {
  const { t } = useTranslation();
  const server = useServer();
  const [domain, setDomain] = useState("");
  const [open, setOpen] = useState(false);
  return (
    <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
      <CopyBtn text={value} />
      <input className="input inline" style={{ flex: "1 1 200px" }} value={domain} onChange={(e) => setDomain(e.target.value.trim().toLowerCase())} placeholder={t("dns.domain_ph")} list="mf-domains2" aria-label={t("dns.domain_ph")} />
      <datalist id="mf-domains2">{server.data?.domains.map((d) => <option key={d} value={d} />)}</datalist>
      <button className="btn" type="button" disabled={!domain} onClick={() => setOpen(true)}><Cloud /> {t("dns.publish")}</button>
      {open && <PublishModal kind={kind} domain={domain} value={value} onClose={() => setOpen(false)} />}
    </div>
  );
}

function SpfTab() {
  const { t } = useTranslation();
  const server = useServer();
  const [f, setF] = useState({ mailfilter: true, m365: false, google: false, other: "", ip4: "", ip6: "", mx: false, a: false, all: "~" as "~" | "-" | "?" });
  const split = (s: string) => s.split(/[\s,]+/).filter(Boolean);
  const body = {
    mailfilter: f.mailfilter, includes: [...(f.m365 ? ["m365"] : []), ...(f.google ? ["google"] : []), ...split(f.other)],
    ip4: split(f.ip4), ip6: split(f.ip6), mx: f.mx, a: f.a, all: f.all,
  };
  const q = useQuery({ queryKey: ["dns-spf", body], queryFn: () => api.post<{ value: string; lookups: number; problems: string[] }>("/dns/spf/generate", body), retry: false, placeholderData: (p) => p });
  const chk = (k: "mailfilter" | "m365" | "google" | "mx" | "a", label: string) => (
    <label className="check"><input type="checkbox" checked={f[k]} onChange={(e) => setF({ ...f, [k]: e.target.checked })} /><span>{label}</span></label>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <p className="muted" style={{ margin: 0 }}>{t("dns.spf.help")}</p>
      <div className="callout"><Info /><div><b>{t("dns.spf.merge_title")}</b><br /><span className="cell-sub">{t("dns.spf.merge_help")}</span></div></div>
      {chk("mailfilter", t("dns.spf.mailfilter", { name: server.data?.spf_name ?? "_spf…" }))}
      {chk("m365", t("dns.spf.m365"))}
      {chk("google", t("dns.spf.google"))}
      {chk("mx", t("dns.spf.mx"))}
      {chk("a", t("dns.spf.a"))}
      <div className="form-grid">
        <Field label={t("dns.spf.ip4")}><input className="input mono" value={f.ip4} onChange={(e) => setF({ ...f, ip4: e.target.value })} placeholder="203.0.113.25 198.51.100.0/24" /></Field>
        <Field label={t("dns.spf.ip6")}><input className="input mono" value={f.ip6} onChange={(e) => setF({ ...f, ip6: e.target.value })} placeholder="2001:db8::/48" /></Field>
        <Field label={t("dns.spf.other")} full><input className="input mono" value={f.other} onChange={(e) => setF({ ...f, other: e.target.value })} placeholder="spf.transip.email" /></Field>
      </div>
      <Field label={t("dns.spf.all")}>
        <div className="seg" role="group">
          {(["~", "-", "?"] as const).map((x) => <button key={x} type="button" aria-pressed={f.all === x} onClick={() => setF({ ...f, all: x })}>{t(`dns.spf.all_${x === "~" ? "soft" : x === "-" ? "hard" : "neutral"}`)}</button>)}
        </div>
      </Field>
      <div className="cell-sub" style={{ fontWeight: 650 }}>{t("dns.result")}</div>
      {q.error ? <ErrorText error={q.error} /> : q.data && (
        <>
          <Code>{q.data.value}</Code>
          <div className="cell-sub">{t("dns.spf.lookups", { n: q.data.lookups })} {q.data.lookups > 10 && <span className="status s-bad">{t("dns.problem.too_many_lookups")}</span>}</div>
          <Problems codes={q.data.problems} />
          <PublishTo kind="spf_value" value={q.data.value} />
        </>
      )}
    </div>
  );
}

// --- DMARC generator -------------------------------------------------------------------------

function DmarcTab() {
  const { t } = useTranslation();
  const [f, setF] = useState({ policy: "none" as "none" | "quarantine" | "reject", pct: 100, rua: "", adkim: "r" as "r" | "s", aspf: "r" as "r" | "s" });
  const body = { policy: f.policy, pct: f.pct, rua: f.rua.split(/[\s,]+/).filter(Boolean), adkim: f.adkim, aspf: f.aspf };
  const q = useQuery({ queryKey: ["dns-dmarc", body], queryFn: () => api.post<{ value: string; status: string; problems: string[] }>("/dns/dmarc/generate", body), retry: false, placeholderData: (p) => p });
  const seg = <K extends "policy" | "adkim" | "aspf">(k: K, opts: readonly string[], key: (o: string) => string) => (
    <div className="seg" role="group">
      {opts.map((o) => <button key={o} type="button" aria-pressed={f[k] === o} onClick={() => setF({ ...f, [k]: o })}>{t(key(o))}</button>)}
    </div>
  );
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <p className="muted" style={{ margin: 0 }}>{t("dns.dmarc.help")}</p>
      <Field label={t("dns.dmarc.policy")} hint={t(`dns.dmarc.policy_${f.policy}`)}>{seg("policy", ["none", "quarantine", "reject"], (o) => `dns.dmarc.p_${o}`)}</Field>
      <div className="form-grid">
        <Field label={t("dns.dmarc.pct")}><input className="input" type="number" min={0} max={100} value={f.pct} onChange={(e) => setF({ ...f, pct: Math.max(0, Math.min(100, Number(e.target.value) || 0)) })} /></Field>
        <Field label={t("dns.dmarc.rua")}><input className="input" value={f.rua} onChange={(e) => setF({ ...f, rua: e.target.value })} placeholder="dmarc@rhis.nl" /></Field>
        <Field label={t("dns.dmarc.adkim")}>{seg("adkim", ["r", "s"], (o) => `dns.dmarc.${o === "r" ? "relaxed" : "strict"}`)}</Field>
        <Field label={t("dns.dmarc.aspf")}>{seg("aspf", ["r", "s"], (o) => `dns.dmarc.${o === "r" ? "relaxed" : "strict"}`)}</Field>
      </div>
      <div className="cell-sub" style={{ fontWeight: 650 }}>{t("dns.result")}</div>
      {q.error ? <ErrorText error={q.error} /> : q.data && (
        <>
          <Code>{q.data.value}</Code>
          <Problems codes={q.data.problems} />
          <PublishTo kind="dmarc" value={q.data.value} />
        </>
      )}
    </div>
  );
}

// --- MTA-STS, TLS-RPT, DANE ----------------------------------------------------------------

type StsPolicy = { domain: string; mode: "testing" | "enforce" | "none"; mx: string[]; max_age: number; id: string; updated: string; txt: string; policy: string };
type TlsInfo = {
  domain: string; hostname: string; managed: boolean; policy: StsPolicy | null; policy_url: string; tlsrpt_default: string;
  policy_default: { mode: "testing" | "enforce" | "none"; max_age: number; mx: string[] };
  dane: { host: string; name: string; expected: string | null; published: string[]; host_signed: boolean | null; domain_signed: boolean | null; problems: string[]; status: string };
};

function TlsTab() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const server = useServer();
  const domain = params.get("domain") ?? server.data?.domains[0] ?? "";
  const q = useQuery({ queryKey: ["dns-tls", domain], queryFn: () => api.get<TlsInfo>(`/dns/tls/${encodeURIComponent(domain)}`), enabled: !!domain });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <p className="muted" style={{ margin: 0 }}>{t("dns.tls.help")}</p>
      <DomainForm initial={domain} domains={server.data?.domains} placeholder={t("dns.domain_ph")} onSubmit={(d) => setParams((p) => { p.set("domain", d); return p; })} />
      {!domain ? <div className="empty">{t("dns.check_empty")}</div> : q.isLoading ? <Loading /> : q.error ? <ErrorText error={q.error} /> : q.data && (
        <>
          {!q.data.managed && <div className="callout warn"><Info /><span>{t("dns.not_managed", { domain })}</span></div>}
          <StsCard info={q.data} onSaved={() => q.refetch()} />
          <TlsRptCard info={q.data} />
          <DaneCard info={q.data} onDone={() => q.refetch()} />
        </>
      )}
    </div>
  );
}

function TlsCard({ icon, title, right, children }: { icon: ReactNode; title: string; right?: ReactNode; children: ReactNode }) {
  return (
    <section className="card">
      <div className="card-head">
        <span className="status s-info" style={{ padding: 4 }}>{icon}</span>
        <h2>{title}</h2>
        {right && <div className="right">{right}</div>}
      </div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 12 }}>{children}</div>
    </section>
  );
}

function StsCard({ info, onSaved }: { info: TlsInfo; onSaved: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const start = info.policy ?? info.policy_default;
  const [f, setF] = useState({ mode: start.mode, mx: start.mx.join(" "), max_age: start.max_age });
  useEffect(() => { const s2 = info.policy ?? info.policy_default; setF({ mode: s2.mode, mx: s2.mx.join(" "), max_age: s2.max_age }); }, [info]);
  const [publish, setPublish] = useState(false);
  const save = useMutation({
    mutationFn: () => api.put<StsPolicy>(`/dns/mta-sts/${encodeURIComponent(info.domain)}`, { mode: f.mode, mx: f.mx.split(/[\s,]+/).filter(Boolean), max_age: f.max_age }),
    onSuccess: () => { toast(t("common.saved")); onSaved(); },
  });
  const p = info.policy;
  const dirty = !p || p.mode !== f.mode || p.max_age !== f.max_age || p.mx.join(" ") !== f.mx.split(/[\s,]+/).filter(Boolean).join(" ");
  return (
    <TlsCard icon={<Lock />} title={t("dns.tls.sts_title")} right={p ? <Pill s={p.mode === "enforce" ? "ok" : "weak"} label={t(`dns.tls.mode_${p.mode}`)} /> : <Pill s="missing" />}>
      <p className="cell-sub" style={{ margin: 0 }}>{t("dns.tls.sts_help", { host: info.hostname })}</p>
      <Field label={t("dns.tls.mode")} hint={t(`dns.tls.mode_${f.mode}_help`)}>
        <div className="seg" role="group">
          {(["testing", "enforce", "none"] as const).map((m) => <button key={m} type="button" aria-pressed={f.mode === m} onClick={() => setF({ ...f, mode: m })}>{t(`dns.tls.mode_${m}`)}</button>)}
        </div>
      </Field>
      <div className="form-grid">
        <Field label={t("dns.tls.mx")} hint={t("dns.tls.mx_hint")}><input className="input mono" value={f.mx} onChange={(e) => setF({ ...f, mx: e.target.value })} spellCheck={false} /></Field>
        <Field label={t("dns.tls.max_age")}>
          <select className="select" value={f.max_age} onChange={(e) => setF({ ...f, max_age: Number(e.target.value) })}>
            {[86400, 604800, 2592000].map((n) => <option key={n} value={n}>{t(`dns.tls.age_${n}`)}</option>)}
          </select>
        </Field>
      </div>
      <ErrorText error={save.error} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn primary" type="button" disabled={!dirty || save.isPending || !info.managed} onClick={() => save.mutate()}><Save /> {t("dns.tls.save_policy")}</button>
        {p && <button className="btn" type="button" disabled={dirty} onClick={() => setPublish(true)}><Cloud /> {t("dns.publish")}</button>}
        {p && <a className="btn ghost" href={info.policy_url} target="_blank" rel="noreferrer"><ExternalLink /> {t("dns.tls.open_policy")}</a>}
      </div>
      {p && (
        <>
          <div className="cell-sub" style={{ fontWeight: 650 }}>{t("dns.tls.policy_file")} · <span className="mono">{info.policy_url}</span></div>
          <Code>{p.policy}</Code>
          <div className="cell-sub" style={{ fontWeight: 650 }}>{t("dns.tls.records")}</div>
          <Code>{`_mta-sts.${info.domain}  TXT  "${p.txt}"\nmta-sts.${info.domain}  CNAME  ${info.hostname}`}</Code>
        </>
      )}
      {publish && <PublishModal kind="mta_sts" domain={info.domain} onClose={() => setPublish(false)} onDone={onSaved} />}
    </TlsCard>
  );
}

function TlsRptCard({ info }: { info: TlsInfo }) {
  const { t } = useTranslation();
  const [rua, setRua] = useState(info.tlsrpt_default);
  useEffect(() => setRua(info.tlsrpt_default), [info.tlsrpt_default]);
  const [open, setOpen] = useState(false);
  return (
    <TlsCard icon={<Info />} title={t("dns.tls.rpt_title")}>
      <p className="cell-sub" style={{ margin: 0 }}>{t("dns.tls.rpt_help")}</p>
      <Field label={t("dns.tls.rpt_rua")}><input className="input" value={rua} onChange={(e) => setRua(e.target.value)} spellCheck={false} /></Field>
      <div><button className="btn" type="button" disabled={!rua.trim()} onClick={() => setOpen(true)}><Cloud /> {t("dns.publish")}</button></div>
      {open && <PublishModal kind="tls_rpt" domain={info.domain} value={rua} onClose={() => setOpen(false)} />}
    </TlsCard>
  );
}

function DaneCard({ info, onDone }: { info: TlsInfo; onDone: () => void }) {
  const { t } = useTranslation();
  const d = info.dane;
  const [open, setOpen] = useState(false);
  const zoneRow = (label: string, v: boolean | null) => (
    <><dt>{label}</dt><dd>{v === null ? <span className="muted">{t("dns.tls.unknown")}</span> : <Pill s={v ? "ok" : "bad"} label={t(v ? "dns.tls.signed" : "dns.tls.unsigned")} />}</dd></>
  );
  return (
    <TlsCard icon={<ShieldCheck />} title={t("dns.tls.dane_title")} right={<Pill s={d.status} />}>
      <p className="cell-sub" style={{ margin: 0 }}>{t("dns.tls.dane_help", { host: d.host })}</p>
      <dl className="kv">
        <dt>{t("dns.tls.record")}</dt><dd className="mono" style={{ fontSize: 12 }}>{d.name} TLSA</dd>
        <dt>{t("dns.tls.expected")}</dt><dd className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>{d.expected ?? "–"}</dd>
        <dt>{t("dns.tls.published")}</dt><dd className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>{d.published.join(" · ") || "–"}</dd>
        {zoneRow(t("dns.tls.dnssec_host", { host: d.host }), d.host_signed)}
        {zoneRow(t("dns.tls.dnssec_domain", { domain: info.domain }), d.domain_signed)}
      </dl>
      <Problems codes={d.problems} />
      {(d.host_signed === false || d.domain_signed === false) && (
        <div className="callout warn"><Info /><span>{t("dns.tls.dnssec_how")}</span></div>
      )}
      <div><button className="btn" type="button" disabled={!d.expected} onClick={() => setOpen(true)}><Cloud /> {t("dns.tls.publish_tlsa")}</button></div>
      {open && <PublishModal kind="tlsa" domain={info.hostname} onClose={() => setOpen(false)} onDone={onDone} />}
    </TlsCard>
  );
}

// --- blocklists ------------------------------------------------------------------------------

function BlacklistTab() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["dns-blacklist"], queryFn: () => api.get<Blacklist>("/dns/blacklist") });
  const run = useMutation({ mutationFn: () => api.post<Blacklist>("/dns/blacklist/check"), onSuccess: (d) => qc.setQueryData(["dns-blacklist"], d) });
  const d = q.data;
  const zones = d?.addresses[0]?.results ?? [];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <p className="muted grow" style={{ margin: 0, flex: "1 1 300px" }}>{t("dns.bl.help")}</p>
        <span className="cell-sub">{t("dns.bl.last")}: {d?.checked_at ? formatDate(d.checked_at, i18n.language) : t("common.never")}</span>
        <button className="btn" type="button" disabled={run.isPending} onClick={() => run.mutate()}><RefreshCw /> {t("dns.bl.check_now")}</button>
      </div>
      <ErrorText error={run.error ?? q.error} />
      {q.isLoading || run.isPending ? <Loading /> : d && (
        <>
          {d.listed.map((x) => (
            <div key={x.ip + x.zone} className="callout warn" role="alert">
              <Ban />
              <div className="grow">
                <b>{t("dns.bl.listed_on", { ip: x.ip, name: x.name })}</b>
                <div className="cell-sub">{t("dns.bl.since", { time: formatDate(x.since, i18n.language) })}{x.reason ? ` · ${x.reason}` : ""}</div>
              </div>
              <a className="btn small" href={x.delist} target="_blank" rel="noreferrer"><ExternalLink /> {t("dns.bl.delist")}</a>
            </div>
          ))}
          {!d.addresses.length ? <div className="empty">{t("dns.bl.none_yet")}</div> : (
            <div className="tbl-wrap">
              <table>
                <thead><tr><th>{t("dns.bl.list")}</th>{d.addresses.map((a) => <th key={a.ip} className="mono" style={{ textTransform: "none" }}>{a.ip}</th>)}</tr></thead>
                <tbody>
                  {zones.map((z, n) => (
                    <tr key={z.zone}>
                      <td><span className="cell-main">{z.name}</span><div className="cell-sub mono">{z.zone}</div></td>
                      {d.addresses.map((a) => <td key={a.ip}><Pill s={a.results[n].status} /></td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {d.unusable.length > 0 && <div className="callout"><Info /><span>{t("dns.bl.unusable", { lists: d.unusable.join(", ") })}</span></div>}
        </>
      )}
    </div>
  );
}

// --- Cloudflare --------------------------------------------------------------------------------

function CloudflareTab() {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const server = useServer();
  const q = useQuery({ queryKey: ["dns-cf"], queryFn: () => api.get<CfStatus>("/dns/cloudflare") });
  const [token, setToken] = useState("");
  const [publishServer, setPublishServer] = useState(false);
  const save = useMutation({
    mutationFn: () => api.put<CfStatus>("/dns/cloudflare", { token }),
    onSuccess: (d) => { qc.setQueryData(["dns-cf"], d); setToken(""); toast(t("common.saved")); },
  });
  const remove = useMutation({ mutationFn: () => api.del("/dns/cloudflare"), onSuccess: () => qc.invalidateQueries({ queryKey: ["dns-cf"] }) });
  const d = q.data;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
        <Cloud size={20} aria-hidden />
        <b>{t("dns.cf.title")}</b>
        {d && (d.configured ? <Pill s={d.status === "active" ? "ok" : "bad"} label={t(`dns.cf.st_${d.status === "active" ? "ok" : d.status ?? "unreachable"}`, { defaultValue: d.status ?? "" })} /> : <Pill s="missing" label={t("dns.cf.not_set")} />)}
      </div>
      <p className="muted" style={{ margin: 0 }}>{t("dns.cf.help")}</p>
      {d?.configured && d.zones.length > 0 && (
        <div><div className="cell-sub" style={{ fontWeight: 650, marginBottom: 6 }}>{t("dns.cf.zones")}</div>{d.zones.map((z) => <span key={z} className="tag" style={{ marginRight: 4 }}>{z}</span>)}</div>
      )}
      <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
        <Field label={d?.configured ? t("dns.cf.replace") : t("dns.cf.token")} hint={t("dns.cf.token_hint")}>
          <input className="input mono" type="password" value={token} onChange={(e) => setToken(e.target.value)} autoComplete="off" />
        </Field>
        <ErrorText error={save.error} />
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn primary" type="submit" disabled={token.trim().length < 20 || save.isPending}>{t("dns.cf.save")}</button>
          {d?.configured && <button className="btn danger" type="button" onClick={() => window.confirm(t("dns.cf.remove_confirm")) && remove.mutate()}>{t("dns.cf.remove")}</button>}
        </div>
      </form>
      {d?.configured && server.data && (
        <div style={{ borderTop: "1px solid var(--line)", paddingTop: 14, display: "flex", flexDirection: "column", gap: 8 }}>
          <b>{t("dns.cf.server_spf")}</b>
          <span className="cell-sub">{t("dns.cf.server_spf_help", { name: server.data.spf_name })}</span>
          <div><button className="btn" type="button" onClick={() => setPublishServer(true)}><Cloud /> {t("dns.publish")}</button></div>
          {publishServer && <PublishModal kind="server_spf" domain={server.data.spf_name} onClose={() => setPublishServer(false)} />}
        </div>
      )}
    </div>
  );
}

// --- change log ------------------------------------------------------------------------------------

function JournalTab() {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const q = useQuery({ queryKey: ["dns-journal"], queryFn: () => api.get<Journal>("/dns/journal?limit=100") });
  const undo = useMutation({ mutationFn: (b: string) => api.post(`/dns/undo/${b}`), onSuccess: () => { toast(t("dns.log.undone")); q.refetch(); } });
  const undone = new Set((q.data ?? []).map((e) => e.undo).filter(Boolean));
  const seen = new Set<string>();
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <p className="muted" style={{ margin: 0 }}>{t("dns.log.help")}</p>
      <ErrorText error={undo.error} />
      {q.isLoading ? <Loading /> : !q.data?.length ? <div className="empty">{t("dns.log.empty")}</div> : (
        <div className="tbl-wrap">
          <table>
            <thead><tr><th>{t("dns.log.when")}</th><th>{t("dns.log.what")}</th><th>{t("dns.current")}</th><th>{t("dns.new")}</th><th /></tr></thead>
            <tbody>
              {q.data.map((e, n) => {
                const first = !seen.has(e.batch);
                seen.add(e.batch);
                return (
                  <tr key={n}>
                    <td className="nowrap"><div>{formatDate(e.time, i18n.language)}</div><div className="cell-sub">{e.actor}</div></td>
                    <td><span className="tag">{t(`dns.action.${e.action}`)}</span> <span className="mono" style={{ fontSize: 12 }}>{(e.after ?? e.before)?.type} {(e.after ?? e.before)?.name}</span>{e.undo && <div className="cell-sub">{t("dns.log.is_undo")}</div>}</td>
                    <td className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>{recText(e.before)}</td>
                    <td className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}>{recText(e.after)}</td>
                    <td className="nowrap">
                      {first && !e.undo && e.reason !== "golive_mx" && (undone.has(e.batch) ? <span className="muted">{t("dns.log.undone")}</span> : (
                        <button className="btn small" type="button" disabled={undo.isPending} onClick={() => window.confirm(t("dns.log.undo_confirm")) && undo.mutate(e.batch)}><Undo2 /> {t("dns.log.undo")}</button>
                      ))}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
