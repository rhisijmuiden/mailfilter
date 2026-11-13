import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Logo } from "../components/Logo";
import { ErrorText } from "../components/ui";
import { setLanguage, type Lang } from "../i18n";
import { CodeInput } from "../components/TwoFactor";
import { api, ApiError } from "../lib/api";
import { useBranding } from "../lib/branding";
import type { Me } from "../lib/auth";

export default function Login() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const brand = useBranding();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [challenge, setChallenge] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [useRecovery, setUseRecovery] = useState(false);
  const m = useMutation({
    mutationFn: () => api.post<Me | { two_factor_required: true; challenge: string }>("/auth/login", { email, password }),
    onSuccess: (r) => {
      if ("two_factor_required" in r) {
        setChallenge(r.challenge);
        setCode("");
      } else qc.setQueryData(["me"], r);
    },
  });
  const second = useMutation({
    mutationFn: () => api.post<Me>("/auth/login/2fa", { challenge, code }),
    onSuccess: (me) => qc.setQueryData(["me"], me),
    onError: (e) => {
      if (e instanceof ApiError && e.code === "challenge_expired") {
        setChallenge(null);
        setPassword("");
      }
    },
  });
  return (
    <div style={{ minHeight: "100%", display: "grid", placeItems: "center", padding: 16 }}>
      <div style={{ width: "min(400px,100%)", display: "flex", flexDirection: "column", gap: 20 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div className="brand" style={{ padding: 0 }}>
            <Logo />
            <span className="product">{brand.name}</span>
          </div>
          <div className="seg" role="group" aria-label={t("common.language")}>
            {(["en", "nl"] as Lang[]).map((l) => (
              <button key={l} type="button" aria-pressed={i18n.language === l} onClick={() => setLanguage(l)}>
                {l.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        {challenge ? (
          <form
            className="card"
            onSubmit={(e) => {
              e.preventDefault();
              second.mutate();
            }}
          >
            <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
              <div>
                <h1 className="display" style={{ margin: 0, fontSize: 26 }}>{t("login.twofa_title")}</h1>
                <p className="muted" style={{ margin: "4px 0 0" }}>{useRecovery ? t("login.twofa_recovery_sub") : t("login.twofa_sub")}</p>
              </div>
              <ErrorText error={second.error} />
              <div className="field">
                <label htmlFor="code">{useRecovery ? t("login.recovery_code") : t("twofa.code")}</label>
                {useRecovery ? (
                  <input id="code" className="input mono" autoComplete="off" required autoFocus maxLength={20} placeholder="abcde-fghjk" value={code} onChange={(e) => setCode(e.target.value)} />
                ) : (
                  <CodeInput id="code" value={code} onChange={setCode} autoFocus />
                )}
              </div>
              <button className="btn primary" type="submit" disabled={second.isPending} style={{ justifyContent: "center" }}>
                {second.isPending ? t("login.busy") : t("login.verify")}
              </button>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, flexWrap: "wrap" }}>
                <button type="button" className="btn ghost small" onClick={() => { setUseRecovery(!useRecovery); setCode(""); second.reset(); }}>
                  {useRecovery ? t("login.use_app") : t("login.use_recovery")}
                </button>
                <button type="button" className="btn ghost small" onClick={() => { setChallenge(null); second.reset(); }}>{t("common.back")}</button>
              </div>
            </div>
          </form>
        ) : (
        <form
          className="card"
          onSubmit={(e) => {
            e.preventDefault();
            m.mutate();
          }}
        >
          <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
            <div>
              <h1 className="display" style={{ margin: 0, fontSize: 26 }}>{t("login.title")}</h1>
              <p className="muted" style={{ margin: "4px 0 0" }}>{t("login.sub")}</p>
            </div>
            <ErrorText error={m.error} />
            <div className="field">
              <label htmlFor="email">{t("login.email")}</label>
              <input id="email" className="input" type="email" autoComplete="username" required autoFocus value={email} onChange={(e) => setEmail(e.target.value)} />
            </div>
            <div className="field">
              <label htmlFor="password">{t("login.password")}</label>
              <input id="password" className="input" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
            </div>
            <button className="btn primary" type="submit" disabled={m.isPending} style={{ justifyContent: "center" }}>
              {m.isPending ? t("login.busy") : t("login.submit")}
            </button>
            <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>{t("login.forgot")}</p>
          </div>
        </form>
        )}
      </div>
    </div>
  );
}
