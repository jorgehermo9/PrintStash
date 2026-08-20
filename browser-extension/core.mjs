const SOURCE_RULES = [
  {
    source: "Printables",
    host: (hostname) => hostname === "printables.com" || hostname.endsWith(".printables.com"),
    path: /^\/(?:[^/]+\/)?(?:model|collections)\/\d+(?:[-/]|$)/,
  },
  {
    source: "MakerWorld",
    host: (hostname) => hostname === "makerworld.com" || hostname.endsWith(".makerworld.com"),
    path: /^\/(?:[^/]+\/)?models\/\d+(?:[-/]|$)/,
  },
];

export function normalizeVault(value) {
  const raw = String(value ?? "").trim();
  if (!raw) throw new Error("Vault URL is required.");
  let parsed;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("Vault URL is invalid.");
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) {
    throw new Error("Vault URL must use HTTP or HTTPS.");
  }
  if (parsed.username || parsed.password) {
    throw new Error("Vault URL cannot contain credentials.");
  }
  parsed.search = "";
  parsed.hash = "";
  parsed.pathname = parsed.pathname.replace(/\/+$/, "");
  return parsed.toString().replace(/\/$/, "");
}

export function classifyModelPage(value) {
  let parsed;
  try {
    parsed = new URL(value);
  } catch {
    return null;
  }
  if (!['http:', 'https:'].includes(parsed.protocol)) return null;
  const hostname = parsed.hostname.toLowerCase();
  const rule = SOURCE_RULES.find((candidate) => candidate.host(hostname) && candidate.path.test(parsed.pathname));
  return rule?.source ?? null;
}

async function responseDetail(response, fallback) {
  const body = await response.json().catch(() => ({}));
  if (typeof body.detail === "string") return body.detail;
  return fallback;
}

function findValue(payload, keys) {
  const queue = [payload];
  while (queue.length) {
    const value = queue.shift();
    if (Array.isArray(value)) {
      queue.push(...value);
      continue;
    }
    if (!value || typeof value !== "object") continue;
    for (const key of keys) {
      if (value[key] !== undefined && value[key] !== null && value[key] !== "") {
        return value[key];
      }
    }
    queue.push(...Object.values(value));
  }
  return null;
}

function makerWorldInstanceId(payload) {
  const explicit = findValue(payload, ["defaultInstanceId", "instanceId"]);
  if (explicit) return explicit;
  const queue = [payload];
  while (queue.length) {
    const value = queue.shift();
    if (Array.isArray(value)) {
      queue.push(...value);
      continue;
    }
    if (!value || typeof value !== "object") continue;
    if (Array.isArray(value.instances)) {
      const instance = value.instances.find((item) => item && typeof item === "object" && item.id);
      if (instance) return instance.id;
    }
    queue.push(...Object.values(value));
  }
  return null;
}

function firstDownloadUrl(payload) {
  const queue = [payload];
  let fallback = null;
  while (queue.length) {
    const value = queue.shift();
    if (Array.isArray(value)) {
      queue.push(...value);
      continue;
    }
    if (!value || typeof value !== "object") continue;
    for (const key of ["downloadUrl", "download_url", "url", "link"]) {
      const candidate = value[key];
      if (typeof candidate !== "string" || !candidate.startsWith("https://")) continue;
      fallback ??= candidate;
      if (/\.(?:3mf|zip|stl|obj|step|stp)(?:[?#]|$)|\/download(?:[/?#]|$)/i.test(candidate)) {
        return candidate;
      }
    }
    queue.push(...Object.values(value));
  }
  return fallback;
}

function filenameFromUrl(value, fallback) {
  try {
    const name = decodeURIComponent(new URL(value).pathname.split("/").pop() || "");
    if (name && /\.[a-z0-9]{1,8}$/i.test(name)) return name;
  } catch {
    // The caller will use its stable model-id fallback.
  }
  return fallback;
}

function contentDispositionFilename(value) {
  if (!value) return null;
  const encoded = value.match(/filename\*=UTF-8''([^;]+)/i);
  if (encoded) {
    try {
      return decodeURIComponent(encoded[1].trim());
    } catch {
      return null;
    }
  }
  const plain = value.match(/filename="?([^";]+)"?/i);
  return plain?.[1]?.trim() || null;
}

export async function makerWorldDownload({ pageUrl, requestPageJson }) {
  if (typeof requestPageJson !== "function") {
    throw new Error("MakerWorld must be opened in the active browser tab.");
  }
  const page = new URL(pageUrl);
  const match = page.pathname.match(/\/models\/(\d+)/);
  if (!match) throw new Error("Open an individual MakerWorld model page first.");
  const designId = match[1];
  const apiBase = `${page.origin}/api/v1/design-service`;
  const design = await requestPageJson(`${apiBase}/design/${designId}`);
  const instanceId = makerWorldInstanceId(design);
  if (!instanceId) throw new Error("MakerWorld did not expose a downloadable model instance.");
  const payload = await requestPageJson(
    `${apiBase}/instance/${encodeURIComponent(String(instanceId))}/f3mf?type=download&fileType=3mfstl`,
  );
  const url = firstDownloadUrl(payload);
  if (!url) throw new Error("MakerWorld did not return a download link for this model.");
  return { url, filename: filenameFromUrl(url, `${designId}.3mf`) };
}

async function vaultLogin({ fetchImpl, base, username, apiKey }) {
  const login = await fetchImpl(`${base}/api/v1/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username: String(username).trim(),
      api_key: String(apiKey).trim(),
      remember_me: false,
    }),
  });
  if (!login.ok) {
    throw new Error(await responseDetail(login, `PrintStash login returned ${login.status}.`));
  }
  const loginBody = await login.json();
  if (typeof loginBody.access_token !== "string" || !loginBody.access_token) {
    throw new Error("PrintStash did not return an access token.");
  }
  return loginBody.access_token;
}

export async function captureModelPage({
  fetchImpl = fetch,
  vault,
  username,
  apiKey,
  pageUrl,
  title,
  requestPageJson,
  ensureOriginPermission = async () => {},
}) {
  const base = normalizeVault(vault);
  const source = classifyModelPage(pageUrl);
  if (!source) throw new Error("Open a MakerWorld model or Printables model or collection page first.");
  if (!String(username ?? "").trim() || !String(apiKey ?? "").trim()) {
    throw new Error("Username and named API key are required.");
  }

  const accessToken = await vaultLogin({ fetchImpl, base, username, apiKey });

  if (source === "MakerWorld") {
    const resolved = await makerWorldDownload({ pageUrl, requestPageJson });
    const permission = `${new URL(resolved.url).origin}/*`;
    await ensureOriginPermission(permission);
    const download = await fetchImpl(resolved.url, { credentials: "omit" });
    if (!download.ok) {
      throw new Error(`MakerWorld download returned ${download.status}.`);
    }
    const blob = await download.blob();
    const headerName = contentDispositionFilename(download.headers.get("Content-Disposition"));
    const filename = headerName || resolved.filename;
    const form = new FormData();
    form.append("source_url", pageUrl);
    if (String(title ?? "").trim()) form.append("title", String(title).trim());
    form.append("file", blob, filename);
    const uploaded = await fetchImpl(`${base}/api/v1/inbox/browser-upload`, {
      method: "POST",
      headers: { Authorization: `Bearer ${accessToken}` },
      body: form,
    });
    if (!uploaded.ok) {
      throw new Error(await responseDetail(uploaded, `PrintStash returned ${uploaded.status}.`));
    }
    return { source, item: await uploaded.json(), inboxUrl: `${base}/inbox` };
  }

  const captured = await fetchImpl(`${base}/api/v1/inbox`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${accessToken}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      url: pageUrl,
      title: String(title ?? "").trim() || null,
      source_kind: "browser",
    }),
  });
  if (!captured.ok) {
    throw new Error(await responseDetail(captured, `PrintStash returned ${captured.status}.`));
  }
  return { source, item: await captured.json(), inboxUrl: `${base}/inbox` };
}
