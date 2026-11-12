// System > Backups: copies on another server over FTP, FTPS or SCP, and restoring from there.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CloudUpload, Copy, Eye, KeyRound, PlugZap, RefreshCw, Server, Settings2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, Drawer, ErrorText, Field, Loading, formatDate, useToast } from "./ui";
import { api } from "../lib/api";
import type { Job } from "../lib/jobs";

export type Protocol = "ftp" | "ftps" | "scp";
export type RemoteSettings = {
  enabled: boolean; protocol: Protocol; host: string; port: number; username: string; path: string;
  verify_tls: boolean; encrypt: boolean; keep_full: number;
  has_password: boolean; has_passphrase: boolean; public_key: string; host_keys: string[];
  status: { at: string; ok: boolean; error?: string; uploaded?: string[]; removed?: string[] } | null;
};
export type RemoteBackup = {
  id: string; chain: string; step: number; type: "full" | "incremental"; label: string; created: string;
  version: string | null; branch: string | null; components: string[]; size: number; encrypted: boolean; uploaded: string;
};

const DEFAULT_PORT: Record<Protocol, number> = { ftp: 21, ftps: 21, scp: 22 };

export function RemoteCard({
  busy, onJob, onRestore, bytes,
}: {
  busy: boolean; onJob: (id: string) => void; onRestore: (b: RemoteBackup) => void; bytes: (n: number) => string;
}) {
  const { t, i18n } = useTranslation();
  const settings = useQuery({ queryKey: ["backups-remote"], queryFn: () => api.get<RemoteSettings>("/backups/remote") });
  const [editing, setEditing] = useState(false);
  const [showList, setShowList] = useState(false);
  const list = useQuery({
    queryKey: ["backups-remote-list"],
    queryFn: () => api.get<{ backups: RemoteBackup[] }>("/backups/remote/backups"),
    enabled: showList,
    retry: false,
  });
  const sync = useMutation({ mutationFn: () => api.post<Job>("/backups/remote/sync"), onSuccess: (j) => onJob(j.id) });
  const s = settings.data;
  const st = s?.status;
  const where = s ? `${s.protocol.toUpperCase()} · ${s.username ? `${s.username}@` : ""}${s.host}${s.port !== DEFAULT_PORT[s.protocol] ? `:${s.port}` : ""}/${s.path.replace(/^\//, "")}` : "";

  return (
    <Card
      title={t("backups.remote.title")}
      right={s && <button className="btn small ghost" onClick={() => setEditing(true)}><Settings2 /> {t("backups.remote.settings")}</button>}
    >
      {settings.isLoading ? (
        <Loading />
      ) : !s ? (
        <ErrorText error={settings.error} />
      ) : !s.enabled ? (
        <>
          <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>{t("backups.remote.off_d")}</p>
          <button className="btn primary" onClick={() => setEditing(true)}><Server /> {t("backups.remote.set_up")}</button>
        </>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10, fontSize: 13 }}>
          <div className="mono" style={{ wordBreak: "break-all" }}>{where}</div>
          <div>
            {!st ? (
              <span className="status s-mute">{t("backups.remote.never")}</span>
            ) : st.ok ? (
              <span className="status s-ok">{t("backups.remote.ok", { when: formatDate(st.at, i18n.language) })}</span>
            ) : (
              <span className="status s-bad">{t("backups.remote.failed", { when: formatDate(st.at, i18n.language) })}</span>
            )}
          </div>
          {st && !st.ok && st.error && <div className="callout warn"><span>{st.error}</span></div>}
          <p className="muted" style={{ margin: 0 }}>
            {t("backups.remote.on_d", { count: s.keep_full })} {s.encrypt ? t("backups.remote.encrypted") : t("backups.remote.not_encrypted")}
          </p>
          <ErrorText error={sync.error || list.error} />
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn" disabled={busy || sync.isPending} onClick={() => sync.mutate()}><CloudUpload /> {t("backups.remote.sync_now")}</button>
            <button className="btn ghost" onClick={() => (showList ? list.refetch() : setShowList(true))} disabled={list.isFetching}>
              <RefreshCw /> {showList ? t("backups.remote.refresh") : t("backups.remote.show")}
            </button>
          </div>
          {showList && (list.isFetching ? <Loading /> : list.data && (
            !list.data.backups.length ? (
              <div className="empty">{t("backups.remote.empty")}</div>
            ) : (
              <div className="tbl-wrap">
                <table>
                  <thead><tr><th>{t("backups.when")}</th><th>{t("backups.size")}</th><th /></tr></thead>
                  <tbody>
                    {list.data.backups.map((b) => (
                      <tr key={b.id}>
                        <td>
                          <div className="cell-main">{formatDate(b.created, i18n.language)}</div>
                          <div className="cell-sub">{t(`backups.type_${b.type}`)} · <span className="mono">{b.id}</span>{b.version && ` · ${b.version}`}</div>
                        </td>
                        <td className="nowrap num">{bytes(b.size)}</td>
                        <td className="nowrap" style={{ textAlign: "right" }}>
                          <button className="btn small" disabled={busy} onClick={() => onRestore(b)}>{t("backups.restore")}</button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )
          ))}
        </div>
      )}
      {editing && s && <RemoteDrawer settings={s} onClose={() => setEditing(false)} />}
    </Card>
  );
}

function RemoteDrawer({ settings, onClose }: { settings: RemoteSettings; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ ...settings, port: settings.port || DEFAULT_PORT[settings.protocol] });
  const [password, setPassword] = useState("");
  const [passphrase, setPassphrase] = useState("");
  const [revealed, setRevealed] = useState<string | null>(null);
  useEffect(() => setF((old) => ({ ...old, public_key: settings.public_key, host_keys: settings.host_keys })), [settings]);
  const refresh = () => qc.invalidateQueries({ queryKey: ["backups-remote"] });

  const body = () => ({
    enabled: f.enabled, protocol: f.protocol, host: f.host.trim(), port: f.port || null, username: f.username.trim(),
    path: f.path.trim(), verify_tls: f.verify_tls, encrypt: f.encrypt, keep_full: f.keep_full,
    password: password ? password : null, passphrase: passphrase ? passphrase : null,
  });
  const save = useMutation({
    mutationFn: () => api.put<RemoteSettings>("/backups/remote", body()),
    onSuccess: (s) => {
      qc.setQueryData(["backups-remote"], s);
      setPassword("");
      setPassphrase("");
      toast(t("common.saved"));
    },
  });
  const test = useMutation({
    mutationFn: async () => {
      const s = await api.put<RemoteSettings>("/backups/remote", body());
      qc.setQueryData(["backups-remote"], s);
      setPassword("");
      setPassphrase("");
      return api.post<{ ok: boolean; host_keys_learned: string[] }>("/backups/remote/test");
    },
    onSettled: refresh,
  });
  const forget = useMutation({ mutationFn: () => api.post("/backups/remote/forget-hostkey"), onSuccess: refresh });
  const reveal = useMutation({
    mutationFn: () => api.get<{ passphrase: string }>("/backups/remote/passphrase"),
    onSuccess: (d) => setRevealed(d.passphrase),
  });
  const copy = (v: string) => navigator.clipboard?.writeText(v).then(() => toast(t("common.copied")));
  const setProtocol = (p: Protocol) => setF({ ...f, protocol: p, port: f.port === DEFAULT_PORT[f.protocol] ? DEFAULT_PORT[p] : f.port });

  return (
    <Drawer title={t("backups.remote.settings_title")} sub={t("backups.remote.settings_sub")} onClose={onClose}>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="full" style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div>
            <b>{t("backups.remote.enabled")}</b>
            <div className="muted" style={{ fontSize: 12.5 }}>{t("backups.remote.enabled_d")}</div>
          </div>
          <label className="switch">
            <input type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} aria-label={t("backups.remote.enabled")} />
            <span />
          </label>
        </div>
        <Field label={t("backups.remote.protocol")} full>
          <select className="select" value={f.protocol} onChange={(e) => setProtocol(e.target.value as Protocol)}>
            <option value="scp">{t("backups.remote.p_scp")}</option>
            <option value="ftps">{t("backups.remote.p_ftps")}</option>
            <option value="ftp">{t("backups.remote.p_ftp")}</option>
          </select>
        </Field>
        {f.protocol === "ftp" && <div className="full callout warn"><span>{t("backups.remote.ftp_warn")}</span></div>}
        <Field label={t("backups.remote.host")}>
          <input className="input" required value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })} placeholder="backup.example.org" autoComplete="off" />
        </Field>
        <Field label={t("backups.remote.port")}>
          <input className="input" type="number" min={1} max={65535} value={f.port} onChange={(e) => setF({ ...f, port: +e.target.value })} />
        </Field>
        <Field label={t("backups.remote.username")}>
          <input className="input" required value={f.username} onChange={(e) => setF({ ...f, username: e.target.value })} autoComplete="off" />
        </Field>
        {f.protocol !== "scp" ? (
          <Field label={t("backups.remote.password")} hint={settings.has_password ? t("backups.remote.password_keep") : undefined}>
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password"
              placeholder={settings.has_password ? "••••••••" : ""} />
          </Field>
        ) : <div />}
        <Field label={t("backups.remote.path")} hint={t("backups.remote.path_d")} full>
          <input className="input mono" required value={f.path} onChange={(e) => setF({ ...f, path: e.target.value })} />
        </Field>
        <Field label={t("backups.remote.keep")} hint={t("backups.remote.keep_d")} full>
          <input className="input" type="number" min={1} max={100} required value={f.keep_full} onChange={(e) => setF({ ...f, keep_full: +e.target.value })} />
        </Field>
        {f.protocol === "ftps" && (
          <label className="check full">
            <input type="checkbox" checked={f.verify_tls} onChange={(e) => setF({ ...f, verify_tls: e.target.checked })} />
            <span>{t("backups.remote.verify_tls")} <span className="muted">{t("backups.remote.verify_tls_d")}</span></span>
          </label>
        )}

        {f.protocol === "scp" && (
          <div className="full" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <b style={{ fontSize: 12.5 }}><KeyRound style={{ width: 14, height: 14, verticalAlign: -2 }} /> {t("backups.remote.ssh_key")}</b>
            {settings.public_key ? (
              <>
                <div className="muted" style={{ fontSize: 12.5 }}>{t("backups.remote.ssh_key_d", { user: f.username || "backup" })}</div>
                <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                  <code className="mono" style={{ fontSize: 11.5, wordBreak: "break-all", flex: 1 }}>{settings.public_key}</code>
                  <button type="button" className="btn small ghost" onClick={() => copy(settings.public_key)} title={t("common.copy")}><Copy /></button>
                </div>
              </>
            ) : (
              <div className="muted" style={{ fontSize: 12.5 }}>{t("backups.remote.ssh_key_after_save")}</div>
            )}
            {settings.host_keys.length > 0 && (
              <div style={{ fontSize: 12.5 }}>
                <span className="muted">{t("backups.remote.host_key")}</span>{" "}
                {settings.host_keys.map((k) => <code key={k} className="mono" style={{ fontSize: 11.5, display: "block" }}>{k}</code>)}
                <button type="button" className="btn small ghost" onClick={() => window.confirm(t("backups.remote.forget_confirm")) && forget.mutate()}>
                  {t("backups.remote.forget")}
                </button>
              </div>
            )}
          </div>
        )}

        <label className="check full">
          <input type="checkbox" checked={f.encrypt} onChange={(e) => setF({ ...f, encrypt: e.target.checked })} />
          <span>{t("backups.remote.encrypt")} <span className="muted">{t("backups.remote.encrypt_d")}</span></span>
        </label>
        {!f.encrypt && <div className="full callout warn"><span>{t("backups.remote.encrypt_warn")}</span></div>}
        {f.encrypt && (
          <div className="full" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            <div className="callout"><span>{t("backups.remote.passphrase_d")}</span></div>
            {settings.has_passphrase && (
              <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                {revealed ? (
                  <>
                    <code className="mono" style={{ fontSize: 12.5, wordBreak: "break-all" }}>{revealed}</code>
                    <button type="button" className="btn small ghost" onClick={() => copy(revealed)} title={t("common.copy")}><Copy /></button>
                  </>
                ) : (
                  <button type="button" className="btn small" onClick={() => reveal.mutate()} disabled={reveal.isPending}><Eye /> {t("backups.remote.passphrase_show")}</button>
                )}
              </div>
            )}
            <Field label={settings.has_passphrase ? t("backups.remote.passphrase_change") : t("backups.remote.passphrase")}
              hint={t("backups.remote.passphrase_hint")}>
              <input className="input" type="password" minLength={12} value={passphrase} onChange={(e) => setPassphrase(e.target.value)} autoComplete="new-password" />
            </Field>
          </div>
        )}

        <div className="full"><ErrorText error={save.error || test.error || forget.error || reveal.error} /></div>
        {test.isSuccess && (
          <div className="full callout">
            <span>
              {t("backups.remote.test_ok")}
              {test.data.host_keys_learned?.length ? ` ${t("backups.remote.host_key_learned", { key: test.data.host_keys_learned.join(", ") })}` : ""}
            </span>
          </div>
        )}
        <div className="full" style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn primary" disabled={save.isPending}>{t("common.save")}</button>
          <button type="button" className="btn" disabled={test.isPending || !f.host} onClick={() => test.mutate()}>
            <PlugZap /> {test.isPending ? t("backups.remote.testing") : t("backups.remote.test")}
          </button>
          <button type="button" className="btn ghost" onClick={onClose}>{t("common.close")}</button>
        </div>
      </form>
    </Drawer>
  );
}
