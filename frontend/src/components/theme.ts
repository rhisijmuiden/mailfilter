// Light, dark or "system" (follow the computer). Saved in this browser;
// public/theme.js applies it before the first paint so the page does not flash.
import { useCallback, useState } from "react";

export type ThemeChoice = "light" | "dark" | "system";

function saved(): ThemeChoice {
  try {
    const v = localStorage.getItem("mf-theme");
    if (v === "light" || v === "dark") return v;
  } catch {
    /* storage unavailable */
  }
  return "system";
}

export function applyTheme(choice: ThemeChoice) {
  const root = document.documentElement;
  if (choice === "system") delete root.dataset.theme;
  else root.dataset.theme = choice;
  try {
    if (choice === "system") localStorage.removeItem("mf-theme");
    else localStorage.setItem("mf-theme", choice);
  } catch {
    /* storage unavailable */
  }
}

export function useTheme(): [ThemeChoice, (c: ThemeChoice) => void] {
  const [choice, setChoice] = useState<ThemeChoice>(saved);
  const set = useCallback((c: ThemeChoice) => {
    applyTheme(c);
    setChoice(c);
  }, []);
  return [choice, set];
}
