// "Powered by RHIS" at the bottom right of every page.
//
// This mark is deliberately NOT a branding setting: Mailfilter is open source
// under GPLv3 with an additional term (section 7(b)) that this attribution must
// stay visible. See LICENSE and NOTICE before you change it.
export function PoweredBy() {
  return (
    <a className="powered-by" href="https://rhis.nl" target="_blank" rel="noopener" title="Mailfilter is made by RHIS">
      <svg viewBox="0 0 64 64" width="16" height="16" aria-hidden="true">
        <rect width="64" height="64" rx="16" fill="#14213D" />
        <rect x="27.5" y="31" width="9" height="22" rx="2.5" fill="#FF6B57" />
        <path d="M25.64 22.64A9 9 0 0 1 38.36 22.64M20.69 17.69A16 16 0 0 1 43.31 17.69" fill="none" stroke="#FF6B57" strokeWidth="4.5" strokeLinecap="round" />
      </svg>
      <span>
        Powered by <b>RHIS</b>
      </span>
    </a>
  );
}
