import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { ModuleGate } from "./components/ModuleGate";
import { PoweredBy } from "./components/PoweredBy";
import { Loading, ToastProvider } from "./components/ui";
import { i18nReady } from "./i18n";
import { useMe } from "./lib/auth";
import { useApplyBranding, useBranding } from "./lib/branding";
import Login from "./pages/Login";
import TwoFactorGate from "./pages/TwoFactorGate";
import NotFound from "./pages/NotFound";
import { ROUTES } from "./routes";
import "./styles/index.css";

const ReportAction = lazy(() => import("./pages/ReportAction"));

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function App() {
  const me = useMe();
  if (me.isLoading) return <Loading />;
  if (!me.data) return <Login />;
  const user = me.data;
  if (user.two_factor_setup_required) return <TwoFactorGate me={user} />;
  return (
    <Routes>
      <Route element={<Layout me={user} />}>
        {ROUTES.filter((r) => (user.role === "reseller" ? r.reseller : !r.adminOnly || user.role === "admin")).map((r) => (
          <Route key={r.path} path={r.path} element={r.module ? <ModuleGate module={r.module} me={user}>{r.element(user)}</ModuleGate> : r.element(user)} />
        ))}
        {user.role === "reseller" && <Route path="/" element={<Navigate to="/usage" replace />} />}
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

// The links in the daily quarantine report work without logging in.
function Root() {
  useApplyBranding(useBranding());
  const m = window.location.pathname.match(/^\/report\/([^/]+)$/);
  return (
    <>
      {m ? (
        <Suspense fallback={<Loading />}>
          <ReportAction token={decodeURIComponent(m[1]!)} />
        </Suspense>
      ) : (
        <App />
      )}
      {/* Fixed on every page, also before login. Not a setting: see LICENSE. */}
      <PoweredBy />
    </>
  );
}

void i18nReady.finally(() =>
  createRoot(document.getElementById("root")!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <BrowserRouter>
          <ToastProvider>
            <Root />
          </ToastProvider>
        </BrowserRouter>
      </QueryClientProvider>
    </StrictMode>,
  ),
);
