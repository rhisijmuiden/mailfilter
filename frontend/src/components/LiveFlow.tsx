// Dashboard "live" block: messages per minute over the last hour and a feed of
// what is happening now. Polls every few seconds while the page is visible.
import { useQuery } from "@tanstack/react-query";
import { Pause, Play } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { api, qs } from "../lib/api";
import { Legend, StackedColumns, type Series } from "./charts";
import { Card, Skeleton } from "./ui";

type Minute = { t: string; delivered: number; spam: number; refused: number; delayed: number; failed: number };
type Event = { queue_id: string; at: string; direction: string | null; sender: string | null; recipients: string[]; status: string; category: string; reason: string | null };
type Live = { now: string; minutes: Minute[]; events: Event[] };

const POLL_MS = 5000;

export function LiveFlow({ direction, series }: { direction: string; series: Series[] }) {
  const { t, i18n } = useTranslation();
  const [paused, setPaused] = useState(false);
  const live = useQuery({
    queryKey: ["dashboard-live", direction],
    queryFn: () => api.get<Live>(`/dashboard/live${qs({ direction })}`),
    refetchInterval: paused ? false : POLL_MS,
    refetchIntervalInBackground: false,
  });
  // Rows that were not there on the previous poll slide in.
  const seen = useRef<Set<string> | null>(null);
  const [fresh, setFresh] = useState<Set<string>>(new Set());
  useEffect(() => {
    if (!live.data) return;
    const keys = live.data.events.map((e) => `${e.queue_id}@${e.at}`);
    if (seen.current) setFresh(new Set(keys.filter((k) => !seen.current!.has(k))));
    seen.current = new Set(keys);
  }, [live.data]);
  useEffect(() => {
    seen.current = null;
  }, [direction]);

  const locale = i18n.language === "nl" ? "nl-NL" : "en-GB";
  const hm = (iso: string) => new Date(iso).toLocaleTimeString(locale, { hour: "2-digit", minute: "2-digit" });
  const color = Object.fromEntries(series.map((s) => [s.key, s.color]));
  const pill = (
    <span className={`live-pill${paused ? " paused" : ""}`}>
      <i aria-hidden />
      {paused ? t("live.paused") : t("live.live")}
    </span>
  );
  const pause = (
    <button type="button" className="btn small" onClick={() => setPaused(!paused)} aria-pressed={paused}>
      {paused ? <Play aria-hidden /> : <Pause aria-hidden />} {paused ? t("live.resume") : t("live.pause")}
    </button>
  );
  const d = live.data;

  return (
    <div className="grid two">
      <Card title={<span style={{ display: "inline-flex", gap: 10, alignItems: "center" }}>{t("live.chart_title")} {pill}</span>} right={pause}>
        {!d ? (
          <Skeleton rows={5} cols={1} />
        ) : (
          <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            <Legend series={series} />
            <StackedColumns
              height={240}
              data={d.minutes}
              series={series}
              label={t("live.chart_title")}
              xLabel={(b) => hm(b.t)}
              xTick={(b, i) => (i % 10 === 0 || i === d.minutes.length - 1 ? hm(b.t) : null)}
            />
            <p className="muted" style={{ margin: 0, fontSize: 12 }}>{t("live.chart_note", { seconds: POLL_MS / 1000 })}</p>
          </div>
        )}
      </Card>
      <Card title={<span style={{ display: "inline-flex", gap: 10, alignItems: "center" }}>{t("live.feed_title")} {pill}</span>}
        right={<Link className="btn small" to="/trace?hours=1">{t("dashboard.all")}</Link>} flush>
        {!d ? (
          <Skeleton rows={6} cols={2} />
        ) : d.events.length === 0 ? (
          <div className="empty">{t("live.empty")}</div>
        ) : (
          <ul className="feed" aria-live="off">
            {d.events.map((e) => {
              const key = `${e.queue_id}@${e.at}`;
              const to = e.status === "quarantined" ? `/quarantine?id=${encodeURIComponent(e.queue_id)}` : `/trace?id=${encodeURIComponent(e.queue_id)}`;
              return (
                <li key={key} className={fresh.has(key) ? "new" : undefined}>
                  <Link to={to}>
                    <span className="t">{hm(e.at)}</span>
                    <i className="d" style={{ background: color[e.category] ?? "var(--ink-muted)" }} aria-hidden />
                    <span style={{ minWidth: 0 }}>
                      <b>{e.sender || t("trace.empty_sender")} → {e.recipients.join(", ") || "–"}</b>
                      <span className="s">
                        {t(`status.${e.status}`, { defaultValue: e.status })}
                        {e.reason ? ` · ${e.reason}` : ""}
                      </span>
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </Card>
    </div>
  );
}
