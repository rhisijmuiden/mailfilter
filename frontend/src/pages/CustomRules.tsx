import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, Forward, Info, Newspaper, Paperclip, Save, ShieldAlert, type LucideIcon } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import GatewayError from "../components/GatewayError";
import { Card, Loading, PageHead, useToast } from "../components/ui";
import { api } from "../lib/api";

type Text = { en: string; nl: string };
type Option = {
  id: string;
  type: "score" | "bool" | "text" | "choice" | "list";
  default: unknown;
  value: unknown;
  label: Text;
  max_length?: number;
  max_items?: number;
  choices?: { id: string; label: Text }[];
};
type Rule = {
  id: string;
  group: string;
  direction: "inbound" | "outbound" | "both";
  symbol: string | null;
  title: Text;
  summary: Text;
  details: Text;
  default: boolean;
  enabled: boolean;
  per_domain: boolean;
  options: Option[];
  domains: Record<string, boolean>;
};
type RulesData = { rules: Rule[]; domains: string[]; can_edit_global: boolean };

const KEY = ["custom-rules"];
const ICONS: Record<string, LucideIcon> = { newsletter: Newspaper, phishing: ShieldAlert, attachments: Paperclip, arc: Forward };

// Lists are edited as text (one per line) and sent as arrays.
const toForm = (o: Option): unknown => (o.type === "list" ? ((o.value as string[]) ?? []).join("\n") : o.value);
const fromForm = (o: Option, v: unknown): unknown =>
  o.type === "list" ? String(v ?? "").split(/[\n,;]+/).map((x) => x.trim()).filter(Boolean) : v;

// Configuration > Custom rules: switchable filter rules (newsletters, phishing, attachments, ARC),
// globally and per domain, each with a plain explanation in EN/NL from the server.
export default function CustomRules() {
  const { t } = useTranslation();
  const data = useQuery({ queryKey: KEY, queryFn: () => api.get<RulesData>("/rules") });
  if (!data.data) return <><PageHead title={t("rules.title")} sub={t("rules.sub")} /><Loading /></>;
  const groups = [...new Set(data.data.rules.map((r) => r.group))];
  return (
    <>
      <PageHead title={t("rules.title")} sub={t("rules.sub")} />
      <div className="callout" style={{ marginBottom: 16 }}>
        <Info aria-hidden />
        <div>{data.data.can_edit_global ? t("rules.intro") : t("rules.intro_customer")}</div>
      </div>
      {groups.map((g) => {
        const Icon = ICONS[g] ?? Newspaper;
        return (
        <Card key={g} title={<span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><Icon size={18} aria-hidden /> {t(`rules.group.${g}`, { defaultValue: g })}</span>}
          right={<span className="muted">{t(`rules.group_sub.${g}`, { defaultValue: "" })}</span>}>
          {data.data!.rules.filter((r) => r.group === g).map((r) => (
            <RuleRow key={r.id} rule={r} domains={data.data!.domains} admin={data.data!.can_edit_global} />
          ))}
        </Card>
        );
      })}
    </>
  );
}

function RuleRow({ rule, domains, admin }: { rule: Rule; domains: string[]; admin: boolean }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const lang = i18n.language.startsWith("nl") ? "nl" : "en";
  const [open, setOpen] = useState(false);
  const [perDomain, setPerDomain] = useState(Object.keys(rule.domains).length > 0);
  const [opts, setOpts] = useState<Record<string, unknown>>({});
  const fresh = () => Object.fromEntries(rule.options.map((o) => [o.id, toForm(o)]));
  const current = () => Object.fromEntries(rule.options.map((o) => [o.id, o.value]));
  const payload = () => Object.fromEntries(rule.options.map((o) => [o.id, fromForm(o, opts[o.id])]));
  useEffect(() => setOpts(fresh()), [rule]); // eslint-disable-line react-hooks/exhaustive-deps
  const dirty = rule.options.some((o) => opts[o.id] !== toForm(o));
  const inline = rule.options.filter((o) => o.type !== "list");
  const lists = rule.options.filter((o) => o.type === "list");

  const put = useMutation({
    mutationFn: (body: { enabled: boolean; options: Record<string, unknown> }) =>
      api.put<RulesData>(`/rules/${rule.id}`, { ...body, domains: rule.domains }),
    onSuccess: (res) => { qc.setQueryData(KEY, res); toast(t("rules.saved")); },
  });
  const choose = useMutation({
    mutationFn: ({ domain, state }: { domain: string; state: "default" | "on" | "off" }) =>
      api.put<RulesData>(`/rules/${rule.id}/domains/${encodeURIComponent(domain)}`, { state }),
    onSuccess: (res) => { qc.setQueryData(KEY, res); toast(t("rules.saved")); },
  });
  const busy = put.isPending || choose.isPending;
  const globalState = rule.enabled ? t("rules.on") : t("rules.off");

  return (
    <div className="setting" style={{ alignItems: "flex-start", flexWrap: "wrap" }}>
      <label className="switch" style={{ marginTop: 2 }} title={admin ? undefined : t("rules.admin_only")}>
        <input type="checkbox" checked={rule.enabled} disabled={!admin || busy} aria-label={rule.title[lang]}
          onChange={(e) => put.mutate({ enabled: e.target.checked, options: current() })} />
        <span />
      </label>
      <div className="txt" style={{ minWidth: 240 }}>
        <b>
          {rule.title[lang]}{" "}
          <span className="tag lucht" style={{ marginLeft: 6, verticalAlign: 1 }}>{t(`rules.dir.${rule.direction}`)}</span>
          {rule.enabled !== rule.default && <span className="tag" style={{ marginLeft: 6, verticalAlign: 1 }}>{t("rules.changed")}</span>}
        </b>
        <span style={{ display: "block", marginTop: 2 }}>{rule.summary[lang]}</span>
        <button type="button" className="btn ghost small" style={{ paddingLeft: 0, marginTop: 4 }} aria-expanded={open} onClick={() => setOpen(!open)}>
          {open ? <ChevronUp /> : <ChevronDown />} {open ? t("rules.less") : t("rules.more")}
        </button>
        {open && (
          <div style={{ fontSize: 13.5, lineHeight: 1.55, margin: "4px 0 6px", maxWidth: 760 }}>
            <p style={{ margin: 0 }}>{rule.details[lang]}</p>
            {rule.symbol && <p className="muted" style={{ margin: "6px 0 0", fontSize: 12.5 }}>{t("rules.symbol")} <code className="mono">{rule.symbol}</code></p>}
          </div>
        )}

        {rule.options.length > 0 && (
          <form className="filters" style={{ border: 0, padding: "8px 0 0", gap: 14, flexWrap: "wrap" }}
            onSubmit={(e) => { e.preventDefault(); put.mutate({ enabled: rule.enabled, options: payload() }); }}>
            {inline.map((o) => (
              <label key={o.id} style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13 }}>
                {o.type === "bool" ? (
                  <>
                    <span className="switch">
                      <input type="checkbox" disabled={!admin} checked={Boolean(opts[o.id])} onChange={(e) => setOpts({ ...opts, [o.id]: e.target.checked })} />
                      <span />
                    </span>
                    {o.label[lang]}
                  </>
                ) : o.type === "choice" ? (
                  <>
                    {o.label[lang]}
                    <select className="select" disabled={!admin} style={{ width: "auto" }} value={String(opts[o.id] ?? "")}
                      onChange={(e) => setOpts({ ...opts, [o.id]: e.target.value })}>
                      {(o.choices ?? []).map((c) => <option key={c.id} value={c.id}>{c.label[lang]}</option>)}
                    </select>
                  </>
                ) : (
                  <>
                    {o.label[lang]}
                    <input className="input" disabled={!admin} style={{ width: o.type === "score" ? 90 : 170 }}
                      type={o.type === "score" ? "number" : "text"} step={o.type === "score" ? 0.5 : undefined}
                      min={o.type === "score" ? 0 : undefined} max={o.type === "score" ? 20 : undefined} maxLength={o.max_length}
                      value={String(opts[o.id] ?? "")} required={o.type === "score"}
                      onChange={(e) => setOpts({ ...opts, [o.id]: o.type === "score" ? Number(e.target.value) : e.target.value })} />
                  </>
                )}
              </label>
            ))}
            {lists.map((o) => (
              <label key={o.id} style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13, flex: "1 1 100%", maxWidth: 560 }}>
                <span>{o.label[lang]} <span className="muted" style={{ fontSize: 12 }}>({t("rules.list_hint")})</span></span>
                <textarea className="input mono" rows={4} disabled={!admin} style={{ fontSize: 12.5 }}
                  value={String(opts[o.id] ?? "")} onChange={(e) => setOpts({ ...opts, [o.id]: e.target.value })} />
                {admin && Array.isArray(o.default) && (o.default as string[]).length > 0 && String(opts[o.id]) !== (o.default as string[]).join("\n") && (
                  <button type="button" className="btn ghost small" style={{ alignSelf: "flex-start", paddingLeft: 0 }}
                    onClick={() => setOpts({ ...opts, [o.id]: (o.default as string[]).join("\n") })}>{t("rules.reset_default")}</button>
                )}
              </label>
            ))}
            {admin && dirty && (
              <button className="btn primary small" disabled={busy}><Save /> {put.isPending ? t("gateway.applying") : t("common.save")}</button>
            )}
          </form>
        )}

        {!rule.per_domain && <p className="muted" style={{ fontSize: 12.5, margin: "8px 0 0" }}>{t("rules.global_only")}</p>}
        {rule.per_domain && domains.length > 0 && (
          <div style={{ marginTop: 8 }}>
            <button type="button" className="btn ghost small" style={{ paddingLeft: 0 }} aria-expanded={perDomain} onClick={() => setPerDomain(!perDomain)}>
              {perDomain ? <ChevronUp /> : <ChevronDown />} {t("rules.per_domain")}
              {Object.keys(rule.domains).length > 0 && <span className="tag" style={{ marginLeft: 4 }}>{Object.keys(rule.domains).length}</span>}
            </button>
            {perDomain && (
              <div style={{ marginTop: 4 }}>
                <p className="muted" style={{ fontSize: 12.5, margin: "0 0 6px" }}>{t("rules.per_domain_hint")}</p>
                {domains.map((d) => {
                  const state = d in rule.domains ? (rule.domains[d] ? "on" : "off") : "default";
                  return (
                    <div key={d} style={{ display: "flex", alignItems: "center", gap: 12, padding: "5px 0", flexWrap: "wrap" }}>
                      <span className="mono" style={{ minWidth: 180 }}>{d}</span>
                      <div className="seg" role="group" aria-label={d}>
                        {(["default", "on", "off"] as const).map((s) => (
                          <button key={s} type="button" aria-pressed={state === s} disabled={busy}
                            onClick={() => state !== s && choose.mutate({ domain: d, state: s })}>
                            {s === "default" ? t("rules.follow_default", { state: globalState }) : t(`rules.${s}`)}
                          </button>
                        ))}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
        <div style={{ marginTop: 6 }}>
          <GatewayError error={put.error || choose.error} />
        </div>
      </div>
    </div>
  );
}
