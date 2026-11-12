// Mailtrap: mail from test environments is caught here and never sent. Three
// panes like a mail program (mailboxes, messages, preview with checks), plus a
// drawer where admins set up a mailbox (IPs, login, access, retention).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle, Check, Download, FlaskConical, Forward, Inbox, KeyRound, Minus, Paperclip, Plus, RefreshCw, Search, Send,
  Server, Settings2, Trash2, UserCheck, X,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import GatewayError from "../components/GatewayError";
import { Drawer, ErrorText, Field, Loading, Modal, PageHead, useToast } from "../components/ui";
import { api, ApiError, qs } from "../lib/api";
import type { Me } from "../lib/auth";
import { formatSize } from "../lib/trace";
import { CopyBtn } from "./Dns";

type Network = { cidr: string; description: string };
type Box = {
  id: number; slug: string; login: string; name: string; domain: string | null; networks: Network[];
  smtp_login: boolean; imap: boolean; mode: "all" | "except"; allow: string[]; retention_days: number; max_messages: number;
  users: number[]; user_emails: string[]; enabled: boolean; total: number; unread: number; bytes: number;
};
type BoxCreated = Box & { password: string };
type Status = { enabled: boolean; can_manage: boolean; imap_host: string; boxes: Box[] };
type Issue = { code: string; status: "bad" | "warn" | "info" | "ok" };
type Row = {
  id: string; seen: boolean; received: number; size: number; subject: string; from: string; to: string[]; to_count: number;
  attachments: boolean; score: number | null; issues: Issue[];
};
type CheckItem = { code: string; status: "bad" | "warn" | "info" | "ok"; params: Record<string, any> };
type Detail = {
  id: string; received: number; size: number; subject: string; from: string; header_to: string; cc: string;
  envelope_from: string; envelope_to: string[]; delivered_to: string[]; client: string; date: string;
  html: string | null; text: string | null; headers: string; source: string; source_truncated: boolean;
  attachments: { name: string; type: string; size: number }[]; checks: CheckItem[];
};
type AccessUser = { id: number; email: string; name: string; role: string };

const TONE: Record<string, string> = { ok: "s-ok", warn: "s-warn", bad: "s-bad", info: "s-mute" };

function when(ts: number, lang: string): string {
  const d = new Date(ts * 1000);
  const today = new Date();
  const loc = lang === "nl" ? "nl-NL" : "en-GB";
  if (d.toDateString() === today.toDateString()) return d.toLocaleTimeString(loc, { hour: "2-digit", minute: "2-digit" });
  return d.toLocaleDateString(loc, { day: "numeric", month: "short" });
}

function scoreTone(score: number | null): string {
  if (score == null) return "s-mute";
  return score < 5 ? "s-ok" : score < 15 ? "s-warn" : "s-bad";
}

// ---- page ---------------------------------------------------------------------------------

export default function Mailtrap({ me }: { me: Me }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const status = useQuery({ queryKey: ["mailtrap"], queryFn: () => api.get<Status>("/mailtrap"), refetchInterval: 30_000 });
  const [boxId, setBoxId] = useState<number | null>(null);
  const [msgId, setMsgId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Box | "new" | null>(null);
  const [password, setPassword] = useState<BoxCreated | null>(null);
  const admin = me.role === "admin";

  const boxes = status.data?.boxes ?? [];
  // Start with the first mailbox that has mail in it
  const box = boxes.find((b) => b.id === boxId) ?? boxes.find((b) => b.total > 0) ?? boxes[0] ?? null;
  useEffect(() => { setMsgId(null); }, [box?.id]);

  const toggle = useMutation({
    mutationFn: (enabled: boolean) => api.put("/mailtrap/settings", { enabled }),
    onSuccess: (_, enabled) => {
      qc.invalidateQueries({ queryKey: ["mailtrap"] });
      qc.invalidateQueries({ queryKey: ["mailtrap-menu"] });
      toast(t(enabled ? "mailtrap.switched_on" : "mailtrap.switched_off"));
    },
  });

  if (status.isLoading) return <Loading />;
  if (status.error) return <ErrorText error={status.error} />;
  const on = !!status.data?.enabled;

  return (
    <>
      <PageHead
        title={t("mailtrap.title")}
        sub={<>{t("mailtrap.sub_1")} <b>{t("mailtrap.sub_never")}</b>{t("mailtrap.sub_2")}</>}
        actions={admin && (
          <>
            <label className="mt-switch" htmlFor="mt-global">
              <span className="switch"><input id="mt-global" type="checkbox" checked={on} disabled={toggle.isPending}
                onChange={(e) => toggle.mutate(e.target.checked)} /><span /></span>
              {t("mailtrap.module")} <span className={on ? "st-on" : "st-off"}>{t(on ? "mailtrap.on" : "mailtrap.off")}</span>
            </label>
            <button className="btn primary" type="button" onClick={() => setEditing("new")}><Plus /> {t("mailtrap.add")}</button>
          </>
        )}
      />
      <GatewayError error={toggle.error} />
      {!on && (
        <div className="callout warn"><AlertTriangle aria-hidden />
          <span>{t(admin ? "mailtrap.off_note_admin" : "mailtrap.off_note")}</span>
        </div>
      )}

      {boxes.length === 0 ? (
        <section className="card"><div className="empty">
          <FlaskConical aria-hidden style={{ width: 32, height: 32, color: "var(--ink-muted)" }} />
          <p style={{ maxWidth: "56ch", margin: "8px auto" }}>{t(admin ? "mailtrap.empty_admin" : "mailtrap.empty")}</p>
          {admin && <button className="btn primary" type="button" onClick={() => setEditing("new")}><Plus /> {t("mailtrap.add")}</button>}
        </div></section>
      ) : (
        <section className="card trap">
          <div className="pane">
            <div className="pane-head"><b>{t("mailtrap.mailboxes")}</b></div>
            <div className="mbx" role="list">
              {boxes.map((b) => (
                <button key={b.id} type="button" aria-current={b.id === box?.id} onClick={() => setBoxId(b.id)}>
                  <span className="n">{b.name}</span>
                  <span className="c num" title={t("mailtrap.unread_of", { unread: b.unread, total: b.total })}>{b.unread || b.total}</span>
                  <span className="s">
                    {!b.enabled ? t("mailtrap.box_off") : [...b.networks.map((n) => n.cidr), ...(b.smtp_login ? [b.login] : [])].join(", ") || t("mailtrap.no_source")}
                  </span>
                </button>
              ))}
            </div>
            {admin && box && (
              <div style={{ padding: "4px 14px 16px", marginTop: "auto" }}>
                <button className="btn small" type="button" onClick={() => setEditing(box)}><Settings2 /> {t("mailtrap.settings")}</button>
              </div>
            )}
          </div>
          {box && <MessageList box={box} selected={msgId} onSelect={setMsgId} />}
          {box && <Preview box={box} msgId={msgId} admin={admin} onGone={() => setMsgId(null)} />}
        </section>
      )}

      <section className="card">
        <div className="card-head"><h2>{t("mailtrap.how_title")}</h2></div>
        <div className="card-body how">
          <div className="list-row"><Server aria-hidden /><span className="grow">{t("mailtrap.how_1")}</span></div>
          <div className="list-row"><Check aria-hidden /><span className="grow">{t("mailtrap.how_2")}</span></div>
          <div className="list-row"><UserCheck aria-hidden /><span className="grow">{t("mailtrap.how_3")}</span></div>
          <div className="list-row"><RefreshCw aria-hidden /><span className="grow">{t("mailtrap.how_4")}</span></div>
        </div>
      </section>

      {editing && (
        <BoxDrawer box={editing === "new" ? null : editing} imapHost={status.data?.imap_host ?? ""} onClose={() => setEditing(null)}
          onSaved={(saved) => {
            qc.invalidateQueries({ queryKey: ["mailtrap"] });
            qc.invalidateQueries({ queryKey: ["mailtrap-menu"] });
            setEditing(null);
            if ("password" in saved) setPassword(saved as BoxCreated);
            setBoxId(saved.id);
          }}
          onDeleted={() => { qc.invalidateQueries({ queryKey: ["mailtrap"] }); setEditing(null); setBoxId(null); }} />
      )}
      {password && <PasswordModal box={password} host={status.data?.imap_host ?? ""} onClose={() => setPassword(null)} />}
    </>
  );
}

// ---- message list ---------------------------------------------------------------------------

function MessageList({ box, selected, onSelect }: { box: Box; selected: string | null; onSelect: (id: string) => void }) {
  const { t, i18n } = useTranslation();
  const [q, setQ] = useState("");
  const [search, setSearch] = useState("");
  useEffect(() => { const h = setTimeout(() => setSearch(q), 250); return () => clearTimeout(h); }, [q]);
  const list = useQuery({
    queryKey: ["mailtrap-msgs", box.id, search],
    queryFn: () => api.get<{ total: number; messages: Row[] }>(`/mailtrap/boxes/${box.id}/messages${qs({ q: search })}`),
    refetchInterval: 15_000,
  });
  const rows = list.data?.messages ?? [];
  useEffect(() => { if (!selected && rows.length) onSelect(rows[0]!.id); }, [rows, selected, onSelect]);

  return (
    <div className="pane">
      <div className="pane-head">
        <div className="search" style={{ padding: "6px 10px" }}>
          <Search aria-hidden />
          <input id="mt-search" value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("mailtrap.search")} aria-label={t("mailtrap.search")} />
        </div>
      </div>
      <div className="msgs">
        {list.isLoading && <Loading />}
        {!list.isLoading && rows.length === 0 && <div className="empty">{t(search ? "mailtrap.no_results" : "mailtrap.no_mail")}</div>}
        {rows.map((m) => (
          <div key={m.id} className={`msg${m.seen ? "" : " unread"}`} aria-current={m.id === selected} tabIndex={0} role="button"
            onClick={() => onSelect(m.id)} onKeyDown={(e) => e.key === "Enter" && onSelect(m.id)}>
            <span className="to">{m.to_count > 1 ? t("mailtrap.to_many", { first: m.to[0], count: m.to_count - 1 }) : t("mailtrap.to_one", { to: m.to[0] ?? "-" })}</span>
            <span className="tm">{when(m.received, i18n.language)}</span>
            <span className="subj">{m.subject || t("mailtrap.no_subject")}</span>
            <span className="chips">
              {m.score != null && <span className={`mini ${scoreTone(m.score)}`}>{t("mailtrap.spam_chip", { score: m.score.toLocaleString(i18n.language) })}</span>}
              {m.issues.slice(0, 2).map((x, i) => (
                <span key={i} className={`mini ${TONE[x.status]}`}>{t(`mailtrap.issue.${x.code}`, { defaultValue: x.code })}</span>
              ))}
              {m.issues.length > 2 && <span className="mini s-mute">+{m.issues.length - 2}</span>}
              {m.attachments && <span className="mini s-mute"><Paperclip aria-hidden /></span>}
            </span>
          </div>
        ))}
      </div>
      <div className="pane-foot">{t("mailtrap.footer", { count: box.total, size: formatSize(box.bytes), days: box.retention_days })}</div>
    </div>
  );
}

// ---- preview ----------------------------------------------------------------------------------

type Tab = "view" | "check" | "text" | "headers" | "source";

function Preview({ box, msgId, admin, onGone }: { box: Box; msgId: string | null; admin: boolean; onGone: () => void }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("view");
  const [width, setWidth] = useState<"100%" | "375px">("100%");
  const [confirm, setConfirm] = useState(false);
  const d = useQuery({
    queryKey: ["mailtrap-msg", box.id, msgId],
    queryFn: () => api.get<Detail>(`/mailtrap/boxes/${box.id}/messages/${encodeURIComponent(msgId!)}`),
    enabled: !!msgId,
  });
  useEffect(() => { setConfirm(false); if (d.data) { qc.invalidateQueries({ queryKey: ["mailtrap-msgs", box.id] }); qc.invalidateQueries({ queryKey: ["mailtrap-menu"] }); } }, [d.data, box.id, qc]);
  const base = `/mailtrap/boxes/${box.id}/messages/${encodeURIComponent(msgId ?? "")}`;
  const refresh = () => { qc.invalidateQueries({ queryKey: ["mailtrap-msgs", box.id] }); qc.invalidateQueries({ queryKey: ["mailtrap"] }); };
  const forward = useMutation({ mutationFn: () => api.post<{ sent_to: string }>(`${base}/forward`), onSuccess: (r) => toast(t("mailtrap.forwarded", { to: r.sent_to })) });
  const release = useMutation({
    mutationFn: () => api.post<{ sent_to: string[] }>(`${base}/release`),
    onSuccess: (r) => { setConfirm(false); toast(t("mailtrap.released", { to: r.sent_to.join(", ") })); },
  });
  const del = useMutation({ mutationFn: () => api.del(base), onSuccess: () => { onGone(); refresh(); toast(t("mailtrap.deleted")); } });
  const problems = (d.data?.checks ?? []).filter((c) => c.status === "bad").length;

  if (!msgId) return <div className="pane preview"><div className="empty">{t("mailtrap.pick")}</div></div>;
  if (d.isLoading) return <div className="pane preview"><Loading /></div>;
  if (d.error || !d.data) return <div className="pane preview"><div className="pv-body"><ErrorText error={d.error} /></div></div>;
  const m = d.data;
  const pending = m.envelope_to.filter((r) => !m.delivered_to.includes(r));

  return (
    <div className="pane preview">
      <div className="pv-head">
        <h2>{m.subject || t("mailtrap.no_subject")}</h2>
        <dl className="kv">
          <dt>{t("mailtrap.from")}</dt><dd>{m.from || m.envelope_from || "-"}</dd>
          <dt>{t("mailtrap.would_go")}</dt>
          <dd>
            {m.envelope_to.join(", ")} <span className="tag">{t("mailtrap.not_sent")}</span>
            {m.delivered_to.length > 0 && <div className="cell-sub">{t("mailtrap.delivered_testers", { to: m.delivered_to.join(", ") })}</div>}
          </dd>
          <dt>{t("mailtrap.came_from")}</dt><dd className="mono">{m.client || "-"}</dd>
          <dt>{t("mailtrap.received")}</dt>
          <dd>{new Date(m.received * 1000).toLocaleString(i18n.language === "nl" ? "nl-NL" : "en-GB")} · {formatSize(m.size)}</dd>
        </dl>
        <div className="pv-tools">
          <button className="btn small" type="button" disabled={forward.isPending} onClick={() => forward.mutate()}><Forward /> {t("mailtrap.forward")}</button>
          {admin && pending.length > 0 && (
            <button className="btn small" type="button" onClick={() => setConfirm(true)}><Send /> {t("mailtrap.release")}</button>
          )}
          <a className="btn small" href={`/api${base}/raw`} download><Download /> .eml</a>
          <button className="btn small ghost danger" type="button" disabled={del.isPending} onClick={() => del.mutate()}><Trash2 /> {t("common.delete")}</button>
        </div>
        {confirm && (
          <div className="callout warn" role="alert">
            <AlertTriangle aria-hidden />
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <span>{t("mailtrap.release_confirm", { to: pending.join(", ") })}</span>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="btn small dark" type="button" disabled={release.isPending} onClick={() => release.mutate()}><Send /> {t("mailtrap.release_yes")}</button>
                <button className="btn small ghost" type="button" onClick={() => setConfirm(false)}>{t("common.cancel")}</button>
              </div>
            </div>
          </div>
        )}
        <ErrorText error={forward.error || release.error || del.error} />
      </div>
      <div className="tabs" role="tablist">
        {(["view", "check", "text", "headers", "source"] as Tab[]).map((k) => (
          <button key={k} type="button" role="tab" aria-selected={tab === k} onClick={() => setTab(k)}>
            {t(`mailtrap.tab.${k}`)}
            {k === "check" && problems > 0 && <span className="tag" style={{ color: "var(--bad)", marginLeft: 6 }}>{problems}</span>}
          </button>
        ))}
      </div>
      <div className="pv-body">
        {tab === "view" && (m.html ? (
          <>
            <div className="devices">
              <span className="hint">{t("mailtrap.view_as")}</span>
              <div className="seg" role="group" aria-label={t("mailtrap.view_as")}>
                <button type="button" aria-pressed={width === "100%"} onClick={() => setWidth("100%")}>{t("mailtrap.desktop")}</button>
                <button type="button" aria-pressed={width === "375px"} onClick={() => setWidth("375px")}>{t("mailtrap.phone")}</button>
              </div>
              <span className="hint">{t("mailtrap.remote_images")}</span>
            </div>
            {/* sandbox="": no scripts, no forms, no navigation; the page's CSP blocks remote images. */}
            <iframe className="mailframe" title={t("mailtrap.tab.view")} sandbox="" srcDoc={m.html}
              style={{ width, margin: width === "100%" ? 0 : "0 auto" }} />
          </>
        ) : <pre className="log" style={{ whiteSpace: "pre-wrap" }}>{m.text || t("mailtrap.empty_body")}</pre>)}
        {tab === "check" && <Checks checks={m.checks} />}
        {tab === "text" && <pre className="log" style={{ whiteSpace: "pre-wrap" }}>{m.text || t("mailtrap.no_text")}</pre>}
        {tab === "headers" && <pre className="log">{m.headers}</pre>}
        {tab === "source" && <pre className="log">{m.source}{m.source_truncated ? `\n\n${t("mailtrap.truncated")}` : ""}</pre>}
        {m.attachments.length > 0 && tab === "view" && (
          <div className="sender-rows">
            {m.attachments.map((a, i) => (
              <div key={i} className="sender-row"><Paperclip aria-hidden /><span>{a.name || a.type}</span><span className="muted">{formatSize(a.size)}</span></div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}

function Checks({ checks }: { checks: CheckItem[] }) {
  const { t, i18n } = useTranslation();
  const spam = checks.find((c) => c.code === "spam");
  const fmt = (p: Record<string, any>) => ({
    ...p,
    score: typeof p.score === "number" ? p.score.toLocaleString(i18n.language) : p.score,
    size: typeof p.size === "number" ? formatSize(p.size) : p.size,
    limit: typeof p.limit === "number" ? formatSize(p.limit) : p.limit,
  });
  const list = (p: Record<string, any>): string[] => p.links ?? p.fields ?? p.files ?? p.images ?? [];
  return (
    <>
      {spam && (
        <div className={`score ${scoreTone(spam.params.score)}`}>
          <span className="big num">{Number(spam.params.score).toLocaleString(i18n.language)}</span>
          <span><b>{t(`mailtrap.spam_${spam.status}`)}</b><span className="muted" style={{ fontSize: 12.5 }}>{t("mailtrap.spam_scale", { mark: spam.params.mark, reject: spam.params.reject })}</span></span>
        </div>
      )}
      <div className="chk-list">
        {checks.filter((c) => c.code !== "spam").map((c, i) => (
          <div key={i} className="chk-row">
            <span className={`st st-${c.status === "info" ? "neutral" : c.status}`} aria-hidden>
              {c.status === "ok" ? <Check /> : c.status === "info" ? <Minus /> : c.status === "warn" ? <AlertTriangle /> : <X />}
            </span>
            <span style={{ minWidth: 0 }}>
              <b>{t(`mailtrap.check.${c.code}.title`, fmt(c.params))}</b>
              <span className="d">{t(`mailtrap.check.${c.code}.desc`, { ...fmt(c.params), defaultValue: "" })}</span>
              {list(c.params).length > 0 && (
                <span className="d" style={{ display: "flex", flexDirection: "column", gap: 2, marginTop: 4 }}>
                  {list(c.params).map((x, j) => <code key={j}>{x}</code>)}
                </span>
              )}
            </span>
            <span className={`mini ${TONE[c.status]}`}>{t(`mailtrap.verdict.${c.status}`)}</span>
          </div>
        ))}
      </div>
    </>
  );
}

// ---- mailbox settings (admins) ----------------------------------------------------------------

function BoxDrawer({ box, imapHost, onClose, onSaved, onDeleted }: {
  box: Box | null; imapHost: string; onClose: () => void; onSaved: (b: Box | BoxCreated) => void; onDeleted: () => void;
}) {
  const { t } = useTranslation();
  const toast = useToast();
  const [name, setName] = useState(box?.name ?? "");
  const [domain, setDomain] = useState(box?.domain ?? "");
  const [networks, setNetworks] = useState<Network[]>(box?.networks ?? []);
  const [newNet, setNewNet] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [smtpLogin, setSmtpLogin] = useState(box?.smtp_login ?? false);
  const [imap, setImap] = useState(box?.imap ?? false);
  const [mode, setMode] = useState<"all" | "except">(box?.mode ?? "all");
  const [allow, setAllow] = useState((box?.allow ?? []).join(", "));
  const [users, setUsers] = useState<number[]>(box?.users ?? []);
  const [days, setDays] = useState(String(box?.retention_days ?? 7));
  const [max, setMax] = useState(String(box?.max_messages ?? 1000));
  const [enabled, setEnabled] = useState(box?.enabled ?? true);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [newPw, setNewPw] = useState<string | null>(null);
  const domains = useQuery({ queryKey: ["mailtrap-domains"], queryFn: () => api.get<string[]>("/mailtrap/domains") });
  const people = useQuery({ queryKey: ["mailtrap-users"], queryFn: () => api.get<AccessUser[]>("/mailtrap/users") });

  const addNet = () => {
    const c = newNet.trim();
    if (!c || networks.some((n) => n.cidr === c)) return;
    setNetworks([...networks, { cidr: c, description: newDesc.trim() }]);
    setNewNet(""); setNewDesc("");
  };
  const body = () => ({
    name: name.trim(), domain: domain || null, networks: newNet.trim() ? [...networks, { cidr: newNet.trim(), description: newDesc.trim() }] : networks,
    smtp_login: smtpLogin, imap, mode, allow: allow.split(/[\s,;]+/).filter(Boolean), users,
    retention_days: Number(days) || 7, max_messages: Number(max) || 1000, enabled,
  });
  const save = useMutation({
    mutationFn: () => box ? api.put<Box>(`/mailtrap/boxes/${box.id}`, body()) : api.post<BoxCreated>("/mailtrap/boxes", body()),
    onSuccess: (b) => { toast(t("mailtrap.saved")); onSaved(b); },
  });
  const remove = useMutation({ mutationFn: () => api.del(`/mailtrap/boxes/${box!.id}`), onSuccess: () => { toast(t("mailtrap.box_deleted")); onDeleted(); } });
  const empty = useMutation({ mutationFn: () => api.del(`/mailtrap/boxes/${box!.id}/messages`), onSuccess: () => toast(t("mailtrap.emptied")) });
  const resetPw = useMutation({ mutationFn: () => api.post<BoxCreated>(`/mailtrap/boxes/${box!.id}/password`), onSuccess: (b) => setNewPw(b.password) });
  const nonAdmins = useMemo(() => (people.data ?? []).filter((u) => u.role !== "admin"), [people.data]);
  const conflict = save.error instanceof ApiError && save.error.code === "network_in_other_mailbox" ? save.error.info : null;

  return (
    <Drawer title={box ? box.name : t("mailtrap.new_box")} onClose={onClose}
      sub={<><span className="tag lucht">{t("mailtrap.box_tag")}</span>{box && ` ${t("mailtrap.box_stats", { count: box.total, size: formatSize(box.bytes) })}`}</>}>
      <form style={{ display: "contents" }} onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="form-grid">
          <Field label={t("mailtrap.name")}><input id="mt-name" className="input" required value={name} onChange={(e) => setName(e.target.value)} placeholder={t("mailtrap.name_ph")} /></Field>
          <Field label={t("mailtrap.customer")} hint={t("mailtrap.customer_hint")}>
            <select id="mt-domain" className="select" value={domain} onChange={(e) => setDomain(e.target.value)}>
              <option value="">{t("mailtrap.no_customer")}</option>
              {(domains.data ?? []).map((d) => <option key={d} value={d}>{d}</option>)}
            </select>
          </Field>
        </div>

        <section className="drawer-sec">
          <h3 className="sec-title">{t("mailtrap.sources")}</h3>
          {networks.length > 0 && (
            <div className="sender-rows">
              {networks.map((n) => (
                <div key={n.cidr} className="sender-row">
                  <Server aria-hidden /><span className="mono">{n.cidr}</span><span className="muted">{n.description}</span>
                  <button type="button" className="icon-btn" aria-label={t("common.delete")} onClick={() => setNetworks(networks.filter((x) => x !== n))}><Trash2 /></button>
                </div>
              ))}
            </div>
          )}
          <div className="inline-add">
            <input id="mt-net" className="input inline" style={{ flex: "1 1 160px" }} placeholder="10.0.8.21 / 2a01:…/64" value={newNet} onChange={(e) => setNewNet(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addNet(); } }} />
            <input id="mt-net-desc" className="input inline" style={{ flex: "1 1 140px" }} placeholder={t("mailtrap.net_desc_ph")} value={newDesc} onChange={(e) => setNewDesc(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") { e.preventDefault(); addNet(); } }} />
            <button type="button" className="btn small" onClick={addNet}><Plus /> {t("mailtrap.add_net")}</button>
          </div>
          <span className="hint">{t("mailtrap.sources_hint")}</span>
          <label className="check">
            <span className="switch"><input id="mt-smtp" type="checkbox" checked={smtpLogin} onChange={(e) => setSmtpLogin(e.target.checked)} /><span /></span>
            <span><b>{t("mailtrap.smtp_login")}</b><br /><span className="hint">{t("mailtrap.smtp_login_hint", { login: box?.login ?? "trap-…" })}</span></span>
          </label>
          {conflict && <div className="callout warn" role="alert"><AlertTriangle aria-hidden /><span>{t("mailtrap.net_conflict", { cidr: conflict.cidr, mailbox: conflict.mailbox })}</span></div>}
        </section>

        <section className="drawer-sec">
          <h3 className="sec-title">{t("mailtrap.what")}</h3>
          <div role="radiogroup" aria-label={t("mailtrap.what")} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
            <label className={`kind-choice${mode === "all" ? " on" : ""}`}>
              <input type="radio" name="mt-mode" checked={mode === "all"} onChange={() => setMode("all")} />
              <span className="kind-icon" aria-hidden><Inbox /></span>
              <span><b>{t("mailtrap.mode_all")}</b><br /><span className="muted">{t("mailtrap.mode_all_hint")}</span></span>
            </label>
            <label className={`kind-choice${mode === "except" ? " on" : ""}`}>
              <input type="radio" name="mt-mode" checked={mode === "except"} onChange={() => setMode("except")} />
              <span className="kind-icon" aria-hidden><UserCheck /></span>
              <span style={{ flex: 1, minWidth: 0 }}><b>{t("mailtrap.mode_except")}</b><br /><span className="muted">{t("mailtrap.mode_except_hint")}</span></span>
            </label>
          </div>
          {mode === "except" && (
            <Field label={t("mailtrap.allow")} hint={t("mailtrap.allow_hint")}>
              <input id="mt-allow" className="input" value={allow} onChange={(e) => setAllow(e.target.value)} placeholder="@rhis.nl, tester@example.nl" />
            </Field>
          )}
        </section>

        <section className="drawer-sec">
          <h3 className="sec-title">{t("mailtrap.access")}</h3>
          <span className="hint">{t("mailtrap.access_hint")}</span>
          {nonAdmins.length > 0 && (
            <div className="sender-rows" style={{ maxHeight: 220, overflowY: "auto" }}>
              {nonAdmins.map((u) => (
                <label key={u.id} className="sender-row check" style={{ cursor: "pointer" }}>
                  <input type="checkbox" checked={users.includes(u.id)}
                    onChange={(e) => setUsers(e.target.checked ? [...users, u.id] : users.filter((x) => x !== u.id))} />
                  <span>{u.name || u.email}</span><span className="muted">{u.name ? u.email : ""}</span>
                </label>
              ))}
            </div>
          )}
          <label className="check">
            <span className="switch"><input id="mt-imap" type="checkbox" checked={imap} onChange={(e) => setImap(e.target.checked)} /><span /></span>
            <span><b>{t("mailtrap.imap")}</b><br /><span className="hint">{t("mailtrap.imap_hint")}</span></span>
          </label>
          {(imap || smtpLogin) && box && (
            <dl className="creds">
              <dt>{t("mailtrap.server")}</dt><dd>{imapHost}</dd><span />
              {imap && <><dt>{t("mailtrap.port")}</dt><dd>993 (SSL/TLS)</dd><span /></>}
              <dt>{t("mailtrap.username")}</dt><dd>{box.login}</dd><CopyBtn text={box.login} />
              <dt>{t("mailtrap.password")}</dt>
              <dd>{newPw ? <code className="pw">{newPw}</code> : "•••••••••••••"}</dd>
              {newPw ? <CopyBtn text={newPw} /> : (
                <button type="button" className="btn small ghost" disabled={resetPw.isPending} onClick={() => resetPw.mutate()}><KeyRound /> {t("mailtrap.new_password")}</button>
              )}
            </dl>
          )}
          {(imap || smtpLogin) && !box && <span className="hint">{t("mailtrap.password_after_save")}</span>}
        </section>

        <section className="drawer-sec">
          <h3 className="sec-title">{t("mailtrap.keep")}</h3>
          <div className="form-grid">
            <Field label={t("mailtrap.days")}><input id="mt-days" className="input" type="number" min={1} max={365} value={days} onChange={(e) => setDays(e.target.value)} /></Field>
            <Field label={t("mailtrap.max")} hint={t("mailtrap.max_hint")}><input id="mt-max" className="input" type="number" min={1} max={100000} value={max} onChange={(e) => setMax(e.target.value)} /></Field>
          </div>
        </section>

        <label className="check">
          <span className="switch"><input id="mt-enabled" type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} /><span /></span>
          <span><b>{t("mailtrap.box_enabled")}</b><br /><span className="hint">{t("mailtrap.box_enabled_hint")}</span></span>
        </label>

        {!conflict && <GatewayError error={save.error} />}
        <ErrorText error={remove.error || empty.error || resetPw.error} />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <button className="btn primary" type="submit" disabled={save.isPending}>{save.isPending ? t("common.saving") : t("common.save")}</button>
          <button className="btn ghost" type="button" onClick={onClose}>{t("common.cancel")}</button>
          {box && !confirmDelete && (
            <>
              <button className="btn ghost small" type="button" style={{ marginLeft: "auto" }} disabled={empty.isPending} onClick={() => empty.mutate()}><Trash2 /> {t("mailtrap.empty_box")}</button>
              <button className="btn danger" type="button" onClick={() => setConfirmDelete(true)}><Trash2 /> {t("common.delete")}</button>
            </>
          )}
        </div>
        {confirmDelete && (
          <div className="callout warn" role="alert"><AlertTriangle aria-hidden />
            <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <span>{t("mailtrap.delete_confirm", { count: box?.total ?? 0 })}</span>
              <div style={{ display: "flex", gap: 8 }}>
                <button className="btn small danger" type="button" disabled={remove.isPending} onClick={() => remove.mutate()}>{t("common.delete")}</button>
                <button className="btn small ghost" type="button" onClick={() => setConfirmDelete(false)}>{t("common.cancel")}</button>
              </div>
            </div>
          </div>
        )}
      </form>
    </Drawer>
  );
}

function PasswordModal({ box, host, onClose }: { box: BoxCreated; host: string; onClose: () => void }) {
  const { t } = useTranslation();
  return (
    <Modal title={t("mailtrap.created_title")} onClose={onClose} icon={<KeyRound aria-hidden />}>
      <p style={{ margin: 0 }}>{t("mailtrap.created_text")}</p>
      <dl className="creds">
        <dt>{t("mailtrap.server")}</dt><dd>{host}</dd><span />
        <dt>{t("mailtrap.username")}</dt><dd>{box.login}</dd><CopyBtn text={box.login} />
        <dt>{t("mailtrap.password")}</dt><dd><code className="pw">{box.password}</code></dd><CopyBtn text={box.password} />
      </dl>
      <span className="hint">{t("mailtrap.created_hint")}</span>
      <div><button className="btn primary" type="button" onClick={onClose}>{t("common.done", { defaultValue: "OK" })}</button></div>
    </Modal>
  );
}
