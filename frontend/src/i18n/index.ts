import i18n from "i18next";
import { initReactI18next } from "react-i18next";

export type Lang = "en" | "nl";

// Each language is its own file that is only downloaded when it is needed, so
// the first page load does not carry both translations.
const LOADERS: Record<Lang, () => Promise<{ default: Record<string, unknown> }>> = {
  en: () => import("./en.json"),
  nl: () => import("./nl.json"),
};

function initial(): Lang {
  try {
    const saved = localStorage.getItem("mf-lang");
    if (saved === "en" || saved === "nl") return saved;
  } catch {
    /* storage unavailable */
  }
  return navigator.language?.toLowerCase().startsWith("nl") ? "nl" : "en";
}

async function load(lang: Lang): Promise<void> {
  if (i18n.hasResourceBundle(lang, "translation")) return;
  const m = await LOADERS[lang]();
  i18n.addResourceBundle(lang, "translation", m.default, true, true);
}

/** Resolves once the starting language is loaded; render the app after this. */
export const i18nReady: Promise<void> = (async () => {
  const lng = initial();
  await i18n.use(initReactI18next).init({
    resources: {},
    partialBundledLanguages: true,
    lng,
    fallbackLng: "en",
    interpolation: { escapeValue: false },
  });
  await load(lng);
  if (lng !== "en") void load("en").catch(() => {}); // fallback for any missing text
  await i18n.changeLanguage(lng);
  document.documentElement.lang = lng;
})();

export function setLanguage(lang: Lang) {
  void load(lang).then(() => {
    if (i18n.language !== lang) i18n.changeLanguage(lang);
    document.documentElement.lang = lang;
  });
  try {
    localStorage.setItem("mf-lang", lang);
  } catch {
    /* ignore */
  }
}

export default i18n;
