/** Cache controls preserve active readers and separate policy changes from clearing. */
import "@testing-library/jest-dom/vitest";
import { act, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { ArtifactCacheCard } from "@/components/artifact-cache-card";
import type { ArtifactCacheRead } from "@/lib/api/artifact-cache";
import { renderApp } from "@/test-support/render";
import { anArtifactCache } from "@/test-support/factories";

const INITIAL = anArtifactCache();

describe("ArtifactCacheCard", () => {
  it("saves enabled policy without clearing files", async () => {
    let persisted = INITIAL;
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => INITIAL,
          save: async (policy) => {
            persisted = { ...INITIAL, policy };
            return persisted;
          },
          reset: async () => INITIAL,
          clear: async () => {
            throw new Error("unexpected clear");
          },
        }}
      />,
    );
    await userEvent.click(
      await screen.findByRole("checkbox", { name: "Enable remote Artifact cache" }),
    );
    await userEvent.click(screen.getByRole("button", { name: "Save cache settings" }));
    expect(persisted.policy.enabled).toBe(true);
    expect(await screen.findByText(/100 B cached/)).toBeVisible();
  });

  it("shows remaining leased bytes after clear", async () => {
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => INITIAL,
          save: async () => INITIAL,
          reset: async () => INITIAL,
          clear: async () => ({ ...INITIAL, usage: { bytes: 25, entries: 1, leases: 1 } }),
        }}
      />,
    );
    await userEvent.click(await screen.findByRole("button", { name: "Clear cached files" }));
    expect(await screen.findByText(/25 B cached/)).toHaveTextContent("1 active reads");
  });

  it("shows restart requirement after changing root", async () => {
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => ({ ...INITIAL, restart_required: true }),
          save: async () => INITIAL,
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );
    expect(
      await screen.findByText("Restart PrintStash to use the new cache folder."),
    ).toBeInTheDocument();
  });

  it("allows retry after loading fails", async () => {
    let failed = true;
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => {
            if (failed) throw new Error("unavailable");
            return INITIAL;
          },
          save: async () => INITIAL,
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );
    const retry = await screen.findByRole("button", { name: "Retry" });
    failed = false;
    await userEvent.click(retry);
    expect(
      await screen.findByRole("checkbox", { name: "Enable remote Artifact cache" }),
    ).toBeInTheDocument();
  });

  it("reports a malformed cache response without breaking Settings", async () => {
    renderApp(
      <ArtifactCacheCard
        api={{
          // SAFETY: This test deliberately violates the wire contract to verify containment.
          read: async () => ({}) as ArtifactCacheRead,
          save: async () => INITIAL,
          reset: async () => INITIAL,
          clear: async () => INITIAL,
        }}
      />,
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Cache settings could not be loaded.",
    );
  });
});

describe("ArtifactCacheCard observability", () => {
  it("shows cache effectiveness with policy source", async () => {
    const observed = anArtifactCache({
      source: "database",
      usage: {
        bytes: 100,
        entries: 1,
        hit_ratio_percent: 75,
        bytes_saved: 300,
        completed_fills: 1,
        publication_failures: 5,
        corruptions: 2,
        bypasses: 3,
        evictions: 4,
        last_verification: Date.parse("2026-01-01T00:00:00Z") / 1000,
      },
    });
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => observed,
          save: async () => observed,
          reset: async () => observed,
          clear: async () => observed,
        }}
      />,
    );
    await userEvent.click(await screen.findByText("Advanced cache settings"));
    expect(screen.getByText(/Policy source: Saved settings/)).toBeVisible();
    await userEvent.click(screen.getByText("Cache activity and diagnostics"));
    expect(screen.getByText("75%")).toBeInTheDocument();
    expect(screen.getByText("300")).toBeInTheDocument();
    expect(screen.getByText("Publication failures")).toBeInTheDocument();
    expect(screen.getByText("5")).toBeInTheDocument();
    expect(screen.getByText(/Last verification:/)).not.toHaveTextContent("No cached files");
  });

  it("shows safe reclamation progress", async () => {
    const observed = anArtifactCache({ usage: { pending_eviction_bytes: 100, leases: 1 } });
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => observed,
          save: async () => observed,
          reset: async () => observed,
          clear: async () => observed,
        }}
      />,
    );
    expect(await screen.findByText(/100 bytes wait for active reads/)).toBeInTheDocument();
  });

  it("clears a transient polling failure after polling recovers", async () => {
    vi.useFakeTimers();
    let reads = 0;
    const observed = anArtifactCache({ usage: { pending_eviction_bytes: 100 } });
    try {
      renderApp(
        <ArtifactCacheCard
          api={{
            read: async () => {
              reads += 1;
              if (reads === 2) throw new Error("temporary poll failure");
              return observed;
            },
            save: async () => observed,
            reset: async () => observed,
            clear: async () => observed,
          }}
        />,
      );
      await act(async () => Promise.resolve());
      await act(async () => vi.advanceTimersByTimeAsync(1000));
      expect(screen.getByRole("alert")).toHaveTextContent("could not be loaded");
      await act(async () => vi.advanceTimersByTimeAsync(1000));
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("distinguishes cache degradation from original storage", async () => {
    const observed = anArtifactCache({ health: "corrupt_index" });
    renderApp(
      <ArtifactCacheCard
        api={{
          read: async () => observed,
          save: async () => observed,
          reset: async () => observed,
          clear: async () => observed,
        }}
      />,
    );
    expect(await screen.findByText(/Cache needs attention/)).toHaveTextContent(
      "Original Vault storage remains authoritative",
    );
  });
});
