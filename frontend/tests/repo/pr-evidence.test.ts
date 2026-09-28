/*
 * PR evidence media never enters the repository.
 *
 * Screenshots and videos recorded for a PR (`pnpm evidence`, guide in
 * `.agents/skills/printstash/references/pr-evidence.md`) are attached to the
 * PR description and nowhere else. A committed video bloats every clone
 * forever, and history rewriting is the only way to take it back out. So the
 * throwaway specs and the default output directory are gitignored, an output
 * directory inside the repository is refused outright, and no video file is
 * tracked at all: README media is GIF, so a tracked video can only be evidence
 * that slipped through.
 */
import { execFileSync } from "node:child_process";
import os from "node:os";
import path from "node:path";
import { describe, expect, it } from "vitest";

import { evidenceOutputDir } from "../../playwright.evidence.config";

const FRONTEND_ROOT = path.resolve(__dirname, "../..");
const REPO_ROOT = path.resolve(FRONTEND_ROOT, "..");

function isIgnored(relative: string): boolean {
  try {
    execFileSync("git", ["check-ignore", "-q", relative], { cwd: REPO_ROOT });
    return true;
  } catch {
    return false;
  }
}

describe("prEvidence", () => {
  it("refuses an output directory inside the repository", () => {
    expect(() => evidenceOutputDir(path.join(REPO_ROOT, "docs", "evidence"))).toThrow(
      /outside the repository/,
    );
  });

  it("writes to a directory outside the repository when one is given", () => {
    const scratchpad = path.join(os.tmpdir(), "printstash-evidence");
    expect(evidenceOutputDir(scratchpad)).toBe(scratchpad);
  });

  it("defaults to a gitignored directory", () => {
    const relative = path.relative(REPO_ROOT, evidenceOutputDir(undefined));
    expect(isIgnored(path.join(relative, "flow.webm"))).toBe(true);
  });

  it("keeps the throwaway evidence specs gitignored", () => {
    expect(isIgnored("frontend/tests/pr-evidence/flow.evidence.ts")).toBe(true);
  });

  it("tracks no video file anywhere in the repository", () => {
    const tracked = execFileSync("git", ["ls-files", "*.webm", "*.mp4", "*.mov"], {
      cwd: REPO_ROOT,
      encoding: "utf8",
    });
    expect(tracked.split("\n").filter(Boolean)).toEqual([]);
  });
});
