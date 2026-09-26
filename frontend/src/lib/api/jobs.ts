import { getJson, sendJson } from "@/lib/api/request";
import type { JobStatus } from "@/types";

/** One Job, uncached: a cached status never sees its Job finish. */
export function getJobStatus(jobId: string): Promise<JobStatus> {
  return getJson<JobStatus>(`/api/v1/jobs/${encodeURIComponent(jobId)}`, { fresh: true });
}

/**
 * The caller's active Jobs plus a bounded tail of finished ones. `trackedJobIds`
 * are always included whatever their age, so a browser that tracked a Job can
 * still observe how it ended after a reload.
 */
export function listJobs(trackedJobIds: string[] = []): Promise<JobStatus[]> {
  const params = new URLSearchParams();
  trackedJobIds.forEach((jobId) => params.append("tracked_job_id", jobId));
  const query = params.size ? `?${params.toString()}` : "";
  return getJson<JobStatus[]>(`/api/v1/jobs${query}`, { fresh: true });
}

/** Withdraw what the Job was doing (the server releases its subject first). */
export function cancelJob(jobId: string): Promise<JobStatus> {
  return sendJson<JobStatus>(`/api/v1/jobs/${encodeURIComponent(jobId)}/cancel`, "POST", {});
}

/** Queue a failed or cancelled Job again on the same subject. */
export function retryJob(jobId: string): Promise<JobStatus> {
  return sendJson<JobStatus>(`/api/v1/jobs/${encodeURIComponent(jobId)}/retry`, "POST", {});
}
