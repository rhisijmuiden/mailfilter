// One table component for list pages: a filter box, sortable columns, a column
// picker (remembered per table in this browser) and cards instead of a table on phones.
import { ArrowDown, ArrowUp, ArrowUpDown, Columns3, Search } from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Skeleton } from "./ui";

export type Column<T> = {
  key: string;
  label: string;
  render: (row: T) => ReactNode;
  /** Value to sort on; columns without it cannot be sorted. */
  sort?: (row: T) => string | number | null | undefined;
  /** Text the filter box searches; defaults to the sort value. */
  text?: (row: T) => string;
  /** Hidden until the user switches it on in the column picker. */
  optional?: boolean;
  /** Cannot be switched off. */
  always?: boolean;
  align?: "right";
  nowrap?: boolean;
  width?: number | string;
  /** Place on a phone card: title (top left), aside (top right), row (default) or hide. */
  mobile?: "title" | "aside" | "row" | "hide";
  /** Show the column name before the value on a phone card. */
  mobileLabel?: boolean;
};

type SortState = { key: string; dir: 1 | -1 } | null;

type Props<T> = {
  id: string;
  rows: T[] | undefined;
  rowKey: (row: T) => string | number;
  columns: Column<T>[];
  loading?: boolean;
  empty?: ReactNode;
  onRowClick?: (row: T) => void;
  /** Server-side search: the page owns the query. Without it the table filters what it has. */
  search?: { value: string; onChange: (v: string) => void; placeholder: string; autoFocus?: boolean };
  filterPlaceholder?: string;
  noFilter?: boolean;
  /** Extra filters (selects, buttons) next to the search box. */
  tools?: ReactNode;
  footer?: ReactNode;
  /** Buttons at the bottom of a phone card (e.g. Release / Block). */
  cardActions?: (row: T) => ReactNode;
  defaultSort?: SortState;
  rowClass?: (row: T) => string | undefined;
};

export function useMediaQuery(query: string): boolean {
  const get = () => typeof window !== "undefined" && window.matchMedia(query).matches;
  const [match, setMatch] = useState(get);
  useEffect(() => {
    const m = window.matchMedia(query);
    const on = () => setMatch(m.matches);
    on();
    m.addEventListener("change", on);
    return () => m.removeEventListener("change", on);
  }, [query]);
  return match;
}

function loadCols(id: string): Record<string, boolean> {
  try {
    return JSON.parse(localStorage.getItem(`mf-cols:${id}`) || "{}") || {};
  } catch {
    return {};
  }
}

function compare(a: unknown, b: unknown): number {
  if (a == null || a === "") return b == null || b === "" ? 0 : 1;
  if (b == null || b === "") return -1;
  if (typeof a === "number" && typeof b === "number") return a - b;
  return String(a).localeCompare(String(b), undefined, { numeric: true, sensitivity: "base" });
}

export function DataTable<T>(p: Props<T>) {
  const { t } = useTranslation();
  const phone = useMediaQuery("(max-width: 700px)");
  const [filter, setFilter] = useState("");
  const [sort, setSort] = useState<SortState>(p.defaultSort ?? null);
  const [shown, setShown] = useState<Record<string, boolean>>(() => loadCols(p.id));
  const [picker, setPicker] = useState(false);
  const pickRef = useRef<HTMLDivElement>(null);

  const visible = (c: Column<T>) => c.always || (shown[c.key] ?? !c.optional);
  const cols = p.columns.filter(visible);
  const toggle = (key: string, on: boolean) => {
    const next = { ...shown, [key]: on };
    setShown(next);
    try {
      localStorage.setItem(`mf-cols:${p.id}`, JSON.stringify(next));
    } catch {
      /* storage unavailable */
    }
  };

  useEffect(() => {
    if (!picker) return;
    const close = (e: MouseEvent | KeyboardEvent) => {
      if (e instanceof KeyboardEvent ? e.key === "Escape" : !pickRef.current?.contains(e.target as Node)) setPicker(false);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", close);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", close);
    };
  }, [picker]);

  const rows = useMemo(() => {
    let list = p.rows ?? [];
    const q = filter.trim().toLowerCase();
    if (q && !p.search) {
      list = list.filter((r) =>
        p.columns.some((c) => {
          const v = c.text ? c.text(r) : c.sort?.(r);
          return v != null && String(v).toLowerCase().includes(q);
        }),
      );
    }
    if (sort) {
      const col = p.columns.find((c) => c.key === sort.key);
      if (col?.sort) list = [...list].sort((a, b) => compare(col.sort!(a), col.sort!(b)) * sort.dir);
    }
    return list;
  }, [p.rows, p.columns, p.search, filter, sort]);

  const cycleSort = (key: string) =>
    setSort((s) => (!s || s.key !== key ? { key, dir: 1 } : s.dir === 1 ? { key, dir: -1 } : null));

  const searchBox = p.search ? (
    <label className="search">
      <Search aria-hidden />
      <input type="search" value={p.search.value} onChange={(e) => p.search!.onChange(e.target.value)} placeholder={p.search.placeholder}
        aria-label={p.search.placeholder} autoFocus={p.search.autoFocus && !phone} />
    </label>
  ) : !p.noFilter ? (
    <label className="search">
      <Search aria-hidden />
      <input type="search" value={filter} onChange={(e) => setFilter(e.target.value)} placeholder={p.filterPlaceholder ?? t("table.filter")}
        aria-label={p.filterPlaceholder ?? t("table.filter")} />
    </label>
  ) : null;

  const pickable = p.columns.filter((c) => !c.always);
  const clickProps = (r: T) =>
    p.onRowClick
      ? { tabIndex: 0, onClick: () => p.onRowClick!(r), onKeyDown: (e: React.KeyboardEvent) => e.key === "Enter" && p.onRowClick!(r) }
      : {};

  let body: ReactNode;
  if (p.loading && !p.rows) body = <Skeleton rows={5} cols={Math.min(cols.length, 4)} />;
  else if (!rows.length) body = <div className="empty">{filter ? t("table.no_match") : p.empty ?? t("table.empty")}</div>;
  else if (phone) {
    body = (
      <div className="dt-cards">
        {rows.map((r) => {
          const title = cols.find((c) => c.mobile === "title") ?? cols[0];
          const aside = cols.find((c) => c.mobile === "aside");
          const rest = cols.filter((c) => c !== title && c !== aside && c.mobile !== "hide");
          return (
            <article key={p.rowKey(r)} className={`dt-card${p.onRowClick ? " click" : ""}`} {...clickProps(r)}>
              <div className="top">
                <div>{title.render(r)}</div>
                {aside && <div className="aside">{aside.render(r)}</div>}
              </div>
              {rest.map((c) => (
                <div key={c.key} className="row">
                  {c.mobileLabel && <span className="lbl">{c.label}</span>}
                  {c.render(r)}
                </div>
              ))}
              {p.cardActions && (
                <div className="acts" onClick={(e) => e.stopPropagation()} onKeyDown={(e) => e.stopPropagation()}>
                  {p.cardActions(r)}
                </div>
              )}
            </article>
          );
        })}
      </div>
    );
  } else {
    body = (
      <div className="tbl-wrap">
        <table>
          <thead>
            <tr>
              {cols.map((c) => {
                const dir = sort?.key === c.key ? sort.dir : 0;
                return (
                  <th key={c.key} className={c.sort ? "sortable" : undefined} style={{ textAlign: c.align, width: c.width }}
                    aria-sort={dir === 1 ? "ascending" : dir === -1 ? "descending" : c.sort ? "none" : undefined}>
                    {c.sort ? (
                      <button type="button" onClick={() => cycleSort(c.key)}>
                        {c.label}
                        {dir === 1 ? <ArrowUp aria-hidden /> : dir === -1 ? <ArrowDown aria-hidden /> : <ArrowUpDown aria-hidden />}
                      </button>
                    ) : (
                      c.label
                    )}
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={p.rowKey(r)} className={[p.onRowClick ? "click" : "", p.rowClass?.(r) ?? ""].join(" ").trim() || undefined} {...clickProps(r)}>
                {cols.map((c) => (
                  <td key={c.key} className={c.nowrap ? "nowrap" : undefined} style={{ textAlign: c.align }}>
                    {c.render(r)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }

  return (
    <>
      <div className="dt-tools">
        {searchBox}
        {p.tools}
        {pickable.length > 0 && !phone && (
          <div className="colpick spacer" ref={pickRef}>
            <button type="button" className="btn small" aria-expanded={picker} onClick={() => setPicker(!picker)}>
              <Columns3 aria-hidden /> {t("table.columns")}
            </button>
            {picker && (
              <div className="colmenu" role="group" aria-label={t("table.columns_show")}>
                <span className="h">{t("table.columns_show")}</span>
                {pickable.map((c) => (
                  <label key={c.key} className="check">
                    <input type="checkbox" checked={visible(c)} onChange={(e) => toggle(c.key, e.target.checked)} />
                    <span>{c.label}</span>
                  </label>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
      {body}
      {p.footer && <div className="dt-foot">{p.footer}</div>}
    </>
  );
}
