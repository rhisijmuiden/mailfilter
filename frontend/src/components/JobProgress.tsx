// Live progress of an update, backup or restore, as a timeline of steps.
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { isActive, useJob, type Job, type JobStep } from "../lib/jobs";
import { api } from "../lib/api";

export function stepText(t: (k: string, o?: Record<string, unknown>) => string, s: JobStep) {
  if (!s.key) return s.text;
  const params = { ...(s.params ?? {}) } as Record<string, unknown>;
  if (params.type) params.type = t(`backups.type_${params.type}`);
  if (params.channel) params.channel = t(`info.${params.channel}`);
  return t(`jobs.steps.${s.key}`, { ...params, defaultValue: s.text });
}

export function JobProgress({ id, onDone }: { id: string; onDone?: (job: Job) => void }) {
  const { t, i18n } = useTranslation();
  const q = useJob(id);
  const job = q.data;
  const [log, setLog] = useState<string | null>(null);
  const reported = useRef(false);
  useEffect(() => {
    if (job && !isActive(job) && !reported.current) {
      reported.current = true;
      onDone?.(job);
    }
  }, [job, onDone]);
  const steps = job?.steps ?? [];
  const reconnecting = q.isError || (q.failureCount > 0 && isActive(job));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {isActive(job) || !job ? <div className="progress indeterminate" aria-label={t("jobs.running")}><i /></div> : null}
      {reconnecting && (
        <div className="callout">
          <span>{t("jobs.reconnecting")}</span>
        </div>
      )}
      <ol className="timeline">
        {steps.map((s, i) => {
          const last = i === steps.length - 1;
          const cls = !last || job?.state === "done" ? "" : job?.state === "failed" ? " b" : " n";
          return (
            <li key={i}>
              <span className="t">{new Date(s.at).toLocaleTimeString(i18n.language === "nl" ? "nl-NL" : "en-GB", { hour: "2-digit", minute: "2-digit", second: "2-digit" })}</span>
              <span className={`m${cls}`} />
              <div className="d">
                <b>{stepText(t, s)}</b>
              </div>
            </li>
          );
        })}
      </ol>
      {job?.state === "done" && (
        <div className="callout">
          <span>{t(`jobs.done_${job.kind}`)}</span>
        </div>
      )}
      {job?.state === "failed" && (
        <div className="callout warn" role="alert">
          <span>
            <b>{t(`jobs.failed_${job.kind}`)}</b>
            <br />
            <span className="mono" style={{ fontSize: 12 }}>{job.message}</span>
          </span>
        </div>
      )}
      {job && !isActive(job) && (
        <div>
          <button
            className="btn small ghost"
            type="button"
            onClick={async () => setLog(log === null ? (await api.get<{ log: string }>(`/system/jobs/${id}/log`)).log : null)}
          >
            {log === null ? t("jobs.show_log") : t("jobs.hide_log")}
          </button>
          {log !== null && <pre className="log" style={{ marginTop: 8, maxHeight: 280, overflow: "auto" }}>{log || "—"}</pre>}
        </div>
      )}
    </div>
  );
}
