// Cloud mail: put a Microsoft 365 or Google Workspace tenant behind Mailfilter.
// One wizard per domain. Mailfilter sets up its own side in one click; for the
// provider's side it gives a PowerShell script (Microsoft) or click-by-click
// steps (Google), and checks what it can: DNS, and mail seen in the trace.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Cloud, Download, Info, Link2, Plus, RefreshCw, Search, Send, ShieldCheck, Trash2, Undo2, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { DataTable, type Column } from "../components/DataTable";
import GatewayError from "../components/GatewayError";
import { ErrorText, Field, Loading, Modal, PageHead, formatDate, useToast } from "../components/ui";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";
import { Code, CopyBtn, Pill, PublishModal } from "./Dns";

const enc = encodeURIComponent;

type Provider = "m365" | "google";
type StepState = "done" | "todo" | "waiting";
type Step = { id: string; state: StepState; manual: boolean; confirmed_at: string | null; confirmed_by: string | null };
type Summary = { done: number; total: number; next: string | null };
type TenantRow = { domain: string; provider: Provider; tenant_id: string; enabled: boolean; summary: Summary; customers: string[] };
type Detail = {
  domain: string; provider: Provider; tenant_id: string; enabled: boolean; destinations: string[];
  steps: Step[]; summary: Summary;
  evidence: { inbound_seen: string | null; outbound_seen: string | null; forged_7d: number; mx_since: string | null; lockdown_ready: boolean };
  dns: { mx: string; spf: string; mx_value: string; spf_value: string; spf_fix: string | null; dkim: string | null } | null;
  server: { hostname: string; v4: string[]; v6: string[]; spf_include: string };
};
type Detect = {
  domain: string; mx: string[]; provider: Provider | null; tenant_id: string | null; destinations: string[];
  exists: boolean; configured: boolean; current_destinations: string[]; linked: boolean; customer_ids: number[];
};
type UserRow = { id: number; email: string; name: string; role: string };

export default function CloudMail({ me }: { me: Me }) {
  const { t } = useTranslation();
  const admin = me.role === "admin";
  const [params, setParams] = useSearchParams();
  const open = params.get("open");
  const [adding, setAdding] = useState(false);
  const list = useQuery({ queryKey: ["cloud"], queryFn: () => api.get<TenantRow[]>("/cloud") });
  const setOpen = (d: string | null) => setParams(d ? { open: d } : {}, { replace: true });

  const columns: Column<TenantRow>[] = [
    { key: "domain", label: t("cloud.domain"), render: (r) => <b>{r.domain}</b>, sort: (r) => r.domain, always: true, mobile: "title" },
    { key: "provider", label: t("cloud.provider"), render: (r) => t(`cloud.p.${r.provider}`), sort: (r) => r.provider },
    ...(admin ? [{ key: "customers", label: t("cloud.customer"), render: (r: TenantRow) => <span className="muted">{r.customers.join(", ") || "–"}</span>, text: (r: TenantRow) => r.customers.join(" "), optional: true }] : []),
    { key: "progress", label: t("cloud.progress"), render: (r) => <Progress s={r.summary} enabled={r.enabled} />, sort: (r) => r.summary.done, mobile: "aside" },
  ];

  return (
    <div className="page">
      <PageHead title={t("cloud.title")} sub={t("cloud.sub")}
        actions={admin && <button className="btn primary" type="button" onClick={() => setAdding(true)}><Plus /> {t("cloud.add")}</button>} />
      <DataTable id="cloud" rows={list.data} rowKey={(r) => r.domain} columns={columns} loading={list.isLoading}
        onRowClick={(r) => setOpen(r.domain)} rowClass={(r) => (r.domain === open ? "selected" : undefined)}
        empty={<div style={{ padding: 24, textAlign: "center" }}><Cloud /><p className="muted">{t(admin ? "cloud.empty_admin" : "cloud.empty")}</p></div>} />
      {open && <Wizard key={open} domain={open} admin={admin} onClose={() => setOpen(null)} />}
      {adding && <AddModal onClose={() => setAdding(false)} onDone={(d) => { setAdding(false); setOpen(d); }} />}
    </div>
  );
}

function Progress({ s, enabled }: { s: Summary; enabled: boolean }) {
  const { t } = useTranslation();
  const done = s.done >= s.total;
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, minWidth: 200 }}>
      <div className="wiz-bar" aria-hidden><span style={{ width: `${(100 * s.done) / s.total}%` }} className={done ? "ok" : ""} /></div>
      <span className={`status ${!enabled ? "s-mute" : done ? "s-ok" : "s-warn"}`}>
        {!enabled ? t("cloud.paused") : done ? t("cloud.all_done") : t("cloud.n_of", { n: s.done, total: s.total })}
      </span>
    </div>
  );
}

// ---- link a new tenant ---------------------------------------------------------------------

function AddModal({ onClose, onDone }: { onClose: () => void; onDone: (domain: string) => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [domain, setDomain] = useState("");
  const [found, setFound] = useState<Detect | null>(null);
  const [provider, setProvider] = useState<Provider>("m365");
  const [tenant, setTenant] = useState("");
  const [dests, setDests] = useState("");
  const [replace, setReplace] = useState(true);
  const [customer, setCustomer] = useState<string>("");
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<UserRow[]>("/users") });
  const detect = useMutation({
    mutationFn: (d: string) => api.get<Detect>(`/cloud/detect/${enc(d)}`),
    onSuccess: (r) => {
      setFound(r);
      setProvider(r.provider ?? "m365");
      setTenant(r.tenant_id ?? "");
      setDests(r.destinations.join("\n"));
      setCustomer(r.customer_ids[0] ? String(r.customer_ids[0]) : "");
    },
  });
  const create = useMutation({
    mutationFn: () => api.post("/cloud", {
      domain: found!.domain, provider, tenant_id: provider === "m365" ? tenant.trim() : "",
      destinations: dests.split(/\s+/).filter(Boolean), replace_destinations: replace,
      customer_id: customer ? Number(customer) : null,
    }),
    onSuccess: () => { toast(t("cloud.linked"), "success"); qc.invalidateQueries({ queryKey: ["cloud"] }); qc.invalidateQueries({ queryKey: ["gateway"] }); onDone(found!.domain); },
  });
  return (
    <Modal title={t("cloud.add")} onClose={onClose} icon={<Cloud />}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <form style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}
          onSubmit={(e) => { e.preventDefault(); if (domain.trim()) detect.mutate(domain.trim().toLowerCase()); }}>
          <div style={{ flex: "1 1 220px" }}>
            <Field label={t("cloud.domain")}>
              <input className="input" autoFocus required placeholder="example.nl" value={domain} onChange={(e) => { setDomain(e.target.value); setFound(null); }} spellCheck={false} />
            </Field>
          </div>
          <button className="btn dark" type="submit" disabled={detect.isPending}><Search /> {t("cloud.detect")}</button>
        </form>
        <ErrorText error={detect.error} />
        {detect.isPending && <Loading />}
        {found && (
          <>
            <div className={`callout${found.provider ? "" : " warn"}`}>
              <Info />
              <span>
                {found.provider ? t("cloud.detected", { provider: t(`cloud.p.${found.provider}`) }) : t("cloud.not_detected")}
                {found.mx.length > 0 && <span className="cell-sub mono" style={{ display: "block" }}>MX: {found.mx.join(", ")}</span>}
              </span>
            </div>
            {found.linked ? <div className="callout warn"><Info /><span>{t("cloud.already_linked")}</span></div> : (
              <form style={{ display: "flex", flexDirection: "column", gap: 12 }} onSubmit={(e) => { e.preventDefault(); create.mutate(); }}>
                <div role="radiogroup" aria-label={t("cloud.provider")} style={{ display: "grid", gap: 10, gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))" }}>
                  {(["m365", "google"] as Provider[]).map((p) => (
                    <label key={p} className={`kind-choice${provider === p ? " on" : ""}`}>
                      <input type="radio" name="mf-provider" checked={provider === p}
                        onChange={() => { setProvider(p); if (p === "google") setDests("aspmx.l.google.com\nalt1.aspmx.l.google.com\nalt2.aspmx.l.google.com\nalt3.aspmx.l.google.com\nalt4.aspmx.l.google.com"); else setDests(found.destinations.join("\n")); }} />
                      <span><b>{t(`cloud.p.${p}`)}</b><br /><span className="muted">{t(`cloud.p_hint.${p}`)}</span></span>
                    </label>
                  ))}
                </div>
                {provider === "m365" && (
                  <Field label={t("cloud.tenant_id")} hint={found.tenant_id ? t("cloud.tenant_found") : t("cloud.tenant_hint")}>
                    <input className="input mono" required pattern="[0-9a-fA-F-]{36}" value={tenant} onChange={(e) => setTenant(e.target.value)} placeholder="00000000-0000-0000-0000-000000000000" spellCheck={false} />
                  </Field>
                )}
                <Field label={t("cloud.destinations")} hint={t("cloud.destinations_hint")}>
                  <textarea className="input mono" rows={provider === "google" ? 5 : 2} value={dests} onChange={(e) => setDests(e.target.value)} spellCheck={false} />
                </Field>
                {found.configured && (
                  <label className="check">
                    <input type="checkbox" checked={replace} onChange={(e) => setReplace(e.target.checked)} />
                    <span>{t("cloud.replace", { current: found.current_destinations.join(", ") })}</span>
                  </label>
                )}
                <Field label={t("cloud.customer")} hint={t("cloud.customer_hint")}>
                  <select className="input" value={customer} onChange={(e) => setCustomer(e.target.value)}>
                    <option value="">{t("cloud.no_customer")}</option>
                    {users.data?.filter((u) => u.role === "customer").map((u) => <option key={u.id} value={u.id}>{u.name ? `${u.name} (${u.email})` : u.email}</option>)}
                  </select>
                </Field>
                <p className="cell-sub" style={{ margin: 0 }}>{t("cloud.setup_explain", { provider: t(`cloud.p.${provider}`) })}</p>
                <GatewayError error={create.error} />
                <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
                  <button type="button" className="btn" onClick={onClose}>{t("common.cancel")}</button>
                  <button className="btn primary" disabled={create.isPending}><Link2 /> {t("cloud.link_now")}</button>
                </div>
              </form>
            )}
          </>
        )}
      </div>
    </Modal>
  );
}

// ---- the wizard ---------------------------------------------------------------------------

function Wizard({ domain, admin, onClose }: { domain: string; admin: boolean; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["cloud", domain], queryFn: () => api.get<Detail>(`/cloud/${enc(domain)}`), retry: false });
  const [active, setActive] = useState<string | null>(null);
  const d = q.data;
  useEffect(() => { if (d && !active) setActive(d.summary.next ?? d.steps[d.steps.length - 1].id); }, [d, active]);
  // The list shows the progress the last check stored: refresh it after each check.
  const done = d?.summary.done;
  useEffect(() => { if (done !== undefined) qc.invalidateQueries({ queryKey: ["cloud"], exact: true }); }, [done, qc]);
  const confirm = useMutation({
    mutationFn: ({ step, done }: { step: string; done: boolean }) => api.post<Detail>(`/cloud/${enc(domain)}/steps/${step}`, { done }),
    onSuccess: (r, v) => {
      qc.setQueryData(["cloud", domain], r);
      qc.invalidateQueries({ queryKey: ["cloud"], exact: true });
      if (v.done) {
        const i = r.steps.findIndex((s) => s.id === v.step);
        if (r.steps[i + 1]) setActive(r.steps[i + 1].id);
      }
    },
  });
  if (q.isLoading) return <section className="card"><div className="card-body"><Loading /></div></section>;
  if (q.error || !d) return <section className="card"><div className="card-body"><ErrorText error={q.error} /></div></section>;
  const idx = Math.max(0, d.steps.findIndex((s) => s.id === active));
  const step = d.steps[idx];
  return (
    <section className="wiz" aria-label={t("cloud.wizard_for", { domain })}>
      <ol className="wiz-steps card">
        <li className="wiz-head">
          <span>{domain} · {t(`cloud.p.${d.provider}`)}</span>
          <button type="button" className="btn small ghost" onClick={onClose} aria-label={t("common.close")}><X /></button>
        </li>
        {d.steps.map((s, i) => (
          <li key={s.id}>
            <button type="button" className={`wiz-step${s.id === step.id ? " on" : ""}`} aria-current={s.id === step.id ? "step" : undefined} onClick={() => setActive(s.id)}>
              <span className={`wiz-num ${s.state}`} aria-hidden>{s.state === "done" ? <Check /> : i + 1}</span>
              <span>
                <b>{t(`cloud.s.${s.id}.title`)}</b>
                <span className="cell-sub" style={{ display: "block" }}>{t(`cloud.state.${s.state}`)}</span>
              </span>
            </button>
          </li>
        ))}
      </ol>
      <article className="card wiz-body">
        <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div>
            <div className="cell-sub">{t("cloud.step_n", { n: idx + 1, total: d.steps.length })}</div>
            <h2 style={{ margin: "2px 0 0", fontSize: 20 }}>{t(`cloud.s.${step.id}.title`)}</h2>
            <p className="muted" style={{ margin: "6px 0 0" }}>{t(`cloud.s.${step.id}.intro`, { host: d.server.hostname, domain })}</p>
          </div>
          <StepBody d={d} step={step} admin={admin} onChanged={() => q.refetch()} />
          {d.evidence.forged_7d > 0 && (step.id === "outbound_connector" || step.id === "outbound_gateway") && (
            <div className="callout warn"><Info /><span>{t("cloud.forged", { count: d.evidence.forged_7d })}</span></div>
          )}
          <ErrorText error={confirm.error} />
          <div className="wiz-foot">
            <button type="button" className="btn" disabled={idx === 0} onClick={() => setActive(d.steps[idx - 1].id)}>{t("cloud.prev")}</button>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button type="button" className="btn" onClick={() => q.refetch()} disabled={q.isFetching}><RefreshCw /> {t("cloud.check_again")}</button>
              {step.manual && step.confirmed_at && (
                <button type="button" className="btn ghost" onClick={() => confirm.mutate({ step: step.id, done: false })}><Undo2 /> {t("cloud.not_done")}</button>
              )}
              {step.manual && !step.confirmed_at ? (
                <button type="button" className="btn primary" disabled={confirm.isPending} onClick={() => confirm.mutate({ step: step.id, done: true })}><Check /> {t("cloud.done_next")}</button>
              ) : idx < d.steps.length - 1 && (
                <button type="button" className="btn primary" onClick={() => setActive(d.steps[idx + 1].id)}>{t("cloud.next")}</button>
              )}
            </div>
          </div>
          {step.confirmed_at && step.confirmed_by && (
            <p className="cell-sub" style={{ margin: 0 }}>{t("cloud.confirmed_by", { who: step.confirmed_by, when: formatDate(step.confirmed_at, i18n.language) })}</p>
          )}
        </div>
      </article>
    </section>
  );
}

function Check2({ ok, children }: { ok: boolean | null; children: ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
      <span className={`status ${ok ? "s-ok" : ok === null ? "s-mute" : "s-warn"}`} style={{ padding: 2 }}>{ok ? <Check /> : <Info />}</span>
      <span>{children}</span>
    </div>
  );
}

function Ol({ prefix, count, vars }: { prefix: string; count: number; vars?: Record<string, string> }) {
  const { t } = useTranslation();
  return <ol className="wiz-list">{Array.from({ length: count }, (_, i) => <li key={i}>{t(`${prefix}.${i + 1}`, vars)}</li>)}</ol>;
}

function Script({ domain, part }: { domain: string; part: "setup" | "lockdown" | "undo" }) {
  const { t } = useTranslation();
  const q = useQuery({
    queryKey: ["cloud-script", domain, part],
    queryFn: async () => {
      const r = await fetch(`/api/cloud/${enc(domain)}/script?part=${part}`, { credentials: "same-origin" });
      if (!r.ok) throw new Error(String(r.status));
      return r.text();
    },
  });
  if (!q.data) return q.isLoading ? <Loading /> : <ErrorText error={q.error} />;
  const download = () => {
    const url = URL.createObjectURL(new Blob([q.data!], { type: "text/plain" }));
    const a = Object.assign(document.createElement("a"), { href: url, download: `mailfilter-${part}-${domain}.ps1` });
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <pre className="wiz-code">{q.data}</pre>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <CopyBtn text={q.data} />
        <button type="button" className="btn small ghost" onClick={download}><Download /> {t("cloud.download")}</button>
      </div>
    </div>
  );
}

function Ways({ powershell, clicks }: { powershell: ReactNode; clicks: ReactNode }) {
  const { t } = useTranslation();
  const [way, setWay] = useState<"ps" | "eac">("ps");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div className="seg" role="tablist" aria-label={t("cloud.way")}>
        <button type="button" role="tab" aria-pressed={way === "ps"} aria-selected={way === "ps"} onClick={() => setWay("ps")}>{t("cloud.way_ps")}</button>
        <button type="button" role="tab" aria-pressed={way === "eac"} aria-selected={way === "eac"} onClick={() => setWay("eac")}>{t("cloud.way_eac")}</button>
      </div>
      {way === "ps" ? powershell : clicks}
    </div>
  );
}

function StepBody({ d, step, admin, onChanged }: { d: Detail; step: Step; admin: boolean; onChanged: () => void }) {
  const { t, i18n } = useTranslation();
  const host = d.server.hostname;
  const vars = { host, domain: d.domain, ips: d.server.v4.join(", "), ips6: d.server.v6.join(", ") };
  const seen = (iso: string | null, key: string) => (
    <Check2 ok={!!iso}>{iso ? t(`cloud.${key}_seen`, { when: formatDate(iso, i18n.language) }) : t(`cloud.${key}_not_seen`)}</Check2>
  );
  switch (step.id) {
    case "detect":
      return <DetectStep d={d} admin={admin} onChanged={onChanged} />;
    case "mailfilter":
      return <MailfilterStep d={d} admin={admin} onChanged={onChanged} />;
    case "inbound_connector":
      return (
        <>
          <Ways powershell={<><p className="cell-sub" style={{ margin: 0 }}>{t("cloud.script_covers")}</p><Script domain={d.domain} part="setup" /></>}
            clicks={<Ol prefix="cloud.eac.inbound" count={6} vars={vars} />} />
          {seen(d.evidence.inbound_seen, "inbound")}
        </>
      );
    case "enhanced_filtering":
      return <Ways powershell={<p style={{ margin: 0 }}>{t("cloud.in_script")}</p>} clicks={<Ol prefix="cloud.eac.ef" count={4} vars={vars} />} />;
    case "outbound_connector":
      return (
        <>
          <Ways powershell={<p style={{ margin: 0 }}>{t("cloud.in_script")}</p>} clicks={<Ol prefix="cloud.eac.outbound" count={6} vars={vars} />} />
          {seen(d.evidence.outbound_seen, "outbound")}
        </>
      );
    case "dkim":
      return (
        <>
          <Ol prefix="cloud.g.dkim" count={4} vars={vars} />
          <Check2 ok={d.dns?.dkim === "ok" ? true : d.dns?.dkim === "missing" ? false : null}>{t(`cloud.dkim_${d.dns?.dkim ?? "error"}`, { domain: d.domain })}</Check2>
        </>
      );
    case "inbound_gateway":
      return (
        <>
          <Ol prefix="cloud.g.inbound" count={5} vars={vars} />
          <Code>{[...d.server.v4, ...d.server.v6].join("\n") || "–"}</Code>
          <div><CopyBtn text={[...d.server.v4, ...d.server.v6].join("\n")} /></div>
          {seen(d.evidence.inbound_seen, "inbound")}
        </>
      );
    case "outbound_gateway":
      return (
        <>
          <Ol prefix="cloud.g.outbound" count={4} vars={vars} />
          <div style={{ display: "flex", gap: 8, alignItems: "center" }}><Code>{host}</Code><CopyBtn text={host} /></div>
          {seen(d.evidence.outbound_seen, "outbound")}
        </>
      );
    case "test":
      return <TestStep d={d} />;
    case "dns":
      return <DnsStep d={d} admin={admin} onChanged={onChanged} />;
    case "lockdown":
      return <LockdownStep d={d} />;
  }
  return null;
}

function DetectStep({ d, admin, onChanged }: { d: Detail; admin: boolean; onChanged: () => void }) {
  const { t } = useTranslation();
  const [tenant, setTenant] = useState(d.tenant_id);
  const save = useMutation({ mutationFn: () => api.put(`/cloud/${enc(d.domain)}`, { tenant_id: tenant.trim() }), onSuccess: onChanged });
  return (
    <>
      <dl className="kv">
        <dt>{t("cloud.provider")}</dt><dd>{t(`cloud.p.${d.provider}`)}</dd>
        {d.provider === "m365" && <><dt>{t("cloud.tenant_id")}</dt><dd className="mono">{d.tenant_id || "–"}</dd></>}
      </dl>
      {admin && d.provider === "m365" && (
        <form style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
          <div style={{ flex: "1 1 320px" }}><Field label={t("cloud.change_tenant")}><input className="input mono" value={tenant} onChange={(e) => setTenant(e.target.value)} spellCheck={false} /></Field></div>
          <button className="btn" disabled={save.isPending || tenant.trim() === d.tenant_id}>{t("common.save")}</button>
        </form>
      )}
      <GatewayError error={save.error} />
    </>
  );
}

function MailfilterStep({ d, admin, onChanged }: { d: Detail; admin: boolean; onChanged: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const toggle = useMutation({ mutationFn: () => api.put(`/cloud/${enc(d.domain)}`, { enabled: !d.enabled }), onSuccess: () => { onChanged(); qc.invalidateQueries({ queryKey: ["cloud"], exact: true }); } });
  const unlink = useMutation({
    mutationFn: () => api.del(`/cloud/${enc(d.domain)}`),
    onSuccess: () => { toast(t("cloud.unlinked")); qc.invalidateQueries({ queryKey: ["cloud"] }); },
  });
  return (
    <>
      <Check2 ok={d.destinations.length > 0}>{t("cloud.mf.route", { hosts: d.destinations.join(", ") || "–" })} <Link to={`/domains?open=${enc(d.domain)}`}>{t("cloud.mf.open_domain")}</Link></Check2>
      <Check2 ok={d.enabled}>{d.enabled ? t(`cloud.mf.relay_on.${d.provider}`, { domain: d.domain }) : t("cloud.mf.relay_off")}</Check2>
      <div className="callout"><ShieldCheck /><span>{t(`cloud.mf.safe.${d.provider}`)}</span></div>
      {admin && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button type="button" className="btn" disabled={toggle.isPending} onClick={() => toggle.mutate()}>{d.enabled ? t("cloud.mf.pause") : t("cloud.mf.resume")}</button>
          <button type="button" className="btn danger" disabled={unlink.isPending} onClick={() => window.confirm(t("cloud.mf.unlink_confirm", { domain: d.domain })) && unlink.mutate()}><Trash2 /> {t("cloud.mf.unlink")}</button>
        </div>
      )}
      <GatewayError error={toggle.error ?? unlink.error} />
    </>
  );
}

function TestStep({ d }: { d: Detail }) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const [to, setTo] = useState(`postmaster@${d.domain}`);
  const send = useMutation({ mutationFn: () => api.post(`/cloud/${enc(d.domain)}/test`, { to }), onSuccess: () => toast(t("cloud.test_sent"), "success") });
  return (
    <>
      <form style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }} onSubmit={(e) => { e.preventDefault(); send.mutate(); }}>
        <div style={{ flex: "1 1 280px" }}><Field label={t("cloud.test_to")}><input className="input" type="email" value={to} onChange={(e) => setTo(e.target.value)} /></Field></div>
        <button className="btn dark" disabled={send.isPending}><Send /> {t("cloud.test_send")}</button>
      </form>
      <ErrorText error={send.error} />
      <Check2 ok={!!d.evidence.inbound_seen}>{d.evidence.inbound_seen ? t("cloud.inbound_seen", { when: formatDate(d.evidence.inbound_seen, i18n.language) }) : t("cloud.inbound_not_seen")}</Check2>
      <Check2 ok={!!d.evidence.outbound_seen}>{d.evidence.outbound_seen ? t("cloud.outbound_seen", { when: formatDate(d.evidence.outbound_seen, i18n.language) }) : t("cloud.outbound_not_seen")}</Check2>
      <p className="cell-sub" style={{ margin: 0 }}>{t("cloud.test_trace")} <Link to={`/trace?q=${enc(d.domain)}`}>{t("nav.trace")}</Link></p>
    </>
  );
}

function DnsStep({ d, admin, onChanged }: { d: Detail; admin: boolean; onChanged: () => void }) {
  const { t } = useTranslation();
  const [publish, setPublish] = useState<"mx" | "spf_value" | null>(null);
  const cf = useQuery({ queryKey: ["dns-cloudflare"], queryFn: () => api.get<{ configured: boolean }>("/dns/cloudflare"), enabled: admin });
  const dns = d.dns;
  const spfWant = dns?.spf_fix ?? dns?.spf_value ?? "";
  return (
    <>
      <div className="dns-rec">
        <div className="sec-head"><b>MX</b><Pill s={dns?.mx ?? "error"} /></div>
        <p className="cell-sub" style={{ margin: "4px 0" }}>{t("cloud.dns.mx_now")}</p>
        <Code>{dns?.mx_value || "–"}</Code>
        <p className="cell-sub" style={{ margin: "8px 0 4px" }}>{t("cloud.dns.mx_want")}</p>
        <Code>{`${d.domain}. MX 10 ${d.server.hostname}.`}</Code>
        {admin && cf.data?.configured && dns?.mx !== "ok" && (
          <div style={{ marginTop: 8 }}><button type="button" className="btn small primary" onClick={() => setPublish("mx")}><Cloud /> {t("cloud.dns.publish_mx")}</button></div>
        )}
      </div>
      <div className="dns-rec">
        <div className="sec-head"><b>SPF</b><Pill s={dns?.spf ?? "error"} /></div>
        <p className="cell-sub" style={{ margin: "4px 0" }}>{t(`cloud.dns.spf_want.${d.provider}`, { include: d.server.spf_include })}</p>
        {spfWant && <Code>{spfWant}</Code>}
        {admin && cf.data?.configured && dns?.spf !== "ok" && dns?.spf_fix && (
          <div style={{ marginTop: 8 }}><button type="button" className="btn small primary" onClick={() => setPublish("spf_value")}><Cloud /> {t("cloud.dns.publish_spf")}</button></div>
        )}
      </div>
      {admin && cf.data && !cf.data.configured && <div className="callout"><Info /><span>{t("cloud.dns.no_cloudflare")}</span></div>}
      <div className="callout"><Info /><span>{t(`cloud.dns.keep.${d.provider}`)}</span></div>
      {publish === "mx" && <PublishModal kind="mx" domain={d.domain} mx={[{ priority: 10, host: d.server.hostname }]} onClose={() => setPublish(null)} onDone={onChanged} />}
      {publish === "spf_value" && <PublishModal kind="spf_value" domain={d.domain} value={dns?.spf_fix ?? ""} onClose={() => setPublish(null)} onDone={onChanged} />}
    </>
  );
}

function LockdownStep({ d }: { d: Detail }) {
  const { t, i18n } = useTranslation();
  const since = d.evidence.mx_since;
  return (
    <>
      <Check2 ok={d.evidence.lockdown_ready}>
        {since ? (d.evidence.lockdown_ready ? t("cloud.lock.ready", { when: formatDate(since, i18n.language) }) : t("cloud.lock.wait", { when: formatDate(since, i18n.language) }))
          : t("cloud.lock.mx_first")}
      </Check2>
      {d.provider === "m365" ? (
        <Ways powershell={<Script domain={d.domain} part="lockdown" />} clicks={<Ol prefix="cloud.eac.lock" count={4} vars={{ host: d.server.hostname, domain: d.domain }} />} />
      ) : <Ol prefix="cloud.g.lock" count={3} vars={{ host: d.server.hostname, domain: d.domain }} />}
      <div className="callout warn"><Info /><span>{t(`cloud.lock.note.${d.provider}`)}</span></div>
    </>
  );
}
