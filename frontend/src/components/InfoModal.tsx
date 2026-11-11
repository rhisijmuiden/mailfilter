// The Info button: current version and channel. The updates feature extends
// this with "check for updates", "update now" and the channel switch.
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { api } from "../lib/api";
import { Modal } from "./ui";

export type SystemInfo = { version: string; app_version: string; branch: string; hostname: string };

export function useSystemInfo() {
  return useQuery({ queryKey: ["system-info"], queryFn: () => api.get<SystemInfo>("/system/info"), staleTime: 5 * 60_000 });
}

export function InfoModal({ onClose }: { onClose: () => void }) {
  const { t } = useTranslation();
  const { data } = useSystemInfo();
  const channel = data?.branch === "main" ? t("info.production") : data?.branch === "test" ? t("info.test") : data?.branch;
  return (
    <Modal title={t("info.title")} onClose={onClose}>
      <div className="version-hero">
        <img className="ic" src="/favicon.svg" alt="" />
        <div>
          <div className="muted">{t("app.product")}</div>
          <div className="vn">{data?.version ?? "…"}</div>
        </div>
      </div>
      <dl className="kv">
        <dt>{t("info.branch")}</dt>
        <dd>
          <span className={`status ${data?.branch === "main" ? "s-ok" : "s-info"}`}>{channel}</span>
        </dd>
        <dt>{t("info.app")}</dt>
        <dd className="num">{data?.app_version}</dd>
        <dt>{t("info.host")}</dt>
        <dd className="mono">{data?.hostname}</dd>
      </dl>
      <div className="callout">
        <span>{t("info.updates_soon")}</span>
      </div>
    </Modal>
  );
}
