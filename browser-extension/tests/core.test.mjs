import assert from "node:assert/strict";
import test from "node:test";

import {
  captureModelPage,
  classifyModelPage,
  makerWorldDownload,
  normalizeVault,
} from "../core.mjs";

test("recognizes MakerWorld model pages and Printables model and collection pages", () => {
  assert.equal(classifyModelPage("https://makerworld.com/en/models/1234-widget"), "MakerWorld");
  assert.equal(classifyModelPage("https://www.makerworld.com/en/collections/42-parts"), null);
  assert.equal(classifyModelPage("https://www.printables.com/model/3161-3d-benchy/files"), "Printables");
  assert.equal(classifyModelPage("https://www.printables.com/@user/collections/77"), "Printables");
  assert.equal(classifyModelPage("https://example.com/model/3161"), null);
  assert.equal(classifyModelPage("https://evilmakerworld.com/models/123"), null);
});

test("resolves the MakerWorld package inside the authenticated page", async () => {
  const requests = [];
  const result = await makerWorldDownload({
    pageUrl: "https://makerworld.com/en/models/1234-widget",
    requestPageJson: async (url) => {
      requests.push(url);
      if (url.endsWith("/design/1234")) {
        return { data: { defaultInstanceId: 77 } };
      }
      return {
        data: {
          files: [
            { downloadUrl: "https://makerworld.bblmw.com/makerlab/widget.3mf?signature=secret" },
          ],
        },
      };
    },
  });

  assert.deepEqual(requests, [
    "https://makerworld.com/api/v1/design-service/design/1234",
    "https://makerworld.com/api/v1/design-service/instance/77/f3mf?type=download&fileType=3mfstl",
  ]);
  assert.equal(result.filename, "widget.3mf");
  assert.match(result.url, /^https:\/\/makerworld\.bblmw\.com\//);
});

test("falls back to MakerWorld's first listed instance, not the design id", async () => {
  const requests = [];
  await makerWorldDownload({
    pageUrl: "https://makerworld.com/en/models/1234-widget",
    requestPageJson: async (url) => {
      requests.push(url);
      if (url.endsWith("/design/1234")) {
        return { data: { id: 1234, instances: [{ id: 88 }] } };
      }
      return { url: "https://makerworld.bblmw.com/files/widget.3mf" };
    },
  });
  assert.match(requests[1], /\/instance\/88\/f3mf/);
});

test("normalizes a self-hosted Vault URL without accepting credentials", () => {
  assert.equal(normalizeVault(" https://prints.example.com/app/ "), "https://prints.example.com/app");
  assert.throws(() => normalizeVault("ftp://prints.example.com"), /HTTP or HTTPS/);
  assert.throws(() => normalizeVault("https://admin:secret@prints.example.com"), /credentials/);
});

test("logs in with a named API key and captures the browser source", async () => {
  const calls = [];
  const fetchImpl = async (url, options) => {
    calls.push({ url, options, body: JSON.parse(options.body) });
    if (url.endsWith("/auth/login")) {
      return { ok: true, json: async () => ({ access_token: "jwt" }) };
    }
    return { ok: true, json: async () => ({ id: 9, state: "captured" }) };
  };

  const result = await captureModelPage({
    fetchImpl,
    vault: "https://prints.example.com/",
    username: "owner",
    apiKey: "psk_secret",
    pageUrl: "https://www.printables.com/model/3161-3d-benchy/files",
    title: "3DBenchy",
  });

  assert.equal(result.source, "Printables");
  assert.equal(result.item.id, 9);
  assert.equal(result.inboxUrl, "https://prints.example.com/inbox");
  assert.deepEqual(calls[0].body, { username: "owner", api_key: "psk_secret", remember_me: false });
  assert.deepEqual(calls[1].body, {
    url: "https://www.printables.com/model/3161-3d-benchy/files",
    title: "3DBenchy",
    source_kind: "browser",
  });
  assert.equal(calls[1].options.headers.Authorization, "Bearer jwt");
});

test("uploads MakerWorld bytes without sending site cookies to the Vault", async () => {
  const calls = [];
  const granted = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options });
    if (url.endsWith("/auth/login")) {
      return new Response(JSON.stringify({ access_token: "jwt" }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }
    if (url.startsWith("https://makerworld.bblmw.com/")) {
      return new Response(new Blob(["3mf bytes"]), {
        status: 200,
        headers: { "Content-Type": "application/octet-stream" },
      });
    }
    return new Response(JSON.stringify({ id: 10, state: "review" }), {
      status: 201,
      headers: { "Content-Type": "application/json" },
    });
  };

  const result = await captureModelPage({
    fetchImpl,
    vault: "https://prints.example.com/",
    username: "owner",
    apiKey: "psk_secret",
    pageUrl: "https://makerworld.com/en/models/1234-widget",
    title: "Widget",
    requestPageJson: async (url) => {
      if (url.endsWith("/design/1234")) return { data: { defaultInstanceId: "77" } };
      return { url: "https://makerworld.bblmw.com/files/widget.3mf?signature=secret" };
    },
    ensureOriginPermission: async (origin) => granted.push(origin),
  });

  assert.equal(result.item.id, 10);
  assert.deepEqual(granted, ["https://makerworld.bblmw.com/*"]);
  assert.equal(calls.length, 3);
  const upload = calls[2];
  assert.equal(upload.url, "https://prints.example.com/api/v1/inbox/browser-upload");
  assert.equal(upload.options.headers.Authorization, "Bearer jwt");
  assert.equal(upload.options.headers.Cookie, undefined);
  assert.equal(upload.options.body.get("source_url"), "https://makerworld.com/en/models/1234-widget");
  assert.equal(upload.options.body.get("title"), "Widget");
  assert.equal(upload.options.body.get("file").name, "widget.3mf");
});

test("rejects unsupported pages before sending credentials", async () => {
  let called = false;
  await assert.rejects(
    captureModelPage({
      fetchImpl: async () => { called = true; },
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_secret",
      pageUrl: "https://example.com/model/1",
    }),
    /MakerWorld model or Printables/,
  );
  assert.equal(called, false);
});
