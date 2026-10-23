/** Operators can correct rejected backup files using localized recovery guidance. */
import { expect, test } from "@playwright/test";
import { localeDefinitions } from "../../src/locales/catalogs";
import { useMockApi } from "./_setup";

useMockApi();

test.describe("Backup upload recovery", () => {
  for (const [locale, definition] of Object.entries(localeDefinitions)) {
    test(`recovers from rejected archives in ${locale}`, async ({ page }) => {
      await page.setViewportSize({ width: 1440, height: 1000 });
      await page.addInitScript((locale) => {
        localStorage.setItem("printstash.locale", locale);
      }, locale);
      const errors: string[] = [];
      page.on("pageerror", (error) => errors.push(error.message));
      const failures = [
        ["backup_filename_invalid", "settings.backupFilenameInvalid"],
        ["backup_archive_invalid", "settings.backupArchiveInvalid"],
        ["backup_already_exists", "settings.backupAlreadyExists"],
      ] as const;
      const backup = {
        backup_id: "recovered-backup",
        created_at: "2026-01-01T00:00:00Z",
        location: "local",
        app_version: "0.15.0",
        file_count: 42,
        size_bytes: 1048576,
        storage_backend: "local",
        namespace: "vault-backups",
        source_ref: "local-source-ref",
        provider_ref: "local",
        key: "printstash-backups/recovered-backup.tar.gz",
        prefix: "printstash-backups/",
        archive_sha256: "a".repeat(64),
      };
      let uploads = 0;
      let saved = false;
      await page.route("**/api/v1/backups/sources", (route) =>
        route.fulfill({ json: saved ? [backup] : [] }),
      );
      await page.route("**/api/v1/backups/upload", async (route) => {
        const failure = failures[uploads++];
        if (failure) {
          await route.fulfill({ status: 409, json: { detail: failure[0] } });
          return;
        }
        saved = true;
        await route.fulfill({ status: 201, json: backup });
      });
      await page.goto("/settings?section=backup");
      const upload = page.getByLabel(definition.messages["settings.backupUploadTitle"], {
        exact: true,
      });
      await expect(upload).toBeEnabled();
      await expect(
        page.getByText(definition.messages["settings.backupUploadDescription"], { exact: true }),
      ).toBeVisible();
      for (const [code, key] of failures) {
        await upload.setInputFiles({
          name: `printstash-backup-${code}.tar.gz`,
          mimeType: "application/gzip",
          buffer: Buffer.from(code),
        });
        await expect(page.getByText(definition.messages[key], { exact: true })).toBeVisible();
        await expect(upload).toBeEnabled();
      }
      await upload.setInputFiles({
        name: "printstash-backup-recovered-backup.tar.gz",
        mimeType: "application/gzip",
        buffer: Buffer.from("corrected archive"),
      });
      await expect(page.getByText(backup.backup_id, { exact: true })).toBeVisible();
      expect(uploads).toBe(4);
      expect(errors).toEqual([]);
    });
  }
});
