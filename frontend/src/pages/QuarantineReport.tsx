import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { MailCheck, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, ErrorText, Field, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";

type Config = { enabled: boolean; hour: number; language: "en" | "nl"; only_new: boolean };
type Settings = { config: Config | null; domains: { name: string; enabled: boolean }[]; last_sent: string | null; timezone: string };

export default function QuarantineReport({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const s = useQuery({ queryKey: ["qreport"], queryFn: () => api.get<Settings>("/quarantine-report/settings") });
  const [cfg, setCfg] = useState<Config | null>(null);
  useEffect(() => {
    if (s.data?.config) setCfg(s.data.config);
  }, [s.data]);
  const save = useMutation({
    mutationFn: (body: Partial<{ config: Config; domains: Settings["domains"] }>) => api.put<Settings>("/quarantine-report/settings", body),
    onSuccess: (data) => {
      qc.setQueryData(["qreport"], data);
      toast(t("common.saved"));
    },
  });
  const example = useMutation({
    mutationFn: () => api.post<{ sent_to: string; messages: number }>("/quarantine-report/example"),
    onSuccess: (r) => toast(t("qreport.example_sent", { to: r.sent_to, count: r.messages })),
  });
  if (s.isLoading || !s.data) return <Loading />;
  const admin = me.role === "admin";
  const hours = Array.from({ length: 24 }, (_, h) => h);

  return (
    <>
      <PageHead
        title={t("qreport.title")}
        sub={t("qreport.sub")}
        actions={
          <button className="btn" disabled={example.isPending} onClick={() => example.mutate()}>
            <Send /> {example.isPending ? t("qreport.sending") : t("qreport.example")}
          </button>
        }
      />
      <ErrorText error={example.error || save.error} />
      <div className="grid two">
        {admin && cfg && (
          <Card title={t("qreport.schedule")}>
            <form className="form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate({ config: cfg }); }}>
              <label className="check full">
                <input type="checkbox" checked={cfg.enabled} onChange={(e) => setCfg({ ...cfg, enabled: e.target.checked })} />
                <span>
                  <b style={{ display: "block" }}>{t("qreport.enabled")}</b>
                  <span className="muted" style={{ fontSize: 12.5 }}>{t("qreport.enabled_hint")}</span>
                </span>
              </label>
              <Field label={t("qreport.hour")} hint={t("qreport.hour_hint", { tz: s.data.timezone })}>
                <select className="select" value={cfg.hour} onChange={(e) => setCfg({ ...cfg, hour: +e.target.value })}>
                  {hours.map((h) => <option key={h} value={h}>{String(h).padStart(2, "0")}:00</option>)}
                </select>
              </Field>
              <Field label={t("qreport.language")} hint={t("qreport.language_hint")}>
                <select className="select" value={cfg.language} onChange={(e) => setCfg({ ...cfg, language: e.target.value as Config["language"] })}>
                  <option value="en">English</option>
                  <option value="nl">Nederlands</option>
                </select>
              </Field>
              <label className="check full">
                <input type="checkbox" checked={cfg.only_new} onChange={(e) => setCfg({ ...cfg, only_new: e.target.checked })} />
                <span>
                  <b style={{ display: "block" }}>{t("qreport.only_new")}</b>
                  <span className="muted" style={{ fontSize: 12.5 }}>{t("qreport.only_new_hint")}</span>
                </span>
              </label>
              <div className="full" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
                <button className="btn primary" disabled={save.isPending}>{t("common.save")}</button>
                <span className="muted" style={{ fontSize: 12.5 }}>
                  {s.data.last_sent ? t("qreport.last_sent", { when: formatDate(s.data.last_sent, i18n.language) }) : t("qreport.never_sent")}
                </span>
              </div>
            </form>
          </Card>
        )}
        <Card title={t("qreport.domains")} right={<span className="muted">{t("qreport.domains_sub")}</span>} flush>
          {!s.data.domains.length ? (
            <div className="empty">{t("qreport.no_domains")}</div>
          ) : (
            <div>
              {s.data.domains.map((d) => (
                <div key={d.name} className="list-row" style={{ padding: "10px 16px" }}>
                  <MailCheck size={16} aria-hidden />
                  <span className="grow">{d.name}</span>
                  <label className="switch" title={d.enabled ? t("relay.on") : t("relay.off")}>
                    <input type="checkbox" checked={d.enabled} aria-label={t("qreport.domain_toggle", { domain: d.name })}
                      onChange={() => save.mutate({ domains: [{ name: d.name, enabled: !d.enabled }] })} />
                    <span />
                  </label>
                </div>
              ))}
            </div>
          )}
          {admin && cfg && !cfg.enabled && (
            <div style={{ padding: "0 16px 14px" }}>
              <p className="muted" style={{ fontSize: 12.5, margin: 0 }}>{t("qreport.off_globally")}</p>
            </div>
          )}
        </Card>
      </div>
      <Card title={t("qreport.how")}>
        <ul style={{ margin: 0, paddingLeft: 18, lineHeight: 1.6 }}>
          <li>{t("qreport.how_1")}</li>
          <li>{t("qreport.how_2")}</li>
          <li>{t("qreport.how_3")}</li>
          <li>{t("qreport.how_4")}</li>
        </ul>
      </Card>
    </>
  );
}
