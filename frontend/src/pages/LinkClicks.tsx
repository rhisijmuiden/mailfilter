// Link protection: every click on a link in incoming mail, with what the check
// at the click found. Admins can block a domain from here (also for mail that
// was already delivered) and test a link. Customers see their own domains.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, ChevronLeft, ChevronRight, CircleCheck, ExternalLink, MousePointerClick, RefreshCw, Search, ShieldAlert, TriangleAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { DataTable, type Column } from "../components/DataTable";
import { PublicHttpsNotice } from "../components/PublicHttpsNotice";
import { Card, ErrorText, PageHead, formatDate, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import type { Me } from "../lib/auth";

type Reason = { code: string; level: Level; params: Record<string, string> };
type Level = "safe" | "warn" | "block";
type Click = {
  id: number; at: string; url: string; host: string; org: string; final_url: string; recipient: string;
  domains: string[]; message_id: string; queue_id: string; delivered_at: string | null; level: Level;
  reasons: Reason[]; continued: boolean; scanner: boolean; ip: string | null;
};
type Page = {
  items: Click[]; total: number; page: number; page_size: number;
  counts: Record<"safe" | "warn" | "block" | "continued", number>; enabled: boolean; can_block: boolean;
};
type Filter = "all" | "block" | "warn" | "continued" | "safe";
type TestResult = { url: string; level: Level; reasons: Reason[] };

const PAGE_SIZE = 50;
const FILTERS: Filter[] = ["all", "block", "warn", "continued", "safe"];
const PERIODS = [1, 7, 30, 90, 180];

function useDebounced<T>(value: T, ms = 300): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = setTimeout(() => setV(value), ms);
    return () => clearTimeout(id);
  }, [value, ms]);
  return v;
}

export function reasonText(t: (k: string, o?: Record<string, unknown>) => string, r: Reason): string {
  const p: Record<string, unknown> = { ...r.params };
  if (r.params.kind) p.kind = t(`links.kind.${r.params.kind}`, { defaultValue: r.params.kind });
  return t(`links.reason.${r.code}`, { ...p, defaultValue: r.code });
}

function Result({ c }: { c: Pick<Click, "level" | "continued"> }) {
  const { t } = useTranslation();
  if (c.level === "block") return <span className="status s-bad"><ShieldAlert aria-hidden />{t("links.level_block")}</span>;
  if (c.level === "warn")
    return c.continued ? (
      <span className="status s-bad"><TriangleAlert aria-hidden />{t("links.level_continued")}</span>
    ) : (
      <span className="status s-warn"><TriangleAlert aria-hidden />{t("links.level_warn")}</span>
    );
  return <span className="status s-ok"><CircleCheck aria-hidden />{t("links.level_safe")}</span>;
}

function TestLink() {
  const { t } = useTranslation();
  const [url, setUrl] = useState("");
  const test = useMutation({ mutationFn: () => api.post<TestResult>("/links/test", { url }) });
  return (
    <Card title={t("links.test_title")}>
      <form
        className="card-body"
        style={{ display: "flex", flexDirection: "column", gap: 12 }}
        onSubmit={(e) => {
          e.preventDefault();
          if (url.trim()) test.mutate();
        }}
      >
        <p className="muted" style={{ margin: 0 }}>{t("links.test_sub")}</p>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
          <input className="input" style={{ flex: "1 1 240px" }} value={url} onChange={(e) => setUrl(e.target.value)}
            placeholder="https://…" aria-label={t("links.test_title")} />
          <button className="btn" type="submit" disabled={test.isPending || !url.trim()}><Search /> {t("links.test_button")}</button>
        </div>
        <ErrorText error={test.error} />
        {test.data && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div><Result c={{ level: test.data.level, continued: false }} /></div>
            {test.data.reasons.length === 0 ? (
              <span className="muted">{t("links.test_clean")}</span>
            ) : (
              test.data.reasons.map((r, i) => <span key={i}>{reasonText(t, r)}</span>)
            )}
          </div>
        )}
      </form>
    </Card>
  );
}

export default function LinkClicks({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [q, setQ] = useState("");
  const dq = useDebounced(q);
  const [filter, setFilter] = useState<Filter>("all");
  const [days, setDays] = useState(30);
  const [scanners, setScanners] = useState(false);
  const [page, setPage] = useState(1);
  useEffect(() => setPage(1), [dq, filter, days, scanners]);

  const list = useQuery({
    queryKey: ["links", dq, filter, days, scanners, page],
    queryFn: () => api.get<Page>(`/links${qs({ q: dq || undefined, level: filter, days, scanners: scanners || undefined, page, page_size: PAGE_SIZE })}`),
    placeholderData: keepPreviousData,
    refetchInterval: 30_000,
  });
  const block = useMutation({
    mutationFn: (domain: string) => api.post("/links/block", { domain }),
    onSuccess: (_, domain) => {
      toast(t("links.blocked_toast", { domain }), "success");
      qc.invalidateQueries({ queryKey: ["links"] });
      qc.invalidateQueries({ queryKey: ["rules"] });
    },
    onError: (e) => toast(String((e as Error).message || e), "error"),
  });
  const data = list.data;
  const pages = data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1;

  const columns: Column<Click>[] = [
    { key: "time", label: t("links.time"), sort: (c) => c.at, nowrap: true, mobile: "aside",
      render: (c) => <span className="num">{formatDate(c.at, i18n.language)}</span> },
    { key: "recipient", label: t("links.recipient"), sort: (c) => c.recipient,
      render: (c) => (
        <>
          <div className="cell-main">{c.recipient || c.domains.join(", ") || "–"}</div>
          {c.scanner && <div className="cell-sub">{t("links.scanner")}</div>}
        </>
      ) },
    { key: "link", label: t("links.link"), always: true, sort: (c) => c.host, mobile: "title",
      render: (c) => (
        <>
          <div className="cell-main mono" style={{ wordBreak: "break-all" }}>{c.host}</div>
          <div className="cell-sub mono" style={{ wordBreak: "break-all", maxWidth: 420 }} title={c.url}>
            {c.url.length > 90 ? c.url.slice(0, 90) + "…" : c.url}
          </div>
          {c.final_url && c.final_url !== c.url && (
            <div className="cell-sub mono" style={{ wordBreak: "break-all" }}>→ {c.final_url}</div>
          )}
        </>
      ) },
    { key: "result", label: t("links.result"), sort: (c) => (c.level === "block" ? 3 : c.continued ? 2 : c.level === "warn" ? 1 : 0), nowrap: true,
      render: (c) => <Result c={c} /> },
    { key: "why", label: t("links.why"),
      render: (c) =>
        c.reasons.length === 0 ? <span className="muted">–</span> : (
          <div className="cell-sub" style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            {c.reasons.map((r, i) => <span key={i}>{reasonText(t, r)}</span>)}
          </div>
        ) },
    { key: "delivered", label: t("links.delivered"), optional: true, sort: (c) => c.delivered_at ?? "",
      render: (c) => <span className="num">{c.delivered_at ? formatDate(c.delivered_at, i18n.language) : "–"}</span> },
    { key: "ip", label: "IP", optional: true, render: (c) => <span className="mono">{c.ip ?? "–"}</span> },
    { key: "actions", label: "", align: "right", mobile: "hide",
      render: (c) => <Actions c={c} /> },
  ];

  function Actions({ c }: { c: Click }) {
    return (
      <div style={{ display: "flex", gap: 6, justifyContent: "flex-end" }}>
        {c.message_id && (
          <Link className="btn small ghost" to={`/trace${qs({ q: c.message_id, hours: 24 * 90 })}`} title={t("links.open_trace")}>
            <ExternalLink /> {t("links.trace")}
          </Link>
        )}
        {data?.can_block && c.level !== "block" && (
          <button className="btn small ghost danger" type="button" disabled={block.isPending}
            onClick={() => window.confirm(t("links.block_confirm", { domain: c.org })) && block.mutate(c.org)}>
            <Ban /> {t("links.block")}
          </button>
        )}
      </div>
    );
  }

  return (
    <>
      <PageHead
        title={t("links.title")}
        sub={t("links.sub")}
        actions={
          <button className="btn" onClick={() => list.refetch()}>
            <RefreshCw /> {t("common.refresh")}
          </button>
        }
      />
      <PublicHttpsNotice feature="links" />
      {data && !data.enabled && (
        <div className="callout warn" style={{ marginBottom: 16 }}>
          <MousePointerClick aria-hidden />
          <span>
            {t("links.off")} <Link to="/rules">{t("links.off_link")}</Link>
          </span>
        </div>
      )}
      <div className="grid kpis" style={{ marginBottom: 16 }}>
        {(["block", "warn", "continued", "safe"] as const).map((k) => (
          <button key={k} type="button" className="card kpi" style={{ textAlign: "left", cursor: "pointer", font: "inherit", color: "inherit" }}
            aria-pressed={filter === k} onClick={() => setFilter(filter === k ? "all" : k)}>
            <span className="label">{t(`links.kpi_${k}`)}</span>
            <span className="value">{data ? data.counts[k] : "–"}</span>
            <span className="delta">{t("links.kpi_period", { count: days })}</span>
          </button>
        ))}
      </div>
      <Card flush>
        <DataTable
          id="links"
          rows={data?.items}
          rowKey={(c) => c.id}
          loading={list.isLoading}
          columns={columns}
          cardActions={(c) => <Actions c={c} />}
          search={{ value: q, onChange: setQ, placeholder: t("links.search") }}
          tools={
            <>
              <div className="seg" role="group" aria-label={t("links.result")}>
                {FILTERS.map((f) => (
                  <button key={f} type="button" aria-pressed={filter === f} onClick={() => setFilter(f)}>
                    {t(`links.filter_${f}`)}
                  </button>
                ))}
              </div>
              <select className="select inline" value={days} onChange={(e) => setDays(Number(e.target.value))} aria-label={t("links.period")}>
                {PERIODS.map((p) => (
                  <option key={p} value={p}>{t("links.period_days", { count: p })}</option>
                ))}
              </select>
              <label style={{ display: "inline-flex", gap: 6, alignItems: "center", fontSize: 13 }}>
                <input type="checkbox" checked={scanners} onChange={(e) => setScanners(e.target.checked)} />
                {t("links.show_scanners")}
              </label>
            </>
          }
          empty={dq || filter !== "all" ? t("links.no_results") : t("links.empty")}
          footer={
            data && data.total > 0 && (
              <>
                <span className="muted">{t("links.count", { total: data.total, page, pages })}</span>
                <div style={{ display: "flex", gap: 6 }}>
                  <button className="btn small" disabled={page <= 1} onClick={() => setPage(page - 1)} aria-label={t("links.prev")}>
                    <ChevronLeft />
                  </button>
                  <button className="btn small" disabled={page >= pages} onClick={() => setPage(page + 1)} aria-label={t("links.next")}>
                    <ChevronRight />
                  </button>
                </div>
              </>
            )
          }
        />
      </Card>
      {me.role === "admin" && (
        <div style={{ marginTop: 16 }}>
          <TestLink />
        </div>
      )}
    </>
  );
}
