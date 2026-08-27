/*
 * The shape rules for this repo's own tests, checked by reading them.
 *
 * These are the frontend half of `backend/tests/repo/test_test_hygiene.py`, and
 * they exist for the same reason: a test suite decays in ways that never fail.
 * A file with no header is readable today and unmaintainable in a year, when it
 * goes red and the obvious fix is to delete the assertion nobody can justify. A
 * test outside a `describe` belongs to nothing, so it accumulates in write order
 * and drifts away from the component it defends. Neither is a bug, so nothing
 * else would ever report them.
 *
 * Both rules are absolute rather than ratcheted: the whole suite was converted,
 * so the first violation to appear is the regression, and it appears in the PR
 * that wrote it.
 *
 * The conjunction rule is capped rather than absolute, because a mechanical split
 * of a name that legitimately needs "and" produces duplicated setup in two tests
 * that assert halves of one behaviour. The cap may only fall.
 */
import fs from "node:fs";
import path from "node:path";
import { describe, expect, it } from "vitest";

const FRONTEND_ROOT = path.resolve(__dirname, "../../..");

/**
 * Test names containing " and ". Some hold two behaviours, which a failure cannot
 * tell apart; others describe one invariant that needs the word. Both are worth
 * reducing and neither is worth a mechanical split, so the count is capped.
 */
const MAX_CONJUNCTION_NAMES = 133;

function testFiles(root: string): string[] {
  return fs.readdirSync(root, { withFileTypes: true }).flatMap((entry) => {
    if (entry.name === "node_modules" || entry.name.startsWith(".")) return [];
    const full = path.join(root, entry.name);
    if (entry.isDirectory()) return testFiles(full);
    return /\.(test|spec)\.tsx?$/.test(entry.name) ? [full] : [];
  });
}

function suiteFiles(): { file: string; relative: string; source: string }[] {
  return [
    ...testFiles(path.join(FRONTEND_ROOT, "src")),
    ...testFiles(path.join(FRONTEND_ROOT, "tests")),
  ].map((file) => ({
    file,
    relative: path.relative(FRONTEND_ROOT, file),
    source: fs.readFileSync(file, "utf8"),
  }));
}

describe("suiteHygiene", () => {
  it("opens every test file with a contract header", () => {
    const offenders = suiteFiles()
      .filter(({ source }) => !/^\s*(\/\*|\/\/)/.test(source.split("\n")[0] ?? ""))
      .map(({ relative }) => relative);

    expect(offenders, "start each file with a block comment saying what it defends").toEqual([]);
  });

  it("defines every test inside a describe", () => {
    const offenders = suiteFiles().flatMap(({ relative, source }) => {
      const top = source
        .split("\n")
        .filter((line) =>
          /^(?:it|test)(?:\.(?:only|skip|fixme|failing|concurrent|sequential))?\(/.test(line),
        ).length;
      return top ? [`${relative} (${top})`] : [];
    });

    expect(offenders, "wrap each test in a describe naming the unit it exercises").toEqual([]);
  });

  it("keeps the count of test names joining two behaviours falling", () => {
    const offenders = suiteFiles().flatMap(({ relative, source }) =>
      [...source.matchAll(/\b(?:it|test)\(\s*["'`]([^"'`]*\sand\s[^"'`]*)["'`]/g)].map(
        (match) => `${relative}::${match[1]}`,
      ),
    );

    expect(offenders.length).toBeLessThanOrEqual(MAX_CONJUNCTION_NAMES);
    // Lower the cap whenever it falls, or it stops meaning anything.
    expect(offenders.length).toBe(MAX_CONJUNCTION_NAMES);
  });
});
