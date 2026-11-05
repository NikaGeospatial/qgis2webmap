"""Which language the dialog speaks, and fetching the ones that are not built in.

**English is built in; every other language is a download.** The plugin zip
carries no translations. Each language is one compiled Qt catalogue (`.qm`,
136-219 KB), and every release carries all of them in one extra asset,
`qgis2webmap-language-packs-<version>.zip` (about 350 KB), built beside the
plugin zip by `scripts/package_plugin.py`. Asking for any language downloads
that zip from the GitHub release and installs every language in it at once,
so a second language never needs a second download.

**Pinned, like the runtime.** `i18n/packs.json` ships inside the plugin and
holds the SHA-256 of every pack as it was at release. The download is accepted
only if every file in it matches its pin and nothing else is in it, so the
bytes this plugin installs are exactly the ones it was released with,
whatever arrives. It is installed as a whole or not at all.

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
import io
import json
import shutil
import tempfile
import zipfile
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

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

RELEASES_URL = f"https://github.com/{REPO}/releases/download"
# Must match `LANGUAGE_PACKS_BASENAME` in scripts/package_plugin.py.
ASSET_BASENAME = "qgis2webmap-language-packs"

# GitHub answers a release download with a redirect to its file host
# (release-assets.githubusercontent.com when this was written). The download
# follows it there, and to no other host.
TRUSTED_HOST = "github.com"
TRUSTED_HOST_SUFFIX = ".githubusercontent.com"
MAX_REDIRECTS = 5

# Far above what is released (the zip is about 350 KB, the largest pack 219
# KB) and far below what could hurt: a zip that unpacks to gigabytes is
# refused before it is unpacked.
MAX_DOWNLOAD_BYTES = 16 * 1024 * 1024
MAX_PACK_BYTES = 4 * 1024 * 1024

REDIRECTS = {301, 302, 303, 307, 308}


@dataclass(frozen=True)
class Reply:
    """One HTTP answer, redirects not followed."""

    status: int
    body: bytes = b""
    location: str = ""


Fetcher = Callable[[str], Reply]


class LanguageDownloadError(RuntimeError):
    """A pack could not be fetched, or arrived different from the release."""


def pinned_packs() -> dict[str, str]:
    """Language code -> SHA-256 of its pack, as released with this plugin."""
    try:
        data = json.loads(PACKS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    packs: dict[str, str] = {}
    for code, entry in data.get("packs", {}).items():
        digest = entry.get("sha256") if isinstance(entry, dict) else entry
        if isinstance(digest, str) and digest:
            packs[str(code)] = digest
    return packs


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


def asset_name(version: str | None = None) -> str:
    return f"{ASSET_BASENAME}-{version or plugin_version()}.zip"


def asset_url(version: str | None = None) -> str:
    """The language packs zip attached to this version's GitHub release."""
    version = version or plugin_version()
    return f"{RELEASES_URL}/v{version}/{asset_name(version)}"


def _trusted(url: str) -> bool:
    """https to GitHub or its file host, and nowhere else."""
    parts = urlsplit(url)
    host = (parts.hostname or "").lower()
    return (
        parts.scheme == "https"
        and parts.port in (None, 443)
        and not parts.username
        and (host == TRUSTED_HOST or host.endswith(TRUSTED_HOST_SUFFIX))
    )


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


def _get(url: str, fetch: Fetcher) -> bytes:
    """The body at `url`, following redirects only to hosts `_trusted` names."""
    for _ in range(MAX_REDIRECTS + 1):
        if not _trusted(url):
            raise LanguageDownloadError(
                f"Refusing to fetch the language packs from {_origin(url)}"
            )
        reply = fetch(url)
        if reply.status in REDIRECTS and reply.location:
            url = urljoin(url, reply.location)
            continue
        if reply.status != 200:
            raise LanguageDownloadError(
                f"The language packs could not be downloaded (HTTP {reply.status})."
            )
        if len(reply.body) > MAX_DOWNLOAD_BYTES:
            raise LanguageDownloadError("The language packs download is too large.")
        return reply.body
    raise LanguageDownloadError("The language packs download redirected too often.")


def _pack_name(code: str) -> str:
    return f"qgis2webmap_{code}.qm"


def verified_packs(data: bytes, pins: dict[str, str]) -> dict[str, bytes]:
    """File name -> bytes of every pack in the zip `data`, each checked.

    All or nothing: the zip must hold exactly the pinned packs, by name at its
    root, each within `MAX_PACK_BYTES` and matching its pin. Anything else -
    an extra file, a path, a pack missing, damaged or different - refuses the
    whole download.
    """
    expected = {_pack_name(code): digest for code, digest in pins.items()}
    packs: dict[str, bytes] = {}
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            for info in archive.infolist():
                name = info.filename
                # Exact names only, so `../x` or `a/qgis2webmap_fr.qm` is
                # never written anywhere, let alone outside the folder.
                if name not in expected or name in packs:
                    raise LanguageDownloadError(
                        "The language packs download holds an unexpected file, "
                        f"{name!r}."
                    )
                if info.file_size > MAX_PACK_BYTES:
                    raise LanguageDownloadError(
                        f"{name} in the language packs download is too large."
                    )
                with archive.open(info) as member:
                    content = member.read(MAX_PACK_BYTES + 1)
                if len(content) > MAX_PACK_BYTES:
                    raise LanguageDownloadError(
                        f"{name} in the language packs download is too large."
                    )
                if _sha256(content) != expected[name]:
                    raise LanguageDownloadError(
                        "The language packs that arrived are not the ones released "
                        "with this version of the plugin, so none was installed."
                    )
                packs[name] = content
    except (zipfile.BadZipFile, zlib.error, EOFError, OSError) as exc:
        raise LanguageDownloadError(
            f"The language packs download is damaged ({exc}), so none was installed."
        ) from exc
    missing = sorted(set(expected) - set(packs))
    if missing:
        raise LanguageDownloadError(
            f"The language packs download lacks {', '.join(missing)}."
        )
    return packs


def _install_all(packs: dict[str, bytes]) -> None:
    """Make `packs` the installed set, in one rename.

    Written into a fresh folder beside the final one, which then takes its
    place, so an interrupted install leaves the old set or the new one, never
    a mix that a later check might partly trust.
    """
    final = install_dir()
    final.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{final.name}-", dir=final.parent))
    retired = staging.with_name(staging.name + "-old")
    try:
        for name, content in packs.items():
            (staging / name).write_bytes(content)
        if final.exists():
            final.rename(retired)
        try:
            staging.rename(final)
        except OSError:
            if retired.exists() and not final.exists():
                retired.rename(final)
            raise
    except OSError as exc:
        raise LanguageDownloadError(
            f"The language packs could not be installed: {exc}"
        ) from exc
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.rmtree(retired, ignore_errors=True)


def download(code: str, fetch: Fetcher) -> Path:
    """Fetch, verify and install every language pack. Returns `code`'s path.

    One download for all of them: whichever language is asked for, the rest
    come with it.
    """
    pins = pinned_packs()
    if code not in pins:
        raise LanguageDownloadError(f"No language pack named {code!r} in this version.")
    _install_all(verified_packs(_get(asset_url(), fetch), pins))
    return install_dir() / _pack_name(code)


def qgis_fetcher(timeout_ms: int = 30_000) -> Fetcher:
    """One request through QGIS's network stack, so a configured proxy is used.

    Redirects are not followed here: `download` follows them itself, and only
    to the hosts it trusts. A reply growing past `MAX_DOWNLOAD_BYTES` is cut
    off as it arrives rather than held in memory.
    """

    def fetch(url: str) -> Reply:
        from qgis.core import QgsNetworkAccessManager
        from qgis.PyQt.QtCore import QEventLoop, QUrl
        from qgis.PyQt.QtNetwork import QNetworkReply, QNetworkRequest

        request = QNetworkRequest(QUrl(url))
        request.setRawHeader(b"User-Agent", b"QGIS2WebMap-by-NIKA")
        request.setTransferTimeout(timeout_ms)
        request.setAttribute(
            QNetworkRequest.Attribute.RedirectPolicyAttribute,
            QNetworkRequest.RedirectPolicy.ManualRedirectPolicy,
        )
        reply = QgsNetworkAccessManager.instance().get(request)
        # Bounded, so Qt's network thread does not buffer far ahead of `read`,
        # which is where an oversized reply is cut off.
        reply.setReadBufferSize(256 * 1024)
        body = bytearray()
        too_large: list[bool] = []

        def read() -> None:
            if too_large:
                return
            body.extend(bytes(reply.readAll()))
            if len(body) > MAX_DOWNLOAD_BYTES:
                too_large.append(True)
                reply.abort()

        reply.readyRead.connect(read)
        loop = QEventLoop()
        reply.finished.connect(loop.quit)
        if not reply.isFinished():
            loop.exec()
        read()
        try:
            if too_large:
                raise LanguageDownloadError("The language packs download is too large.")
            if reply.error() != QNetworkReply.NetworkError.NoError:
                raise LanguageDownloadError(
                    f"{reply.errorString()}\n\nIf this computer reaches the internet "
                    "through a proxy, check Settings > Options > Network in QGIS."
                )
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            location = bytes(reply.rawHeader(b"Location")).decode("latin-1")
            return Reply(int(status or 0), bytes(body), location)
        finally:
            reply.deleteLater()

    return fetch
