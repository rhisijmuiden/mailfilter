// Admin: whether two-factor login is required, for nobody, admins or everyone.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Card, ErrorText, Loading, PageHead, useToast } from "../components/ui";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";

type Policy = "off" | "admins" | "all";
type UserRow = { id: number; email: string; name: string; role: "admin" | "customer"; is_active: boolean; two_factor_enabled: boolean };

export default function Security({ me }: { me: Me }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const policy = useQuery({ queryKey: ["security"], queryFn: () => api.get<{ require_2fa: Policy }>("/system/security") });
  const users = useQuery({ queryKey: ["users"], queryFn: () => api.get<UserRow[]>("/users") });
  const [value, setValue] = useState<Policy>("off");
  useEffect(() => {
    if (policy.data) setValue(policy.data.require_2fa);
  }, [policy.data]);
  const save = useMutation({
    mutationFn: () => api.put<{ require_2fa: Policy }>("/system/security", { require_2fa: value }),
    onSuccess: (r) => {
      qc.setQueryData(["security"], r);
      toast(t("common.saved"));
    },
  });

  const active = (users.data ?? []).filter((u) => u.is_active);
  const affected = active.filter((u) => value === "all" || (value === "admins" && u.role === "admin"));
  const missing = affected.filter((u) => !u.two_factor_enabled);

  return (
    <>
      <PageHead title={t("security.title")} sub={t("security.sub")} />
      <div className="grid halves">
        <Card title={t("security.require")}>
          {policy.isLoading ? (
            <Loading />
          ) : (
            <form
              style={{ display: "flex", flexDirection: "column", gap: 14 }}
              onSubmit={(e) => {
                e.preventDefault();
                save.mutate();
              }}
            >
              <ErrorText error={save.error} />
              {!me.two_factor_enabled && (
                <div className="callout warn">
                  <span>
                    {t("security.self_first")} <Link to="/account">{t("nav.account")}</Link>
                  </span>
                </div>
              )}
              {(["off", "admins", "all"] as Policy[]).map((p) => (
                <label key={p} className="check" style={{ alignItems: "flex-start", gap: 10 }}>
                  <input type="radio" name="policy" checked={value === p} onChange={() => setValue(p)} style={{ marginTop: 3 }} />
                  <span>
                    <strong>{t(`security.policy_${p}`)}</strong>
                    <br />
                    <span className="muted" style={{ fontSize: 12.5 }}>{t(`security.policy_${p}_hint`)}</span>
                  </span>
                </label>
              ))}
              <div>
                <button className="btn primary" disabled={save.isPending || value === policy.data?.require_2fa}>
                  <ShieldCheck /> {t("common.save")}
                </button>
              </div>
            </form>
          )}
        </Card>
        <Card title={t("security.status")}>
          {users.isLoading ? (
            <Loading />
          ) : (
            <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
              <dl className="kv">
                <dt>{t("security.with_2fa")}</dt>
                <dd>{t("security.of", { n: active.filter((u) => u.two_factor_enabled).length, total: active.length })}</dd>
              </dl>
              {value !== "off" && missing.length > 0 && (
                <>
                  <p className="muted" style={{ margin: 0 }}>{t("security.missing", { count: missing.length })}</p>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                    {missing.map((u) => (
                      <span key={u.id} className="tag">{u.email}</span>
                    ))}
                  </div>
                </>
              )}
              <p className="muted" style={{ margin: 0, fontSize: 12.5 }}>{t("security.lost_phone")}</p>
            </div>
          )}
        </Card>
      </div>
    </>
  );
}
