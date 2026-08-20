"""Resolve model *page* URLs to direct download URLs.

Users paste the page they are looking at — e.g.
``https://www.printables.com/model/3161-3d-benchy/files`` — rather than a
direct download link. Each host keeps the real file behind an API call keyed by
the model id embedded in the page URL. The resolvers here turn a recognised
page URL into a direct download URL that :func:`importer.download_to_staging`
can fetch; that function re-runs the SSRF guard on every hop, including the
resolved one, so resolution never bypasses the public-IP check.

Contract of :func:`resolve_page_url`:

* **Unrecognised host** (or a known host whose URL carries no model id) →
  ``None``. The caller treats the original URL as an already-direct download.
* **Recognised page that resolves** → a direct download URL string.
* **Recognised page that fails to resolve** → ``ImportError_`` with a
  host-specific code (e.g. ``printables_resolve_failed``) so the UI can tell the
  user to paste a direct link instead of silently downloading the HTML page.

The host APIs dictate the request/response shapes (that is their public
contract); everything else here — dispatch, pack selection, JSON walking,
graceful degradation — is ours.
"""

from __future__ import annotations

import json
from typing import Any, Optional

from printstash_core.imports import resolvers as _resolver_rules

from app.core.http_client import get_http_client
from app.core.logging import get_logger
from app.services import browser_fetch
from app.services.importer import ImportError_

logger = get_logger(__name__)

# A browser-like UA: model hosts gate their APIs/HTML behind one.
_BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
_TIMEOUT = 30.0

_PRINTABLES_GRAPHQL = "https://api.printables.com/graphql/"

# Compatibility aliases preserve the OSS resolver module's existing API and
# patch points while delegating deterministic rules to the shared core.
ModelFile = _resolver_rules.ModelFile
CollectionMember = _resolver_rules.CollectionMember
_MODEL_EXTS = _resolver_rules.MODEL_EXTENSIONS
_PRINTABLES_HOSTS = _resolver_rules.PRINTABLES_HOSTS
_THINGIVERSE_HOSTS = _resolver_rules.THINGIVERSE_HOSTS
_CHALLENGE_MARKERS = _resolver_rules.CHALLENGE_MARKERS
_PRINTABLES_FILE_CATEGORIES = _resolver_rules.PRINTABLES_FILE_CATEGORIES
_host = _resolver_rules.host
_printables_id = _resolver_rules.printables_id
_makerworld_id = _resolver_rules.makerworld_id
_thingiverse_id = _resolver_rules.thingiverse_id
_collection_id = _resolver_rules.collection_id
classify_collection = _resolver_rules.classify_collection
classify_page = _resolver_rules.classify_page
_looks_like_download = _resolver_rules.looks_like_download
_first_download_url = _resolver_rules.first_download_url
_looks_like_challenge = _resolver_rules.looks_like_challenge
_extract_next_data = _resolver_rules.extract_next_data
_pick_printables_pack = _resolver_rules.pick_printables_pack
_printables_link_from_output = _resolver_rules.printables_link_from_output
_printables_files_from_print = _resolver_rules.printables_files_from_print
_printables_links_from_output = _resolver_rules.printables_links_from_output
_makerworld_instance_id = _resolver_rules.makerworld_instance_id
_makerworld_collection_title = _resolver_rules.makerworld_collection_title
_makerworld_collection_members = _resolver_rules.makerworld_collection_members


# --------------------------------------------------------------------------- #
# Printables (GraphQL)
# --------------------------------------------------------------------------- #
_PRINTABLES_META_QUERY = """
query ($id: ID!) {
  print(id: $id) {
    id
    downloadPacks { id fileType }
    stls { id name }
  }
}
"""

_PRINTABLES_LINK_MUTATION = """
mutation ($printId: ID!, $source: DownloadSourceEnum!, $fileType: DownloadFileTypeEnum, $id: ID, $files: [DownloadFileInput!]) {
  getDownloadLink(printId: $printId, source: $source, fileType: $fileType, id: $id, files: $files) {
    ok
    output { link files { link } }
  }
}
"""


async def _printables_graphql(query: str, variables: dict, referer: str) -> Any:
    client = get_http_client()
    resp = await client.post(
        _PRINTABLES_GRAPHQL,
        json={"query": query, "variables": variables},
        headers={
            "User-Agent": _BROWSER_UA,
            "Accept": "application/json",
            "Origin": "https://www.printables.com",
            "Referer": referer,
        },
        timeout=_TIMEOUT,
    )
    if resp.status_code in (401, 403, 429):
        raise ImportError_("printables_blocked")
    resp.raise_for_status()
    return resp.json()


async def _resolve_printables(url: str) -> Optional[str]:
    print_id = _printables_id(url)
    if not print_id:
        return None
    meta = await _printables_graphql(_PRINTABLES_META_QUERY, {"id": print_id}, url)
    print_obj = (meta or {}).get("data", {}).get("print")
    if not isinstance(print_obj, dict):
        return None

    pack_id = _pick_printables_pack(print_obj.get("downloadPacks"))
    if pack_id:
        payload = await _printables_graphql(
            _PRINTABLES_LINK_MUTATION,
            {"printId": print_id, "source": "model_detail", "fileType": "pack", "id": pack_id},
            url,
        )
        link = _printables_link_from_output(payload)
        if link:
            return link

    stl_ids = [
        str(s["id"])
        for s in (print_obj.get("stls") or [])
        if isinstance(s, dict) and s.get("id")
    ]
    if stl_ids:
        payload = await _printables_graphql(
            _PRINTABLES_LINK_MUTATION,
            {
                "printId": print_id,
                "source": "model_detail",
                "files": [{"fileType": "stl", "ids": stl_ids}],
            },
            url,
        )
        link = _printables_link_from_output(payload)
        if link:
            return link
    return None


# Printables exposes downloadable files in per-type buckets on the `print` type;
# each bucket maps to a value of DownloadFileTypeEnum used by the link mutation.
_PRINTABLES_FILES_QUERY = """
query ($id: ID!) {
  print(id: $id) {
    id
    name
    stls { id name fileSize }
    gcodes { id name fileSize }
    slas { id name fileSize }
    otherFiles { id name fileSize }
  }
}
"""

async def _list_printables_files(url: str) -> Optional[tuple[str, list[ModelFile]]]:
    print_id = _printables_id(url)
    if not print_id:
        return None
    meta = await _printables_graphql(_PRINTABLES_FILES_QUERY, {"id": print_id}, url)
    print_obj = (meta or {}).get("data", {}).get("print")
    if not isinstance(print_obj, dict):
        return None
    title = str(print_obj.get("name") or print_id)
    return title, _printables_files_from_print(print_obj)


async def _printables_download_links(url: str, files: list[ModelFile]) -> list[str]:
    """Resolve direct download links for a chosen subset of a model's files."""
    print_id = _printables_id(url)
    if not print_id or not files:
        return []
    grouped: dict[str, list[str]] = {}
    for f in files:
        grouped.setdefault(f.file_type, []).append(f.file_id)
    files_arg = [{"fileType": file_type, "ids": ids} for file_type, ids in grouped.items()]
    payload = await _printables_graphql(
        _PRINTABLES_LINK_MUTATION,
        {"printId": print_id, "source": "model_detail", "files": files_arg},
        url,
    )
    return _printables_links_from_output(payload)


# Collection name + paginated member list. `moreCollectionModels` requires an
# explicit ordering (its server-side default errors), and returns items whose
# real print lives under `item.print`.
_PRINTABLES_COLLECTION_QUERY = """
query ($id: ID!) { collection(id: $id) { id name } }
"""

_PRINTABLES_COLLECTION_MODELS_QUERY = """
query ($collectionId: ID!, $limit: Int, $cursor: String, $ordering: CollectionPrintsOrderingEnum) {
  moreCollectionModels(collectionId: $collectionId, limit: $limit, cursor: $cursor, ordering: $ordering) {
    cursor
    items { id print { id name } }
  }
}
"""


async def _resolve_printables_collection(url: str) -> Optional[tuple[str, list[CollectionMember]]]:
    collection_id = _collection_id(url)
    if not collection_id:
        return None
    meta = await _printables_graphql(_PRINTABLES_COLLECTION_QUERY, {"id": collection_id}, url)
    collection = (meta or {}).get("data", {}).get("collection") or {}
    title = str(collection.get("name") or f"Collection {collection_id}")

    members: list[CollectionMember] = []
    seen: set[str] = set()
    cursor: Optional[str] = None
    for _ in range(50):  # safety cap: 50 pages * 50 = 2500 members
        data = await _printables_graphql(
            _PRINTABLES_COLLECTION_MODELS_QUERY,
            {
                "collectionId": collection_id,
                "limit": 50,
                "cursor": cursor,
                "ordering": "added_to_collection",
            },
            url,
        )
        block = (data or {}).get("data", {}).get("moreCollectionModels") or {}
        items = block.get("items") or []
        for item in items:
            print_obj = (item or {}).get("print") or {}
            print_id = print_obj.get("id") or (item or {}).get("id")
            if not print_id or str(print_id) in seen:
                continue
            seen.add(str(print_id))
            members.append(
                CollectionMember(
                    page_url=f"https://www.printables.com/model/{print_id}",
                    title=str(print_obj.get("name") or print_id),
                    source_id=str(print_id),
                )
            )
        cursor = block.get("cursor")
        if not cursor or not items:
            break
    return title, members


# --------------------------------------------------------------------------- #
# MakerWorld (Next.js page → instance/model download API)
# --------------------------------------------------------------------------- #
def _makerworld_api_headers(referer: str, nonce: Optional[str]) -> dict:
    # The login cookie is injected into the browser context by browser_fetch, not
    # set here, so the request carries it alongside Cloudflare clearance.
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "application/json",
        "X-BBL-Client-Type": "web",
        "X-BBL-Client-Name": "MakerWorld",
        "Referer": referer,
    }
    if nonce:
        headers["X-Nonce"] = nonce
    return headers


async def _makerworld_fetch_page(url: str, cookie: Optional[str]) -> Optional[str]:
    """Fetch a MakerWorld page's HTML, rendering past Cloudflare if needed.

    The cheap httpx fetch is tried first. If MakerWorld returns nothing usable —
    a non-200, or the Cloudflare "Verify you are human" interstitial — fall back
    to a headless browser that solves the challenge and returns rendered HTML.
    """
    client = get_http_client()
    headers = {
        "User-Agent": _BROWSER_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }
    if cookie:
        headers["Cookie"] = cookie
    resp = await client.get(url, headers=headers, follow_redirects=True, timeout=_TIMEOUT)
    html = resp.text if resp.status_code == 200 else None

    if html is not None and not _looks_like_challenge(html):
        return html

    # httpx was blocked or challenged — render the page in a fresh browser context
    # that solves the Cloudflare challenge.
    rendered = await browser_fetch.fetch_rendered_html(
        url, wait_selector="script#__NEXT_DATA__", extra_cookie=cookie
    )
    if rendered:
        return rendered
    return html


async def _makerworld_api_get(
    api_url: str, referer: str, nonce: Optional[str], cookie: Optional[str]
) -> Optional[Any]:
    """GET a MakerWorld API endpoint through the browser, past Cloudflare.

    Plain httpx is challenged (403) by Cloudflare, so the request rides the
    browser's request context. ``cookie`` carries the user's login session;
    download endpoints are auth-gated and answer 403 "please log in" without it.
    """
    headers = _makerworld_api_headers(referer, nonce)
    result = await browser_fetch.api_get(api_url, cookie=cookie, headers=headers)
    if result is None:
        return None
    status, text = result
    if status in (401, 403, 429):
        if "log in" in text.lower() or "login" in text.lower():
            raise ImportError_("makerworld_login_required")
        raise ImportError_("makerworld_blocked")
    if status != 200:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


async def _resolve_makerworld(url: str, cookie: Optional[str]) -> Optional[str]:
    """Resolve a MakerWorld model page to a direct download URL.

    The page HTML embeds no real file link (only thumbnails and store links), so
    we go straight to the download API: the public design endpoint yields the
    instance id, and the instance's ``f3mf`` endpoint yields the file link. The
    latter is auth-gated — without a login ``cookie`` it raises
    ``makerworld_login_required`` (surfaced via :func:`_makerworld_api_get`).
    """
    design_id = _makerworld_id(url)
    if not design_id:
        return None
    base = "https://makerworld.com/api/v1/design-service"

    design = await _makerworld_api_get(f"{base}/design/{design_id}", url, None, cookie)
    instance_id = _makerworld_instance_id(design)

    if instance_id:
        api = f"{base}/instance/{instance_id}/f3mf?type=download&fileType=3mfstl"
        data = await _makerworld_api_get(api, url, None, cookie)
        link = _first_download_url(data) if data is not None else None
        if link:
            return link

    # Fallback to the model-level download endpoint.
    api = f"https://makerworld.com/api/v1/models/{design_id}/download"
    data = await _makerworld_api_get(api, url, None, cookie)
    return _first_download_url(data) if data is not None else None


async def _resolve_makerworld_collection(
    url: str, cookie: Optional[str]
) -> Optional[tuple[str, list[CollectionMember]]]:
    collection_id = _collection_id(url)
    if not collection_id:
        return None
    html = await _makerworld_fetch_page(url, cookie)
    if not html:
        return None
    next_data = _extract_next_data(html)
    if next_data is None:
        return None

    title = _makerworld_collection_title(next_data, collection_id)
    members = _makerworld_collection_members(next_data)
    return (title, members) if members else None


# --------------------------------------------------------------------------- #
# Thingiverse (stable public per-thing zip endpoint)
# --------------------------------------------------------------------------- #
async def _resolve_thingiverse(url: str, cookie: Optional[str]) -> Optional[str]:
    thing_id = _thingiverse_id(url)
    if not thing_id:
        return None
    # Public things expose every file as one zip at this stable URL; it
    # 302-redirects to a CDN blob that ``download_to_staging`` follows. No API
    # token needed for public models, so we prefer it over the token dance.
    return f"https://www.thingiverse.com/thing:{thing_id}/zip"


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #
async def resolve_page_url(
    url: str,
    *,
    makerworld_cookie: Optional[str] = None,
    thingiverse_cookie: Optional[str] = None,
) -> Optional[str]:
    """Resolve a known model *page* URL to a direct download URL (see module doc)."""
    kind = classify_page(url)
    if kind is None:
        return None

    try:
        if kind == "printables":
            resolved = await _resolve_printables(url)
        elif kind == "makerworld":
            resolved = await _resolve_makerworld(url, makerworld_cookie)
        else:
            resolved = await _resolve_thingiverse(url, thingiverse_cookie)
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — network/parse boundary
        logger.warning("page resolution errored for %s: %s", url, exc)
        raise ImportError_(f"{kind}_resolve_failed") from exc

    if not resolved:
        raise ImportError_(f"{kind}_resolve_failed")
    return resolved


async def list_model_files(url: str) -> Optional[tuple[str, list[ModelFile]]]:
    """List a model page's selectable files without downloading anything.

    Printables-only (its API enumerates files cheaply). Returns ``(title, files)``
    or ``None`` for any other host, so the caller falls back to resolve+download.
    """
    if classify_page(url) != "printables":
        return None
    try:
        return await _list_printables_files(url)
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — network/parse boundary
        logger.warning("file listing errored for %s: %s", url, exc)
        raise ImportError_("printables_resolve_failed") from exc


async def resolve_selected_download(url: str, files: list[ModelFile]) -> list[str]:
    """Resolve direct download links for a user-chosen subset of a page's files."""
    if classify_page(url) != "printables":
        raise ImportError_("file_selection_unsupported")
    try:
        links = await _printables_download_links(url, files)
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — network/parse boundary
        logger.warning("selected download errored for %s: %s", url, exc)
        raise ImportError_("printables_resolve_failed") from exc
    if not links:
        raise ImportError_("printables_resolve_failed")
    return links


async def resolve_collection_url(
    url: str, *, makerworld_cookie: Optional[str] = None
) -> Optional[tuple[str, list[CollectionMember]]]:
    """Resolve a collection URL to ``(title, members)``; ``None`` if not a collection."""
    kind = classify_collection(url)
    if kind is None:
        return None

    try:
        if kind == "printables":
            resolved = await _resolve_printables_collection(url)
        else:
            resolved = await _resolve_makerworld_collection(url, makerworld_cookie)
    except ImportError_:
        raise
    except Exception as exc:  # noqa: BLE001 — network/parse boundary
        logger.warning("collection resolution errored for %s: %s", url, exc)
        raise ImportError_(f"{kind}_collection_resolve_failed") from exc

    if not resolved or not resolved[1]:
        raise ImportError_(f"{kind}_collection_resolve_failed")
    return resolved
