import type { CaptureSourceDraft } from "./capture-adapter.ts";

export interface BrowserCaptureFile {
  id: string;
  file: Blob;
  filename: string;
  mediaType: string;
  role?: "file" | "cover";
}

interface CaptureUploadSlot {
  id: string;
  role: "file" | "cover";
  source_file_id: string | null;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
}

interface CaptureSlotResponse {
  item: { id: number };
  slots: CaptureUploadSlot[];
}

interface DeclaredCaptureFile {
  id: string;
  filename: string;
  media_type: string;
  size_bytes: number;
  sha256: string;
}

interface PreparedCaptureFile {
  declaration: DeclaredCaptureFile;
  file: Blob;
  role: "file" | "cover";
}

async function sha256Hex(file: Blob): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, "0")).join("");
}

async function prepareCaptureFile(
  upload: BrowserCaptureFile,
  id: string,
  role: "file" | "cover",
): Promise<PreparedCaptureFile> {
  return {
    declaration: {
      id,
      filename: upload.filename,
      media_type: upload.mediaType,
      size_bytes: upload.file.size,
      sha256: await sha256Hex(upload.file),
    },
    file: upload.file,
    role,
  };
}

function matchingSlot(slots: CaptureUploadSlot[], upload: PreparedCaptureFile): CaptureUploadSlot {
  const slot = slots.find((candidate) =>
    upload.role === "cover"
      ? candidate.role === "cover"
      : candidate.role === "file" && candidate.source_file_id === upload.declaration.id,
  );
  if (
    slot === undefined ||
    slot.filename !== upload.declaration.filename ||
    slot.media_type !== upload.declaration.media_type ||
    slot.size_bytes !== upload.declaration.size_bytes ||
    slot.sha256 !== upload.declaration.sha256
  ) {
    throw new Error("PrintStash returned invalid capture upload slots.");
  }
  return slot;
}

export async function captureRichFiles({
  fetchImpl = fetch,
  vault,
  authorization,
  sourceUrl,
  title,
  captureSource,
  files,
  cover,
}: {
  fetchImpl?: typeof fetch;
  vault: string;
  authorization: string;
  sourceUrl: string;
  title?: string;
  captureSource: CaptureSourceDraft;
  files: BrowserCaptureFile[];
  cover?: BrowserCaptureFile;
}): Promise<unknown> {
  const base = vault.replace(/\/$/, "");
  const ids = files.map((file) => file.id);
  if (ids.some((id) => !/^[a-zA-Z0-9._:-]{1,255}$/.test(id)) || new Set(ids).size !== ids.length) {
    throw new Error("Capture file IDs must be unique, bounded identifiers.");
  }
  const preparedFiles = await Promise.all(
    files.map((upload) => prepareCaptureFile(upload, upload.id, "file")),
  );
  const preparedCover = cover ? await prepareCaptureFile(cover, "cover", "cover") : undefined;
  const uploads = preparedCover ? [...preparedFiles, preparedCover] : preparedFiles;
  const created = await fetchImpl(`${base}/api/v1/inbox/capture-upload-slots`, {
    method: "POST",
    headers: { Authorization: `Bearer ${authorization}`, "Content-Type": "application/json" },
    body: JSON.stringify({
      source_url: sourceUrl,
      title: title || null,
      capture_source: captureSource,
      files: preparedFiles.map(({ declaration }) => declaration),
      ...(preparedCover ? { cover: preparedCover.declaration } : {}),
    }),
  });
  if (!created.ok)
    throw new Error(`PrintStash returned ${created.status} while creating upload slots.`);
  const payload = (await created.json()) as CaptureSlotResponse;
  if (!payload?.item || !Array.isArray(payload.slots) || payload.slots.length !== uploads.length) {
    throw new Error("PrintStash returned invalid capture upload slots.");
  }

  for (const upload of uploads) {
    const slot = matchingSlot(payload.slots, upload);
    const uploaded = await fetchImpl(
      `${base}/api/v1/inbox/capture-upload-slots/${encodeURIComponent(slot.id)}`,
      {
        method: "PUT",
        headers: {
          Authorization: `Bearer ${authorization}`,
          "Content-Type": upload.declaration.media_type,
        },
        body: upload.file,
      },
    );
    if (!uploaded.ok)
      throw new Error(
        `PrintStash returned ${uploaded.status} while uploading ${upload.declaration.filename}.`,
      );
  }

  const finalized = await fetchImpl(
    `${base}/api/v1/inbox/${payload.item.id}/capture-upload-finalize`,
    {
      method: "POST",
      headers: { Authorization: `Bearer ${authorization}` },
    },
  );
  if (!finalized.ok)
    throw new Error(`PrintStash returned ${finalized.status} while finalizing the capture.`);
  return finalized.json();
}
