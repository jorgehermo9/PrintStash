import path from "node:path";
import { fileURLToPath } from "node:url";

import { defineConfig } from "@playwright/test";

import real from "./playwright.real.config";

// PR visual evidence: the real-backend stack from playwright.real.config.ts,
// driving throwaway `*.evidence.ts` specs in tests/pr-evidence/ (gitignored).
// Guide: .agents/skills/printstash/references/pr-evidence.md.

const frontendDir = path.dirname(fileURLToPath(import.meta.url));
const repoDir = path.resolve(frontendDir, "..");

/**
 * Where screenshots and videos land: PR_EVIDENCE_OUT (a scratchpad outside the
 * repository), or the gitignored test-results/pr-evidence/ without it. Evidence
 * media is attached to the PR, never committed, so a directory inside the
 * repository is refused rather than trusted to be ignored.
 */
export function evidenceOutputDir(requested: string | undefined): string {
  if (requested === undefined) return path.join(frontendDir, "test-results", "pr-evidence");
  const out = path.resolve(requested);
  const fromRepo = path.relative(repoDir, out);
  if (!fromRepo.startsWith("..") && !path.isAbsolute(fromRepo)) {
    throw new Error(`PR_EVIDENCE_OUT must be outside the repository, got ${out}`);
  }
  return out;
}

export default defineConfig({
  ...real,
  testDir: "./tests/pr-evidence",
  testMatch: "**/*.evidence.ts",
  testIgnore: [],
  outputDir: evidenceOutputDir(process.env.PR_EVIDENCE_OUT),
  retries: 0,
  reporter: "list",
  use: { ...real.use, trace: "off" },
});
