const SOURCE_RULES = [
  {
    source: "Printables",
    host: (hostname) => hostname === "printables.com" || hostname === "www.printables.com",
    path: /^\/(?:[^/]+\/)?(?:model|collections)\/\d+(?:[-/]|$)/,
  },
  {
    source: "MakerWorld",
    host: (hostname) => hostname === "makerworld.com" || hostname.endsWith(".makerworld.com"),
    path: /^\/(?:[^/]+\/)?models\/\d+(?:[-/]|$)/,
  },
  {
    source: "Thingiverse",
    host: (hostname) => hostname === "thingiverse.com" || hostname === "www.thingiverse.com",
    path: /^\/(?:thing:\d+|things\/\d+)(?:[-/]|$)/,
  },
];

const DIRECT_FILE_PATH = /\.(?:zip|3mf|stl|obj|step|stp|gcode|g|gco|bgcode)$/i;

export const BROWSER_EXTENSION_SETUP_STORAGE_KEY = "printstash.browser-extension-setup:v1";

const BROWSER_EXTENSION_SETUP_MAX_AGE_MS = 10 * 60 * 1000;

function hostnameFromUnqualifiedVault(value) {
  const authority = value.split(/[/?#]/, 1)[0];
  if (authority.startsWith("[")) return authority.slice(1, authority.indexOf("]"));
  return authority.replace(/:\d+$/, "");
}

function isPrivateIpv4(hostname) {
  const octets = hostname.split(".").map(Number);
  if (octets.length !== 4 || octets.some((octet) => !Number.isInteger(octet) || octet < 0 || octet > 255)) {
    return false;
  }
  return (
    octets[0] === 10 ||
    octets[0] === 127 ||
    (octets[0] === 169 && octets[1] === 254) ||
    (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
    (octets[0] === 192 && octets[1] === 168)
  );
}

function isLocalHostname(value) {
  const hostname = String(value ?? "")
    .trim()
    .replace(/^\[|\]$/g, "")
    .replace(/\.$/, "")
    .toLowerCase();
  if (!hostname) return false;
  if (
    hostname === "localhost" ||
    hostname.endsWith(".localhost") ||
    hostname.endsWith(".local") ||
    hostname === "::1"
  ) {
    return true;
  }
  if (isPrivateIpv4(hostname)) return true;
  if (hostname.includes(":")) {
    return hostname.startsWith("fc") || hostname.startsWith("fd") || /^fe[89ab]/.test(hostname);
  }
  return !hostname.includes(".");
}

export function isLocalVault(value) {
  try {
    return isLocalHostname(new URL(value).hostname);
  } catch {
    return false;
  }
}

export function normalizeVault(value) {
  let raw = String(value ?? "").trim();
  if (!raw) throw new Error("Vault URL is required.");
  if (!/^[a-z][a-z\d+.-]*:\/\//i.test(raw)) {
    const hostname = hostnameFromUnqualifiedVault(raw);
    raw = `${isLocalHostname(hostname) ? "http" : "https"}://${raw}`;
  }
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

export function parseBrowserExtensionSetup(value, activePageUrl, now = Date.now()) {
  if (typeof value !== "string" || !value) return null;
  let payload;
  try {
    payload = JSON.parse(value);
  } catch {
    return null;
  }
  if (
    !payload ||
    payload.version !== 1 ||
    typeof payload.vault !== "string" ||
    typeof payload.username !== "string" ||
    typeof payload.apiKey !== "string" ||
    typeof payload.expiresAt !== "number" ||
    payload.expiresAt <= now ||
    payload.expiresAt > now + BROWSER_EXTENSION_SETUP_MAX_AGE_MS
  ) {
    return null;
  }

  try {
    const vault = normalizeVault(payload.vault);
    const activePage = new URL(activePageUrl);
    if (new URL(vault).origin !== activePage.origin) return null;
    requireCredentials(payload.username, payload.apiKey);
    return {
      vault,
      username: payload.username.trim(),
      apiKey: payload.apiKey.trim(),
    };
  } catch {
    return null;
  }
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
  if (rule) return rule.source;
  return DIRECT_FILE_PATH.test(parsed.pathname) ? "Direct file" : null;
}

async function responseDetail(response, fallback) {
  const body = await response.json().catch(() => ({}));
  if (typeof body.detail === "string") {
    const messages = {
      invalid_credentials: "The username or API key is incorrect.",
      not_authenticated: "The PrintStash connection expired. Reconnect and try again.",
      insufficient_scope: "This PrintStash user does not have import permission.",
      provide_password_or_api_key: "Enter a username and named API key.",
    };
    return messages[body.detail] || body.detail;
  }
  return fallback;
}

function requireCredentials(username, apiKey) {
  if (!String(username ?? "").trim() || !String(apiKey ?? "").trim()) {
    throw new Error("Username and named API key are required.");
  }
}

async function fetchVault(fetchImpl, base, path, options = {}) {
  const local = isLocalVault(base);
  try {
    return await fetchImpl(`${base}${path}`, options);
  } catch {
    throw new Error(
      local
        ? `Couldn't reach PrintStash at ${new URL(base).host}. Check that PrintStash is running and that this address opens in Chrome.`
        : `Couldn't reach PrintStash at ${new URL(base).host}. Check the Vault URL, network, and HTTPS certificate.`,
    );
  }
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
  const login = await fetchVault(fetchImpl, base, "/api/v1/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    credentials: "omit",
    body: JSON.stringify({
      username: String(username).trim(),
      api_key: String(apiKey).trim(),
      remember_me: false,
    }),
  });
  if (!login.ok) {
    throw new Error(await responseDetail(login, `PrintStash login returned ${login.status}.`));
  }
  const loginBody = await login.json().catch(() => null);
  if (typeof loginBody.access_token !== "string" || !loginBody.access_token) {
    throw new Error("PrintStash did not return an access token.");
  }
  return loginBody.access_token;
}

export async function verifyVaultConnection({
  fetchImpl = fetch,
  vault,
  username,
  apiKey,
}) {
  const base = normalizeVault(vault);
  requireCredentials(username, apiKey);

  const health = await fetchVault(fetchImpl, base, "/api/v1/health", {
    headers: { Accept: "application/json" },
    credentials: "omit",
    cache: "no-store",
  });
  const healthBody = await health.json().catch(() => null);
  if (!health.ok || healthBody?.status !== "ok" || healthBody?.name !== "PrintStash") {
    throw new Error("That URL is not a PrintStash server.");
  }

  const accessToken = await vaultLogin({ fetchImpl, base, username, apiKey });
  const profile = await fetchVault(fetchImpl, base, "/api/v1/auth/me", {
    headers: {
      Accept: "application/json",
      Authorization: `Bearer ${accessToken}`,
    },
    credentials: "omit",
    cache: "no-store",
  });
  if (!profile.ok) {
    throw new Error(await responseDetail(profile, `PrintStash profile check returned ${profile.status}.`));
  }
  const user = await profile.json().catch(() => null);
  if (!user || typeof user.username !== "string" || typeof user.is_superuser !== "boolean") {
    throw new Error("PrintStash returned an invalid user profile.");
  }
  return { base, accessToken, user };
}

export async function captureModelPage({
  fetchImpl = fetch,
  vault,
  username,
  apiKey,
  accessToken,
  pageUrl,
  title,
  requestPageJson,
  ensureOriginPermission = async () => {},
}) {
  const base = normalizeVault(vault);
  const source = classifyModelPage(pageUrl);
  if (!source) throw new Error("Open a supported model page or direct model file first.");
  requireCredentials(username, apiKey);

  const token = accessToken || await vaultLogin({ fetchImpl, base, username, apiKey });

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
    const uploaded = await fetchVault(fetchImpl, base, "/api/v1/inbox/browser-upload", {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: form,
    });
    if (!uploaded.ok) {
      throw new Error(await responseDetail(uploaded, `PrintStash returned ${uploaded.status}.`));
    }
    return { source, item: await uploaded.json(), inboxUrl: `${base}/inbox` };
  }

  const captured = await fetchVault(fetchImpl, base, "/api/v1/inbox", {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
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
