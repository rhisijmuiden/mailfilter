import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Bug, Check, MailCheck, Paperclip, RefreshCw } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router-dom";
import { DataTable, type Column } from "../components/DataTable";
import { Card, Drawer, ErrorText, Loading, PageHead, formatDate, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import type { Message } from "../lib/trace";
import { formatSize } from "../lib/trace";
import type { Me } from "../lib/auth";

type Item = Message & { virus: boolean; reasons: string[] };
type Preview = { headers: [string, string][]; text: string; truncated: boolean; attachments: { filename: string; content_type: string; size: number }[] };

export default function Quarantine({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const [params, setParams] = useSearchParams();
  const [q, setQ] = useState("");
  const open = params.get("id");
  const list = useQuery({
    queryKey: ["quarantine", q],
    queryFn: () => api.get<{ items: Item[]; total: number; queue_available: boolean }>(`/quarantine${qs({ q })}`),
    refetchInterval: 30_000,
  });
  const setOpen = (id: string | null) => {
    const p = new URLSearchParams(params);
    if (id) p.set("id", id);
    else p.delete("id");
    setParams(p);
  };
  const current = list.data?.items.find((i) => i.queue_id === open);
  const reasons = (m: Item) => m.reasons.map((r) => t(`symbols.${r}`, { defaultValue: r })).join(" · ");
  const columns: Column<Item>[] = [
    { key: "time", label: t("trace.time"), sort: (m) => m.first_seen, nowrap: true, mobile: "aside",
      render: (m) => <span className="num">{formatDate(m.first_seen, i18n.language)}</span> },
    { key: "from", label: t("quarantine.col_from"), sort: (m) => m.sender ?? "", text: (m) => m.sender ?? "", mobile: "title",
      render: (m) => <div className="cell-main" style={{ wordBreak: "break-all" }}>{m.sender || t("trace.empty_sender")}</div> },
    { key: "to", label: t("quarantine.col_to"), sort: (m) => m.recipients[0]?.address ?? "", text: (m) => m.recipients.map((r) => r.address).join(" "),
      render: (m) => <div className="cell-sub" style={{ wordBreak: "break-all" }}>→ {m.recipients.map((r) => r.address).join(", ")}</div> },
    { key: "why", label: t("quarantine.why"), sort: (m) => (m.virus ? 1000 : m.spam_score ?? 0), text: reasons,
      render: (m) => (
        <>
          {m.virus ? (
            <span className="status s-bad"><Bug aria-hidden />{t("quarantine.virus")}</span>
          ) : (
            <span className="status s-info">{t("quarantine.score", { score: m.spam_score?.toFixed(1) ?? "?" })}</span>
          )}
          <span className="reason">{reasons(m)}</span>
        </>
      ) },
    { key: "client", label: t("quarantine.col_client"), optional: true, sort: (m) => m.client_ip ?? "", text: (m) => `${m.client_host ?? ""} ${m.client_ip ?? ""}`,
      render: (m) => <span className="mono" style={{ wordBreak: "break-all" }}>{m.client_host || m.client_ip || "–"}</span> },
    { key: "size", label: t("trace.size"), optional: true, sort: (m) => m.size ?? 0, align: "right", nowrap: true,
      render: (m) => <span className="num">{m.size != null ? formatSize(m.size) : "–"}</span> },
    { key: "open", label: "", always: true, align: "right", nowrap: true, mobile: "hide",
      render: () => <span className="btn small">{t("quarantine.view")}</span> },
  ];

  return (
    <>
      <PageHead
        title={t("quarantine.title")}
        sub={t("quarantine.sub")}
        actions={
          <>
            <Link className="btn" to="/quarantine/report">
              <MailCheck /> {t("quarantine.report_settings")}
            </Link>
            <button className="btn" onClick={() => list.refetch()}>
              <RefreshCw /> {t("common.refresh")}
            </button>
          </>
        }
      />
      {list.data && !list.data.queue_available && (
        <div className="callout warn" role="alert">
          <span>{t("quarantine.queue_unavailable")}</span>
        </div>
      )}
      <Card flush>
        <DataTable
          id="quarantine"
          rows={list.data?.items}
          rowKey={(m) => m.queue_id}
          loading={list.isLoading}
          columns={columns}
          defaultSort={{ key: "time", dir: -1 }}
          search={{ value: q, onChange: setQ, placeholder: t("quarantine.search") }}
          tools={list.data && <span className="muted">{t("quarantine.count", { count: list.data.total })}</span>}
          empty={q ? t("trace.no_results") : t("quarantine.empty")}
          onRowClick={(m) => setOpen(m.queue_id)}
          cardActions={(m) => <QuickActions item={m} />}
        />
      </Card>
      {open && <QuarantineDrawer id={open} item={current} me={me} onClose={() => setOpen(null)} />}
    </>
  );
}

function QuarantineDrawer({ id, item, me, onClose }: { id: string; item?: Item; me: Me; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [teach, setTeach] = useState(true);
  const preview = useQuery({ queryKey: ["quarantine-preview", id], queryFn: () => api.get<Preview>(`/quarantine/${encodeURIComponent(id)}/preview`), retry: false });
  const decide = useMutation({
    mutationFn: (action: "release" | "delete") => api.post<{ action: string; learned: string | null }>(`/quarantine/${encodeURIComponent(id)}/${action}`, { learn: teach }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["quarantine"] });
      qc.invalidateQueries({ queryKey: ["trace-stats"] });
      toast(t(res.action === "released" ? "quarantine.released" : "quarantine.deleted") + (res.learned ? ` ${t("quarantine.learned")}` : ""));
      onClose();
    },
  });
  const rcpt = item?.recipients[0]?.address;
  const listIt = useMutation({
    mutationFn: (list: "allow" | "block") => api.post<{ list: string; sender: string; recipient: string }>("/lists", { list, recipient: rcpt, sender: item?.sender }),
    onSuccess: (e) => toast(t(e.list === "allow" ? "quarantine.listed_allow" : "quarantine.listed_block", { sender: e.sender, recipient: e.recipient })),
  });
  const virus = item?.virus ?? false;
  const canRelease = !virus || me.role === "admin";
  const subject = preview.data?.headers.find(([h]) => h === "Subject")?.[1];

  const footer = (
    <>
      {/* For a virus the safe choice (block) is the prominent one. */}
      {virus && (
        <button className="btn primary" disabled={decide.isPending} onClick={() => decide.mutate("delete")}>
          <Ban /> {t("quarantine.block")}
        </button>
      )}
      {canRelease && (
        <button className={`btn${virus ? " danger" : " primary"}`} disabled={decide.isPending} onClick={() => decide.mutate("release")}>
          <Check /> {t("quarantine.release")}
        </button>
      )}
      {!virus && (
        <button className="btn" disabled={decide.isPending} onClick={() => decide.mutate("delete")}>
          <Ban /> {t("quarantine.block")}
        </button>
      )}
      <Link className="btn ghost" to={`/trace?id=${encodeURIComponent(id)}`}>{t("quarantine.open_trace")}</Link>
    </>
  );

  return (
    <Drawer footer={footer} title={subject || item?.sender || t("quarantine.title")} sub={item && <>{item.sender} → {item.recipients.map((r) => r.address).join(", ")}</>} onClose={onClose}>
      {virus && (
        <div className="callout warn" role="alert">
          <Bug aria-hidden />
          <span>{me.role === "admin" ? t("quarantine.virus_warning_admin") : t("quarantine.virus_warning")}</span>
        </div>
      )}
      <ErrorText error={decide.error} />
      {item?.sender && rcpt && (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <span className="muted" style={{ fontSize: 12.5 }}>{t("quarantine.sender_list", { recipient: rcpt })}</span>
          <button className="btn small" disabled={listIt.isPending} onClick={() => listIt.mutate("allow")}><Check /> {t("quarantine.always_allow")}</button>
          <button className="btn small" disabled={listIt.isPending} onClick={() => listIt.mutate("block")}><Ban /> {t("quarantine.always_block")}</button>
          <Link className="btn small ghost" to="/lists">{t("quarantine.manage_lists")}</Link>
        </div>
      )}
      <ErrorText error={listIt.error} />
      <label className="check">
        <input type="checkbox" checked={teach} onChange={(e) => setTeach(e.target.checked)} />
        <span>
          <b style={{ display: "block" }}>{t("quarantine.teach")}</b>
          <span className="muted" style={{ fontSize: 12.5 }}>{t("quarantine.teach_hint")}</span>
        </span>
      </label>
      {preview.isLoading ? (
        <Loading />
      ) : preview.error ? (
        <ErrorText error={preview.error} />
      ) : preview.data ? (
        <>
          <dl className="kv">
            {preview.data.headers.map(([h, v]) => (
              <div key={h} style={{ display: "contents" }}>
                <dt>{h}</dt>
                <dd style={{ wordBreak: "break-word" }}>{v}</dd>
              </div>
            ))}
          </dl>
          {preview.data.attachments.length > 0 && (
            <div>
              <h3 style={{ margin: "0 0 8px", fontSize: 14 }}>{t("quarantine.attachments")}</h3>
              {preview.data.attachments.map((a, i) => (
                <div key={i} className="list-row">
                  <Paperclip size={16} aria-hidden />
                  <span className="grow mono" style={{ wordBreak: "break-all" }}>{a.filename || a.content_type}</span>
                  <span className="muted num">{formatSize(a.size)}</span>
                </div>
              ))}
            </div>
          )}
          <div>
            <h3 style={{ margin: "0 0 8px", fontSize: 14 }}>{t("quarantine.content")}</h3>
            <p className="muted" style={{ margin: "0 0 8px", fontSize: 12.5 }}>{t("quarantine.content_hint")}</p>
            <pre className="log" style={{ whiteSpace: "pre-wrap", wordBreak: "break-word", maxHeight: 420, overflowY: "auto" }}>
              {preview.data.text || t("quarantine.no_text")}
              {preview.data.truncated && "\n…"}
            </pre>
          </div>
        </>
      ) : null}
    </Drawer>
  );
}

/** Release / Block straight from a card on the phone, without opening the message. */
function QuickActions({ item }: { item: Item }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const decide = useMutation({
    mutationFn: (action: "release" | "delete") =>
      api.post<{ action: string; learned: string | null }>(`/quarantine/${encodeURIComponent(item.queue_id)}/${action}`, { learn: true }),
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["quarantine"] });
      qc.invalidateQueries({ queryKey: ["trace-stats"] });
      toast(t(res.action === "released" ? "quarantine.released" : "quarantine.deleted") + (res.learned ? ` ${t("quarantine.learned")}` : ""));
    },
    onError: () => toast(t("errors.error"), "error"),
  });
  if (item.virus) {
    return (
      <button type="button" className="btn small primary" disabled={decide.isPending} onClick={() => decide.mutate("delete")}>
        <Ban aria-hidden /> {t("quarantine.block")}
      </button>
    );
  }
  return (
    <>
      <button type="button" className="btn small primary" disabled={decide.isPending} onClick={() => decide.mutate("release")}>
        <Check aria-hidden /> {t("quarantine.release")}
      </button>
      <button type="button" className="btn small" disabled={decide.isPending} onClick={() => decide.mutate("delete")}>
        <Ban aria-hidden /> {t("quarantine.block")}
      </button>
    </>
  );
}
