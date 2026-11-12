// Ctrl+K (⌘K on a Mac) or "/": one search box for messages, domains, users,
// pages, settings and quick actions. Arrow keys choose, Enter opens.
import { useQuery } from "@tanstack/react-query";
import { AtSign, CornerDownLeft, Info, Languages, LogOut, Mail, Monitor, Moon, Search, ShieldAlert, Sun, UserRound } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { api, qs } from "../lib/api";
import type { NavItem } from "../nav";
import { formatDate } from "./ui";
import type { ThemeChoice } from "./theme";

type Hits = {
  messages: { queue_id: string; at: string; sender: string | null; recipients: string[]; status: string }[];
  domains: { name: string }[];
  users: { id: number; email: string; name: string; role: string }[];
};

type Item = { id: string; group: string; icon: LucideIcon; title: string; sub?: string; run: () => void; words?: string };

export type PaletteActions = {
  setTheme: (t: ThemeChoice) => void;
  setLang: (l: "en" | "nl") => void;
  openInfo: () => void;
  logout: () => void;
};

function useDebounced<T>(value: T, ms: number): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const id = window.setTimeout(() => setV(value), ms);
    return () => window.clearTimeout(id);
  }, [value, ms]);
  return v;
}

const norm = (s: string) => s.normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();

export function useCommandPaletteKey(open: () => void) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const typing = e.target instanceof HTMLElement && (e.target.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(e.target.tagName));
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        open();
      } else if (e.key === "/" && !typing && !e.ctrlKey && !e.metaKey && !e.altKey) {
        e.preventDefault();
        open();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open]);
}

export function CommandPalette({ nav, onClose, actions }: { nav: NavItem[]; onClose: () => void; actions: PaletteActions }) {
  const { t, i18n } = useTranslation();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [sel, setSel] = useState(0);
  const input = useRef<HTMLInputElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const dq = useDebounced(q.trim(), 180);
  const hits = useQuery({
    queryKey: ["quick-search", dq],
    queryFn: () => api.get<Hits>(`/search${qs({ q: dq })}`),
    enabled: dq.length >= 2,
    staleTime: 10_000,
  });

  useEffect(() => {
    input.current?.focus();
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = prev;
    };
  }, []);

  const go = (to: string) => () => {
    navigate(to);
    onClose();
  };
  const act = (fn: () => void) => () => {
    fn();
    onClose();
  };

  const items = useMemo<Item[]>(() => {
    const out: Item[] = [];
    const h = dq.length >= 2 ? hits.data : undefined;
    for (const m of h?.messages ?? []) {
      const held = m.status === "quarantined";
      out.push({
        id: `m:${m.queue_id}`, group: t("search.messages"), icon: held ? ShieldAlert : Mail,
        title: m.recipients.join(", ") || m.queue_id,
        sub: `${m.sender || t("trace.empty_sender")} · ${formatDate(m.at, i18n.language)} · ${t(`status.${m.status}`, { defaultValue: m.status })}`,
        run: go(held ? `/quarantine?id=${encodeURIComponent(m.queue_id)}` : `/trace?id=${encodeURIComponent(m.queue_id)}`),
      });
    }
    if (h?.messages.length) {
      out.push({ id: "m:all", group: t("search.messages"), icon: Search, title: t("search.all_messages", { q: dq }), run: go(`/trace${qs({ q: dq })}`) });
    }
    for (const d of h?.domains ?? []) {
      out.push({ id: `d:${d.name}`, group: t("search.domains"), icon: AtSign, title: d.name, sub: t("search.domain_sub"), run: go(`/domains?open=${encodeURIComponent(d.name)}`) });
    }
    for (const u of h?.users ?? []) {
      out.push({ id: `u:${u.id}`, group: t("search.users"), icon: UserRound, title: u.name || u.email, sub: `${u.email} · ${t(`users.${u.role}`, { defaultValue: u.role })}`, run: go(`/users?id=${u.id}`) });
    }

    // Pages and settings: matched in the browser on their name and keywords, in both languages.
    const needle = norm(q.trim());
    const pages: Item[] = nav.map((n) => {
      const key = n.label.replace(/^nav\./, "");
      return {
        id: `p:${n.to}`, group: t("search.pages"), icon: n.icon, title: t(n.label), sub: t(`search.kw.${key}`, { defaultValue: "" }),
        words: `${t(n.label)} ${t(`search.kw.${key}`, { defaultValue: "" })} ${t(n.label, { lng: "en" })} ${t(n.label, { lng: "nl" })}`, run: go(n.to),
      };
    });
    pages.push({ id: "p:/account", group: t("search.pages"), icon: UserRound, title: t("nav.account"), sub: t("search.kw.account"),
      words: `${t("nav.account")} ${t("search.kw.account")}`, run: go("/account") });
    const quick: Item[] = [
      { id: "a:light", group: t("search.actions"), icon: Sun, title: t("theme.light"), words: `${t("theme.light")} theme thema licht light`, run: act(() => actions.setTheme("light")) },
      { id: "a:dark", group: t("search.actions"), icon: Moon, title: t("theme.dark"), words: `${t("theme.dark")} theme thema donker dark`, run: act(() => actions.setTheme("dark")) },
      { id: "a:system", group: t("search.actions"), icon: Monitor, title: t("theme.system"), words: `${t("theme.system")} theme thema auto systeem system`, run: act(() => actions.setTheme("system")) },
      { id: "a:en", group: t("search.actions"), icon: Languages, title: "English", words: "english engels language taal", run: act(() => actions.setLang("en")) },
      { id: "a:nl", group: t("search.actions"), icon: Languages, title: "Nederlands", words: "nederlands dutch language taal", run: act(() => actions.setLang("nl")) },
      { id: "a:info", group: t("search.actions"), icon: Info, title: t("search.info"), words: `${t("search.info")} info version versie update updates`, run: act(actions.openInfo) },
      { id: "a:logout", group: t("search.actions"), icon: LogOut, title: t("common.logout"), words: `${t("common.logout")} logout uitloggen`, run: act(actions.logout) },
    ];
    const match = (it: Item) => !needle || needle.split(/\s+/).every((w) => norm(`${it.title} ${it.words ?? ""}`).includes(w));
    out.push(...pages.filter(match).slice(0, needle ? 8 : 6));
    out.push(...quick.filter(match).slice(0, needle ? 4 : 3));
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [hits.data, dq, q, nav, i18n.language]);

  useEffect(() => setSel(0), [q, hits.data]);
  useEffect(() => {
    list.current?.querySelector('[aria-selected="true"]')?.scrollIntoView({ block: "nearest" });
  }, [sel]);

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    } else if (e.key === "ArrowDown" || (e.key === "Tab" && !e.shiftKey)) {
      e.preventDefault();
      setSel((s) => (items.length ? (s + 1) % items.length : 0));
    } else if (e.key === "ArrowUp" || (e.key === "Tab" && e.shiftKey)) {
      e.preventDefault();
      setSel((s) => (items.length ? (s - 1 + items.length) % items.length : 0));
    } else if (e.key === "Enter") {
      e.preventDefault();
      items[sel]?.run();
    }
  };

  const busy = dq.length >= 2 && hits.isFetching;
  let lastGroup = "";
  return (
    <div className="cmdk-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="cmdk" role="dialog" aria-modal="true" aria-label={t("search.title")} onKeyDown={onKey}>
        <div className="cmdk-in">
          <Search aria-hidden />
          <input ref={input} value={q} onChange={(e) => setQ(e.target.value)} placeholder={t("search.placeholder")} aria-label={t("search.placeholder")}
            role="combobox" aria-expanded="true" aria-controls="cmdk-list" aria-activedescendant={items[sel] ? `cmdk-${sel}` : undefined} autoComplete="off" spellCheck={false} />
          <kbd>Esc</kbd>
        </div>
        <div className="cmdk-list" id="cmdk-list" role="listbox" ref={list}>
          {items.length === 0 ? (
            <div className="cmdk-empty">{busy ? t("common.loading") : t("search.nothing", { q })}</div>
          ) : (
            items.map((it, i) => {
              const head = it.group !== lastGroup ? it.group : null;
              lastGroup = it.group;
              return (
                <div key={it.id}>
                  {head && <div className="cmdk-group">{head}</div>}
                  <button type="button" id={`cmdk-${i}`} role="option" aria-selected={i === sel} className="cmdk-item"
                    onMouseMove={() => i !== sel && setSel(i)} onClick={it.run}>
                    <span className="ci"><it.icon aria-hidden /></span>
                    <span className="grow">
                      <b>{it.title}</b>
                      {it.sub && <span>{it.sub}</span>}
                    </span>
                    {i === sel && <kbd><CornerDownLeft size={11} aria-hidden /></kbd>}
                  </button>
                </div>
              );
            })
          )}
        </div>
        <div className="cmdk-foot">
          <span><kbd>↑</kbd> <kbd>↓</kbd> {t("search.choose")}</span>
          <span><kbd>↵</kbd> {t("search.open")}</span>
          <span><kbd>Esc</kbd> {t("common.close")}</span>
          {busy && <span className="busy">{t("search.searching")}</span>}
        </div>
      </div>
    </div>
  );
}
