// Two-factor login (authenticator app) for the logged-in user: set up, show the
// recovery codes once, make new codes, turn off.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Copy, Download, KeyRound, ShieldCheck, ShieldOff } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";
import { Card, ErrorText, Field, Loading, formatDate, useToast } from "./ui";

type Status = { enabled: boolean; enabled_at: string | null; recovery_codes_left: number; required: boolean };
type Setup = { secret: string; uri: string; qr_svg: string };

function CodeInput({ value, onChange, id, autoFocus }: { value: string; onChange: (v: string) => void; id: string; autoFocus?: boolean }) {
  return (
    <input
      id={id}
      className="input mono"
      inputMode="numeric"
      autoComplete="one-time-code"
      pattern="[0-9 ]{6,7}"
      maxLength={7}
      required
      autoFocus={autoFocus}
      placeholder="123456"
      value={value}
      onChange={(e) => onChange(e.target.value.replace(/[^0-9 ]/g, ""))}
      style={{ letterSpacing: "0.25em", fontSize: 18, maxWidth: 200 }}
    />
  );
}

export function RecoveryCodes({ codes, onDone }: { codes: string[]; onDone: () => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const text = codes.join("\n");
  const download = () => {
    const url = URL.createObjectURL(new Blob([`${t("twofa.codes_file")}\n\n${text}\n`], { type: "text/plain" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "mailfilter-recovery-codes.txt";
    a.click();
    URL.revokeObjectURL(url);
  };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <div className="callout warn"><span>{t("twofa.codes_once")}</span></div>
      <div className="mono" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(130px,1fr))", gap: 8, fontSize: 15 }}>
        {codes.map((c) => (
          <span key={c} className="tag" style={{ fontSize: 14, padding: "6px 10px", textAlign: "center", fontFamily: "var(--mono)" }}>{c}</span>
        ))}
      </div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="button" className="btn" onClick={() => navigator.clipboard.writeText(text).then(() => toast(t("common.copied")))}>
          <Copy /> {t("common.copy")}
        </button>
        <button type="button" className="btn" onClick={download}>
          <Download /> {t("twofa.download")}
        </button>
        <button type="button" className="btn primary" onClick={onDone} style={{ marginLeft: "auto" }}>{t("twofa.codes_saved")}</button>
      </div>
    </div>
  );
}

export function TwoFactorCard({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: ["2fa"], queryFn: () => api.get<Status>("/auth/2fa") });
  const [setup, setSetup] = useState<Setup | null>(null);
  const [codes, setCodes] = useState<string[] | null>(null);
  const [mode, setMode] = useState<"none" | "renew" | "disable">("none");
  const [code, setCode] = useState("");
  const [password, setPassword] = useState("");

  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["2fa"] });
    qc.invalidateQueries({ queryKey: ["me"] });
  };
  const start = useMutation({ mutationFn: () => api.post<Setup>("/auth/2fa/setup"), onSuccess: (s) => { setSetup(s); setCode(""); } });
  const enable = useMutation({
    mutationFn: () => api.post<{ codes: string[] }>("/auth/2fa/enable", { code }),
    onSuccess: (r) => { setSetup(null); setCode(""); setCodes(r.codes); qc.invalidateQueries({ queryKey: ["2fa"] }); },
  });
  const renew = useMutation({
    mutationFn: () => api.post<{ codes: string[] }>("/auth/2fa/recovery-codes", { code }),
    onSuccess: (r) => { setMode("none"); setCode(""); setCodes(r.codes); qc.invalidateQueries({ queryKey: ["2fa"] }); },
  });
  const disable = useMutation({
    mutationFn: () => api.post("/auth/2fa/disable", { code, password }),
    onSuccess: () => { setMode("none"); setCode(""); setPassword(""); refresh(); toast(t("twofa.disabled_toast")); },
  });

  const s = status.data;
  const title = (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}>
      {t("twofa.title")}
      {s && (s.enabled ? <span className="status s-ok"><ShieldCheck /> {t("twofa.on")}</span> : <span className="status s-mute">{t("twofa.off")}</span>)}
    </span>
  );

  if (status.isLoading || !s) return <Card title={title}><Loading /></Card>;

  if (codes)
    return (
      <Card title={title}>
        <RecoveryCodes codes={codes} onDone={() => { setCodes(null); refresh(); toast(t("twofa.enabled_toast")); }} />
      </Card>
    );

  if (setup)
    return (
      <Card title={title}>
        <form
          style={{ display: "flex", flexDirection: "column", gap: 16 }}
          onSubmit={(e) => { e.preventDefault(); enable.mutate(); }}
        >
          <ol className="muted" style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 4 }}>
            <li>{t("twofa.step_app")}</li>
            <li>{t("twofa.step_scan")}</li>
            <li>{t("twofa.step_code")}</li>
          </ol>
          <div style={{ display: "flex", gap: 20, flexWrap: "wrap", alignItems: "center" }}>
            <a href={setup.uri} aria-label={t("twofa.qr")} style={{ background: "#fff", borderRadius: 12, padding: 6, lineHeight: 0, border: "1px solid var(--line)" }}
              dangerouslySetInnerHTML={{ __html: setup.qr_svg }} />
            <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
              <span className="muted" style={{ fontSize: 12.5 }}>{t("twofa.manual")}</span>
              <code className="mono" style={{ wordBreak: "break-all", fontSize: 14 }}>{setup.secret.replace(/(.{4})/g, "$1 ").trim()}</code>
              <button type="button" className="btn small" style={{ alignSelf: "flex-start" }}
                onClick={() => navigator.clipboard.writeText(setup.secret).then(() => toast(t("common.copied")))}>
                <Copy /> {t("common.copy")}
              </button>
            </div>
          </div>
          <ErrorText error={enable.error} />
          <Field label={t("twofa.code")}>
            <CodeInput id="totp-enable" value={code} onChange={setCode} autoFocus />
          </Field>
          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn primary" disabled={enable.isPending}><ShieldCheck /> {t("twofa.confirm")}</button>
            <button type="button" className="btn ghost" onClick={() => setSetup(null)}>{t("common.cancel")}</button>
          </div>
        </form>
      </Card>
    );

  if (!s.enabled)
    return (
      <Card title={title}>
        <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
          <p className="muted" style={{ margin: 0 }}>{t("twofa.intro")}</p>
          {s.required && <div className="callout warn"><span>{t("twofa.required_notice")}</span></div>}
          <ErrorText error={start.error} />
          <div>
            <button className="btn primary" onClick={() => start.mutate()} disabled={start.isPending}><KeyRound /> {t("twofa.setup")}</button>
          </div>
        </div>
      </Card>
    );

  return (
    <Card title={title}>
      <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <dl className="kv">
          <dt>{t("twofa.since")}</dt>
          <dd>{formatDate(s.enabled_at, i18n.language)}</dd>
          <dt>{t("twofa.codes_left")}</dt>
          <dd>
            <span className={`status ${s.recovery_codes_left > 2 ? "s-mute" : "s-warn"}`}>{s.recovery_codes_left}</span>
          </dd>
        </dl>
        {s.required && <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>{t("twofa.required_by_admin")}</p>}
        {mode === "none" ? (
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn" onClick={() => { setMode("renew"); setCode(""); }}><KeyRound /> {t("twofa.renew")}</button>
            {!s.required && (
              <button className="btn danger" onClick={() => { setMode("disable"); setCode(""); }}><ShieldOff /> {t("twofa.disable")}</button>
            )}
          </div>
        ) : (
          <form
            style={{ display: "flex", flexDirection: "column", gap: 14 }}
            onSubmit={(e) => { e.preventDefault(); (mode === "renew" ? renew : disable).mutate(); }}
          >
            <p className="muted" style={{ margin: 0 }}>{mode === "renew" ? t("twofa.renew_hint") : t("twofa.disable_hint")}</p>
            <ErrorText error={mode === "renew" ? renew.error : disable.error} />
            {mode === "disable" && (
              <Field label={t("account.current")}>
                <input className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
              </Field>
            )}
            <Field label={t("twofa.code_or_recovery")}>
              <input className="input mono" autoComplete="one-time-code" required maxLength={20} value={code} onChange={(e) => setCode(e.target.value)} style={{ maxWidth: 220 }} autoFocus />
            </Field>
            <div style={{ display: "flex", gap: 8 }}>
              <button className={`btn ${mode === "disable" ? "danger" : "primary"}`} disabled={renew.isPending || disable.isPending}>
                {mode === "renew" ? t("twofa.renew") : t("twofa.disable")}
              </button>
              <button type="button" className="btn ghost" onClick={() => setMode("none")}>{t("common.cancel")}</button>
            </div>
          </form>
        )}
        {me.role === "admin" && <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>{t("twofa.admin_tip")}</p>}
      </div>
    </Card>
  );
}

export { CodeInput };
