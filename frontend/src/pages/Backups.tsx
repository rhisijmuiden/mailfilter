// System > Backups: local full and incremental backups, schedule, restore.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, DatabaseBackup, History, ShieldCheck, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { JobProgress } from "../components/JobProgress";
import { Card, Drawer, ErrorText, Field, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { api } from "../lib/api";
import { isActive, useJobs, type Job } from "../lib/jobs";

type Backup = {
  id: string; chain: string; step: number; type: "full" | "incremental"; label: string; created: string;
  version: string | null; branch: string | null; components: string[]; size: number; size_human: string;
};
type Config = { enabled: boolean; time: string; full_every_days: number; keep_full: number; location: string };
type Summary = { config: Config; backups: Backup[]; total_size: number; disk_free: number; disk_total: number; last: Backup | null };

const COMPONENTS = ["files", "database", "redis"] as const;

export function bytes(n: number, lang: string) {
  const units = ["B", "KB", "MB", "GB", "TB"];
  let i = 0;
  while (n >= 1024 && i < units.length - 1) {
    n /= 1024;
    i++;
  }
  return `${n.toLocaleString(lang === "nl" ? "nl-NL" : "en-GB", { maximumFractionDigits: i ? 1 : 0 })} ${units[i]}`;
}

export default function Backups() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const summary = useQuery({ queryKey: ["backups"], queryFn: () => api.get<Summary>("/backups") });
  const jobs = useJobs();
  const [jobId, setJobId] = useState<string | null>(null);
  const [restoring, setRestoring] = useState<Backup | null>(null);
  const running = jobs.data?.jobs.find((j) => isActive(j) && j.kind !== "update");
  useEffect(() => {
    if (running && !jobId) setJobId(running.id);
  }, [running, jobId]);

  const create = useMutation({
    mutationFn: (type: "full" | "incremental") => api.post<Job>("/backups", { type }),
    onSuccess: (job) => setJobId(job.id),
  });
  const verify = useMutation({
    mutationFn: (id: string) => api.post<{ ok: boolean }>(`/backups/${id}/verify`),
    onSuccess: () => toast(t("backups.verified")),
  });
  const del = useMutation({
    mutationFn: (chain: string) => api.del(`/backups/chains/${chain}`),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["backups"] }),
  });
  const onDone = () => {
    qc.invalidateQueries({ queryKey: ["backups"] });
    qc.invalidateQueries({ queryKey: ["jobs"] });
  };

  const s = summary.data;
  const busy = !!running || create.isPending;
  return (
    <>
      <PageHead
        title={t("backups.title")}
        sub={t("backups.sub")}
        actions={
          <>
            <button className="btn" disabled={busy} onClick={() => create.mutate("incremental")}>
              <History /> {t("backups.incremental_now")}
            </button>
            <button className="btn primary" disabled={busy} onClick={() => create.mutate("full")}>
              <DatabaseBackup /> {t("backups.full_now")}
            </button>
          </>
        }
      />
      <ErrorText error={summary.error || create.error || verify.error || del.error} />
      {s && (
        <div className="grid kpis" style={{ marginBottom: 16 }}>
          <div className="card kpi">
            <span className="label">{t("backups.last")}</span>
            <span className="value" style={{ fontSize: 20 }}>{s.last ? formatDate(s.last.created, i18n.language) : t("common.never")}</span>
            <span className="delta">{s.last ? t(`backups.type_${s.last.type}`) : ""}</span>
          </div>
          <div className="card kpi">
            <span className="label">{t("backups.count")}</span>
            <span className="value">{s.backups.length}</span>
            <span className="delta">{t("backups.full_count", { count: new Set(s.backups.map((b) => b.chain)).size })}</span>
          </div>
          <div className="card kpi">
            <span className="label">{t("backups.used")}</span>
            <span className="value">{bytes(s.total_size, i18n.language)}</span>
            <span className="delta mono">{s.config.location}</span>
          </div>
          <div className="card kpi">
            <span className="label">{t("backups.free")}</span>
            <span className="value">{bytes(s.disk_free, i18n.language)}</span>
            <span className="delta">{t("backups.of", { total: bytes(s.disk_total, i18n.language) })}</span>
          </div>
        </div>
      )}
      <div className="grid two" style={{ alignItems: "start" }}>
        <Card title={t("backups.list")} flush>
          {summary.isLoading ? (
            <Loading />
          ) : !s?.backups.length ? (
            <div className="empty">{t("backups.empty")}</div>
          ) : (
            <div className="tbl-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("backups.when")}</th>
                    <th>{t("backups.type")}</th>
                    <th>{t("backups.size")}</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {s.backups.map((b) => (
                    <tr key={b.id}>
                      <td>
                        <div className="cell-main">{formatDate(b.created, i18n.language)}</div>
                        <div className="cell-sub">
                          <span className="mono">{b.id}</span>
                          {b.label && ` · ${b.label}`}
                          {b.version && ` · ${b.version}`}
                        </div>
                      </td>
                      <td className="nowrap">
                        <span className={`status ${b.type === "full" ? "s-info" : "s-mute"}`}>{t(`backups.type_${b.type}`)}</span>
                      </td>
                      <td className="nowrap num">{bytes(b.size, i18n.language)}</td>
                      <td className="nowrap" style={{ textAlign: "right" }}>
                        <button className="btn small ghost" title={t("backups.verify")} onClick={() => verify.mutate(b.id)} disabled={verify.isPending}>
                          <ShieldCheck />
                        </button>
                        <button className="btn small" onClick={() => setRestoring(b)} disabled={busy}>
                          <Archive /> {t("backups.restore")}
                        </button>
                        {b.type === "full" && (
                          <button
                            className="btn small ghost danger"
                            title={t("backups.delete")}
                            onClick={() => window.confirm(t("backups.delete_confirm", { when: formatDate(b.created, i18n.language) })) && del.mutate(b.chain)}
                          >
                            <Trash2 />
                          </button>
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>
        <div className="grid" style={{ alignContent: "start" }}>
          {jobId && (
            <Card title={t("backups.task")} right={!running && <button className="btn small ghost" onClick={() => setJobId(null)}>{t("common.close")}</button>}>
              <JobProgress id={jobId} onDone={onDone} />
            </Card>
          )}
          {s && <Schedule config={s.config} />}
          <Card title={t("backups.contents")}>
            <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
              {COMPONENTS.map((c) => <li key={c}><b>{t(`backups.comp_${c}`)}</b>: <span className="muted">{t(`backups.comp_${c}_d`)}</span></li>)}
            </ul>
            <p className="muted" style={{ fontSize: 12.5, marginBottom: 0 }}>{t("backups.remote_later")}</p>
          </Card>
        </div>
      </div>
      {restoring && (
        <RestoreDrawer
          backup={restoring}
          onClose={() => setRestoring(null)}
          onStarted={(id) => {
            setRestoring(null);
            setJobId(id);
          }}
        />
      )}
    </>
  );
}

function Schedule({ config }: { config: Config }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState(config);
  useEffect(() => setF(config), [config]);
  const save = useMutation({
    mutationFn: () => api.put<Config>("/backups/config", { enabled: f.enabled, time: f.time, full_every_days: f.full_every_days, keep_full: f.keep_full }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["backups"] });
      toast(t("common.saved"));
    },
  });
  return (
    <Card title={t("backups.schedule")}>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="full"><ErrorText error={save.error} /></div>
        <div className="setting full" style={{ paddingTop: 0 }}>
          <div className="txt">
            <b>{t("backups.nightly")}</b>
            <span>{t("backups.nightly_d", { days: f.full_every_days })}</span>
          </div>
          <label className="switch">
            <input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} aria-label={t("backups.nightly")} />
            <span />
          </label>
        </div>
        <Field label={t("backups.time")}>
          <input className="input" type="time" required value={f.time} onChange={(e) => setF({ ...f, time: e.target.value })} />
        </Field>
        <Field label={t("backups.full_every")} hint={t("backups.days")}>
          <input className="input" type="number" min={1} max={90} required value={f.full_every_days} onChange={(e) => setF({ ...f, full_every_days: +e.target.value })} />
        </Field>
        <Field label={t("backups.keep")} hint={t("backups.keep_d")} full>
          <input className="input" type="number" min={1} max={100} required value={f.keep_full} onChange={(e) => setF({ ...f, keep_full: +e.target.value })} />
        </Field>
        <div className="full">
          <button className="btn primary" disabled={save.isPending}>{t("common.save")}</button>
        </div>
      </form>
    </Card>
  );
}

function RestoreDrawer({ backup, onClose, onStarted }: { backup: Backup; onClose: () => void; onStarted: (jobId: string) => void }) {
  const { t, i18n } = useTranslation();
  const [parts, setParts] = useState<string[]>(backup.components.length ? backup.components : [...COMPONENTS]);
  const [safety, setSafety] = useState(true);
  const [confirm, setConfirm] = useState("");
  const word = t("backups.confirm_word");
  const restore = useMutation({
    mutationFn: () => api.post<Job>(`/backups/${backup.id}/restore`, { components: parts, safety_backup: safety }),
    onSuccess: (job) => onStarted(job.id),
  });
  return (
    <Drawer title={t("backups.restore_title")} sub={`${formatDate(backup.created, i18n.language)} · ${backup.id}`} onClose={onClose}>
      <div className="callout warn">
        <span>{t("backups.restore_warn")}</span>
      </div>
      {backup.type === "incremental" && <p className="muted" style={{ fontSize: 13 }}>{t("backups.restore_chain", { count: backup.step })}</p>}
      <div className="field">
        <span style={{ fontSize: 12.5, fontWeight: 650 }}>{t("backups.restore_parts")}</span>
        {COMPONENTS.map((c) => (
          <label key={c} className="check">
            <input
              type="checkbox"
              disabled={!backup.components.includes(c)}
              checked={parts.includes(c)}
              onChange={() => setParts(parts.includes(c) ? parts.filter((x) => x !== c) : [...parts, c])}
            />
            <span>
              <b>{t(`backups.comp_${c}`)}</b> <span className="muted">{t(`backups.comp_${c}_d`)}</span>
            </span>
          </label>
        ))}
      </div>
      <label className="check">
        <input type="checkbox" checked={safety} onChange={(e) => setSafety(e.target.checked)} />
        <span>{t("backups.safety")}</span>
      </label>
      <Field label={t("backups.type_to_confirm", { word })}>
        <input className="input" value={confirm} onChange={(e) => setConfirm(e.target.value)} autoComplete="off" />
      </Field>
      <ErrorText error={restore.error} />
      <div style={{ display: "flex", gap: 8 }}>
        <button
          className="btn primary"
          disabled={!parts.length || confirm.trim().toLowerCase() !== word.toLowerCase() || restore.isPending}
          onClick={() => restore.mutate()}
        >
          <Archive /> {t("backups.restore_go")}
        </button>
        <button className="btn ghost" onClick={onClose}>{t("common.cancel")}</button>
      </div>
    </Drawer>
  );
}
