import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Ban, Check, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { DataTable, type Column } from "../components/DataTable";
import { Card, ErrorText, PageHead, formatDate, useToast } from "../components/ui";
import { api, qs } from "../lib/api";
import type { Me } from "../lib/auth";

export type ListName = "allow" | "block";
export type ListEntry = {
  id: number;
  list: ListName;
  recipient: string;
  sender: string;
  scope: "everyone" | "domain" | "mailbox";
  sender_kind: "address" | "domain";
  comment: string;
  created_by: string;
  created_at: string;
};
type Options = { domains: string[]; everyone: boolean };

export function ListBadge({ list }: { list: ListName }) {
  const { t } = useTranslation();
  return list === "allow" ? (
    <span className="status s-ok"><Check aria-hidden />{t("lists.allowed")}</span>
  ) : (
    <span className="status s-bad"><Ban aria-hidden />{t("lists.blocked")}</span>
  );
}

export default function Lists({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const [filter, setFilter] = useState<"" | ListName>("");
  const [q, setQ] = useState("");
  const entries = useQuery({
    queryKey: ["lists", filter, q],
    queryFn: () => api.get<{ items: ListEntry[]; total: number }>(`/lists${qs({ list: filter, q })}`),
  });
  const opts = useQuery({ queryKey: ["lists-options"], queryFn: () => api.get<Options>("/lists/options") });
  const domains = opts.data?.domains ?? [];
  const [f, setF] = useState({ list: "allow" as ListName, sender: "", target: "", mailbox: "", comment: "" });
  // "" = first domain; "*" = all domains; "@" = one mailbox; otherwise a domain
  const target = f.target || (opts.data?.everyone ? "*" : domains[0] ?? "");
  const recipient = target === "@" ? f.mailbox : target;
  const refresh = () => qc.invalidateQueries({ queryKey: ["lists"] });
  const add = useMutation({
    mutationFn: () => api.post<ListEntry>("/lists", { list: f.list, recipient, sender: f.sender, comment: f.comment }),
    onSuccess: (e) => {
      refresh();
      toast(t(e.list === "allow" ? "lists.added_allow" : "lists.added_block", { sender: e.sender }));
      setF({ ...f, sender: "", comment: "" });
    },
  });
  const del = useMutation({ mutationFn: (id: number) => api.del(`/lists/${id}`), onSuccess: refresh });
  const forLabel = (e: ListEntry) =>
    e.scope === "everyone" ? t("lists.for_everyone") : e.scope === "domain" ? t("lists.for_domain", { domain: e.recipient }) : e.recipient;

  const columns: Column<ListEntry>[] = [
    { key: "list", label: t("lists.list"), sort: (e) => e.list, nowrap: true, mobile: "aside", render: (e) => <ListBadge list={e.list} /> },
    { key: "sender", label: t("lists.sender"), always: true, sort: (e) => e.sender, mobile: "title",
      render: (e) => (
        <>
          <div className="cell-main mono" style={{ wordBreak: "break-all" }}>{e.sender}</div>
          <div className="cell-sub">{t(e.sender_kind === "domain" ? "lists.kind_domain" : "lists.kind_address")}</div>
        </>
      ) },
    { key: "for", label: t("lists.for"), sort: forLabel, mobileLabel: true,
      render: (e) => <span style={{ wordBreak: "break-all" }}>{e.scope === "everyone" ? <span className="tag">{forLabel(e)}</span> : forLabel(e)}</span> },
    { key: "comment", label: t("lists.comment"), sort: (e) => e.comment ?? "", render: (e) => e.comment || <span className="muted">—</span> },
    { key: "added", label: t("lists.added"), sort: (e) => e.created_at, optional: false, mobileLabel: true,
      render: (e) => (
        <>
          <div className="cell-main nowrap num">{formatDate(e.created_at, i18n.language, false)}</div>
          {e.created_by && <div className="cell-sub" style={{ wordBreak: "break-all" }}>{e.created_by}</div>}
        </>
      ) },
    { key: "del", label: "", always: true, align: "right",
      render: (e) => (
        <button className="icon-btn" aria-label={t("common.delete")}
          onClick={() => window.confirm(t("lists.delete_confirm", { sender: e.sender })) && del.mutate(e.id)}>
          <Trash2 />
        </button>
      ) },
  ];

  return (
    <>
      <PageHead title={t("lists.title")} sub={me.role === "admin" ? t("lists.sub") : t("lists.sub_customer")} />
      <Card title={t("lists.add_title")}>
        <form className="form-grid" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
          <div className="field full">
            <span style={{ fontSize: 12.5, fontWeight: 650 }}>{t("lists.what")}</span>
            <div className="seg" style={{ alignSelf: "flex-start" }} role="group" aria-label={t("lists.what")}>
              {(["allow", "block"] as ListName[]).map((l) => (
                <button key={l} type="button" aria-pressed={f.list === l} onClick={() => setF({ ...f, list: l })}>
                  {t(`lists.do_${l}`)}
                </button>
              ))}
            </div>
          </div>
          <div className="field">
            <label htmlFor="l-sender">{t("lists.sender")}</label>
            <input id="l-sender" className="input mono" required placeholder={t("lists.sender_placeholder")} value={f.sender}
              onChange={(e) => setF({ ...f, sender: e.target.value })} />
            <span className="hint">{t("lists.sender_hint")}</span>
          </div>
          <div className="field">
            <label htmlFor="l-for">{t("lists.for")}</label>
            <select id="l-for" className="select" value={target} onChange={(e) => setF({ ...f, target: e.target.value })}>
              {opts.data?.everyone && <option value="*">{t("lists.for_everyone")}</option>}
              {domains.map((d) => <option key={d} value={d}>{t("lists.for_domain", { domain: d })}</option>)}
              <option value="@">{t("lists.for_mailbox")}</option>
            </select>
            {target === "@" && (
              <input className="input mono" style={{ marginTop: 8 }} required type="email" aria-label={t("lists.mailbox")}
                placeholder={t("lists.mailbox_placeholder", { domain: domains[0] ?? "example.nl" })} value={f.mailbox}
                onChange={(e) => setF({ ...f, mailbox: e.target.value })} />
            )}
          </div>
          <div className="field full">
            <label htmlFor="l-comment">{t("lists.comment")}</label>
            <input id="l-comment" className="input" placeholder={t("lists.comment_placeholder")} value={f.comment}
              onChange={(e) => setF({ ...f, comment: e.target.value })} />
          </div>
          <div className="full" style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
            <button className={`btn ${f.list === "block" ? "danger" : "primary"}`} disabled={add.isPending}>
              <Plus /> {t(f.list === "allow" ? "lists.add_allow" : "lists.add_block")}
            </button>
            <span className="muted" style={{ fontSize: 12.5 }}>{t(f.list === "allow" ? "lists.allow_hint" : "lists.block_hint")}</span>
          </div>
          <div className="full"><ErrorText error={add.error || del.error} /></div>
        </form>
      </Card>

      <Card flush>
        <DataTable
          id="lists"
          rows={entries.data?.items}
          rowKey={(e) => e.id}
          loading={entries.isLoading}
          columns={columns}
          search={{ value: q, onChange: setQ, placeholder: t("lists.search") }}
          tools={
            <>
              <div className="seg" role="group" aria-label={t("lists.show")}>
                {(["", "allow", "block"] as const).map((l) => (
                  <button key={l || "all"} type="button" aria-pressed={filter === l} onClick={() => setFilter(l)}>
                    {t(l ? `lists.show_${l}` : "lists.show_all")}
                  </button>
                ))}
              </div>
              {entries.data && <span className="muted">{t("lists.count", { count: entries.data.total })}</span>}
            </>
          }
          empty={q || filter ? t("trace.no_results") : t("lists.empty")}
        />
      </Card>
    </>
  );
}
