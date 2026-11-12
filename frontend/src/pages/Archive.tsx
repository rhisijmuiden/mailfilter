// Mail archive: an unchangeable copy of all mail per domain. Search it, read a
// message (with its fingerprint and day seal), download or export, and (admins)
// switch archiving, retention and legal hold per domain.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Archive as ArchiveIcon, ArrowDownLeft, ArrowUpRight, BadgeCheck, Download, Gavel, Paperclip, Route, ShieldAlert, ShieldCheck,
} from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { DataTable, type Column } from "../components/DataTable";
import GatewayError from "../components/GatewayError";
import { Drawer, ErrorText, Field, Loading, PageHead, SkeletonTiles, formatDate, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import type { Me } from "../lib/auth";
import { formatSize } from "../lib/trace";
import { CopyBtn } from "./Dns";

type Problem = { domain: string; code: string; seq: number | null; detail: string };
type VerifyState = { ok: boolean; started: string; finished: string; messages: number | null; files: number | null;
  seals: number | null; problems: Problem[]; running?: boolean; files_checked?: boolean };
type ArchiveDomain = {
  name: string; enabled: boolean; configured: boolean; retention_years: number; legal_hold: boolean;
  legal_hold_since: string | null; legal_hold_note: string; messages: number; bytes: number; oldest: string | null;
};
type Status = {
  can_manage: boolean; messages: number; bytes: number; today: number;
  last_seal: { day: string; at: string | null; days: number } | null;
  verify: VerifyState | null; verify_running: boolean; key_id: string; public_key: string; domains: ArchiveDomain[];
};
type Item = {
  id: number; domain: string; seq: number; received_at: string; direction: "in" | "out"; sender: string; from_addr: string;
  recipients: string[]; subject: string; attachments: { name: string; type: string; size: number }[]; size: number;
  sealed: boolean; retain_until: string;
};
type Detail = Item & {
  queue_id: string; message_id: string; header_to: string[]; envelope_to: string[]; sha256: string; entry_hash: string;
  prev_hash: string; intact: boolean; problem: string | null; seal: { day: string; seal_hash: string; key_id: string } | null;
  legal_hold: boolean; text: string;
};
type Filters = { q: string; domain: string; direction: "" | "in" | "out"; sender: string; recipient: string;
  date_from: string; date_to: string; attachments: boolean };

const EMPTY: Filters = { q: "", domain: "", direction: "", sender: "", recipient: "", date_from: "", date_to: "", attachments: false };
const PAGE = 50;

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

function DirectionIcon({ d }: { d: "in" | "out" }) {
  const { t } = useTranslation();
  return d === "in"
    ? <ArrowDownLeft aria-label={t("archive.in")} style={{ width: 16, height: 16, color: "var(--info)" }} />
    : <ArrowUpRight aria-label={t("archive.out")} style={{ width: 16, height: 16, color: "var(--accent-text)" }} />;
}

export default function Archive({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const admin = me.role === "admin";
  const status = useQuery({ queryKey: ["archive"], queryFn: () => api.get<Status>("/archive/status"),
    refetchInterval: (q) => (q.state.data?.verify_running ? 2000 : 60_000) });
  const [tab, setTab] = useState<"search" | "domains">("search");
  const qc = useQueryClient();
  const toast = useToast();
  const verify = useMutation({
    mutationFn: () => api.post<{ started: boolean }>("/archive/verify", { files: true }),
    onSuccess: () => { toast(t("archive.verify_started"), "info"); qc.invalidateQueries({ queryKey: ["archive"] }); },
  });

  if (status.isLoading) return <><PageHead title={t("archive.title")} /><SkeletonTiles /></>;
  if (status.error) return <ErrorText error={status.error} />;
  const s = status.data!;
  const v = s.verify;
  const on = s.domains.filter((d) => d.enabled);
  const running = s.verify_running || !!v?.running;

  return (
    <>
      <PageHead
        title={t("archive.title")}
        sub={t(admin ? "archive.sub" : "archive.sub_customer")}
        actions={admin && (
          <button className="btn" type="button" disabled={running || verify.isPending} onClick={() => verify.mutate()}>
            <ShieldCheck /> {running ? t("archive.verifying") : t("archive.verify")}
          </button>
        )}
      />
      <ErrorText error={verify.error} />
      {v && !v.ok && !running && (
        <div className="callout bad" role="alert"><ShieldAlert aria-hidden />
          <div>
            <b>{t("archive.problems_title", { count: v.problems.length })}</b>
            <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
              {v.problems.slice(0, 8).map((p, i) => (
                <li key={i}>{p.domain}{p.seq != null ? ` #${p.seq}` : ""}: {t(`archive.problem.${p.code}`, { defaultValue: p.code })}</li>
              ))}
            </ul>
          </div>
        </div>
      )}

      <div className="grid kpis">
        <div className="card kpi">
          <span className="label">{t("archive.kpi_messages")}</span>
          <span className="value">{s.messages.toLocaleString(i18n.language)}</span>
          <span className="delta"><b>+{s.today.toLocaleString(i18n.language)}</b> {t("archive.today")}</span>
        </div>
        <div className="card kpi">
          <span className="label">{t("archive.kpi_storage")}</span>
          <span className="value">{formatSize(s.bytes)}</span>
          <span className="delta">{t("archive.compressed")}</span>
        </div>
        <div className="card kpi">
          <span className="label">{t("archive.kpi_integrity")}</span>
          <span className="value" style={{ color: running ? "var(--info)" : !v ? "var(--ink-muted)" : v.ok ? "var(--ok)" : "var(--bad)" }}>
            {running ? t("archive.checking") : !v ? "—" : v.ok ? t("archive.intact") : t("archive.problems_short")}
          </span>
          <span className="delta">
            {v?.finished ? t("archive.checked_at", { when: formatDate(v.finished, i18n.language) }) : t("archive.not_checked")}
            {s.last_seal && <> · {t("archive.seals", { count: s.last_seal.days })}</>}
          </span>
        </div>
        <div className="card kpi">
          <span className="label">{t("archive.kpi_domains")}</span>
          <span className="value">{on.length} / {s.domains.length}</span>
          <span className="delta">{t("archive.with_hold", { count: s.domains.filter((d) => d.legal_hold).length })}</span>
        </div>
      </div>

      <section className="card">
        <div className="tabs" role="tablist">
          <button type="button" role="tab" aria-selected={tab === "search"} onClick={() => setTab("search")}>{t("archive.tab_search")}</button>
          <button type="button" role="tab" aria-selected={tab === "domains"} onClick={() => setTab("domains")}>{t("archive.tab_domains")}</button>
        </div>
        {tab === "search"
          ? (on.length === 0 && s.messages === 0
            ? <EmptyArchive admin={admin} onSetup={() => setTab("domains")} />
            : <SearchPane domains={s.domains.filter((d) => d.configured)} />)
          : <DomainsPane status={s} />}
      </section>
    </>
  );
}

function EmptyArchive({ admin, onSetup }: { admin: boolean; onSetup: () => void }) {
  const { t } = useTranslation();
  return (
    <div className="empty">
      <ArchiveIcon aria-hidden style={{ width: 32, height: 32 }} />
      <p style={{ maxWidth: "56ch", margin: "8px auto" }}>{t(admin ? "archive.empty_admin" : "archive.empty")}</p>
      {admin && <button className="btn primary" type="button" onClick={onSetup}>{t("archive.setup")}</button>}
    </div>
  );
}

// ---- search -------------------------------------------------------------------------------
function SearchPane({ domains }: { domains: ArchiveDomain[] }) {
  const { t, i18n } = useTranslation();
  const [f, setF] = useState<Filters>(EMPTY);
  const [more, setMore] = useState(false);
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<number | null>(null);
  const df = useDebounced(f);
  useEffect(() => setPage(1), [df]);
  const params = { ...df, attachments: df.attachments || undefined };
  const res = useQuery({
    queryKey: ["archive-search", params, page],
    queryFn: () => api.get<{ items: Item[]; total: number }>(`/archive/search${qs({ ...params, page, size: PAGE })}`),
    placeholderData: (prev) => prev,
  });
  const total = res.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));
  const exportHref = `/api/archive/export${qs(params)}`;

  const columns: Column<Item>[] = [
    { key: "dir", label: "", always: true, width: 28, mobile: "hide", render: (m) => <DirectionIcon d={m.direction} /> },
    { key: "who", label: t("archive.from_to"), always: true, mobile: "title",
      render: (m) => (
        <>
          <div className="cell-main" style={{ wordBreak: "break-all" }}>{m.from_addr || m.sender || "<>"}</div>
          <div className="cell-sub" style={{ wordBreak: "break-all" }}>
            → {m.recipients.slice(0, 2).join(", ")}{m.recipients.length > 2 ? ` +${m.recipients.length - 2}` : ""}
          </div>
        </>
      ) },
    { key: "subject", label: t("archive.subject"), always: true, mobile: "row",
      render: (m) => (
        <span>
          {m.subject || <span className="muted">{t("archive.no_subject")}</span>}
          {m.attachments.length > 0 && (
            <span className="tag" style={{ marginLeft: 6 }} title={m.attachments.map((a) => a.name).join(", ")}>
              <Paperclip style={{ width: 11, height: 11, verticalAlign: -1 }} /> {m.attachments.length}
            </span>
          )}
        </span>
      ) },
    { key: "domain", label: t("archive.domain"), nowrap: true, optional: domains.length < 2, mobile: "hide", render: (m) => <span className="tag">{m.domain}</span> },
    { key: "size", label: t("archive.size"), align: "right", nowrap: true, optional: true, render: (m) => <span className="num">{formatSize(m.size)}</span> },
    { key: "when", label: t("archive.received"), align: "right", nowrap: true, mobile: "aside",
      render: (m) => <span className="num">{formatDate(m.received_at, i18n.language)}</span> },
    { key: "sealed", label: "", nowrap: true, mobile: "hide",
      render: (m) => m.sealed ? <span title={t("archive.sealed")}><BadgeCheck style={{ width: 16, height: 16, color: "var(--ok)" }} /></span> : null },
  ];

  const set = <K extends keyof Filters>(k: K, val: Filters[K]) => setF({ ...f, [k]: val });
  return (
    <>
      <DataTable
        id="archive"
        rows={res.data?.items}
        rowKey={(m) => m.id}
        columns={columns}
        loading={res.isLoading}
        noFilter
        search={{ value: f.q, onChange: (q) => set("q", q), placeholder: t("archive.search_ph"), autoFocus: true }}
        onRowClick={(m) => setOpen(m.id)}
        empty={t(Object.values(df).some(Boolean) ? "archive.no_results" : "archive.no_mail")}
        tools={(
          <>
            {domains.length > 1 && (
              <select className="select inline" aria-label={t("archive.domain")} value={f.domain} onChange={(e) => set("domain", e.target.value)}>
                <option value="">{t("archive.all_domains")}</option>
                {domains.map((d) => <option key={d.name} value={d.name}>{d.name}</option>)}
              </select>
            )}
            <select className="select inline" aria-label={t("archive.direction")} value={f.direction}
              onChange={(e) => set("direction", e.target.value as Filters["direction"])}>
              <option value="">{t("archive.both")}</option>
              <option value="in">{t("archive.in")}</option>
              <option value="out">{t("archive.out")}</option>
            </select>
            <button className="btn small ghost" type="button" aria-expanded={more} onClick={() => setMore(!more)}>
              {t(more ? "archive.fewer_filters" : "archive.more_filters")}
            </button>
            <a className="btn small" href={exportHref} title={t("archive.export_hint")}><Download /> {t("archive.export", { count: total })}</a>
          </>
        )}
        footer={(
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <span className="muted">{t("archive.results", { count: total })}</span>
            {pages > 1 && (
              <span style={{ marginLeft: "auto", display: "flex", gap: 6, alignItems: "center" }}>
                <button className="btn small" type="button" disabled={page <= 1} onClick={() => setPage(page - 1)}>{t("audit.prev")}</button>
                <span className="num muted">{page} / {pages}</span>
                <button className="btn small" type="button" disabled={page >= pages} onClick={() => setPage(page + 1)}>{t("audit.next")}</button>
              </span>
            )}
          </div>
        )}
      />
      {more && (
        <div className="filters" style={{ borderTop: "1px solid var(--line)" }}>
          <input className="input inline mono" style={{ minWidth: 0, flex: "1 1 180px" }} placeholder={t("archive.sender_ph")}
            aria-label={t("archive.sender")} value={f.sender} onChange={(e) => set("sender", e.target.value)} />
          <input className="input inline mono" style={{ minWidth: 0, flex: "1 1 180px" }} placeholder={t("archive.recipient_ph")}
            aria-label={t("archive.recipient")} value={f.recipient} onChange={(e) => set("recipient", e.target.value)} />
          <label className="muted" htmlFor="a-from">{t("archive.from_date")}</label>
          <input id="a-from" className="input inline" type="date" value={f.date_from} onChange={(e) => set("date_from", e.target.value)} />
          <label className="muted" htmlFor="a-to">{t("archive.to_date")}</label>
          <input id="a-to" className="input inline" type="date" value={f.date_to} onChange={(e) => set("date_to", e.target.value)} />
          <label className="chk" htmlFor="a-att" style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
            <input id="a-att" type="checkbox" checked={f.attachments} onChange={(e) => set("attachments", e.target.checked)} />
            {t("archive.with_attachments")}
          </label>
          <button className="btn small ghost" type="button" onClick={() => setF(EMPTY)}>{t("archive.clear")}</button>
        </div>
      )}
      <p className="muted" style={{ fontSize: 12, padding: "10px 14px", margin: 0 }}>{t("archive.audit_note")}</p>
      {open != null && <MessageDrawer id={open} onClose={() => setOpen(null)} />}
    </>
  );
}

function MessageDrawer({ id, onClose }: { id: number; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const d = useQuery({ queryKey: ["archive-msg", id], queryFn: () => api.get<Detail>(`/archive/messages/${id}`) });
  const m = d.data;
  return (
    <Drawer
      title={m ? (m.subject || t("archive.no_subject")) : t("common.loading")}
      sub={m && <><span className={`tag ${m.direction === "in" ? "lucht" : ""}`}>{t(m.direction === "in" ? "archive.in" : "archive.out")}</span> <span className="tag">{m.domain}</span></>}
      onClose={onClose}
      footer={m && (
        <>
          <a className="btn primary" href={`/api/archive/messages/${m.id}/eml`}><Download /> {t("archive.download")}</a>
          {m.queue_id && <Link className="btn" to={`/trace?id=${encodeURIComponent(m.queue_id)}`}><Route /> {t("archive.trace")}</Link>}
        </>
      )}
    >
      {d.isLoading && <Loading />}
      <ErrorText error={d.error} />
      {m && (
        <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          <div className={`callout ${m.intact ? "ok" : "bad"}`}>
            {m.intact ? <BadgeCheck aria-hidden /> : <ShieldAlert aria-hidden />}
            <div>
              <b>{t(m.intact ? "archive.intact_title" : "archive.changed_title")}</b>
              <div className="muted" style={{ fontSize: 12.5 }}>
                {!m.intact ? t(`archive.problem.${m.problem}`, { defaultValue: m.problem ?? "" })
                  : m.seal ? t("archive.in_seal", { day: formatDate(m.seal.day, i18n.language, false) }) : t("archive.not_sealed_yet")}
              </div>
              <div className="mono muted" style={{ fontSize: 11.5, wordBreak: "break-all" }}>SHA-256 {m.sha256}</div>
            </div>
          </div>
          <dl className="kv">
            <dt>{t("archive.from")}</dt><dd>{m.from_addr || "—"}{m.sender && m.sender !== m.from_addr && <span className="muted"> ({t("archive.envelope")}: {m.sender})</span>}</dd>
            <dt>{t("archive.to_header")}</dt><dd>{m.header_to.join(", ") || "—"}</dd>
            <dt>{t("archive.to_envelope")}</dt><dd>{m.envelope_to.length ? m.envelope_to.join(", ") : <span className="muted">{t("archive.envelope_pending")}</span>}</dd>
            <dt>{t("archive.received")}</dt><dd>{formatDate(m.received_at, i18n.language)}</dd>
            <dt>{t("archive.kept_until")}</dt>
            <dd>{formatDate(m.retain_until, i18n.language, false)}{m.legal_hold && <> · <span className="tag s-warn"><Gavel style={{ width: 11, height: 11, verticalAlign: -1 }} /> {t("archive.hold")}</span></>}</dd>
            {m.attachments.length > 0 && (<><dt>{t("archive.attachments")}</dt><dd>{m.attachments.map((a) => `${a.name || a.type} (${formatSize(a.size)})`).join(", ")}</dd></>)}
            <dt>{t("archive.number")}</dt><dd className="num">#{m.seq}</dd>
            {m.message_id && (<><dt>Message-ID</dt><dd className="mono" style={{ fontSize: 12 }}>{m.message_id}</dd></>)}
          </dl>
          <div>
            <div className="sec-title">{t("archive.content")}</div>
            <pre style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", background: "var(--surface-sunk)", padding: 12, borderRadius: "var(--r-sm)", fontFamily: "inherit", fontSize: 13, maxHeight: 420, overflow: "auto", margin: "6px 0 0" }}>
              {m.text || t("archive.no_text")}
            </pre>
          </div>
        </div>
      )}
    </Drawer>
  );
}

// ---- domains ------------------------------------------------------------------------------
function DomainsPane({ status }: { status: Status }) {
  const { t, i18n } = useTranslation();
  const [edit, setEdit] = useState<ArchiveDomain | null>(null);
  const columns: Column<ArchiveDomain>[] = [
    { key: "name", label: t("archive.domain"), always: true, sort: (d) => d.name, mobile: "title", render: (d) => <b>{d.name}</b> },
    { key: "on", label: t("archive.archiving"), sort: (d) => (d.enabled ? 1 : 0), mobile: "aside",
      render: (d) => <span className={`status ${d.enabled ? "s-ok" : "s-mute"}`}>{t(d.enabled ? "archive.on" : "archive.off")}</span> },
    { key: "ret", label: t("archive.retention"), sort: (d) => d.retention_years, mobileLabel: true,
      render: (d) => (d.configured ? t("archive.years", { count: d.retention_years }) : <span className="muted">—</span>) },
    { key: "hold", label: t("archive.hold"), sort: (d) => (d.legal_hold ? 1 : 0), mobileLabel: true,
      render: (d) => d.legal_hold
        ? <span className="tag s-warn" title={d.legal_hold_note}>{t("archive.hold_since", { when: formatDate(d.legal_hold_since, i18n.language, false) })}</span>
        : <span className="muted">—</span> },
    { key: "n", label: t("archive.messages"), align: "right", sort: (d) => d.messages, mobileLabel: true,
      render: (d) => <span className="num">{d.messages.toLocaleString(i18n.language)}</span> },
    { key: "b", label: t("archive.storage"), align: "right", sort: (d) => d.bytes, mobileLabel: true, render: (d) => <span className="num">{formatSize(d.bytes)}</span> },
    { key: "old", label: t("archive.oldest"), align: "right", optional: true, sort: (d) => d.oldest ?? "",
      render: (d) => <span className="num">{d.oldest ? formatDate(d.oldest, i18n.language, false) : "—"}</span> },
  ];
  return (
    <>
      <DataTable id="archive-domains" rows={status.domains} rowKey={(d) => d.name} columns={columns}
        onRowClick={status.can_manage ? setEdit : undefined} defaultSort={{ key: "name", dir: 1 }}
        empty={t("archive.no_domains")} />
      <div className="card-body" style={{ display: "grid", gap: 10, borderTop: "1px solid var(--line)" }}>
        <p className="muted" style={{ margin: 0, fontSize: 13, maxWidth: "80ch" }}>{t("archive.how")}</p>
        {status.public_key && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 12.5 }}>
            <span className="muted">{t("archive.public_key", { id: status.key_id })}</span>
            <code className="mono" style={{ wordBreak: "break-all", fontSize: 11.5 }}>{status.public_key}</code>
            <CopyBtn text={status.public_key} />
          </div>
        )}
      </div>
      {edit && <DomainDrawer d={edit} onClose={() => setEdit(null)} />}
    </>
  );
}

function DomainDrawer({ d, onClose }: { d: ArchiveDomain; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [f, setF] = useState({ enabled: d.configured ? d.enabled : true, retention_years: d.retention_years,
    legal_hold: d.legal_hold, legal_hold_note: d.legal_hold_note });
  const save = useMutation({
    mutationFn: () => api.put<ArchiveDomain>(`/archive/domains/${encodeURIComponent(d.name)}`, f, { quiet: true }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["archive"] });
      toast(t(f.enabled ? "archive.saved_on" : "archive.saved_off", { domain: d.name }), "success");
      onClose();
    },
  });
  return (
    <Drawer title={d.name} sub={t("archive.domain_settings")} onClose={onClose}
      footer={<>
        <button className="btn primary" type="button" disabled={save.isPending} onClick={() => save.mutate()}>{t(save.isPending ? "common.saving" : "common.save")}</button>
        <button className="btn" type="button" onClick={onClose}>{t("common.cancel")}</button>
      </>}>
      <div style={{ display: "grid", gap: 18 }}>
        <label className="setting" htmlFor="ad-on" style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
          <span className="switch"><input id="ad-on" type="checkbox" checked={f.enabled} onChange={(e) => setF({ ...f, enabled: e.target.checked })} /><span /></span>
          <span><b>{t("archive.archive_this")}</b><br /><span className="muted" style={{ fontSize: 12.5 }}>{t("archive.archive_this_hint")}</span></span>
        </label>
        <Field label={t("archive.retention")} hint={t("archive.retention_hint")}>
          <select id="ad-ret" className="select" value={f.retention_years} onChange={(e) => setF({ ...f, retention_years: Number(e.target.value) })}>
            {Array.from({ length: 10 }, (_, i) => i + 1).map((y) => <option key={y} value={y}>{t("archive.years", { count: y })}</option>)}
          </select>
        </Field>
        <label className="setting" htmlFor="ad-hold" style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
          <span className="switch"><input id="ad-hold" type="checkbox" checked={f.legal_hold} onChange={(e) => setF({ ...f, legal_hold: e.target.checked })} /><span /></span>
          <span><b>{t("archive.hold")}</b><br /><span className="muted" style={{ fontSize: 12.5 }}>{t("archive.hold_hint")}</span></span>
        </label>
        {f.legal_hold && (
          <Field label={t("archive.hold_note")}>
            <input id="ad-note" className="input" maxLength={300} placeholder={t("archive.hold_note_ph")} value={f.legal_hold_note}
              onChange={(e) => setF({ ...f, legal_hold_note: e.target.value })} />
          </Field>
        )}
        {d.configured && !f.enabled && d.messages > 0 && (
          <div className="callout warn"><ShieldAlert aria-hidden /><span>{t("archive.off_note", { count: d.messages })}</span></div>
        )}
        <GatewayError error={save.error} />
      </div>
    </Drawer>
  );
}
