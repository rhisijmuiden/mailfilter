import { Check, ChevronDown, Minus, ShieldAlert, ShieldCheck, ShieldQuestion, TriangleAlert, X } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Detail, ReportGroup } from "../lib/trace";

const STORE_KEY = "mf.spamreport.open";
const BLOCKING = new Set(["quarantine", "reject", "soft_reject"]);
const FLAGGING = new Set(["add_header", "rewrite_subject", "greylist"]);

function readOpen(): boolean {
  try {
    return localStorage.getItem(STORE_KEY) !== "0";
  } catch {
    return true;
  }
}

function useNum() {
  const { i18n } = useTranslation();
  const f = new Intl.NumberFormat(i18n.language, { minimumFractionDigits: 1, maximumFractionDigits: 1, signDisplay: "exceptZero" });
  const plain = new Intl.NumberFormat(i18n.language, { maximumFractionDigits: 1 });
  return { signed: (n: number) => f.format(n), plain: (n: number) => plain.format(n) };
}

function tone(n: number) {
  return n > 0 ? "plus" : n < 0 ? "min" : "zero";
}

const STATUS_ICON = { ok: Check, warn: TriangleAlert, bad: X, neutral: Minus, skipped: Minus } as const;

/** Per-message spam check report: conclusion, score against the limits, and a checklist per check. */
export function SpamReport({ m }: { m: Detail }) {
  const { t, i18n } = useTranslation();
  const num = useNum();
  const [open, setOpen] = useState(readOpen);
  const [showTech, setShowTech] = useState(false);
  const r = m.report;

  const toggle = () => {
    setOpen(!open);
    try {
      localStorage.setItem(STORE_KEY, open ? "0" : "1");
    } catch {
      /* private mode: just don't remember */
    }
  };

  if (!r) {
    return (
      <div className="spamrep">
        <div className="spamrep-head static">
          <span className="ico s-mute"><ShieldQuestion aria-hidden /></span>
          <span className="grow">
            <b>{t("spamreport.title")}</b>
            <span className="why">{t(m.status === "rejected" ? "spamreport.not_scanned_rejected" : "spamreport.not_scanned")}</span>
          </span>
        </div>
      </div>
    );
  }

  const th = r.thresholds;
  const blocked = BLOCKING.has(r.action);
  const flagged = !blocked && FLAGGING.has(r.action);
  const headTone = blocked ? "s-bad" : flagged ? "s-warn" : "s-ok";
  const HeadIcon = blocked || flagged ? ShieldAlert : ShieldCheck;
  const ruleName = (s: string) => r.groups.flatMap((g) => g.items).find((i) => i.symbol === s)?.label ?? t(`symbols.${s}`, { defaultValue: s });
  const why = r.decisive.length
    ? t("spamreport.why_decisive", { rule: ruleName(r.decisive[0]) })
    : t(`spamreport.why_${r.action}`, { score: num.plain(r.score), limit: num.plain(th.quarantine), header: num.plain(th.add_header), defaultValue: t("spamreport.why_other", { score: num.plain(r.score) }) });

  // Sentence with the reasons: what passed for clean mail, what failed otherwise.
  const list = (items: string[]) => {
    try {
      return new Intl.ListFormat(i18n.language, { type: "conjunction" }).format(items);
    } catch {
      return items.join(", ");
    }
  };
  const failed = r.groups.filter((g) => g.status === "bad" || g.status === "warn");
  const passed = r.groups.filter((g) => g.status === "ok" && g.id !== "format" && g.id !== "policy");
  const reasons = blocked || flagged ? failed.map((g) => t(`spamreport.fail_${g.id}`)) : passed.map((g) => t(`spamreport.pass_${g.id}`));
  const intro = blocked ? "spamreport.summary_block" : flagged ? "spamreport.summary_flag" : "spamreport.summary_pass";

  // Score bar from 0 (or the lowest score) to a bit past the reject limit.
  const lo = Math.min(0, r.score);
  const hi = Math.max(th.reject * 1.1, r.score);
  const pct = (v: number) => `${((Math.min(Math.max(v, lo), hi) - lo) / (hi - lo)) * 100}%`;
  const marks: [string, number][] = [["greylist", th.greylist], ["add_header", th.add_header], ["quarantine", th.quarantine], ["reject", th.reject]];

  return (
    <div className={`spamrep${open ? " open" : ""}`}>
      <button className="spamrep-head" onClick={toggle} aria-expanded={open}>
        <span className={`ico ${headTone}`}><HeadIcon aria-hidden /></span>
        <span className="grow">
          <b>{t("spamreport.title")}</b>
          <span className="why">{why}</span>
        </span>
        <ChevronDown className="chev" aria-hidden />
      </button>
      {open && (
        <div className="spamrep-body">
          <div className="gauge">
            <div className="gauge-top">
              <span className="gauge-score">
                {num.signed(r.score)} <small>{t("spamreport.points")}</small>
              </span>
              <span className="muted">{t(`spamreport.action_${r.action}`, { defaultValue: r.action.replace(/_/g, " ") })}</span>
            </div>
            <div
              className="gauge-bar"
              style={{
                background: `linear-gradient(90deg, var(--ok-soft) 0 ${pct(th.greylist)}, var(--warn-soft) ${pct(th.greylist)} ${pct(th.quarantine)}, var(--bad-soft) ${pct(th.quarantine)} 100%)`,
              }}
              role="img"
              aria-label={t("spamreport.gauge_label", { score: num.plain(r.score), limit: num.plain(th.quarantine) })}
            >
              {marks.map(([k, v]) => <span key={k} className="tick" style={{ left: pct(v) }} />)}
              <span className={`me ${headTone}`} style={{ left: pct(r.score) }} />
            </div>
            <div className="gauge-scale" aria-hidden>
              {marks.map(([k, v]) => (
                <span key={k} style={{ left: pct(v) }}>
                  <b className="num">{num.plain(v)}</b>
                  {t(`spamreport.th_${k}`)}
                </span>
              ))}
            </div>
          </div>

          <p className="spamrep-summary">
            {reasons.length > 0 && <>{t(intro, { reasons: list(reasons) })} </>}
            {r.decisive.length > 0 && <>{t("spamreport.decisive", { rules: list(r.decisive.map(ruleName)) })} </>}
            {t("spamreport.total", { score: num.plain(r.score), limit: num.plain(th.quarantine) })}
          </p>

          <div className="checks">
            {r.groups.map((g) => <CheckGroup key={g.id} g={g} />)}
          </div>

          {m.symbols.length > 0 && (
            <div>
              <button className="btn small" onClick={() => setShowTech(!showTech)} aria-expanded={showTech}>
                {showTech ? t("trace.hide_tech") : t("trace.show_tech")}
              </button>
              {showTech && (
                <div className="tbl-wrap" style={{ marginTop: 10 }}>
                  <table>
                    <thead><tr><th>{t("trace.rule")}</th><th style={{ textAlign: "right" }}>{t("trace.score")}</th></tr></thead>
                    <tbody>
                      {m.symbols.map((s) => (
                        <tr key={s.name}>
                          <td className="mono">{s.name}</td>
                          <td className="num" style={{ textAlign: "right", color: s.score > 0 ? "var(--bad)" : s.score < 0 ? "var(--ok)" : undefined }}>{Number(s.score).toFixed(2)}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function CheckGroup({ g }: { g: ReportGroup }) {
  const { t } = useTranslation();
  const num = useNum();
  const Icon = STATUS_ICON[g.status] ?? Minus;
  return (
    <details className="chk" open={g.status === "bad" || g.status === "warn"}>
      <summary>
        <span className={`st st-${g.status}`}><Icon aria-hidden /></span>
        <span className="name">
          {t(`spamreport.group_${g.id}`)}
          <span className="res">{t(`spamreport.res_${g.id}_${g.status}`, { defaultValue: "" })}</span>
        </span>
        <span className={`pts ${tone(g.points)}`}>{g.points === 0 ? "0" : num.signed(g.points)}</span>
        <ChevronDown className="chev" aria-hidden />
      </summary>
      <div className="items">
        {g.items.map((i) => (
          <div key={i.symbol} className="item">
            <span className="what">
              {i.label ?? t(`symbols.${i.symbol}`, { defaultValue: i.symbol })} <span className="rule">{i.symbol}</span>
            </span>
            <span className={`v ${tone(i.score)}`}>{i.score === 0 ? "" : num.signed(i.score)}</span>
            {i.options.length > 0 && <span className="detail">{i.options.join(" · ")}</span>}
          </div>
        ))}
        {g.status !== "skipped" && <span className="muted note">{t(`spamreport.checked_${g.id}`)}</span>}
      </div>
    </details>
  );
}
