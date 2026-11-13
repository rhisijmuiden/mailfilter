// The Info button: running version and channel for everyone; for admins also
// "check for updates", "update now" and switching between production and test.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpCircle, CheckCircle2, GitBranch, RefreshCw } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { useMe } from "../lib/auth";
import { isActive, useJobs, type Job } from "../lib/jobs";
import { JobProgress } from "./JobProgress";
import { ErrorText, Modal, formatDate } from "./ui";
import { ProductName } from "./Logo";

export type SystemInfo = {
  version: string; app_version: string; branch: string; hostname: string;
  label?: string; channel?: string; commit?: string; commit_date?: string;
};

type Details = {
  version: string; label: string; branch: string | null; channel: string; commit?: string; commit_short?: string;
  commit_date?: string; os: string; kernel: string; python: string; uptime_seconds?: number;
  components: Record<string, string>;
  last_update?: { at: string; ok: boolean; to: { version: string; branch: string } } | null;
};

type BranchInfo = { branch: "main" | "test"; channel: string; version: string; commit_short: string; commit_date: string; installable: boolean };
type Check = {
  checked_at: string; update_available: boolean; behind: number;
  commits: { commit: string; date: string; subject: string }[];
  branches: Record<"main" | "test", BranchInfo | null>;
};

export function useSystemInfo() {
  return useQuery({ queryKey: ["system-info"], queryFn: () => api.get<SystemInfo>("/system/info"), staleTime: 5 * 60_000 });
}

const COMPONENT_NAMES: Record<string, string> = {
  postfix: "Postfix", rspamd: "Rspamd", "clamav-daemon": "ClamAV", postgresql: "PostgreSQL", "redis-server": "Redis",
  "dovecot-core": "Dovecot", caddy: "Caddy", crowdsec: "CrowdSec", nftables: "nftables",
};

export function InfoModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const { data } = useSystemInfo();
  const me = useMe();
  const isAdmin = me.data?.role === "admin";
  const channel = data?.branch === "main" ? t("info.production") : data?.branch === "test" ? t("info.test") : data?.branch;
  return (
    <Modal title={t("info.title")} onClose={onClose}>
      <div className="version-hero">
        <img className="ic" src="/favicon.svg" alt="" />
        <div>
          <div className="muted"><ProductName /></div>
          <div className="vn">{data?.version ?? "…"}</div>
          <a className="muted" style={{ fontSize: 12 }} href="https://github.com/rhisijmuiden/mailfilter" target="_blank" rel="noopener">{t("info.license")}</a>
        </div>
      </div>
      <dl className="kv">
        <dt>{t("info.branch")}</dt>
        <dd>
          <span className={`status ${data?.branch === "main" ? "s-ok" : "s-info"}`}>{channel}</span>
        </dd>
        <dt>{t("info.host")}</dt>
        <dd className="mono">{data?.hostname}</dd>
        {!isAdmin && (
          <>
            <dt>{t("info.app")}</dt>
            <dd className="num">{data?.app_version}</dd>
          </>
        )}
      </dl>
      {isAdmin && <AdminInfo current={data?.branch} />}
    </Modal>
  );
}

function AdminInfo({ current }: { current?: string }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const details = useQuery({ queryKey: ["system-details"], queryFn: () => api.get<Details>("/system/details"), staleTime: 60_000 });
  const jobs = useJobs();
  const [jobId, setJobId] = useState<string | null>(null);
  const [target, setTarget] = useState<"main" | "test">(current === "main" ? "main" : "test");
  const [backup, setBackup] = useState(true);
  const [showComponents, setShowComponents] = useState(false);
  const check = useMutation({ mutationFn: () => api.post<Check>("/system/update/check") });
  const update = useMutation({
    mutationFn: (branch?: "main" | "test") => api.post<Job>("/system/update", { branch, backup }),
    onSuccess: (job) => setJobId(job.id),
  });

  // Pick up an update that is already running (e.g. after the page reloaded mid-update)
  useEffect(() => {
    const running = jobs.data?.jobs.find((j) => j.kind === "update" && isActive(j));
    if (running && !jobId) setJobId(running.id);
  }, [jobs.data, jobId]);
  useEffect(() => {
    if (current === "main" || current === "test") setTarget(current);
  }, [current]);

  const d = details.data;
  const c = check.data;
  const switching = target !== current;
  const targetInfo = c?.branches?.[target];

  const onDone = (job: Job) => {
    qc.invalidateQueries({ queryKey: ["system-info"] });
    qc.invalidateQueries({ queryKey: ["system-details"] });
    qc.invalidateQueries({ queryKey: ["jobs"] });
    // New version: reload so the browser runs the new interface too
    if (job.state === "done" && (job.result as { changed?: boolean } | null)?.changed) window.setTimeout(() => window.location.reload(), 2500);
  };

  return (
    <>
      <dl className="kv">
        <dt>{t("info.commit")}</dt>
        <dd className="mono">
          {d?.commit_short ?? "…"}
          {d?.commit_date && <span className="muted"> · {formatDate(d.commit_date, i18n.language)}</span>}
        </dd>
        <dt>{t("info.os")}</dt>
        <dd>{d ? `${d.os} · Linux ${d.kernel}` : "…"}</dd>
        {d?.uptime_seconds !== undefined && (
          <>
            <dt>{t("info.uptime")}</dt>
            <dd>{t("info.uptime_v", { days: Math.floor(d.uptime_seconds / 86400), hours: Math.floor((d.uptime_seconds % 86400) / 3600) })}</dd>
          </>
        )}
        {d?.last_update && (
          <>
            <dt>{t("info.last_update")}</dt>
            <dd>
              {formatDate(d.last_update.at, i18n.language)} · {d.last_update.to.version}{" "}
              <span className={`status ${d.last_update.ok ? "s-ok" : "s-bad"}`}>{d.last_update.ok ? t("info.ok") : t("info.rolled_back")}</span>
            </dd>
          </>
        )}
        <dt>{t("info.components")}</dt>
        <dd>
          {showComponents && d ? (
            <span style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {Object.entries(d.components).map(([k, v]) => (
                <span key={k} className="tag" title={v}>
                  {COMPONENT_NAMES[k] ?? k} {v.replace(/^\d+:/, "").split(/[-+~]/)[0]}
                </span>
              ))}
            </span>
          ) : (
            <button className="btn small ghost" type="button" onClick={() => setShowComponents(true)} style={{ padding: 0 }}>
              {t("info.show_components")}
            </button>
          )}
        </dd>
      </dl>
      <ErrorText error={details.error} />

      {jobId ? (
        <JobProgress id={jobId} onDone={onDone} />
      ) : (
        <>
          <div className="field">
            <span style={{ fontSize: 12.5, fontWeight: 650 }}>{t("info.channel")}</span>
            <div className="channel" role="radiogroup">
              {(["main", "test"] as const).map((b) => {
                const bi = c?.branches?.[b];
                return (
                  <label key={b}>
                    <span className="nm">
                      <input type="radio" name="channel" checked={target === b} onChange={() => setTarget(b)} />
                      {t(b === "main" ? "info.production" : "info.test")}
                      {current === b && <span className="tag">{t("info.current")}</span>}
                    </span>
                    <span className="ds">{t(b === "main" ? "info.ch_prod_d" : "info.ch_test_d")}</span>
                    {bi && (
                      <span className={`ds${bi.installable ? " mono" : ""}`}>
                        {bi.installable ? `${bi.version} · ${bi.commit_short}` : t("info.not_installable")}
                      </span>
                    )}
                  </label>
                );
              })}
            </div>
          </div>

          <ErrorText error={check.error || update.error} />

          {c && !switching && (
            c.update_available ? (
              <div className="callout">
                <ArrowUpCircle aria-hidden />
                <div style={{ minWidth: 0 }}>
                  <b>{t("info.avail", { version: c.branches[target]?.version, count: c.behind })}</b>
                  <ul style={{ margin: "6px 0 0", paddingLeft: 18, fontSize: 12.5 }}>
                    {c.commits.slice(0, 8).map((x) => <li key={x.commit}>{x.subject}</li>)}
                    {c.commits.length > 8 && <li className="muted">{t("info.more", { count: c.commits.length - 8 })}</li>}
                  </ul>
                </div>
              </div>
            ) : (
              <div className="callout">
                <CheckCircle2 aria-hidden />
                <span>{t("info.uptodate")}</span>
              </div>
            )
          )}
          {switching && targetInfo && !targetInfo.installable && (
            <div className="callout warn"><span>{t("info.not_installable_long")}</span></div>
          )}

          <label className="check">
            <input type="checkbox" checked={backup} onChange={(e) => setBackup(e.target.checked)} />
            <span>{t("info.backup_first")}</span>
          </label>

          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn" type="button" onClick={() => check.mutate()} disabled={check.isPending}>
              <RefreshCw /> {check.isPending ? t("info.checking") : t("info.check")}
            </button>
            {switching ? (
              <button
                className="btn primary"
                type="button"
                disabled={update.isPending || (targetInfo ? !targetInfo.installable : false)}
                onClick={() =>
                  window.confirm(t("info.switch_confirm", { channel: t(target === "main" ? "info.production" : "info.test") })) && update.mutate(target)
                }
              >
                <GitBranch /> {t("info.switch_to", { channel: t(target === "main" ? "info.production" : "info.test") })}
              </button>
            ) : (
              c?.update_available && (
                <button className="btn primary" type="button" disabled={update.isPending} onClick={() => update.mutate(undefined)}>
                  <ArrowUpCircle /> {t("info.update_now")}
                </button>
              )
            )}
          </div>
          {c && <div className="muted" style={{ fontSize: 12 }}>{t("info.checked_at", { at: formatDate(c.checked_at, i18n.language) })}</div>}
        </>
      )}
    </>
  );
}
