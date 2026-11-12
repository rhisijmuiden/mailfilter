import { ArrowRight, Check, Clock, Globe, Server, ShieldAlert, ShieldCheck, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type { Explanation, Message, RouteNode } from "../lib/trace";
import { statusMeta } from "../lib/trace";

export function StatusPill({ status, recipient }: { status: string; recipient?: boolean }) {
  const { t } = useTranslation();
  const { tone, Icon } = statusMeta(status);
  return (
    <span className={`status s-${tone}`}>
      <Icon aria-hidden />
      {t(`${recipient ? "rstatus" : "status"}.${status}`, { defaultValue: status })}
    </span>
  );
}

export function Direction({ direction }: { direction: Message["direction"] }) {
  const { t } = useTranslation();
  if (!direction) return null;
  return (
    <span className="dir">
      {direction === "inbound" ? <ArrowRight aria-hidden style={{ transform: "rotate(90deg)" }} /> : <ArrowRight aria-hidden style={{ transform: "rotate(-90deg)" }} />}
      {t(`trace.${direction}`)}
    </span>
  );
}

export function Reason({ explanation }: { explanation: Explanation | null }) {
  if (!explanation) return null;
  return (
    <span className="reason">
      <b>{explanation.title}</b> {explanation.action}
    </span>
  );
}

type Leg = "ok" | "warn" | "bad" | "stop" | "none";
const LEG_COLOR: Record<Exclude<Leg, "none">, [string, string]> = {
  ok: ["var(--ok)", "var(--ok-soft)"],
  warn: ["var(--warn)", "var(--warn-soft)"],
  bad: ["var(--bad)", "var(--bad-soft)"],
  stop: ["var(--info)", "var(--info-soft)"],
};

function Link({ leg }: { leg: Leg }) {
  if (leg === "none") return <div className="link" aria-hidden />;
  const [c, bg] = LEG_COLOR[leg];
  const Icon = leg === "ok" ? Check : leg === "warn" ? Clock : leg === "stop" ? ShieldAlert : X;
  return (
    <div className="link" aria-hidden>
      <svg viewBox="0 0 44 16">
        <path d="M2 8h34" stroke={c} strokeWidth="2" strokeDasharray={leg === "ok" ? undefined : "4 3"} fill="none" />
        <path d="M34 3l7 5-7 5" fill="none" stroke={c} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
      <span className="st" style={{ background: bg, color: c }}>
        <Icon />
      </span>
    </div>
  );
}

function legFor(status: string | null): Leg {
  switch (status) {
    case "sent": return "ok";
    case "deferred": case "queued": return "warn";
    case "quarantined": case "held": case "trapped": return "stop";
    case "rejected": return "none";
    default: return "bad";
  }
}

/** Source server > Mailfilter > destination(s), with the state of each hop. */
export function RoutePicture({ route, message }: { route: RouteNode[]; message: Message }) {
  const { t } = useTranslation();
  const [source, filter, ...dests] = route;
  const rejected = message.status === "rejected";
  const trapped = message.status === "trapped";
  const quarantined = message.status === "quarantined" || message.status === "held" || trapped;
  const firstDest = dests[0];
  const leg2: Leg = rejected ? "none" : quarantined ? "stop" : legFor(firstDest?.status ?? "queued");
  const spam = filter.detail ? t("trace.route_score", { score: filter.detail }) : t("trace.route_checked");
  return (
    <>
      <div className="route" role="img" aria-label={t("trace.route")}>
        <div className="node">
          <span className="ico">{message.direction === "outbound" ? <Server /> : <Globe />}</span>
          <span className="k">{t("trace.route_source")}</span>
          <b>{source.name || t("trace.unknown_server")}</b>
          <span className="cell-sub">{source.detail || (message.direction === "outbound" ? t("trace.relay") : t("trace.internet"))}</span>
          {source.ip && <span className="ip">{source.ip}</span>}
        </div>
        <Link leg={rejected ? "bad" : "ok"} />
        <div className="node mf">
          <span className="ico"><ShieldCheck /></span>
          <span className="k">Mailfilter</span>
          <b>{filter.name}</b>
          <span className="cell-sub">{spam}</span>
          {filter.ip && <span className="ip">{filter.ip}</span>}
        </div>
        <Link leg={leg2} />
        {rejected || quarantined || !firstDest ? (
          <div className="node dim">
            <span className="ico"><Server /></span>
            <span className="k">{trapped ? t("trace.in_mailtrap") : t("trace.route_destination")}</span>
            <b>{trapped ? t("trace.not_sent_test") : quarantined ? t("trace.not_forwarded") : rejected ? t("trace.refused") : t("trace.not_yet")}</b>
            <span className="cell-sub">{message.recipients[0]?.address}</span>
          </div>
        ) : (
          <div className="node">
            <span className="ico"><Server /></span>
            <span className="k">{t("trace.route_destination")}</span>
            <b>{firstDest.name || t("trace.not_yet")}</b>
            {dests.length > 1 && <span className="cell-sub">{t("trace.more_servers", { count: dests.length - 1 })}</span>}
            {firstDest.ip && <span className="ip">{firstDest.ip}</span>}
          </div>
        )}
      </div>
    </>
  );
}
