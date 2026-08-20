# PrintStash Model Importer

This Manifest V3 Chrome extension recognizes MakerWorld model pages and
Printables model or collection pages, then sends them to the Pending Imports
inbox of a self-hosted PrintStash instance.

## Install

1. In PrintStash, create a named API key in **Settings → API keys**.
2. Open `chrome://extensions`, enable **Developer mode**, choose **Load
   unpacked**, and select this `browser-extension/` directory.
3. For MakerWorld, sign in on `makerworld.com` and open an individual model.
   For Printables, open a model or collection. Click the extension, enter the
   PrintStash URL, username, and named API key, then choose **Import model**.
4. Review the detected files in **Pending Imports** before completing the
   import.

For MakerWorld, the extension asks the already-authenticated page for the
selected model's signed package URL, downloads that package in the browser,
and uploads the bytes to PrintStash. MakerWorld cookies and credentials never
leave the browser and are not stored by PrintStash. MakerWorld collections are
not supported; capture their model pages individually. Printables continues to
send only the page URL and title for server-side resolution with PrintStash's
SSRF protections.

The helper exchanges the username and named API key for a short-lived access
token for each capture; it does not retain that token. Vault URL, username, and
named API key remain in local extension storage and are not synced through the
browser account.

Run the extension's pure unit tests with:

```bash
node --test browser-extension/tests/*.test.mjs
```
