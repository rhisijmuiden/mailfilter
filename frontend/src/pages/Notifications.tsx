// System > Notifications: alerts outside the web interface (e-mail, Telegram, webhook).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BellRing, Mail, MessageCircle, Search, Send, Webhook } from "lucide-react";
import type { ReactNode } from "react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, ErrorText, Field, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { api } from "../lib/api";

type Channel = "email" | "telegram" | "webhook";
const CHANNELS: Channel[] = ["email", "telegram", "webhook"];
type TypeCfg = { channels: Channel[]; threshold: number };
type Active = { key: string; type: string; code: string; level: string; since: string; sent: string | null; params: Record<string, unknown> };

type Settings = {
  language: "en" | "nl"; repeat_hours: number; send_resolved: boolean;
  email_enabled: boolean; email_to: string[]; email_default: string[];
  telegram_enabled: boolean; telegram_chat_id: string; has_telegram_token: boolean;
  webhook_enabled: boolean; webhook_url: string; webhook_format: "json" | "slack" | "discord"; has_webhook_secret: boolean;
  types: Record<string, TypeCfg>; type_order: string[]; thresholds: string[]; active: Active[];
};
type Form = Omit<Settings, "email_default" | "has_telegram_token" | "has_webhook_secret" | "type_order" | "thresholds" | "active" | "email_to"> & {
  email_to: string; telegram_token: string; webhook_secret: string; clear_webhook_secret: boolean;
};
type HistoryItem = { time: string; type: string; code: string; key: string; level: string; event: string; params: Record<string, unknown>; channels: Record<string, string> };

const ICONS: Record<Channel, typeof Mail> = { email: Mail, telegram: MessageCircle, webhook: Webhook };

export default function Notifications() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["notifications"], queryFn: () => api.get<Settings>("/settings/notifications") });
  return (
    <>
      <PageHead title={t("notify.title")} sub={t("notify.sub")} />
      {q.isLoading ? <Loading /> : q.data ? <NotifyForm data={q.data} /> : <ErrorText error={q.error} />}
    </>
  );
}

function toForm(d: Settings): Form {
  return {
    language: d.language, repeat_hours: d.repeat_hours, send_resolved: d.send_resolved,
    email_enabled: d.email_enabled, email_to: d.email_to.join("\n"),
    telegram_enabled: d.telegram_enabled, telegram_chat_id: d.telegram_chat_id, telegram_token: "",
    webhook_enabled: d.webhook_enabled, webhook_url: d.webhook_url, webhook_format: d.webhook_format,
    webhook_secret: "", clear_webhook_secret: false, types: d.types,
  };
}

function NotifyForm({ data }: { data: Settings }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState<Form>(toForm(data));
  useEffect(() => setF(toForm(data)), [data]);
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF((x) => ({ ...x, [k]: v }));

  const body = () => ({
    ...f,
    email_to: f.email_to.split(/[\s,;]+/).filter(Boolean),
    telegram_token: f.telegram_token || null,
    webhook_secret: f.clear_webhook_secret ? "" : f.webhook_secret || null,
  });
  const save = useMutation({
    mutationFn: () => api.put<Settings>("/settings/notifications", body()),
    onSuccess: (d) => {
      qc.setQueryData(["notifications"], d);
      qc.invalidateQueries({ queryKey: ["notifications-history"] });
      toast(t("common.saved"));
    },
  });

  const toggleType = (type: string, ch: Channel, on: boolean) => {
    const cur = f.types[type] ?? { channels: [], threshold: 0 };
    const channels = on ? [...new Set([...cur.channels, ch])] : cur.channels.filter((c) => c !== ch);
    set("types", { ...f.types, [type]: { ...cur, channels: CHANNELS.filter((c) => channels.includes(c)) } });
  };
  const setThreshold = (type: string, v: number) => set("types", { ...f.types, [type]: { ...f.types[type], threshold: v } });
  const enabled = (ch: Channel) => f[`${ch}_enabled` as const];

  return (
    <form
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate();
      }}
    >
      <div className="grid halves" style={{ alignItems: "start" }}>
        <div className="grid" style={{ alignContent: "start", minWidth: 0, gridTemplateColumns: "minmax(0,1fr)" }}>
          <ChannelCard ch="email" on={f.email_enabled} setOn={(v) => set("email_enabled", v)} body={body}>
            <Field label={t("notify.email_to")} hint={t("notify.email_to_d", { list: data.email_default.join(", ") || "-" })} full>
              <textarea className="input" rows={3} placeholder={data.email_default.join("\n")} value={f.email_to}
                onChange={(e) => set("email_to", e.target.value)} />
            </Field>
            <p className="muted full" style={{ margin: 0, fontSize: 13 }}>{t("notify.email_via")} <a href="/system/mail">{t("nav.mail_sending")}</a></p>
          </ChannelCard>

          <ChannelCard ch="telegram" on={f.telegram_enabled} setOn={(v) => set("telegram_enabled", v)} body={body}>
            <ol className="muted full" style={{ margin: 0, paddingLeft: 18, fontSize: 13, display: "flex", flexDirection: "column", gap: 4 }}>
              <li>{t("notify.tg_step1")}</li>
              <li>{t("notify.tg_step2")}</li>
              <li>{t("notify.tg_step3")}</li>
            </ol>
            <Field label={t("notify.tg_token")} hint={data.has_telegram_token ? t("notify.secret_keep") : undefined} full>
              <input className="input mono" type="password" autoComplete="off" placeholder={data.has_telegram_token ? "••••••••" : "123456789:AA…"}
                value={f.telegram_token} onChange={(e) => set("telegram_token", e.target.value.trim())} />
            </Field>
            <TelegramChat f={f} hasToken={data.has_telegram_token} onPick={(id) => set("telegram_chat_id", id)} />
          </ChannelCard>

          <ChannelCard ch="webhook" on={f.webhook_enabled} setOn={(v) => set("webhook_enabled", v)} body={body}>
            <Field label={t("notify.wh_url")} full>
              <input className="input" type="url" placeholder="https://" value={f.webhook_url} onChange={(e) => set("webhook_url", e.target.value)} />
            </Field>
            <Field label={t("notify.wh_format")} hint={t(`notify.wh_format_${f.webhook_format}_d`)} full>
              <select className="select" value={f.webhook_format} onChange={(e) => set("webhook_format", e.target.value as Form["webhook_format"])}>
                {(["json", "slack", "discord"] as const).map((x) => <option key={x} value={x}>{t(`notify.wh_format_${x}`)}</option>)}
              </select>
            </Field>
            {f.webhook_format === "json" && (
              <>
                <Field label={t("notify.wh_secret")} hint={data.has_webhook_secret ? t("notify.secret_keep") : t("notify.wh_secret_d")} full>
                  <input className="input mono" type="password" autoComplete="new-password" placeholder={data.has_webhook_secret ? "••••••••" : ""}
                    value={f.webhook_secret} onChange={(e) => setF({ ...f, webhook_secret: e.target.value, clear_webhook_secret: false })} />
                </Field>
                {data.has_webhook_secret && (
                  <label className="check full">
                    <input type="checkbox" checked={f.clear_webhook_secret} onChange={(e) => setF({ ...f, clear_webhook_secret: e.target.checked, webhook_secret: "" })} />
                    <span>{t("notify.wh_secret_clear")}</span>
                  </label>
                )}
              </>
            )}
          </ChannelCard>
        </div>

        <div className="grid" style={{ alignContent: "start", minWidth: 0, gridTemplateColumns: "minmax(0,1fr)" }}>
          <Card title={t("notify.matrix")} right={<button className="btn primary small" disabled={save.isPending}>{t("common.save")}</button>} flush>
            <div className="tbl-wrap">
              <table>
                <thead>
                  <tr>
                    <th>{t("notify.alert")}</th>
                    {CHANNELS.map((ch) => <th key={ch} style={{ textAlign: "center" }}>{t(`notify.ch_${ch}`)}</th>)}
                  </tr>
                </thead>
                <tbody>
                  {data.type_order.map((type) => (
                    <tr key={type}>
                      <td>
                        <div className="cell-main">{t(`notify.type_${type}`)}</div>
                        <div className="cell-sub">{t(`notify.type_${type}_d`)}</div>
                        {data.thresholds.includes(type) && (
                          <label style={{ display: "inline-flex", alignItems: "center", gap: 8, marginTop: 6, fontSize: 13 }}>
                            <span className="muted" style={{ whiteSpace: "nowrap" }}>{t(`notify.threshold_${type}`)}</span>
                            <input className="input" type="number" min={1} max={100000} required style={{ width: 90 }}
                              value={f.types[type]?.threshold ?? 0} onChange={(e) => setThreshold(type, Number(e.target.value))} />
                          </label>
                        )}
                      </td>
                      {CHANNELS.map((ch) => (
                        <td key={ch} style={{ textAlign: "center" }}>
                          <input type="checkbox" aria-label={`${t(`notify.type_${type}`)} - ${t(`notify.ch_${ch}`)}`}
                            checked={f.types[type]?.channels.includes(ch) ?? false} disabled={!enabled(ch)}
                            onChange={(e) => toggleType(type, ch, e.target.checked)} />
                        </td>
                      ))}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </Card>

          <Card title={t("notify.general")}>
            <div className="setting">
              <div className="txt"><b>{t("notify.language")}</b><span>{t("notify.language_d")}</span></div>
              <select className="select" style={{ width: 150 }} value={f.language} onChange={(e) => set("language", e.target.value as Form["language"])}>
                <option value="en">English</option>
                <option value="nl">Nederlands</option>
              </select>
            </div>
            <div className="setting">
              <div className="txt"><b>{t("notify.repeat")}</b><span>{t("notify.repeat_d")}</span></div>
              <input className="input" type="number" min={0} max={168} style={{ width: 90 }} value={f.repeat_hours}
                aria-label={t("notify.repeat")} onChange={(e) => set("repeat_hours", Number(e.target.value))} />
            </div>
            <div className="setting">
              <div className="txt"><b>{t("notify.resolved")}</b><span>{t("notify.resolved_d")}</span></div>
              <label className="switch" style={{ display: "inline-block" }}>
                <input type="checkbox" checked={f.send_resolved} aria-label={t("notify.resolved")} onChange={(e) => set("send_resolved", e.target.checked)} />
                <span />
              </label>
            </div>
            <ErrorText error={save.error} />
            <div style={{ marginTop: 12 }}>
              <button className="btn primary" disabled={save.isPending}>{t("common.save")}</button>
            </div>
          </Card>

          <ActiveCard active={data.active} />
        </div>
      </div>
      <div style={{ marginTop: 20 }}>
        <HistoryCard />
      </div>
    </form>
  );
}

function ChannelCard({ ch, on, setOn, body, children }: { ch: Channel; on: boolean; setOn: (v: boolean) => void; body: () => object; children: ReactNode }) {
  const { t } = useTranslation();
  const Icon = ICONS[ch];
  const test = useMutation({ mutationFn: () => api.post<{ ok: boolean }>("/settings/notifications/test", { ...body(), channel: ch }) });
  return (
    <Card
      title={<span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><Icon style={{ width: 18, height: 18 }} aria-hidden />{t(`notify.ch_${ch}`)}</span>}
      right={
        <label className="switch" style={{ display: "inline-block" }}>
          <input type="checkbox" checked={on} aria-label={t("notify.enable", { channel: t(`notify.ch_${ch}`) })} onChange={(e) => setOn(e.target.checked)} />
          <span />
        </label>
      }
    >
      <p className="muted" style={{ marginTop: 0, marginBottom: 14, fontSize: 13 }}>{t(`notify.ch_${ch}_d`)}</p>
      {on && (
        <div className="form-grid">
          {children}
          <div className="full">
            <ErrorText error={test.error} />
            {test.isSuccess && <div className="callout"><span>{t("notify.test_ok")}</span></div>}
          </div>
          <div className="full">
            <button type="button" className="btn" disabled={test.isPending} onClick={() => test.mutate()}>
              <Send /> {test.isPending ? t("notify.testing") : t("notify.test")}
            </button>
          </div>
        </div>
      )}
    </Card>
  );
}

function TelegramChat({ f, hasToken, onPick }: { f: Form; hasToken: boolean; onPick: (id: string) => void }) {
  const { t } = useTranslation();
  const find = useMutation({
    mutationFn: () => api.post<{ chats: { id: string; name: string; type: string }[] }>("/settings/notifications/telegram/chats", { telegram_token: f.telegram_token || null }),
  });
  return (
    <>
      <Field label={t("notify.tg_chat")} hint={t("notify.tg_chat_d")} full>
        <div style={{ display: "flex", gap: 8 }}>
          <input className="input mono" value={f.telegram_chat_id} placeholder="-1001234567890" onChange={(e) => onPick(e.target.value.trim())} />
          <button type="button" className="btn" disabled={find.isPending || (!hasToken && !f.telegram_token)} onClick={() => find.mutate()}>
            <Search /> {t("notify.tg_find")}
          </button>
        </div>
      </Field>
      {find.error ? <div className="full"><ErrorText error={find.error} /></div> : null}
      {find.data && (
        <div className="full">
          {find.data.chats.length === 0 ? (
            <div className="callout warn"><span>{t("notify.tg_none")}</span></div>
          ) : (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
              {find.data.chats.map((c) => (
                <button type="button" key={c.id} className="chip-btn" onClick={() => onPick(c.id)} aria-pressed={f.telegram_chat_id === c.id}>
                  {c.name} <span className="mono muted">{c.id}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}

function AlertTitle({ code, params, event }: { code: string; params: Record<string, unknown>; event?: string }) {
  const { t } = useTranslation();
  const title = t(`notify.code_${code}`, { ...params, defaultValue: code });
  return <>{event === "resolved" ? `${t("notify.ev_resolved")}: ${title}` : title}</>;
}

function ActiveCard({ active }: { active: Active[] }) {
  const { t, i18n } = useTranslation();
  return (
    <Card title={<span style={{ display: "inline-flex", alignItems: "center", gap: 8 }}><BellRing style={{ width: 18, height: 18 }} aria-hidden />{t("notify.active")}</span>}>
      {active.length === 0 ? (
        <p className="muted" style={{ margin: 0 }}>{t("notify.active_none")}</p>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          {active.map((a) => (
            <div key={a.key} style={{ display: "flex", gap: 10, alignItems: "center" }}>
              <span className={`status ${a.level === "critical" ? "s-bad" : a.level === "warning" ? "s-warn" : "s-info"}`}>{t(`notify.level_${a.level}`)}</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="cell-main"><AlertTitle code={a.code} params={a.params} /></div>
                <div className="cell-sub">
                  {t("notify.since", { when: formatDate(a.since, i18n.language) })}
                  {" · "}
                  {a.sent ? t("notify.sent_at", { when: formatDate(a.sent, i18n.language) }) : t("notify.not_sent")}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}
    </Card>
  );
}

function HistoryCard() {
  const { t, i18n } = useTranslation();
  const q = useQuery({ queryKey: ["notifications-history"], queryFn: () => api.get<{ items: HistoryItem[] }>("/settings/notifications/history"), refetchInterval: 60000 });
  const items = q.data?.items ?? [];
  return (
    <Card title={t("notify.history")} flush>
      {q.isLoading ? (
        <Loading />
      ) : items.length === 0 ? (
        <div className="empty">{t("notify.history_empty")}</div>
      ) : (
        <div className="tbl-wrap">
          <table>
            <thead>
              <tr>
                <th>{t("notify.when")}</th>
                <th>{t("notify.alert")}</th>
                <th>{t("notify.channels")}</th>
              </tr>
            </thead>
            <tbody>
              {items.map((h, i) => (
                <tr key={`${h.time}-${h.key}-${i}`}>
                  <td className="nowrap">{formatDate(h.time, i18n.language)}</td>
                  <td>
                    <div className="cell-main"><AlertTitle code={h.code} params={h.params} event={h.event} /></div>
                    <div className="cell-sub">{t(`notify.level_${h.event === "resolved" ? "resolved" : h.level}`)}</div>
                  </td>
                  <td>
                    <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                      {Object.entries(h.channels).map(([ch, res]) => (
                        <span key={ch} className={`status ${res === "ok" ? "s-ok" : "s-bad"}`} title={res === "ok" ? undefined : res}>
                          {t(`notify.ch_${ch}`)}{res === "ok" ? "" : ` · ${t(`errors.${res.split(":")[0]}`, { value: res.split(":").slice(1).join(":"), defaultValue: res })}`}
                        </span>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Card>
  );
}
