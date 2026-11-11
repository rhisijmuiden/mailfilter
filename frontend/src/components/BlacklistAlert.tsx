// Big red banner on the dashboard as soon as one of Mailfilter's own
// addresses is on a DNS blocklist (checked every 15 minutes by a timer).
import { useQuery } from "@tanstack/react-query";
import { Ban } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api } from "../lib/api";
import { formatDate } from "./ui";

type Status = { checked_at: string | null; listed: boolean | { ip: string; name: string; zone: string; since: string }[] };

export default function BlacklistAlert({ admin }: { admin: boolean }) {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: ["dns-blacklist-alert"], queryFn: () => api.get<Status>("/dns/blacklist"), refetchInterval: 60_000 });
  const listed = q.data?.listed;
  if (!listed || (Array.isArray(listed) && listed.length === 0)) return null;
  const items = Array.isArray(listed) ? listed : [];
  const first = items[0];
  return (
    <div className="alert-bl" role="alert" style={{ marginBottom: 20 }}>
      <Ban />
      <div className="grow">
        <b>{t("dns.alert.title")}</b>
        <span>
          {first
            ? t("dns.alert.detail", { ip: first.ip, name: first.name, zone: first.zone, time: formatDate(first.since, i18n.language), count: items.length })
            : t("dns.alert.customer")}
        </span>
      </div>
      {admin && <Link className="btn" to="/dns?tab=blacklist">{t("dns.alert.open")}</Link>}
    </div>
  );
}
