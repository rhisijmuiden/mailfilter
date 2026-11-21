// First-time setup wizard. The Proxmox LXC template starts with only a login "admin" and a
// password shown on the console; this page walks the admin through the rest. It reuses the
// normal pages for each step, so everything can also be changed later in the menu.
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, CheckCircle2, Server } from "lucide-react";
import { useState, type FormEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { JobProgress } from "../components/JobProgress";
import { ErrorText, Field, Loading, PageHead } from "../components/ui";
import { api } from "../lib/api";
import type { Me } from "../lib/auth";
import type { Job } from "../lib/jobs";
import Backups from "./Backups";
import Domains from "./Domains";
import MailSending from "./MailSending";
import RelayAccess from "./RelayAccess";
import WebAccess from "./WebAccess";

type SetupState = {
  pending: boolean; hostname: string; admin_email: string; ipv4: string; ipv6: string;
  web_url: string; web_mode: string; container: string;
};

const STEPS = ["server", "web", "domains", "relay", "mail", "backups", "done"] as const;
type StepId = (typeof STEPS)[number];

const placeholder = (host: string) => !host || host.endsWith(".localdomain");

export default function Setup({ me }: { me: Me }) {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["setup"], queryFn: () => api.get<SetupState>("/system/setup") });
  const [active, setActive] = useState<StepId>("server");
  const [visited, setVisited] = useState<Set<StepId>>(new Set(["server"]));
  if (q.isLoading) return <section className="card"><div className="card-body"><Loading /></div></section>;
  if (q.error || !q.data) return <section className="card"><div className="card-body"><ErrorText error={q.error} /></div></section>;
  const d = q.data;
  const idx = STEPS.indexOf(active);
  const go = (s: StepId) => { setActive(s); setVisited(new Set([...visited, s])); window.scrollTo({ top: 0 }); };
  const next = () => go(STEPS[Math.min(idx + 1, STEPS.length - 1)]);
  const serverDone = !placeholder(d.hostname);

  let body: ReactNode;
  switch (active) {
    case "server": body = <ServerStep d={d} me={me} onDone={() => { void q.refetch(); next(); }} />; break;
    case "web": body = <WebAccess />; break;
    case "domains": body = <Domains me={me} />; break;
    case "relay": body = <RelayAccess me={me} />; break;
    case "mail": body = <MailSending />; break;
    case "backups": body = <Backups />; break;
    case "done": body = <DoneStep d={d} />; break;
  }

  return (
    <>
      <PageHead title={t("setup.title")} sub={t("setup.sub")} />
      <section className="wiz" aria-label={t("setup.title")}>
        <ol className="wiz-steps card">
          <li className="wiz-head"><span>{t("setup.steps")}</span></li>
          {STEPS.map((s, i) => {
            const done = s === "server" ? serverDone : s !== "done" && visited.has(s) && s !== active && STEPS.indexOf(s) < idx;
            return (
              <li key={s}>
                <button type="button" className={`wiz-step${s === active ? " on" : ""}`} aria-current={s === active ? "step" : undefined} onClick={() => go(s)}>
                  <span className={`wiz-num${done ? " done" : ""}`} aria-hidden>{done ? <Check /> : i + 1}</span>
                  <span>
                    <b>{t(`setup.s.${s}.title`)}</b>
                    <span className="cell-sub" style={{ display: "block" }}>{t(`setup.s.${s}.short`)}</span>
                  </span>
                </button>
              </li>
            );
          })}
        </ol>
        <div className="wiz-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
          {active !== "server" && active !== "done" && (
            <div className="callout" role="note"><span>{t(`setup.s.${active}.intro`)}</span></div>
          )}
          {body}
          {active !== "done" && (
            <div className="wiz-foot">
              <button type="button" className="btn" disabled={idx === 0} onClick={() => go(STEPS[idx - 1])}>{t("setup.prev")}</button>
              {active !== "server" && <button type="button" className="btn primary" onClick={next}>{t("setup.next")}</button>}
              {active === "server" && serverDone && <button type="button" className="btn" onClick={next}>{t("setup.skip")}</button>}
            </div>
          )}
        </div>
      </section>
    </>
  );
}

function ServerStep({ d, me, onDone }: { d: SetupState; me: Me; onDone: () => void }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const [host, setHost] = useState(placeholder(d.hostname) ? "" : d.hostname);
  const [email, setEmail] = useState(me.email.includes("@") ? me.email : "");
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [job, setJob] = useState<Job | null>(null);
  // The console showed a generated password for "admin": a new one is required then.
  const mustChangePassword = !me.email.includes("@");
  const save = useMutation({
    mutationFn: () => api.put<Job>("/system/setup/server", { hostname: host.trim(), admin_email: email.trim(), ...(pw ? { new_password: pw } : {}) }, { quiet: true }),
    onSuccess: (j) => {
      setJobId(j.id);
      void qc.invalidateQueries({ queryKey: ["me"] });
      if (j.state === "done" || j.state === "failed") setJob(j);
    },
  });
  const pwError = pw !== pw2 ? t("setup.pw_mismatch") : pw && pw.length < 10 ? t("setup.pw_short") : mustChangePassword && !pw ? t("setup.pw_required") : "";
  const submit = (e: FormEvent) => { e.preventDefault(); if (!pwError) save.mutate(); };
  const finished = job?.state === "done";

  return (
    <article className="card">
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
          <span className="mod-ico" aria-hidden><Server /></span>
          <div>
            <h2 style={{ margin: 0, fontSize: 20 }}>{t("setup.s.server.title")}</h2>
            <p className="muted" style={{ margin: "6px 0 0" }}>{t("setup.s.server.intro")}</p>
          </div>
        </div>
        <div className="callout" role="note">
          <span>
            {t("setup.addresses")} <span className="mono">{[d.ipv4, d.ipv6].filter(Boolean).join(" · ") || "-"}</span>
            {d.container === "lxc" && <> · {t("setup.proxmox_ip")}</>}
          </span>
        </div>
        {!jobId ? (
          <form className="grid" onSubmit={submit}>
            <Field label={t("setup.hostname")} hint={t("setup.hostname_hint")}>
              <input className="input mono" required value={host} onChange={(e) => setHost(e.target.value.toLowerCase())} placeholder="mailfilter01.example.nl" autoFocus />
            </Field>
            <Field label={t("setup.email")} hint={t("setup.email_hint")}>
              <input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="you@example.nl" autoComplete="username" />
            </Field>
            <div className="grid" style={{ gridTemplateColumns: "repeat(auto-fit,minmax(220px,1fr))" }}>
              <Field label={t("setup.password")} hint={mustChangePassword ? t("setup.password_hint_required") : t("setup.password_hint")}>
                <input className="input" type="password" value={pw} onChange={(e) => setPw(e.target.value)} autoComplete="new-password" />
              </Field>
              <Field label={t("setup.password2")}>
                <input className="input" type="password" value={pw2} onChange={(e) => setPw2(e.target.value)} autoComplete="new-password" />
              </Field>
            </div>
            {(pw || pw2 || mustChangePassword) && pwError && <div className="muted" role="alert">{pwError}</div>}
            <ErrorText error={save.error} />
            <div>
              <button className="btn primary" type="submit" disabled={save.isPending || !!pwError || !host || !email}>{t("setup.apply_server")}</button>
            </div>
          </form>
        ) : (
          <>
            {!job && <p className="muted" style={{ margin: 0 }}>{t("setup.running")}</p>}
            <JobProgress id={jobId} onDone={setJob} />
            {finished && (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button className="btn primary" type="button" onClick={onDone}>{t("setup.next")}</button>
              </div>
            )}
            {job?.state === "failed" && <button className="btn" type="button" onClick={() => { setJobId(null); setJob(null); save.reset(); }}>{t("setup.try_again")}</button>}
          </>
        )}
      </div>
    </article>
  );
}

function DoneStep({ d }: { d: SetupState }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const nav = useNavigate();
  const finish = useMutation({
    mutationFn: () => api.post("/system/setup/finish"),
    onSuccess: async () => { await qc.invalidateQueries({ queryKey: ["system-info"] }); nav("/"); },
  });
  return (
    <article className="card">
      <div className="card-body" style={{ display: "flex", flexDirection: "column", gap: 14 }}>
        <div style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
          <span className="mod-ico" aria-hidden style={{ color: "var(--ok)" }}><CheckCircle2 /></span>
          <div>
            <h2 style={{ margin: 0, fontSize: 20 }}>{t("setup.s.done.title")}</h2>
            <p className="muted" style={{ margin: "6px 0 0" }}>{t("setup.s.done.intro", { host: d.hostname })}</p>
          </div>
        </div>
        <ol className="wiz-list">
          <li>{t("setup.after.dns")}</li>
          <li>{t("setup.after.twofa")}</li>
          <li>{t("setup.after.golive")}</li>
        </ol>
        <ErrorText error={finish.error} />
        <div><button className="btn primary" type="button" disabled={finish.isPending} onClick={() => finish.mutate()}>{t("setup.finish")}</button></div>
      </div>
    </article>
  );
}
