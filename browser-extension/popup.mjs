import {
  BROWSER_EXTENSION_SETUP_STORAGE_KEY,
  captureModelPage,
  classifyModelPage,
  isLocalVault,
  normalizeVault,
  parseBrowserExtensionSetup,
  verifyVaultConnection,
} from "./core.mjs";

const vaultInput = document.querySelector("#vault");
const usernameInput = document.querySelector("#username");
const keyInput = document.querySelector("#key");
const pageLabel = document.querySelector("#page");
const sourceLabel = document.querySelector("#source");
const statusLabel = document.querySelector("#status");
const captureButton = document.querySelector("#capture");
const inboxButton = document.querySelector("#open-inbox");
const importPanel = document.querySelector("#import-panel");
const importHint = document.querySelector("#import-hint");
const connectionStatus = document.querySelector("#connection-status");
const connectionTitle = document.querySelector("#connection-title");
const connectionDetail = document.querySelector("#connection-detail");
const connectionPanel = document.querySelector("#connection-panel");
const connectionForm = document.querySelector("#connection-form");
const connectButton = document.querySelector("#connect");
const editButton = document.querySelector("#edit-connection");
const cancelButton = document.querySelector("#cancel-edit");
const disconnectButton = document.querySelector("#disconnect");
const apiSettingsButton = document.querySelector("#open-api-settings");

let activePage = null;
let activeSource = null;
let connectionState = "checking";
let connectedConfig = null;
let connectedProfile = null;
let accessToken = null;
let editingConnection = false;
let importBusy = false;

function messageFrom(error) {
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

function setButtonBusy(button, busy, busyLabel) {
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

function setConnectionFormBusy(busy) {
  connectionForm.toggleAttribute("aria-busy", busy);
  for (const control of connectionForm.elements) control.disabled = busy;
  setButtonBusy(connectButton, busy, "Connecting…");
}

function configFromForm() {
  const config = {
    vault: normalizeVault(vaultInput.value),
    username: usernameInput.value.trim(),
    apiKey: keyInput.value.trim(),
  };
  if (!config.username || !config.apiKey) {
    throw new Error("Vault URL, username, and named API key are required.");
  }
  return config;
}

function fillConnectionForm(config) {
  vaultInput.value = config?.vault || "";
  usernameInput.value = config?.username || "";
  keyInput.value = config?.apiKey || "";
}

function permissionOrigin(config) {
  const vault = new URL(config.vault);
  return isLocalVault(config.vault)
    ? `${vault.protocol}//${vault.hostname}/*`
    : `${vault.origin}/*`;
}

function connectionHost(config) {
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

function renderConnection(state, { config, profile, detail } = {}) {
  connectionState = state;
  connectionStatus.dataset.state = state;

  if (state === "connected") {
    const role = profile?.is_superuser ? "Admin" : "Member";
    connectionTitle.textContent = "Connected";
    connectionDetail.textContent = `${profile?.username || config.username} · ${connectionHost(config)} · ${role}`;
  } else if (state === "checking") {
    connectionTitle.textContent = "Checking connection…";
    connectionDetail.textContent = detail || (config ? connectionHost(config) : "Reading saved settings");
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
  activeSource = classifyModelPage(activePage?.url);
  pageLabel.textContent = activePage?.title || activePage?.url || "No active page";
  sourceLabel.textContent = activeSource
    ? activeSource === "Direct file"
      ? "Direct model file detected"
      : `${activeSource} page detected`
    : "Open a MakerWorld, Printables, or Thingiverse page, or a direct model file";
  sourceLabel.dataset.supported = activeSource ? "true" : "false";
  renderImportAvailability();
}

async function ensureVaultPermission(config, requestPermission) {
  const origins = [permissionOrigin(config)];
  const granted = requestPermission
    ? await chrome.permissions.request({ origins })
    : await chrome.permissions.contains({ origins });
  if (!granted) {
    throw new Error(
      requestPermission
        ? "Permission to contact this PrintStash vault was not granted."
        : "Reconnect this vault to restore its browser permission.",
    );
  }
}

async function establishConnection(config, { requestPermission, persist }) {
  const previous = connectedConfig
    ? { config: connectedConfig, profile: connectedProfile, token: accessToken }
    : null;
  const preservePrevious = Boolean(previous && editingConnection);
  renderConnection("checking", { config });
  setConnectionFormBusy(true);
  showStatus();
  try {
    await ensureVaultPermission(config, requestPermission);
    const verified = await verifyVaultConnection(config);
    const normalized = {
      vault: verified.base,
      username: config.username.trim(),
      apiKey: config.apiKey.trim(),
    };
    if (persist) await chrome.storage.local.set(normalized);

    if (previous && permissionOrigin(previous.config) !== permissionOrigin(normalized)) {
      await chrome.permissions
        .remove({ origins: [permissionOrigin(previous.config)] })
        .catch(() => false);
    }

    connectedConfig = normalized;
    connectedProfile = verified.user;
    accessToken = verified.accessToken;
    if (previous && previous.config.vault !== normalized.vault) clearInboxAction();
    editingConnection = false;
    connectionPanel.hidden = true;
    cancelButton.hidden = true;
    disconnectButton.hidden = true;
    fillConnectionForm(normalized);
    renderConnection("connected", { config: normalized, profile: verified.user });
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
      await chrome.permissions.remove({ origins: [failedOrigin] }).catch(() => false);
    }
    if (preservePrevious) {
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

async function requestPageJson(url) {
  if (!activePage?.id) throw new Error("The active tab is unavailable.");
  const results = await chrome.scripting.executeScript({
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
  const result = results[0]?.result;
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

async function ensureOriginPermission(origin) {
  const granted = await chrome.permissions.request({ origins: [origin] });
  if (!granted) throw new Error("Permission to download this MakerWorld file was not granted.");
}

async function takePreparedSetup(page) {
  if (!Number.isInteger(page?.id) || !/^https?:/i.test(page?.url || "")) return null;
  try {
    const results = await chrome.scripting.executeScript({
      target: { tabId: page.id },
      world: "MAIN",
      func: (storageKey) => {
        const value = window.sessionStorage.getItem(storageKey);
        if (value) window.sessionStorage.removeItem(storageKey);
        return value;
      },
      args: [BROWSER_EXTENSION_SETUP_STORAGE_KEY],
    });
    return parseBrowserExtensionSetup(results[0]?.result, page.url);
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
  renderConnection("connected", { config: connectedConfig, profile: connectedProfile });
  showStatus();
});

disconnectButton.addEventListener("click", async () => {
  const previous = connectedConfig;
  const loopbackPermission = previous ? isLocalVault(previous.vault) : false;
  try {
    await chrome.storage.local.remove(["apiKey"]);
  } catch (error) {
    showStatus(`Couldn't remove the stored API key. ${messageFrom(error)}`, "error");
    return;
  }

  let permissionStillGranted = false;
  if (previous && !loopbackPermission) {
    const origins = [permissionOrigin(previous)];
    try {
      await chrome.permissions.remove({ origins });
      permissionStillGranted = await chrome.permissions.contains({ origins });
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
  connectionPanel.hidden = false;
  cancelButton.hidden = true;
  disconnectButton.hidden = true;
  connectButton.textContent = "Connect";
  renderConnection("disconnected");
  showStatus(
    permissionStillGranted
      ? "Disconnected and removed the stored API key, but Chrome kept the vault permission. Remove it from the extension's site access settings."
      : loopbackPermission
        ? "Disconnected. The stored API key was removed; built-in loopback access contains no credentials."
        : "Disconnected. The stored API key and vault permission were removed from this browser.",
    permissionStillGranted ? "error" : "success",
  );
});

apiSettingsButton.addEventListener("click", () => {
  try {
    const base = normalizeVault(vaultInput.value);
    chrome.tabs.create({ url: `${base}/settings?section=access` });
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
    const result = await captureModelPage({
      ...connectedConfig,
      accessToken,
      pageUrl: activePage?.url,
      title: activePage?.title,
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
    renderImportAvailability();
  }
});

inboxButton.addEventListener("click", () => {
  if (inboxButton.dataset.url) chrome.tabs.create({ url: inboxButton.dataset.url });
});

async function initialize() {
  const [stored, tabs] = await Promise.all([
    chrome.storage.local.get(["vault", "username", "apiKey"]),
    chrome.tabs.query({ active: true, currentWindow: true }),
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
    const alreadyAllowed = await chrome.permissions.contains({ origins }).catch(() => false);
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
  fillConnectionForm(stored);

  if (!stored.vault || !stored.username || !stored.apiKey) {
    connectionPanel.hidden = false;
    renderConnection("disconnected");
    return;
  }

  connectionPanel.hidden = true;
  try {
    await establishConnection(stored, { requestPermission: false, persist: false });
  } catch {}
}

initialize().catch((error) => {
  connectionPanel.hidden = false;
  renderConnection("error", { detail: messageFrom(error) });
  showStatus(messageFrom(error), "error");
});
