// Admin: own branding (name, colours, logos). Mailfilter is open source, so an
// install can look like its own product. Only the small "Powered by RHIS" mark
// at the bottom right is fixed (see components/PoweredBy.tsx and LICENSE).
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ImageUp, Info, Palette, RotateCcw, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { PoweredBy } from "../components/PoweredBy";
import { Card, ErrorText, Field, PageHead } from "../components/ui";
import { ApiError, api } from "../lib/api";
import { DEFAULT_BRANDING, onColor, useBranding, type Branding as B } from "../lib/branding";
import logoSvg from "../components/logo.svg?raw";

type Kind = "logo" | "logo_dark" | "favicon";
const MAX = 256 * 1024;
const HEX = /^#[0-9a-fA-F]{6}$/;

function readDataUrl(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    if (f.size > MAX) return reject(new ApiError(400, "too_large"));
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new ApiError(400, "bad_image"));
    r.readAsDataURL(f);
  });
}

function ColorField({ label, hint, value, onChange }: { label: string; hint: string; value: string; onChange: (v: string) => void }) {
  return (
    <Field label={label} hint={hint}>
      <span style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <input type="color" value={HEX.test(value) ? value : "#000000"} onChange={(e) => onChange(e.target.value.toUpperCase())}
          style={{ width: 44, height: 36, padding: 2, border: "1px solid var(--line)", borderRadius: "var(--r-sm)", background: "var(--surface-raised)", cursor: "pointer" }} />
        <input className="input mono" value={value} maxLength={7} onChange={(e) => onChange(e.target.value)} style={{ width: 110 }} aria-label={label} />
      </span>
    </Field>
  );
}

function ImageSlot({ kind, url, dark }: { kind: Kind; url: string | null; dark?: boolean }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const input = useRef<HTMLInputElement>(null);
  const [fileError, setFileError] = useState<unknown>(null);
  const done = (b: B) => qc.setQueryData(["branding"], b);
  const upload = useMutation({ mutationFn: (data: string) => api.put<B>(`/branding/${kind}`, { data }), onSuccess: done });
  const remove = useMutation({ mutationFn: () => api.del<B>(`/branding/${kind}`), onSuccess: done });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      <div>
        <strong>{t(`branding.${kind}`)}</strong>
        <div className="muted" style={{ fontSize: 12.5 }}>{t(`branding.${kind}_hint`)}</div>
      </div>
      <div style={{ display: "grid", placeItems: "center", minHeight: 76, padding: 12, borderRadius: "var(--r-md)", border: "1px dashed var(--line)",
        background: dark ? "#0E1626" : "#FFFFFF" }}>
        {url ? (
          <img src={url} alt="" style={{ maxWidth: "100%", maxHeight: kind === "favicon" ? 32 : 48 }} />
        ) : (
          <span className="muted" style={{ fontSize: 12.5, color: dark ? "#A3AEC4" : "#4F5B73" }}>{t("branding.default_used")}</span>
        )}
      </div>
      <ErrorText error={fileError ?? upload.error ?? remove.error} />
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <input ref={input} type="file" hidden accept=".svg,.png,.webp,.jpg,.jpeg,.ico,image/svg+xml,image/png,image/webp,image/jpeg,image/x-icon"
          onChange={(e) => {
            const f = e.target.files?.[0];
            e.target.value = "";
            if (!f) return;
            setFileError(null);
            readDataUrl(f).then((d) => upload.mutate(d), setFileError);
          }} />
        <button type="button" className="btn small" disabled={upload.isPending} onClick={() => input.current?.click()}>
          <ImageUp /> {url ? t("branding.replace") : t("branding.upload")}
        </button>
        {url && (
          <button type="button" className="btn small ghost" disabled={remove.isPending} onClick={() => remove.mutate()}>
            <Trash2 /> {t("branding.remove")}
          </button>
        )}
      </div>
    </div>
  );
}

export default function Branding() {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const brand = useBranding();
  const [name, setName] = useState(brand.name);
  const [accent, setAccent] = useState(brand.accent);
  const [primary, setPrimary] = useState(brand.primary);
  useEffect(() => {
    setName(brand.name);
    setAccent(brand.accent);
    setPrimary(brand.primary);
  }, [brand.name, brand.accent, brand.primary]);

  const save = useMutation({
    mutationFn: () => api.put<B>("/branding", { name: name.trim() || DEFAULT_BRANDING.name, accent, primary }),
    onSuccess: (b) => qc.setQueryData(["branding"], b),
  });
  const reset = useMutation({ mutationFn: () => api.post<B>("/branding/reset"), onSuccess: (b) => qc.setQueryData(["branding"], b) });
  const valid = HEX.test(accent) && HEX.test(primary) && name.trim().length > 0;
  const dirty = name !== brand.name || accent !== brand.accent || primary !== brand.primary;

  return (
    <>
      <PageHead
        title={t("branding.title")}
        sub={t("branding.sub")}
        actions={
          brand.custom && (
            <button type="button" className="btn" disabled={reset.isPending} onClick={() => window.confirm(t("branding.reset_confirm")) && reset.mutate()}>
              <RotateCcw /> {t("branding.reset")}
            </button>
          )
        }
      />
      <div className="grid halves">
        <Card title={t("branding.identity")}>
          <form
            style={{ display: "flex", flexDirection: "column", gap: 14 }}
            onSubmit={(e) => {
              e.preventDefault();
              if (valid) save.mutate();
            }}
          >
            <ErrorText error={save.error ?? reset.error} />
            <Field label={t("branding.name")} hint={t("branding.name_hint")}>
              <input className="input" value={name} maxLength={40} required onChange={(e) => setName(e.target.value)} />
            </Field>
            <ColorField label={t("branding.accent")} hint={t("branding.accent_hint")} value={accent} onChange={setAccent} />
            <ColorField label={t("branding.primary")} hint={t("branding.primary_hint")} value={primary} onChange={setPrimary} />
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <button className="btn primary" disabled={!valid || !dirty || save.isPending}>
                <Palette /> {t("common.save")}
              </button>
              {(accent !== DEFAULT_BRANDING.accent || primary !== DEFAULT_BRANDING.primary) && (
                <button type="button" className="btn ghost" onClick={() => { setAccent(DEFAULT_BRANDING.accent); setPrimary(DEFAULT_BRANDING.primary); }}>
                  {t("branding.house_colors")}
                </button>
              )}
            </div>
          </form>
        </Card>
        <Card title={t("branding.preview")}>
          <Preview name={name || DEFAULT_BRANDING.name} accent={HEX.test(accent) ? accent : brand.accent} primary={HEX.test(primary) ? primary : brand.primary} logo={brand.logo} />
        </Card>
      </div>
      <Card title={t("branding.images")}>
        <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))", gap: 20 }}>
          <ImageSlot kind="logo" url={brand.logo} />
          <ImageSlot kind="logo_dark" url={brand.logo_dark} dark />
          <ImageSlot kind="favicon" url={brand.favicon} />
        </div>
      </Card>
      <div className="callout">
        <Info aria-hidden />
        <span>{t("branding.powered_note")}</span>
      </div>
    </>
  );
}

// A small mock-up of the menu, a button and the fixed "Powered by RHIS" mark.
function Preview({ name, accent, primary, logo }: { name: string; accent: string; primary: string; logo: string | null }) {
  const { t } = useTranslation();
  return (
    <div style={{ position: "relative", border: "1px solid var(--line)", borderRadius: "var(--r-md)", overflow: "hidden", background: "#F6F7FB", color: "#14213D", minHeight: 210 }}>
      <div style={{ display: "grid", gridTemplateColumns: "150px 1fr", minHeight: 210 }}>
        <div style={{ background: "#fff", borderRight: "1px solid #DCE1EB", padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
          {logo ? (
            <img src={logo} alt="" style={{ maxWidth: 110, maxHeight: 32, objectFit: "contain", objectPosition: "left" }} />
          ) : (
            <span style={{ width: 84, display: "block" }} className="logo" dangerouslySetInnerHTML={{ __html: logoSvg }} />
          )}
          <span style={{ fontSize: 10, fontWeight: 700, letterSpacing: ".14em", textTransform: "uppercase", color: "#4F5B73" }}>{name}</span>
          <span style={{ marginTop: 8, padding: "6px 8px", borderRadius: 6, background: "#EDF0F6", fontSize: 12, fontWeight: 650, borderLeft: `3px solid ${accent}` }}>{t("nav.dashboard")}</span>
          <span style={{ padding: "6px 8px", fontSize: 12 }}>{t("nav.trace")}</span>
        </div>
        <div style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, alignItems: "flex-start" }}>
          <span style={{ fontWeight: 800, fontSize: 16 }}>{t("nav.dashboard")}</span>
          <span style={{ background: accent, color: onColor(accent), padding: "7px 12px", borderRadius: 6, fontSize: 12.5, fontWeight: 600 }}>{t("branding.sample_button")}</span>
          <span style={{ background: primary, color: onColor(primary), padding: "7px 12px", borderRadius: 6, fontSize: 12.5, fontWeight: 600 }}>{t("branding.sample_dark")}</span>
        </div>
      </div>
      <div style={{ position: "absolute", right: 0, bottom: 0, transform: "scale(.9)", transformOrigin: "bottom right" }} className="preview-powered">
        <PoweredBy />
      </div>
    </div>
  );
}
