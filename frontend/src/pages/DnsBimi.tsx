// BIMI tab of the DNS toolbox: brand logo next to a domain's mail in Gmail,
// Apple Mail and Yahoo. Shows what is still missing (DMARC enforcement, logo
// in SVG Tiny PS, DNS record, mark certificate), lets the admin upload and
// convert the logo, upload a VMC/CMC, and publish the record via Cloudflare.
import { useMutation, useQuery } from "@tanstack/react-query";
import { Award, Check, Cloud, FileBadge, Image, Info, ShieldCheck, Trash2, Upload, Wand2, X } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { ErrorText, Field, Loading, formatDate, useToast } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { Code, CopyBtn, Pill, PublishModal } from "./Dns";

type SvgReport = { ok: boolean; errors: string[]; warnings: string[]; title: string; size: number; sha256: string };
type CertReport = {
  ok: boolean; kind: "vmc" | "cmc" | "unknown" | ""; subject: string; organization: string; issuer: string; not_after: string;
  days_left: number | null; names: string[]; chain: number; has_logo: boolean; logo_matches: boolean | null; errors: string[]; warnings: string[];
};
type BimiStatus = {
  domain: string; record_name: string;
  logo: { size: number; updated: string; url: string; report: SvgReport } | null;
  cert: { size: number; updated: string; url: string; report: CertReport } | null;
  expected: string | null; published: string[]; record_status: "ok" | "missing" | "different" | "bad";
  dmarc: { record: string | null; at: string | null; policy: string | null; pct: number; ok: boolean; problems: string[] };
  steps: { dmarc: boolean; logo: boolean; record: boolean; cert: boolean };
  level: "full" | "basic" | "none";
};
type LogoCheck = { report: SvgReport; changes: string[]; convert_error: string | null; svg: string | null };

const enc = encodeURIComponent;
const dataUrl = (svg: string) => `data:image/svg+xml;base64,${btoa(unescape(encodeURIComponent(svg)))}`;

function Codes({ prefix, codes, warn }: { prefix: string; codes: string[]; warn?: boolean }) {
  const { t } = useTranslation();
  if (!codes.length) return null;
  return (
    <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13, color: warn ? "var(--warn, inherit)" : undefined }}>
      {codes.map((c) => {
        const [code, ...rest] = c.split(":");
        return <li key={c}>{t(`${prefix}.${code}`, { value: rest.join(":"), defaultValue: c })}</li>;
      })}
    </ul>
  );
}

function Section({ icon, title, right, children }: { icon: ReactNode; title: string; right?: ReactNode; children: ReactNode }) {
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

/** The logo the way mail apps show it: a circle in the inbox list, a square in the message. */
function Preview({ src }: { src: string }) {
  const { t } = useTranslation();
  const box = { border: "1px solid var(--line)", background: "var(--surface-sunk)", objectFit: "contain" as const };
  return (
    <div style={{ display: "flex", gap: 16, alignItems: "flex-end", flexWrap: "wrap" }}>
      <figure style={{ margin: 0, textAlign: "center" }}>
        <img src={src} alt="" width={96} height={96} style={{ ...box, borderRadius: 12 }} />
        <figcaption className="cell-sub">{t("bimi.logo.square")}</figcaption>
      </figure>
      <figure style={{ margin: 0, textAlign: "center" }}>
        <img src={src} alt="" width={48} height={48} style={{ ...box, borderRadius: "50%" }} />
        <figcaption className="cell-sub">{t("bimi.logo.round")}</figcaption>
      </figure>
      <figure style={{ margin: 0, textAlign: "center" }}>
        <img src={src} alt="" width={24} height={24} style={{ ...box, borderRadius: "50%" }} />
        <figcaption className="cell-sub">{t("bimi.logo.small")}</figcaption>
      </figure>
    </div>
  );
}

function readFile(f: File, max: number): Promise<string> {
  if (f.size > max) return Promise.reject(new ApiError(400, `too_large:${Math.round(f.size / 1024)}`));
  return f.text();
}

// --- tab ----------------------------------------------------------------------------

export default function BimiTab() {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const list = useQuery({ queryKey: ["bimi-domains"], queryFn: () => api.get<{ domain: string; logo: boolean; cert: boolean }[]>("/bimi/domains") });
  const domain = params.get("domain") || list.data?.[0]?.domain || "";
  const q = useQuery({ queryKey: ["bimi", domain], queryFn: () => api.get<BimiStatus>(`/bimi/status/${enc(domain)}`), enabled: !!domain });
  const refresh = () => { q.refetch(); list.refetch(); };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <p className="muted" style={{ margin: 0 }}>{t("bimi.help")}</p>
      {list.isLoading ? <Loading /> : list.error ? <ErrorText error={list.error} /> : !list.data?.length ? <div className="empty">{t("bimi.no_domains")}</div> : (
        <Field label={t("bimi.domain")}>
          <select className="select" style={{ maxWidth: 360 }} value={domain} onChange={(e) => setParams((p) => { p.set("domain", e.target.value); return p; }, { replace: true })}>
            {list.data.map((d) => <option key={d.domain} value={d.domain}>{d.domain}{d.logo ? " ✓" : ""}</option>)}
          </select>
        </Field>
      )}
      {domain && (q.isLoading ? <Loading /> : q.error ? <ErrorText error={q.error} /> : q.data && (
        <>
          <Readiness s={q.data} />
          <DmarcCard s={q.data} onDone={refresh} />
          <LogoCard s={q.data} onDone={refresh} />
          <CertCard s={q.data} onDone={refresh} />
          <RecordCard s={q.data} onDone={refresh} />
        </>
      ))}
    </div>
  );
}

function Readiness({ s }: { s: BimiStatus }) {
  const { t } = useTranslation();
  const steps = (["dmarc", "logo", "record", "cert"] as const);
  return (
    <section className="card" style={{ borderColor: s.level === "full" ? "var(--ok, var(--accent))" : "var(--accent)" }}>
      <div className="card-head">
        <span className="status s-info" style={{ padding: 4 }}><Award /></span>
        <h2>{t("bimi.ready.title")}</h2>
        <div className="right"><Pill s={s.level === "full" ? "ok" : s.level === "basic" ? "weak" : "missing"} label={t(`bimi.ready.level_${s.level}`)} /></div>
      </div>
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {steps.map((k, i) => (
          <div key={k} style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
            <span className={`status ${s.steps[k] ? "s-ok" : k === "cert" ? "s-warn" : "s-bad"}`} style={{ padding: 2 }}>{s.steps[k] ? <Check /> : <X />}</span>
            <span>
              <strong>{i + 1}. {t(`bimi.ready.${k}`)}</strong>
              <span className="cell-sub" style={{ display: "block" }}>{t(`bimi.ready.${k}_help`)}</span>
            </span>
          </div>
        ))}
        <p className="cell-sub" style={{ margin: "4px 0 0" }}>{t(`bimi.ready.level_${s.level}_help`)}</p>
      </div>
    </section>
  );
}

function DmarcCard({ s, onDone }: { s: BimiStatus; onDone: () => void }) {
  const { t } = useTranslation();
  const [publish, setPublish] = useState<string | null>(null);
  const d = s.dmarc;
  return (
    <Section icon={<ShieldCheck />} title={t("bimi.dmarc.title")} right={<Pill s={d.ok ? "ok" : "bad"} label={d.policy ? `p=${d.policy}` : undefined} />}>
      <p className="cell-sub" style={{ margin: 0 }}>{t("bimi.dmarc.help")}</p>
      {d.record ? <Code>{`_dmarc.${d.at}  TXT  "${d.record}"`}</Code> : null}
      <Codes prefix="bimi.dmarc.p" codes={d.problems} />
      {!d.ok && d.record && d.at === s.domain && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn" type="button" onClick={() => setPublish("quarantine")}><Cloud /> {t("bimi.dmarc.set_quarantine")}</button>
          <button className="btn ghost" type="button" onClick={() => setPublish("reject")}><Cloud /> {t("bimi.dmarc.set_reject")}</button>
        </div>
      )}
      {!d.ok && <div className="callout warn"><Info /><span>{t("bimi.dmarc.careful")}</span></div>}
      {publish && <PublishModal kind="dmarc_policy" domain={s.domain} value={publish} onClose={() => setPublish(null)} onDone={onDone} />}
    </Section>
  );
}

function LogoCard({ s, onDone }: { s: BimiStatus; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [svg, setSvg] = useState<string | null>(null);
  const [title, setTitle] = useState("");
  const [convert, setConvert] = useState(false);
  useEffect(() => { setSvg(null); setConvert(false); }, [s.domain]);
  const stored = useQuery({
    queryKey: ["bimi-preview", s.domain, s.logo?.report.sha256], enabled: !!s.logo,
    queryFn: () => api.get<{ data_url: string | null }>(`/bimi/preview/${enc(s.domain)}`),
  });
  const check = useQuery({
    queryKey: ["bimi-check", s.domain, svg, convert, title], enabled: svg !== null, retry: false, gcTime: 0,
    queryFn: () => api.post<LogoCheck>(`/bimi/logo/${enc(s.domain)}/check`, { svg, convert, title }),
  });
  const save = useMutation({
    mutationFn: () => api.put(`/bimi/logo/${enc(s.domain)}`, { svg, convert, title }),
    onSuccess: () => { toast(t("common.saved")); setSvg(null); setConvert(false); onDone(); },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/bimi/logo/${enc(s.domain)}`),
    onSuccess: () => { toast(t("bimi.logo.removed")); onDone(); },
  });
  const [fileError, setFileError] = useState<unknown>(null);
  const c = check.data;
  const shown = c?.svg ?? svg;
  return (
    <Section icon={<Image />} title={t("bimi.logo.title")} right={s.logo ? <Pill s={s.logo.report.ok ? "ok" : "bad"} /> : <Pill s="missing" />}>
      <p className="cell-sub" style={{ margin: 0 }}>{t("bimi.logo.help")}</p>
      {s.logo && svg === null && (
        <>
          {stored.data?.data_url && <Preview src={stored.data.data_url} />}
          <dl className="kv">
            <dt>{t("bimi.logo.name")}</dt><dd>{s.logo.report.title || "–"}</dd>
            <dt>{t("bimi.logo.size")}</dt><dd>{(s.logo.size / 1024).toFixed(1)} KB</dd>
            <dt>{t("bimi.logo.updated")}</dt><dd>{formatDate(s.logo.updated, i18n.language)}</dd>
            <dt>{t("bimi.logo.url")}</dt><dd className="mono" style={{ fontSize: 12, wordBreak: "break-all" }}><a href={s.logo.url} target="_blank" rel="noreferrer">{s.logo.url}</a></dd>
          </dl>
          <Codes prefix="bimi.svg" codes={s.logo.report.errors} />
        </>
      )}
      {svg !== null && (
        check.isLoading ? <Loading /> : check.error ? <ErrorText error={check.error} /> : c && (
          <>
            {shown && <Preview src={dataUrl(shown)} />}
            {c.report.ok ? (
              <div className="callout"><Check /><span>{t(convert ? "bimi.logo.converted_ok" : "bimi.logo.valid")}</span></div>
            ) : (
              <div className="callout warn"><Info /><span>{t("bimi.logo.invalid")}<Codes prefix="bimi.svg" codes={c.report.errors} /></span></div>
            )}
            {c.convert_error && <ErrorText error={new ApiError(400, c.convert_error)} />}
            {c.changes.length > 0 && (
              <div>
                <div className="cell-sub" style={{ fontWeight: 650 }}>{t("bimi.logo.changes")}</div>
                <Codes prefix="bimi.change" codes={c.changes} />
              </div>
            )}
            {!c.report.ok && !convert && (
              <div className="form-grid">
                <Field label={t("bimi.logo.title_field")} hint={t("bimi.logo.title_hint")}>
                  <input className="input" value={title} onChange={(e) => setTitle(e.target.value)} placeholder={s.domain} maxLength={100} />
                </Field>
              </div>
            )}
          </>
        )
      )}
      <ErrorText error={fileError ?? save.error ?? remove.error} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input ref={input} type="file" accept=".svg,image/svg+xml" hidden onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (!f) return;
          setFileError(null);
          readFile(f, 256 * 1024).then((txt) => { setSvg(txt); setConvert(false); setTitle(""); }, setFileError);
        }} />
        <button className="btn" type="button" onClick={() => input.current?.click()}><Upload /> {t(s.logo ? "bimi.logo.replace" : "bimi.logo.upload")}</button>
        {svg !== null && c && !c.report.ok && !convert && (
          <button className="btn" type="button" onClick={() => setConvert(true)}><Wand2 /> {t("bimi.logo.convert")}</button>
        )}
        {svg !== null && c?.report.ok && (
          <button className="btn primary" type="button" disabled={save.isPending} onClick={() => save.mutate()}><Check /> {t("bimi.logo.save")}</button>
        )}
        {svg !== null && <button className="btn ghost" type="button" onClick={() => { setSvg(null); setConvert(false); }}>{t("common.cancel")}</button>}
        {s.logo && svg === null && (
          <button className="btn ghost danger" type="button" disabled={remove.isPending}
            onClick={() => window.confirm(t("bimi.logo.remove_confirm")) && remove.mutate()}><Trash2 /> {t("bimi.logo.remove")}</button>
        )}
      </div>
    </Section>
  );
}

function CertCard({ s, onDone }: { s: BimiStatus; onDone: () => void }) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [pem, setPem] = useState<string | null>(null);
  const [fileError, setFileError] = useState<unknown>(null);
  useEffect(() => setPem(null), [s.domain]);
  const check = useQuery({
    queryKey: ["bimi-cert-check", s.domain, pem], enabled: pem !== null, retry: false, gcTime: 0,
    queryFn: () => api.post<CertReport>(`/bimi/cert/${enc(s.domain)}/check`, { pem }),
  });
  const save = useMutation({
    mutationFn: () => api.put(`/bimi/cert/${enc(s.domain)}`, { pem }),
    onSuccess: () => { toast(t("common.saved")); setPem(null); onDone(); },
  });
  const remove = useMutation({
    mutationFn: () => api.del(`/bimi/cert/${enc(s.domain)}`),
    onSuccess: () => { toast(t("bimi.cert.removed")); onDone(); },
  });
  const useLogo = useMutation({
    mutationFn: () => api.post(`/bimi/logo/${enc(s.domain)}/from-cert`),
    onSuccess: () => { toast(t("bimi.cert.logo_used")); onDone(); },
  });
  const r = pem !== null ? check.data : s.cert?.report;
  return (
    <Section icon={<FileBadge />} title={t("bimi.cert.title")}
      right={s.cert ? <Pill s={s.cert.report.ok ? "ok" : "bad"} label={t(`bimi.cert.kind_${s.cert.report.kind || "unknown"}`)} /> : <Pill s="skipped" label={t("bimi.cert.none")} />}>
      <p className="cell-sub" style={{ margin: 0 }}>{t("bimi.cert.help")}</p>
      <ul style={{ margin: 0, paddingLeft: 18, fontSize: 13 }}>
        <li>{t("bimi.cert.vmc")}</li>
        <li>{t("bimi.cert.cmc")}</li>
      </ul>
      {pem !== null && check.isLoading && <Loading />}
      {pem !== null && check.error && <ErrorText error={check.error} />}
      {r && (
        <>
          <dl className="kv">
            <dt>{t("bimi.cert.kind")}</dt><dd>{t(`bimi.cert.kind_${r.kind || "unknown"}`)}</dd>
            <dt>{t("bimi.cert.org")}</dt><dd>{r.organization || "–"}</dd>
            <dt>{t("bimi.cert.issuer")}</dt><dd>{r.issuer || "–"}</dd>
            <dt>{t("bimi.cert.names")}</dt><dd className="mono" style={{ fontSize: 12 }}>{r.names.join(", ") || "–"}</dd>
            <dt>{t("bimi.cert.valid_until")}</dt><dd>{formatDate(r.not_after, i18n.language, false)}{r.days_left !== null && <span className="muted"> · {t("bimi.cert.days_left", { n: r.days_left })}</span>}</dd>
          </dl>
          <Codes prefix="bimi.certp" codes={r.errors} />
          <Codes prefix="bimi.certp" codes={r.warnings} warn />
        </>
      )}
      <ErrorText error={fileError ?? save.error ?? remove.error ?? useLogo.error} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input ref={input} type="file" accept=".pem,.crt,.cer,application/x-pem-file" hidden onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (!f) return;
          setFileError(null);
          readFile(f, 64 * 1024).then(setPem, setFileError);
        }} />
        <button className="btn" type="button" onClick={() => input.current?.click()}><Upload /> {t(s.cert ? "bimi.cert.replace" : "bimi.cert.upload")}</button>
        {pem !== null && check.data?.ok && <button className="btn primary" type="button" disabled={save.isPending} onClick={() => save.mutate()}><Check /> {t("bimi.cert.save")}</button>}
        {pem !== null && <button className="btn ghost" type="button" onClick={() => setPem(null)}>{t("common.cancel")}</button>}
        {pem === null && s.cert?.report.has_logo && (!s.logo || s.cert.report.logo_matches === false) && (
          <button className="btn" type="button" disabled={useLogo.isPending} onClick={() => useLogo.mutate()}><Image /> {t("bimi.cert.use_logo")}</button>
        )}
        {pem === null && s.cert && (
          <button className="btn ghost danger" type="button" disabled={remove.isPending}
            onClick={() => window.confirm(t("bimi.cert.remove_confirm")) && remove.mutate()}><Trash2 /> {t("bimi.cert.remove")}</button>
        )}
      </div>
    </Section>
  );
}

function RecordCard({ s, onDone }: { s: BimiStatus; onDone: () => void }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const st = s.record_status === "different" && !s.expected ? "bad" : s.record_status;
  return (
    <Section icon={<Cloud />} title={t("bimi.record.title")} right={<Pill s={st === "different" ? "weak" : st} label={t(`bimi.record.status_${s.record_status}`)} />}>
      <p className="cell-sub" style={{ margin: 0 }}>{t("bimi.record.help")}</p>
      {s.expected ? (
        <>
          <div className="cell-sub" style={{ fontWeight: 650 }}>{t("bimi.record.expected")}</div>
          <Code>{`${s.record_name}  TXT  "${s.expected}"`}</Code>
        </>
      ) : <div className="callout warn"><Info /><span>{t("bimi.record.need_logo")}</span></div>}
      {s.published.length > 0 && s.record_status !== "ok" && (
        <>
          <div className="cell-sub" style={{ fontWeight: 650 }}>{t("bimi.record.published")}</div>
          <Code>{s.published.join("\n")}</Code>
        </>
      )}
      {s.expected && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn primary" type="button" onClick={() => setOpen(true)}><Cloud /> {t("dns.publish")}</button>
          <CopyBtn text={s.expected} />
        </div>
      )}
      {open && <PublishModal kind="bimi" domain={s.domain} onClose={() => setOpen(false)} onDone={onDone} />}
    </Section>
  );
}
