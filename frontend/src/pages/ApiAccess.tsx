// API keys and the documentation of the public REST API (/api/v1).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Braces, Check, ChevronDown, Copy, Download, KeyRound, Plus, ShieldCheck, Trash2 } from "lucide-react";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { DataTable, type Column } from "../components/DataTable";
import { Card, Drawer, ErrorText, Field, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";

type Level = "read" | "write";
type Area = { key: string; write: boolean; admin_only: boolean };
type Areas = { areas: Area[]; rate_limit: number };
export type ApiKey = {
  id: number; name: string; hint: string; owner: string; owner_role: "admin" | "customer"; owner_domains: string[];
  scopes: Record<string, Level>; allowed_ips: string[]; expires_at: string | null; expired: boolean;
  created_at: string; created_by: string; last_used_at: string | null; last_used_ip: string | null;
};
type UserRow = { id: number; email: string; name: string; role: "admin" | "customer"; is_active: boolean };

const TAB = { display: "inline-flex", alignItems: "center", gap: 6 } as const;
// Same order as the backend's AREAS (JSON objects from the database come back sorted differently).
const AREA_ORDER = ["domains", "relay", "smtp", "quarantine", "trace", "lists", "users", "audit"];
const BASE = () => `${window.location.origin}/api/v1`;

function useCopy() {
  const { t } = useTranslation();
  const toast = useToast();
  return (text: string) =>
    navigator.clipboard?.writeText(text).then(() => toast(t("common.copied"), "success"), () => toast(t("errors.error"), "error"));
}

export default function ApiAccess({ me }: { me: Me }) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const tab = params.get("tab") === "docs" ? "docs" : "keys";
  const setTab = (v: "keys" | "docs") => setParams(v === "docs" ? { tab: "docs" } : {}, { replace: true });
  return (
    <>
      <PageHead title={t("apikeys.title")} sub={t(me.role === "admin" ? "apikeys.sub_admin" : "apikeys.sub_customer")} />
      <Card flush>
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "keys"} onClick={() => setTab("keys")} style={TAB}>
            <KeyRound style={{ width: 15, height: 15 }} aria-hidden />{t("apikeys.tab_keys")}
          </button>
          <button type="button" role="tab" aria-selected={tab === "docs"} onClick={() => setTab("docs")} style={TAB}>
            <Braces style={{ width: 15, height: 15 }} aria-hidden />{t("apikeys.tab_docs")}
          </button>
        </div>
        {tab === "keys" ? <Keys me={me} /> : <Docs />}
      </Card>
    </>
  );
}

// ---- keys ------------------------------------------------------------------------
function ScopeTags({ k }: { k: ApiKey }) {
  const { t } = useTranslation();
  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 4 }}>
      {Object.entries(k.scopes).sort(([a], [b]) => AREA_ORDER.indexOf(a) - AREA_ORDER.indexOf(b)).map(([area, level]) => (
        <span key={area} className={`tag${level === "write" ? " lucht" : ""}`}>
          {t(`apikeys.area.${area}`)} · {t(`apikeys.level_${level}`)}
        </span>
      ))}
    </div>
  );
}

function Keys({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const admin = me.role === "admin";
  const keys = useQuery({ queryKey: ["api-keys"], queryFn: () => api.get<ApiKey[]>("/api-keys") });
  const [editing, setEditing] = useState<ApiKey | "new" | null>(null);

  const columns: Column<ApiKey>[] = [
    { key: "name", label: t("apikeys.name"), always: true, sort: (k) => k.name, mobile: "title",
      render: (k) => (
        <>
          <div className="cell-main">{k.name}</div>
          <div className="cell-sub mono">{k.hint}…</div>
        </>
      ) },
    ...(admin ? [{ key: "owner", label: t("apikeys.owner"), sort: (k: ApiKey) => k.owner, mobileLabel: true,
      render: (k: ApiKey) => (
        <>
          <div className="cell-main" style={{ wordBreak: "break-all" }}>{k.owner}</div>
          <div className="cell-sub">{k.owner_role === "admin" ? t("apikeys.all_domains") : k.owner_domains.join(", ") || "—"}</div>
        </>
      ) } as Column<ApiKey>] : []),
    { key: "scopes", label: t("apikeys.scopes"), text: (k) => Object.keys(k.scopes).join(" "), render: (k) => <ScopeTags k={k} /> },
    { key: "limits", label: t("apikeys.limits"), mobileLabel: true,
      render: (k) => (
        <>
          <div className="cell-main">
            {k.expired ? <span className="status s-bad">{t("apikeys.expired")}</span>
              : k.expires_at ? t("apikeys.until", { date: formatDate(k.expires_at, i18n.language, false) }) : t("apikeys.no_expiry")}
          </div>
          <div className="cell-sub mono" style={{ wordBreak: "break-all" }}>{k.allowed_ips.length ? k.allowed_ips.join(", ") : t("apikeys.any_ip")}</div>
        </>
      ) },
    { key: "used", label: t("apikeys.last_used"), sort: (k) => k.last_used_at ?? "", mobile: "aside",
      render: (k) => k.last_used_at ? (
        <>
          <div className="cell-main nowrap num">{formatDate(k.last_used_at, i18n.language)}</div>
          {k.last_used_ip && <div className="cell-sub mono">{k.last_used_ip}</div>}
        </>
      ) : <span className="muted">{t("apikeys.never")}</span> },
    { key: "created", label: t("apikeys.created"), optional: true, sort: (k) => k.created_at,
      render: (k) => (
        <>
          <div className="cell-main nowrap num">{formatDate(k.created_at, i18n.language, false)}</div>
          <div className="cell-sub">{k.created_by}</div>
        </>
      ) },
  ];

  return (
    <>
      <div style={{ padding: "14px 16px 0", display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn primary" onClick={() => setEditing("new")}><Plus /> {t("apikeys.new")}</button>
        <span className="muted" style={{ fontSize: 13, flex: "1 1 280px" }}>{t("apikeys.intro")}</span>
      </div>
      <DataTable
        id="api-keys"
        rows={keys.data}
        rowKey={(k) => k.id}
        loading={keys.isLoading}
        columns={columns}
        onRowClick={(k) => setEditing(k)}
        rowClass={(k) => (k.expired ? "muted" : undefined)}
        empty={t("apikeys.empty")}
      />
      {editing && <KeyDrawer me={me} apiKey={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}

type Form = { name: string; owner: string; scopes: Record<string, Level>; ips: string; expires: string };

function KeyDrawer({ me, apiKey, onClose }: { me: Me; apiKey: ApiKey | null; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const copy = useCopy();
  const admin = me.role === "admin";
  const areas = useQuery({ queryKey: ["api-key-areas"], queryFn: () => api.get<Areas>("/api-keys/areas") });
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<UserRow[]>("/users"), enabled: admin && !apiKey });
  const [f, setF] = useState<Form>(() => ({
    name: apiKey?.name ?? "",
    owner: apiKey?.owner ?? me.email,
    scopes: apiKey?.scopes ?? { domains: "read", relay: "write" },
    ips: (apiKey?.allowed_ips ?? []).join("\n"),
    expires: apiKey?.expires_at ? apiKey.expires_at.slice(0, 10) : "",
  }));
  const [created, setCreated] = useState<(ApiKey & { key: string }) | null>(null);
  const ownerRole = apiKey ? apiKey.owner_role : f.owner === me.email ? me.role : users.data?.find((u) => u.email === f.owner)?.role ?? "customer";
  const shown = (areas.data?.areas ?? []).filter((a) => !a.admin_only || ownerRole === "admin");

  const body = () => ({
    name: f.name,
    owner: apiKey ? undefined : f.owner,
    scopes: Object.fromEntries(Object.entries(f.scopes).filter(([k]) => shown.some((a) => a.key === k))),
    allowed_ips: f.ips.split(/[\s,]+/).filter(Boolean),
    // End of the chosen day, in the browser's time zone.
    expires_at: f.expires ? new Date(`${f.expires}T23:59:59`).toISOString() : null,
  });
  const save = useMutation({
    mutationFn: () => (apiKey ? api.put<ApiKey>(`/api-keys/${apiKey.id}`, body(), { quiet: true }) : api.post<ApiKey & { key: string }>("/api-keys", body())),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["api-keys"] });
      if (!apiKey) setCreated(res as ApiKey & { key: string });
      else {
        toast(t("common.saved"), "success");
        onClose();
      }
    },
  });
  const revoke = useMutation({
    mutationFn: () => api.del(`/api-keys/${apiKey!.id}`, { quiet: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["api-keys"] });
      toast(t("apikeys.revoked"), "success");
      onClose();
    },
  });
  const setLevel = (area: string, level: Level | "") => {
    const next = { ...f.scopes };
    if (level) next[area] = level;
    else delete next[area];
    setF({ ...f, scopes: next });
  };

  if (created) {
    const example = `curl ${BASE()}/me \\\n  -H "Authorization: Bearer ${created.key}"`;
    return (
      <Drawer title={t("apikeys.created_title")} sub={created.name} onClose={onClose}
        footer={<button className="btn primary" onClick={onClose}><Check /> {t("apikeys.stored_it")}</button>}>
        <div className="callout warn"><KeyRound aria-hidden /><span>{t("apikeys.once")}</span></div>
        <div style={{ display: "flex", gap: 8, alignItems: "stretch" }}>
          <pre className="log" style={{ fontSize: 13.5, flex: 1, whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{created.key}</pre>
          <button type="button" className="btn" onClick={() => copy(created.key)}><Copy /> {t("common.copy")}</button>
        </div>
        <div className="sec-title" style={{ marginTop: 18 }}>{t("apikeys.try_it")}</div>
        <pre className="log" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{example}</pre>
        <div><button type="button" className="btn small ghost" onClick={() => copy(example)}><Copy /> {t("common.copy")}</button></div>
      </Drawer>
    );
  }

  return (
    <Drawer
      title={apiKey ? t("apikeys.edit") : t("apikeys.new")}
      sub={apiKey ? <span className="mono">{apiKey.hint}…</span> : undefined}
      onClose={onClose}
      footer={
        <>
          <button className="btn primary" form="api-key-form" disabled={save.isPending || !Object.keys(f.scopes).length}>
            <ShieldCheck /> {apiKey ? t("common.save") : t("apikeys.create")}
          </button>
          <button type="button" className="btn ghost" onClick={onClose}>{t("common.cancel")}</button>
          {apiKey && (
            <button type="button" className="btn danger" style={{ marginLeft: "auto" }} disabled={revoke.isPending}
              onClick={() => window.confirm(t("apikeys.revoke_confirm", { name: apiKey.name })) && revoke.mutate()}>
              <Trash2 /> {t("apikeys.revoke")}
            </button>
          )}
        </>
      }
    >
      <form id="api-key-form" className="form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="full"><ErrorText error={save.error || revoke.error} /></div>
        <Field label={t("apikeys.name")} hint={t("apikeys.name_hint")} full={!admin || !!apiKey}>
          <input className="input" required maxLength={100} value={f.name} placeholder="DirectAdmin" onChange={(e) => setF({ ...f, name: e.target.value })} />
        </Field>
        {admin && !apiKey && (
          <Field label={t("apikeys.owner")} hint={t("apikeys.owner_hint")}>
            <select className="select" value={f.owner} onChange={(e) => setF({ ...f, owner: e.target.value })}>
              <option value={me.email}>{t("apikeys.owner_me", { email: me.email })}</option>
              {(users.data ?? []).filter((u) => u.email !== me.email && u.is_active).map((u) => (
                <option key={u.id} value={u.email}>{u.email}{u.role === "admin" ? ` (${t("users.admin")})` : ""}</option>
              ))}
            </select>
          </Field>
        )}
        {apiKey && admin && (
          <Field label={t("apikeys.owner")} full>
            <input className="input" disabled value={apiKey.owner} />
          </Field>
        )}

        <div className="field full">
          <span style={{ fontSize: 12.5, fontWeight: 650 }}>{t("apikeys.scopes")}</span>
          <span className="hint">{t(ownerRole === "admin" ? "apikeys.scopes_hint_admin" : "apikeys.scopes_hint_customer")}</span>
          {areas.isLoading ? <Loading /> : (
            <div className="chk-list" style={{ marginTop: 6 }}>
              {shown.map((a) => (
                <div key={a.key} style={{ display: "flex", alignItems: "center", gap: 12, padding: "9px 12px", borderBottom: "1px solid var(--line)", flexWrap: "wrap" }}>
                  <div style={{ flex: "1 1 200px", minWidth: 0 }}>
                    <div style={{ fontWeight: 600 }}>{t(`apikeys.area.${a.key}`)}</div>
                    <div className="muted" style={{ fontSize: 12.5 }}>{t(`apikeys.area_hint.${a.key}`)}</div>
                  </div>
                  <div className="seg" role="group" aria-label={t(`apikeys.area.${a.key}`)}>
                    {(["", "read", ...(a.write ? ["write"] : [])] as (Level | "")[]).map((lvl) => (
                      <button key={lvl || "none"} type="button" aria-pressed={(f.scopes[a.key] ?? "") === lvl} onClick={() => setLevel(a.key, lvl)}>
                        {t(lvl ? `apikeys.level_${lvl}` : "apikeys.level_none")}
                      </button>
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>

        <Field label={t("apikeys.allowed_ips")} hint={t("apikeys.allowed_ips_hint")} full>
          <textarea className="input mono" rows={3} value={f.ips} placeholder={"203.0.113.10\n2001:db8::/64"} onChange={(e) => setF({ ...f, ips: e.target.value })} />
        </Field>
        <Field label={t("apikeys.expires")} hint={t("apikeys.expires_hint")}>
          <input className="input" type="date" value={f.expires} min={new Date().toISOString().slice(0, 10)} onChange={(e) => setF({ ...f, expires: e.target.value })} />
        </Field>
      </form>
    </Drawer>
  );
}

// ---- documentation -----------------------------------------------------------------
type Schema = {
  $ref?: string; type?: string; format?: string; items?: Schema; anyOf?: Schema[]; enum?: unknown[]; const?: unknown;
  properties?: Record<string, Schema>; required?: string[]; default?: unknown; maxLength?: number; minimum?: number; maximum?: number;
  additionalProperties?: Schema | boolean; title?: string;
};
type Param = { name: string; in: string; required?: boolean; schema: Schema };
type Op = {
  summary?: string; operationId: string; parameters?: Param[]; tags?: string[]; "x-scope"?: string; "x-admin-only"?: boolean; "x-helper"?: boolean;
  requestBody?: { content: Record<string, { schema: Schema }> }; responses: Record<string, { description: string; content?: Record<string, { schema: Schema }> }>;
};
type Spec = { info: { version: string }; paths: Record<string, Record<string, Op>>; components: { schemas: Record<string, Schema> } };

const opId = (method: string, path: string) => `${method}${path.replace(/^\/api\/v1/, "").replace(/[{}]/g, "").replace(/[^a-z0-9]+/gi, "_")}`.toLowerCase();

function refName(s: Schema): string | null {
  return s.$ref ? s.$ref.split("/").pop() ?? null : null;
}

function typeOf(s: Schema | undefined, spec: Spec): string {
  if (!s) return "";
  const ref = refName(s);
  if (ref) return ref;
  if (s.anyOf) return s.anyOf.map((x) => typeOf(x, spec)).filter((x) => x !== "null").join(" | ") + (s.anyOf.some((x) => x.type === "null") ? " | null" : "");
  if (s.enum) return s.enum.map((v) => JSON.stringify(v)).join(" | ");
  if (s.const !== undefined) return JSON.stringify(s.const);
  if (s.type === "array") return `${typeOf(s.items, spec) || "any"}[]`;
  if (s.type === "object" && s.additionalProperties && typeof s.additionalProperties === "object") return `{ [key]: ${typeOf(s.additionalProperties, spec)} }`;
  return s.format ? `${s.type} (${s.format})` : s.type ?? "any";
}

function resolve(s: Schema | undefined, spec: Spec): Schema | undefined {
  const ref = s && refName(s);
  return ref ? spec.components.schemas[ref] : s;
}

function Fields({ schema, spec }: { schema: Schema | undefined; spec: Spec }) {
  const { t } = useTranslation();
  let s = resolve(schema, spec);
  const list = s?.type === "array";
  if (list) s = resolve(s!.items, spec);
  if (!s?.properties) return <div className="mono" style={{ fontSize: 12.5 }}>{typeOf(schema, spec)}</div>;
  return (
    <div className="tbl-wrap">
      {list && <div className="muted" style={{ fontSize: 12.5, marginBottom: 6 }}>{t("apikeys.doc_list_of")}</div>}
      <table className="tbl-mini" style={{ fontSize: 12.5 }}>
        <tbody>
          {Object.entries(s.properties).map(([name, p]) => (
            <tr key={name}>
              <td className="mono nowrap" style={{ fontWeight: 600 }}>{name}{s!.required?.includes(name) && <span style={{ color: "var(--bad)" }}> *</span>}</td>
              <td className="mono" style={{ wordBreak: "break-word" }}>{typeOf(p, spec)}</td>
              <td className="muted">{p.default !== undefined && p.default !== null && !(Array.isArray(p.default) && !p.default.length) ? `${t("apikeys.doc_default")}: ${JSON.stringify(p.default)}` : ""}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function curlFor(method: string, path: string, op: Op, spec: Spec): string {
  const url = `${window.location.origin}${path.replace(/\{[^}]+\}/g, (m) => `<${m.slice(1, -1)}>`)}`;
  const lines = [`curl${method === "get" ? "" : ` -X ${method.toUpperCase()}`} "${url}"`, `  -H "Authorization: Bearer $MAILFILTER_KEY"`];
  const body = resolve(op.requestBody?.content["application/json"]?.schema, spec);
  if (body?.properties) {
    const sample: Record<string, unknown> = {};
    for (const name of body.required ?? []) {
      const p = resolve(body.properties[name], spec);
      sample[name] = p?.type === "integer" || p?.type === "number" ? 1 : p?.type === "array" ? [] : p?.type === "boolean" ? true : p?.type === "object" ? {} : "…";
    }
    lines.push(`  -H "Content-Type: application/json"`, `  -d '${JSON.stringify(sample)}'`);
  }
  return lines.join(" \\\n");
}

function Operation({ method, path, op, spec }: { method: string; path: string; op: Op; spec: Spec }) {
  const { t } = useTranslation();
  const copy = useCopy();
  const [open, setOpen] = useState(false);
  const id = opId(method, path);
  const params = op.parameters ?? [];
  const body = op.requestBody?.content["application/json"]?.schema;
  const okCode = Object.keys(op.responses).find((c) => c.startsWith("2")) ?? "200";
  const ok = op.responses[okCode]?.content?.["application/json"]?.schema;
  const curl = curlFor(method, path, op, spec);
  const tone = method === "get" ? "s-info" : method === "delete" ? "s-bad" : "s-ok";
  return (
    <div style={{ borderTop: "1px solid var(--line)" }}>
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        style={{ all: "unset", boxSizing: "border-box", width: "100%", cursor: "pointer", display: "flex", gap: 10, alignItems: "center", padding: "11px 16px", flexWrap: "wrap" }}>
        <span className={`status ${tone}`} style={{ minWidth: 62, justifyContent: "center", fontFamily: "var(--mono)", fontSize: 11.5 }}>{method.toUpperCase()}</span>
        <span className="mono" style={{ fontWeight: 600, wordBreak: "break-all", flex: "1 1 260px" }}>{path.replace(/^\/api\/v1/, "")}</span>
        <span className="muted" style={{ flex: "2 1 240px", fontSize: 13 }}>{t(`apikeys.ops.${id}`, { defaultValue: op.summary ?? "" })}</span>
        {op["x-admin-only"] && <span className="tag">{t("apikeys.doc_admin_only")}</span>}
        <ChevronDown style={{ width: 16, height: 16, transform: open ? "rotate(180deg)" : undefined, transition: "transform .15s" }} aria-hidden />
      </button>
      {open && (
        <div style={{ padding: "0 16px 16px", display: "grid", gap: 12 }}>
          <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
            <span className="muted" style={{ fontSize: 12.5 }}>{t("apikeys.doc_needs")}</span>
            {op["x-scope"] ? <span className="tag lucht">{t(`apikeys.area.${op["x-scope"].split(":")[0]}`)} · {t(`apikeys.level_${op["x-scope"].split(":")[1]}`)}</span>
              : <span className="tag">{t("apikeys.doc_any_key")}</span>}
          </div>
          {params.length > 0 && (
            <div>
              <div className="sec-title">{t("apikeys.doc_params")}</div>
              <div className="tbl-wrap">
                <table className="tbl-mini" style={{ fontSize: 12.5 }}>
                  <tbody>
                    {params.map((p) => (
                      <tr key={p.in + p.name}>
                        <td className="mono nowrap" style={{ fontWeight: 600 }}>{p.name}{p.required && <span style={{ color: "var(--bad)" }}> *</span>}</td>
                        <td><span className="tag">{p.in}</span></td>
                        <td className="mono">{typeOf(p.schema, spec)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
          {body && (
            <div>
              <div className="sec-title">{t("apikeys.doc_body")}</div>
              <Fields schema={body} spec={spec} />
            </div>
          )}
          <div>
            <div className="sec-title">{t("apikeys.doc_response", { code: okCode })}</div>
            {ok ? <Fields schema={ok} spec={spec} /> : <span className="muted" style={{ fontSize: 12.5 }}>{t("apikeys.doc_no_body")}</span>}
          </div>
          <div>
            <div className="sec-title" style={{ display: "flex", alignItems: "center", gap: 8 }}>
              {t("apikeys.doc_example")}
              <button type="button" className="btn small ghost" onClick={() => copy(curl)}><Copy /> {t("common.copy")}</button>
            </div>
            <pre className="log" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{curl}</pre>
          </div>
        </div>
      )}
    </div>
  );
}

const ERRORS = ["api_key_required", "invalid_api_key", "api_key_expired", "ip_not_allowed", "scope_missing", "not_available_in_api", "admin_only", "api_customers_only", "rate_limited"];

function Docs() {
  const { t } = useTranslation();
  const copy = useCopy();
  const spec = useQuery({
    queryKey: ["api-v1-spec"],
    staleTime: Infinity,
    queryFn: async () => {
      const r = await fetch("/api/v1/openapi.json", { credentials: "omit" });
      if (!r.ok) throw new Error(String(r.status));
      return (await r.json()) as Spec;
    },
  });
  const groups = useMemo(() => {
    const out = new Map<string, { method: string; path: string; op: Op }[]>();
    for (const [path, ops] of Object.entries(spec.data?.paths ?? {}))
      for (const [method, op] of Object.entries(ops)) {
        if (op["x-helper"]) continue;
        const tag = op.tags?.[0] ?? "key";
        out.set(tag, [...(out.get(tag) ?? []), { method, path, op }]);
      }
    return out;
  }, [spec.data]);
  const areas = useQuery({ queryKey: ["api-key-areas"], queryFn: () => api.get<Areas>("/api-keys/areas") });

  if (spec.isLoading) return <Loading />;
  if (!spec.data) return <div style={{ padding: 16 }}><ErrorText error={spec.error} /></div>;
  const first = `curl ${BASE()}/gateway/relay-networks \\\n  -H "Authorization: Bearer $MAILFILTER_KEY" \\\n  -H "Content-Type: application/json" \\\n  -d '{"cidr": "203.0.113.10", "domain": "klant.nl", "description": "Webserver"}'`;
  return (
    <div>
      <div style={{ padding: 16, display: "grid", gap: 14 }}>
        <div className="grid two" style={{ gap: 14 }}>
          <div>
            <div className="sec-title">{t("apikeys.doc_base")}</div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <code className="mono" style={{ wordBreak: "break-all" }}>{BASE()}</code>
              <button type="button" className="icon-btn" aria-label={t("common.copy")} onClick={() => copy(BASE())}><Copy /></button>
            </div>
            <div className="sec-title" style={{ marginTop: 14 }}>{t("apikeys.doc_auth")}</div>
            <code className="mono">Authorization: Bearer mfk_…</code>
            <p className="muted" style={{ fontSize: 13, margin: "8px 0 0" }}>{t("apikeys.doc_auth_text", { limit: areas.data?.rate_limit ?? 600 })}</p>
            <div style={{ display: "flex", gap: 8, marginTop: 14, flexWrap: "wrap" }}>
              <a className="btn" href="/api/v1/openapi.json" download="mailfilter-openapi.json"><Download /> {t("apikeys.doc_download")}</a>
              <span className="tag" style={{ alignSelf: "center" }}>v{spec.data.info.version}</span>
            </div>
            <p className="muted" style={{ fontSize: 12.5, margin: "8px 0 0" }}>{t("apikeys.doc_download_hint")}</p>
          </div>
          <div>
            <div className="sec-title">{t("apikeys.doc_first")}</div>
            <pre className="log" style={{ whiteSpace: "pre-wrap", wordBreak: "break-all" }}>{first}</pre>
          </div>
        </div>
        <details>
          <summary style={{ cursor: "pointer", fontWeight: 650 }}>{t("apikeys.doc_errors")}</summary>
          <div className="tbl-wrap" style={{ marginTop: 8 }}>
            <table className="tbl-mini" style={{ fontSize: 12.5 }}>
              <tbody>
                {ERRORS.map((code) => (
                  <tr key={code}>
                    <td className="mono nowrap">{t(`apikeys.err_status.${code}`)}</td>
                    <td className="mono nowrap" style={{ fontWeight: 600 }}>{code}{code === "scope_missing" ? ":<area>:<level>" : ""}</td>
                    <td>{t(`apikeys.err.${code}`)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </details>
      </div>
      {[...groups.entries()].map(([tag, ops]) => (
        <section key={tag} style={{ borderTop: "1px solid var(--line)" }}>
          <div style={{ padding: "16px 16px 10px" }}>
            <h3 style={{ margin: 0, fontSize: 15 }}>{tag === "key" ? t("apikeys.doc_key_group") : t(`apikeys.area.${tag}`)}</h3>
            <div className="muted" style={{ fontSize: 13 }}>{tag === "key" ? t("apikeys.doc_key_group_hint") : t(`apikeys.area_hint.${tag}`)}</div>
          </div>
          {ops.map((o) => <Operation key={o.method + o.path} {...o} spec={spec.data!} />)}
        </section>
      ))}
    </div>
  );
}
