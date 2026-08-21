# PrintStash Model Importer

This Manifest V3 Chrome extension recognizes MakerWorld, Printables, and
Thingiverse model pages, Printables collections, and direct model/archive file
URLs, then sends them to the Pending Imports inbox of a self-hosted PrintStash
instance.

## Install

1. In PrintStash, create a named API key in **Settings → API keys**.
2. Open `chrome://extensions`, enable **Developer mode**, choose **Load
   unpacked**, and select this `browser-extension/` directory.
3. Open a supported source: an individual MakerWorld model (while signed in),
   a Printables model or collection, a Thingiverse model, or a direct model or
   archive file URL. Click the extension, enter the PrintStash URL, username,
   and named API key, then choose **Connect**.
4. Confirm that the popup shows **Connected** with the expected vault hostname
   and username, then choose **Send to Pending Imports**.
5. Review the detected files in **Pending Imports** before completing the
   import.

The extension verifies the public PrintStash health endpoint before sending
credentials, exchanges the named API key for a short-lived token, and confirms
the authenticated profile. Invalid settings are not saved. On later opens it
rechecks the saved connection and enables importing only when both the vault
connection and current source page are valid. **Manage → Disconnect** removes
the stored API key and the vault host permission from the browser.

For MakerWorld, the extension asks the already-authenticated page for the
selected model's signed package URL, downloads that package in the browser,
and uploads the bytes to PrintStash. MakerWorld cookies and credentials never
leave the browser and are not stored by PrintStash. MakerWorld collections are
not supported; capture their model pages individually. Printables, Thingiverse,
and direct file captures send only the active URL and title for server-side
resolution with PrintStash's SSRF protections. Direct URLs may point to `.zip`,
`.3mf`, `.stl`, `.obj`, `.step`, `.stp`, `.gcode`, `.g`, `.gco`, or `.bgcode`
files.

The helper does not persist access tokens. Vault URL, username, and named API
key remain in local extension storage and are not synced through the browser
account. The popup links directly to **Settings → Access** on the configured
vault when a new named API key is needed.

Run the extension's pure unit tests with:

```bash
node --test browser-extension/tests/*.test.mjs
```
