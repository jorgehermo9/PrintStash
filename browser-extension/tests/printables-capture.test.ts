import { afterEach, describe, expect, it, vi } from "vitest";

import {
  PRINTABLES_LINK_MUTATION,
  PRINTABLES_METADATA_FIXTURE_VERSION,
  PRINTABLES_METADATA_QUERY,
  parsePrintablesMetadataResponse,
  readBoundedPrintablesResponse,
  requestPrintablesLinksInExtensionContext,
  requestPrintablesMetadataInExtensionContext,
  requestPrintablesLinksInMainWorld,
  requestPrintablesMetadataInMainWorld,
  selectedGroups,
  validatePrintablesResolvedLinks,
  validatePrintablesMetadataDto,
  type PrintablesSelectedFile,
} from "../printables-capture.ts";

const sourceItemId = "3161";

function metadataPayload() {
  return {
    data: {
      print: {
        id: sourceItemId,
        name: "3DBenchy",
        description: "A bounded description",
        user: {
          id: "ada-7",
          name: "Ada Maker",
          url: "https://www.printables.com/@ada?tracking=discarded",
        },
        license: {
          code: "CC-BY-4.0",
          url: "https://creativecommons.org/licenses/by/4.0/?tracking=discarded",
        },
        stls: [{ id: "stl-1", name: "benchy.stl", fileSize: 42 }],
        gcodes: [{ id: "gcode-1", name: "benchy.gcode", fileSize: "43" }],
        slas: [{ id: "sla-1", name: "benchy.sla" }],
        otherFiles: [
          { id: "mesh-3mf", name: "船体.3mf", fileSize: 44 },
          { id: "notes", name: "README.txt", fileSize: null },
        ],
      },
    },
  };
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const selected: PrintablesSelectedFile[] = [
  { id: "stl-1", filename: "benchy.stl", fileType: "stl", sizeBytes: 42 },
  { id: "mesh-3mf", filename: "船体.3mf", fileType: "other", sizeBytes: 44 },
  { id: "gcode-1", filename: "benchy.gcode", fileType: "gcode", sizeBytes: 43 },
];

afterEach(() => {
  vi.unstubAllGlobals();
  document.title = "";
});

describe("Printables metadata contract", () => {
  it("maps a streamed metadata body over the supplied cap to response_too_large", async () => {
    const fetchImpl = vi.fn(
      async () =>
        new Response(
          new ReadableStream<Uint8Array>({
            start(controller) {
              controller.enqueue(new TextEncoder().encode('{"data":'));
              controller.enqueue(new Uint8Array(64));
              controller.close();
            },
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        ),
    );
    await expect(
      requestPrintablesMetadataInExtensionContext({
        fetchImpl,
        endpoint: "https://api.printables.com/graphql/",
        query: PRINTABLES_METADATA_QUERY,
        sourceItemId,
        fixtureVersion: PRINTABLES_METADATA_FIXTURE_VERSION,
        maxResponseBytes: 16,
      }),
    ).resolves.toMatchObject({ ok: false, code: "response_too_large" });
  });

  it("normalizes every provider file bucket, Unicode names, and source allowlist", () => {
    const result = parsePrintablesMetadataResponse(metadataPayload(), sourceItemId);
    expect(result).toEqual({
      fixtureVersion: PRINTABLES_METADATA_FIXTURE_VERSION,
      sourceItemId,
      source: {
        title: "3DBenchy",
        description: "A bounded description",
        creatorName: "Ada Maker",
        creatorId: "ada-7",
        creatorUrl: "https://www.printables.com/@ada",
        licenseCode: "CC-BY-4.0",
        licenseUrl: "https://creativecommons.org/licenses/by/4.0/",
      },
      files: [
        { id: "stl-1", filename: "benchy.stl", fileType: "stl", sizeBytes: 42 },
        { id: "gcode-1", filename: "benchy.gcode", fileType: "gcode", sizeBytes: 43 },
        { id: "sla-1", filename: "benchy.sla", fileType: "sla" },
        { id: "mesh-3mf", filename: "船体.3mf", fileType: "other", sizeBytes: 44 },
        { id: "notes", filename: "README.txt", fileType: "other" },
      ],
    });
    expect(JSON.stringify(result)).not.toContain("tracking=discarded");
    expect(JSON.stringify(result)).not.toMatch(/signed|download|link/i);
  });

  it("keeps the metadata operation aligned with the verified Printables schema", () => {
    expect(PRINTABLES_METADATA_QUERY.replace(/\s+/g, " ").trim()).toBe(
      "query ($id: ID!) { print(id: $id) { id name license { name } stls { id name fileSize } gcodes { id name fileSize } slas { id name fileSize } otherFiles { id name fileSize } } }",
    );
  });

  it("sends the verified ModelFiles GraphQL envelope", async () => {
    const fetchImpl = vi.fn(async (_input: URL | RequestInfo, _init?: RequestInit) =>
      response(metadataPayload()),
    );
    await expect(
      requestPrintablesMetadataInExtensionContext({
        fetchImpl,
        endpoint: "https://api.printables.com/graphql/",
        query: PRINTABLES_METADATA_QUERY,
        sourceItemId,
        fixtureVersion: PRINTABLES_METADATA_FIXTURE_VERSION,
        maxResponseBytes: 512 * 1024,
      }),
    ).resolves.toMatchObject({ ok: true });
    const request = JSON.parse(String(fetchImpl.mock.calls[0]?.[1]?.body));
    expect(request).toEqual({
      query: PRINTABLES_METADATA_QUERY,
      variables: { id: sourceItemId },
    });
  });

  it("sends the exact confirmation mutation variables without auth material", async () => {
    const fetchImpl = vi.fn(async (_input: URL | RequestInfo, _init?: RequestInit) =>
      response({
        data: {
          getDownloadLink: {
            ok: true,
            output: {
              files: selected.map((file) => ({
                id: file.id,
                link: `https://media.printables.com/${file.id}?signature=secret`,
              })),
            },
          },
        },
      }),
    );
    await expect(
      requestPrintablesLinksInExtensionContext({
        fetchImpl,
        endpoint: "https://api.printables.com/graphql/",
        query: PRINTABLES_LINK_MUTATION,
        sourceItemId,
        selected,
        maxResponseBytes: 512 * 1024,
      }),
    ).resolves.toMatchObject({ ok: true });
    const request = JSON.parse(String(fetchImpl.mock.calls[0]?.[1]?.body));
    expect(request).toEqual({
      query: PRINTABLES_LINK_MUTATION,
      variables: {
        printId: sourceItemId,
        source: "model_detail",
        files: selectedGroups(selected),
      },
    });
    expect(JSON.stringify(fetchImpl.mock.calls[0]?.[1])).not.toContain("secret");
  });

  it("maps the verified license name while leaving creator metadata to page JSON-LD", () => {
    const result = parsePrintablesMetadataResponse(
      {
        data: {
          print: {
            id: sourceItemId,
            name: "Live Printables model",
            license: { name: "CC BY 4.0" },
            stls: [{ id: "live-stl", name: "live.stl", fileSize: 7 }],
          },
        },
      },
      sourceItemId,
    );

    expect(result.source).toEqual({
      title: "Live Printables model",
      licenseCode: "CC BY 4.0",
    });
    expect(result.source.creatorName).toBeUndefined();
  });

  it("fails closed for changed, oversized, deep, duplicate, and invalid-size responses", () => {
    expect(() => parsePrintablesMetadataResponse({ data: {} }, sourceItemId)).toThrow();
    expect(() =>
      parsePrintablesMetadataResponse(
        {
          data: {
            print: {
              id: sourceItemId,
              stls: Array.from({ length: 257 }, (_, index) => ({
                id: String(index),
                name: `${index}.stl`,
              })),
            },
          },
        },
        sourceItemId,
      ),
    ).toThrow();
    const deep: Record<string, unknown> = {};
    let cursor = deep;
    for (let index = 0; index < 20; index += 1) {
      cursor.child = {};
      cursor = cursor.child as Record<string, unknown>;
    }
    expect(() => parsePrintablesMetadataResponse(deep, sourceItemId)).toThrow();
    expect(() =>
      parsePrintablesMetadataResponse(
        {
          data: {
            print: {
              id: sourceItemId,
              stls: [{ id: "same", name: "a.stl" }],
              gcodes: [{ id: "same", name: "b.gcode" }],
            },
          },
        },
        sourceItemId,
      ),
    ).toThrow();
    expect(() =>
      parsePrintablesMetadataResponse(
        {
          data: {
            print: { id: sourceItemId, stls: [{ id: "a", name: "a.stl", fileSize: 2 ** 40 }] },
          },
        },
        sourceItemId,
      ),
    ).toThrow();
  });

  it("groups confirmation IDs by provider file type in first-selection order", () => {
    expect(selectedGroups(selected)).toEqual([
      { fileType: "stl", ids: ["stl-1"] },
      { fileType: "other", ids: ["mesh-3mf"] },
      { fileType: "gcode", ids: ["gcode-1"] },
    ]);
  });

  it("rejects unsafe, missing, extra, duplicate, and malformed signed-link mappings", () => {
    const links = selected.map((file) => ({
      id: file.id,
      link: `https://media.printables.com/${file.id}`,
    }));
    expect(validatePrintablesResolvedLinks(selected, links).map((item) => item.id)).toEqual(
      selected.map((file) => file.id),
    );
    expect(() => validatePrintablesResolvedLinks(selected, links.slice(1))).toThrow();
    expect(() =>
      validatePrintablesResolvedLinks(selected, [...links, { id: "extra", link: links[0].link }]),
    ).toThrow();
    expect(() =>
      validatePrintablesResolvedLinks(selected, [links[0], links[0], links[2]]),
    ).toThrow();
    expect(() =>
      validatePrintablesResolvedLinks(
        selected,
        selected.map((file) => ({ id: file.id, link: "https://evil.example/file" })),
      ),
    ).toThrow();
  });

  it("validates the typed DTO again after the MAIN-world boundary", () => {
    const dto = {
      fixtureVersion: PRINTABLES_METADATA_FIXTURE_VERSION,
      sourceItemId,
      source: { title: "3DBenchy" },
      files: [{ id: "stl-1", filename: "benchy.stl", fileType: "stl", sizeBytes: 42 }],
    };
    expect(validatePrintablesMetadataDto(dto, sourceItemId)).toEqual({
      ...dto,
      source: { title: "3DBenchy" },
    });
  });
});

describe("Printables MAIN-world request seams", () => {
  it("returns only typed metadata and checks the active tab challenge before fetch", async () => {
    document.title = "Verify you are human";
    const fetchImpl = vi.fn();
    vi.stubGlobal("fetch", fetchImpl);
    await expect(
      requestPrintablesMetadataInMainWorld({
        endpoint: "https://api.printables.com/graphql/",
        query: PRINTABLES_METADATA_QUERY,
        sourceItemId,
        fixtureVersion: PRINTABLES_METADATA_FIXTURE_VERSION,
        maxResponseBytes: 512 * 1024,
      }),
    ).resolves.toMatchObject({ ok: false, code: "challenge" });
    expect(fetchImpl).not.toHaveBeenCalled();
  });

  it("is executable without module closures and never returns raw metadata payload", async () => {
    document.title = "3DBenchy";
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => response(metadataPayload())),
    );
    const isolated = new Function(
      `return (${requestPrintablesMetadataInMainWorld.toString()})`,
    )() as typeof requestPrintablesMetadataInMainWorld;
    const result = await isolated({
      endpoint: "https://api.printables.com/graphql/",
      query: PRINTABLES_METADATA_QUERY,
      sourceItemId,
      fixtureVersion: PRINTABLES_METADATA_FIXTURE_VERSION,
      maxResponseBytes: 512 * 1024,
    });
    expect(result.ok).toBe(true);
    expect(JSON.stringify(result)).not.toContain('"print"');
    expect(JSON.stringify(result)).not.toMatch(/signed|download|tracking=discarded/i);
  });

  it("resolves grouped links only through the confirmation mutation seam", async () => {
    document.title = "3DBenchy";
    const fetchImpl = vi.fn(async (_input: RequestInfo, _init?: RequestInit) =>
      response({
        data: {
          getDownloadLink: {
            ok: true,
            output: {
              files: [
                { fileId: "stl-1", link: "https://media.printables.com/stl-1?signature=secret" },
                { id: "mesh-3mf", link: "https://media.printables.com/mesh-3mf?signature=secret" },
                { id: "gcode-1", link: "https://media.printables.com/gcode-1?signature=secret" },
              ],
            },
          },
        },
      }),
    );
    vi.stubGlobal("fetch", fetchImpl);
    const result = await requestPrintablesLinksInMainWorld({
      endpoint: "https://api.printables.com/graphql/",
      query: PRINTABLES_LINK_MUTATION,
      sourceItemId,
      groups: selectedGroups(selected),
      maxResponseBytes: 512 * 1024,
    });
    expect(result).toMatchObject({
      ok: true,
      links: [{ id: "stl-1" }, { id: "mesh-3mf" }, { id: "gcode-1" }],
    });
    const request = JSON.parse(String(fetchImpl.mock.calls[0]?.[1]?.body));
    expect(request.variables.files).toEqual(selectedGroups(selected));
    expect(JSON.stringify(request)).not.toContain("signature");
  });

  it("executes the signed-link seam without module closures", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () =>
        response({
          data: {
            getDownloadLink: {
              output: {
                files: [
                  { id: "stl-1", link: "https://media.printables.com/stl-1?signature=secret" },
                ],
              },
            },
          },
        }),
      ),
    );
    const isolated = new Function(
      `return (${requestPrintablesLinksInMainWorld.toString()})`,
    )() as typeof requestPrintablesLinksInMainWorld;
    const result = await isolated({
      endpoint: "https://api.printables.com/graphql/",
      query: PRINTABLES_LINK_MUTATION,
      sourceItemId,
      groups: [{ fileType: "stl", ids: ["stl-1"] }],
      maxResponseBytes: 512 * 1024,
    });
    expect(result).toMatchObject({ ok: true, links: [{ id: "stl-1" }] });
  });

  it("caps streamed downloads with or without Content-Length", async () => {
    const expected = new Response(new Uint8Array([1, 2, 3]), {
      headers: { "Content-Length": "4" },
    });
    await expect(readBoundedPrintablesResponse(expected, 4)).rejects.toThrow(/file changed/);
    let chunks = 0;
    const oversized = new Response(
      new ReadableStream({
        pull(controller) {
          if (chunks > 512) {
            controller.close();
            return;
          }
          chunks += 1;
          controller.enqueue(new Uint8Array(1024 * 1024));
        },
      }),
    );
    await expect(readBoundedPrintablesResponse(oversized)).rejects.toThrow(/too large/);
  });
});
