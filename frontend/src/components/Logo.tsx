import { useBranding } from "../lib/branding";
import logo from "./logo.svg?raw";

// The product logo: the admin's own logo (System > Branding), or the RHIS logo.
export function Logo() {
  const b = useBranding();
  if (b.logo)
    return (
      <span className="logo brand-logo">
        <img className={b.logo_dark ? "only-light" : undefined} src={b.logo} alt={b.name} />
        {b.logo_dark && <img className="only-dark" src={b.logo_dark} alt={b.name} />}
      </span>
    );
  return <span className="logo" style={{ color: "var(--ink)", display: "block", width: 112 }} dangerouslySetInnerHTML={{ __html: logo }} />;
}

// The product name: the admin's own name (System > Branding), or "Mailfilter".
export function ProductName() {
  return <>{useBranding().name}</>;
}
