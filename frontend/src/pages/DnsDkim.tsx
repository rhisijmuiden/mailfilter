// DKIM keys tab of the DNS toolbox: the key every domain signs with, its age,
// and automatic rotation (new key, publish, switch, old key removed after an
// overlap). The work runs on the server every six hours; "Check now" runs it
// right away.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Cloud, Info, KeyRound, RefreshCw, RotateCw, X } from "lucide-react";
import { Fragment, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, ErrorText, Loading, formatDate, useToast } from "../components/ui";
import { api } from "../lib/api";
import { Code, CopyBtn } from "./Dns";

type Phase = "publish" | "wait" | "overlap";
type Rotation = {
  phase: Phase; old: string; new: string; auto: boolean; started_at: string; seen_at: string | null; switched_at: string | null;
  error: string; dns: string | null; new_name: string; new_record: string; switch_after: string | null; finish_after: string | null;
};
type Row = {
  domain: string; kind: "transport" | "relay"; selector: string | null; created: string | null; due: string | null;
  cloudflare: boolean; record: string | null; rotation: Rotation | null;
};
type Settings = { enabled: boolean; interval_months: number; overlap_days: number; publish_wait_hours: number; manual_dns: boolean };
type History = { domain: string; old: string; new: string; started_at: string; finished_at: string; old_record: "removed" | "manual"; auto: boolean };
type Overview = {
  config: Settings; leader: string | null; is_leader: boolean; cloudflare: boolean; cloudflare_error: string; error: string;
  domains: Row[]; history: History[];
};

const enc = encodeURIComponent;
const STEPS: Phase[] = ["publish", "wait", "overlap"];

function useOverview() {
  return useQuery({
    queryKey: ["dkim"], queryFn: () => api.get<Overview>("/dkim"),
    // While a new record is being published, look again every 30 seconds.
    refetchInterval: (q) => (q.state.data?.domains.some((d) => d.rotation?.phase === "publish") ? 30_000 : false),
  });
}

function age(iso: string | null): number | null {
  return iso ? Math.floor((Date.now() - new Date(iso).getTime()) / 86_400_000) : null;
}

function StatePill({ row, enabled }: { row: Row; enabled: boolean }) {
  const { t } = useTranslation();
  const r = row.rotation;
  if (!row.selector) return <span className="status s-bad"><X />{t("dkimrot.state.no_key")}</span>;
  if (r?.error) return <span className="status s-bad"><X />{t(`dkimrot.phase.${r.phase}`)}</span>;
  if (r) return <span className="status s-info"><RotateCw />{t(`dkimrot.phase.${r.phase}`)}</span>;
  const due = row.due && new Date(row.due).getTime() <= Date.now();
  if (due && enabled) return <span className="status s-warn"><RotateCw />{t(row.cloudflare ? "dkimrot.state.due" : "dkimrot.state.due_manual")}</span>;
  return <span className="status s-ok"><Check />{t("dkimrot.state.ok")}</span>;
}

function Timeline({ r }: { r: Rotation }) {
  const { t, i18n } = useTranslation();
  const at = STEPS.indexOf(r.phase);
  const when: Record<Phase, string | null> = { publish: r.started_at, wait: r.seen_at, overlap: r.switched_at };
  return (
    <ol className="dkimrot-steps">
      {STEPS.map((s, i) => (
        <li key={s} className={i < at ? "done" : i === at ? "now" : ""}>
          <b>{t(`dkimrot.step.${s}`)}</b>
          <span>{when[s] ? formatDate(when[s], i18n.language) : "–"}</span>
        </li>
      ))}
      <li className="">
        <b>{t("dkimrot.step.done")}</b>
        <span>{r.finish_after ? `${t("dkimrot.from")} ${formatDate(r.finish_after, i18n.language, false)}` : "–"}</span>
      </li>
    </ol>
  );
}

function Detail({ row, cfg }: { row: Row; cfg: Settings }) {
  const { t, i18n } = useTranslation();
  const r = row.rotation!;
  const help = r.phase === "publish"
    ? (r.auto ? t("dkimrot.help.publish_auto") : t("dkimrot.help.publish_manual"))
    : r.phase === "wait"
      ? t("dkimrot.help.wait", { date: r.switch_after ? formatDate(r.switch_after, i18n.language) : "–", hours: cfg.publish_wait_hours })
      : t(r.auto ? "dkimrot.help.overlap_auto" : "dkimrot.help.overlap_manual",
          { date: r.finish_after ? formatDate(r.finish_after, i18n.language, false) : "–", old: `${r.old}._domainkey.${row.domain}` });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: "4px 0 8px" }}>
      <Timeline r={r} />
      <p className="cell-sub" style={{ margin: 0 }}>{help}</p>
      {r.error && <div className="callout warn" role="alert"><X aria-hidden /><span>{t(`dkimrot.error.${r.error}`, { defaultValue: r.error })}</span></div>}
      {r.phase === "publish" && (
        <div>
          <div className="cell-sub" style={{ fontWeight: 650, marginBottom: 4 }}>
            {t("dkimrot.new_record")} · <span className="mono">{r.new_name}</span> · TXT
            {r.dns && r.dns !== "ok" && <> · {t(`dkimrot.dns.${r.dns}`, { defaultValue: r.dns })}</>}
          </div>
          <Code>{r.new_record}</Code>
          <div style={{ display: "flex", gap: 6, marginTop: 6 }}>
            <CopyBtn text={r.new_name} />
            <CopyBtn text={r.new_record} />
          </div>
        </div>
      )}
    </div>
  );
}

function SettingsCard({ data }: { data: Overview }) {
  const { t } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const [f, setF] = useState<Settings>(data.config);
  useEffect(() => setF(data.config), [data.config]);
  const set = <K extends keyof Settings>(k: K, v: Settings[K]) => setF((x) => ({ ...x, [k]: v }));
  const save = useMutation({
    mutationFn: () => api.put<Settings>("/dkim/settings", f),
    onSuccess: () => { toast(t("common.saved")); qc.invalidateQueries({ queryKey: ["dkim"] }); },
  });
  return (
    <Card title={t("dkimrot.settings")}>
      <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="setting">
          <div className="txt"><b>{t("dkimrot.enabled")}</b><span>{t("dkimrot.enabled_d")}</span></div>
          <label className="switch" style={{ display: "inline-block" }}>
            <input type="checkbox" checked={f.enabled} aria-label={t("dkimrot.enabled")} onChange={(e) => set("enabled", e.target.checked)} />
            <span />
          </label>
        </div>
        <div className="setting">
          <div className="txt"><b>{t("dkimrot.interval")}</b><span>{t("dkimrot.interval_d")}</span></div>
          <select className="input" style={{ width: 150 }} value={f.interval_months} aria-label={t("dkimrot.interval")}
            onChange={(e) => set("interval_months", Number(e.target.value))}>
            {[3, 6, 12, 24].map((m) => <option key={m} value={m}>{t("dkimrot.months", { count: m })}</option>)}
          </select>
        </div>
        <div className="setting">
          <div className="txt"><b>{t("dkimrot.wait")}</b><span>{t("dkimrot.wait_d")}</span></div>
          <input className="input" type="number" min={1} max={336} style={{ width: 90 }} value={f.publish_wait_hours}
            aria-label={t("dkimrot.wait")} onChange={(e) => set("publish_wait_hours", Number(e.target.value))} />
        </div>
        <div className="setting">
          <div className="txt"><b>{t("dkimrot.overlap")}</b><span>{t("dkimrot.overlap_d")}</span></div>
          <input className="input" type="number" min={2} max={60} style={{ width: 90 }} value={f.overlap_days}
            aria-label={t("dkimrot.overlap")} onChange={(e) => set("overlap_days", Number(e.target.value))} />
        </div>
        <div className="setting">
          <div className="txt"><b>{t("dkimrot.manual_dns")}</b><span>{t("dkimrot.manual_dns_d")}</span></div>
          <label className="switch" style={{ display: "inline-block" }}>
            <input type="checkbox" checked={f.manual_dns} aria-label={t("dkimrot.manual_dns")} onChange={(e) => set("manual_dns", e.target.checked)} />
            <span />
          </label>
        </div>
        <ErrorText error={save.error} />
        <div style={{ marginTop: 12 }}>
          <button className="btn primary" disabled={save.isPending}>{t("common.save")}</button>
        </div>
      </form>
    </Card>
  );
}

export default function DkimTab() {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const qc = useQueryClient();
  const q = useOverview();
  const [open, setOpen] = useState<string | null>(null);
  const refresh = (d: Overview) => qc.setQueryData(["dkim"], d);
  const run = useMutation({
    mutationFn: () => api.post<{ ran: boolean; errors: string[] }>("/dkim/run"),
    onSuccess: (r) => { toast(r.errors.length ? t("dkimrot.run_errors", { count: r.errors.length }) : t("dkimrot.run_done")); qc.invalidateQueries({ queryKey: ["dkim"] }); },
  });
  const rotate = useMutation({
    mutationFn: (d: string) => api.post<Overview>(`/dkim/${enc(d)}/rotate`),
    onSuccess: (r, d) => { refresh(r); setOpen(d); toast(t("dkimrot.started", { domain: d })); },
  });
  const cancel = useMutation({
    mutationFn: (d: string) => api.post<Overview>(`/dkim/${enc(d)}/cancel`),
    onSuccess: (r) => { refresh(r); toast(t("dkimrot.cancelled")); },
  });

  if (q.isLoading) return <Loading />;
  if (q.error || !q.data) return <ErrorText error={q.error} />;
  const d = q.data;
  const readOnly = !d.is_leader;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <p className="cell-sub" style={{ margin: 0 }}>{t("dkimrot.help.intro", { months: d.config.interval_months, days: d.config.overlap_days })}</p>
      {readOnly && <div className="callout"><Info aria-hidden /><span>{t("dkimrot.not_leader", { node: d.leader })}</span></div>}
      {!d.cloudflare && (
        <div className="callout warn"><Cloud aria-hidden /><span>{d.cloudflare_error ? t("dkimrot.cf_error", { error: d.cloudflare_error }) : t("dkimrot.no_cloudflare")}</span></div>
      )}
      {d.error && <div className="callout warn" role="alert"><X aria-hidden /><span>{t("dkimrot.helper_error", { error: d.error })}</span></div>}

      <Card title={<span style={{ display: "inline-flex", gap: 8, alignItems: "center" }}><KeyRound size={16} />{t("dkimrot.keys")}</span>}
        right={!readOnly && (
          <button type="button" className="btn small" disabled={run.isPending} onClick={() => run.mutate()}>
            <RefreshCw /> {t("dkimrot.run")}
          </button>
        )} flush>
        <ErrorText error={run.error || rotate.error || cancel.error} />
        {d.domains.length === 0 ? <div className="empty">{t("dkimrot.empty")}</div> : (
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("dkimrot.col.domain")}</th><th>{t("dkimrot.col.selector")}</th><th>{t("dkimrot.col.age")}</th>
                  <th>{t("dkimrot.col.next")}</th><th>{t("dkimrot.col.status")}</th><th />
                </tr>
              </thead>
              <tbody>
                {d.domains.map((row) => {
                  const days = age(row.created);
                  const r = row.rotation;
                  return (
                    <Fragment key={row.domain}>
                      <tr className={r ? "click" : ""} onClick={() => r && setOpen(open === row.domain ? null : row.domain)}>
                        <td>
                          <span className="cell-main">{row.domain}</span>
                          <span className="cell-sub" style={{ display: "flex", gap: 6, alignItems: "center" }}>
                            {t(`dkimrot.kind.${row.kind}`)}
                            {row.cloudflare && <span title={t("dkimrot.cloudflare")} style={{ display: "inline-flex" }}><Cloud size={13} /></span>}
                          </span>
                        </td>
                        <td className="mono nowrap">{row.selector ?? "–"}{r && <span className="muted"> → {r.new}</span>}</td>
                        <td className="nowrap">{days === null ? "–" : t("dkimrot.days", { count: days })}</td>
                        <td className="nowrap">{r ? "–" : !d.config.enabled ? <span className="muted">{t("dkimrot.off")}</span> : row.due ? formatDate(row.due, i18n.language, false) : "–"}</td>
                        <td className="nowrap"><StatePill row={row} enabled={d.config.enabled} /></td>
                        <td className="nowrap" style={{ textAlign: "right" }} onClick={(e) => e.stopPropagation()}>
                          {!readOnly && row.selector && !r && (
                            <button type="button" className="btn small ghost" disabled={rotate.isPending}
                              onClick={() => window.confirm(t("dkimrot.rotate_confirm", { domain: row.domain })) && rotate.mutate(row.domain)}>
                              <RotateCw /> {t("dkimrot.rotate")}
                            </button>
                          )}
                          {!readOnly && r && r.phase !== "overlap" && (
                            <button type="button" className="btn small ghost" disabled={cancel.isPending}
                              onClick={() => window.confirm(t("dkimrot.cancel_confirm")) && cancel.mutate(row.domain)}>
                              <X /> {t("dkimrot.cancel")}
                            </button>
                          )}
                        </td>
                      </tr>
                      {r && open === row.domain && (
                        <tr><td colSpan={6} style={{ background: "var(--surface-sunk)" }}><Detail row={row} cfg={d.config} /></td></tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </Card>

      {!readOnly && <SettingsCard data={d} />}

      {d.history.length > 0 && (
        <Card title={t("dkimrot.history")} flush>
          <div className="tbl-wrap">
            <table>
              <thead><tr><th>{t("dkimrot.col.domain")}</th><th>{t("dkimrot.col.selector")}</th><th>{t("dkimrot.col.finished")}</th><th>{t("dkimrot.col.old_record")}</th></tr></thead>
              <tbody>
                {d.history.map((h) => (
                  <tr key={`${h.domain}-${h.new}`}>
                    <td className="cell-main">{h.domain}</td>
                    <td className="mono nowrap">{h.old} → {h.new}</td>
                    <td className="nowrap">{formatDate(h.finished_at, i18n.language)}</td>
                    <td>{h.old_record === "removed" ? t("dkimrot.old_removed") : t("dkimrot.old_manual", { name: `${h.old}._domainkey.${h.domain}` })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
    </div>
  );
}
