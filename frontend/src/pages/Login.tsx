import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Logo } from "../components/Logo";
import { ErrorText } from "../components/ui";
import { setLanguage, type Lang } from "../i18n";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";

export default function Login() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const m = useMutation({
    mutationFn: () => api.post<Me>("/auth/login", { email, password }),
    onSuccess: (me) => qc.setQueryData(["me"], me),
  });
  return (
    <div style={{ minHeight: "100%", display: "grid", placeItems: "center", padding: 16 }}>
      <div style={{ width: "min(400px,100%)", display: "flex", flexDirection: "column", gap: 20 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div className="brand" style={{ padding: 0 }}>
            <Logo />
            <span className="product">{t("app.product")}</span>
          </div>
          <div className="seg" role="group" aria-label={t("common.language")}>
            {(["en", "nl"] as Lang[]).map((l) => (
              <button key={l} type="button" aria-pressed={i18n.language === l} onClick={() => setLanguage(l)}>
                {l.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
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
      </div>
    </div>
  );
}
