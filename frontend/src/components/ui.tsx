import { AlertTriangle, CircleCheck, Info, X } from "lucide-react";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { ApiError, type SavedEvent } from "../lib/api";

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

export function Drawer({ title, sub, onClose, children, footer }: { title: ReactNode; sub?: ReactNode; onClose: () => void; children: ReactNode; footer?: ReactNode }) {
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
        {footer && <div className="drawer-foot">{footer}</div>}
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

/** Grey placeholder blocks that shimmer while data loads (instead of an empty screen). */
export function Skeleton({ rows = 4, cols = 3, label }: { rows?: number; cols?: number; label?: string }) {
  const { t } = useTranslation();
  const widths = [72, 100, 58, 86, 64, 93];
  return (
    <div className="sk-wrap" role="status" aria-live="polite" aria-label={label ?? t("common.loading")}>
      {Array.from({ length: rows }, (_, r) => (
        <div className="sk-row" key={r}>
          {Array.from({ length: cols }, (_, c) => (
            <i key={c} className="sk" style={c === 1 ? { flex: 1, maxWidth: `${widths[(r + c) % widths.length]}%` } : { width: c === 0 ? 92 : 76 }} />
          ))}
        </div>
      ))}
    </div>
  );
}

/** Placeholder for a row of KPI tiles. */
export function SkeletonTiles({ count = 4 }: { count?: number }) {
  return (
    <div className="grid kpis" aria-hidden>
      {Array.from({ length: count }, (_, i) => (
        <div key={i} className="card kpi">
          <i className="sk" style={{ width: "45%" }} />
          <i className="sk" style={{ width: "60%", height: 26 }} />
          <i className="sk" style={{ width: "80%" }} />
        </div>
      ))}
    </div>
  );
}

export function Loading() {
  return <Skeleton />;
}

export type ToastKind = "success" | "error" | "info";
type ToastCtx = (msg: string, kind?: ToastKind) => void;
type ToastItem = { id: number; msg: string; kind: ToastKind; leaving?: boolean };
const ToastContext = createContext<ToastCtx>(() => {});
export const useToast = () => useContext(ToastContext);

const TOAST_ICON = { success: CircleCheck, error: AlertTriangle, info: Info };
const TOAST_MS = 3800;

export function ToastProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation();
  const [items, setItems] = useState<ToastItem[]>([]);
  const seq = useRef(0);
  const lastShown = useRef(0);
  const dismiss = useCallback((id: number) => {
    setItems((all) => all.map((x) => (x.id === id ? { ...x, leaving: true } : x)));
    window.setTimeout(() => setItems((all) => all.filter((x) => x.id !== id)), 180);
  }, []);
  const show = useCallback<ToastCtx>((msg, kind = "success") => {
    const id = ++seq.current;
    lastShown.current = performance.now();
    setItems((all) => [...all.filter((x) => x.msg !== msg).slice(-3), { id, msg, kind }]);
    window.setTimeout(() => dismiss(id), kind === "error" ? TOAST_MS * 1.6 : TOAST_MS);
  }, [dismiss]);

  // A change was saved: confirm it, unless the page shows its own message.
  useEffect(() => {
    const onSaved = (e: Event) => {
      const at = performance.now();
      const method = (e as SavedEvent).detail.method;
      window.setTimeout(() => {
        if (lastShown.current < at) show(t(method === "DELETE" ? "common.deleted" : "common.saved"));
      }, 350);
    };
    window.addEventListener("mf:saved", onSaved);
    return () => window.removeEventListener("mf:saved", onSaved);
  }, [show, t]);

  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts" aria-live="polite">
        {items.map((x) => {
          const Icon = TOAST_ICON[x.kind];
          return (
            <div key={x.id} className={`toast t-${x.kind}${x.leaving ? " leaving" : ""}`} role={x.kind === "error" ? "alert" : "status"}>
              <span className="ti"><Icon aria-hidden /></span>
              <span className="tm">{x.msg}</span>
              <button type="button" className="tx" onClick={() => dismiss(x.id)} aria-label={t("common.close")}><X aria-hidden /></button>
            </div>
          );
        })}
      </div>
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
