import assert from "node:assert/strict";
import test from "node:test";

import {
  captureModelPage,
  classifyModelPage,
  makerWorldDownload,
  normalizeVault,
  verifyVaultConnection,
} from "../core.mjs";

test("recognizes supported provider pages and direct model downloads", () => {
  assert.equal(classifyModelPage("https://makerworld.com/en/models/1234-widget"), "MakerWorld");
  assert.equal(classifyModelPage("https://www.makerworld.com/en/collections/42-parts"), null);
  assert.equal(classifyModelPage("https://www.printables.com/model/3161-3d-benchy/files"), "Printables");
  assert.equal(classifyModelPage("https://www.printables.com/@user/collections/77"), "Printables");
  assert.equal(classifyModelPage("https://www.thingiverse.com/thing:763622/files"), "Thingiverse");
  assert.equal(classifyModelPage("https://thingiverse.com/things/763622"), "Thingiverse");
  assert.equal(classifyModelPage("https://cdn.example.com/models/widget.3mf?download=1"), "Direct file");
  assert.equal(classifyModelPage("https://cdn.example.com/archive/parts.ZIP#files"), "Direct file");
  assert.equal(classifyModelPage("https://example.com/model/3161"), null);
  assert.equal(classifyModelPage("https://evilmakerworld.com/models/123"), null);
  assert.equal(classifyModelPage("https://evilthingiverse.com/thing:763622"), null);
  assert.equal(classifyModelPage("https://cdn.example.com/models/widget.pdf"), null);
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

test("verifies the PrintStash service and authenticated user before connecting", async () => {
  const calls = [];
  const fetchImpl = async (url, options = {}) => {
    calls.push({ url, options });
    if (url.endsWith("/health")) {
      return Response.json({ status: "ok", name: "PrintStash" });
    }
    if (url.endsWith("/auth/login")) {
      return Response.json({ access_token: "jwt", scope: "admin" });
    }
    return Response.json({
      id: 7,
      username: "owner",
      email: null,
      is_superuser: true,
      is_active: true,
      oidc_managed: false,
      created_at: "2026-01-01T00:00:00Z",
      updated_at: "2026-01-01T00:00:00Z",
    });
  };

  const result = await verifyVaultConnection({
    fetchImpl,
    vault: "https://prints.example.com/",
    username: " owner ",
    apiKey: " psk_secret ",
  });

  assert.equal(result.base, "https://prints.example.com");
  assert.equal(result.accessToken, "jwt");
  assert.equal(result.user.username, "owner");
  assert.equal(result.user.is_superuser, true);
  assert.deepEqual(calls.map((call) => call.url), [
    "https://prints.example.com/api/v1/health",
    "https://prints.example.com/api/v1/auth/login",
    "https://prints.example.com/api/v1/auth/me",
  ]);
  assert.equal(calls[0].options.headers.Authorization, undefined);
  assert.deepEqual(JSON.parse(calls[1].options.body), {
    username: "owner",
    api_key: "psk_secret",
    remember_me: false,
  });
  assert.equal(calls[2].options.headers.Authorization, "Bearer jwt");
});

test("does not send credentials when the configured URL is not PrintStash", async () => {
  const calls = [];
  await assert.rejects(
    verifyVaultConnection({
      fetchImpl: async (url, options = {}) => {
        calls.push({ url, options });
        return Response.json({ status: "ok", name: "Another service" });
      },
      vault: "https://wrong.example.com",
      username: "owner",
      apiKey: "psk_secret",
    }),
    /not a PrintStash server/,
  );
  assert.equal(calls.length, 1);
  assert.equal(calls[0].options.body, undefined);
});

test("turns API login codes and network failures into actionable connection errors", async () => {
  const health = () => Response.json({ status: "ok", name: "PrintStash" });
  let request = 0;
  await assert.rejects(
    verifyVaultConnection({
      fetchImpl: async () => {
        request += 1;
        return request === 1
          ? health()
          : Response.json({ detail: "invalid_credentials" }, { status: 401 });
      },
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "wrong",
    }),
    /username or API key is incorrect/,
  );

  await assert.rejects(
    verifyVaultConnection({
      fetchImpl: async () => { throw new TypeError("fetch failed"); },
      vault: "https://offline.example.com",
      username: "owner",
      apiKey: "psk_secret",
    }),
    /Couldn't reach PrintStash at offline\.example\.com/,
  );
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

test("captures Thingiverse and direct-file URLs through the server resolver", async () => {
  for (const [pageUrl, source] of [
    ["https://www.thingiverse.com/thing:763622/files", "Thingiverse"],
    ["https://cdn.example.com/models/widget.stl?download=1", "Direct file"],
  ]) {
    const calls = [];
    const fetchImpl = async (url, options) => {
      calls.push({ url, options });
      if (url.endsWith("/auth/login")) {
        return new Response(JSON.stringify({ access_token: "jwt" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify({ id: 11, state: "captured" }), {
        status: 202,
        headers: { "Content-Type": "application/json" },
      });
    };

    const result = await captureModelPage({
      fetchImpl,
      vault: "https://prints.example.com",
      username: "owner",
      apiKey: "psk_secret",
      pageUrl,
      title: "Captured model",
    });

    assert.equal(result.source, source);
    assert.equal(calls[1].url, "https://prints.example.com/api/v1/inbox");
    assert.deepEqual(JSON.parse(calls[1].options.body), {
      url: pageUrl,
      title: "Captured model",
      source_kind: "browser",
    });
  }
});

test("reuses an already verified access token for capture", async () => {
  const calls = [];
  const result = await captureModelPage({
    fetchImpl: async (url, options) => {
      calls.push({ url, options });
      return Response.json({ id: 12, state: "captured" }, { status: 202 });
    },
    vault: "https://prints.example.com",
    username: "owner",
    apiKey: "psk_secret",
    accessToken: "verified-jwt",
    pageUrl: "https://www.thingiverse.com/thing:763622/files",
    title: "Whistle",
  });

  assert.equal(result.item.id, 12);
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, "https://prints.example.com/api/v1/inbox");
  assert.equal(calls[0].options.headers.Authorization, "Bearer verified-jwt");
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
    /supported model page or direct model file/,
  );
  assert.equal(called, false);
});
