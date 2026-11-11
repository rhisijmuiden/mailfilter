import { ArrowRight } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Card, PageHead } from "../components/ui";
import type { Me } from "../lib/auth";

export default function Dashboard({ me }: { me: Me }) {
  const { t } = useTranslation();
  const admin = me.role === "admin";
  const steps = [
    ...(admin ? [{ to: "/users", label: t("dashboard.next_users") }] : []),
    { to: "/trace", label: t("dashboard.next_trace") },
    { to: "/quarantine", label: t("dashboard.next_quarantine") },
  ];
  return (
    <>
      <PageHead
        title={t("dashboard.welcome", { name: me.name || me.email })}
        sub={admin ? t("dashboard.sub_admin") : t("dashboard.sub_customer", { domains: me.domains.join(", ") || "–" })}
      />
      <Card title={t("dashboard.next")}>
        {steps.map((s) => (
          <Link key={s.to} to={s.to} className="list-row" style={{ color: "inherit", textDecoration: "none" }}>
            <span className="grow" style={{ fontWeight: 600 }}>{s.label}</span>
            <ArrowRight size={16} aria-hidden />
          </Link>
        ))}
      </Card>
    </>
  );
}
