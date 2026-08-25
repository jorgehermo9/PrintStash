import {
  BROWSER_EXTENSION_SETUP_STORAGE_KEY,
  captureModelPage,
  claimBrowserPairing,
  classifyModelPage,
  isLocalVault,
  normalizeVault,
  parseBrowserExtensionSetup,
  verifyBrowserDevice,
  verifyVaultConnection,
} from "./core.ts";
import {
  buildBrowserCaptureMessage,
  JSON_LD_MAX_SCRIPT_BYTES,
  JSON_LD_MAX_SCRIPTS,
  JSON_LD_MAX_TOTAL_BYTES,
  stableCaptureFileId,
  type BrowserCaptureMessage,
} from "./capture-adapter.ts";
import { captureRichFiles, type BrowserCaptureFile } from "./capture-transport.ts";
import {
  createBrowserProviderAdapter,
  type BrowserExtensionApi,
  type BrowserProviderAdapter,
} from "./browser-provider-adapter.ts";

declare const chrome: unknown;
function requiredElement<T extends Element>(selector: string, constructor: { new (): T }): T {
  const element = document.querySelector(selector);
  if (!(element instanceof constructor)) throw new Error(`Missing ${selector}`);
  return element;
}

const browser: BrowserProviderAdapter = createBrowserProviderAdapter(
  chrome as unknown as BrowserExtensionApi,
);
type Page = { id?: number; title?: string; url?: string };
type Source = "Printables" | "MakerWorld" | "Thingiverse" | "Cults" | "Direct file" | null;
type Config = {
  vault: string;
  pairingCode?: string;
  username?: string;
  apiKey?: string;
  deviceCredential?: string;
};
type Profile = { username: string; is_superuser: boolean };
type ConnectionState = "checking" | "connected" | "error" | "disconnected";
type PageFetchResult = { ok: boolean; status: number; body: string };
type VisibleCaptureResult = {
  pageTitle: string;
  challengeDetected?: boolean;
  jsonLd: string[];
};

const vaultInput = requiredElement("#vault", HTMLInputElement);
const pairingCodeInput = requiredElement("#pairing-code", HTMLInputElement);
const usernameInput = requiredElement("#username", HTMLInputElement);
const keyInput = requiredElement("#key", HTMLInputElement);
const pageLabel = requiredElement("#page", HTMLElement);
const sourceLabel = requiredElement("#source", HTMLElement);
const statusLabel = requiredElement("#status", HTMLElement);
const captureButton = requiredElement("#capture", HTMLButtonElement);
const inboxButton = requiredElement("#open-inbox", HTMLButtonElement);
const importPanel = requiredElement("#import-panel", HTMLElement);
const importHint = requiredElement("#import-hint", HTMLElement);
const candidatePanel = requiredElement("#candidate-panel", HTMLFieldSetElement);
const candidateList = requiredElement("#candidate-list", HTMLElement);
const manualFilePanel = requiredElement("#manual-file-panel", HTMLFieldSetElement);
const manualFileInput = requiredElement("#manual-file", HTMLInputElement);
const connectionStatus = requiredElement("#connection-status", HTMLElement);
const connectionTitle = requiredElement("#connection-title", HTMLElement);
const connectionDetail = requiredElement("#connection-detail", HTMLElement);
const connectionPanel = requiredElement("#connection-panel", HTMLElement);
const connectionForm = requiredElement("#connection-form", HTMLFormElement);
const connectButton = requiredElement("#connect", HTMLButtonElement);
const editButton = requiredElement("#edit-connection", HTMLButtonElement);
const cancelButton = requiredElement("#cancel-edit", HTMLButtonElement);
const disconnectButton = requiredElement("#disconnect", HTMLButtonElement);
const apiSettingsButton = requiredElement("#open-api-settings", HTMLButtonElement);

let activePage: Page | null = null;
let activeSource: Source = null;
let connectionState = "checking";
let connectedConfig: Config | null = null;
let connectedProfile: Profile | null = null;
let accessToken: string | null = null;
let editingConnection = false;
let importBusy = false;
let pendingPrintablesCapture: BrowserCaptureMessage | null = null;
let pendingManualCapture: BrowserCaptureMessage | null = null;

function messageFrom(error: unknown) {
  return error instanceof Error ? error.message : "Something went wrong.";
}

function showStatus(message = "", kind = "") {
  statusLabel.textContent = message;
  if (kind) statusLabel.dataset.kind = kind;
  else delete statusLabel.dataset.kind;
}

function clearInboxAction() {
  inboxButton.hidden = true;
  delete inboxButton.dataset.url;
}

function clearCandidateSelection() {
  pendingPrintablesCapture = null;
  candidatePanel.hidden = true;
  candidateList.replaceChildren();
  captureButton.textContent = "Send to Pending Imports";
}

function clearManualFileSelection() {
  pendingManualCapture = null;
  manualFileInput.value = "";
  manualFilePanel.hidden = true;
  captureButton.textContent = "Send to Pending Imports";
}

function renderManualFileSelection(capture: BrowserCaptureMessage) {
  pendingManualCapture = capture;
  manualFilePanel.hidden = false;
  captureButton.textContent = "Upload selected file";
}

function selectedManualFile(capture: BrowserCaptureMessage): BrowserCaptureFile {
  const file = manualFileInput.files?.item(0);
  if (!file) throw new Error("Choose a downloaded model file before uploading.");
  return {
    id: stableCaptureFileId(capture.source.source_item_id, file.name),
    file,
    filename: file.name,
    mediaType: file.type || "application/octet-stream",
  };
}

function renderCandidateSelection(capture: BrowserCaptureMessage) {
  pendingPrintablesCapture = capture;
  candidateList.replaceChildren();
  capture.candidates.forEach((candidate, index) => {
    const label = document.createElement("label");
    label.className = "candidate-option";
    const input = document.createElement("input");
    input.type = "checkbox";
    input.checked = true;
    input.dataset.candidateIndex = String(index);
    const filename = document.createElement("span");
    filename.textContent = candidate.filename;
    label.append(input, filename);
    candidateList.append(label);
  });
  candidatePanel.hidden = false;
  captureButton.textContent = "Confirm and upload selected files";
}

function selectedPrintablesCandidates(capture: BrowserCaptureMessage) {
  const indexes = [...candidateList.querySelectorAll<HTMLInputElement>("input:checked")].map(
    (input) => Number(input.dataset.candidateIndex),
  );
  return indexes.flatMap((index) => capture.candidates[index] ?? []);
}

function setButtonBusy(button: HTMLButtonElement, busy: boolean, busyLabel = "Working…") {
  if (busy) {
    button.dataset.label = button.textContent;
    button.textContent = busyLabel;
    button.setAttribute("aria-busy", "true");
  } else {
    button.textContent = button.dataset.label || button.textContent;
    button.removeAttribute("aria-busy");
    delete button.dataset.label;
  }
  button.disabled = busy;
}

function setConnectionFormBusy(busy: boolean) {
  connectionForm.toggleAttribute("aria-busy", busy);
  for (const control of connectionForm.elements)
    if (control instanceof HTMLInputElement || control instanceof HTMLButtonElement)
      control.disabled = busy;
  setButtonBusy(connectButton, busy, "Connecting…");
}

function configFromForm() {
  const vault = normalizeVault(vaultInput.value);
  const pairingCode = pairingCodeInput.value.trim();
  if (pairingCode) return { vault, pairingCode };
  const username = usernameInput.value.trim();
  const apiKey = keyInput.value.trim();
  if (!username || !apiKey)
    throw new Error("Enter a pairing code, or a username and named API key.");
  return { vault, username, apiKey };
}

function fillConnectionForm(config: Config | null) {
  vaultInput.value = config?.vault || "";
  pairingCodeInput.value = "";
  usernameInput.value = config?.username || "";
  keyInput.value = config?.apiKey || "";
}

function permissionOrigin(config: Config) {
  const vault = new URL(config.vault);
  return isLocalVault(config.vault)
    ? `${vault.protocol}//${vault.hostname}/*`
    : `${vault.origin}/*`;
}

function connectionHost(config: Config) {
  try {
    return new URL(config.vault).host;
  } catch {
    return config.vault;
  }
}

function renderImportAvailability() {
  const connected = connectionState === "connected" && connectedConfig;
  const readyConnection = connected && !editingConnection;
  importPanel.hidden = !readyConnection;
  captureButton.disabled = importBusy || !readyConnection || !activeSource;
  if (importBusy) {
    importHint.textContent = "Sending the model to your review inbox…";
  } else if (!connected) {
    importHint.textContent = "Connect PrintStash to enable importing.";
  } else if (!activeSource) {
    importHint.textContent = "Open a supported model page or direct model file first.";
  } else {
    importHint.textContent = `${activeSource} is ready. Files will remain reviewable before import.`;
  }
}

function renderConnection(
  state: ConnectionState,
  { config, profile, detail }: { config?: Config; profile?: Profile | null; detail?: string } = {},
) {
  connectionState = state;
  connectionStatus.dataset.state = state;

  if (state === "connected") {
    if (!config) throw new Error("Connected state requires a configuration.");
    const role = profile?.is_superuser ? "Admin" : "Member";
    connectionTitle.textContent = "Connected";
    connectionDetail.textContent = `${profile?.username || (config.deviceCredential ? "Paired browser" : config.username)} · ${connectionHost(config)} · ${role}`;
  } else if (state === "checking") {
    connectionTitle.textContent = "Checking connection…";
    connectionDetail.textContent =
      detail || (config ? connectionHost(config) : "Reading saved settings");
  } else if (state === "error") {
    connectionTitle.textContent = "Connection failed";
    connectionDetail.textContent = detail || "Review the URL and credentials below.";
  } else {
    connectionTitle.textContent = "Not connected";
    connectionDetail.textContent = detail || "Connect a PrintStash vault to start importing.";
  }

  const showConnectedActions = state === "connected";
  editButton.hidden = !showConnectedActions || editingConnection;
  disconnectButton.hidden = !showConnectedActions || !editingConnection;
  renderImportAvailability();
}

function renderActivePage() {
  const classified = activePage?.url ? classifyModelPage(activePage.url) : null;
  activeSource =
    classified === "Printables" ||
    classified === "MakerWorld" ||
    classified === "Thingiverse" ||
    classified === "Cults" ||
    classified === "Direct file"
      ? classified
      : null;
  pageLabel.textContent = activePage?.title || activePage?.url || "No active page";
  sourceLabel.textContent = activeSource
    ? activeSource === "Direct file"
      ? "Direct model file detected"
      : `${activeSource} page detected`
    : "Open a MakerWorld, Printables, Thingiverse, or Cults page, or a direct model file";
  sourceLabel.dataset.supported = activeSource ? "true" : "false";
  renderImportAvailability();
}

async function ensureVaultPermission(config: Config, requestPermission: boolean) {
  const origins = [permissionOrigin(config)];
  const granted = requestPermission
    ? await browser.permissions.request({ origins })
    : await browser.permissions.contains({ origins });
  if (!granted) {
    throw new Error(
      requestPermission
        ? "Permission to contact this PrintStash vault was not granted."
        : "Reconnect this vault to restore its browser permission.",
    );
  }
}

async function establishConnection(
  config: Config,
  { requestPermission, persist }: { requestPermission: boolean; persist: boolean },
) {
  const previous = connectedConfig
    ? { config: connectedConfig, profile: connectedProfile, token: accessToken }
    : null;
  const preservePrevious = Boolean(previous && editingConnection);
  renderConnection("checking", { config });
  setConnectionFormBusy(true);
  showStatus();
  try {
    await ensureVaultPermission(config, requestPermission);
    let verified;
    let normalized;
    if (config.pairingCode) {
      verified = await claimBrowserPairing({
        vault: config.vault,
        code: config.pairingCode,
        name: "Browser extension",
      });
      normalized = { vault: verified.base, deviceCredential: verified.deviceCredential };
      if (persist) {
        await browser.storage.set(normalized);
        await browser.storage.remove(["username", "apiKey"]);
      }
    } else if (config.deviceCredential) {
      verified = await verifyBrowserDevice(config);
      normalized = { vault: verified.base, deviceCredential: config.deviceCredential };
      if (persist) await browser.storage.set(normalized);
    } else {
      verified = await verifyVaultConnection({
        fetchImpl: fetch,
        vault: config.vault,
        username: config.username || "",
        apiKey: config.apiKey || "",
      });
      normalized = {
        vault: verified.base,
        username: config.username || "",
        apiKey: config.apiKey || "",
      };
      if (persist) await browser.storage.set(normalized);
    }

    if (previous && permissionOrigin(previous.config) !== permissionOrigin(normalized)) {
      await browser.permissions
        .remove({ origins: [permissionOrigin(previous.config)] })
        .catch(() => false);
    }

    connectedConfig = normalized;
    const verifiedConnection = verified as { user?: Profile; accessToken?: string };
    connectedProfile = verifiedConnection.user || null;
    accessToken = verifiedConnection.accessToken || null;
    if (previous && previous.config.vault !== normalized.vault) clearInboxAction();
    editingConnection = false;
    connectionPanel.hidden = true;
    cancelButton.hidden = true;
    disconnectButton.hidden = true;
    fillConnectionForm(normalized);
    renderConnection("connected", { config: normalized, profile: verifiedConnection.user });
  } catch (error) {
    let failedOrigin = null;
    let previousOrigin = null;
    try {
      failedOrigin = permissionOrigin(config);
      previousOrigin = previous ? permissionOrigin(previous.config) : null;
    } catch {
      // Invalid legacy settings have no origin permission to clean up.
    }
    if (requestPermission && failedOrigin && failedOrigin !== previousOrigin) {
      await browser.permissions.remove({ origins: [failedOrigin] }).catch(() => false);
    }
    if (preservePrevious && previous) {
      connectedConfig = previous.config;
      connectedProfile = previous.profile;
      accessToken = previous.token;
      renderConnection("connected", { config: previous.config, profile: previous.profile });
    } else {
      connectedConfig = null;
      connectedProfile = null;
      accessToken = null;
      renderConnection("error", { detail: messageFrom(error) });
    }
    connectionPanel.hidden = false;
    throw error;
  } finally {
    setConnectionFormBusy(false);
    connectButton.textContent = editingConnection ? "Update connection" : "Connect";
  }
}

async function requestPageJson(url: string): Promise<Record<string, unknown> | unknown[]> {
  if (!activePage?.id) throw new Error("The active tab is unavailable.");
  const results = await browser.scripting.executeScript({
    target: { tabId: activePage.id },
    world: "MAIN",
    func: async (requestUrl) => {
      const response = await fetch(requestUrl, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      return { ok: response.ok, status: response.status, body: await response.text() };
    },
    args: [url],
  });
  const result = results[0]?.result as PageFetchResult | undefined;
  if (!result?.ok) {
    if (result?.status === 401 || result?.status === 403) {
      throw new Error("Log in to MakerWorld in this tab, then try again.");
    }
    throw new Error(`MakerWorld returned ${result?.status || "an invalid response"}.`);
  }
  try {
    return JSON.parse(result.body);
  } catch {
    throw new Error("MakerWorld returned an invalid response.");
  }
}

async function ensureOriginPermission(origin: string) {
  const granted = await browser.permissions.request({ origins: [origin] });
  if (!granted) throw new Error("Permission to download the selected source file was not granted.");
}

async function downloadPrintablesCandidate(
  candidate: BrowserCaptureMessage["candidates"][number],
): Promise<BrowserCaptureFile> {
  const origin = `${new URL(candidate.url).origin}/*`;
  await ensureOriginPermission(origin);
  const response = await fetch(candidate.url, {
    credentials: "omit",
    cache: "no-store",
  });
  if (!response.ok) {
    throw new Error(
      "user_file_required: Printables could not provide the selected file. Attach it manually in Pending Imports.",
    );
  }
  const file = await response.blob();
  if (candidate.sizeBytes !== undefined && file.size !== candidate.sizeBytes) {
    throw new Error(
      "user_file_required: The selected Printables file changed. Attach it manually in Pending Imports.",
    );
  }
  return {
    id: candidate.id,
    file,
    filename: candidate.filename,
    mediaType:
      candidate.mediaType || response.headers.get("Content-Type") || "application/octet-stream",
  };
}

function isRichProvider(source: Source): source is Exclude<Source, "Direct file" | null> {
  return (
    source === "Printables" ||
    source === "MakerWorld" ||
    source === "Thingiverse" ||
    source === "Cults"
  );
}
async function readVisibleCapture() {
  if (!activePage?.id || !activePage.url || !isRichProvider(activeSource)) return null;
  const tabId = activePage.id;
  const pageUrl = activePage.url;
  const provider = activeSource;
  const results = await browser.scripting.executeScript({
    target: { tabId },
    world: "MAIN",
    func: (limits: { maxScripts: number; maxScriptBytes: number; maxTotalBytes: number }) => {
      const pageTitle = document.title;
      const challengeDetected =
        /captcha|verify you are human|access denied/i.test(document.title) ||
        Boolean(document.querySelector('iframe[src*="challenge"], [class*="captcha"]'));
      const scripts = [...document.querySelectorAll('script[type="application/ld+json"]')];
      const safeResult = () => ({ pageTitle, challengeDetected, jsonLd: [] });
      if (scripts.length > limits.maxScripts) return safeResult();

      const encoder = new TextEncoder();
      const jsonLd: string[] = [];
      let totalBytes = 0;
      for (const script of scripts) {
        const text = script.textContent || "";
        const scriptBytes = encoder.encode(text).byteLength;
        if (
          scriptBytes > limits.maxScriptBytes ||
          totalBytes + scriptBytes > limits.maxTotalBytes
        ) {
          return safeResult();
        }
        totalBytes += scriptBytes;
        jsonLd.push(text);
      }
      return { pageTitle, challengeDetected, jsonLd };
    },
    args: [
      {
        maxScripts: JSON_LD_MAX_SCRIPTS,
        maxScriptBytes: JSON_LD_MAX_SCRIPT_BYTES,
        maxTotalBytes: JSON_LD_MAX_TOTAL_BYTES,
      },
    ],
  });
  const visible = results[0]?.result as VisibleCaptureResult | undefined;
  if (!visible || !Array.isArray(visible.jsonLd)) return null;
  if (provider === "Printables" && visible.challengeDetected) {
    throw new Error(
      "user_file_required: Printables blocked browser capture. Attach the file manually in Pending Imports.",
    );
  }
  return buildBrowserCaptureMessage({
    provider,
    pageUrl,
    pageTitle: visible.pageTitle || activePage.title,
    jsonLd: visible.jsonLd,
  });
}

async function takePreparedSetup(page: Page) {
  if (
    page.id === undefined ||
    !page.url ||
    !Number.isInteger(page.id) ||
    !/^https?:/i.test(page.url)
  )
    return null;
  const tabId = page.id;
  const pageUrl = page.url;
  try {
    const results = await browser.scripting.executeScript({
      target: { tabId },
      world: "MAIN",
      func: (storageKey) => {
        const value = window.sessionStorage.getItem(storageKey);
        if (value) window.sessionStorage.removeItem(storageKey);
        return value;
      },
      args: [BROWSER_EXTENSION_SETUP_STORAGE_KEY],
    });
    return parseBrowserExtensionSetup(results[0]?.result, pageUrl);
  } catch {
    return null;
  }
}

connectionForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    const config = configFromForm();
    await establishConnection(config, { requestPermission: true, persist: true });
    showStatus("Connection verified. This browser is ready to import.", "success");
  } catch (error) {
    if (connectionState === "connected") {
      showStatus(`Connection was not updated. ${messageFrom(error)}`, "error");
    } else {
      showStatus();
    }
  }
});

editButton.addEventListener("click", () => {
  editingConnection = true;
  connectionPanel.hidden = false;
  editButton.hidden = true;
  cancelButton.hidden = false;
  disconnectButton.hidden = false;
  connectButton.textContent = "Update connection";
  renderImportAvailability();
  showStatus();
  vaultInput.focus();
});

cancelButton.addEventListener("click", () => {
  editingConnection = false;
  fillConnectionForm(connectedConfig);
  connectionPanel.hidden = Boolean(connectedConfig);
  cancelButton.hidden = true;
  disconnectButton.hidden = true;
  connectButton.textContent = "Connect";
  renderConnection("connected", {
    config: connectedConfig ?? undefined,
    profile: connectedProfile,
  });
  showStatus();
});

disconnectButton.addEventListener("click", async () => {
  const previous = connectedConfig;
  const loopbackPermission = previous ? isLocalVault(previous.vault) : false;
  try {
    await browser.storage.remove(["apiKey", "username", "deviceCredential"]);
  } catch (error) {
    showStatus(`Couldn't remove the stored browser credential. ${messageFrom(error)}`, "error");
    return;
  }

  let permissionStillGranted = false;
  if (previous && !loopbackPermission) {
    const origins = [permissionOrigin(previous)];
    try {
      await browser.permissions.remove({ origins });
      permissionStillGranted = await browser.permissions.contains({ origins });
    } catch {
      permissionStillGranted = true;
    }
  }
  connectedConfig = null;
  connectedProfile = null;
  accessToken = null;
  editingConnection = false;
  keyInput.value = "";
  clearInboxAction();
  clearCandidateSelection();
  clearManualFileSelection();
  connectionPanel.hidden = false;
  cancelButton.hidden = true;
  disconnectButton.hidden = true;
  connectButton.textContent = "Connect";
  renderConnection("disconnected");
  showStatus(
    permissionStillGranted
      ? "Disconnected and removed the stored browser credential, but Chrome kept the vault permission. Remove it from the extension's site access settings."
      : loopbackPermission
        ? "Disconnected. The stored browser credential was removed; built-in loopback access contains no credentials."
        : "Disconnected. The stored browser credential and vault permission were removed from this browser.",
    permissionStillGranted ? "error" : "success",
  );
});

apiSettingsButton.addEventListener("click", () => {
  try {
    const base = normalizeVault(vaultInput.value);
    browser.tabs.create({ url: `${base}/settings?section=imports` });
  } catch (error) {
    showStatus(messageFrom(error), "error");
  }
});

captureButton.addEventListener("click", async () => {
  if (!connectedConfig || connectionState !== "connected") {
    showStatus("Connect PrintStash before importing.", "error");
    return;
  }
  importBusy = true;
  setButtonBusy(captureButton, true, "Sending…");
  renderImportAvailability();
  showStatus();
  try {
    if (pendingManualCapture) {
      const authorization = accessToken || connectedConfig.deviceCredential;
      if (!authorization)
        throw new Error("The browser connection expired. Connect PrintStash again.");
      const file = selectedManualFile(pendingManualCapture);
      await captureRichFiles({
        vault: connectedConfig.vault,
        authorization,
        sourceUrl: pendingManualCapture.source.canonical_url,
        title: activePage?.title,
        captureSource: pendingManualCapture.source,
        files: [file],
      });
      const inboxUrl = `${normalizeVault(connectedConfig.vault)}/inbox`;
      clearManualFileSelection();
      inboxButton.hidden = false;
      inboxButton.dataset.url = inboxUrl;
      showStatus("Selected file and source metadata sent to Pending Imports.", "success");
      return;
    }
    if (pendingPrintablesCapture) {
      const selected = selectedPrintablesCandidates(pendingPrintablesCapture);
      if (selected.length === 0) {
        showStatus("Select at least one Printables file to upload.", "error");
        return;
      }
      const authorization = accessToken || connectedConfig.deviceCredential;
      if (!authorization)
        throw new Error("The browser connection expired. Connect PrintStash again.");
      const files = await Promise.all(selected.map(downloadPrintablesCandidate));
      await captureRichFiles({
        vault: connectedConfig.vault,
        authorization,
        sourceUrl: pendingPrintablesCapture.source.canonical_url,
        title: activePage?.title,
        captureSource: pendingPrintablesCapture.source,
        files,
      });
      const inboxUrl = `${normalizeVault(connectedConfig.vault)}/inbox`;
      clearCandidateSelection();
      inboxButton.hidden = false;
      inboxButton.dataset.url = inboxUrl;
      showStatus("Selected Printables files sent to Pending Imports.", "success");
      return;
    }
    const visibleCapture = await readVisibleCapture();
    if (visibleCapture?.state === "manual_file_required") {
      renderManualFileSelection(visibleCapture);
      showStatus(visibleCapture.message);
      return;
    }
    if (activeSource === "Printables" && visibleCapture && visibleCapture.candidates.length > 0) {
      renderCandidateSelection(visibleCapture);
      showStatus("Review the selected Printables files, then confirm the upload.");
      return;
    }
    const pageUrl = activePage?.url;
    if (!pageUrl) throw new Error("The active tab has no capture URL.");
    const result = await captureModelPage({
      ...connectedConfig,
      accessToken: accessToken ?? undefined,
      pageUrl,
      title: activePage?.title ?? undefined,
      captureSource: visibleCapture?.source,
      requestPageJson,
      ensureOriginPermission,
    });
    inboxButton.hidden = false;
    inboxButton.dataset.url = result.inboxUrl;
    showStatus(`Model from ${result.source} sent to Pending Imports.`, "success");
  } catch (error) {
    const message = messageFrom(error);
    const connectionLost = [
      "Couldn't reach PrintStash",
      "connection expired",
      "username or API key is incorrect",
      "browser connection is no longer valid",
    ].some((marker) => message.includes(marker));
    if (connectionLost) {
      accessToken = null;
      editingConnection = false;
      connectionPanel.hidden = false;
      cancelButton.hidden = true;
      renderConnection("error", { detail: message });
      showStatus();
    } else {
      showStatus(message, "error");
    }
  } finally {
    importBusy = false;
    setButtonBusy(captureButton, false);
    if (pendingPrintablesCapture) {
      captureButton.textContent = "Confirm and upload selected files";
    } else if (pendingManualCapture) {
      captureButton.textContent = "Upload selected file";
    }
    renderImportAvailability();
  }
});

inboxButton.addEventListener("click", () => {
  if (inboxButton.dataset.url) browser.tabs.create({ url: inboxButton.dataset.url });
});

async function initialize() {
  const [stored, tabs] = await Promise.all([
    browser.storage.get(["vault", "username", "apiKey", "deviceCredential"]),
    browser.tabs.query({ active: true, currentWindow: true }),
  ]);
  activePage = tabs[0] || null;
  renderActivePage();
  const prepared = await takePreparedSetup(activePage);
  if (prepared) {
    fillConnectionForm(prepared);
    connectionPanel.hidden = false;
    renderConnection("disconnected", {
      detail: "Setup received from this PrintStash tab.",
    });
    connectButton.textContent = "Finish setup";
    const origins = [permissionOrigin(prepared)];
    const alreadyAllowed = await browser.permissions.contains({ origins }).catch(() => false);
    if (alreadyAllowed) {
      try {
        await establishConnection(prepared, { requestPermission: false, persist: true });
        showStatus("Extension setup completed and connection verified.", "success");
      } catch {
        showStatus();
      }
    } else {
      showStatus("Setup received. Choose Finish setup to approve access to this vault.", "success");
    }
    return;
  }
  const storedConfig: Config = {
    vault: typeof stored.vault === "string" ? stored.vault : "",
    ...(typeof stored.username === "string" ? { username: stored.username } : {}),
    ...(typeof stored.apiKey === "string" ? { apiKey: stored.apiKey } : {}),
    ...(typeof stored.deviceCredential === "string"
      ? { deviceCredential: stored.deviceCredential }
      : {}),
  };
  fillConnectionForm(storedConfig);

  if (
    !storedConfig.vault ||
    (!storedConfig.deviceCredential && (!storedConfig.username || !storedConfig.apiKey))
  ) {
    connectionPanel.hidden = false;
    renderConnection("disconnected");
    return;
  }

  connectionPanel.hidden = true;
  try {
    await establishConnection(storedConfig, { requestPermission: false, persist: false });
  } catch {}
}

initialize().catch((error) => {
  connectionPanel.hidden = false;
  renderConnection("error", { detail: messageFrom(error) });
  showStatus(messageFrom(error), "error");
});
