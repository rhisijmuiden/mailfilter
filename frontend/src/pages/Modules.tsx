// System > Modules: switch optional parts of Mailfilter on and off.
// Core filtering is always on; a module that is off leaves the menu and stops its background work.
import { useMutation, useQueryClient } from "@tanstack/react-query";
import type { LucideIcon } from "lucide-react";
import { AlertTriangle, Archive, Boxes, Braces, Cloud, FileChartColumn, FileInput, Flag, FlaskConical, MailCheck, MousePointerClick, Power, Receipt, ShieldCheck } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import GatewayError from "../components/GatewayError";
import { Card, Modal, PageHead, Skeleton, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useModules, type ModuleInfo, type ModuleKey, type ModulesOut } from "../lib/modules";

const GROUPS: { id: string; keys: ModuleKey[] }[] = [
  { id: "reports", keys: ["usage", "monthly_report", "quarantine_report"] },
  { id: "mail", keys: ["spam_reports", "dmarc", "link_protection", "archive", "mailtrap"] },
  { id: "integrations", keys: ["cloud", "api"] },
  { id: "system", keys: ["cluster", "pmg_import"] },
];

const ICONS: Record<ModuleKey, LucideIcon> = {
  usage: Receipt, monthly_report: FileChartColumn, quarantine_report: MailCheck, spam_reports: Flag, dmarc: ShieldCheck,
  link_protection: MousePointerClick, archive: Archive, mailtrap: FlaskConical, cloud: Cloud, api: Braces, cluster: Boxes,
  pmg_import: FileInput,
};

export default function Modules() {
  const { t } = useTranslation();
  const q = useModules();
  const [confirm, setConfirm] = useState<ModuleInfo | null>(null);
  const qc = useQueryClient();
  const toast = useToast();
  const sw = useMutation({
    mutationFn: ({ key, enabled }: { key: ModuleKey; enabled: boolean }) => api.put<ModulesOut>(`/modules/${key}`, { enabled }, { quiet: true }),
    onSuccess: (d, v) => {
      qc.setQueryData(["modules"], d);
      qc.invalidateQueries({ queryKey: ["mailtrap-menu"] });
      qc.invalidateQueries({ queryKey: ["custom-rules"] });
      toast(t(v.enabled ? "modules.switched_on" : "modules.switched_off", { name: t(`modules.m.${v.key}.title`) }), "success");
      setConfirm(null);
    },
  });
  const flip = (m: ModuleInfo, on: boolean) => {
    sw.reset();
    // Switching off something that is in use, or that changes the mail flow, asks first.
    if (!on && (m.in_use > 0 || m.gateway)) setConfirm(m);
    else sw.mutate({ key: m.key, enabled: on });
  };
  const byKey = new Map((q.data?.modules ?? []).map((m) => [m.key, m]));
  const onCount = (q.data?.modules ?? []).filter((m) => m.enabled).length;

  return (
    <>
      <PageHead title={t("modules.title")} sub={t("modules.sub")}
        actions={q.data?.modules && <span className="tag num">{t("modules.count", { on: onCount, total: q.data.modules.length })}</span>} />
      <div className="callout" style={{ marginBottom: 16 }}>
        <Power aria-hidden />
        <div>
          <b>{t("modules.core_title")}</b>
          <div className="muted" style={{ fontSize: 13 }}>{t("modules.core")}</div>
        </div>
      </div>
      {!confirm && sw.error && <div style={{ marginBottom: 16 }}><SwitchError error={sw.error} /></div>}
      {q.isLoading ? <Card><Skeleton rows={6} cols={2} /></Card> : (
        <div className="grid">
          {GROUPS.map((g) => (
            <Card key={g.id} title={t(`modules.group.${g.id}`)}>
              {g.keys.map((k) => byKey.get(k)).filter((m): m is ModuleInfo => !!m).map((m) => {
                const Icon = ICONS[m.key];
                const locked = m.key === "cluster" && m.enabled && !!q.data?.cluster_member;
                return (
                  <div className="setting" key={m.key}>
                    <span className="mod-ico" aria-hidden><Icon /></span>
                    <div className="txt">
                      <b>{t(`modules.m.${m.key}.title`)}</b>
                      <span>{t(`modules.m.${m.key}.desc`)}</span>
                      <div className="mod-tags">
                        {m.gateway && <span className="tag">{t("modules.tag_mailflow")}</span>}
                        {m.per_node && <span className="tag">{t("modules.tag_per_node")}</span>}
                        {m.in_use > 0 && <span className="tag lucht">{t(`modules.in_use.${m.key}`, { count: m.in_use, defaultValue: t("modules.in_use_any") })}</span>}
                        {locked && <span className="tag">{t("modules.cluster_locked")}</span>}
                      </div>
                    </div>
                    <span className={m.enabled ? "st-on" : "st-off"} style={{ fontSize: 12, fontWeight: 650 }}>{t(m.enabled ? "modules.on" : "modules.off_short")}</span>
                    <label className="switch" title={locked ? t("modules.cluster_locked") : undefined}>
                      <input type="checkbox" checked={m.enabled} disabled={locked || (sw.isPending && sw.variables?.key === m.key)}
                        aria-label={t(`modules.m.${m.key}.title`)} onChange={(e) => flip(m, e.target.checked)} />
                      <span />
                    </label>
                  </div>
                );
              })}
            </Card>
          ))}
        </div>
      )}
      {confirm && (
        <Modal title={t("modules.confirm_title", { name: t(`modules.m.${confirm.key}.title`) })} onClose={() => setConfirm(null)} icon={<AlertTriangle />}>
          <p style={{ margin: 0 }}>{t(`modules.m.${confirm.key}.off`)}</p>
          <p className="muted" style={{ margin: 0 }}>{t("modules.data_kept")}</p>
          {sw.error && <SwitchError error={sw.error} />}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", flexWrap: "wrap" }}>
            <button className="btn" type="button" onClick={() => setConfirm(null)}>{t("common.cancel")}</button>
            <button className="btn danger" type="button" disabled={sw.isPending} onClick={() => sw.mutate({ key: confirm.key, enabled: false })}>
              {t("modules.switch_off")}
            </button>
          </div>
        </Modal>
      )}
    </>
  );
}

function SwitchError({ error }: { error: unknown }) {
  const { t } = useTranslation();
  const code = (error as { code?: string })?.code;
  if (code === "cluster_member") {
    return (
      <div className="callout warn" role="alert">
        <AlertTriangle aria-hidden />
        <div>{t("modules.cluster_locked")} <Link to="/system/cluster">{t("nav.cluster")}</Link></div>
      </div>
    );
  }
  return <GatewayError error={error} />;
}
