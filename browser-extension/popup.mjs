import { captureModelPage, classifyModelPage, normalizeVault } from "./core.mjs";

const vaultInput = document.querySelector("#vault");
const usernameInput = document.querySelector("#username");
const keyInput = document.querySelector("#key");
const pageLabel = document.querySelector("#page");
const sourceLabel = document.querySelector("#source");
const statusLabel = document.querySelector("#status");
const captureButton = document.querySelector("#capture");
const configButton = document.querySelector("#save-config");
const inboxButton = document.querySelector("#open-inbox");

let activePage = null;

function showStatus(message, kind = "") {
  statusLabel.textContent = message;
  statusLabel.dataset.kind = kind;
}

function values() {
  return {
    vault: normalizeVault(vaultInput.value),
    username: usernameInput.value.trim(),
    apiKey: keyInput.value.trim(),
  };
}

async function saveConfig() {
  const config = values();
  if (!config.username || !config.apiKey) {
    throw new Error("Vault URL, username, and named API key are required.");
  }
  const origin = `${new URL(config.vault).origin}/*`;
  const granted = await chrome.permissions.request({ origins: [origin] });
  if (!granted) throw new Error("Permission to contact this Vault was not granted.");
  await chrome.storage.local.set(config);
  return config;
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

configButton.addEventListener("click", async () => {
  try {
    await saveConfig();
    showStatus("Settings saved.", "success");
  } catch (error) {
    showStatus(error.message, "error");
  }
});

captureButton.addEventListener("click", async () => {
  captureButton.disabled = true;
  try {
    const config = await saveConfig();
    const result = await captureModelPage({
      ...config,
      pageUrl: activePage?.url,
      title: activePage?.title,
      requestPageJson,
      ensureOriginPermission,
    });
    inboxButton.hidden = false;
    inboxButton.dataset.url = result.inboxUrl;
    showStatus(`${result.source} model sent to Pending Imports.`, "success");
  } catch (error) {
    showStatus(error.message, "error");
  } finally {
    captureButton.disabled = classifyModelPage(activePage?.url) === null;
  }
});

inboxButton.addEventListener("click", () => {
  if (inboxButton.dataset.url) chrome.tabs.create({ url: inboxButton.dataset.url });
});

Promise.all([
  chrome.storage.local.get(["vault", "username", "apiKey"]),
  chrome.tabs.query({ active: true, currentWindow: true }),
]).then(([config, tabs]) => {
  vaultInput.value = config.vault || "";
  usernameInput.value = config.username || "";
  keyInput.value = config.apiKey || "";
  activePage = tabs[0] || null;
  const source = classifyModelPage(activePage?.url);
  pageLabel.textContent = activePage?.title || activePage?.url || "No active page";
  sourceLabel.textContent = source
    ? `${source} page detected`
    : "Open a MakerWorld model or Printables model/collection page";
  sourceLabel.dataset.supported = source ? "true" : "false";
  captureButton.disabled = source === null;
});
