"""The publish handshake: reserve, upload, finalize.

Three calls and a set of PUTs, in that order and never in another:

1. `POST /maps/publish/start` names the files and their sizes. **No map bytes
   leave the machine here** - only a title, filenames and lengths - which is
   what makes it safe to run before the last confirmation, and is why the free
   tier can be discovered (a null `licenseKey`) while the user can still stop.
2. A plain `PUT` per file to the presigned URL the server handed back. No
   Authorization header on these: the signature in the query string *is* the
   authorization, and attaching a bearer token to a third-party storage host
   would leak it to somewhere it was never issued for.
3. `POST /maps/publish/finalize` makes the version live.

`licenseKey` coming back `None` is the free tier, not a failure. A free map
keeps OnlyMap's own caps, which is what `hosting.consent` warns about.

**HTTPS is not optional and is not configurable**, for both the API and the
presigned URLs, and for the same reason `packaging.runtime_manager` refuses a
plain-HTTP runtime: a downgrade puts the user's map - and their bearer token -
in front of anyone on the path. The check is applied to the presigned URLs too
even though the server produced them, because "the server said so" is not a
property this client can verify.

The `Transport` seam mirrors `runtime_manager.Downloader` and exists for the
same two reasons: QGIS users behind a corporate proxy need requests to go
through the stack they configured in QGIS, and the test suite needs the whole
handshake exercised without a socket.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

# The two deployments. Dev is never the default: a user who publishes to it
# would get a URL that quietly stops working.
PROD_API_BASE = "https://api.nika.eco"
DEV_API_BASE = "https://api-dev.nika.eco"

# The escape hatch for staging and for anyone running the API locally behind a
# TLS terminator. Same precedence idea as `core.settings.LICENSE_KEY_ENV`: the
# environment overrides the built-in default and nothing is written to the
# project.
API_BASE_ENV = "NIKA_API_BASE"

# **The complete set of filenames the server accepts.** Enforced here rather
# than only server-side so a rejection costs nothing: discovering the allowlist
# after uploading twenty megabytes is the failure this avoids.
#
# `onlymap.js` is deliberately absent, and its absence is why hosting a map now
# costs 8.3 MB less than the artifact on disk. The runtime is the same bytes for
# every map built against a release, so NIKA stores one copy per runtime version
# and serves it to every map pinned to that version; the exported page still
# asks for `./onlymap.js` and the server answers by pointing it at the shared
# copy. The server mints no upload slot for that name at all - a publish that
# could write it would be choosing the JavaScript on every other customer's
# pages - so sending it would be refused rather than merely ignored.
ALLOWED_FILENAMES = ("index.html", "thumbnail.png", "thumbnail.jpg")

# Metadata calls are small and should fail fast; an upload carries the map.
REQUEST_TIMEOUT_SECONDS = 30
UPLOAD_TIMEOUT_SECONDS = 300

# A presigned PUT that fails is usually a dropped connection or a storage node
# briefly refusing, both of which succeed on a retry. Three attempts total,
# because a fourth mostly means the network is gone and the user is watching a
# progress bar that will never move.
UPLOAD_ATTEMPTS = 3

USER_AGENT = "QGIS2WebMap-by-NIKA"


class HostingError(RuntimeError):
    """Publishing failed, with a message written for the person publishing."""


class AuthRequiredError(HostingError):
    """The token was missing, expired or revoked.

    Its own type because the caller must do something different about it: sign
    in again rather than retry. `hosting.auth.clear_token` is the other half -
    a token the server has rejected must not stay on disk waiting to fail again.
    """


@dataclass(frozen=True)
class HttpRequest:
    """One outbound request, as data, so a fake transport can assert on it."""

    method: str
    url: str
    body: bytes | None = None
    headers: Mapping[str, str] = field(default_factory=dict)
    timeout: int = REQUEST_TIMEOUT_SECONDS


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


# Performs one request. The only thing this module knows about the outside
# world; `ui.host_flow` supplies a QGIS-backed implementation so a proxy
# configured in QGIS Options is honoured, and the tests supply a fake.
Transport = Callable[[HttpRequest], HttpResponse]


@dataclass(frozen=True)
class UploadFile:
    """A file to publish: its allowlisted name and how big it is."""

    filename: str
    size_bytes: int


@dataclass(frozen=True)
class UploadTarget:
    """Where one file's bytes go. `url` is presigned and short-lived."""

    filename: str
    url: str


@dataclass(frozen=True)
class PublishStart:
    """The reservation. Nothing is live yet and nothing has been uploaded.

    `license_key` is `None` on the free tier. That is a correct answer, not an
    error, and the one signal the client gets about which caps the hosted map
    will render under.
    """

    map_id: str
    version: int
    upload_urls: tuple[UploadTarget, ...]
    license_key: str | None = None
    expires_at: str | None = None
    public_url: str = ""
    expires_in: int | None = None
    # Which OnlyMap build the map will actually run, and whether that differs
    # from the one this plugin built it against. Reported at `start` rather than
    # only at `finalize` so the user learns about a substitution while there is
    # still something they can do about it.
    runtime_version: str | None = None
    runtime_substituted: bool = False

    @property
    def is_free_tier(self) -> bool:
        return self.license_key is None

    def target_for(self, filename: str) -> UploadTarget:
        for target in self.upload_urls:
            if target.filename == filename:
                return target
        raise HostingError(
            f"The server did not offer an upload slot for {filename}. "
            "Nothing was published; try again."
        )


@dataclass(frozen=True)
class PublishResult:
    """A live map."""

    map_id: str
    public_url: str
    version: int
    expires_at: str | None = None
    runtime_version: str | None = None
    # The owner-facing sentence for a map not running the build it was authored
    # against. `None` is the ordinary case. Never a failure: the map is live.
    runtime_warning: str | None = None


# Called with (bytes sent, total bytes) as an upload proceeds.
ProgressCallback = Callable[[int, int], None]


def resolve_api_base(explicit: str | None = None) -> str:
    """Which deployment to talk to, most specific source first.

    Same shape as `core.settings.resolve_license_key`, and for the same reason:
    one documented precedence beats three places to look when a request lands
    somewhere unexpected.
    """
    for candidate in (explicit, os.environ.get(API_BASE_ENV)):
        text = (candidate or "").strip().rstrip("/")
        if text:
            return require_https(text)
    return PROD_API_BASE


def require_https(url: str) -> str:
    """The one gate every URL in this module passes through."""
    if not url.lower().startswith("https://"):
        raise HostingError(
            f"Refusing to publish over {url}. Hosting requires HTTPS, because "
            "anything else puts your map and your sign-in token in front of "
            "whoever is on the network between you and the server."
        )
    return url


def urllib_transport(request: HttpRequest) -> HttpResponse:
    """The stdlib transport. Knows nothing about QGIS, so it stays testable."""
    require_https(request.url)
    headers = {"User-Agent": USER_AGENT, **dict(request.headers)}
    raw = urllib.request.Request(
        request.url, data=request.body, headers=headers, method=request.method
    )
    try:
        # B310 is suppressed on the call below. The scheme is checked by
        # `require_https` immediately above and again inside every caller;
        # B310 exists to catch `urlopen` on a string that could name `file://`
        # or a custom handler, which this cannot be. Recorded as a pragma
        # rather than left outstanding because the QGIS plugin scanner runs
        # Bandit as a blocking check.
        #
        # This comment must not begin with the pragma word itself: Bandit reads
        # the rest of such a line as a list of test IDs.
        with urllib.request.urlopen(raw, timeout=request.timeout) as response:  # nosec B310
            return HttpResponse(status=int(response.status), body=response.read())
    except urllib.error.HTTPError as exc:
        # An error status is an answer, not a transport failure: the body
        # usually carries the server's own explanation, which is far better
        # than "HTTP Error 402".
        return HttpResponse(status=int(exc.code), body=exc.read())
    except urllib.error.URLError as exc:
        raise HostingError(f"Could not reach {request.url}.\n\n{exc.reason}") from exc
    except OSError as exc:  # a reset or timeout mid-body arrives as this
        raise HostingError(f"The connection to {request.url} failed.\n\n{exc}") from exc


# ---------------------------------------------------------------------------
# Reading JSON without pretending it is typed
# ---------------------------------------------------------------------------

# JSON is dynamic by nature; `object` values plus the narrow readers below
# keep every field that reaches a dataclass explicitly checked, which a
# `dict[str, Any]` would quietly skip.
JsonObject = dict[str, object]


def decode_json(response: HttpResponse, what: str) -> JsonObject:
    """A JSON object from a response body, or a message a user can act on."""
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise HostingError(
            f"The hosting server's reply to {what} was not readable. "
            "Nothing was published."
        ) from exc
    if not isinstance(payload, dict):
        raise HostingError(f"The hosting server's reply to {what} was not an object.")
    return payload


def server_message(response: HttpResponse) -> str:
    """The server's own explanation of a failure, if it sent one."""
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return response.body.decode("utf-8", "replace").strip()[:400]
    if isinstance(payload, dict):
        for key in ("message", "error", "detail"):
            value = payload.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def _text(data: JsonObject, key: str) -> str:
    value = data.get(key)
    return value.strip() if isinstance(value, str) else ""


def _optional_text(data: JsonObject, key: str) -> str | None:
    """Empty string and absent both mean "not set"; `None` is the one answer.

    Collapsing them matters for `licenseKey`: a server that sends `""` for a
    free account must not read as a licensed one.
    """
    return _text(data, key) or None


def _optional_int(data: JsonObject, key: str) -> int | None:
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _required_int(data: JsonObject, key: str, what: str) -> int:
    """A whole number from the reply, or a readable failure.

    `version` arrives as a JSON *number*, not a string, so `_required_text`
    silently reads it as absent - which failed every publish on the first call
    with a message blaming the server for omitting a field it had sent. Accepts
    a numeric string too, since the cost of being lenient in one direction here
    is nothing and the cost of being wrong is a dead feature.
    """
    value = data.get(key)
    if isinstance(value, bool):  # bool is an int subclass; never a version
        value = None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    raise HostingError(
        f"The hosting server's reply to {what} was missing a usable '{key}'. "
        "Nothing was published."
    )


def _required_text(data: JsonObject, key: str, what: str) -> str:
    value = _text(data, key)
    if not value:
        raise HostingError(
            f"The hosting server's reply to {what} was missing '{key}'. "
            "Nothing was published."
        )
    return value


def _raise_for_status(response: HttpResponse, what: str) -> None:
    if 200 <= response.status < 300:
        return
    if response.status in (401, 403):
        raise AuthRequiredError(
            "Your NIKA sign-in is no longer valid. Sign in again and republish."
        )
    detail = server_message(response)
    suffix = f"\n\n{detail}" if detail else ""
    raise HostingError(
        f"The hosting server refused {what} (HTTP {response.status}).{suffix}"
    )


class HostingClient:
    """The publish handshake against one API base, for one signed-in user."""

    def __init__(
        self,
        token: str,
        api_base: str | None = None,
        transport: Transport | None = None,
    ) -> None:
        self.token = token
        self.api_base = resolve_api_base(api_base)
        self._transport: Transport = transport or urllib_transport

    def _post(self, path: str, payload: JsonObject, what: str) -> JsonObject:
        url = require_https(f"{self.api_base}{path}")
        response = self._transport(
            HttpRequest(
                method="POST",
                url=url,
                body=json.dumps(payload).encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {self.token}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
            )
        )
        _raise_for_status(response, what)
        return decode_json(response, what)

    def start_publish(
        self,
        title: str,
        files: Sequence[UploadFile],
        map_id: str | None = None,
        runtime_version: str | None = None,
    ) -> PublishStart:
        """Reserve a version and collect the presigned URLs.

        The allowlist is checked before the request so a bad filename costs a
        message rather than a round trip, and so the set of names that can ever
        reach the server is visible in one place in this client.
        """
        if not files:
            raise HostingError("There is nothing to publish.")
        for item in files:
            if item.filename not in ALLOWED_FILENAMES:
                raise HostingError(
                    f"'{item.filename}' cannot be published. Hosted maps carry "
                    f"only {', '.join(ALLOWED_FILENAMES)}."
                )

        payload: JsonObject = {
            "title": title,
            "files": [
                {"filename": item.filename, "sizeBytes": item.size_bytes}
                for item in files
            ],
        }
        # Omitted rather than sent as null on a first publish: `mapId` present
        # is what tells the server this is a republication of an existing map.
        if map_id:
            payload["mapId"] = map_id
        # Declared so the server can pin the map to the matching runtime. A
        # version it does not host yet is substituted, not refused, so an
        # unrecognised value costs a warning rather than a failed publish.
        if runtime_version:
            payload["runtimeVersion"] = runtime_version

        data = self._post("/maps/publish/start", payload, "starting the upload")
        targets: list[UploadTarget] = []
        raw_targets = data.get("uploadUrls")
        if isinstance(raw_targets, list):
            for entry in raw_targets:
                if not isinstance(entry, dict):
                    continue
                filename = _text(entry, "filename")
                url = _text(entry, "url")
                if filename and url:
                    targets.append(UploadTarget(filename, require_https(url)))

        return PublishStart(
            map_id=_required_text(data, "mapId", "starting the upload"),
            version=_required_int(data, "version", "starting the upload"),
            upload_urls=tuple(targets),
            license_key=_optional_text(data, "licenseKey"),
            expires_at=_optional_text(data, "expiresAt"),
            public_url=_text(data, "publicUrl"),
            expires_in=_optional_int(data, "expiresIn"),
            runtime_version=_optional_text(data, "runtimeVersion"),
            runtime_substituted=data.get("runtimeSubstituted") is True,
        )

    def upload(
        self,
        target: UploadTarget,
        payload: bytes,
        content_type: str = "application/octet-stream",
    ) -> None:
        """PUT one file's bytes to its presigned URL.

        No Authorization header, by design - see the module docstring. Retried
        because the common failures here are transient and the alternative is
        making the user re-upload the whole map.
        """
        require_https(target.url)
        last = ""
        for _attempt in range(UPLOAD_ATTEMPTS):
            try:
                response = self._transport(
                    HttpRequest(
                        method="PUT",
                        url=target.url,
                        body=payload,
                        headers={
                            "Content-Type": content_type,
                            "Content-Length": str(len(payload)),
                        },
                        timeout=UPLOAD_TIMEOUT_SECONDS,
                    )
                )
            except HostingError as exc:
                last = str(exc)
                continue

            if 200 <= response.status < 300:
                return

            detail = server_message(response)
            last = f"HTTP {response.status}" + (f" - {detail}" if detail else "")
            # A presigned URL that has expired or been tampered with comes back
            # 4xx and will come back 4xx again. Only the statuses that can
            # plausibly change on their own are worth another attempt, and
            # re-PUTting tens of megabytes into a certain refusal is not free.
            if response.status < 500 and response.status != 429:
                break

        raise HostingError(
            f"Uploading {target.filename} failed after {UPLOAD_ATTEMPTS} "
            f"attempts.\n\n{last}\n\nNothing was published; the map you have on "
            "disk is unchanged. Check your connection and try again."
        )

    def finalize(
        self,
        map_id: str,
        version: int,
        title: str | None = None,
        runtime_version: str | None = None,
    ) -> PublishResult:
        """Make the uploaded version the live one."""
        payload: JsonObject = {"mapId": map_id, "version": version}
        if title:
            payload["title"] = title
        # Sent again rather than relied upon from `start`: the server persists
        # nothing at `start`, so this call is the one whose answer is stored.
        if runtime_version:
            payload["runtimeVersion"] = runtime_version
        data = self._post("/maps/publish/finalize", payload, "publishing the map")
        return PublishResult(
            map_id=_text(data, "mapId") or map_id,
            public_url=_required_text(data, "publicUrl", "publishing the map"),
            # Echoed back by the server; fall back to what we sent.
            version=_required_int(
                {"version": data.get("version", version)},
                "version",
                "publishing the map",
            ),
            expires_at=_optional_text(data, "expiresAt"),
            runtime_version=_optional_text(data, "runtimeVersion"),
            runtime_warning=_optional_text(data, "runtimeWarning"),
        )
