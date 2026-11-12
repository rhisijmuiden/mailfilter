import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Info, LogOut, Menu, Moon, Sun, UserRound } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { setLanguage, type Lang } from "../i18n";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";
import type { Stats } from "../lib/trace";
import { GROUPS, NAV } from "../nav";
import { InfoModal, useSystemInfo } from "./InfoModal";
import { Logo } from "./Logo";

function useTheme(): [string, () => void] {
  const dark = () => {
    const set = document.documentElement.dataset.theme;
    return set ? set === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  };
  const [isDark, setDark] = useState(dark);
  const toggle = () => {
    const next = isDark ? "light" : "dark";
    document.documentElement.dataset.theme = next;
    try {
      localStorage.setItem("mf-theme", next);
    } catch {
      /* ignore */
    }
    setDark(!isDark);
  };
  return [isDark ? "dark" : "light", toggle];
}

export function Layout({ me }: { me: Me }) {
  const { t, i18n } = useTranslation();
  const qc = useQueryClient();
  const loc = useLocation();
  const [navOpen, setNavOpen] = useState(false);
  const [info, setInfo] = useState(false);
  const [theme, toggleTheme] = useTheme();
  const sys = useSystemInfo();
  const stats = useQuery({ queryKey: ["trace-stats"], queryFn: () => api.get<Stats>("/trace/stats?hours=24"), refetchInterval: 60_000 });
  const BADGES = { quarantine: stats.data?.quarantined_now };
  useEffect(() => setNavOpen(false), [loc.pathname]);

  const changeLang = async (lang: Lang) => {
    setLanguage(lang);
    const updated = await api.patch<Me>("/auth/me", { language: lang }).catch(() => null);
    if (updated) qc.setQueryData(["me"], updated);
  };
  const logout = async () => {
    await api.post("/auth/logout").catch(() => null);
    qc.clear();
    qc.setQueryData(["me"], null);
  };

  const items = NAV.filter((n) => !n.adminOnly || me.role === "admin");
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
            <button className="icon-btn" type="button" style={{ marginLeft: 0 }} aria-label={t("common.theme")} onClick={toggleTheme}>
              {theme === "dark" ? <Sun /> : <Moon />}
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
          <Outlet />
        </main>
      </div>
      {info && <InfoModal onClose={() => setInfo(false)} />}
    </div>
  );
}
