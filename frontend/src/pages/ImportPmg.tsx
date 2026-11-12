import { useMutation } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, FileUp, Info, Trash2, Upload } from "lucide-react";
import { useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, ErrorText, PageHead, useToast } from "../components/ui";
import { ApiError, api } from "../lib/api";

type Problem = { field: string; code: string; en: string; nl: string };
type Item = {
  section: Section; key: string; label: string; detail: string;
  status: "new" | "exists" | "unsupported"; selected: boolean;
  note_en: string; note_nl: string; problems: Problem[];
};
type Rule = {
  name: string; priority: number; active: boolean; direction: string;
  from_groups: string[]; to_groups: string[]; what_groups: string[]; when_groups: string[]; actions: string[];
  is_default: boolean; handled_by: string; handled_by_nl: string;
};
type View = {
  id: string;
  source: { product: string; version: string; backup_time: string };
  summary: Record<string, number>;
  items: Item[];
  problems: Problem[];
  rules: Rule[];
  warnings: { code: string; message: string; message_nl: string }[];
  settings: Record<string, unknown>;
};
type Result = { ok: boolean; imported?: Record<string, number>; problems?: Problem[]; error?: string };

const SECTIONS = ["domains", "relay_networks", "address_routes", "tls_policies", "lists", "dkim"] as const;
type Section = (typeof SECTIONS)[number];
type Tab = Section | "rules" | "settings";

async function uploadBackup(file: File): Promise<View> {
  const res = await fetch("/api/pmg-import", {
    method: "POST",
    credentials: "same-origin",
    headers: { "X-Requested-With": "mailfilter", "Content-Type": "application/octet-stream" },
    body: file,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new ApiError(res.status, typeof data?.detail === "string" ? data.detail : "error");
  return data as View;
}

export default function ImportPmg() {
  const { t, i18n } = useTranslation();
  const nl = i18n.language === "nl";
  const toast = useToast();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [drag, setDrag] = useState(false);
  const [view, setView] = useState<View | null>(null);
  const [chosen, setChosen] = useState<Set<string>>(new Set());
  const [tab, setTab] = useState<Tab>("domains");
  const [result, setResult] = useState<Result | null>(null);

  const id = (i: Item) => `${i.section}\u0000${i.key}`;
  const upload = useMutation({
    mutationFn: () => uploadBackup(file!),
    onSuccess: (v) => {
      setView(v);
      setResult(null);
      setChosen(new Set(v.items.filter((i) => i.selected).map(id)));
      setTab((SECTIONS.find((s) => v.items.some((i) => i.section === s)) as Tab) ?? "rules");
    },
  });
  const apply = useMutation({
    mutationFn: () => {
      const selection: Record<string, string[]> = {};
      for (const it of view!.items) if (chosen.has(id(it))) (selection[it.section] ??= []).push(it.key);
      return api.post<Result>(`/pmg-import/${view!.id}/apply`, { selection });
    },
    onSuccess: (r) => {
      setResult(r);
      if (r.ok) toast(t("pmg.done_toast"));
    },
  });
  const discard = useMutation({
    mutationFn: () => api.del(`/pmg-import/${view!.id}`),
    onSuccess: () => {
      setView(null);
      setFile(null);
      setResult(null);
    },
  });

  const bySection = useMemo(() => {
    const m: Record<string, Item[]> = {};
    for (const it of view?.items ?? []) (m[it.section] ??= []).push(it);
    return m;
  }, [view]);
  const count = chosen.size;
  const note = (i: Item) => (nl ? i.note_nl : i.note_en);
  const msg = (p: Problem) => (nl ? p.nl : p.en);
  // Lists: key is "list:scope:kind:value"; show it in words instead of codes
  const detail = (it: Item) => {
    if (it.section === "dkim") return <span className="mono">{it.detail}</span>;
    if (it.section !== "lists") return <span className="mono">{it.detail}</span>;
    const [list, scope, ...rest] = it.key.split(":");
    const kind = scope === "user" ? rest[1] : rest[0];
    const owner = scope === "user" ? rest[0] : null;
    return (
      <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        <span className={`status ${list === "block" ? "s-bad" : "s-ok"}`}>{t(`pmg.list.${list}`)}</span>
        <span className="tag">{t(`pmg.kind.${kind}`, { defaultValue: kind })}</span>
        {scope === "smtp" && <span className="tag">{t("pmg.scope_smtp")}</span>}
        {owner && <span className="tag">{t("pmg.scope_user", { user: owner })}</span>}
      </span>
    );
  };

  const toggle = (it: Item) => {
    const n = new Set(chosen);
    n.has(id(it)) ? n.delete(id(it)) : n.add(id(it));
    setChosen(n);
  };
  const setAll = (section: Section, on: boolean) => {
    const n = new Set(chosen);
    for (const it of bySection[section] ?? []) {
      if (it.status === "unsupported") continue;
      if (on && it.status === "new") n.add(id(it));
      if (!on) n.delete(id(it));
    }
    setChosen(n);
  };

  return (
    <>
      <PageHead title={t("pmg.title")} sub={t("pmg.sub")} />

      {!view && (
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(min(100%,340px),1fr))", alignItems: "start" }}>
          <Card title={t("pmg.step1")}>
            <ol style={{ margin: 0, paddingLeft: 20, display: "flex", flexDirection: "column", gap: 6 }}>
              <li>{t("pmg.how1")}</li>
              <li>{t("pmg.how2")}</li>
              <li>{t("pmg.how3")}</li>
            </ol>
            <p className="muted" style={{ marginTop: 12, marginBottom: 0, fontSize: 13 }}>
              {t("pmg.how_cli")} <code>pmgbackup backup</code>
            </p>
          </Card>
          <Card title={t("pmg.step2")}>
            <div
              onDragOver={(e) => {
                e.preventDefault();
                setDrag(true);
              }}
              onDragLeave={() => setDrag(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDrag(false);
                if (e.dataTransfer.files[0]) setFile(e.dataTransfer.files[0]);
              }}
              onClick={() => input.current?.click()}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => (e.key === "Enter" || e.key === " ") && input.current?.click()}
              style={{
                border: `2px dashed ${drag ? "var(--accent)" : "var(--line)"}`, borderRadius: "var(--r-md)", padding: "28px 16px",
                textAlign: "center", cursor: "pointer", background: drag ? "var(--surface-sunk)" : "transparent",
              }}
            >
              <FileUp style={{ width: 28, height: 28, stroke: "var(--ink-muted)" }} />
              <div className="cell-main" style={{ marginTop: 8 }}>{file ? file.name : t("pmg.drop")}</div>
              <div className="cell-sub">{file ? `${(file.size / 1024 / 1024).toFixed(1)} MB` : "pmg-backup_*.tgz"}</div>
              <input ref={input} type="file" accept=".tgz,.tar.gz,.tar,application/gzip" hidden onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
            </div>
            <div style={{ marginTop: 14, display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button className="btn primary" disabled={!file || upload.isPending} onClick={() => upload.mutate()}>
                <Upload /> {upload.isPending ? t("pmg.reading") : t("pmg.read")}
              </button>
              <span className="muted" style={{ fontSize: 12.5 }}>{t("pmg.nothing_changes")}</span>
            </div>
            <div style={{ marginTop: 12 }}><ErrorText error={upload.error} /></div>
          </Card>
        </div>
      )}

      {view && (
        <>
          <div className="callout" style={{ marginBottom: 16 }}>
            <Info />
            <span>
              {t("pmg.found", { product: view.source.product || "PMG", version: view.source.version || "?", time: view.source.backup_time || "?" })}
            </span>
          </div>

          <div className="grid" style={{ marginBottom: 16, gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))" }}>
            {(["domains", "relay_networks", "welcomelist", "blocklist", "dkim_domains", "rules_to_review"] as const).map((k) => (
              <div key={k} className="card kpi">
                <span className="label">{t(`pmg.kpi.${k}`)}</span>
                <span className="value">{view.summary[k] ?? 0}</span>
              </div>
            ))}
          </div>

          {view.warnings.length > 0 && (
            <div className="callout warn" style={{ marginBottom: 16 }}>
              <AlertTriangle />
              <div>
                <b>{t("pmg.warnings")}</b>
                <ul style={{ margin: "6px 0 0", paddingLeft: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 4 }}>
                  {view.warnings.map((w, i) => <li key={i}>{nl ? w.message_nl : w.message}</li>)}
                </ul>
              </div>
            </div>
          )}
          {view.problems.length > 0 && (
            <div className="callout warn" style={{ marginBottom: 16 }}>
              <AlertTriangle />
              <span>{view.problems.map(msg).join(" ")}</span>
            </div>
          )}

          <Card flush>
            <div className="tabs" role="tablist">
              {[...SECTIONS, "rules" as const, "settings" as const].map((s) => {
                const n = s === "rules" ? view.rules.length : s === "settings" ? null : (bySection[s]?.length ?? 0);
                if (n === 0) return null;
                const sel = s === "rules" || s === "settings" ? null : (bySection[s] ?? []).filter((i) => chosen.has(id(i))).length;
                return (
                  <button key={s} role="tab" aria-selected={tab === s} onClick={() => setTab(s)}>
                    {t(`pmg.tab.${s}`)} {sel !== null ? <span className="tag" style={{ marginLeft: 4 }}>{sel}/{n}</span> : n ? <span className="tag" style={{ marginLeft: 4 }}>{n}</span> : null}
                  </button>
                );
              })}
            </div>

            {SECTIONS.includes(tab as Section) && (
              <>
                <div className="filters">
                  <span className="muted" style={{ fontSize: 13, flex: 1, minWidth: 200 }}>{t(`pmg.help.${tab}`)}</span>
                  <button className="btn small" onClick={() => setAll(tab as Section, true)}>{t("pmg.all_new")}</button>
                  <button className="btn small ghost" onClick={() => setAll(tab as Section, false)}>{t("pmg.none")}</button>
                </div>
                <div className="tbl-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th style={{ width: 36 }} aria-label={t("pmg.import")} />
                        <th>{t(`pmg.col.${tab}`)}</th>
                        <th>{t("pmg.col.detail")}</th>
                        <th>{t("pmg.col.status")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {(bySection[tab] ?? []).map((it) => (
                        <tr key={it.key} className={it.status === "unsupported" ? "" : "click"} onClick={() => it.status !== "unsupported" && toggle(it)}>
                          <td>
                            <input
                              type="checkbox"
                              aria-label={it.label}
                              checked={chosen.has(id(it))}
                              disabled={it.status === "unsupported"}
                              onChange={() => toggle(it)}
                              onClick={(e) => e.stopPropagation()}
                              style={{ accentColor: "var(--koraal)" }}
                            />
                          </td>
                          <td style={{ minWidth: 180 }}>
                            <div className="cell-main" style={{ wordBreak: "break-word" }}>{it.label}</div>
                            {note(it) && <span className="reason">{note(it)}</span>}
                            {it.problems.map((p, i) => (
                              <span key={i} className="reason" style={{ color: "var(--bad)" }}>{msg(p)}</span>
                            ))}
                          </td>
                          <td style={{ fontSize: 12.5, wordBreak: "break-word" }}>{detail(it)}</td>
                          <td className="nowrap">
                            <span className={`status ${it.problems.length ? "s-bad" : it.status === "new" ? "s-ok" : it.status === "exists" ? "s-info" : "s-mute"}`}>
                              {it.problems.length ? t("pmg.status.problem") : t(`pmg.status.${it.status}`)}
                            </span>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {tab === "rules" && (
              <>
                <div className="filters"><span className="muted" style={{ fontSize: 13 }}>{t("pmg.help.rules")}</span></div>
                <div className="tbl-wrap">
                  <table>
                    <thead>
                      <tr>
                        <th>{t("pmg.col.rule")}</th>
                        <th>{t("pmg.col.conditions")}</th>
                        <th>{t("pmg.col.mailfilter")}</th>
                      </tr>
                    </thead>
                    <tbody>
                      {view.rules.map((r, i) => (
                        <tr key={i}>
                          <td>
                            <div className="cell-main">{r.name}</div>
                            <div className="cell-sub">
                              {t("pmg.priority")} {r.priority} · {t(`pmg.dir.${r.direction}`)} · {r.active ? t("pmg.active") : t("pmg.inactive")}
                            </div>
                          </td>
                          <td style={{ fontSize: 12.5 }}>
                            {(["from", "to", "what", "when"] as const).map((g) => {
                              const v = r[`${g}_groups`];
                              return v.length ? <div key={g}><b>{t(`pmg.cond.${g}`)}:</b> {v.join("; ")}</div> : null;
                            })}
                            {r.actions.length > 0 && <div><b>{t("pmg.cond.action")}:</b> {r.actions.join(", ")}</div>}
                          </td>
                          <td>
                            {r.handled_by ? (
                              <span className="status s-ok" style={{ whiteSpace: "normal" }}><CheckCircle2 />{nl ? r.handled_by_nl : r.handled_by}</span>
                            ) : (
                              <span className="status s-warn"><AlertTriangle />{t("pmg.manual")}</span>
                            )}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </>
            )}

            {tab === "settings" && (
              <div className="card-body">
                <p className="muted" style={{ marginTop: 0, fontSize: 13 }}>{t("pmg.help.settings")}</p>
                <dl className="kv">
                  {Object.entries(view.settings).map(([k, v]) => (
                    <div key={k} style={{ display: "contents" }}>
                      <dt className="mono" style={{ fontSize: 12 }}>{k}</dt>
                      <dd className="mono" style={{ fontSize: 12 }}>{Array.isArray(v) ? v.join(", ") : String(v)}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            )}
          </Card>

          {result && (
            <div className={`callout${result.ok ? "" : " warn"}`} style={{ marginTop: 16 }} role="status">
              {result.ok ? <CheckCircle2 /> : <AlertTriangle />}
              <div>
                <b>{result.ok ? t("pmg.done") : t("pmg.failed")}</b>
                {result.ok && result.imported && (
                  <div style={{ marginTop: 4 }}>
                    {Object.entries(result.imported).filter(([, n]) => n > 0).map(([s, n]) => `${t(`pmg.tab.${s}`)}: ${n}`).join(" · ") || t("pmg.nothing_new")}
                  </div>
                )}
                {result.problems?.map((p, i) => <div key={i}>{msg(p)}</div>)}
                {result.error && <pre className="log" style={{ marginTop: 6, whiteSpace: "pre-wrap" }}>{result.error}</pre>}
              </div>
            </div>
          )}
          <div style={{ marginTop: 12 }}><ErrorText error={apply.error || discard.error} /></div>

          <div
            style={{
              position: "sticky", bottom: 0, marginTop: 16, padding: "12px 0", display: "flex", gap: 8, alignItems: "center",
              flexWrap: "wrap", background: "var(--surface)", borderTop: "1px solid var(--line)",
            }}
          >
            <button
              className="btn primary"
              disabled={count === 0 || apply.isPending}
              onClick={() => window.confirm(t("pmg.confirm", { count })) && apply.mutate()}
            >
              <CheckCircle2 /> {apply.isPending ? t("pmg.applying") : t("pmg.apply", { count })}
            </button>
            <button className="btn ghost" onClick={() => discard.mutate()} disabled={discard.isPending}>
              <Trash2 /> {t("pmg.discard")}
            </button>
            <span className="muted" style={{ fontSize: 12.5 }}>{t("pmg.safe")}</span>
          </div>
        </>
      )}
    </>
  );
}
