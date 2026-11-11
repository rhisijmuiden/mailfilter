import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, UserRound } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, Drawer, ErrorText, Field, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { api } from "../lib/api";

type User = {
  id: number; email: string; name: string; role: "admin" | "customer"; language: "en" | "nl";
  is_active: boolean; domains: string[]; last_login_at: string | null;
};
type Form = Omit<User, "id" | "last_login_at"> & { password: string };

const EMPTY: Form = { email: "", name: "", role: "customer", language: "nl", is_active: true, domains: [], password: "" };

export default function Users() {
  const { t, i18n } = useTranslation();
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<User[]>("/users") });
  const [editing, setEditing] = useState<User | "new" | null>(null);
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
        {users.isLoading ? (
          <Loading />
        ) : !users.data?.length ? (
          <div className="empty">{t("users.empty")}</div>
        ) : (
          <div className="tbl-wrap">
            <table>
              <thead>
                <tr>
                  <th>{t("users.email")}</th>
                  <th>{t("users.role")}</th>
                  <th>{t("users.domains")}</th>
                  <th>{t("users.last_login")}</th>
                </tr>
              </thead>
              <tbody>
                {users.data.map((u) => (
                  <tr key={u.id} className="click" onClick={() => setEditing(u)} tabIndex={0} onKeyDown={(e) => e.key === "Enter" && setEditing(u)}>
                    <td>
                      <div className="cell-main">{u.name || u.email}</div>
                      <div className="cell-sub">{u.email}</div>
                    </td>
                    <td className="nowrap">
                      <span className={`status ${u.role === "admin" ? "s-info" : "s-mute"}`}>{t(`users.${u.role}`)}</span>
                      {!u.is_active && <span className="status s-bad" style={{ marginLeft: 6 }}>{t("users.inactive")}</span>}
                    </td>
                    <td>{u.role === "admin" ? <span className="muted">{t("users.all_domains")}</span> : u.domains.map((d) => <span key={d} className="tag" style={{ marginRight: 4 }}>{d}</span>)}</td>
                    <td className="nowrap muted">{u.last_login_at ? formatDate(u.last_login_at, i18n.language) : t("common.never")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Card>
      {editing && <UserDrawer user={editing === "new" ? null : editing} onClose={() => setEditing(null)} />}
    </>
  );
}

function UserDrawer({ user, onClose }: { user: User | null; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState<Form>(user ? { ...user, password: "" } : EMPTY);
  const [created, setCreated] = useState<string | null>(null);
  const options = useQuery({ queryKey: ["domain-options"], queryFn: () => api.get<string[]>("/users/domain-options") });
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
        <div className="full"><ErrorText error={save.error || del.error} /></div>
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
          </select>
        </Field>
        <Field label={t("users.language")}>
          <select className="select" value={f.language} onChange={(e) => setF({ ...f, language: e.target.value as User["language"] })}>
            <option value="nl">{t("lang.nl")}</option>
            <option value="en">{t("lang.en")}</option>
          </select>
        </Field>
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
