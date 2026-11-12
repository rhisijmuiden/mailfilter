import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { Loading, ToastProvider } from "./components/ui";
import "./i18n";
import { useMe } from "./lib/auth";
import Login from "./pages/Login";
import NotFound from "./pages/NotFound";
import ReportAction from "./pages/ReportAction";
import { ROUTES } from "./routes";
import "./styles/index.css";

const queryClient = new QueryClient({
  defaultOptions: { queries: { retry: 1, refetchOnWindowFocus: false } },
});

function App() {
  const me = useMe();
  if (me.isLoading) return <Loading />;
  if (!me.data) return <Login />;
  const user = me.data;
  return (
    <Routes>
      <Route element={<Layout me={user} />}>
        {ROUTES.filter((r) => !r.adminOnly || user.role === "admin").map((r) => (
          <Route key={r.path} path={r.path} element={r.element(user)} />
        ))}
        <Route path="/login" element={<Navigate to="/" replace />} />
        <Route path="*" element={<NotFound />} />
      </Route>
    </Routes>
  );
}

// The links in the daily quarantine report work without logging in.
function Root() {
  const m = window.location.pathname.match(/^\/report\/([^/]+)$/);
  return m ? <ReportAction token={decodeURIComponent(m[1])} /> : <App />;
}

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
);
