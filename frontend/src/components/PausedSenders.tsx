import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CirclePause, Play, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { api } from "../lib/api";
import type { Hold } from "../lib/gateway";
import GatewayError from "./GatewayError";
import { Card, formatDate, Modal, useToast } from "./ui";

/** Why a sender was paused, in the user's language. */
export function holdReason(h: Hold, t: TFunction): string {
  if (h.reason === "spam") return t("holds.why_spam", h.detail);
  if (h.reason === "volume") return t("holds.why_volume", h.detail);
  return t("holds.why_manual");
}

/** Red banner plus list on the Relay access page: the one-click release lives here. */
export default function PausedSenders({ admin }: { admin: boolean }) {
  const { t, i18n } = useTranslation();
  const holds = useQuery({ queryKey: ["gw-holds"], queryFn: () => api.get<Hold[]>("/gateway/holds"), refetchInterval: 30_000 });
  const [releasing, setReleasing] = useState<Hold | null>(null);
  const active = holds.data?.filter((h) => !h.released_at) ?? [];
  const recent = holds.data?.filter((h) => h.released_at) ?? [];
  if (!holds.data?.length) return null;

  return (
    <>
      {active.map((h) => (
        <div className="alert-bl" key={h.id} style={{ marginBottom: 14 }}>
          <CirclePause aria-hidden />
          <div className="grow">
            <b>{t("holds.paused_title", { value: h.value })}</b>
            <span>
              {holdReason(h, t)}. {t("holds.held_count", { count: h.held_messages })}{" "}
              {t("holds.since", { when: formatDate(h.created_at, i18n.language) })}
            </span>
          </div>
          {admin ? (
            <button className="btn" onClick={() => setReleasing(h)}><Play /> {t("holds.release")}</button>
          ) : (
            <span className="tag">{t("holds.ask_admin")}</span>
          )}
        </div>
      ))}
      {recent.length > 0 && (
        <Card title={t("holds.recent")} flush>
          <div className="tbl-wrap">
            <table>
              <tbody>
                {recent.map((h) => (
                  <tr key={h.id}>
                    <td className="mono nowrap">{h.value}</td>
                    <td>{holdReason(h, t)}</td>
                    <td className="muted nowrap">
                      {t("holds.released_by", { who: h.released_by ?? "?", when: formatDate(h.released_at, i18n.language) })}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Card>
      )}
      {releasing && <ReleaseDialog h={releasing} onClose={() => setReleasing(null)} />}
    </>
  );
}

function ReleaseDialog({ h, onClose }: { h: Hold; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const release = useMutation({
    mutationFn: (held_mail: "deliver" | "delete") =>
      api.post<{ done: number; failed: number }>(`/gateway/holds/${h.id}/release`, { held_mail }),
    onSuccess: (res, held_mail) => {
      qc.invalidateQueries({ queryKey: ["gw-holds"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      toast(t(held_mail === "deliver" ? "holds.released_delivered" : "holds.released_deleted", { value: h.value, count: res.done }));
      onClose();
    },
  });
  return (
    <Modal title={t("holds.release_title", { value: h.value })} onClose={onClose} icon={<Play />}>
      <p>{t("holds.release_text")}</p>
      <p className="muted">{t("holds.held_count", { count: h.held_messages })}</p>
      <GatewayError error={release.error} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", justifyContent: "flex-end", marginTop: 16 }}>
        <button className="btn ghost" onClick={onClose}>{t("common.cancel")}</button>
        <button className="btn danger" disabled={release.isPending} onClick={() => release.mutate("delete")}>
          <Trash2 /> {t("holds.release_delete")}
        </button>
        <button className="btn primary" disabled={release.isPending} onClick={() => release.mutate("deliver")}>
          <Play /> {t("holds.release_deliver")}
        </button>
      </div>
    </Modal>
  );
}

/** Small pause button for a relay network row (single IP addresses only). */
export function PauseButton({ ip }: { ip: string }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const pause = useMutation({
    mutationFn: () => api.post("/gateway/holds", { kind: "ip", value: ip }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["gw-holds"] }); toast(t("holds.paused_toast", { value: ip })); },
    onError: (e: any) => toast(e?.message ?? String(e)),
  });
  return (
    <button className="icon-btn" title={t("holds.pause")} aria-label={t("holds.pause")} disabled={pause.isPending}
      onClick={() => window.confirm(t("holds.pause_confirm", { value: ip })) && pause.mutate()}>
      <CirclePause />
    </button>
  );
}
