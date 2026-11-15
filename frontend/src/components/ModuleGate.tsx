// Shows a page only while its module is on (System > Modules).
import { PowerOff } from "lucide-react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import type { Me } from "../lib/auth";
import { moduleOn, useModules, type ModuleKey } from "../lib/modules";
import { Loading, PageHead } from "./ui";

export function ModuleGate({ module, me, children }: { module: ModuleKey; me: Me; children: ReactNode }) {
  const { t } = useTranslation();
  const q = useModules();
  if (q.isLoading) return <Loading />;
  if (moduleOn(q.data, module)) return <>{children}</>;
  return (
    <>
      <PageHead title={t(`modules.m.${module}.title`)} />
      <section className="card">
        <div className="empty">
          <PowerOff aria-hidden style={{ width: 32, height: 32, color: "var(--ink-muted)", display: "block", margin: "0 auto" }} />
          <p style={{ maxWidth: "56ch", margin: "8px auto" }}>{t(me.role === "admin" ? "modules.off_admin" : "modules.off")}</p>
          {me.role === "admin" && <Link className="btn primary" to="/system/modules">{t("modules.open")}</Link>}
        </div>
      </section>
    </>
  );
}
