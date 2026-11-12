// System > E-mail sending: how Mailfilter sends its own mail (alerts, reports).
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Send } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Card, ErrorText, Field, Loading, PageHead, useToast } from "../components/ui";
import { api } from "../lib/api";
import { useMe } from "../lib/auth";

type Smtp = {
  mode: "local" | "smtp"; host: string; port: number; security: "starttls" | "tls" | "none"; username: string;
  has_password: boolean; verify_tls: boolean; from_address: string; from_name: string; reply_to: string; default_from: string;
};
type Form = Omit<Smtp, "has_password" | "default_from"> & { password: string; clear_password: boolean };

const PORTS = { starttls: 587, tls: 465, none: 25 } as const;

export default function MailSending() {
  const { t } = useTranslation();
  const q = useQuery({ queryKey: ["smtp"], queryFn: () => api.get<Smtp>("/settings/smtp") });
  return (
    <>
      <PageHead title={t("smtp.title")} sub={t("smtp.sub")} />
      {q.isLoading ? <Loading /> : q.data ? <SmtpForm data={q.data} /> : <ErrorText error={q.error} />}
    </>
  );
}

function SmtpForm({ data }: { data: Smtp }) {
  const { t } = useTranslation();
  const qc = useQueryClient();
  const toast = useToast();
  const me = useMe();
  const init = (d: Smtp): Form => ({ ...d, password: "", clear_password: false });
  const [f, setF] = useState<Form>(init(data));
  const [to, setTo] = useState(me.data?.email ?? "");
  useEffect(() => setF(init(data)), [data]);
  useEffect(() => {
    if (!to && me.data?.email) setTo(me.data.email);
  }, [me.data?.email, to]);
  const body = { ...f, password: f.password || null };
  const save = useMutation({
    mutationFn: () => api.put<Smtp>("/settings/smtp", body),
    onSuccess: (d) => {
      qc.setQueryData(["smtp"], d);
      toast(t("common.saved"));
    },
  });
  const test = useMutation({ mutationFn: () => api.post<{ ok: boolean }>("/settings/smtp/test", { ...body, to }) });
  const set = <K extends keyof Form>(k: K, v: Form[K]) => setF({ ...f, [k]: v });
  const custom = f.mode === "smtp";

  return (
    <div className="grid two">
      <Card title={t("smtp.how")}>
        <form
          className="form-grid"
          onSubmit={(e) => {
            e.preventDefault();
            save.mutate();
          }}
        >
          <div className="full channel" role="radiogroup">
            {(["local", "smtp"] as const).map((m) => (
              <label key={m}>
                <span className="nm">
                  <input type="radio" name="mode" checked={f.mode === m} onChange={() => set("mode", m)} />
                  {t(`smtp.mode_${m}`)}
                </span>
                <span className="ds">{t(`smtp.mode_${m}_d`)}</span>
              </label>
            ))}
          </div>
          {custom && (
            <>
              <Field label={t("smtp.host")}>
                <input className="input" required placeholder="smtp.example.org" value={f.host} onChange={(e) => set("host", e.target.value)} />
              </Field>
              <Field label={t("smtp.security")}>
                <select
                  className="select"
                  value={f.security}
                  onChange={(e) => {
                    const sec = e.target.value as Form["security"];
                    setF({ ...f, security: sec, port: ([587, 465, 25] as number[]).includes(f.port) ? PORTS[sec] : f.port });
                  }}
                >
                  <option value="starttls">{t("smtp.sec_starttls")}</option>
                  <option value="tls">{t("smtp.sec_tls")}</option>
                  <option value="none">{t("smtp.sec_none")}</option>
                </select>
              </Field>
              <Field label={t("smtp.port")}>
                <input className="input" type="number" min={1} max={65535} required value={f.port} onChange={(e) => set("port", +e.target.value)} />
              </Field>
              <Field label={t("smtp.username")} hint={t("smtp.username_d")}>
                <input className="input" autoComplete="off" value={f.username} onChange={(e) => set("username", e.target.value)} />
              </Field>
              <Field label={t("smtp.password")} hint={data.has_password ? t("smtp.password_keep") : undefined} full>
                <input
                  className="input"
                  type="password"
                  autoComplete="new-password"
                  value={f.password}
                  placeholder={data.has_password ? "••••••••" : ""}
                  onChange={(e) => setF({ ...f, password: e.target.value, clear_password: false })}
                />
              </Field>
              {data.has_password && (
                <label className="check full">
                  <input type="checkbox" checked={f.clear_password} onChange={(e) => setF({ ...f, clear_password: e.target.checked, password: "" })} />
                  <span>{t("smtp.clear_password")}</span>
                </label>
              )}
              <label className="check full">
                <input type="checkbox" checked={f.verify_tls} onChange={(e) => set("verify_tls", e.target.checked)} disabled={f.security === "none"} />
                <span>{t("smtp.verify_tls")}</span>
              </label>
              {(f.security === "none" || !f.verify_tls) && (
                <div className="full callout warn"><span>{t("smtp.insecure")}</span></div>
              )}
            </>
          )}
          <Field label={t("smtp.from_address")} hint={t("smtp.from_default", { address: data.default_from })}>
            <input className="input" type="email" value={f.from_address} placeholder={data.default_from} onChange={(e) => set("from_address", e.target.value)} />
          </Field>
          <Field label={t("smtp.from_name")}>
            <input className="input" value={f.from_name} onChange={(e) => set("from_name", e.target.value)} />
          </Field>
          <Field label={t("smtp.reply_to")} full>
            <input className="input" type="email" value={f.reply_to} onChange={(e) => set("reply_to", e.target.value)} />
          </Field>
          <div className="full"><ErrorText error={save.error} /></div>
          <div className="full">
            <button className="btn primary" disabled={save.isPending}>{t("common.save")}</button>
          </div>
        </form>
      </Card>
      <div className="grid" style={{ alignContent: "start" }}>
        <Card title={t("smtp.test")}>
          <form
            style={{ display: "flex", flexDirection: "column", gap: 12 }}
            onSubmit={(e) => {
              e.preventDefault();
              test.mutate();
            }}
          >
            <p className="muted" style={{ margin: 0, fontSize: 13 }}>{t("smtp.test_d")}</p>
            <Field label={t("smtp.test_to")}>
              <input className="input" type="email" required value={to} onChange={(e) => setTo(e.target.value)} />
            </Field>
            <ErrorText error={test.error} />
            {test.isSuccess && <div className="callout"><span>{t("smtp.test_ok", { to })}</span></div>}
            <div>
              <button className="btn" disabled={test.isPending}>
                <Send /> {test.isPending ? t("smtp.testing") : t("smtp.test_send")}
              </button>
            </div>
          </form>
        </Card>
        <Card title={t("smtp.modern")}>
          <ul style={{ margin: 0, paddingLeft: 18, display: "flex", flexDirection: "column", gap: 6, fontSize: 13 }}>
            <li>{t("smtp.modern_tls")}</li>
            <li>{t("smtp.modern_auth")}</li>
            <li>{t("smtp.modern_headers")}</li>
            <li>{t("smtp.modern_local")}</li>
          </ul>
        </Card>
      </div>
    </div>
  );
}
