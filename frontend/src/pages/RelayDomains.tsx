// Relay domains: outbound only. Mailfilter accepts no mail for them and has no
// mail server to deliver to; it signs their outgoing mail with DKIM and shows
// the SPF, DKIM and DMARC records they need (published through the DNS toolbox).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, Check, Cloud, Copy, Inbox, KeyRound, Plus, RefreshCw, Send, Server, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import GatewayError from "../components/GatewayError";
import { Drawer, Field, Loading, Modal, useToast } from "../components/ui";
import { api } from "../lib/api";
import type { DomainKind, DomainRecords, DomainRow, Network, SmtpAccount, SmtpAccountCreated } from "../lib/gateway";
import { numOrNull, numStr } from "../lib/gateway";
import { Code, CopyBtn, Pill, Problems, PublishModal, type CfStatus, type CheckResult, type Item, type PublishKind } from "./Dns";

const enc = encodeURIComponent;

// ---- "Add domain": choose the kind first ----------------------------------------------

export function KindPicker({ initial = "transport", onPick, onClose }: { initial?: DomainKind; onPick: (name: string, kind: DomainKind) => void; onClose: () => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [kind, setKind] = useState<DomainKind>(initial);
  const choice = (k: DomainKind, icon: ReactNode) => (
    <label className={`kind-choice${kind === k ? " on" : ""}`}>
      <input type="radio" name="mf-kind" checked={kind === k} onChange={() => setKind(k)} />
      <span className="kind-icon" aria-hidden>{icon}</span>
      <span>
        <b>{t(`relaydom.kind_${k}`)}</b>
        <br />
        <span className="muted">{t(`relaydom.kind_${k}_hint`)}</span>
      </span>
    </label>
  );
  return (
    <Modal title={t("domains.add")} onClose={onClose}>
      <form style={{ display: "flex", flexDirection: "column", gap: 16 }}
        onSubmit={(e) => { e.preventDefault(); if (name.trim()) onPick(name.trim().toLowerCase(), kind); }}>
        <Field label={t("domains.name")}>
          <input className="input" required autoFocus placeholder="example.nl" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <div role="radiogroup" aria-label={t("relaydom.kind")} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {choice("transport", <Inbox />)}
          {choice("relay", <Send />)}
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          <button className="btn primary">{t("relaydom.next")}</button>
          <button type="button" className="btn ghost" onClick={onClose}>{t("common.cancel")}</button>
        </div>
      </form>
    </Modal>
  );
}

// ---- list ------------------------------------------------------------------------------

function useCheck(domain: string, enabled = true) {
  return useQuery({
    queryKey: ["dns-check", domain],
    queryFn: () => api.get<CheckResult>(`/dns/check/${enc(domain)}`),
    enabled, staleTime: 5 * 60_000, retry: false,
  });
}

function DnsCells({ domain }: { domain: string }) {
  const q = useCheck(domain);
  const item = (code: string) => q.data?.items.find((i) => i.code === code);
  return (
    <>
      {(["dkim", "spf", "dmarc"] as const).map((c) => (
        <td key={c} className="nowrap">
          {q.isLoading ? <span className="muted">…</span> : item(c) ? <Pill s={item(c)!.status} /> : <span className="muted">–</span>}
        </td>
      ))}
    </>
  );
}

export function RelayTable({ domains, admin, onOpen }: { domains: DomainRow[]; admin: boolean; onOpen: (d: DomainRow) => void }) {
  const { t } = useTranslation();
  if (!domains.length) return <div className="empty">{t("relaydom.empty")}</div>;
  return (
    <div className="tbl-wrap">
      <table>
        <thead>
          <tr>
            <th>{t("domains.name")}</th>
            <th>{t("relaydom.sends_via")}</th>
            {admin && <><th>DKIM</th><th>SPF</th><th>DMARC</th></>}
            <th>{t("domains.status")}</th>
          </tr>
        </thead>
        <tbody>
          {domains.map((d) => (
            <tr key={d.id} className="click" tabIndex={0} onClick={() => onOpen(d)} onKeyDown={(e) => e.key === "Enter" && onOpen(d)}>
              <td>
                <div className="cell-main">{d.name}</div>
                {d.users.length > 0 && <div className="cell-sub">{t("domains.users_count", { count: d.users.length })}</div>}
              </td>
              <td className="muted">
                {d.relay_networks + d.smtp_accounts === 0 ? t("relaydom.nobody") : (
                  <>
                    {d.relay_networks > 0 && <div>{t("relaydom.n_ips", { count: d.relay_networks })}</div>}
                    {d.smtp_accounts > 0 && <div>{t("relaydom.n_accounts", { count: d.smtp_accounts })}</div>}
                  </>
                )}
              </td>
              {admin && <DnsCells domain={d.name} />}
              <td className="nowrap">
                {d.enabled ? <span className="status s-ok">{t("domains.active")}</span> : <span className="status s-mute">{t("domains.paused")}</span>}
                {d.sender_lock && <div className="cell-sub">{t("relaydom.locked")}</div>}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- drawer ----------------------------------------------------------------------------

type Form = Pick<DomainRow, "name" | "sender_lock" | "enabled" | "out_per_hour" | "out_per_day" | "note">;

function toBody(d: DomainRow | null, f: Form, kind: DomainKind = "relay") {
  return {
    tls: "may", verify_recipients: true, concurrency: null, rate_delay: null, ...(d ?? {}), ...f,
    kind, destinations: kind === "relay" ? [] : d?.destinations ?? [],
  };
}

export function RelayDrawer({ domain: initial, name, readOnly, admin, onClose, onConvert }: {
  domain: DomainRow | null; name?: string; readOnly: boolean; admin: boolean; onClose: () => void; onConvert: (d: DomainRow) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  // After "Add" the drawer stays open on the new domain, so senders and DNS can be set up straight away.
  const [domain, setDomain] = useState<DomainRow | null>(initial);
  const [f, setF] = useState<Form>({
    name: initial?.name ?? name ?? "", sender_lock: initial ? initial.sender_lock : true, enabled: initial?.enabled ?? true,
    out_per_hour: initial?.out_per_hour ?? null, out_per_day: initial?.out_per_day ?? null, note: initial?.note ?? "",
  });
  const done = () => {
    qc.invalidateQueries({ queryKey: ["gw-domains"] });
    qc.invalidateQueries({ queryKey: ["gw-status"] });
    qc.invalidateQueries({ queryKey: ["domain-options"] });
  };
  const save = useMutation({
    mutationFn: () => (domain ? api.put<DomainRow>(`/gateway/domains/${domain.id}`, toBody(domain, f))
      : api.post<DomainRow>("/gateway/domains", toBody(null, f))),
    onSuccess: (d) => {
      done();
      toast(t("gateway.applied"));
      if (domain) onClose();
      else setDomain(d);
    },
  });
  const del = useMutation({ mutationFn: () => api.del(`/gateway/domains/${domain!.id}`), onSuccess: () => { done(); onClose(); } });
  const num = (k: "out_per_hour" | "out_per_day", label: string) => (
    <Field label={label} hint={t("domains.outbound_hint")}>
      <input className="input" type="number" min={1} max={10_000_000} disabled={readOnly} placeholder={t("domains.default")}
        value={numStr(f[k])} onChange={(e) => setF({ ...f, [k]: numOrNull(e.target.value) })} />
    </Field>
  );
  return (
    <Drawer title={domain ? domain.name : t("relaydom.add_title")} sub={<><span className="tag lucht">{t("relaydom.kind_relay")}</span> {t("relaydom.outbound_only")}</>} onClose={onClose}>
      {initial && initial.destinations.length > 0 ? (
        <div className="callout warn"><ArrowLeftRight aria-hidden /><span>{t("relaydom.converting_warn", { name: initial.name })}</span></div>
      ) : <div className="callout"><Send aria-hidden /><span>{t("relaydom.explain")}</span></div>}
      <form className="form-grid" onSubmit={(e) => { e.preventDefault(); if (!readOnly) save.mutate(); }}>
        <div className="full"><GatewayError error={save.error || del.error} /></div>
        {!domain && (
          <Field label={t("domains.name")} full>
            <input className="input" required autoFocus placeholder="example.nl" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          </Field>
        )}
        <label className="check full">
          <input type="checkbox" disabled={readOnly} checked={f.sender_lock} onChange={(e) => setF({ ...f, sender_lock: e.target.checked })} />
          <span>
            {t("relaydom.lock", { domain: f.name || "example.nl" })}
            <br /><span className="hint">{t("relaydom.lock_hint")}</span>
          </span>
        </label>
        <div className="full"><h3 className="sec-title">{t("domains.outbound_title")}</h3></div>
        {num("out_per_hour", t("domains.per_hour"))}
        {num("out_per_day", t("domains.per_day"))}
        <Field label={t("domains.note")} full>
          <textarea className="input" rows={2} disabled={readOnly} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
        </Field>
        <label className="check full">
          <input type="checkbox" disabled={readOnly} checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />
          <span>{t("relaydom.enabled")}</span>
        </label>
        {!readOnly && (
          <div className="full" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn primary" disabled={save.isPending}>
              {save.isPending ? t("gateway.applying") : domain ? t("common.save") : t("relaydom.create")}
            </button>
            <button type="button" className="btn ghost" onClick={onClose}>{domain ? t("common.close") : t("common.cancel")}</button>
            {domain && admin && (
              <>
                <button type="button" className="btn ghost small" style={{ marginLeft: "auto" }} onClick={() => onConvert(domain)}>
                  <ArrowLeftRight /> {t("relaydom.to_transport")}
                </button>
                <button type="button" className="btn danger" onClick={() => window.confirm(t("relaydom.delete_confirm", { name: domain.name })) && del.mutate()}>
                  {t("common.delete")}
                </button>
              </>
            )}
          </div>
        )}
      </form>
      {domain && <Senders domain={domain.name} />}
      {domain && (admin ? <DnsRecords domain={domain} /> : <RecordsForCustomer domain={domain} />)}
    </Drawer>
  );
}

// ---- who may send: relay IPs and SMTP accounts --------------------------------------------

export function Senders({ domain }: { domain: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const nets = useQuery({ queryKey: ["gw-nets"], queryFn: () => api.get<Network[]>("/gateway/relay-networks") });
  const accounts = useQuery({ queryKey: ["gw-accounts", domain], queryFn: () => api.get<SmtpAccount[]>(`/gateway/smtp-accounts?domain=${enc(domain)}`) });
  const [net, setNet] = useState({ cidr: "", description: "" });
  const [acc, setAcc] = useState({ username: `smtp@${domain}`, description: "" });
  const [shown, setShown] = useState<SmtpAccountCreated | null>(null);
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["gw-nets"] });
    qc.invalidateQueries({ queryKey: ["gw-accounts", domain] });
    qc.invalidateQueries({ queryKey: ["gw-domains"] });
  };
  const addNet = useMutation({
    mutationFn: () => api.post("/gateway/relay-networks", { ...net, domain }),
    onSuccess: () => { setNet({ cidr: "", description: "" }); refresh(); },
  });
  const delNet = useMutation({ mutationFn: (id: number) => api.del(`/gateway/relay-networks/${id}`), onSuccess: refresh });
  const addAcc = useMutation({
    mutationFn: () => api.post<SmtpAccountCreated>("/gateway/smtp-accounts", { ...acc, domain }),
    onSuccess: (a) => { setShown(a); setAcc({ username: "", description: "" }); refresh(); },
  });
  const reset = useMutation({ mutationFn: (id: number) => api.post<SmtpAccountCreated>(`/gateway/smtp-accounts/${id}/password`), onSuccess: (a) => setShown(a) });
  const delAcc = useMutation({ mutationFn: (id: number) => api.del(`/gateway/smtp-accounts/${id}`), onSuccess: refresh });
  const mine = (nets.data ?? []).filter((n) => n.domain === domain);
  return (
    <section className="drawer-sec">
      <h3 className="sec-title">{t("relaydom.senders_title")}</h3>
      <GatewayError error={addNet.error || delNet.error || addAcc.error || reset.error || delAcc.error} />
      {nets.isLoading || accounts.isLoading ? <Loading /> : (
        <div className="sender-rows">
          {mine.map((n) => (
            <div className="sender-row" key={`n${n.id}`}>
              <Server aria-hidden />
              <span className="mono">{n.cidr}</span>
              <span className="muted">{n.description}</span>
              {!n.enabled && <span className="tag">{t("domains.paused")}</span>}
              <button type="button" className="icon-btn" aria-label={t("relaydom.unlink")} title={t("relaydom.unlink")}
                onClick={() => window.confirm(t("relaydom.unlink_confirm", { what: n.cidr })) && delNet.mutate(n.id)}><Trash2 /></button>
            </div>
          ))}
          {(accounts.data ?? []).map((a) => (
            <div className="sender-row" key={`a${a.id}`}>
              <KeyRound aria-hidden />
              <span className="mono">{a.username}</span>
              <span className="muted">{a.description || t("relaydom.smtp_account")}</span>
              <button type="button" className="btn small ghost" style={{ marginLeft: "auto" }} disabled={reset.isPending}
                onClick={() => window.confirm(t("relaydom.reset_confirm", { name: a.username })) && reset.mutate(a.id)}>
                <RefreshCw /> {t("relaydom.new_password")}
              </button>
              <button type="button" className="icon-btn" style={{ marginLeft: 0 }} aria-label={t("common.delete")}
                onClick={() => window.confirm(t("relaydom.unlink_confirm", { what: a.username })) && delAcc.mutate(a.id)}><Trash2 /></button>
            </div>
          ))}
          {!mine.length && !accounts.data?.length && <div className="sender-row muted">{t("relaydom.nobody_long")}</div>}
        </div>
      )}
      {shown && (
        <div className="callout" role="status">
          <KeyRound aria-hidden />
          <div style={{ minWidth: 0, flex: 1, display: "flex", flexDirection: "column", gap: 6 }}>
            <b>{t("relaydom.password_once", { name: shown.username })}</b>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <code className="mono pw">{shown.password}</code>
              <CopyBtn text={shown.password} />
            </div>
            <span className="hint">{t("relaydom.password_hint")}</span>
            <div><button type="button" className="btn small" onClick={() => setShown(null)}><Check /> {t("relaydom.saved_it")}</button></div>
          </div>
        </div>
      )}
      <form className="inline-add" onSubmit={(e) => { e.preventDefault(); addNet.mutate(); }}>
        <input className="input" style={{ flex: "1 1 170px" }} required placeholder="203.0.113.25 / 2a02:58::/64" aria-label={t("relaydom.ip")}
          value={net.cidr} onChange={(e) => setNet({ ...net, cidr: e.target.value })} />
        <input className="input" style={{ flex: "1 1 150px" }} placeholder={t("relaydom.ip_desc")} aria-label={t("relaydom.ip_desc")}
          value={net.description} onChange={(e) => setNet({ ...net, description: e.target.value })} />
        <button className="btn small" disabled={addNet.isPending}><Plus /> {t("relaydom.add_ip")}</button>
      </form>
      <form className="inline-add" onSubmit={(e) => { e.preventDefault(); addAcc.mutate(); }}>
        <input className="input" style={{ flex: "1 1 170px" }} required placeholder={`smtp@${domain}`} aria-label={t("relaydom.username")}
          value={acc.username} onChange={(e) => setAcc({ ...acc, username: e.target.value })} />
        <input className="input" style={{ flex: "1 1 150px" }} placeholder={t("relaydom.account_desc")} aria-label={t("relaydom.account_desc")}
          value={acc.description} onChange={(e) => setAcc({ ...acc, description: e.target.value })} />
        <button className="btn small" disabled={addAcc.isPending}><Plus /> {t("relaydom.add_account")}</button>
      </form>
    </section>
  );
}

// ---- DNS records (admin: checked in DNS and published through Cloudflare) -----------------

function RecordBlock({ label, value }: { label: string; value: string }) {
  return (
    <div className="rec-block">
      <span className="rec-label">{label}</span>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
        <div style={{ flex: 1, minWidth: 0 }}><Code>{value}</Code></div>
        <CopyBtn text={value} />
      </div>
    </div>
  );
}

export function DnsRecords({ domain }: { domain: DomainRow }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const check = useCheck(domain.name);
  const records = useQuery({ queryKey: ["gw-records", domain.id], queryFn: () => api.get<DomainRecords>(`/gateway/domains/${domain.id}/records`) });
  const cf = useQuery({ queryKey: ["dns-cloudflare"], queryFn: () => api.get<CfStatus>("/dns/cloudflare"), staleTime: 60_000 });
  const [spfMode, setSpfMode] = useState<"merge" | "only">("merge");
  const [spfAll, setSpfAll] = useState<"~" | "-">("~");
  const [publish, setPublish] = useState<{ kind: PublishKind; value?: string } | null>(null);
  const item = (code: string): Item | undefined => check.data?.items.find((i) => i.code === code);
  const rec = records.data;
  const spf = item("spf");
  const dkim = item("dkim");
  const dmarc = item("dmarc");
  const spfOnly = rec ? `v=spf1 ${rec.spf_include} ${spfAll}all` : "";
  const spfNew = spfMode === "only" ? spfOnly : spf?.fix ?? null;
  const canPublish = !!cf.data?.configured;
  const again = () => qc.invalidateQueries({ queryKey: ["dns-check", domain.name] });
  const pubBtn = (kind: PublishKind, value?: string) => canPublish && (
    <button type="button" className="btn small dark" onClick={() => setPublish({ kind, value })}><Cloud /> {t("relaydom.publish")}</button>
  );
  return (
    <section className="drawer-sec">
      <div className="sec-head">
        <h3 className="sec-title">{t("relaydom.dns_title")}</h3>
        <button type="button" className="btn small ghost" disabled={check.isFetching} onClick={again}><RefreshCw /> {t("relaydom.check_again")}</button>
      </div>
      {!cf.isLoading && !canPublish && <div className="callout"><Copy aria-hidden /><span>{t("relaydom.no_cloudflare")}</span></div>}
      {check.isLoading || records.isLoading ? <Loading /> : (
        <>
          {/* DKIM */}
          <div className="dns-rec">
            <div className="sec-head"><b>DKIM</b>{dkim && <Pill s={dkim.status} />}</div>
            {rec?.dkim ? (
              <>
                <dl className="kv">
                  <dt>{t("relaydom.dns_name")}</dt><dd className="mono">{rec.dkim.name}</dd>
                  <dt>{t("relaydom.record_type")}</dt><dd>TXT · {rec.dkim.key_type.toUpperCase()}</dd>
                </dl>
                <RecordBlock label={t("relaydom.value")} value={rec.dkim.value} />
                {dkim && dkim.status !== "ok" && <div>{pubBtn("dkim")}</div>}
              </>
            ) : <p className="muted" style={{ margin: 0 }}>{t("relaydom.dkim_pending")}</p>}
          </div>
          {/* SPF */}
          <div className="dns-rec">
            <div className="sec-head"><b>SPF</b>{spf && <Pill s={spf.status} />}</div>
            <dl className="kv"><dt>{t("relaydom.now_in_dns")}</dt><dd className="mono">{spf?.value || t("relaydom.no_record")}</dd></dl>
            {spf && <Problems codes={spf.problems} />}
            {spf && spf.status !== "ok" && (
              <>
                <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                  <label className="check"><input type="radio" name="spf-mode" checked={spfMode === "merge"} onChange={() => setSpfMode("merge")} />
                    <span>{t("relaydom.spf_merge")} <span className="tag">{t("relaydom.recommended")}</span></span></label>
                  <label className="check"><input type="radio" name="spf-mode" checked={spfMode === "only"} onChange={() => setSpfMode("only")} />
                    <span>{t("relaydom.spf_only")}</span></label>
                </div>
                {spfMode === "only" && (
                  <div className="seg" role="group" aria-label={t("relaydom.spf_all")} style={{ alignSelf: "flex-start" }}>
                    <button type="button" aria-pressed={spfAll === "~"} onClick={() => setSpfAll("~")}>{t("relaydom.spf_soft")}</button>
                    <button type="button" aria-pressed={spfAll === "-"} onClick={() => setSpfAll("-")}>{t("relaydom.spf_hard")}</button>
                  </div>
                )}
                {spfNew && <RecordBlock label={t("relaydom.new_record")} value={spfNew} />}
                {spfNew && <div>{pubBtn(spfMode === "only" ? "spf_value" : "spf", spfMode === "only" ? spfNew : undefined)}</div>}
              </>
            )}
          </div>
          {/* DMARC */}
          <div className="dns-rec">
            <div className="sec-head"><b>DMARC</b> <span className="muted">{t("relaydom.optional")}</span>{dmarc && <Pill s={dmarc.status} />}</div>
            {dmarc?.value ? (
              <dl className="kv"><dt>{t("relaydom.now_in_dns")}</dt><dd className="mono">{dmarc.value}</dd></dl>
            ) : rec && (
              <>
                <RecordBlock label={`${t("relaydom.suggestion")} · ${rec.dmarc_name}`} value={rec.dmarc_suggested} />
                <span className="hint">{t("relaydom.dmarc_hint")}</span>
                <div>{pubBtn("dmarc", rec.dmarc_suggested)}</div>
              </>
            )}
          </div>
        </>
      )}
      {publish && <PublishModal kind={publish.kind} domain={domain.name} value={publish.value} onClose={() => setPublish(null)} onDone={again} />}
    </section>
  );
}

// ---- customers: what to put in DNS (no lookups, no publishing) --------------------------

function RecordsForCustomer({ domain }: { domain: DomainRow }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["gw-records", domain.id], queryFn: () => api.get<DomainRecords>(`/gateway/domains/${domain.id}/records`) });
  const r = q.data;
  return (
    <section className="drawer-sec">
      <h3 className="sec-title">{t("relaydom.dns_title")}</h3>
      <p className="muted" style={{ margin: 0 }}>{t("relaydom.customer_dns")}</p>
      {q.isLoading ? <Loading /> : r && (
        <>
          {r.dkim ? <RecordBlock label={`DKIM · TXT · ${r.dkim.name}`} value={r.dkim.value} /> : <p className="muted">{t("relaydom.dkim_pending")}</p>}
          <RecordBlock label={`SPF · TXT · ${r.domain}`} value={r.spf_new} />
          <span className="hint">{t("relaydom.spf_customer_hint", { include: r.spf_include })}</span>
          <RecordBlock label={`DMARC · TXT · ${r.dmarc_name}`} value={r.dmarc_suggested} />
        </>
      )}
    </section>
  );
}
