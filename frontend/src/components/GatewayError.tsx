import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { ApiError } from "../lib/api";
import type { Problem } from "../lib/gateway";
import { ErrorText } from "./ui";

// Shows why a gateway change was refused: the validation problems in the
// user's language, or the server check that failed (nothing was changed then).
export default function GatewayError({ error }: { error: unknown }) {
  const { t, i18n } = useTranslation();
  if (!error) return null;
  if (error instanceof ApiError && error.info?.problems?.length) {
    const nl = i18n.language.startsWith("nl");
    return (
      <div className="callout warn" role="alert">
        <AlertTriangle aria-hidden />
        <div>
          <b>{t("gateway.refused")}</b>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18 }}>
            {(error.info.problems as Problem[]).map((p, i) => (
              <li key={i}>{nl ? p.nl : p.en}</li>
            ))}
          </ul>
        </div>
      </div>
    );
  }
  if (error instanceof ApiError && error.code === "apply_failed") {
    return (
      <div className="callout warn" role="alert">
        <AlertTriangle aria-hidden />
        <div style={{ minWidth: 0 }}>
          <b>{t("gateway.apply_failed", { step: error.info?.step || "" })}</b>
          {error.info?.output && <pre className="log" style={{ marginTop: 8, maxHeight: 200, overflow: "auto" }}>{error.info.output}</pre>}
        </div>
      </div>
    );
  }
  return <ErrorText error={error} />;
}
