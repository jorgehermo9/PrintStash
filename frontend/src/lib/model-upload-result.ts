import type { JobStatus } from "@/types";

export interface UploadedModelResult {
  id: number;
  created: boolean;
}

/** Use the server's committed outcome; filenames and requested names are not identity. */
export function uploadedModelForJobs(jobs: readonly JobStatus[]): UploadedModelResult | undefined {
  const uploads = jobs.filter(
    (job) =>
      job.state === "completed" &&
      (job.kind === "ingestion.artifact_upload" || job.kind === "ingestion.upload") &&
      job.model_id !== null &&
      job.result?.created !== undefined,
  );
  const id = uploads[0]?.model_id;
  if (id === undefined || id === null || uploads.some((job) => job.model_id !== id)) return;
  return { id, created: uploads.some((job) => job.result?.created === true) };
}
