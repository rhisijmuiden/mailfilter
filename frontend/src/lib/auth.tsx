import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api, ApiError } from "./api";
import { setLanguage } from "../i18n";

export type Me = { id: number; email: string; name: string; role: "admin" | "customer"; language: "en" | "nl"; domains: string[];
  two_factor_enabled: boolean;
  // 2FA is required by the admin but not set up yet: only the setup screen works.
  two_factor_setup_required: boolean;
};

export function useMe() {
  const qc = useQueryClient();
  const q = useQuery<Me | null>({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        return await api.get<Me>("/auth/me");
      } catch (e) {
        if (e instanceof ApiError && e.status === 401) return null;
        throw e;
      }
    },
    staleTime: 60_000,
  });
  useEffect(() => {
    const onOut = () => qc.setQueryData(["me"], null);
    window.addEventListener("mf:logged-out", onOut);
    return () => window.removeEventListener("mf:logged-out", onOut);
  }, [qc]);
  useEffect(() => {
    if (q.data?.language) setLanguage(q.data.language);
  }, [q.data?.language]);
  return q;
}
