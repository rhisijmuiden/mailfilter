// Background tasks (update, backup, restore) run by mailfilterctl on the server.
import { useQuery } from "@tanstack/react-query";
import { api } from "./api";

export type JobStep = { at: string; text: string; key?: string | null; params?: Record<string, string> };
export type Job = {
  id: string;
  kind: "update" | "backup" | "restore";
  state: "queued" | "running" | "done" | "failed";
  created: string;
  started: string | null;
  finished: string | null;
  message: string;
  steps: JobStep[];
  result: Record<string, unknown> | null;
};

export const isActive = (j?: Job | null) => !!j && (j.state === "queued" || j.state === "running");

// Polls a job until it ends. Network errors are expected while an update
// restarts the web interface, so they do not stop the polling.
export function useJob(id: string | null) {
  return useQuery({
    queryKey: ["job", id],
    queryFn: () => api.get<Job>(`/system/jobs/${id}`),
    enabled: !!id,
    retry: true,
    retryDelay: 2000,
    refetchInterval: (q) => (q.state.data && !isActive(q.state.data) ? false : 1500),
  });
}

export function useJobs() {
  return useQuery({ queryKey: ["jobs"], queryFn: () => api.get<{ jobs: Job[] }>("/system/jobs"), refetchInterval: 10_000 });
}
