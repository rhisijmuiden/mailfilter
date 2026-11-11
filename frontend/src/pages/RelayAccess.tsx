import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Network, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import GatewayError from "../components/GatewayError";
import { Card, Loading, PageHead, useToast } from "../components/ui";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";
import type { DomainRow, Network as Net, Port } from "../lib/gateway";

export default function RelayAccess({ me }: { me: Me }) {
  const { t } = useTranslation();
  const admin = me.role === "admin";
  return (
    <>
      <PageHead title={t("relay.title")} sub={admin ? t("relay.sub") : t("relay.sub_customer")} />
      <Networks admin={admin} />
      {admin && <Ports />}
    </>
  );
}

function Networks({ admin }: { admin: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const nets = useQuery({ queryKey: ["gw-nets"], queryFn: () => api.get<Net[]>("/gateway/relay-networks") });
  const domains = useQuery({ queryKey: ["gw-domains"], queryFn: () => api.get<DomainRow[]>("/gateway/domains") });
  const myDomains = domains.data?.map((d) => d.name) ?? [];
  const [f, setF] = useState({ cidr: "", description: "", domain: "", allow_broad: false });
  const refresh = () => {
    qc.invalidateQueries({ queryKey: ["gw-nets"] });
    qc.invalidateQueries({ queryKey: ["gw-status"] });
  };
  const add = useMutation({
    mutationFn: () => api.post("/gateway/relay-networks", { ...f, domain: f.domain || (admin ? null : myDomains[0]) }),
    onSuccess: () => {
      refresh();
      toast(t("relay.added", { cidr: f.cidr }));
      setF({ cidr: "", description: "", domain: f.domain, allow_broad: false });
    },
  });
  const toggle = useMutation({
    mutationFn: (n: Net) => api.put(`/gateway/relay-networks/${n.id}`, { ...n, enabled: !n.enabled }),
    onSuccess: refresh,
  });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/gateway/relay-networks/${id}`), onSuccess: refresh });
  const broadNeeded = add.error && (add.error as any).info?.problems?.some((p: { code: string }) => p.code === "network_too_broad");

  return (
    <Card title={t("relay.networks")} flush>
      {/* The one thing people come here for: "this IP may relay" */}
      <form className="filters" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input className="input mono" style={{ flex: "1 1 220px" }} required placeholder={t("relay.cidr_placeholder")} aria-label={t("relay.cidr")}
          value={f.cidr} onChange={(e) => setF({ ...f, cidr: e.target.value })} />
        <input className="input" style={{ flex: "1 1 200px" }} placeholder={t("relay.description_placeholder")} aria-label={t("relay.description")}
          value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        {(admin || myDomains.length > 1) && (
          <select className="select" style={{ flex: "0 1 200px" }} aria-label={t("relay.domain")} value={f.domain} onChange={(e) => setF({ ...f, domain: e.target.value })}>
            {admin && <option value="">{t("relay.no_customer")}</option>}
            {myDomains.map((d) => <option key={d} value={d}>{d}</option>)}
          </select>
        )}
        <button className="btn primary" disabled={add.isPending}>
          <Plus /> {t("relay.allow")}
        </button>
      </form>
      <div style={{ padding: "0 14px" }}>
        <GatewayError error={add.error || toggle.error || del.error} />
        {admin && broadNeeded && (
          <label className="check" style={{ margin: "8px 0" }}>
            <input type="checkbox" checked={f.allow_broad} onChange={(e) => setF({ ...f, allow_broad: e.target.checked })} />
            <span>{t("relay.confirm_broad")}</span>
          </label>
        )}
        <p className="muted" style={{ fontSize: 12.5 }}>{t("relay.hint")}</p>
      </div>
      {nets.isLoading ? (
        <Loading />
      ) : !nets.data?.length ? (
        <div className="empty">{t("relay.empty")}</div>
      ) : (
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("relay.cidr")}</th>
                <th>{t("relay.description")}</th>
                <th>{t("relay.domain")}</th>
                <th>{t("relay.status")}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {nets.data.map((n) => (
                <tr key={n.id}>
                  <td className="mono nowrap">
                    <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><Network style={{ width: 14, height: 14 }} aria-hidden />{n.cidr}</span>
                  </td>
                  <td>{n.description || <span className="muted">—</span>}</td>
                  <td>{n.domain ? <span className="tag">{n.domain}</span> : <span className="muted">{t("relay.no_customer")}</span>}</td>
                  <td className="nowrap">
                    <label className="switch" style={{ display: "inline-block" }} title={n.enabled ? t("relay.on") : t("relay.off")}>
                      <input type="checkbox" checked={n.enabled} onChange={() => toggle.mutate(n)} aria-label={t("relay.enabled")} />
                      <span />
                    </label>
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <button className="icon-btn" aria-label={t("common.delete")}
                      onClick={() => window.confirm(t("relay.delete_confirm", { cidr: n.cidr })) && del.mutate(n.id)}>
                      <Trash2 />
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}

function Ports() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const ports = useQuery({ queryKey: ["gw-ports"], queryFn: () => api.get<Port[]>("/gateway/relay-ports") });
  const [f, setF] = useState({ port: "", tls: "may", description: "" });
  const refresh = () => qc.invalidateQueries({ queryKey: ["gw-ports"] });
  const add = useMutation({
    mutationFn: () => api.post("/gateway/relay-ports", { ...f, port: Number(f.port) }),
    onSuccess: () => { refresh(); setF({ port: "", tls: "may", description: "" }); },
  });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/gateway/relay-ports/${id}`), onSuccess: refresh });
  return (
    <Card title={t("relay.ports")} right={<span className="muted">{t("relay.ports_sub")}</span>} flush>
      <form className="filters" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <input className="input" style={{ width: 110 }} type="number" min={1} max={65535} required placeholder="2525" aria-label={t("relay.port")}
          value={f.port} onChange={(e) => setF({ ...f, port: e.target.value })} />
        <select className="select" style={{ width: 210 }} aria-label={t("relay.tls")} value={f.tls} onChange={(e) => setF({ ...f, tls: e.target.value })}>
          <option value="may">{t("relay.tls_may")}</option>
          <option value="encrypt">{t("relay.tls_encrypt")}</option>
          <option value="wrapper">{t("relay.tls_wrapper")}</option>
        </select>
        <input className="input" style={{ flex: "1 1 200px" }} placeholder={t("relay.description_placeholder")} aria-label={t("relay.description")}
          value={f.description} onChange={(e) => setF({ ...f, description: e.target.value })} />
        <button className="btn" disabled={add.isPending}><Plus /> {t("relay.add_port")}</button>
      </form>
      <div style={{ padding: "0 14px" }}><GatewayError error={add.error || del.error} /></div>
      <div className="tbl-wrap">
        <table>
          <tbody>
            {[{ id: -1, port: 587, tls: "encrypt", description: t("relay.fixed_587") }, { id: -2, port: 465, tls: "wrapper", description: t("relay.fixed_465") },
              ...(ports.data ?? [])].map((p) => (
              <tr key={p.id}>
                <td className="mono">{p.port}</td>
                <td>{t(`relay.tls_${p.tls}`)}</td>
                <td>{p.description}</td>
                <td style={{ textAlign: "right" }}>
                  {p.id > 0 ? (
                    <button className="icon-btn" aria-label={t("common.delete")}
                      onClick={() => window.confirm(t("relay.delete_port_confirm", { port: p.port })) && del.mutate(p.id)}>
                      <Trash2 />
                    </button>
                  ) : <span className="tag">{t("relay.fixed")}</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}
