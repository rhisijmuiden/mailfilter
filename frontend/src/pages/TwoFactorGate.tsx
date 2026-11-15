// Shown instead of the app when the admin requires two-factor login and this
// user has not set it up yet. The backend refuses everything else meanwhile.
import { useQueryClient } from "@tanstack/react-query";
import { LogOut } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Logo, ProductName } from "../components/Logo";
import { TwoFactorCard } from "../components/TwoFactor";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";

export default function TwoFactorGate({ me }: { me: Me }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  return (
    <div style={{ minHeight: "100%", display: "grid", placeItems: "center", padding: 16 }}>
      <div style={{ width: "min(560px,100%)", display: "flex", flexDirection: "column", gap: 20 }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div className="brand" style={{ padding: 0 }}>
            <Logo />
            <span className="product"><ProductName /></span>
          </div>
          <button className="btn ghost small" onClick={() => api.post("/auth/logout").finally(() => qc.setQueryData(["me"], null))}>
            <LogOut /> {t("common.logout")}
          </button>
        </div>
        <div>
          <h1 className="display" style={{ margin: 0, fontSize: 26 }}>{t("twofa.gate_title")}</h1>
          <p className="muted" style={{ margin: "4px 0 0" }}>{t("twofa.gate_sub", { email: me.email })}</p>
        </div>
        <TwoFactorCard me={me} />
      </div>
    </div>
  );
}
