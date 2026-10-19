import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { api } from "../api/client";
import type { Job } from "../api/types";
import { useAppState } from "../state/AppState";

export const isActive = (job: Job | null | undefined) => !!job && (job.status === "queued" || job.status === "running");

/** Poll a background job until it finishes; calls onFinish once with the final job. */
export function useJob(jobId: number | null | undefined, onFinish?: (job: Job) => void) {
  const finished = useRef<number | null>(null);
  const query = useQuery({
    queryKey: ["job", jobId],
    queryFn: () => api.job(jobId as number),
    enabled: !!jobId,
    refetchInterval: (q) => (isActive(q.state.data) || !q.state.data ? 1000 : false),
  });
  const job = query.data;
  useEffect(() => {
    if (job && !isActive(job) && finished.current !== job.id) {
      finished.current = job.id;
      onFinish?.(job);
    }
  }, [job, onFinish]);
  return job ?? null;
}

export function useProjects() {
  return useQuery({
    queryKey: ["projects"],
    queryFn: api.projects,
    refetchInterval: (q) => (q.state.data?.some((p) => isActive(p.job)) ? 1500 : false),
  });
}

/** The currently selected project (falls back to the most recent one). */
export function useCurrentProject() {
  const { projectId, setProjectId } = useAppState();
  const projects = useProjects();
  const list = projects.data ?? [];
  const project = list.find((p) => p.id === projectId) ?? null;
  useEffect(() => {
    if (!projects.isSuccess) return;
    if (projectId !== null && !list.some((p) => p.id === projectId)) setProjectId(list[0]?.id ?? null);
    if (projectId === null && list.length) setProjectId(list[0].id);
  }, [projects.isSuccess, list, projectId, setProjectId]);
  return { project, projects, isLoading: projects.isLoading };
}

export function useHealth() {
  return useQuery({ queryKey: ["health"], queryFn: api.health, refetchInterval: 15000, retry: 0 });
}

export function useInvalidate() {
  const client = useQueryClient();
  return (...keys: unknown[][]) => keys.forEach((key) => client.invalidateQueries({ queryKey: key }));
}
