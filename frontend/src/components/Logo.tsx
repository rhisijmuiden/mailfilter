import logo from "./logo.svg?raw";

export function Logo() {
  return <span className="logo" style={{ color: "var(--ink)", display: "block", width: 112 }} dangerouslySetInnerHTML={{ __html: logo }} />;
}
