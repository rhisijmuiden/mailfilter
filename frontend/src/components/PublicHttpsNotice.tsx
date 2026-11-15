// Shown on pages whose feature needs this server reachable over HTTPS from the internet
// (MTA-STS, BIMI, link protection, links in report mails) while System > Web access says it is not.
import { Globe } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { useMe } from "../lib/auth";
import { useSystemInfo } from "./InfoModal";

export function PublicHttpsNotice({ feature }: { feature: "dns" | "links" | "report" }) {
  const { t } = useTranslation();
  const info = useSystemInfo();
  const me = useMe();
  const mode = info.data?.web_mode;
  if (mode !== "internal" && mode !== "http") return null;
  return (
    <div className="callout warn" style={{ marginBottom: 16 }}>
      <Globe aria-hidden />
      <span>
        {t(`web.notice.${feature}`, { mode: t(`web.mode.${mode}.title`) })}{" "}
        {me.data?.role === "admin" && <Link to="/system/web">{t("nav.web_access")}</Link>}
      </span>
    </div>
  );
}
