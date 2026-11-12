import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Gauge, Plus, ShieldAlert, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import GatewayError from "../components/GatewayError";
import { Card, Loading, PageHead, useToast } from "../components/ui";
import { api } from "../lib/api";
import type { AbuseSettings, Defaults, Throttling as T } from "../lib/gateway";
import { numOrNull, numStr } from "../lib/gateway";

const LIMITS: (keyof Defaults)[] = ["relay_ip_per_minute", "relay_ip_per_hour", "domain_per_hour", "domain_per_day", "user_per_hour", "concurrency"];

export default function Throttling() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const data = useQuery({ queryKey: ["gw-throttling"], queryFn: () => api.get<T>("/gateway/throttling") });
  const [f, setF] = useState<T | null>(null);
  useEffect(() => { if (data.data) setF(structuredClone(data.data)); }, [data.data]);
  const save = useMutation({
    mutationFn: () => api.put<T>("/gateway/throttling", f),
    onSuccess: (res) => {
      qc.setQueryData(["gw-throttling"], res);
      qc.invalidateQueries({ queryKey: ["gw-status"] });
      toast(t("gateway.applied"));
    },
  });
  if (!f) return <><PageHead title={t("throttling.title")} sub={t("throttling.sub")} /><Loading /></>;
  const setPolicy = (i: number, patch: object) => setF({ ...f, policies: f.policies.map((p, j) => (j === i ? { ...p, ...patch } : p)) });

  return (
    <>
    <form style={{ display: "contents" }} onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <PageHead
        title={t("throttling.title")}
        sub={t("throttling.sub")}
        actions={<button className="btn primary" disabled={save.isPending}><Gauge /> {save.isPending ? t("gateway.applying") : t("common.save")}</button>}
      />
      <GatewayError error={save.error} />
      <Card title={t("throttling.defaults")} right={<span className="muted">{t("throttling.defaults_sub")}</span>}>
        {LIMITS.map((k) => (
          <div className="setting" key={k}>
            <div className="txt">
              <b>{t(`throttling.${k}`)}</b>
              <span>{t(`throttling.${k}_hint`)}</span>
            </div>
            <input className="input" style={{ width: 130 }} type="number" min={1} max={k === "concurrency" ? 100 : 10_000_000} required
              aria-label={t(`throttling.${k}`)} value={f.defaults[k]}
              onChange={(e) => setF({ ...f, defaults: { ...f.defaults, [k]: Number(e.target.value) } })} />
          </div>
        ))}
      </Card>
      <Card title={t("throttling.destinations")} right={<span className="muted">{t("throttling.destinations_sub")}</span>} flush>
        {f.policies.length === 0 ? (
          <div className="empty">{t("throttling.destinations_empty")}</div>
        ) : (
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("throttling.domain")}</th>
                  <th>{t("throttling.concurrency_col")}</th>
                  <th>{t("throttling.delay_col")}</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {f.policies.map((p, i) => (
                  <tr key={p.id ?? `new-${i}`}>
                    <td><input className="input" required placeholder="gmail.com" aria-label={t("throttling.domain")} value={p.domain} onChange={(e) => setPolicy(i, { domain: e.target.value })} /></td>
                    <td><input className="input" style={{ width: 110 }} type="number" min={1} max={100} placeholder={t("domains.default")} aria-label={t("throttling.concurrency_col")}
                      value={numStr(p.concurrency)} onChange={(e) => setPolicy(i, { concurrency: numOrNull(e.target.value) })} /></td>
                    <td><input className="input" style={{ width: 110 }} type="number" min={0} max={3600} placeholder="0" aria-label={t("throttling.delay_col")}
                      value={numStr(p.rate_delay)} onChange={(e) => setPolicy(i, { rate_delay: numOrNull(e.target.value) })} /></td>
                    <td style={{ textAlign: "right" }}>
                      <button type="button" className="icon-btn" aria-label={t("common.delete")} onClick={() => setF({ ...f, policies: f.policies.filter((_, j) => j !== i) })}><Trash2 /></button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className="filters" style={{ borderBottom: 0 }}>
          <button type="button" className="btn small" onClick={() => setF({ ...f, policies: [...f.policies, { domain: "", concurrency: null, rate_delay: null, note: "" }] })}>
            <Plus /> {t("throttling.add_destination")}
          </button>
          <span className="muted" style={{ fontSize: 12.5 }}>{t("throttling.domain_limits_hint")}</span>
        </div>
      </Card>
    </form>
    <Protection />
    </>
  );
}

const PROTECTION: { key: keyof AbuseSettings; min: number; max: number }[] = [
  { key: "window_minutes", min: 5, max: 240 },
  { key: "spam_min", min: 1, max: 100000 },
  { key: "spam_percent", min: 1, max: 100 },
  { key: "volume_min", min: 10, max: 10000000 },
  { key: "volume_factor", min: 2, max: 1000 },
  { key: "quiet_hours", min: 0, max: 168 },
];

/** Automatic protection: pause a sender that suddenly sends spam or far more than usual. */
function Protection() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const data = useQuery({ queryKey: ["gw-abuse"], queryFn: () => api.get<AbuseSettings>("/gateway/abuse-settings") });
  const [f, setF] = useState<AbuseSettings | null>(null);
  useEffect(() => { if (data.data) setF(structuredClone(data.data)); }, [data.data]);
  const save = useMutation({
    mutationFn: () => api.put<AbuseSettings>("/gateway/abuse-settings", f),
    onSuccess: (res) => { qc.setQueryData(["gw-abuse"], res); toast(t("protection.saved")); },
  });
  if (!f) return null;
  const list = (v: string) => v.split(/[\s,;]+/).filter(Boolean);
  return (
    <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
      <Card title={<span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><ShieldAlert style={{ width: 18, height: 18 }} aria-hidden />{t("protection.title")}</span>}
        right={<button className="btn primary small" disabled={save.isPending}>{t("common.save")}</button>}>
        <p className="muted" style={{ marginTop: 0 }}>{t("protection.sub")}</p>
        <GatewayError error={save.error} />
        <div className="setting">
          <div className="txt"><b>{t("protection.enabled")}</b><span>{t("protection.enabled_hint")}</span></div>
          <label className="switch" style={{ display: "inline-block" }}>
            <input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} aria-label={t("protection.enabled")} />
            <span />
          </label>
        </div>
        {PROTECTION.map(({ key, min, max }) => (
          <div className="setting" key={key}>
            <div className="txt"><b>{t(`protection.${key}`)}</b><span>{t(`protection.${key}_hint`)}</span></div>
            <input className="input" style={{ width: 130 }} type="number" min={min} max={max} required disabled={!f.enabled}
              aria-label={t(`protection.${key}`)} value={f[key] as number}
              onChange={(e) => setF({ ...f, [key]: Number(e.target.value) })} />
          </div>
        ))}
        {(["exempt", "notify"] as const).map((key) => (
          <div className="setting" key={key}>
            <div className="txt"><b>{t(`protection.${key}`)}</b><span>{t(`protection.${key}_hint`)}</span></div>
            <textarea className="input mono" style={{ width: 300, minHeight: 64 }} aria-label={t(`protection.${key}`)}
              defaultValue={f[key].join("\n")} onChange={(e) => setF({ ...f, [key]: list(e.target.value) })} />
          </div>
        ))}
      </Card>
    </form>
  );
}
