// Takes the README screenshots from a running demo (see README.md in this folder).
// Usage: node tools/screenshots/shoot.mjs [base-url] [out-dir] [only,these,names]
import { createRequire } from "node:module";
// Uses the Playwright that is installed globally (NODE_PATH=$(npm root -g)) or in frontend/.
const { chromium } = createRequire(import.meta.url)("playwright");

const BASE = process.argv[2] || "http://127.0.0.1:4173";
const OUT = process.argv[3] || "docs/screenshots";
const ONLY = (process.argv[4] || "").split(",").filter(Boolean);
const USER = process.env.DEMO_USER || "reinder@rhis.example";
const CUSTOMER = process.env.DEMO_CUSTOMER || "info@bakkerij-jansen.example";
const PASSWORD = process.env.DEMO_PASSWORD || "demo-password-123";

// name, path, optional action on the page before the shot
export const SHOTS = [
  ["dashboard", "/"],
  ["trace", "/trace?hours=24"],
  ["trace-detail", "/trace", async (p) => { await p.getByText("p.smit@leverancier.example").first().click(); }],
  ["trace-problem", "/trace", async (p) => { await p.getByText("j.peters@ziggo.nl").first().click(); }],
  ["spam-check", "/trace?status=quarantined", async (p) => {
    await p.locator("table tbody tr").first().click(); await p.waitForTimeout(600);
    await p.getByText(/Spam check/i).first().scrollIntoViewIfNeeded(); }],
  ["quarantine", "/quarantine"],
  ["quarantine-detail", "/quarantine", async (p) => { await p.locator("table tbody tr").first().click(); }],
  ["mailtrap", "/mailtrap"],
  ["archive", "/archive"],
  ["lists", "/lists"],
  ["spam-reports", "/spam-reports"],
  ["quarantine-report", "/quarantine/report"],
  ["dmarc", "/dmarc"],
  ["link-clicks", "/links"],
  ["domains", "/domains"],
  ["domain-detail", "/domains?open=bakkerij-jansen.example"],
  ["relay-domains", "/domains", async (p) => { await p.getByRole("tab", { name: /Relay/ }).first().click(); }],
  ["relay", "/relay"],
  ["cloud", "/cloud"],
  ["rules", "/rules"],
  ["throttling", "/throttling"],
  ["dns-spf", "/dns", async (p) => { await p.getByRole("tab", { name: /SPF/ }).first().click(); }],
  ["dns-dkim", "/dns", async (p) => { await p.getByRole("tab", { name: /DKIM/ }).first().click(); }],
  ["dns-bimi", "/dns", async (p) => { await p.getByRole("tab", { name: /BIMI/ }).first().click(); }],
  ["monthly-report", "/reports/monthly"],
  ["usage", "/usage"],
  ["users", "/users"],
  ["security", "/system/security"],
  ["audit", "/audit"],
  ["api", "/system/api"],
  ["api-docs", "/system/api", async (p) => { await p.getByRole("tab", { name: /Documentation/ }).first().click(); }],
  ["mail-sending", "/system/mail"],
  ["notifications", "/system/notifications"],
  ["cluster", "/system/cluster"],
  ["backups", "/system/backups"],
  ["import-pmg", "/system/import-pmg"],
  ["branding", "/system/branding"],
  ["info", "/", async (p) => { await p.locator(".info-btn").click(); await p.waitForTimeout(400);
    const b = p.getByRole("button", { name: /Check for updates/ }); if (await b.count()) await b.first().click(); }],
  ["search", "/", async (p) => { await p.keyboard.press("Control+K"); await p.keyboard.type("bakkerij", { delay: 40 }); }],
  ["account", "/account"],
];

async function login(ctx, email) {
  const r = await ctx.request.post(`${BASE}/api/auth/login`, {
    data: { email, password: PASSWORD }, headers: { "X-Requested-With": "mailfilter" } });
  if (!r.ok()) throw new Error(`login ${email}: ${r.status()} ${await r.text()}`);
}

async function shoot(ctx, list, suffix = "") {
  const page = await ctx.newPage();
  for (const [name, path, act] of list) {
    if (ONLY.length && !ONLY.includes(name + suffix)) continue;
    try {
      await page.goto(BASE + path, { waitUntil: "load" });
      await page.waitForLoadState("networkidle", { timeout: 8000 }).catch(() => {});
      await page.waitForTimeout(900);
      if (act) { await act(page); await page.waitForTimeout(900); }
      await page.evaluate(() => document.activeElement && document.activeElement.blur && document.activeElement.blur());
      await page.screenshot({ path: `${OUT}/${name}${suffix}.png` });
      console.log("ok", name + suffix);
    } catch (e) { console.log("FAIL", name + suffix, String(e).split("\n")[0]); }
  }
  await page.close();
}

const browser = await chromium.launch();
const opts = { viewport: { width: 1440, height: 900 }, deviceScaleFactor: 1, locale: "en-GB", colorScheme: "light", timezoneId: "Europe/Amsterdam" };
const admin = await browser.newContext(opts);
await login(admin, USER);
await shoot(admin, SHOTS);
// The same dashboard in dark mode and Dutch, and on a phone.
const dark = await browser.newContext({ ...opts, colorScheme: "dark" });
await login(dark, USER);
await dark.addInitScript(() => { localStorage.setItem("mf-theme", "dark"); });
await shoot(dark, [["dashboard", "/"], ["trace-detail", "/trace", SHOTS[2][2]]], "-dark");
const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true, locale: "nl-NL" });
await login(phone, USER);
await shoot(phone, [["dashboard", "/"], ["quarantine", "/quarantine"]], "-phone");
const cust = await browser.newContext(opts);
await login(cust, CUSTOMER);
await shoot(cust, [["dashboard", "/"], ["quarantine", "/quarantine"]], "-customer");
const login0 = await browser.newContext(opts);
await shoot(login0, [["login", "/login"]]);
await browser.close();
