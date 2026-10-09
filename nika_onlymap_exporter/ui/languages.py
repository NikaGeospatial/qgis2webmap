"""Which language the dialog speaks, and fetching the ones that are not built in.

**English is built in; every other language is a download.** The plugin zip
carries no translations. Each language is one compiled Qt catalogue (`.qm`,
a few KB) kept in the public repository under `i18n/` and fetched on request
from jsDelivr, which serves any file of a tagged commit - so a release tag is
all it takes to publish the packs, with no extra upload step.

**Pinned, like the runtime.** `i18n/packs.json` ships inside the plugin and
holds the SHA-256 of every pack as it was at release. A download is accepted
only if it matches, so the bytes this plugin installs are exactly the ones it
was released with, whatever the CDN returns.

**What is shown:**

- The setting is "auto" (the default), "en", or a language code.
- "auto" follows the language QGIS is running in, *if* that language's pack is
  installed; otherwise English. Nothing is downloaded without a click - when
  QGIS is in a language we have but the pack is missing, the dialog offers it.
- An explicit choice wins over QGIS, and falls back to English while its pack
  is missing (a cleared cache, say) rather than failing.

Changing language takes effect when the dialog reopens, which the dialog does
itself; QGIS does not need a restart.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import hashlib
import json
import os
from collections.abc import Callable
from pathlib import Path

from ..packaging.runtime_manager import default_cache_dir

PACKAGE_DIR = Path(__file__).resolve().parent.parent
I18N_DIR = PACKAGE_DIR / "i18n"
PACKS_FILE = I18N_DIR / "packs.json"
REPO = "NikaGeospatial/qgis2webmap"

SETTING_KEY = "qgis2webmap/language"
OFFER_DISMISSED_KEY = "qgis2webmap/languageOfferDismissed"
AUTO = "auto"
ENGLISH = "en"

# Each language named in itself: someone looking for their language reads
# their own script, not the English word for it. English first, then the rest
# in the order they were added.
NATIVE_NAMES = {
    "en": "English",
    "pt_BR": "Português (Brasil)",
    "zh_CN": "中文（简体）",  # noqa: RUF001 - full-width brackets are correct in Chinese
    "hi": "हिन्दी",
    "es": "Español",
    "ar": "العربية",
    "fr": "Français",
}

# Written right to left. The dialog mirrors its layout for these.
RIGHT_TO_LEFT = {"ar"}

# Overrides the download location, for testing a pack before its release tag
# exists. Must still be https - see `pack_url`.
BASE_URL_ENV = "QGIS2WEBMAP_LANGUAGE_BASE_URL"

Fetcher = Callable[[str], bytes]


class LanguageDownloadError(RuntimeError):
    """A pack could not be fetched, or arrived different from the release."""


def pinned_packs() -> dict[str, str]:
    """Language code -> SHA-256 of its pack, as released with this plugin."""
    try:
        data = json.loads(PACKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.get("packs", {}).items()}


def plugin_version() -> str:
    for line in (PACKAGE_DIR / "metadata.txt").read_text(encoding="utf-8").splitlines():
        if line.startswith("version="):
            return line.split("=", 1)[1].strip()
    return "0"


def available() -> list[str]:
    """Every language this version can show: English, then the packs."""
    packs = pinned_packs()
    return [ENGLISH, *[code for code in NATIVE_NAMES if code in packs]]


def match_locale(locale: str) -> str | None:
    """The pack for a QGIS locale such as `pt_BR` or `es_MX`, if there is one.

    Exact first, then by language: QGIS in Portuguese (Portugal) reads the
    Brazilian pack, and Spanish (Mexico) the Spanish one, rather than English.
    """
    if not locale:
        return None
    packs = pinned_packs()
    if locale in packs:
        return locale
    language = locale.split("_", 1)[0].lower()
    if language == "en":
        return None
    return next((code for code in packs if code.split("_", 1)[0] == language), None)


def install_dir() -> Path:
    """Where downloaded packs live, per plugin version.

    Per version because each release's packs translate that release's text; a
    pack from an older version would leave every string added since in English
    and hide the fact that it is out of date.
    """
    return default_cache_dir().parent / "languages" / plugin_version()


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def installed_pack(code: str) -> Path | None:
    """The pack file for `code` if one is here and matches the release.

    A git checkout carries the packs in `i18n/` beside the code, so a
    contributor sees every language without downloading anything. An
    installed plugin has none there and reads the download folder.
    """
    expected = pinned_packs().get(code)
    if expected is None:
        return None
    for folder in (I18N_DIR, install_dir()):
        path = folder / f"qgis2webmap_{code}.qm"
        try:
            if path.is_file() and _sha256(path.read_bytes()) == expected:
                return path
        except OSError:
            continue
    return None


def effective(setting: str, qgis_locale: str) -> str:
    """The language to show: a pack code, or `en`."""
    if setting == AUTO:
        code = match_locale(qgis_locale)
        return code if code and installed_pack(code) else ENGLISH
    if setting != ENGLISH and installed_pack(setting):
        return setting
    return ENGLISH


def offer(setting: str, qgis_locale: str, dismissed: str = "") -> str | None:
    """A language worth offering: QGIS speaks it, we have it, it is not here.

    Only while following QGIS. Someone who chose English on purpose is not
    asked again, and neither is someone who closed the offer for this language.
    """
    if setting != AUTO:
        return None
    code = match_locale(qgis_locale)
    if code is None or code == dismissed or installed_pack(code) is not None:
        return None
    return code


def pack_url(code: str, version: str | None = None) -> str:
    base = os.environ.get(BASE_URL_ENV) or (
        f"https://cdn.jsdelivr.net/gh/{REPO}@v{version or plugin_version()}"
        "/nika_onlymap_exporter/i18n"
    )
    return f"{base.rstrip('/')}/qgis2webmap_{code}.qm"


def download(code: str, fetch: Fetcher) -> Path:
    """Fetch, verify and install the pack for `code`. Returns its path.

    Written beside its final name and renamed into place, so an interrupted
    download never leaves a half file that a later check might trust.
    """
    expected = pinned_packs().get(code)
    if expected is None:
        raise LanguageDownloadError(f"No language pack named {code!r} in this version.")
    url = pack_url(code)
    if not url.startswith("https://"):
        raise LanguageDownloadError(f"Refusing to fetch a language pack over {url}")

    data = fetch(url)
    if _sha256(data) != expected:
        raise LanguageDownloadError(
            "The language pack that arrived is not the one released with this "
            "version of the plugin, so it was not installed."
        )

    folder = install_dir()
    folder.mkdir(parents=True, exist_ok=True)
    final = folder / f"qgis2webmap_{code}.qm"
    partial = final.with_suffix(".part")
    partial.write_bytes(data)
    partial.replace(final)
    return final


def qgis_fetcher(timeout_ms: int = 30_000) -> Fetcher:
    """A fetcher through QGIS's network stack, so a configured proxy is used."""

    def fetch(url: str) -> bytes:
        from qgis.core import QgsBlockingNetworkRequest
        from qgis.PyQt.QtCore import QUrl
        from qgis.PyQt.QtNetwork import QNetworkRequest

        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", b"QGIS2WebMap-by-NIKA")
        request.setTransferTimeout(timeout_ms)
        fetcher = QgsBlockingNetworkRequest()
        if fetcher.get(request, True) != QgsBlockingNetworkRequest.ErrorCode.NoError:
            raise LanguageDownloadError(
                f"{fetcher.errorMessage()}\n\nIf this computer reaches the internet "
                "through a proxy, check Settings > Options > Network in QGIS."
            )
        return bytes(fetcher.reply().content())

    return fetch
