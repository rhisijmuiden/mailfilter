import { X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../lib/api";

export function PageHead({ title, sub, actions }: { title: string; sub?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1 className="display">{title}</h1>
        {sub && <p>{sub}</p>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function Card({ title, right, children, flush }: { title?: ReactNode; right?: ReactNode; children: ReactNode; flush?: boolean }) {
  return (
    <section className="card">
      {(title || right) && (
        <div className="card-head">
          {title && <h2>{title}</h2>}
          {right && <div className="right">{right}</div>}
        </div>
      )}
      {flush ? children : <div className="card-body">{children}</div>}
    </section>
  );
}

function useEscape(onClose: () => void) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
}

export function Modal({ title, onClose, children, icon }: { title: string; onClose: () => void; children: ReactNode; icon?: ReactNode }) {
  const { t } = useTranslation();
  useEscape(onClose);
  return (
    <div className="modal-scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title}>
        <div className="modal-head">
          {icon}
          <h2 className="display">{title}</h2>
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}>
            <X />
          </button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function Drawer({ title, sub, onClose, children }: { title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode }) {
  const { t } = useTranslation();
  useEscape(onClose);
  return (
    <div className="scrim" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <aside className="drawer" role="dialog" aria-modal="true">
        <div className="drawer-head">
          <div style={{ minWidth: 0 }}>
            <h2>{title}</h2>
            {sub && <div className="muted" style={{ fontSize: 13 }}>{sub}</div>}
          </div>
          <button className="icon-btn" onClick={onClose} aria-label={t("common.close")}>
            <X />
          </button>
        </div>
        <div className="drawer-body">{children}</div>
      </aside>
    </div>
  );
}

export function Field({ label, hint, children, full }: { label: string; hint?: ReactNode; children: ReactNode; full?: boolean }) {
  return (
    <div className={`field${full ? " full" : ""}`}>
      <label>
        <span style={{ display: "block", marginBottom: 6 }}>{label}</span>
        {children}
      </label>
      {hint && <span className="hint">{hint}</span>}
    </div>
  );
}

export function ErrorText({ error }: { error: unknown }) {
  const { t } = useTranslation();
  if (!error) return null;
  let msg = t("errors.error");
  if (error instanceof ApiError) {
    // "code:value"; the value may itself contain colons (e.g. a server message)
    const [code, ...rest] = error.code.split(":");
    const value = rest.join(":");
    msg = t(`errors.${code}`, { value, defaultValue: t("errors.error") });
  }
  return (
    <div className="callout warn" role="alert">
      <span>{msg}</span>
    </div>
  );
}

export function Loading() {
  const { t } = useTranslation();
  return <div className="empty">{t("common.loading")}</div>;
}

type ToastCtx = (msg: string) => void;
const ToastContext = createContext<ToastCtx>(() => {});
export const useToast = () => useContext(ToastContext);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [msg, setMsg] = useState<string | null>(null);
  const timer = useRef<number>();
  const show = useCallback((m: string) => {
    setMsg(m);
    window.clearTimeout(timer.current);
    timer.current = window.setTimeout(() => setMsg(null), 3200);
  }, []);
  return (
    <ToastContext.Provider value={show}>
      {children}
      {msg && (
        <div className="toast" role="status">
          {msg}
        </div>
      )}
    </ToastContext.Provider>
  );
}

export function formatDate(iso: string | null | undefined, lang: string, withTime = true): string {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(lang === "nl" ? "nl-NL" : "en-GB", {
    day: "numeric", month: "short", year: "numeric",
    ...(withTime ? { hour: "2-digit", minute: "2-digit" } : {}),
  });
}
