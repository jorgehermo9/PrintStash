import { readFile } from "node:fs/promises";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { fakeBrowser } from "@webext-core/fake-browser";

const popupHtml = await readFile("entrypoints/popup/index.html", "utf8");

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function requiredElement<T extends Element>(selector: string, constructor: { new (): T }): T {
  const element = document.querySelector(selector);
  if (!(element instanceof constructor)) throw new Error(`Missing ${selector}`);
  return element;
}

const button = (selector: string) => requiredElement(selector, HTMLButtonElement);
const element = (selector: string) => requiredElement(selector, HTMLElement);

function stringBody(options: RequestInit): string {
  if (typeof options.body !== "string") throw new TypeError("Expected a string request body");
  return options.body;
}

async function settle() {
  await new Promise((resolve) => setTimeout(resolve, 0));
  await new Promise((resolve) => setTimeout(resolve, 0));
}

describe("popup browser adapters", () => {
  beforeEach(() => {
    vi.resetModules();
    fakeBrowser.reset();
    document.documentElement.innerHTML = popupHtml;
    vi.stubGlobal("chrome", fakeBrowser);
    vi.stubGlobal("browser", fakeBrowser);
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "3DBenchy",
        url: "https://www.printables.com/model/3161-3d-benchy/files",
      },
    ]);
    fakeBrowser.permissions.contains = vi.fn().mockResolvedValue(true);
    fakeBrowser.permissions.request = vi.fn().mockResolvedValue(true);
    fakeBrowser.permissions.remove = vi.fn().mockResolvedValue(true);
    fakeBrowser.scripting.executeScript = vi.fn();
    fakeBrowser.tabs.create = vi.fn();
  });

  it("shows the loaded capture protocol marker", () => {
    expect(element("#runtime-marker").textContent).toBe("Capture protocol v2");
    expect(element("#runtime-marker").hidden).toBe(false);
  });

  it("restores settings and checks the vault through fake storage and permission APIs", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url) => {
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      return response({ username: "owner", is_superuser: false });
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();

    expect(fakeBrowser.permissions.contains).toHaveBeenCalledWith({
      origins: ["https://prints.example.com/*"],
    });
    expect(element("#connection-title").textContent).toBe("Connected");
    expect(button("#capture").disabled).toBe(false);
    expect(fetchImpl).toHaveBeenCalledTimes(3);
  });

  it("opens the Imports settings section used for browser pairing", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      deviceCredential: "device-secret",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response({ status: "ok", name: "PrintStash" })),
    );

    await import("../popup.ts");
    await settle();
    button("#open-api-settings").click();

    expect(fakeBrowser.tabs.create).toHaveBeenCalledWith({
      url: "https://prints.example.com/settings?section=imports",
    });
  });

  it("restores a paired device credential and clears it on disconnect", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      deviceCredential: "device-secret",
    });
    const fetchImpl = vi.fn(async (url) => {
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      return response({}, 404);
    });
    vi.stubGlobal("fetch", fetchImpl);
    await import("../popup.ts");
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();
    const stored = await fakeBrowser.storage.local.get([
      "vault",
      "deviceCredential",
      "username",
      "apiKey",
    ]);
    expect(stored).toMatchObject({
      vault: "https://prints.example.com",
      deviceCredential: "device-secret",
    });
    expect(stored.username).toBeUndefined();
    expect(stored.apiKey).toBeUndefined();
    expect(fetchImpl).toHaveBeenCalledTimes(1);
    button("#edit-connection").click();
    button("#disconnect").click();
    await settle();
    expect(await fakeBrowser.storage.local.get("deviceCredential")).toEqual({});
    expect(fakeBrowser.permissions.remove).toHaveBeenCalledWith({
      origins: ["https://prints.example.com/*"],
    });
  });

  it("falls back to a local Printables file without metadata-only capture", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url, options: RequestInit = {}) => {
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url.endsWith("/capture-upload-slots")) {
        const body = JSON.parse(stringBody(options));
        return response(
          {
            item: { id: 22 },
            slots: [
              {
                id: "slot-printables-manual",
                role: "file",
                source_file_id: "3161:benchy.3mf",
                filename: "benchy.3mf",
                media_type: "model/3mf",
                size_bytes: 4,
                sha256: body.files[0].sha256,
              },
            ],
          },
          201,
        );
      }
      if (url.endsWith("/capture-upload-slots/slot-printables-manual"))
        return new Response(null, { status: 204 });
      if (url.endsWith("/capture-upload-finalize")) return response({ id: 22, state: "review" });
      throw new Error(`Unexpected metadata-only capture: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([
      {
        result: {
          pageTitle: "3DBenchy",
          jsonLd: [
            JSON.stringify({
              name: "3DBenchy",
              image: "data:image/png;base64,secret",
              contentUrl: "https://media.printables.com/files/benchy.3mf?signature=signed-secret",
            }),
          ],
        },
      },
    ]);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    expect(element("#manual-file-panel").hidden).toBe(false);
    expect(element("#status").textContent).toContain("Choose a downloaded Printables file");
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/api/v1/inbox"))).toBe(false);

    const input = requiredElement("#manual-file", HTMLInputElement);
    const file = new File(["mesh"], "benchy.3mf", { type: "model/3mf" });
    Object.defineProperty(input, "files", {
      configurable: true,
      value: { 0: file, length: 1, item: (index: number) => (index === 0 ? file : null) },
    });
    button("#capture").click();
    for (let attempt = 0; attempt < 10; attempt += 1) await settle();

    const createCall = fetchImpl.mock.calls.find(([url]) => url.endsWith("/capture-upload-slots"));
    if (createCall === undefined || createCall[1] === undefined)
      throw new Error("Missing durable slot creation request");
    const createBody = stringBody(createCall[1]);
    expect(JSON.parse(createBody)).toMatchObject({
      capture_source: {
        provider: "printables",
        canonical_url: "https://www.printables.com/model/3161-3d-benchy/files",
        source_item_id: "3161",
        fields: { title: { value: "3DBenchy", origin: "confirmed" } },
      },
      files: [
        {
          id: "3161:benchy.3mf",
          filename: "benchy.3mf",
          size_bytes: 4,
          sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
        },
      ],
    });
    expect(createBody).not.toContain("psk_vault_secret");
    expect(createBody).not.toContain("vault-jwt");
    expect(createBody).not.toContain("base64");
    expect(createBody).not.toContain("signed-secret");
    expect(fetchImpl.mock.calls.map(([url]) => url)).toEqual([
      "https://prints.example.com/api/v1/health",
      "https://prints.example.com/api/v1/auth/login",
      "https://prints.example.com/api/v1/auth/me",
      "https://prints.example.com/api/v1/inbox/capture-upload-slots",
      "https://prints.example.com/api/v1/inbox/capture-upload-slots/slot-printables-manual",
      "https://prints.example.com/api/v1/inbox/22/capture-upload-finalize",
    ]);
    expect(element("#status").textContent).toContain("sent to Pending Imports");
  });

  it("fails closed to a local Printables file when capture acquisition is unusable", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url.endsWith("/inbox")) return response({ detail: "user_file_required" }, 400);
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValueOnce([{ result: null }])
      .mockResolvedValueOnce([{ result: { pageTitle: "3DBenchy", jsonLd: "not-an-array" } }]);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    expect(element("#manual-file-panel").hidden).toBe(false);
    expect(element("#status").textContent).toContain("Choose a downloaded Printables file");
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/api/v1/inbox"))).toBe(false);
  });

  it("bounds JSON-LD scripts before returning page metadata to the popup", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
        if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
        if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
        return response({ id: 22, state: "captured" }, 202);
      }),
    );
    fakeBrowser.scripting.executeScript = vi
      .fn()
      .mockResolvedValue([{ result: { pageTitle: "3DBenchy", jsonLd: [] } }]);
    await import("../popup.ts");
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    const executeScript = vi.mocked(fakeBrowser.scripting.executeScript);
    const invocation = executeScript.mock.calls.find(([details]) => {
      const args = details.args;
      return (
        Array.isArray(args) &&
        typeof args[0] === "object" &&
        args[0] !== null &&
        "maxScripts" in args[0] &&
        "maxScriptBytes" in args[0] &&
        "maxTotalBytes" in args[0]
      );
    });
    const details = invocation?.[0];
    if (!details?.func || !details.args?.[0]) throw new Error("Missing JSON-LD collection script");
    const collect = details.func as (limits: {
      maxScripts: number;
      maxScriptBytes: number;
      maxTotalBytes: number;
    }) => { jsonLd: string[] };
    const limits = details.args[0] as {
      maxScripts: number;
      maxScriptBytes: number;
      maxTotalBytes: number;
    };
    const appendJsonLd = (count: number, text: string) => {
      for (let index = 0; index < count; index += 1) {
        const script = document.createElement("script");
        script.type = "application/ld+json";
        script.textContent = text;
        document.body.append(script);
      }
    };
    const clearJsonLd = () => {
      document.querySelectorAll('script[type="application/ld+json"]').forEach((script) => {
        script.remove();
      });
    };

    appendJsonLd(limits.maxScripts + 1, '{"name":"hostile"}');
    expect(collect(limits).jsonLd).toEqual([]);
    clearJsonLd();

    const aggregateText = "x".repeat(Math.floor(limits.maxTotalBytes / limits.maxScripts) + 1);
    appendJsonLd(limits.maxScripts, aggregateText);
    expect(aggregateText.length).toBeLessThan(limits.maxScriptBytes);
    expect(collect(limits).jsonLd).toEqual([]);
    clearJsonLd();

    appendJsonLd(1, "x".repeat(limits.maxScriptBytes + 1));
    expect(collect(limits).jsonLd).toEqual([]);
  });

  it("requires Printables file confirmation before using durable upload slots", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([
      {
        result: {
          pageTitle: "3DBenchy",
          jsonLd: [
            JSON.stringify({
              name: "3DBenchy",
              distribution: [
                {
                  contentUrl:
                    "https://media.printables.com/files/benchy.3mf?signature=signed-secret",
                  encodingFormat: "model/3mf",
                  contentSize: "4",
                },
              ],
            }),
          ],
        },
      },
    ]);
    const fetchImpl = vi.fn(async (url: string, options: RequestInit = {}) => {
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url.startsWith("https://media.printables.com/")) {
        return new Response("mesh", {
          status: 200,
          headers: { "Content-Type": "model/3mf" },
        });
      }
      if (url.endsWith("/capture-upload-slots")) {
        const body = typeof options.body === "string" ? JSON.parse(options.body) : null;
        return response(
          {
            item: { id: 51 },
            slots: [
              {
                id: "slot-printables",
                role: "file",
                source_file_id: "3161:benchy.3mf",
                filename: "benchy.3mf",
                media_type: "model/3mf",
                size_bytes: 4,
                sha256: body.files[0].sha256,
              },
            ],
          },
          201,
        );
      }
      if (url.endsWith("/capture-upload-slots/slot-printables")) {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/capture-upload-finalize")) return response({ id: 51, state: "ready" });
      throw new Error(`Unexpected request: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    expect(element("#candidate-panel").hidden).toBe(false);
    expect(button("#capture").textContent).toBe("Confirm and upload selected files");
    expect(fetchImpl).toHaveBeenCalledTimes(3);

    button("#capture").click();
    for (let attempt = 0; attempt < 10; attempt += 1) await settle();

    expect(
      fetchImpl.mock.calls.filter(([url]) => url.startsWith("https://media.printables.com/")),
    ).toHaveLength(1);
    const createCall = fetchImpl.mock.calls.find(([url]) => url.endsWith("/capture-upload-slots"));
    if (createCall === undefined || createCall[1] === undefined) {
      throw new Error(
        `Missing durable slot creation request: ${fetchImpl.mock.calls.map(([url]) => url).join(", ")}; status=${element("#status").textContent}`,
      );
    }
    const createBody = stringBody(createCall[1]);
    expect(JSON.parse(createBody)).toMatchObject({
      capture_source: { provider: "printables", source_item_id: "3161" },
      files: [
        {
          id: "3161:benchy.3mf",
          filename: "benchy.3mf",
          size_bytes: 4,
          sha256: expect.stringMatching(/^[a-f0-9]{64}$/),
        },
      ],
    });
    expect(createBody).not.toContain("signed-secret");
    expect(fetchImpl.mock.calls.some(([url]) => url.endsWith("/capture-upload-finalize"))).toBe(
      true,
    );
    expect(element("#status").textContent).toContain("sent to Pending Imports");
  });

  it("stops on a changed Printables file contract and directs manual attachment without retry", async () => {
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([
      {
        result: {
          pageTitle: "Changed Printables page",
          jsonLd: [
            JSON.stringify({
              name: "Changed model",
              distribution: [{ download: "https://media.printables.com/files/unsupported.3mf" }],
            }),
          ],
        },
      },
    ]);
    const fetchImpl = vi.fn(async (url: string) => {
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      throw new Error(`Unexpected capture retry: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    expect(element("#status").textContent).toContain("attach it in Pending Imports");
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(element("#candidate-panel").hidden).toBe(true);
  });

  it("uploads a user-selected Thingiverse file through durable slots without URL retry", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Whistle",
        url: "https://www.thingiverse.com/thing:763622/files",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([
      {
        result: {
          pageTitle: "Whistle",
          jsonLd: [JSON.stringify({ name: "Whistle", author: "Ada" })],
        },
      },
    ]);
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string, options: RequestInit = {}) => {
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url.endsWith("/capture-upload-slots")) {
        const body = JSON.parse(stringBody(options));
        return response(
          {
            item: { id: 61 },
            slots: [
              {
                id: "slot-thingiverse",
                role: "file",
                source_file_id: "763622:whistle.stl",
                filename: "whistle.stl",
                media_type: "model/stl",
                size_bytes: 4,
                sha256: body.files[0].sha256,
              },
            ],
          },
          201,
        );
      }
      if (url.endsWith("/capture-upload-slots/slot-thingiverse")) {
        return new Response(null, { status: 204 });
      }
      if (url.endsWith("/capture-upload-finalize")) return response({ id: 61, state: "ready" });
      throw new Error(`Unexpected Thingiverse URL retry: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    await settle();

    expect(element("#status").textContent).toContain("Choose a downloaded Thingiverse file");
    expect(fetchImpl).toHaveBeenCalledTimes(3);
    expect(element("#manual-file-panel").hidden).toBe(false);

    const input = requiredElement("#manual-file", HTMLInputElement);
    const file = new File(["mesh"], "whistle.stl", { type: "model/stl" });
    Object.defineProperty(input, "files", {
      configurable: true,
      value: { 0: file, length: 1, item: (index: number) => (index === 0 ? file : null) },
    });
    button("#capture").click();
    for (let attempt = 0; attempt < 8; attempt += 1) await settle();

    const createCall = fetchImpl.mock.calls.find(([url]) => url.endsWith("/capture-upload-slots"));
    if (createCall === undefined || createCall[1] === undefined) {
      throw new Error(
        `Missing Thingiverse slot request: ${fetchImpl.mock.calls.map(([url]) => url).join(", ")}; status=${element("#status").textContent}`,
      );
    }
    expect(JSON.parse(stringBody(createCall[1]))).toMatchObject({
      capture_source: { provider: "thingiverse", source_item_id: "763622" },
      files: [{ id: "763622:whistle.stl", filename: "whistle.stl" }],
    });
    expect(fetchImpl).toHaveBeenCalledTimes(6);
    expect(element("#status").textContent).toContain("sent to Pending Imports");
  });

  it("uploads a user-selected Cults file through slots, PUT, and finalize without URL capture or retry", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Cult cube",
        url: "https://cults3d.com/en/3d-model/art/cult-cube",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi.fn().mockResolvedValue([
      {
        result: {
          pageTitle: "Cult cube",
          jsonLd: [JSON.stringify({ name: "Cult cube", author: "Ada" })],
        },
      },
    ]);
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string, options: RequestInit = {}) => {
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url.endsWith("/capture-upload-slots")) {
        const body = JSON.parse(stringBody(options));
        return response(
          {
            item: { id: 62 },
            slots: [
              {
                id: "slot-cults",
                role: "file",
                source_file_id: "cult-cube:cult-cube.stl",
                filename: "cult-cube.stl",
                media_type: "model/stl",
                size_bytes: 4,
                sha256: body.files[0].sha256,
              },
            ],
          },
          201,
        );
      }
      if (url.endsWith("/capture-upload-slots/slot-cults"))
        return new Response(null, { status: 204 });
      if (url.endsWith("/capture-upload-finalize")) return response({ id: 62, state: "ready" });
      throw new Error(`Unexpected Cults URL POST/retry: ${url}`);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 4; attempt += 1) await settle();

    expect(element("#manual-file-panel").hidden).toBe(false);
    const input = requiredElement("#manual-file", HTMLInputElement);
    const file = new File(["mesh"], "cult-cube.stl", { type: "model/stl" });
    Object.defineProperty(input, "files", {
      configurable: true,
      value: { 0: file, length: 1, item: (index: number) => (index === 0 ? file : null) },
    });
    button("#capture").click();
    for (let attempt = 0; attempt < 8; attempt += 1) await settle();

    const captureRequests = fetchImpl.mock.calls.map(([url]) => url as string);
    expect(captureRequests).toEqual([
      "https://prints.example.com/api/v1/health",
      "https://prints.example.com/api/v1/auth/login",
      "https://prints.example.com/api/v1/auth/me",
      "https://prints.example.com/api/v1/inbox/capture-upload-slots",
      "https://prints.example.com/api/v1/inbox/capture-upload-slots/slot-cults",
      "https://prints.example.com/api/v1/inbox/62/capture-upload-finalize",
    ]);
    expect(element("#status").textContent).toContain("sent to Pending Imports");
  });

  it("threads the bounded MakerWorld source into the browser-upload form", async () => {
    fakeBrowser.tabs.query = vi.fn().mockResolvedValue([
      {
        id: 42,
        title: "Cube",
        url: "https://makerworld.com/en/models/77-calibration-cube",
      },
    ]);
    fakeBrowser.scripting.executeScript = vi.fn().mockImplementation(async ({ args = [] }) => {
      const [url] = args;
      if (typeof url === "string" && url.endsWith("/design/77")) {
        return [
          {
            result: {
              ok: true,
              status: 200,
              body: JSON.stringify({ data: { defaultInstanceId: "12" } }),
            },
          },
        ];
      }
      if (typeof url === "string" && url.includes("/instance/12/")) {
        return [
          {
            result: {
              ok: true,
              status: 200,
              body: JSON.stringify({
                url: "https://makerworld.bblmw.com/cube.3mf?signature=signed-secret",
              }),
            },
          },
        ];
      }
      return [
        {
          result: {
            pageTitle: "Cube",
            jsonLd: [
              JSON.stringify({
                name: "Cube",
                description: "Safe metadata <script>not forwarded()</script>",
                contentUrl: "https://makerworld.bblmw.com/cube.3mf?signature=signed-secret",
              }),
            ],
          },
        },
      ];
    });
    await fakeBrowser.storage.local.set({
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_vault_secret",
    });
    const fetchImpl = vi.fn(async (url: string, _options: RequestInit = {}) => {
      if (url.endsWith("/health")) return response({ status: "ok", name: "PrintStash" });
      if (url.endsWith("/login")) return response({ access_token: "vault-jwt" });
      if (url.endsWith("/me")) return response({ username: "owner", is_superuser: false });
      if (url.startsWith("https://makerworld.bblmw.com/"))
        return new Response(new Blob(["3mf"]), { status: 200 });
      return response({ id: 23, state: "review" }, 201);
    });
    vi.stubGlobal("fetch", fetchImpl);

    await import("../popup.ts");
    await settle();
    button("#capture").click();
    for (let attempt = 0; attempt < 8; attempt += 1) await settle();

    const upload = fetchImpl.mock.calls.find(([url]) =>
      url.endsWith("/api/v1/inbox/browser-upload"),
    );
    expect(upload).toBeDefined();
    if (upload === undefined) throw new Error("Missing browser upload request");
    expect(upload[0]).toBe("https://prints.example.com/api/v1/inbox/browser-upload");
    const uploadOptions = upload[1];
    if (uploadOptions === undefined) throw new Error("Missing browser upload options");
    const uploadBody = uploadOptions.body;
    if (!(uploadBody instanceof FormData)) throw new TypeError("Expected upload FormData");
    const captureSource = uploadBody.get("capture_source");
    if (typeof captureSource !== "string") throw new TypeError("Expected capture_source text");
    const source = JSON.parse(captureSource);
    expect(source).toMatchObject({
      provider: "makerworld",
      canonical_url: "https://makerworld.com/en/models/77-calibration-cube",
      source_item_id: "77",
      fields: {
        title: { value: "Cube", origin: "confirmed" },
        description: { value: "Safe metadata", origin: "confirmed" },
      },
    });
    expect(JSON.stringify(source)).not.toContain("signed-secret");
    expect(JSON.stringify(source)).not.toContain("not forwarded");
    expect(JSON.stringify(source)).not.toContain("psk_vault_secret");
  });
});
