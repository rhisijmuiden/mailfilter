// Own branding (name, colours, logos), set by an admin under System > Branding.
// The default is the RHIS house style. The "Powered by RHIS" mark is not part
// of this: see components/PoweredBy.tsx and LICENSE.
import { useQuery } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "./api";

export type Branding = {
  name: string;
  accent: string;
  primary: string;
  logo: string | null;
  logo_dark: string | null;
  favicon: string | null;
  custom: boolean;
};

export const DEFAULT_BRANDING: Branding = {
  name: "Mailfilter",
  accent: "#FF6B57",
  primary: "#14213D",
  logo: null,
  logo_dark: null,
  favicon: null,
  custom: false,
};

export function useBranding(): Branding {
  const q = useQuery({ queryKey: ["branding"], queryFn: () => api.get<Branding>("/branding"), staleTime: 5 * 60_000 });
  return q.data ?? DEFAULT_BRANDING;
}

/** Dark or white text, whichever reads best on this colour (WCAG luminance). */
export function onColor(hex: string): string {
  const c = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255).map((v) => (v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4));
  const lum = 0.2126 * c[0]! + 0.7152 * c[1]! + 0.0722 * c[2]!;
  return lum > 0.179 ? "#14213D" : "#FFFFFF";
}

/** CSS that swaps the house-style colours for the chosen ones, in light and dark mode. */
export function brandingCss(b: Pick<Branding, "accent" | "primary">): string {
  if (b.accent.toUpperCase() === DEFAULT_BRANDING.accent && b.primary.toUpperCase() === DEFAULT_BRANDING.primary) return "";
  const light = `--koraal:${b.accent};--accent:${b.accent};--on-accent:${onColor(b.accent)};--nacht:${b.primary};` +
    `--accent-text:color-mix(in srgb,${b.accent} 62%,#000);--accent-soft:color-mix(in srgb,${b.accent} 14%,#fff)`;
  const dark = `--accent-text:color-mix(in srgb,${b.accent} 70%,#fff);--accent-soft:color-mix(in srgb,${b.accent} 22%,#16213A)`;
  // html:root outranks the house-style rules in app.css, whichever loads first.
  return `html:root{${light}}` +
    `@media (prefers-color-scheme:dark){html:root:not([data-theme="light"]){${dark}}}` +
    `html:root[data-theme="dark"]{${dark}}`;
}

const CSS_KEY = "mf-brand-css";

/** Applies colours, page title and favicon. public/theme.js re-applies the
 * saved colours before the first paint, so the page does not flash. */
export function useApplyBranding(b: Branding) {
  useEffect(() => {
    const css = brandingCss(b);
    let el = document.getElementById("mf-brand") as HTMLStyleElement | null;
    if (!el) {
      el = document.createElement("style");
      el.id = "mf-brand";
      document.head.appendChild(el);
    }
    el.textContent = css;
    try {
      if (css) localStorage.setItem(CSS_KEY, css);
      else localStorage.removeItem(CSS_KEY);
    } catch {
      /* storage unavailable */
    }
    document.title = b.name;
    const icon = document.querySelector<HTMLLinkElement>('link[rel="icon"]');
    if (icon) {
      icon.href = b.favicon ?? "/favicon.svg";
      if (b.favicon) icon.removeAttribute("type");
      else icon.type = "image/svg+xml";
    }
  }, [b]);
}
