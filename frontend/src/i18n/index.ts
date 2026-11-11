import i18n from "i18next";
import { initReactI18next } from "react-i18next";
import en from "./en.json";
import nl from "./nl.json";

export type Lang = "en" | "nl";

function initial(): Lang {
  try {
    const saved = localStorage.getItem("mf-lang");
    if (saved === "en" || saved === "nl") return saved;
  } catch {
    /* storage unavailable */
  }
  return navigator.language?.toLowerCase().startsWith("nl") ? "nl" : "en";
}

i18n.use(initReactI18next).init({
  resources: { en: { translation: en }, nl: { translation: nl } },
  lng: initial(),
  fallbackLng: "en",
  interpolation: { escapeValue: false },
});
document.documentElement.lang = i18n.language;

export function setLanguage(lang: Lang) {
  if (i18n.language !== lang) i18n.changeLanguage(lang);
  document.documentElement.lang = lang;
  try {
    localStorage.setItem("mf-lang", lang);
  } catch {
    /* ignore */
  }
}

export default i18n;
