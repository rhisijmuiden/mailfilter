// Recipient check of a transport domain: which addresses exist (backend: /api/gateway/domains/{id}/recipients).
// Mail for an address that does not exist is refused during the SMTP conversation.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, BookUser, CheckCircle2, CircleSlash, FlaskConical, ListChecks, Save, Server, Upload, XCircle } from "lucide-react";
import { useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import type { CacheSettings, LdapSettings, RecipientCheck, RecipientSettings, RecipientTest } from "../lib/gateway";
import GatewayError from "./GatewayError";
import { Field, Loading, useToast } from "./ui";

const METHODS: { key: RecipientCheck; icon: ReactNode }[] = [
  { key: "callout", icon: <Server /> },
  { key: "ldap", icon: <BookUser /> },
  { key: "list", icon: <ListChecks /> },
  { key: "off", icon: <CircleSlash /> },
];

const EMPTY_LDAP: LdapSettings = {
  servers: [], security: "starttls", verify_cert: true, bind_dn: "", bind_password: null, base_dn: "",
  preset: "ad", query_filter: "", result_attribute: "mail", has_password: false,
};

export function RecipientTag({ check }: { check: RecipientCheck }) {
  const { t } = useTranslation();
  return <span className={`tag${check === "off" ? "" : " lucht"}`}>{t(`rcpt.short_${check}`)}</span>;
}

export default function RecipientSection({ domainId, domain, admin }: { domainId: number; domain: string; admin: boolean }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["gw-rcpt", domainId], queryFn: () => api.get<RecipientSettings>(`/gateway/domains/${domainId}/recipients`) });
  return (
    <section className="drawer-sec" aria-labelledby="rcpt-title">
      <div className="sec-head">
        <h3 className="sec-title" id="rcpt-title">{t("rcpt.title")}</h3>
      </div>
      <p className="muted" style={{ margin: 0, fontSize: 13 }}>{t("rcpt.intro", { domain })}</p>
      {q.isLoading || !q.data ? <Loading /> : <RecipientForm key={JSON.stringify(q.data)} data={q.data} domainId={domainId} admin={admin} />}
    </section>
  );
}

function RecipientForm({ data, domainId, admin }: { data: RecipientSettings; domainId: number; admin: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [mode, setMode] = useState<RecipientCheck>(data.recipient_check);
  const [ldap, setLdap] = useState<LdapSettings>(data.ldap ?? EMPTY_LDAP);
  const [list, setList] = useState(data.recipients.join("\n"));
  const fileRef = useRef<HTMLInputElement>(null);
  const settings = () => ({
    recipient_check: mode,
    ldap: mode === "ldap" ? ldap : null,
    recipients: mode === "list" ? list.split(/[\s,;]+/).map((s) => s.trim()).filter(Boolean) : [],
  });
  const changed = mode !== data.recipient_check || (mode === "ldap" && (JSON.stringify(ldap) !== JSON.stringify(data.ldap ?? EMPTY_LDAP)))
    || (mode === "list" && list.trim() !== data.recipients.join("\n"));
  const save = useMutation({
    mutationFn: () => api.put<RecipientSettings>(`/gateway/domains/${domainId}/recipients`, settings(), { quiet: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["gw-rcpt", domainId] });
      qc.invalidateQueries({ queryKey: ["gw-domains"] });
      qc.invalidateQueries({ queryKey: ["gw-status"] });
      toast(t("gateway.applied"), "success");
    },
  });
  const readFile = (f: File | undefined) => {
    if (!f) return;
    f.text().then((text) => {
      // CSV or plain list: take everything that looks like an address (or a bare name on its own line).
      const found = text.match(/[^\s,;"'<>]+@[^\s,;"'<>]+/g) ?? [];
      const merged = Array.from(new Set([...list.split(/\s+/).filter(Boolean), ...found.map((a) => a.toLowerCase())]));
      setList(merged.join("\n"));
      toast(t("rcpt.list_imported", { count: found.length }), "info");
    });
  };
  const ld = (patch: Partial<LdapSettings>) => setLdap({ ...ldap, ...patch });
  const count = list.split(/[\s,;]+/).filter(Boolean).length;
  return (
    <>
      <div role="radiogroup" aria-label={t("rcpt.method")} className="form-grid" style={{ gap: 10 }}>
        {METHODS.map(({ key, icon }) => (
          <label key={key} className={`kind-choice${mode === key ? " on" : ""}`} style={{ cursor: admin ? "pointer" : "default" }}>
            <input type="radio" name="mf-rcpt" disabled={!admin} checked={mode === key} onChange={() => setMode(key)} />
            <span className="kind-icon" aria-hidden>{icon}</span>
            <span>
              <b>{t(`rcpt.m_${key}`)}</b>
              {key === "callout" && <> <span className="tag">{t("rcpt.default")}</span></>}
              <br />
              <span className="muted">{t(`rcpt.m_${key}_hint`)}</span>
            </span>
          </label>
        ))}
      </div>

      {mode === "callout" && (
        <>
          <div className="callout"><Server aria-hidden /><span>{t("rcpt.callout_explain", { server: data.destinations.map((d) => d.host).join(", ") || "—" })}</span></div>
          {admin && <CacheEditor />}
        </>
      )}

      {mode === "ldap" && (
        <div className="form-grid">
          <Field label={t("rcpt.ldap_type")}>
            <div className="seg" role="group" aria-label={t("rcpt.ldap_type")}>
              {(["ad", "generic"] as const).map((p) => (
                <button key={p} type="button" disabled={!admin} aria-pressed={ldap.preset === p} onClick={() => ld({ preset: p })}>{t(`rcpt.preset_${p}`)}</button>
              ))}
            </div>
          </Field>
          <Field label={t("rcpt.security")}>
            <select className="select" disabled={!admin} value={ldap.security} onChange={(e) => ld({ security: e.target.value as LdapSettings["security"] })}>
              <option value="starttls">{t("rcpt.sec_starttls")}</option>
              <option value="ldaps">{t("rcpt.sec_ldaps")}</option>
              <option value="none">{t("rcpt.sec_none")}</option>
            </select>
          </Field>
          <Field label={t("rcpt.servers")} hint={t(ldap.preset === "ad" ? "rcpt.servers_hint_ad" : "rcpt.servers_hint")} full>
            <input className="input mono" disabled={!admin} placeholder={ldap.preset === "ad" ? "dc1.example.local dc2.example.local:3268" : "ldap.example.nl"}
              value={ldap.servers.join(" ")} onChange={(e) => ld({ servers: e.target.value.split(/[\s,]+/).filter(Boolean) })} />
          </Field>
          {ldap.security === "none" && <div className="full callout warn"><AlertTriangle aria-hidden /><span>{t("rcpt.sec_none_warn")}</span></div>}
          <Field label={t("rcpt.base_dn")} hint={t("rcpt.base_dn_hint")} full>
            <input className="input mono" disabled={!admin} placeholder="DC=example,DC=local" value={ldap.base_dn} onChange={(e) => ld({ base_dn: e.target.value })} />
          </Field>
          <Field label={t("rcpt.bind_dn")} hint={t(ldap.preset === "ad" ? "rcpt.bind_dn_hint_ad" : "rcpt.bind_dn_hint")}>
            <input className="input mono" disabled={!admin} autoComplete="off" placeholder={ldap.preset === "ad" ? "svc-mailfilter@example.local" : "cn=mailfilter,ou=services,dc=example,dc=nl"}
              value={ldap.bind_dn} onChange={(e) => ld({ bind_dn: e.target.value })} />
          </Field>
          <Field label={t("rcpt.bind_password")} hint={ldap.has_password ? t("rcpt.password_kept") : undefined}>
            <input className="input" type="password" disabled={!admin || !ldap.bind_dn} autoComplete="new-password"
              placeholder={ldap.has_password ? "••••••••" : ""} value={ldap.bind_password ?? ""}
              onChange={(e) => ld({ bind_password: e.target.value || null })} />
          </Field>
          <details className="full">
            <summary className="muted" style={{ cursor: "pointer", fontSize: 13 }}>{t("rcpt.advanced")}</summary>
            <div className="form-grid" style={{ marginTop: 10 }}>
              <Field label={t("rcpt.filter")} hint={t("rcpt.filter_hint")} full>
                <input className="input mono" disabled={!admin} placeholder={data.default_filters[ldap.preset]} value={ldap.query_filter}
                  onChange={(e) => ld({ query_filter: e.target.value })} />
              </Field>
              <Field label={t("rcpt.attribute")} hint={t("rcpt.attribute_hint")}>
                <input className="input mono" disabled={!admin} value={ldap.result_attribute} onChange={(e) => ld({ result_attribute: e.target.value })} />
              </Field>
              <label className="check" style={{ alignSelf: "end" }}>
                <input type="checkbox" disabled={!admin || ldap.security === "none"} checked={ldap.verify_cert} onChange={(e) => ld({ verify_cert: e.target.checked })} />
                <span>{t("rcpt.verify_cert")}</span>
              </label>
            </div>
          </details>
          <div className="full callout"><BookUser aria-hidden /><span>{t("rcpt.ldap_explain")}</span></div>
        </div>
      )}

      {mode === "list" && (
        <div className="form-grid">
          <Field label={t("rcpt.list", { count })} hint={t("rcpt.list_hint", { domain: data.domain })} full>
            <textarea className="input mono" rows={8} disabled={!admin} spellCheck={false} placeholder={`info@${data.domain}\nsales\n…`}
              value={list} onChange={(e) => setList(e.target.value)} />
          </Field>
          {admin && (
            <div className="full">
              <input ref={fileRef} type="file" accept=".csv,.txt,text/csv,text/plain" hidden onChange={(e) => { readFile(e.target.files?.[0]); e.target.value = ""; }} />
              <button type="button" className="btn small" onClick={() => fileRef.current?.click()}><Upload /> {t("rcpt.import")}</button>
            </div>
          )}
        </div>
      )}

      {mode === "off" && <div className="callout warn"><AlertTriangle aria-hidden /><span>{t("rcpt.off_warn")}</span></div>}

      <GatewayError error={save.error} />
      {admin && (
        <div style={{ display: "flex", gap: 8 }}>
          <button type="button" className="btn primary" disabled={!changed || save.isPending} onClick={() => save.mutate()}>
            <Save /> {save.isPending ? t("gateway.applying") : t("rcpt.save")}
          </button>
        </div>
      )}
      <Tester domainId={domainId} domain={data.domain} settings={admin && changed ? settings() : null} />
    </>
  );
}

function Tester({ domainId, domain, settings }: { domainId: number; domain: string; settings: unknown }) {
  const { t } = useTranslation();
  const [address, setAddress] = useState("");
  const run = useMutation({
    mutationFn: () => api.post<RecipientTest>(`/gateway/domains/${domainId}/recipients/test`, { address, settings }),
  });
  const r = run.data;
  return (
    <div className="dns-rec" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <form style={{ display: "flex", gap: 8, flexWrap: "wrap" }} onSubmit={(e) => { e.preventDefault(); if (address.trim()) run.mutate(); }}>
        <input className="input" style={{ flex: "1 1 220px" }} placeholder={`info@${domain}`} aria-label={t("rcpt.test_address")}
          value={address} onChange={(e) => setAddress(e.target.value)} />
        <button className="btn" disabled={run.isPending || !address.trim()}><FlaskConical /> {run.isPending ? t("rcpt.testing") : t("rcpt.test")}</button>
      </form>
      {settings ? <span className="hint">{t("rcpt.test_unsaved")}</span> : null}
      <GatewayError error={run.error} />
      {r && (
        <div className={`callout${r.exists === false || r.exists === null ? " warn" : ""}`} role="status">
          {r.exists === true ? <CheckCircle2 aria-hidden /> : r.exists === false ? <XCircle aria-hidden /> : <AlertTriangle aria-hidden />}
          <div style={{ minWidth: 0 }}>
            <b>{t(r.exists === true ? "rcpt.r_exists" : r.exists === false ? "rcpt.r_missing" : "rcpt.r_unknown", { address: r.address })}</b>
            <div className="muted" style={{ fontSize: 12.5 }}>
              {t(`rcpt.via_${r.method}`, { server: r.server ?? "" })}
              {r.detail && <> · <span className="mono" style={{ wordBreak: "break-word" }}>{r.detail}</span></>}
            </div>
            {r.method === "callout" && r.exists === false && <div className="muted" style={{ fontSize: 12.5 }}>{t("rcpt.r_missing_callout")}</div>}
            {r.accepts_all && <div style={{ marginTop: 6 }}><b>{t("rcpt.accepts_all")}</b> {t("rcpt.accepts_all_hint")}</div>}
          </div>
        </div>
      )}
    </div>
  );
}

function CacheEditor() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const q = useQuery({ queryKey: ["gw-rcpt-cache"], queryFn: () => api.get<CacheSettings>("/gateway/recipient-cache") });
  const [f, setF] = useState<CacheSettings | null>(null);
  const v = f ?? q.data;
  const save = useMutation({
    mutationFn: () => api.put<CacheSettings>("/gateway/recipient-cache", v, { quiet: true }),
    onSuccess: () => { setF(null); qc.invalidateQueries({ queryKey: ["gw-rcpt-cache"] }); toast(t("gateway.applied"), "success"); },
  });
  if (!v) return null;
  return (
    <details>
      <summary className="muted" style={{ cursor: "pointer", fontSize: 13 }}>
        {t("rcpt.cache_summary", { days: v.positive_days, minutes: v.negative_minutes })}
      </summary>
      <div className="form-grid" style={{ marginTop: 10 }}>
        <Field label={t("rcpt.cache_pos")} hint={t("rcpt.cache_pos_hint")}>
          <input className="input" type="number" min={1} max={90} value={v.positive_days} onChange={(e) => setF({ ...v, positive_days: Number(e.target.value) })} />
        </Field>
        <Field label={t("rcpt.cache_neg")} hint={t("rcpt.cache_neg_hint")}>
          <input className="input" type="number" min={1} max={10080} value={v.negative_minutes} onChange={(e) => setF({ ...v, negative_minutes: Number(e.target.value) })} />
        </Field>
        <div className="full"><span className="hint">{t("rcpt.cache_all")}</span></div>
        <div className="full"><GatewayError error={save.error} /></div>
        <div className="full">
          <button type="button" className="btn small" disabled={!f || save.isPending} onClick={() => save.mutate()}><Save /> {t("common.save")}</button>
        </div>
      </div>
    </details>
  );
}
