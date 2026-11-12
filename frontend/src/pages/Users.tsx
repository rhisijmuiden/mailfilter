import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, ShieldCheck, ShieldOff, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { DataTable, type Column } from "../components/DataTable";
import { useTranslation } from "react-i18next";
import { Card, Drawer, ErrorText, Field, PageHead, formatDate, useToast } from "../components/ui";
import { api } from "../lib/api";

type User = {
  id: number; email: string; name: string; role: "admin" | "customer" | "reseller"; language: "en" | "nl";
  is_active: boolean; domains: string[]; last_login_at: string | null; two_factor_enabled: boolean; reseller_id: number | null;
};
type Form = Omit<User, "id" | "last_login_at" | "two_factor_enabled"> & { password: string };

const EMPTY: Form = { email: "", name: "", role: "customer", language: "nl", is_active: true, domains: [], reseller_id: null, password: "" };

export default function Users() {
  const { t, i18n } = useTranslation();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
  const [editing, setEditing] = useState<User | "new" | null>(null);
  // Opened from the search box (Ctrl+K): /users?id=…
  const [params, setParams] = useSearchParams();
  useEffect(() => {
    const id = Number(params.get("id"));
    const u = id && users.data?.find((x) => x.id === id);
    if (u) {
      setEditing(u);
      setParams({}, { replace: true });
    }
  }, [params, users.data, setParams]);
  const columns: Column<User>[] = [
    { key: "user", label: t("users.email"), always: true, sort: (u) => (u.name || u.email).toLowerCase(), text: (u) => `${u.name} ${u.email}`, mobile: "title",
      render: (u) => (
        <>
          <div className="cell-main">{u.name || u.email}</div>
          <div className="cell-sub">{u.email}</div>
        </>
      ) },
    { key: "role", label: t("users.role"), sort: (u) => u.role, text: (u) => t(`users.${u.role}`), nowrap: true, mobile: "aside",
      render: (u) => (
        <>
          <span className={`status ${u.role === "admin" ? "s-info" : "s-mute"}`}>{t(`users.${u.role}`)}</span>
          {!u.is_active && <span className="status s-bad" style={{ marginLeft: 6 }}>{t("users.inactive")}</span>}
        </>
      ) },
    { key: "domains", label: t("users.domains"), sort: (u) => u.domains.join(","), text: (u) => u.domains.join(" "),
      render: (u) => (u.role === "admin" ? <span className="muted">{t("users.all_domains")}</span> : u.role === "reseller" ? <span className="muted">{t("users.reseller")}</span> : u.domains.map((d) => <span key={d} className="tag" style={{ marginRight: 4 }}>{d}</span>)) },
    { key: "twofa", label: t("users.twofa"), sort: (u) => (u.two_factor_enabled ? 1 : 0), nowrap: true,
      render: (u) => (u.two_factor_enabled ? <span className="status s-ok"><ShieldCheck /> {t("twofa.on")}</span> : <span className="status s-mute">{t("twofa.off")}</span>) },
    { key: "last", label: t("users.last_login"), sort: (u) => u.last_login_at ?? "", nowrap: true, mobileLabel: true,
      render: (u) => <span className="muted">{u.last_login_at ? formatDate(u.last_login_at, i18n.language) : t("common.never")}</span> },
    { key: "lang", label: t("common.language"), optional: true, sort: (u) => u.language, render: (u) => u.language.toUpperCase() },
  ];
  return (
    <>
      <PageHead
        title={t("users.title")}
        sub={t("users.sub")}
        actions={
          <button className="btn primary" onClick={() => setEditing("new")}>
            <Plus /> {t("users.add")}
          </button>
        }
      />
      <Card flush>
        <DataTable
          id="users"
          rows={users.data}
          rowKey={(u) => u.id}
          loading={users.isLoading}
          columns={columns}
          defaultSort={{ key: "user", dir: 1 }}
          empty={t("users.empty")}
          onRowClick={(u) => setEditing(u)}
        />
      </Card>
      {editing && <UserDrawer user={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function UserDrawer({ user, onClose }: { user: User | null; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState<Form>(() => {
    if (!user) return EMPTY;
    const { id: _id, last_login_at: _l, two_factor_enabled: _t, ...rest } = user;
    return { ...rest, password: "" };
  });
  const [twofa, setTwofa] = useState(user?.two_factor_enabled ?? false);
  const reset2fa = useMutation({
    mutationFn: () => api.post<User>(`/users/${user!.id}/2fa/reset`),
    onSuccess: () => {
      setTwofa(false);
      qc.invalidateQueries({ queryKey: ["users"] });
      toast(t("users.twofa_reset_done"));
    },
  });
  const [created, setCreated] = useState<string | null>(null);
  const options = useQuery({ queryKey: ["domain-options"], queryFn: () => api.get<string[]>("/users/domain-options") });
  const resellers = useQuery({ queryKey: ["billing"], queryFn: () => api.get<{ resellers: { id: number; name: string }[] }>("/billing"),
    enabled: f.role === "reseller" });
  const body = { ...f, password: f.password || null };
  const save = useMutation({
    mutationFn: () => (user ? api.put<User & { password?: string | null }>(`/users/${user.id}`, body) : api.post<User & { password?: string | null }>("/users", body)),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["users"] });
      if (!user && res.password) setCreated(res.password);
      else {
        toast(t("common.saved"));
        onClose();
      }
    },
  });
  const del = useMutation({
    mutationFn: () => api.del(`/users/${user!.id}`),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["users"] });
      onClose();
    },
  });
  const toggleDomain = (d: string) => setF({ ...f, domains: f.domains.includes(d) ? f.domains.filter((x) => x !== d) : [...f.domains, d] });

  if (created)
    return (
      <Drawer title={t("users.add")} onClose={onClose}>
        <div className="callout">
          <span>{t("users.generated")}</span>
        </div>
        <pre className="log" style={{ fontSize: 16 }}>{created}</pre>
        <div>
          <button className="btn primary" onClick={onClose}>{t("common.close")}</button>
        </div>
      </Drawer>
    );

  return (
    <Drawer title={user ? t("users.edit") : t("users.add")} sub={user?.email} onClose={onClose}>
      <form
        className="form-grid"
        onSubmit={(e) => {
          e.preventDefault();
          save.mutate();
        }}
      >
        <div className="full"><ErrorText error={save.error || del.error || reset2fa.error} /></div>
        <Field label={t("users.email")}>
          <input className="input" type="email" required value={f.email} onChange={(e) => setF({ ...f, email: e.target.value })} />
        </Field>
        <Field label={t("users.name")}>
          <input className="input" value={f.name} onChange={(e) => setF({ ...f, name: e.target.value })} />
        </Field>
        <Field label={t("users.role")}>
          <select className="select" value={f.role} onChange={(e) => setF({ ...f, role: e.target.value as User["role"] })}>
            <option value="customer">{t("users.customer")}</option>
            <option value="admin">{t("users.admin")}</option>
            <option value="reseller">{t("users.reseller")}</option>
          </select>
        </Field>
        <Field label={t("users.language")}>
          <select className="select" value={f.language} onChange={(e) => setF({ ...f, language: e.target.value as User["language"] })}>
            <option value="nl">{t("lang.nl")}</option>
            <option value="en">{t("lang.en")}</option>
          </select>
        </Field>
        {f.role === "reseller" && (
          <Field label={t("users.reseller")} hint={t("users.reseller_hint")} full>
            {resellers.data && !resellers.data.resellers.length ? <span className="muted">{t("users.no_resellers")}</span> : (
              <select className="select" required value={f.reseller_id ?? ""} onChange={(e) => setF({ ...f, reseller_id: e.target.value ? +e.target.value : null })}>
                <option value="" />
                {resellers.data?.resellers.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
              </select>
            )}
          </Field>
        )}
        {f.role === "customer" && (
          <div className="field full">
            <span style={{ fontSize: 12.5, fontWeight: 650 }}>{t("users.domains")}</span>
            <span className="hint">{t("users.domains_hint")}</span>
            {options.data?.length ? (
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 4 }}>
                {options.data.map((d) => (
                  <label key={d} className="check tag" style={{ cursor: "pointer", padding: "5px 10px" }}>
                    <input type="checkbox" checked={f.domains.includes(d)} onChange={() => toggleDomain(d)} style={{ margin: 0 }} />
                    {d}
                  </label>
                ))}
              </div>
            ) : (
              <span className="muted">{t("users.no_domains")}</span>
            )}
          </div>
        )}
        <Field label={t("users.password")} hint={user ? t("users.password_keep") : t("users.password_hint")} full>
          <input className="input" type="password" autoComplete="new-password" minLength={10} value={f.password} onChange={(e) => setF({ ...f, password: e.target.value })} />
        </Field>
        {user && (
          <div className="field full">
            <span style={{ fontSize: 12.5, fontWeight: 650 }}>{t("users.twofa")}</span>
            <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
              {twofa ? <span className="status s-ok"><ShieldCheck /> {t("twofa.on")}</span> : <span className="status s-mute">{t("twofa.off")}</span>}
              {twofa && (
                <button
                  type="button"
                  className="btn small danger"
                  disabled={reset2fa.isPending}
                  onClick={() => window.confirm(t("users.twofa_reset_confirm", { email: user.email })) && reset2fa.mutate()}
                >
                  <ShieldOff /> {t("users.twofa_reset")}
                </button>
              )}
            </div>
            <span className="hint">{t("users.twofa_hint")}</span>
          </div>
        )}
        <label className="check full">
          <input type="checkbox" checked={f.is_active} onChange={(e) => setF({ ...f, is_active: e.target.checked })} />
          <span>{t("users.active")}</span>
        </label>
        <div className="full" style={{ display: "flex", gap: 8 }}>
          <button className="btn primary" disabled={save.isPending}>
            <UserRound /> {t("common.save")}
          </button>
          <button type="button" className="btn ghost" onClick={onClose}>{t("common.cancel")}</button>
          {user && (
            <button
              type="button"
              className="btn danger"
              style={{ marginLeft: "auto" }}
              onClick={() => window.confirm(t("users.delete_confirm", { email: user.email })) && del.mutate()}
            >
              {t("common.delete")}
            </button>
          )}
        </div>
      </form>
    </Drawer>
  );
}
