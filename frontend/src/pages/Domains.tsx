import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Globe, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import GatewayError from "../components/GatewayError";
import { Card, Drawer, Field, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";
import type { AddressRoute, Destination, DomainRow, GatewayStatus, TlsPolicy } from "../lib/gateway";
import { describeDestination, emptyDestination, numOrNull, numStr } from "../lib/gateway";

type Form = Omit<DomainRow, "id" | "configured" | "users" | "relay_networks">;
const EMPTY: Form = {
  name: "", destinations: [emptyDestination()], enabled: true, tls: "may", verify_recipients: true,
  concurrency: null, rate_delay: null, out_per_hour: null, out_per_day: null, note: "",
};

export default function Domains({ me }: { me: Me }) {
  const { t } = useTranslation();
  const admin = me.role === "admin";
  const domains = useQuery({ queryKey: ["gw-domains"], queryFn: () => api.get<DomainRow[]>("/gateway/domains") });
  const [editing, setEditing] = useState<DomainRow | "new" | null>(null);
  return (
    <>
      <PageHead
        title={t("domains.title")}
        sub={admin ? t("domains.sub") : t("domains.sub_customer")}
        actions={admin && (
          <button className="btn primary" onClick={() => setEditing("new")}>
            <Plus /> {t("domains.add")}
          </button>
        )}
      />
      {admin && <ApplyStatus />}
      <Card flush>
        {domains.isLoading ? (
          <Loading />
        ) : !domains.data?.length ? (
          <div className="empty">{t("domains.empty")}</div>
        ) : (
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("domains.name")}</th>
                  <th>{t("domains.delivers_to")}</th>
                  <th>{t("domains.queue")}</th>
                  <th>{t("domains.status")}</th>
                </tr>
              </thead>
              <tbody>
                {domains.data.map((d) => (
                  <tr key={d.id} className="click" tabIndex={0} onClick={() => setEditing(d)} onKeyDown={(e) => e.key === "Enter" && setEditing(d)}>
                    <td>
                      <div className="cell-main">{d.name}</div>
                      {d.users.length > 0 && <div className="cell-sub">{t("domains.users_count", { count: d.users.length })}</div>}
                    </td>
                    <td className="mono">
                      {d.configured ? d.destinations.map((x, i) => <div key={i}>{i > 0 && <span className="muted">{t("domains.backup")} </span>}{describeDestination(x)}</div>) : <span className="muted">—</span>}
                    </td>
                    <td className="nowrap muted">
                      {d.configured && (d.rate_delay ? t("domains.queue_delay", { s: d.rate_delay }) : t("domains.queue_conc", { n: d.concurrency ?? t("domains.default") }))}
                    </td>
                    <td className="nowrap">
                      {!d.configured ? (
                        <span className="status s-warn">{t("domains.not_configured")}</span>
                      ) : d.enabled ? (
                        <span className="status s-ok">{t("domains.active")}</span>
                      ) : (
                        <span className="status s-mute">{t("domains.paused")}</span>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {admin && <AddressRoutes />}
      {admin && <TlsPolicies />}
      {editing && <DomainDrawer domain={editing === "new" ? null : editing} readOnly={!admin} onClose={() => setEditing(null)} />}
    </>
  );
}

function ApplyStatus() {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const st = useQuery({ queryKey: ["gw-status"], queryFn: () => api.get<GatewayStatus>("/gateway/status") });
  const again = useMutation({
    mutationFn: () => api.post("/gateway/apply"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["gw-status"] });
      toast(t("gateway.applied"));
    },
  });
  const last = st.data?.last_apply;
  return (
    <>
      <div className="callout" style={{ alignItems: "center" }}>
        <CheckCircle2 aria-hidden />
        <span style={{ flex: 1 }}>
          {last
            ? last.ok
              ? t("gateway.last_ok", { at: formatDate(last.at, i18n.language) })
              : t("gateway.last_failed", { at: formatDate(last.at, i18n.language) })
            : t("gateway.never_applied")}
        </span>
        <button className="btn small" disabled={again.isPending} onClick={() => again.mutate()}>
          <RefreshCw /> {t("gateway.apply_again")}
        </button>
      </div>
      <GatewayError error={again.error} />
    </>
  );
}

function DestinationsEditor({ value, onChange, readOnly }: { value: Destination[]; onChange: (v: Destination[]) => void; readOnly: boolean }) {
  const { t } = useTranslation();
  const set = (i: number, patch: Partial<Destination>) => onChange(value.map((d, j) => (j === i ? { ...d, ...patch } : d)));
  return (
    <div className="field full">
      <span style={{ fontSize: 12.5, fontWeight: 650 }}>{t("domains.destinations")}</span>
      <span className="hint">{t("domains.destinations_hint")}</span>
      {value.map((d, i) => (
        <div key={i} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", marginTop: 6 }}>
          <span className="tag" style={{ minWidth: 70, textAlign: "center" }}>{i === 0 ? t("domains.primary") : t("domains.backup")}</span>
          <input className="input" style={{ flex: "1 1 180px" }} required disabled={readOnly} placeholder="mail.example.nl" aria-label={t("domains.host")}
            value={d.host} onChange={(e) => set(i, { host: e.target.value })} />
          <input className="input" style={{ width: 90 }} type="number" min={1} max={65535} disabled={readOnly} aria-label={t("domains.port")}
            value={d.port} onChange={(e) => set(i, { port: Number(e.target.value) })} />
          <select className="select" style={{ width: 90 }} disabled={readOnly} value={d.protocol} aria-label={t("domains.protocol")}
            onChange={(e) => set(i, { protocol: e.target.value as Destination["protocol"] })}>
            <option value="smtp">SMTP</option>
            <option value="lmtp">LMTP</option>
          </select>
          <label className="check" title={t("domains.mx_hint")}>
            <input type="checkbox" disabled={readOnly} checked={d.mx} onChange={(e) => set(i, { mx: e.target.checked })} />
            <span>{t("domains.mx")}</span>
          </label>
          {!readOnly && value.length > 1 && (
            <button type="button" className="icon-btn" aria-label={t("common.delete")} onClick={() => onChange(value.filter((_, j) => j !== i))}>
              <Trash2 />
            </button>
          )}
        </div>
      ))}
      {!readOnly && value.length < 10 && (
        <div style={{ marginTop: 8 }}>
          <button type="button" className="btn small" onClick={() => onChange([...value, emptyDestination()])}>
            <Plus /> {t("domains.add_backup")}
          </button>
        </div>
      )}
    </div>
  );
}

function DomainDrawer({ domain, readOnly, onClose }: { domain: DomainRow | null; readOnly: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const start: Form = domain?.configured ? { ...domain } : domain ? { ...EMPTY, name: domain.name } : EMPTY;
  const [f, setF] = useState<Form>(start);
  const done = () => {
    qc.invalidateQueries({ queryKey: ["gw-domains"] });
    qc.invalidateQueries({ queryKey: ["gw-status"] });
    qc.invalidateQueries({ queryKey: ["domain-options"] });
  };
  const save = useMutation({
    mutationFn: () => (domain ? api.put(`/gateway/domains/${domain.id}`, f) : api.post("/gateway/domains", f)),
    onSuccess: () => {
      done();
      toast(t("gateway.applied"));
      onClose();
    },
  });
  const del = useMutation({
    mutationFn: () => api.del(`/gateway/domains/${domain!.id}`),
    onSuccess: () => {
      done();
      onClose();
    },
  });
  const num = (k: "concurrency" | "rate_delay" | "out_per_hour" | "out_per_day", label: string, hint: string, max: number) => (
    <Field label={label} hint={hint}>
      <input className="input" type="number" min={k === "rate_delay" ? 0 : 1} max={max} disabled={readOnly} placeholder={t("domains.default")}
        value={numStr(f[k])} onChange={(e) => setF({ ...f, [k]: numOrNull(e.target.value) })} />
    </Field>
  );
  return (
    <Drawer title={domain ? domain.name : t("domains.add")} sub={domain && !domain.configured ? t("domains.not_configured_hint") : undefined} onClose={onClose}>
      <form className="form-grid" onSubmit={(e) => { e.preventDefault(); if (!readOnly) save.mutate(); }}>
        <div className="full"><GatewayError error={save.error || del.error} /></div>
        {!domain && (
          <Field label={t("domains.name")} full>
            <input className="input" required autoFocus placeholder="example.nl" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
          </Field>
        )}
        <DestinationsEditor value={f.destinations} readOnly={readOnly} onChange={(destinations) => setF({ ...f, destinations })} />
        <Field label={t("domains.tls")} hint={t("domains.tls_hint")}>
          <select className="select" disabled={readOnly} value={f.tls} onChange={(e) => setF({ ...f, tls: e.target.value as Form["tls"] })}>
            <option value="may">{t("domains.tls_may")}</option>
            <option value="encrypt">{t("domains.tls_encrypt")}</option>
            <option value="verify">{t("domains.tls_verify")}</option>
          </select>
        </Field>
        <div className="field">
          <label className="check" style={{ marginTop: 24 }}>
            <input type="checkbox" disabled={readOnly} checked={f.verify_recipients} onChange={(e) => setF({ ...f, verify_recipients: e.target.checked })} />
            <span>{t("domains.verify")}</span>
          </label>
          <span className="hint">{t("domains.verify_hint")}</span>
        </div>
        <div className="full"><h3 style={{ margin: "8px 0 0", fontSize: 14 }}>{t("domains.queue_title")}</h3></div>
        {num("concurrency", t("domains.concurrency"), t("domains.concurrency_hint"), 100)}
        {num("rate_delay", t("domains.rate_delay"), t("domains.rate_delay_hint"), 3600)}
        <div className="full"><h3 style={{ margin: "8px 0 0", fontSize: 14 }}>{t("domains.outbound_title")}</h3></div>
        {num("out_per_hour", t("domains.per_hour"), t("domains.outbound_hint"), 10_000_000)}
        {num("out_per_day", t("domains.per_day"), t("domains.outbound_hint"), 10_000_000)}
        <Field label={t("domains.note")} full>
          <textarea className="input" rows={2} disabled={readOnly} value={f.note} onChange={(e) => setF({ ...f, note: e.target.value })} />
        </Field>
        <label className="check full">
          <input type="checkbox" disabled={readOnly} checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} />
          <span>{t("domains.enabled")}</span>
        </label>
        {domain?.users.length ? (
          <div className="full muted" style={{ fontSize: 13 }}>{t("domains.linked_users")}: {domain.users.join(", ")}</div>
        ) : null}
        {!readOnly && (
          <div className="full" style={{ display: "flex", gap: 8 }}>
            <button className="btn primary" disabled={save.isPending}>
              <Globe /> {save.isPending ? t("gateway.applying") : t("common.save")}
            </button>
            <button type="button" className="btn ghost" onClick={onClose}>{t("common.cancel")}</button>
            {domain && (
              <button type="button" className="btn danger" style={{ marginLeft: "auto" }}
                onClick={() => window.confirm(t("domains.delete_confirm", { name: domain.name })) && del.mutate()}>
                {t("common.delete")}
              </button>
            )}
          </div>
        )}
      </form>
    </Drawer>
  );
}

// ---- exceptions: single addresses routed elsewhere, TLS per destination ------------
function AddressRoutes() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const rows = useQuery({ queryKey: ["gw-routes"], queryFn: () => api.get<AddressRoute[]>("/gateway/address-routes") });
  const [f, setF] = useState({ address: "", host: "", port: 25, protocol: "smtp" as const, mx: false, note: "" });
  const add = useMutation({
    mutationFn: () => api.post("/gateway/address-routes", f),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["gw-routes"] }); setF({ ...f, address: "", host: "", note: "" }); },
  });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/gateway/address-routes/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["gw-routes"] }) });
  return (
    <Card title={t("domains.routes_title")} right={<span className="muted">{t("domains.routes_sub")}</span>} flush>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input className="input" style={{ flex: "1 1 200px" }} required placeholder="info@example.nl" aria-label={t("domains.address")} value={f.address} onChange={(e) => setF({ ...f, address: e.target.value })} />
        <input className="input" style={{ flex: "1 1 160px" }} required placeholder="mail2.example.nl" aria-label={t("domains.host")} value={f.host} onChange={(e) => setF({ ...f, host: e.target.value })} />
        <input className="input" style={{ width: 90 }} type="number" min={1} max={65535} aria-label={t("domains.port")} value={f.port} onChange={(e) => setF({ ...f, port: Number(e.target.value) })} />
        <button className="btn" disabled={add.isPending}><Plus /> {t("common.add")}</button>
      </form>
      <div style={{ padding: "0 14px" }}><GatewayError error={add.error || del.error} /></div>
      {rows.data?.length ? (
        <div className="tbl-wrap">
          <table>
            <tbody>
              {rows.data.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.address}</td>
                  <td className="mono">{describeDestination(r)}</td>
                  <td style={{ textAlign: "right" }}>
                    <button className="icon-btn" aria-label={t("common.delete")} onClick={() => del.mutate(r.id)}><Trash2 /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty">{t("domains.routes_empty")}</div>
      )}
    </Card>
  );
}

function TlsPolicies() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const rows = useQuery({ queryKey: ["gw-tls"], queryFn: () => api.get<TlsPolicy[]>("/gateway/tls-policies") });
  const [f, setF] = useState({ destination: "", level: "encrypt", note: "" });
  const add = useMutation({
    mutationFn: () => api.post("/gateway/tls-policies", f),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["gw-tls"] }); setF({ ...f, destination: "" }); },
  });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/gateway/tls-policies/${id}`), onSuccess: () => qc.invalidateQueries({ queryKey: ["gw-tls"] }) });
  const levels = ["none", "may", "encrypt", "dane", "verify", "secure"];
  return (
    <Card title={t("domains.tls_title")} right={<span className="muted">{t("domains.tls_sub")}</span>} flush>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input className="input" style={{ flex: "1 1 220px" }} required placeholder={t("domains.tls_placeholder")} aria-label={t("domains.tls_destination")} value={f.destination} onChange={(e) => setF({ ...f, destination: e.target.value })} />
        <select className="select" style={{ width: 150 }} value={f.level} aria-label={t("domains.tls")} onChange={(e) => setF({ ...f, level: e.target.value })}>
          {levels.map((l) => <option key={l} value={l}>{t(`domains.level_${l}`)}</option>)}
        </select>
        <button className="btn" disabled={add.isPending}><Plus /> {t("common.add")}</button>
      </form>
      <div style={{ padding: "0 14px" }}><GatewayError error={add.error || del.error} /></div>
      {rows.data?.length ? (
        <div className="tbl-wrap">
          <table>
            <tbody>
              {rows.data.map((r) => (
                <tr key={r.id}>
                  <td className="mono">{r.destination}</td>
                  <td>{t(`domains.level_${r.level}`)}</td>
                  <td style={{ textAlign: "right" }}>
                    <button className="icon-btn" aria-label={t("common.delete")} onClick={() => del.mutate(r.id)}><Trash2 /></button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="empty">{t("domains.tls_empty")}</div>
      )}
    </Card>
  );
}
