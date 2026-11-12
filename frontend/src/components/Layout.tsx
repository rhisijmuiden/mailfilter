import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, LogOut, Menu, Monitor, Moon, Search, Sun, UserRound } from "lucide-react";
import { Suspense, useCallback, useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { setLanguage, type Lang } from "../i18n";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";
import type { Stats } from "../lib/trace";
import { GROUPS, NAV } from "../nav";
import { CommandPalette, useCommandPaletteKey } from "./CommandPalette";
import { useTheme, type ThemeChoice } from "./theme";
import { InfoModal, useSystemInfo } from "./InfoModal";
import { Logo } from "./Logo";
import { Skeleton } from "./ui";

export function Layout({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const loc = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const [info, setInfo] = useState(false);
  const [theme, setTheme] = useTheme();
  const [palette, setPalette] = useState(false);
  const openPalette = useCallback(() => setPalette(true), []);
  useCommandPaletteKey(openPalette);
  const isMac = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform);
  const sys = useSystemInfo();
  const stats = useQuery({ queryKey: ["trace-stats"], queryFn: () => api.get<Stats>("/trace/stats?hours=24"), refetchInterval: 60_000 });
  const mailtrap = useQuery({
    queryKey: ["mailtrap-menu"],
    queryFn: () => api.get<{ unread: number; visible: boolean }>("/mailtrap/unread"),
    refetchInterval: 60_000,
  });
  const BADGES = { quarantine: stats.data?.quarantined_now, mailtrap: mailtrap.data?.unread };
  const MODULES = { mailtrap: !!mailtrap.data?.visible };
  useEffect(() => setNavOpen(false), [loc.pathname]);

  const changeLang = async (lang: Lang) => {
    setLanguage(lang);
    const updated = await api.patch<Me>("/auth/me", { language: lang }, { quiet: true }).catch(() => null);
    if (updated) qc.setQueryData(["me"], updated);
  };
  const logout = async () => {
    await api.post("/auth/logout").catch(() => null);
    qc.clear();
    qc.setQueryData(["me"], null);
  };

  const items = NAV.filter((n) => (!n.adminOnly || me.role === "admin") && (!n.module || MODULES[n.module]));
  const initials = (me.name || me.email).split(/[\s@.]+/).filter(Boolean).slice(0, 2).map((s) => s[0]!.toUpperCase()).join("");

  return (
    <div className={`app${navOpen ? " nav-open" : ""}`}>
      <aside className="sidebar" aria-label={t("nav.menu")}>
        <div className="brand">
          <Logo />
          <span className="product">{t("app.product")}</span>
          <span className="mono muted" style={{ fontSize: 11.5 }}>{sys.data?.hostname}</span>
        </div>
        <nav className="nav">
          {GROUPS.map((g) => {
            const list = items.filter((n) => n.group === g);
            if (!list.length) return null;
            return (
              <div key={g} style={{ display: "contents" }}>
                <div className="group">{t(`nav.group.${g}`)}</div>
                {list.map((n) => (
                  <NavLink key={n.to} to={n.to} end className={({ isActive }) => (isActive ? "active" : "")}>
                    <n.icon aria-hidden />
                    <span>{t(n.label)}</span>
                    {n.badge && !!BADGES[n.badge] && <span className="count">{BADGES[n.badge]}</span>}
                  </NavLink>
                ))}
              </div>
            );
          })}
        </nav>
        <div className="side-foot">
          <button className="info-btn" type="button" onClick={() => setInfo(true)}>
            <Info aria-hidden />
            <span>Info</span>
            <span className="v num">{sys.data?.version}</span>
          </button>
        </div>
      </aside>
      {navOpen && <div className="scrim" style={{ zIndex: 44 }} onClick={() => setNavOpen(false)} />}
      <div className="main">
        <header className="topbar">
          <button className="icon-btn menu-toggle" type="button" aria-label={t("nav.menu")} onClick={() => setNavOpen(true)}>
            <Menu />
          </button>
          <button type="button" className="cmdk-trigger" onClick={openPalette} aria-label={t("search.placeholder")} aria-keyshortcuts="Control+K Meta+K">
            <Search aria-hidden />
            <span className="ph">{t("search.placeholder")}</span>
            <kbd>{isMac ? "⌘K" : "Ctrl K"}</kbd>
          </button>
          <div className="top-actions">
            <span className={`chip-btn hide-sm`} style={{ cursor: "default" }}>
              <span className="dot" style={{ background: sys.data?.branch === "main" ? "var(--ok)" : "var(--info)" }} />
              {sys.data?.branch === "main" ? t("info.production") : t("info.test")}
            </span>
            <div className="seg" role="group" aria-label={t("common.language")}>
              {(["en", "nl"] as Lang[]).map((l) => (
                <button key={l} type="button" aria-pressed={i18n.language === l} onClick={() => changeLang(l)}>
                  {l.toUpperCase()}
                </button>
              ))}
            </div>
            <div className="seg theme-seg hide-xs" role="group" aria-label={t("common.theme")}>
              {([["light", Sun], ["system", Monitor], ["dark", Moon]] as [ThemeChoice, typeof Sun][]).map(([c, Icon]) => (
                <button key={c} type="button" aria-pressed={theme === c} onClick={() => setTheme(c)} title={t(`theme.${c}`)} aria-label={t(`theme.${c}`)}>
                  <Icon aria-hidden />
                </button>
              ))}
            </div>
            <button type="button" className="icon-btn show-xs" style={{ marginLeft: 0 }} aria-label={t("common.theme")} title={t(`theme.${theme}`)}
              onClick={() => setTheme(theme === "light" ? "dark" : theme === "dark" ? "system" : "light")}>
              {theme === "light" ? <Sun /> : theme === "dark" ? <Moon /> : <Monitor />}
            </button>
            <Link to="/account" className="avatar" title={me.name || me.email} aria-label={t("nav.account")} style={{ textDecoration: "none" }}>
              {initials || <UserRound size={16} />}
            </Link>
            <button className="icon-btn" type="button" style={{ marginLeft: 0 }} aria-label={t("common.logout")} title={t("common.logout")} onClick={logout}>
              <LogOut />
            </button>
          </div>
        </header>
        <main className="content" id="content">
          <Suspense fallback={<Skeleton rows={6} />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      {info && <InfoModal onClose={() => setInfo(false)} />}
      {palette && (
        <CommandPalette
          nav={items}
          onClose={() => setPalette(false)}
          actions={{ setTheme, setLang: (l) => void changeLang(l), openInfo: () => setInfo(true), logout: () => void logout() }}
        />
      )}
    </div>
  );
}
