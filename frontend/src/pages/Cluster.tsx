// System > Cluster: several Mailfilter servers that keep the same configuration.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowUpCircle, Check, Copy, KeyRound, LifeBuoy, LogOut, Mail, Network, Pencil, RefreshCw, Server, Trash2, UserPlus } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, Field, Loading, Modal, PageHead, formatDate, useToast } from "../components/ui";
import { ApiError, api } from "../lib/api";

type NodeState = "self" | "ok" | "behind" | "unreachable" | "error" | "waiting";
type ClusterNode = {
  node_id: string; name: string; url: string; addresses: string[]; is_self: boolean; joined_at: string | null;
  last_seen_at: string | null; last_sync_at: string | null; last_error: string; last_error_at: string | null;
  version: string; in_sync: boolean; latency_ms: number | null; state: NodeState; role: Role;
  failover: FailoverState | null;
};
type Role = "member" | "primary" | "standby";
type Mode = "active" | "standby";
type FailoverSettings = { enabled: boolean; hostname: string; after_minutes: number; back_minutes: number };
type FailoverState = {
  active?: boolean; since?: string; manual?: boolean; down_since?: string; up_since?: string; checked_at?: string;
  handed_back_at?: string; error?: string;
};
type Overview = {
  enabled: boolean; cluster_id: string | null; spf_name: string; revision: { at: string; node: string } | null;
  last_import: { at: string; ok: boolean; from: string; error: string; detail: string } | null;
  nodes: ClusterNode[]; versions_differ: boolean; default_name: string; own_url: string;
  results?: Record<string, string>;
  mode: Mode; role: Role | null; primary: string | null; primary_url: string | null;
  failover: FailoverSettings; failover_state: FailoverState | null;
};

const STATE_CLASS: Record<NodeState, string> = {
  self: "s-info", ok: "s-ok", behind: "s-warn", unreachable: "s-bad", error: "s-bad", waiting: "s-mute",
};
const SYNCED = ["domains", "relay", "throttling", "users", "lists", "rules", "mail", "cloudflare", "dkim"] as const;
const LOCAL = ["queues", "trace", "backups", "updates", "paused", "dmarc", "host"] as const;

function ClusterError({ error }: { error: unknown }) {
  const { t } = useTranslation();
  if (!error) return null;
  let msg = t("errors.error");
  if (error instanceof ApiError) {
    const code = (error.info?.code ?? error.code).replace(/^cluster_/, "");
    msg = t(`cluster.err.${code}`, { defaultValue: t("errors.error") });
    if (error.info?.detail && code !== "auth_failed") msg += ` (${error.info.detail})`;
  }
  return (
    <div className="callout warn" role="alert">
      <span>{msg}</span>
    </div>
  );
}

export function nodeError(t: (k: string, o?: any) => string, raw: string) {
  const [code, ...rest] = raw.split(":");
  const text = t(`cluster.err.${code.replace(/^peer_/, "")}`, { defaultValue: "" });
  return text ? `${text}${rest.length && code !== "auth_failed" ? ` (${rest.join(":").trim()})` : ""}` : raw;
}

export default function Cluster() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["cluster"], queryFn: () => api.get<Overview>("/cluster"), refetchInterval: 30_000 });
  return (
    <>
      {!q.data?.enabled && <PageHead title={t("cluster.title")} sub={t("cluster.sub")} />}
      {q.isLoading ? <Loading /> : q.data ? q.data.enabled ? <InCluster data={q.data} /> : <Standalone data={q.data} /> : <ClusterError error={q.error} />}
      <WhatSyncs />
    </>
  );
}

function Standalone({ data }: { data: Overview }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [name, setName] = useState(data.default_name);
  const [mode, setMode] = useState<Mode>("active");
  const [joinName, setJoinName] = useState(data.default_name.replace(/01$/, "02"));
  const [code, setCode] = useState("");
  const [sure, setSure] = useState(false);
  const create = useMutation({
    mutationFn: () => api.post<Overview>("/cluster/create", { name, mode }),
    onSuccess: (d) => {
      qc.setQueryData(["cluster"], d);
      toast(t("cluster.created"));
    },
  });
  const join = useMutation({
    mutationFn: () => api.post<Overview>("/cluster/join", { code, name: joinName }),
    onSuccess: (d) => {
      qc.setQueryData(["cluster"], d);
      qc.invalidateQueries();
      toast(t("cluster.joined"));
    },
  });
  return (
    <>
      <div className="callout" style={{ marginBottom: 16 }}>
        <span>{t("cluster.single")}</span>
      </div>
      {!data.own_url && <div className="callout warn" style={{ marginBottom: 16 }}><span>{t("cluster.err.no_hostname")}</span></div>}
      <div className="grid two" style={{ alignItems: "start" }}>
        <Card title={t("cluster.create_title")}>
          <p className="muted" style={{ marginTop: 0 }}>{t("cluster.create_d")}</p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              create.mutate();
            }}
          >
            <div className="full channel" role="radiogroup" aria-label={t("cluster.mode")}>
              {(["active", "standby"] as const).map((m) => (
                <label key={m}>
                  <span className="nm">
                    <input type="radio" name="mode" checked={mode === m} onChange={() => setMode(m)} />
                    {t(`cluster.mode_${m}`)}
                  </span>
                  <span className="ds">{t(`cluster.mode_${m}_d`)}</span>
                </label>
              ))}
            </div>
            <Field label={t("cluster.node_name")} hint={t("cluster.node_name_d")} full>
              <input className="input" required maxLength={63} pattern="[A-Za-z0-9][A-Za-z0-9._\-]*" value={name} onChange={(e) => setName(e.target.value)} />
            </Field>
            <div className="full">
              <ClusterError error={create.error} />
              <button className="btn primary" disabled={create.isPending || !data.own_url}>
                <Network /> {t("cluster.create")}
              </button>
            </div>
          </form>
        </Card>
        <Card title={t("cluster.join_title")}>
          <p className="muted" style={{ marginTop: 0 }}>{t("cluster.join_d")}</p>
          <form
            className="form-grid"
            onSubmit={(e) => {
              e.preventDefault();
              join.mutate();
            }}
          >
            <Field label={t("cluster.join_code")} hint={t("cluster.join_code_d")} full>
              <textarea className="input mono" rows={3} required value={code} onChange={(e) => setCode(e.target.value)} placeholder="MFC1.…" />
            </Field>
            <Field label={t("cluster.node_name")} full>
              <input className="input" required maxLength={63} pattern="[A-Za-z0-9][A-Za-z0-9._\-]*" value={joinName} onChange={(e) => setJoinName(e.target.value)} />
            </Field>
            <div className="full callout warn"><span>{t("cluster.join_warn")}</span></div>
            <label className="check full">
              <input type="checkbox" checked={sure} onChange={(e) => setSure(e.target.checked)} />
              <span>{t("cluster.join_sure")}</span>
            </label>
            <div className="full">
              <ClusterError error={join.error} />
              <button className="btn primary" disabled={!sure || join.isPending || !data.own_url}>
                <UserPlus /> {join.isPending ? t("cluster.joining") : t("cluster.join")}
              </button>
            </div>
          </form>
        </Card>
      </div>
    </>
  );
}

function InCluster({ data }: { data: Overview }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [showCode, setShowCode] = useState(false);
  const [removing, setRemoving] = useState<ClusterNode | null>(null);
  const [leaving, setLeaving] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const set = (d: Overview) => qc.setQueryData(["cluster"], d);
  const sync = useMutation({
    mutationFn: () => api.post<Overview>("/cluster/sync"),
    onSuccess: (d) => {
      set(d);
      toast(t("cluster.synced"));
    },
  });
  const me = data.nodes.find((n) => n.is_self);
  const peers = data.nodes.filter((n) => !n.is_self);
  const healthy = peers.filter((n) => n.state === "ok").length;
  const lastSync = peers.map((n) => n.last_sync_at).filter(Boolean).sort().pop() ?? null;
  const lang = i18n.language;

  return (
    <>
      <PageHead
        title={t("cluster.title")}
        sub={t("cluster.sub")}
        actions={
          <>
            {data.role !== "standby" && (
              <button className="btn" onClick={() => setShowCode(true)}>
                <KeyRound /> {t("cluster.add_node")}
              </button>
            )}
            <button className="btn primary" onClick={() => sync.mutate()} disabled={sync.isPending}>
              <RefreshCw /> {t("cluster.sync_now")}
            </button>
          </>
        }
      />
      <ClusterError error={sync.error} />
      {data.role === "standby" && <StandbyPanel data={data} onDone={set} />}
      {data.versions_differ && <div className="callout warn" style={{ marginBottom: 16 }}><span>{t("cluster.versions_differ")}</span></div>}
      {data.last_import && !data.last_import.ok && (
        <div className="callout warn" style={{ marginBottom: 16 }}>
          <span>
            {t("cluster.import_failed", { from: data.last_import.from, when: formatDate(data.last_import.at, lang) })}{" "}
            {nodeError(t, data.last_import.error)}
          </span>
        </div>
      )}
      <div className="grid kpis" style={{ marginBottom: 16 }}>
        <div className="card kpi">
          <span className="label">{t("cluster.this_node")}</span>
          <span className="value" style={{ fontSize: 20 }}>{me?.name}</span>
          <span className="delta mono">{me?.url.replace("https://", "")}</span>
        </div>
        <div className="card kpi">
          <span className="label">{t("cluster.nodes")}</span>
          <span className="value">{data.nodes.length}</span>
          <span className="delta">{t("cluster.healthy", { count: healthy, total: peers.length })}</span>
        </div>
        <div className="card kpi">
          <span className="label">{t("cluster.last_sync")}</span>
          <span className="value" style={{ fontSize: 20 }}>{lastSync ? formatDate(lastSync, lang) : t("common.never")}</span>
          <span className="delta">{t("cluster.auto_sync")}</span>
        </div>
        <div className="card kpi">
          <span className="label">{t("cluster.last_change")}</span>
          <span className="value" style={{ fontSize: 20 }}>{data.revision ? formatDate(data.revision.at, lang) : t("common.never")}</span>
          <span className="delta">{data.nodes.find((n) => n.node_id === data.revision?.node)?.name ?? ""}</span>
        </div>
      </div>

      <Card title={t("cluster.nodes")} flush>
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("cluster.node")}</th>
                <th>{t("cluster.status")}</th>
                <th>{t("cluster.version")}</th>
                <th>{t("cluster.seen")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {data.nodes.map((n) => (
                <tr key={n.node_id}>
                  <td>
                    <div className="cell-main" style={{ display: "flex", alignItems: "center", gap: 8 }}>
                      <Server size={16} /> {n.name}
                      {n.role !== "member" && <span className={`status ${n.role === "primary" ? "s-info" : "s-mute"}`}>{t(`cluster.role_${n.role}`)}</span>}
                    </div>
                    <div className="cell-sub mono">
                      {n.url.replace("https://", "")}
                      {n.addresses.length > 0 && ` · ${n.addresses.join(", ")}`}
                    </div>
                    {n.last_error && n.state !== "ok" && n.state !== "self" && (
                      <div className="cell-sub" style={{ color: "var(--bad, #c0392b)" }}>{nodeError(t, n.last_error)}</div>
                    )}
                  </td>
                  <td className="nowrap">
                    <span className={`status ${STATE_CLASS[n.state]}`}>{t(`cluster.state_${n.state}`)}</span>
                  </td>
                  <td className="nowrap mono">{n.version || "–"}</td>
                  <td className="nowrap">
                    {n.is_self ? "–" : n.last_seen_at ? formatDate(n.last_seen_at, lang) : t("common.never")}
                    {!n.is_self && n.latency_ms !== null && <div className="cell-sub">{n.latency_ms} ms</div>}
                  </td>
                  <td className="nowrap" style={{ textAlign: "right" }}>
                    {n.is_self ? (
                      <>
                        <button className="btn small ghost" title={t("cluster.rename")} onClick={() => setRenaming(true)}>
                          <Pencil />
                        </button>
                        <button className="btn small" onClick={() => setLeaving(true)}>
                          <LogOut /> {t("cluster.leave")}
                        </button>
                      </>
                    ) : (
                      <button className="btn small ghost" title={t("cluster.remove")} onClick={() => setRemoving(n)}>
                        <Trash2 />
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </Card>
      {peers.length === 0 && <div className="callout" style={{ marginTop: 16 }}><span>{t("cluster.alone")}</span></div>}
      <div className="callout" style={{ marginTop: 16 }}>
        <span>{t(data.mode === "standby" ? "cluster.dns_hint_standby" : "cluster.dns_hint", { spf: data.spf_name })}</span>
      </div>
      {data.role !== "standby" && (
        <div className="grid two" style={{ marginTop: 16, alignItems: "start" }}>
          <ModeCard data={data} onDone={set} />
          <MxCard mode={data.mode} />
        </div>
      )}
      {data.mode === "standby" && <FailoverCard data={data} onDone={set} />}

      {showCode && <JoinCodeModal onClose={() => setShowCode(false)} />}
      {renaming && me && <RenameModal current={me.name} onClose={() => setRenaming(false)} onDone={set} />}
      {removing && (
        <ConfirmModal
          title={t("cluster.remove_title", { name: removing.name })}
          text={t("cluster.remove_d")}
          action={t("cluster.remove")}
          run={() => api.del<Overview>(`/cluster/nodes/${removing.node_id}`)}
          onClose={() => setRemoving(null)}
          onDone={(d) => {
            set(d);
            toast(t("cluster.removed"));
          }}
        />
      )}
      {leaving && (
        <ConfirmModal
          title={t("cluster.leave_title")}
          text={t("cluster.leave_d")}
          action={t("cluster.leave")}
          run={() => api.post<Overview>("/cluster/leave")}
          onClose={() => setLeaving(false)}
          onDone={(d) => {
            set(d);
            toast(t("cluster.left"));
          }}
        />
      )}
    </>
  );
}

function JoinCodeModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["cluster-join-code"], queryFn: () => api.get<{ code: string }>("/cluster/join-code"), gcTime: 0 });
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const id = setTimeout(() => setCopied(false), 2000);
    return () => clearTimeout(id);
  }, [copied]);
  return (
    <Modal title={t("cluster.add_node")} onClose={onClose} icon={<KeyRound />}>
      <ol style={{ paddingLeft: 18, marginTop: 0 }}>
        <li>{t("cluster.add_1")}</li>
        <li>{t("cluster.add_2")}</li>
        <li>{t("cluster.add_3")}</li>
      </ol>
      {q.data ? (
        <>
          <textarea className="input mono" rows={4} readOnly value={q.data.code} onFocus={(e) => e.target.select()} style={{ width: "100%" }} />
          <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center", flexWrap: "wrap" }}>
            <button
              className="btn primary"
              onClick={() => navigator.clipboard?.writeText(q.data!.code).then(() => setCopied(true))}
            >
              {copied ? <Check /> : <Copy />} {copied ? t("common.copied") : t("common.copy")}
            </button>
            <span className="muted">{t("cluster.code_secret")}</span>
          </div>
        </>
      ) : q.isLoading ? (
        <Loading />
      ) : (
        <ClusterError error={q.error} />
      )}
    </Modal>
  );
}

function RenameModal({ current, onClose, onDone }: { current: string; onClose: () => void; onDone: (d: Overview) => void }) {
  const { t } = useTranslation();
  const [name, setName] = useState(current);
  const m = useMutation({
    mutationFn: () => api.put<Overview>("/cluster/self", { name }),
    onSuccess: (d) => {
      onDone(d);
      onClose();
    },
  });
  return (
    <Modal title={t("cluster.rename")} onClose={onClose} icon={<Pencil />}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          m.mutate();
        }}
      >
        <Field label={t("cluster.node_name")} hint={t("cluster.node_name_d")}>
          <input className="input" required maxLength={63} pattern="[A-Za-z0-9][A-Za-z0-9._\-]*" value={name} onChange={(e) => setName(e.target.value)} />
        </Field>
        <ClusterError error={m.error} />
        <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
          <button type="button" className="btn" onClick={onClose}>{t("common.cancel")}</button>
          <button className="btn primary" disabled={m.isPending}>{t("common.save")}</button>
        </div>
      </form>
    </Modal>
  );
}

function ConfirmModal({ title, text, action, run, onClose, onDone }: {
  title: string; text: string; action: string; run: () => Promise<Overview>; onClose: () => void; onDone: (d: Overview) => void;
}) {
  const { t } = useTranslation();
  const m = useMutation({
    mutationFn: run,
    onSuccess: (d) => {
      onDone(d);
      onClose();
    },
  });
  return (
    <Modal title={title} onClose={onClose}>
      <p style={{ marginTop: 0 }}>{text}</p>
      <ClusterError error={m.error} />
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end", marginTop: 12 }}>
        <button className="btn" onClick={onClose}>{t("common.cancel")}</button>
        <button className="btn danger" onClick={() => m.mutate()} disabled={m.isPending}>{action}</button>
      </div>
    </Modal>
  );
}

function WhatSyncs() {
  const { t } = useTranslation();
  return (
    <div className="grid two" style={{ marginTop: 16, alignItems: "start" }}>
      <Card title={t("cluster.synced_title")}>
        <p className="muted" style={{ marginTop: 0 }}>{t("cluster.synced_d")}</p>
        <ul style={{ margin: 0, paddingLeft: 18, listStyle: "disc" }}>
          {SYNCED.map((k) => <li key={k}>{t(`cluster.sync_${k}`)}</li>)}
        </ul>
      </Card>
      <Card title={t("cluster.local_title")}>
        <p className="muted" style={{ marginTop: 0 }}>{t("cluster.local_d")}</p>
        <ul style={{ margin: 0, paddingLeft: 18, listStyle: "disc" }}>
          {LOCAL.map((k) => <li key={k}>{t(`cluster.local_${k}`)}</li>)}
        </ul>
      </Card>
    </div>
  );
}

function StandbyPanel({ data, onDone }: { data: Overview; onDone: (d: Overview) => void }) {
  const { t, i18n } = useTranslation();
  const [promoting, setPromoting] = useState(false);
  const st = data.failover_state ?? {};
  return (
    <div className="callout" style={{ marginBottom: 16, display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
      <LifeBuoy size={18} />
      <span style={{ flex: 1, minWidth: 240 }}>
        {t("cluster.standby_banner", { primary: data.primary ?? "" })}{" "}
        {st.active && t("cluster.failover_active_since", { when: formatDate(st.since, i18n.language) })}
      </span>
      {data.primary_url && (
        <a className="btn small" href={data.primary_url} target="_blank" rel="noreferrer">{t("cluster.open_primary")}</a>
      )}
      <button className="btn small" onClick={() => setPromoting(true)}>
        <ArrowUpCircle /> {t("cluster.promote")}
      </button>
      {promoting && (
        <ConfirmModal
          title={t("cluster.promote_title")}
          text={t("cluster.promote_d", { primary: data.primary ?? "" })}
          action={t("cluster.promote")}
          run={() => api.post<Overview>("/cluster/promote")}
          onClose={() => setPromoting(false)}
          onDone={onDone}
        />
      )}
    </div>
  );
}

function ModeCard({ data, onDone }: { data: Overview; onDone: (d: Overview) => void }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [mode, setMode] = useState<Mode>(data.mode);
  useEffect(() => setMode(data.mode), [data.mode]);
  const m = useMutation({
    mutationFn: () => api.put<Overview>("/cluster/mode", { mode }),
    onSuccess: (d) => {
      onDone(d);
      toast(t("common.saved"));
    },
  });
  return (
    <Card title={t("cluster.mode")}>
      <div className="channel" role="radiogroup" aria-label={t("cluster.mode")}>
        {(["active", "standby"] as const).map((x) => (
          <label key={x}>
            <span className="nm">
              <input type="radio" name="cluster-mode" checked={mode === x} onChange={() => setMode(x)} />
              {t(`cluster.mode_${x}`)}
            </span>
            <span className="ds">{t(`cluster.mode_${x}_d`)}</span>
          </label>
        ))}
      </div>
      {mode === "standby" && data.mode !== "standby" && (
        <p className="muted">{t("cluster.mode_switch_note", { name: data.nodes.find((n) => n.is_self)?.name ?? "" })}</p>
      )}
      <ClusterError error={m.error} />
      <button className="btn primary" style={{ marginTop: 12 }} disabled={mode === data.mode || m.isPending} onClick={() => m.mutate()}>
        {t("common.save")}
      </button>
    </Card>
  );
}

type MxPlan = { plans: { domain: string; changes: { action: string; after: { content: string; priority: number } | null; before: { content: string; priority: number } | null }[] }[]; skipped: { domain: string; reason: string }[] };

function MxCard({ mode }: { mode: Mode }) {
  const { t } = useTranslation();
  const toast = useToast();
  const [plan, setPlan] = useState<MxPlan | null>(null);
  const preview = useMutation({ mutationFn: () => api.post<MxPlan>("/cluster/mx/plan"), onSuccess: setPlan });
  const apply = useMutation({
    mutationFn: () => api.post<{ domains: string[] }>("/cluster/mx/apply"),
    onSuccess: (r) => {
      setPlan(null);
      toast(t("cluster.mx_done", { count: r.domains.length }));
    },
  });
  return (
    <Card title={t("cluster.mx_title")}>
      <p className="muted" style={{ marginTop: 0 }}>{t(mode === "standby" ? "cluster.mx_d_standby" : "cluster.mx_d_active")}</p>
      <ClusterError error={preview.error || apply.error} />
      {plan && (
        <div style={{ marginBottom: 12 }}>
          {plan.plans.length === 0 ? (
            <p>{t("cluster.mx_nothing")}</p>
          ) : (
            <ul style={{ paddingLeft: 18, listStyle: "disc", margin: "0 0 8px" }}>
              {plan.plans.map((p) => (
                <li key={p.domain}>
                  <b>{p.domain}</b>:{" "}
                  <span className="mono">
                    {p.changes.filter((c) => c.after).map((c) => `${c.after!.priority} ${c.after!.content}`).join(", ")}
                  </span>
                </li>
              ))}
            </ul>
          )}
          {plan.skipped.length > 0 && (
            <p className="muted">{t("cluster.mx_skipped", { list: plan.skipped.map((x) => x.domain).join(", ") })}</p>
          )}
        </div>
      )}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button className="btn" onClick={() => preview.mutate()} disabled={preview.isPending}>
          <Mail /> {t("cluster.mx_preview")}
        </button>
        {plan && plan.plans.length > 0 && (
          <button className="btn primary" onClick={() => apply.mutate()} disabled={apply.isPending}>
            {t("cluster.mx_apply", { count: plan.plans.length })}
          </button>
        )}
      </div>
    </Card>
  );
}

function FailoverCard({ data, onDone }: { data: Overview; onDone: (d: Overview) => void }) {
  const { t, i18n } = useTranslation();
  const toast = useToast();
  const [f, setF] = useState<FailoverSettings>(data.failover);
  useEffect(() => setF(data.failover), [data.failover]);
  const standby = data.role === "standby";
  const save = useMutation({
    mutationFn: () => api.put<Overview>("/cluster/failover", f),
    onSuccess: (d) => {
      onDone(d);
      toast(t("common.saved"));
    },
  });
  const pointPrimary = useMutation({
    mutationFn: () => api.post<Overview>("/cluster/failover/point-primary"),
    onSuccess: (d) => {
      onDone(d);
      toast(t("cluster.failover_pointed", { name: data.primary ?? "" }));
    },
  });
  const takeOver = useMutation({
    mutationFn: () => api.post<Overview>("/cluster/failover/take-over"),
    onSuccess: (d) => {
      onDone(d);
      toast(t("cluster.failover_taken"));
    },
  });
  const states = standby
    ? [{ name: data.nodes.find((n) => n.is_self)?.name ?? "", st: data.failover_state }]
    : data.nodes.filter((n) => n.role === "standby").map((n) => ({ name: n.name, st: n.failover }));
  return (
    <div style={{ marginTop: 16 }}>
      <Card title={t("cluster.failover_title")}>
        <p className="muted" style={{ marginTop: 0 }}>{t("cluster.failover_d")}</p>
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <label className="check full">
            <input type="checkbox" disabled={standby} checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />
            <span>{t("cluster.failover_enabled")}</span>
          </label>
          <Field label={t("cluster.failover_hostname")} hint={t("cluster.failover_hostname_d")} full>
            <input className="input mono" disabled={standby} placeholder="smtp.example.nl" value={f.hostname} onChange={(e) => setF({ ...f, hostname: e.target.value })} />
          </Field>
          <Field label={t("cluster.failover_after")} hint={t("cluster.failover_after_d")}>
            <input className="input" type="number" min={2} max={120} disabled={standby} value={f.after_minutes} onChange={(e) => setF({ ...f, after_minutes: +e.target.value })} />
          </Field>
          <Field label={t("cluster.failover_back")} hint={t("cluster.failover_back_d")}>
            <input className="input" type="number" min={2} max={1440} disabled={standby} value={f.back_minutes} onChange={(e) => setF({ ...f, back_minutes: +e.target.value })} />
          </Field>
          <div className="full callout warn"><span>{t("cluster.failover_cert")}</span></div>
          <div className="full">
            <ClusterError error={save.error || pointPrimary.error || takeOver.error} />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              {!standby && <button className="btn primary" disabled={save.isPending}>{t("common.save")}</button>}
              {data.failover.hostname && (
                <button type="button" className="btn" onClick={() => pointPrimary.mutate()} disabled={pointPrimary.isPending}>
                  {t("cluster.failover_point_primary", { name: data.primary ?? "" })}
                </button>
              )}
              {standby && data.failover.hostname && !data.failover_state?.active && (
                <button type="button" className="btn danger" onClick={() => takeOver.mutate()} disabled={takeOver.isPending}>
                  {t("cluster.failover_take_over")}
                </button>
              )}
            </div>
          </div>
        </form>
        {data.failover.enabled && states.length > 0 && (
          <div style={{ marginTop: 16 }}>
            {states.map(({ name, st }) => (
              <div key={name} className="cell-sub" style={{ marginTop: 4 }}>
                <b>{name}</b>:{" "}
                {!st || !st.checked_at
                  ? t("cluster.failover_unknown")
                  : st.active
                    ? t("cluster.failover_state_active", { when: formatDate(st.since, i18n.language) })
                    : st.down_since
                      ? t("cluster.failover_state_down", { when: formatDate(st.down_since, i18n.language) })
                      : t("cluster.failover_state_ok", { when: formatDate(st.checked_at, i18n.language) })}
                {st?.error && <span style={{ color: "var(--bad)" }}> · {nodeError(t, st.error)}</span>}
              </div>
            ))}
          </div>
        )}
      </Card>
    </div>
  );
}
