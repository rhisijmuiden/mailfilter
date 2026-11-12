import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, ErrorText, Field, PageHead, useToast } from "../components/ui";
import { TwoFactorCard } from "../components/TwoFactor";
import { setLanguage } from "../i18n";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";

export default function Account({ me }: { me: Me }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(me.name);
  const [language, setLang] = useState(me.language);
  const [pw, setPw] = useState({ current: "", new: "", repeat: "" });
  const [pwErr, setPwErr] = useState<string | null>(null);

  const profile = useMutation({
    mutationFn: () => api.patch<Me>("/auth/me", { name, language }),
    onSuccess: (m) => {
      qc.setQueryData(["me"], m);
      setLanguage(m.language);
      toast(t("common.saved"));
    },
  });
  const password = useMutation({
    mutationFn: () => api.post("/auth/me/password", { current: pw.current, new: pw.new }),
    onSuccess: () => {
      setPw({ current: "", new: "", repeat: "" });
      toast(t("account.changed"));
    },
  });

  return (
    <>
      <PageHead title={t("account.title")} sub={t("account.sub")} />
      <div className="grid halves">
        <Card title={t("account.profile")}>
          <form
            style={{ display: "flex", flexDirection: "column", gap: 16 }}
            onSubmit={(e) => {
              e.preventDefault();
              profile.mutate();
            }}
          >
            <ErrorText error={profile.error} />
            <Field label={t("account.email")}>
              <input className="input" value={me.email} disabled />
            </Field>
            <Field label={t("account.name")}>
              <input className="input" value={name} maxLength={200} onChange={(e) => setName(e.target.value)} />
            </Field>
            <Field label={t("account.language")}>
              <select className="select" value={language} onChange={(e) => setLang(e.target.value as Me["language"])}>
                <option value="en">{t("lang.en")}</option>
                <option value="nl">{t("lang.nl")}</option>
              </select>
            </Field>
            <dl className="kv">
              <dt>{t("account.role")}</dt>
              <dd>{t(`users.${me.role}`)}</dd>
              <dt>{t("account.domains")}</dt>
              <dd>{me.role === "admin" ? t("users.all_domains") : me.domains.join(", ") || t("common.none")}</dd>
            </dl>
            <div>
              <button className="btn primary" disabled={profile.isPending}>{t("common.save")}</button>
            </div>
          </form>
        </Card>
        <Card title={t("account.password")}>
          <form
            style={{ display: "flex", flexDirection: "column", gap: 16 }}
            onSubmit={(e) => {
              e.preventDefault();
              if (pw.new !== pw.repeat) return setPwErr(t("account.mismatch"));
              setPwErr(null);
              password.mutate();
            }}
          >
            <ErrorText error={password.error} />
            {pwErr && <div className="callout warn" role="alert">{pwErr}</div>}
            <Field label={t("account.current")}>
              <input className="input" type="password" autoComplete="current-password" required value={pw.current} onChange={(e) => setPw({ ...pw, current: e.target.value })} />
            </Field>
            <Field label={t("account.new")} hint={t("account.min")}>
              <input className="input" type="password" autoComplete="new-password" minLength={10} required value={pw.new} onChange={(e) => setPw({ ...pw, new: e.target.value })} />
            </Field>
            <Field label={t("account.repeat")}>
              <input className="input" type="password" autoComplete="new-password" minLength={10} required value={pw.repeat} onChange={(e) => setPw({ ...pw, repeat: e.target.value })} />
            </Field>
            <div>
              <button className="btn primary" disabled={password.isPending}>{t("common.save")}</button>
            </div>
          </form>
        </Card>
        <TwoFactorCard me={me} />
      </div>
    </>
  );
}
