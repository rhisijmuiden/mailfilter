import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { PageHead } from "../components/ui";

export default function NotFound() {
  const { t } = useTranslation();
  return (
    <>
      <PageHead title={t("notfound.title")} />
      <div>
        <Link className="btn" to="/">{t("notfound.home")}</Link>
      </div>
    </>
  );
}
