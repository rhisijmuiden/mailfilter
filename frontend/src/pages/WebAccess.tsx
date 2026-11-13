// System > Web access: how the web interface is reached. The installer asks the same
// question (--web-mode); changing it here runs install/web-access.sh on the server.
import { useMutation, useQuery } from "@tanstack/react-query";
import type { LucideIcon } from "lucide-react";
import { AlertTriangle, ArrowRightLeft, ExternalLink, Globe, Lock, ShieldAlert, Shuffle, Terminal } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { JobProgress } from "../components/JobProgress";
import { Card, ErrorText, Field, Modal, PageHead, Skeleton } from "../components/ui";
import { api } from "../lib/api";
import type { Job } from "../lib/jobs";

type Mode = "public" | "internal" | "proxy" | "http";
const MODES: Mode[] = ["public", "internal", "proxy", "http"];
const ICONS: Record<Mode, LucideIcon> = { public: Globe, internal: Lock, proxy: Shuffle, http: ShieldAlert };

type WebState = {
  mode: Mode; port: number; url: string; trusted_proxies: string[]; web_from: string[]; hostname: string;
  own_certificate: boolean; letsencrypt: boolean;
};
type Form = { mode: Mode; port: string; url: string; proxies: string; from: string };

const lines = (s: string) => s.split(/[\s,;]+/).map((x) => x.trim()).filter(Boolean);

function newUrl(f: Form, host: string): string {
  if (f.mode === "proxy") return (f.url.trim() || `https://${host}`).replace(/\/$/, "");
  if (f.mode === "http") return `http://${host}${f.port && f.port !== "80" ? `:${f.port}` : ""}`;
  return `https://${host}`;
}

export default function WebAccess() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["web-access"], queryFn: () => api.get<WebState>("/system/web") });
  const [f, setF] = useState<Form | null>(null);
  const [confirm, setConfirm] = useState(false);
  const [jobId, setJobId] = useState<string | null>(null);
  const [done, setDone] = useState<Job | null>(null);
  useEffect(() => {
    if (q.data && !f) {
      const d = q.data;
      setF({ mode: d.mode, port: String(d.port), url: d.mode === "proxy" ? d.url : "", proxies: d.trusted_proxies.join("\n"), from: d.web_from.join("\n") });
    }
  }, [q.data, f]);
  const save = useMutation({
    mutationFn: (body: object) => api.put<Job>("/system/web", body, { quiet: true }),
    onSuccess: (job) => { setJobId(job.id); setDone(job.state === "done" || job.state === "failed" ? job : null); },
  });
  if (q.isLoading || !f) return (<><PageHead title={t("web.title")} sub={t("web.sub")} />{q.error ? <ErrorText error={q.error} /> : <Card><Skeleton rows={4} /></Card>}</>);
  const d = q.data!;
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF({ ...f, [k]: v });
  const pick = (m: Mode) => setF({ ...f, mode: m, port: m === "proxy" ? (d.mode === "proxy" ? String(d.port) : "8080") : m === "http" ? (d.mode === "http" ? String(d.port) : "80") : "443" });
  const target = newUrl(f, d.hostname);
  const body = {
    mode: f.mode,
    ...(f.mode === "proxy" || f.mode === "http" ? { port: Number(f.port) || undefined } : {}),
    ...(f.mode === "proxy" ? { url: f.url.trim(), trusted_proxies: lines(f.proxies) } : {}),
    ...(f.mode === "internal" || f.mode === "http" ? { web_from: lines(f.from) } : {}),
  };
  const changed = JSON.stringify(body) !== JSON.stringify({
    mode: d.mode,
    ...(d.mode === "proxy" || d.mode === "http" ? { port: d.port } : {}),
    ...(d.mode === "proxy" ? { url: d.url, trusted_proxies: d.trusted_proxies } : {}),
    ...(d.mode === "internal" || d.mode === "http" ? { web_from: d.web_from } : {}),
  });
  const invalid = (f.mode === "proxy" && lines(f.proxies).length === 0) || ((f.mode === "proxy" || f.mode === "http") && !(Number(f.port) >= 1 && Number(f.port) <= 65535));

  return (
    <>
      <PageHead title={t("web.title")} sub={t("web.sub")} />
      <div className="callout" style={{ marginBottom: 16 }}>
        {(() => { const I = ICONS[d.mode]; return <I aria-hidden />; })()}
        <div>
          <b>{t("web.now", { mode: t(`web.mode.${d.mode}.title`) })}</b>
          <div className="muted" style={{ fontSize: 13 }}>
            <span className="mono">{d.url}</span>
            {d.mode === "public" && !d.letsencrypt && <> · {t("web.no_le_yet")}</>}
            {d.mode === "internal" && <> · {t(d.own_certificate ? "web.own_cert" : "web.caddy_cert")}</>}
            {d.mode === "proxy" && <> · {t("web.proxy_on", { port: d.port, proxies: d.trusted_proxies.join(", ") })}</>}
          </div>
        </div>
      </div>

      <Card title={t("web.choose")}>
        <div className="mode-grid" role="radiogroup" aria-label={t("web.choose")}>
          {MODES.map((m) => {
            const I = ICONS[m];
            return (
              <label key={m} className={`mode-card${f.mode === m ? " on" : ""}${m === "http" ? " risky" : ""}`}>
                <input type="radio" name="web-mode" checked={f.mode === m} onChange={() => pick(m)} />
                <span className="mode-ico" aria-hidden><I /></span>
                <span className="mode-txt">
                  <b>{t(`web.mode.${m}.title`)}{m === "public" && <span className="tag lucht" style={{ marginLeft: 8 }}>{t("web.recommended")}</span>}{d.mode === m && <span className="tag" style={{ marginLeft: 8 }}>{t("web.current")}</span>}</b>
                  <span>{t(`web.mode.${m}.desc`)}</span>
                </span>
              </label>
            );
          })}
        </div>

        <div className="grid" style={{ marginTop: 18 }}>
          {f.mode === "proxy" && (
            <>
              <Field label={t("web.proxies")} hint={t("web.proxies_hint")}>
                <textarea className="input mono" rows={3} value={f.proxies} onChange={(e) => set("proxies", e.target.value)} placeholder="10.0.0.5" />
              </Field>
              <div className="grid" style={{ gridTemplateColumns: "minmax(0,1fr) minmax(0,2fr)" }}>
                <Field label={t("web.port")} hint={t("web.port_hint_proxy")}>
                  <input className="input num" inputMode="numeric" value={f.port} onChange={(e) => set("port", e.target.value.replace(/\D/g, ""))} />
                </Field>
                <Field label={t("web.url")} hint={t("web.url_hint")}>
                  <input className="input mono" value={f.url} onChange={(e) => set("url", e.target.value)} placeholder={`https://${d.hostname}`} />
                </Field>
              </div>
            </>
          )}
          {f.mode === "http" && (
            <Field label={t("web.port")}>
              <input className="input num" inputMode="numeric" value={f.port} onChange={(e) => set("port", e.target.value.replace(/\D/g, ""))} style={{ maxWidth: 140 }} />
            </Field>
          )}
          {(f.mode === "internal" || f.mode === "http") && (
            <Field label={t("web.from")} hint={t("web.from_hint")}>
              <textarea className="input mono" rows={3} value={f.from} onChange={(e) => set("from", e.target.value)} placeholder="192.168.10.0/24" />
            </Field>
          )}
          {f.mode === "http" && (
            <div className="callout bad"><AlertTriangle aria-hidden /><div><b>{t("web.http_warn_title")}</b><div>{t("web.http_warn")}</div></div></div>
          )}
          {f.mode === "internal" && (
            <div className="callout"><Lock aria-hidden /><div>{t("web.internal_note")}</div></div>
          )}
          {f.mode !== "public" && (
            <div className="callout warn"><AlertTriangle aria-hidden /><div>{t("web.limits")}</div></div>
          )}
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
            <button className="btn primary" type="button" disabled={!changed || invalid} onClick={() => { save.reset(); setDone(null); setJobId(null); setConfirm(true); }}>
              <ArrowRightLeft /> {t("web.apply")}
            </button>
            {changed && !invalid && <span className="muted">{t("web.new_address")} <span className="mono">{target}</span></span>}
          </div>
        </div>
      </Card>

      <Card title={t("web.rescue_title")}>
        <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
          <Terminal aria-hidden style={{ width: 18, height: 18, flex: "none", marginTop: 2, color: "var(--ink-muted)" }} />
          <div>
            <p style={{ marginTop: 0 }}>{t("web.rescue")}</p>
            <code className="mono" style={{ display: "block", padding: "10px 12px", background: "var(--surface-sunk)", borderRadius: "var(--r-sm)", overflowX: "auto", whiteSpace: "nowrap" }}>
              sudo /opt/mailfilter/install/web-access.sh --mode public
            </code>
          </div>
        </div>
      </Card>

      {confirm && (
        <Modal title={t("web.confirm_title")} onClose={() => !save.isPending && setConfirm(false)} icon={<ArrowRightLeft />}>
          {!jobId ? (
            <>
              <p style={{ margin: 0 }}>{t("web.confirm", { mode: t(`web.mode.${f.mode}.title`) })}</p>
              <p style={{ margin: 0 }}>{t("web.confirm_url")} <a className="mono" href={target}>{target}</a></p>
              {f.mode === "http" && <div className="callout bad"><AlertTriangle aria-hidden /><div>{t("web.http_warn")}</div></div>}
              <ErrorText error={save.error} />
              <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
                <button className="btn" type="button" onClick={() => setConfirm(false)}>{t("common.cancel")}</button>
                <button className="btn primary" type="button" disabled={save.isPending} onClick={() => save.mutate(body)}>{t("web.apply")}</button>
              </div>
            </>
          ) : (
            <>
              {!done && <p className="muted" style={{ margin: 0 }}>{t("web.running", { url: target })}</p>}
              <JobProgress id={jobId} onDone={setDone} />
              {done?.state === "done" && (
                <a className="btn primary" href={target} style={{ alignSelf: "flex-start" }}><ExternalLink /> {t("web.open_new")}</a>
              )}
            </>
          )}
        </Modal>
      )}
    </>
  );
}
