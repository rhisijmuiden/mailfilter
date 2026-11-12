// Usage per customer: the basis for invoices. Admins also manage who pays for
// which domain (billing customers, resellers) and the prices here.
import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Download, Pencil, Plus, RefreshCw, Trash2, TriangleAlert } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Sparkline, StackedColumns } from "../components/charts";
import { DataTable, type Column } from "../components/DataTable";
import { Card, Drawer, ErrorText, Field, PageHead, SkeletonTiles, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import type { Me } from "../lib/auth";

type DomainRow = {
  domain: string; customer_id: number | null; customer: string; mailboxes: number; mail_in: number; spam_virus: number;
  mail_out: number; archive_bytes: number; archive_messages: number;
};
type Line = { code: string; quantity: number; price: number; amount: number };
type CustomerRow = {
  id: number | null; name: string; ref: string; reseller_id: number | null; reseller: string; domains: DomainRow[];
  mailboxes: number; mail_in: number; mail_out: number; archive_bytes: number; lines: Line[]; amount: number | null; trend: number[];
};
type UsageData = {
  month: string; current: boolean; stored: boolean; can_recount: boolean; prices: boolean; currency: string; archive: boolean;
  customers: CustomerRow[]; domains: DomainRow[]; unassigned: string[];
};
type Prices = { per_domain?: number | null; per_mailbox?: number | null; per_1000_in?: number | null; per_1000_out?: number | null;
  per_gb_archive?: number | null; minimum?: number | null; fixed?: number | null };
type Tariffs = Required<Omit<Prices, "fixed">> & { currency: "EUR" | "USD" | "GBP" };
type BillingCustomer = { id: number; name: string; ref: string; reseller_id: number | null; domains: string[]; tariffs: Prices; note: string; logins: number };
type Reseller = { id: number; name: string; ref: string; discount_percent: number; note: string; customers: number; logins: string[] };
type Billing = { customers: BillingCustomer[]; resellers: Reseller[]; tariffs: Tariffs; domains: string[]; unassigned: string[] };

const PRICE_KEYS = ["per_domain", "per_mailbox", "per_1000_in", "per_1000_out", "per_gb_archive", "minimum"] as const;
type Tab = "customers" | "domains" | "billing" | "prices";

const ym = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
const lastMonths = (n: number) => {
  const now = new Date();
  return Array.from({ length: n }, (_, i) => ym(new Date(now.getFullYear(), now.getMonth() - i, 1)));
};

function useFormat() {
  const { i18n } = useTranslation();
  const locale = i18n.language === "nl" ? "nl-NL" : "en-GB";
  return useMemo(() => ({
    locale,
    n: (v: number, digits = 0) => v.toLocaleString(locale, { minimumFractionDigits: digits, maximumFractionDigits: digits }),
    gb: (bytes: number) => `${(bytes / 1e9).toLocaleString(locale, { minimumFractionDigits: 1, maximumFractionDigits: 1 })} GB`,
    money: (v: number, currency: string) => new Intl.NumberFormat(locale, { style: "currency", currency }).format(v),
    month: (m: string, short = false) =>
      new Date(`${m}-15T12:00:00Z`).toLocaleDateString(locale, short ? { month: "short" } : { month: "long", year: "numeric" }),
  }), [locale]);
}

export default function Usage({ me, tab: startTab }: { me: Me; tab?: "billing" }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const f = useFormat();
  const admin = me.role === "admin";
  const months = useMemo(() => lastMonths(13), []);
  const [month, setMonth] = useState(months[1]);
  const [tab, setTab] = useState<Tab>(startTab === "billing" && admin ? "billing" : "customers");
  useEffect(() => { if (startTab === "billing" && admin) setTab("billing"); }, [startTab, admin]);
  const [reseller, setReseller] = useState<string>("");
  const [open, setOpen] = useState<CustomerRow | null>(null);
  const [exporting, setExporting] = useState(false);

  const q = useQuery({ queryKey: ["usage", month], queryFn: () => api.get<UsageData>(`/usage${qs({ month })}`), placeholderData: keepPreviousData });
  const billing = useQuery({ queryKey: ["billing"], queryFn: () => api.get<Billing>("/billing"), enabled: admin });
  const recount = useMutation({
    mutationFn: () => api.post<UsageData>("/usage/recalculate", { month }),
    onSuccess: (d) => { qc.setQueryData(["usage", month], d); toast(t("usage.recounted"), "success"); },
  });

  const u = q.data;
  const cur = u?.currency ?? "EUR";
  const showArchive = !!u && (u.archive || u.domains.some((d) => d.archive_bytes > 0));
  const resellers = useMemo(() => {
    const m = new Map<string, string>();
    u?.customers.forEach((c) => c.reseller_id != null && m.set(String(c.reseller_id), c.reseller));
    return [...m.entries()];
  }, [u]);
  const rows = (u?.customers ?? []).filter((c) =>
    !reseller ? true : reseller === "direct" ? c.reseller_id == null : String(c.reseller_id) === reseller);
  const total = (k: "mailboxes" | "mail_in" | "mail_out" | "archive_bytes") => (u?.domains ?? []).reduce((s, d) => s + d[k], 0);
  const amount = rows.reduce((s, c) => s + (c.amount ?? 0), 0);

  const custCols: Column<CustomerRow>[] = [
    { key: "name", label: t("usage.customer"), always: true, mobile: "title", sort: (c) => c.name,
      text: (c) => `${c.name} ${c.ref} ${c.reseller} ${c.domains.map((d) => d.domain).join(" ")}`,
      render: (c) => (<><div className="cell-main">{c.name}</div>{c.ref && <div className="cell-sub mono">{c.ref}</div>}</>) },
    ...(admin ? [{ key: "reseller", label: t("usage.reseller"), sort: (c: CustomerRow) => c.reseller,
      render: (c: CustomerRow) => c.reseller ? <span className="tag lucht">{c.reseller}</span> : <span className="muted">{t("usage.direct")}</span> }] : []),
    { key: "domains", label: t("usage.domains"), align: "right", mobileLabel: true, sort: (c) => c.domains.length, render: (c) => <span className="num">{c.domains.length}</span> },
    { key: "mailboxes", label: t("usage.mailboxes"), align: "right", mobileLabel: true, sort: (c) => c.mailboxes, render: (c) => <span className="num">{f.n(c.mailboxes)}</span> },
    { key: "mail_in", label: t("usage.mail_in"), align: "right", mobileLabel: true, sort: (c) => c.mail_in, render: (c) => <span className="num">{f.n(c.mail_in)}</span> },
    { key: "mail_out", label: t("usage.mail_out"), align: "right", mobileLabel: true, sort: (c) => c.mail_out, render: (c) => <span className="num">{f.n(c.mail_out)}</span> },
    { key: "archive", label: t("usage.archive"), align: "right", optional: !showArchive, mobileLabel: true, sort: (c) => c.archive_bytes, render: (c) => <span className="num">{f.gb(c.archive_bytes)}</span> },
    ...(u?.prices ? [{ key: "amount", label: t("usage.amount"), align: "right" as const, mobile: "aside" as const, sort: (c: CustomerRow) => c.amount ?? 0,
      render: (c: CustomerRow) => <b className="num">{f.money(c.amount ?? 0, cur)}</b> }] : []),
    { key: "trend", label: t("usage.trend"), mobile: "hide", width: 110, render: (c) => <div style={{ width: 100 }}><Sparkline values={trimTrend(c.trend)} color="var(--chart-2)" label={t("usage.trend_label", { name: c.name })} /></div> },
  ];
  const domCols: Column<DomainRow>[] = [
    { key: "domain", label: t("usage.domain"), always: true, mobile: "title", sort: (d) => d.domain, render: (d) => <span className="mono">{d.domain}</span> },
    { key: "customer", label: t("usage.customer"), sort: (d) => d.customer, text: (d) => d.customer,
      render: (d) => d.customer || <span className="status s-warn">{t("usage.no_customer")}</span> },
    { key: "mailboxes", label: t("usage.mailboxes"), align: "right", mobileLabel: true, sort: (d) => d.mailboxes, render: (d) => <span className="num">{f.n(d.mailboxes)}</span> },
    { key: "mail_in", label: t("usage.mail_in"), align: "right", mobileLabel: true, sort: (d) => d.mail_in, render: (d) => <span className="num">{f.n(d.mail_in)}</span> },
    { key: "spam_virus", label: t("usage.spam_virus"), align: "right", optional: true, mobileLabel: true, sort: (d) => d.spam_virus, render: (d) => <span className="num">{f.n(d.spam_virus)}</span> },
    { key: "mail_out", label: t("usage.mail_out"), align: "right", mobileLabel: true, sort: (d) => d.mail_out, render: (d) => <span className="num">{f.n(d.mail_out)}</span> },
    { key: "archive", label: t("usage.archive"), align: "right", optional: !showArchive, mobileLabel: true, sort: (d) => d.archive_bytes, render: (d) => <span className="num">{f.gb(d.archive_bytes)}</span> },
  ];

  const kpis = u && [
    { label: t("usage.customers"), value: f.n(u.customers.length), sub: admin && resellers.length ? t("usage.n_resellers", { count: resellers.length }) : "" },
    { label: t("usage.domains"), value: f.n(u.domains.length), sub: u.unassigned.length ? t("usage.n_unassigned", { count: u.unassigned.length }) : "" },
    { label: t("usage.mailboxes"), value: f.n(total("mailboxes")), sub: t("usage.mailboxes_sub") },
    { label: t("usage.mail_in_out"), value: `${f.n(total("mail_in"))} / ${f.n(total("mail_out"))}`, sub: t("usage.messages") },
    ...(showArchive ? [{ label: t("usage.archive"), value: f.gb(total("archive_bytes")), sub: t("usage.archive_sub") }] : []),
    ...(u.prices ? [{ label: t("usage.to_invoice"), value: f.money(amount, cur), sub: t("usage.excl_vat") }] : []),
  ];

  return (
    <>
      <PageHead
        title={t("usage.title")}
        sub={me.role === "reseller" ? t("usage.sub_reseller", { name: me.reseller ?? "" }) : admin ? t("usage.sub") : t("usage.sub_customer")}
        actions={
          <>
            <select className="select" style={{ width: "auto" }} aria-label={t("usage.month")} value={month} onChange={(e) => setMonth(e.target.value)}>
              {months.map((m, i) => <option key={m} value={m}>{f.month(m)}{i === 0 ? ` (${t("usage.running")})` : ""}</option>)}
            </select>
            <button className="btn primary" type="button" disabled={!u} onClick={() => setExporting(true)}><Download /> {t("usage.export")}</button>
          </>
        }
      />
      <ErrorText error={q.error || recount.error} />
      {u && (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span className={`status ${u.stored ? "s-ok" : u.current ? "s-info" : "s-mute"}`}>
            {u.stored ? t("usage.state_stored") : u.current ? t("usage.state_running") : t("usage.state_open")}
          </span>
          <span className="muted" style={{ fontSize: 13 }}>
            {u.stored ? t("usage.stored_hint") : u.current ? t("usage.running_hint") : t("usage.open_hint")}
          </span>
          {u.can_recount && (
            <button className="btn small" type="button" disabled={recount.isPending} style={{ marginLeft: "auto" }}
              onClick={() => window.confirm(t("usage.recount_confirm", { month: f.month(month) })) && recount.mutate()}>
              <RefreshCw /> {t("usage.recount")}
            </button>
          )}
        </div>
      )}
      {!u ? <SkeletonTiles count={6} /> : (
        <div className={q.isPlaceholderData ? "refetching" : undefined} style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          <div className="grid kpis usage-kpis">
            {kpis!.map((k) => (
              <div key={k.label} className="card kpi">
                <span className="label">{k.label}</span>
                <span className="value num">{k.value}</span>
                <span className="delta">{k.sub}</span>
              </div>
            ))}
          </div>
          {admin && u.unassigned.length > 0 && tab !== "billing" && (
            <div className="callout warn">
              <TriangleAlert />
              <span>
                <b>{t("usage.unassigned_title", { count: u.unassigned.length })}</b> {u.unassigned.join(", ")}. {t("usage.unassigned_hint")}{" "}
                <button type="button" className="linkish" onClick={() => setTab("billing")}>{t("usage.assign")}</button>
              </span>
            </div>
          )}
        </div>
      )}

      <div className="tabs" role="tablist">
        {(["customers", "domains", ...(admin ? ["billing", "prices"] : [])] as Tab[]).map((x) => (
          <button key={x} type="button" role="tab" aria-selected={tab === x} onClick={() => setTab(x)}>{t(`usage.tab.${x}`)}</button>
        ))}
      </div>

      {tab === "customers" && (
        <DataTable id="usage-customers" rows={u ? rows : undefined} rowKey={(c) => c.id ?? c.name} columns={custCols} loading={q.isLoading}
          onRowClick={(c) => setOpen(c)} defaultSort={{ key: "name", dir: 1 }}
          empty={admin ? t("usage.empty_admin") : t("usage.empty")}
          tools={admin && resellers.length > 0 ? (
            <select className="select" style={{ width: "auto" }} aria-label={t("usage.reseller")} value={reseller} onChange={(e) => setReseller(e.target.value)}>
              <option value="">{t("usage.all_customers")}</option>
              <option value="direct">{t("usage.direct_only")}</option>
              {resellers.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
            </select>
          ) : undefined}
        />
      )}
      {tab === "domains" && (
        <DataTable id="usage-domains" rows={u?.domains} rowKey={(d) => d.domain} columns={domCols} loading={q.isLoading}
          defaultSort={{ key: "domain", dir: 1 }} empty={t("usage.empty")} />
      )}
      {tab === "billing" && admin && <BillingAdmin billing={billing.data} error={billing.error} />}
      {tab === "prices" && admin && billing.data && <PricesForm tariffs={billing.data.tariffs} />}

      {(tab === "customers" || tab === "domains") && <HowCounted />}

      {open && u && <CustomerDrawer c={open} u={u} archive={showArchive} onClose={() => setOpen(null)} />}
      {exporting && u && <ExportDrawer u={u} me={me} resellers={resellers} onClose={() => setExporting(false)} />}
    </>
  );
}

// Leave out the months before the customer had any usage (e.g. before Mailfilter measured it).
function trimTrend(v: number[]): number[] {
  const first = v.findIndex((x) => x > 0);
  return first < 0 ? v : v.slice(Math.min(first, v.length - 2));
}

function HowCounted() {
  const { t } = useTranslation();
  return (
    <Card title={t("usage.how_title")}>
      <ul className="usage-how">
        {(["mailboxes", "domains", "mail", "archive", "frozen"] as const).map((k) => (
          <li key={k}><b>{t(`usage.how.${k}_b`)}</b> {t(`usage.how.${k}`)}</li>
        ))}
      </ul>
    </Card>
  );
}

function lineLabel(t: (k: string, o?: Record<string, unknown>) => string, ln: Line) {
  return ln.code === "discount" ? t("usage.line.discount", { pct: ln.quantity }) : t(`usage.line.${ln.code}`);
}

function CustomerDrawer({ c, u, archive, onClose }: { c: CustomerRow; u: UsageData; archive: boolean; onClose: () => void }) {
  const { t } = useTranslation();
  const f = useFormat();
  const first = new Date(`${u.month}-15T12:00:00Z`);
  const hist = c.trend.map((v, i) => ({ month: ym(new Date(first.getFullYear(), first.getMonth() - (c.trend.length - 1 - i), 1)), mailboxes: v }));
  const qty = (ln: Line) => ln.code === "discount" ? `${f.n(ln.quantity, 0)}%`
    : ["mail_in", "mail_out"].includes(ln.code) ? f.n(ln.quantity, 3) : ln.code === "archive" ? f.n(ln.quantity, 2) : f.n(ln.quantity);
  return (
    <Drawer title={c.name} sub={[c.reseller && t("usage.via", { name: c.reseller }), c.ref && t("usage.ref_is", { ref: c.ref })].filter(Boolean).join(" · ")} onClose={onClose}>
      {u.prices && (
        <div>
          <b>{t("usage.lines_title", { month: f.month(u.month) })}</b>
          {c.lines.length === 0 ? <p className="muted">{t("usage.no_lines")}</p> : (
            <div className="tbl-wrap"><table>
              <thead><tr><th>{t("usage.description")}</th><th style={{ textAlign: "right" }}>{t("usage.quantity")}</th>
                <th style={{ textAlign: "right" }}>{t("usage.price")}</th><th style={{ textAlign: "right" }}>{t("usage.amount")}</th></tr></thead>
              <tbody>
                {c.lines.map((ln) => (
                  <tr key={ln.code}>
                    <td>{lineLabel(t, ln)}</td>
                    <td className="num" style={{ textAlign: "right" }}>{qty(ln)}</td>
                    <td className="num" style={{ textAlign: "right" }}>{ln.code === "discount" ? "" : f.money(ln.price, u.currency)}</td>
                    <td className="num" style={{ textAlign: "right" }}>{f.money(ln.amount, u.currency)}</td>
                  </tr>
                ))}
                <tr><td><b>{t("usage.total_excl")}</b></td><td /><td /><td className="num" style={{ textAlign: "right" }}><b>{f.money(c.amount ?? 0, u.currency)}</b></td></tr>
              </tbody>
            </table></div>
          )}
        </div>
      )}
      <div>
        <b>{t("usage.history")}</b>
        <StackedColumns height={170} data={hist} series={[{ key: "mailboxes", label: t("usage.mailboxes"), color: "var(--chart-1)" }]}
          label={t("usage.history")} xLabel={(h) => f.month(h.month)} xTick={(h) => f.month(h.month, true)} />
      </div>
      <div>
        <b>{t("usage.per_domain")}</b>
        <div className="tbl-wrap"><table>
          <thead><tr><th>{t("usage.domain")}</th><th style={{ textAlign: "right" }}>{t("usage.mailboxes")}</th>
            <th style={{ textAlign: "right" }}>{t("usage.mail_in")}</th><th style={{ textAlign: "right" }}>{t("usage.mail_out")}</th>
            {archive && <th style={{ textAlign: "right" }}>{t("usage.archive")}</th>}</tr></thead>
          <tbody>
            {c.domains.map((d) => (
              <tr key={d.domain}>
                <td className="mono">{d.domain}</td>
                <td className="num" style={{ textAlign: "right" }}>{f.n(d.mailboxes)}</td>
                <td className="num" style={{ textAlign: "right" }}>{f.n(d.mail_in)}</td>
                <td className="num" style={{ textAlign: "right" }}>{f.n(d.mail_out)}</td>
                {archive && <td className="num" style={{ textAlign: "right" }}>{f.gb(d.archive_bytes)}</td>}
              </tr>
            ))}
          </tbody>
        </table></div>
      </div>
    </Drawer>
  );
}

function ExportDrawer({ u, me, resellers, onClose }: { u: UsageData; me: Me; resellers: [string, string][]; onClose: () => void }) {
  const { t, i18n } = useTranslation();
  const f = useFormat();
  const admin = me.role === "admin";
  const [kind, setKind] = useState<"customers" | "domains" | "lines">("customers");
  const [sep, setSep] = useState<";" | ",">(i18n.language === "nl" ? ";" : ",");
  const [reseller, setReseller] = useState("");
  const kinds = (u.prices ? ["customers", "domains", "lines"] : ["customers", "domains"]) as (typeof kind)[];
  const href = `/api/usage/export${qs({ month: u.month, kind, sep, lang: i18n.language, reseller: admin ? reseller : undefined })}`;
  return (
    <Drawer title={t("usage.export_title")} sub={f.month(u.month)} onClose={onClose}
      footer={<a className="btn primary" href={href} download onClick={() => setTimeout(onClose, 300)}><Download /> {t("usage.download", { name: `usage-${u.month}-${kind}.csv` })}</a>}>
      <Field label={t("usage.export_what")}>
        <div className="seg" role="group">
          {kinds.map((k) => <button key={k} type="button" aria-pressed={kind === k} onClick={() => setKind(k)}>{t(`usage.kind.${k}`)}</button>)}
        </div>
      </Field>
      <p className="muted" style={{ margin: 0 }}>{t(`usage.kind_hint.${kind}`)}</p>
      {admin && (
        <Field label={t("usage.reseller")}>
          <select className="select" value={reseller} onChange={(e) => setReseller(e.target.value)}>
            <option value="">{t("usage.all_customers")}</option>
            <option value="direct">{t("usage.direct_only")}</option>
            {resellers.map(([id, name]) => <option key={id} value={id}>{name}</option>)}
          </select>
        </Field>
      )}
      <Field label={t("usage.separator")} hint={t("usage.separator_hint")}>
        <select className="select" value={sep} onChange={(e) => setSep(e.target.value as ";" | ",")}>
          <option value=";">{t("usage.sep_semicolon")}</option>
          <option value=",">{t("usage.sep_comma")}</option>
        </select>
      </Field>
    </Drawer>
  );
}

// --- admin: billing customers, resellers, prices ---------------------------------------

const num = (v: string): number | null => (v.trim() === "" ? null : Number(v.replace(",", ".")));
const str = (v: number | null | undefined) => (v == null ? "" : String(v));

function BillingAdmin({ billing, error }: { billing?: Billing; error: unknown }) {
  const { t } = useTranslation();
  const [cust, setCust] = useState<BillingCustomer | "new" | null>(null);
  const [res, setRes] = useState<Reseller | "new" | null>(null);
  const resName = (id: number | null) => billing?.resellers.find((r) => r.id === id)?.name;
  const cols: Column<BillingCustomer>[] = [
    { key: "name", label: t("usage.customer"), always: true, mobile: "title", sort: (c) => c.name, render: (c) => <span className="cell-main">{c.name}</span> },
    { key: "ref", label: t("usage.ref"), sort: (c) => c.ref, render: (c) => <span className="mono">{c.ref}</span> },
    { key: "reseller", label: t("usage.reseller"), sort: (c) => resName(c.reseller_id) ?? "",
      render: (c) => resName(c.reseller_id) ? <span className="tag lucht">{resName(c.reseller_id)}</span> : <span className="muted">{t("usage.direct")}</span> },
    { key: "domains", label: t("usage.domains"), text: (c) => c.domains.join(" "), render: (c) => c.domains.length ? c.domains.join(", ") : <span className="muted">{t("usage.none")}</span> },
    { key: "prices", label: t("usage.own_prices"), optional: true, render: (c) => Object.keys(c.tariffs || {}).length ? <span className="status s-info">{t("usage.own_prices_yes")}</span> : "" },
    { key: "logins", label: t("usage.logins"), align: "right", sort: (c) => c.logins, render: (c) => <span className="num">{c.logins}</span> },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
      <ErrorText error={error} />
      {billing && billing.unassigned.length > 0 && (
        <div className="callout warn"><TriangleAlert /><span><b>{t("usage.unassigned_title", { count: billing.unassigned.length })}</b> {billing.unassigned.join(", ")}. {t("usage.unassigned_hint")}</span></div>
      )}
      <Card title={t("usage.billing_customers")} right={<button className="btn small primary" type="button" onClick={() => setCust("new")}><Plus /> {t("usage.add_customer")}</button>} flush>
        <DataTable id="billing-customers" rows={billing?.customers} rowKey={(c) => c.id} columns={cols} onRowClick={(c) => setCust(c)}
          empty={t("usage.no_billing_customers")} defaultSort={{ key: "name", dir: 1 }} />
      </Card>
      <Card title={t("usage.resellers")} right={<button className="btn small" type="button" onClick={() => setRes("new")}><Plus /> {t("usage.add_reseller")}</button>} flush>
        {!billing?.resellers.length ? <div className="empty">{t("usage.no_resellers")}</div> : (
          <div className="tbl-wrap"><table>
            <thead><tr><th>{t("usage.reseller")}</th><th>{t("usage.ref")}</th><th style={{ textAlign: "right" }}>{t("usage.customers")}</th>
              <th style={{ textAlign: "right" }}>{t("usage.discount")}</th><th>{t("usage.logins")}</th><th /></tr></thead>
            <tbody>
              {billing.resellers.map((r) => (
                <tr key={r.id} className="click" tabIndex={0} onClick={() => setRes(r)} onKeyDown={(e) => e.key === "Enter" && setRes(r)}>
                  <td><div className="cell-main">{r.name}</div>{r.note && <div className="cell-sub">{r.note}</div>}</td>
                  <td className="mono">{r.ref}</td>
                  <td className="num" style={{ textAlign: "right" }}>{r.customers}</td>
                  <td className="num" style={{ textAlign: "right" }}>{r.discount_percent}%</td>
                  <td>{r.logins.length ? r.logins.join(", ") : <span className="muted">{t("usage.no_logins")}</span>}</td>
                  <td style={{ textAlign: "right" }}><Pencil size={15} aria-hidden /></td>
                </tr>
              ))}
            </tbody>
          </table></div>
        )}
        <p className="muted" style={{ margin: 0, padding: "12px 16px", fontSize: 12.5 }}>{t("usage.reseller_login_hint")}</p>
      </Card>
      {cust && billing && <CustomerForm billing={billing} c={cust === "new" ? null : cust} onClose={() => setCust(null)} />}
      {res && <ResellerForm r={res === "new" ? null : res} onClose={() => setRes(null)} />}
    </div>
  );
}

function CustomerForm({ billing, c, onClose }: { billing: Billing; c: BillingCustomer | null; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [name, setName] = useState(c?.name ?? "");
  const [ref, setRef] = useState(c?.ref ?? "");
  const [reseller, setReseller] = useState<number | null>(c?.reseller_id ?? null);
  const [domains, setDomains] = useState<string[]>(c?.domains ?? []);
  const [prices, setPrices] = useState<Record<string, string>>(() =>
    Object.fromEntries([...PRICE_KEYS, "fixed"].map((k) => [k, str((c?.tariffs as Record<string, number | null> | undefined)?.[k])])));
  const [note, setNote] = useState(c?.note ?? "");
  const owner = (d: string) => billing.customers.find((x) => x.id !== c?.id && x.domains.includes(d))?.name;
  const done = (b: Billing) => { qc.setQueryData(["billing"], b); qc.invalidateQueries({ queryKey: ["usage"] }); onClose(); };
  const body = () => ({ name, ref, reseller_id: reseller, domains, note,
    tariffs: Object.fromEntries(Object.entries(prices).map(([k, v]) => [k, num(v)])) });
  const save = useMutation({
    mutationFn: () => (c ? api.put<Billing>(`/billing/customers/${c.id}`, body()) : api.post<Billing>("/billing/customers", body())),
    onSuccess: done,
  });
  const del = useMutation({ mutationFn: () => api.del<Billing>(`/billing/customers/${c!.id}`), onSuccess: done });
  return (
    <Drawer title={c ? c.name : t("usage.add_customer")} sub={t("usage.customer_form_sub")} onClose={onClose}
      footer={<>
        {c && <button className="btn danger" type="button" disabled={del.isPending} onClick={() => window.confirm(t("usage.delete_customer_confirm", { name: c.name })) && del.mutate()}><Trash2 /> {t("common.delete")}</button>}
        <button className="btn primary" type="submit" form="billing-customer" disabled={save.isPending || !name.trim()} style={{ marginLeft: "auto" }}>{t("common.save")}</button>
      </>}>
      <form id="billing-customer" className="form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="full"><ErrorText error={save.error || del.error} /></div>
        <Field label={t("usage.name")}><input className="input" required value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label={t("usage.ref")} hint={t("usage.ref_hint")}><input className="input" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
        <Field label={t("usage.reseller")} full>
          <select className="select" value={reseller ?? ""} onChange={(e) => setReseller(e.target.value ? +e.target.value : null)}>
            <option value="">{t("usage.direct_customer")}</option>
            {billing.resellers.map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </Field>
        <div className="field full">
          <span style={{ fontSize: 12.5, fontWeight: 650 }}>{t("usage.domains")}</span>
          <span className="hint">{t("usage.domains_hint")}</span>
          {billing.domains.length ? (
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 4 }}>
              {billing.domains.map((d) => (
                <label key={d} className="check tag" style={{ cursor: "pointer", padding: "5px 10px" }} title={owner(d) ? t("usage.moves_from", { name: owner(d) }) : undefined}>
                  <input type="checkbox" style={{ margin: 0 }} checked={domains.includes(d)}
                    onChange={() => setDomains(domains.includes(d) ? domains.filter((x) => x !== d) : [...domains, d])} />
                  {d}{owner(d) && <span className="muted"> · {owner(d)}</span>}
                </label>
              ))}
            </div>
          ) : <span className="muted">{t("users.no_domains")}</span>}
        </div>
        <div className="field full">
          <span style={{ fontSize: 12.5, fontWeight: 650 }}>{t("usage.own_prices")}</span>
          <span className="hint">{t("usage.own_prices_hint", { currency: billing.tariffs.currency })}</span>
        </div>
        {[...PRICE_KEYS, "fixed" as const].map((k) => (
          <Field key={k} label={t(`usage.price_field.${k}`)} hint={k === "fixed" ? t("usage.fixed_hint") : undefined}>
            <input className="input num" inputMode="decimal" placeholder={k === "fixed" ? "" : String(billing.tariffs[k as keyof Tariffs] ?? "")}
              value={prices[k]} onChange={(e) => setPrices({ ...prices, [k]: e.target.value })} />
          </Field>
        ))}
        <Field label={t("usage.note")} full><textarea className="input" rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
      </form>
    </Drawer>
  );
}

function ResellerForm({ r, onClose }: { r: Reseller | null; onClose: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [name, setName] = useState(r?.name ?? "");
  const [ref, setRef] = useState(r?.ref ?? "");
  const [discount, setDiscount] = useState(str(r?.discount_percent ?? 0));
  const [note, setNote] = useState(r?.note ?? "");
  const done = (b: Billing) => { qc.setQueryData(["billing"], b); qc.invalidateQueries({ queryKey: ["usage"] }); onClose(); };
  const body = () => ({ name, ref, discount_percent: num(discount) ?? 0, note });
  const save = useMutation({
    mutationFn: () => (r ? api.put<Billing>(`/billing/resellers/${r.id}`, body()) : api.post<Billing>("/billing/resellers", body())),
    onSuccess: done,
  });
  const del = useMutation({ mutationFn: () => api.del<Billing>(`/billing/resellers/${r!.id}`), onSuccess: done });
  return (
    <Drawer title={r ? r.name : t("usage.add_reseller")} sub={t("usage.reseller_form_sub")} onClose={onClose}
      footer={<>
        {r && <button className="btn danger" type="button" disabled={del.isPending} onClick={() => window.confirm(t("usage.delete_reseller_confirm", { name: r.name })) && del.mutate()}><Trash2 /> {t("common.delete")}</button>}
        <button className="btn primary" type="submit" form="billing-reseller" disabled={save.isPending || !name.trim()} style={{ marginLeft: "auto" }}>{t("common.save")}</button>
      </>}>
      <form id="billing-reseller" className="form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="full"><ErrorText error={save.error || del.error} /></div>
        <Field label={t("usage.name")}><input className="input" required value={name} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label={t("usage.ref")} hint={t("usage.ref_hint")}><input className="input" value={ref} onChange={(e) => setRef(e.target.value)} /></Field>
        <Field label={t("usage.discount")} hint={t("usage.discount_hint")}>
          <input className="input num" inputMode="decimal" value={discount} onChange={(e) => setDiscount(e.target.value)} />
        </Field>
        <Field label={t("usage.note")} full><textarea className="input" rows={2} value={note} onChange={(e) => setNote(e.target.value)} /></Field>
        {r && <p className="muted full" style={{ margin: 0 }}>{r.logins.length ? t("usage.reseller_logins", { list: r.logins.join(", ") }) : t("usage.reseller_login_hint")}</p>}
      </form>
    </Drawer>
  );
}

function PricesForm({ tariffs }: { tariffs: Tariffs }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [v, setV] = useState<Record<string, string>>(() => Object.fromEntries(PRICE_KEYS.map((k) => [k, str(tariffs[k])])));
  const [currency, setCurrency] = useState(tariffs.currency);
  const save = useMutation({
    mutationFn: () => api.put<Billing>("/billing/tariffs", { currency, ...Object.fromEntries(PRICE_KEYS.map((k) => [k, num(v[k]) ?? 0])) }),
    onSuccess: (b) => { qc.setQueryData(["billing"], b); qc.invalidateQueries({ queryKey: ["usage"] }); },
  });
  return (
    <Card title={t("usage.prices_title")} right={<span className="muted">{t("usage.prices_sub")}</span>}>
      <form className="form-grid" onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
        <div className="full"><ErrorText error={save.error} /></div>
        {PRICE_KEYS.map((k) => (
          <Field key={k} label={t(`usage.price_field.${k}`)} hint={k === "per_gb_archive" ? t("usage.archive_price_hint") : k === "minimum" ? t("usage.minimum_hint") : undefined}>
            <input className="input num" inputMode="decimal" value={v[k]} onChange={(e) => setV({ ...v, [k]: e.target.value })} />
          </Field>
        ))}
        <Field label={t("usage.currency")}>
          <select className="select" value={currency} onChange={(e) => setCurrency(e.target.value as Tariffs["currency"])}>
            <option value="EUR">EUR (€)</option><option value="USD">USD ($)</option><option value="GBP">GBP (£)</option>
          </select>
        </Field>
        <p className="muted full" style={{ margin: 0 }}>{t("usage.prices_note")}</p>
        <div className="full"><button className="btn primary" disabled={save.isPending}>{t("common.save")}</button></div>
      </form>
    </Card>
  );
}
