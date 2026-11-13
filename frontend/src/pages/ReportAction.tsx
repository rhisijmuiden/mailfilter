// Public page behind the links in the daily quarantine report. No login: the
// signed token in the URL names one message and one recipient. Opening the
// page changes nothing; only a click on a button does (link scanners in mail
// programs fetch URLs, they do not press buttons).
import { useMutation, useQuery } from "@tanstack/react-query";
import { Ban, Bug, Check, CheckCheck, ShieldCheck } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Logo, ProductName } from "../components/Logo";
import { ErrorText, Loading, formatDate } from "../components/ui";
import { setLanguage, type Lang } from "../i18n";
import { ApiError, api } from "../lib/api";

type Item = { queue_id: string; recipient: string; sender: string; subject: string; received: string; virus: boolean; state: "held" | "gone" };
type Action = "release" | "release_allow" | "block";

export default function ReportAction({ token }: { token: string }) {
  const { t, i18n } = useTranslation();
  const item = useQuery({ queryKey: ["report", token], queryFn: () => api.get<Item>(`/report/${encodeURIComponent(token)}`), retry: false });
  const act = useMutation({ mutationFn: (action: Action) => api.post<{ action: Action; listed: boolean }>(`/report/${encodeURIComponent(token)}`, { action }) });
  const err = item.error instanceof ApiError ? item.error.code : item.error ? "error" : null;

  let body;
  if (item.isLoading) body = <Loading />;
  else if (err) body = <Notice icon={<Ban />} title={t(err === "expired" ? "report.expired" : "report.bad_link")} text={t("report.ask_admin")} />;
  else if (act.data)
    body = (
      <Notice
        icon={act.data.action === "block" ? <Ban /> : <CheckCheck />}
        title={t(`report.done_${act.data.action}`)}
        text={t(`report.done_${act.data.action}_d`, { sender: item.data!.sender, recipient: item.data!.recipient })}
      />
    );
  else if (item.data!.state === "gone") body = <Notice icon={<ShieldCheck />} title={t("report.gone")} text={t("report.gone_d")} />;
  else {
    const d = item.data!;
    body = (
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div>
          <h1 className="display" style={{ margin: 0, fontSize: 24 }}>{t("report.title")}</h1>
          <p className="muted" style={{ margin: "4px 0 0" }}>{t("report.sub", { recipient: d.recipient })}</p>
        </div>
        <dl className="kv">
          <dt>{t("report.from")}</dt>
          <dd style={{ wordBreak: "break-all" }}>{d.sender || t("trace.empty_sender")}</dd>
          <dt>{t("report.subject")}</dt>
          <dd style={{ wordBreak: "break-word" }}>{d.subject || t("report.no_subject")}</dd>
          <dt>{t("report.received")}</dt>
          <dd>{formatDate(d.received, i18n.language)}</dd>
        </dl>
        {d.virus && (
          <div className="callout warn" role="alert">
            <Bug aria-hidden />
            <span>{t("report.virus")}</span>
          </div>
        )}
        <ErrorText error={act.error} />
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {!d.virus && (
            <>
              <button className="btn primary" style={{ justifyContent: "center" }} disabled={act.isPending} onClick={() => act.mutate("release_allow")}>
                <CheckCheck /> {t("report.release_allow")}
              </button>
              <button className="btn" style={{ justifyContent: "center" }} disabled={act.isPending} onClick={() => act.mutate("release")}>
                <Check /> {t("report.release")}
              </button>
            </>
          )}
          <button className="btn danger" style={{ justifyContent: "center" }} disabled={act.isPending || !d.sender} onClick={() => act.mutate("block")}>
            <Ban /> {t("report.block")}
          </button>
        </div>
        <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>{t("report.hint")}</p>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100%", display: "grid", placeItems: "center", padding: 16 }}>
      <div style={{ width: "min(480px,100%)", display: "flex", flexDirection: "column", gap: 20 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div className="brand" style={{ padding: 0 }}>
            <Logo />
            <span className="product"><ProductName /></span>
          </div>
          <div className="seg" role="group" aria-label={t("common.language")}>
            {(["en", "nl"] as Lang[]).map((l) => (
              <button key={l} type="button" aria-pressed={i18n.language === l} onClick={() => setLanguage(l)}>
                {l.toUpperCase()}
              </button>
            ))}
          </div>
        </div>
        <section className="card">{body}</section>
      </div>
    </div>
  );
}

function Notice({ icon, title, text }: { icon: ReactNode; title: string; text: string }) {
  return (
    <div className="card-body" style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
      <span style={{ display: "inline-flex", color: "var(--accent-text)", flex: "none" }}>{icon}</span>
      <div>
        <h1 className="display" style={{ margin: 0, fontSize: 20 }}>{title}</h1>
        <p className="muted" style={{ margin: "6px 0 0" }}>{text}</p>
      </div>
    </div>
  );
}
