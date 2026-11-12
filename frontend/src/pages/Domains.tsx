import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowLeftRight, CheckCircle2, Globe, Plus, RefreshCw, Trash2 } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { DataTable, type Column } from "../components/DataTable";
import GatewayError from "../components/GatewayError";
import { Card, Drawer, Field, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";
import type { AddressRoute, Destination, DomainRow, GatewayStatus, TlsPolicy } from "../lib/gateway";
import { describeDestination, emptyDestination, numOrNull, numStr } from "../lib/gateway";
import { KindPicker, RelayDrawer, RelayTable } from "./RelayDomains";

type Form = Omit<DomainRow, "id" | "configured" | "users" | "relay_networks" | "smtp_accounts">;
const EMPTY: Form = {
  name: "", kind: "transport", sender_lock: false, destinations: [emptyDestination()], enabled: true, tls: "may", verify_recipients: true,
  concurrency: null, rate_delay: null, out_per_hour: null, out_per_day: null, note: "",
};

type Editing =
  | { kind: "transport"; domain: DomainRow | null; name?: string }
  | { kind: "relay"; domain: DomainRow | null; name?: string };

export default function Domains({ me }: { me: Me }) {
  const { t } = useTranslation();
  const admin = me.role === "admin";
  const domains = useQuery({ queryKey: ["gw-domains"], queryFn: () => api.get<DomainRow[]>("/gateway/domains") });
  const [tab, setTab] = useState<"transport" | "relay">("transport");
  const [picking, setPicking] = useState(false);
  const [editing, setEditing] = useState<Editing | null>(null);
  const all = domains.data ?? [];
  const relay = all.filter((d) => d.configured && d.kind === "relay");
  const transport = all.filter((d) => !d.configured || d.kind !== "relay");
  const list = tab === "relay" ? relay : transport;
  // Converting keeps name, limits and senders; a transport domain then still needs its mail server.
  const toTransport = (d: DomainRow) => setEditing({ kind: "transport", domain: { ...d, kind: "transport", destinations: d.destinations.length ? d.destinations : [emptyDestination()] } });
  const toRelay = (d: DomainRow) => setEditing({ kind: "relay", domain: { ...d, kind: "relay" } });
  // Opened from the search box (Ctrl+K): /domains?open=example.nl
  const [params, setParams] = useSearchParams();
  useEffect(() => {
    const name = params.get("open");
    const d = name && all.find((x) => x.name === name);
    if (!d) return;
    const isRelay = d.configured && d.kind === "relay";
    setTab(isRelay ? "relay" : "transport");
    setEditing({ kind: isRelay ? "relay" : "transport", domain: d });
    setParams({}, { replace: true });
  }, [params, all, setParams]);
  const status = (d: DomainRow) => (!d.configured ? "not_configured" : d.enabled ? "active" : "paused");
  const columns: Column<DomainRow>[] = [
    { key: "name", label: t("domains.name"), always: true, sort: (d) => d.name, mobile: "title",
      render: (d) => (
        <>
          <div className="cell-main">{d.name}</div>
          {d.users.length > 0 && <div className="cell-sub">{t("domains.users_count", { count: d.users.length })}</div>}
        </>
      ) },
    { key: "dest", label: t("domains.delivers_to"), sort: (d) => (d.destinations[0] ? describeDestination(d.destinations[0]) : ""),
      text: (d) => d.destinations.map(describeDestination).join(" "),
      render: (d) => (
        <span className="mono">
          {d.configured ? d.destinations.map((x, i) => <div key={i}>{i > 0 && <span className="muted">{t("domains.backup")} </span>}{describeDestination(x)}</div>) : <span className="muted">—</span>}
        </span>
      ) },
    { key: "queue", label: t("domains.queue"), nowrap: true, optional: false, mobile: "hide",
      render: (d) => <span className="muted">{d.configured && (d.rate_delay ? t("domains.queue_delay", { s: d.rate_delay }) : t("domains.queue_conc", { n: d.concurrency ?? t("domains.default") }))}</span> },
    { key: "status", label: t("domains.status"), sort: status, text: (d) => t(`domains.${status(d)}`), nowrap: true, mobile: "aside",
      render: (d) => <span className={`status ${{ not_configured: "s-warn", active: "s-ok", paused: "s-mute" }[status(d)]}`}>{t(`domains.${status(d)}`)}</span> },
  ];
  return (
    <>
      <PageHead
        title={t("domains.title")}
        sub={admin ? t("relaydom.page_sub") : t("domains.sub_customer")}
        actions={admin && (
          <button className="btn primary" onClick={() => setPicking(true)}>
            <Plus /> {t("domains.add")}
          </button>
        )}
      />
      {admin && <ApplyStatus />}
      <Card flush>
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "transport"} onClick={() => setTab("transport")}>
            {t("relaydom.tab_transport")} <span className="tag">{transport.length}</span>
          </button>
          <button type="button" role="tab" aria-selected={tab === "relay"} onClick={() => setTab("relay")}>
            {t("relaydom.tab_relay")} <span className="tag lucht">{relay.length}</span>
          </button>
        </div>
        {domains.isLoading ? (
          <Loading />
        ) : tab === "relay" ? (
          <RelayTable domains={relay} admin={admin} onOpen={(d) => setEditing({ kind: "relay", domain: d })} />
        ) : (
          <DataTable
            id="domains"
            rows={list}
            rowKey={(d) => d.id}
            columns={columns}
            defaultSort={{ key: "name", dir: 1 }}
            empty={t("domains.empty")}
            onRowClick={(d) => setEditing({ kind: "transport", domain: d })}
          />
        )}
      </Card>
      {tab === "relay" && <div className="callout"><Globe aria-hidden /><span>{t("relaydom.explain")}</span></div>}
      {admin && tab === "transport" && <AddressRoutes />}
      {admin && tab === "transport" && <TlsPolicies />}
      {picking && (
        <KindPicker initial={tab} onClose={() => setPicking(false)} onPick={(name, kind) => {
          setPicking(false);
          setTab(kind);
          setEditing({ kind, domain: null, name });
        }} />
      )}
      {editing?.kind === "transport" && (
        <DomainDrawer key={`t${editing.domain?.id ?? "new"}${editing.domain?.kind}`} domain={editing.domain} name={editing.name} readOnly={!admin}
          onClose={() => setEditing(null)} onConvert={admin ? toRelay : undefined} />
      )}
      {editing?.kind === "relay" && (
        <RelayDrawer key={`r${editing.domain?.id ?? "new"}`} domain={editing.domain} name={editing.name} readOnly={!admin} admin={admin}
          onClose={() => setEditing(null)} onConvert={toTransport} />
      )}
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

function DomainDrawer({ domain, name, readOnly, onClose, onConvert }: {
  domain: DomainRow | null; name?: string; readOnly: boolean; onClose: () => void; onConvert?: (d: DomainRow) => void;
}) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const start: Form = domain?.configured ? { ...domain, kind: "transport" } : domain ? { ...EMPTY, name: domain.name } : { ...EMPTY, name: name ?? "" };
  const converting = domain?.configured && domain.destinations.every((d) => !d.host);
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
    <Drawer title={domain ? domain.name : t("domains.add")}
      sub={domain && !domain.configured ? t("domains.not_configured_hint") : converting ? t("relaydom.converting_hint") : <span className="tag">{t("relaydom.kind_transport")}</span>} onClose={onClose}>
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
          <input type="checkbox" disabled={readOnly} checked={f.sender_lock} onChange={(e) => setF({ ...f, sender_lock: e.target.checked })} />
          <span>
            {t("relaydom.lock", { domain: f.name || "example.nl" })}
            <br /><span className="hint">{t("relaydom.lock_hint")}</span>
          </span>
        </label>
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
            {domain?.configured && onConvert && !converting && (
              <button type="button" className="btn ghost small" style={{ marginLeft: "auto" }} onClick={() => onConvert(domain)}>
                <ArrowLeftRight /> {t("relaydom.to_relay")}
              </button>
            )}
            {domain && (
              <button type="button" className="btn danger" style={onConvert && !converting && domain.configured ? undefined : { marginLeft: "auto" }}
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
