"""The publish handshake: start, upload what is missing, complete, watch.

Four calls and a set of PUTs, in that order and never in another:

1. `POST /maps/publish/start` sends the **manifest** - every file's path, size and
   sha256, plus the runtime the page expects. **No map bytes leave the machine
   here**, only a description of them, which is what makes it safe to run
   before the last confirmation and why the user can still stop.
2. A plain `PUT` per file the server asked for. It asks for a file only when it
   does not already hold those bytes: the store is content-addressed, so a
   republish of a map whose data did not change uploads nothing at all. An
   empty `uploads` list is a successful answer, not an empty one.
3. `POST /maps/publish/complete` hands back the release id and nothing else. The
   manifest was already stated at `start`; restating it here would create a
   second place for the two to disagree.
4. `GET /maps/releases/{id}` until the release is `live` or `failed`. The server
   verifies every digest it was promised before the map is served, so "the
   upload finished" and "the map is up" are genuinely different moments.

No Authorization header on the PUTs: the signature in the query string *is* the
authorization, and attaching a bearer token to a third-party storage host would
leak it to somewhere it was never issued for.

That holds for the `presigned` upload mode, which is the only one a deployed
server offers. The local dev stack cannot presign - Miniflare has no such thing
- so it answers with the `direct` mode instead: a relative path back onto the
API itself, which *is* an ordinary authenticated endpoint and therefore *does*
carry the bearer token. The mode is read off the server's answer and never
inferred from the URL's shape, and a mode this plugin does not know is refused
by name rather than defaulted, because guessing would put the map's bytes - and
possibly the token - somewhere nobody chose.

**What is publishable is the server's call now.** This client carries no
filename allowlist: the manifest names the files and the server's kind registry
decides whether that set is a map. A client-side list could only ever be a
stale copy of that decision, and it silently dropped data files.

`licenseKey` coming back `None` is the free tier, not a failure. A free map
keeps OnlyMap's own caps, which is what `hosting.consent` warns about.

**HTTPS is not optional and is not configurable**, for both the API and the
presigned URLs, and for the same reason `packaging.runtime_manager` refuses a
plain-HTTP runtime: a downgrade puts the user's map - and their bearer token -
in front of anyone on the path. The check is applied to the presigned URLs too
even though the server produced them, because "the server said so" is not a
property this client can verify.

The one exemption, off by default and named in full at `ALLOW_HTTP_LOOPBACK_ENV`
below: plain HTTP to a loopback address, for the local dev stack, which has no
certificate and never will. It covers the API base and the URLs built from it
and nothing else - a presigned URL still has to be https whatever the
environment says, because that one is the server's claim rather than the
developer's own machine - and a publish running under it says so in the
confirmation the user reads.

The `Transport` seam mirrors `runtime_manager.Downloader` and exists for the
same two reasons: QGIS users behind a corporate proxy need requests to go
through the stack they configured in QGIS, and the test suite needs the whole
handshake exercised without a socket.

Copyright (C) 2026 NIKA
SPDX-License-Identifier: GPL-2.0-or-later
"""

from __future__ import annotations

import ipaddress
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

from ._build_target import API_BASE as BUILD_API_BASE
from .manifest import PublishManifest

# The two deployments. Dev is never the default: a user who publishes to it
# would get a URL that quietly stops working.
PROD_API_BASE = "https://api.nika.eco"
DEV_API_BASE = "https://api-dev.nika.eco"

# The escape hatch for staging and for anyone running the API locally behind a
# TLS terminator. Same precedence idea as `core.settings.LICENSE_KEY_ENV`: the
# environment overrides the built-in default and nothing is written to the
# project.
API_BASE_ENV = "NIKA_API_BASE"

# The one place plain HTTP is tolerated, and only because the local dev stack
# cannot be anything else: Miniflare serves the API over `http://localhost:8787`
# with no certificate to terminate, so without this the publish button could
# never be pressed on a developer's own machine and the loop that the whole
# local stack exists to close would stay open.
#
# It is a separate variable on purpose, compared to one exact string and off
# unless that string is present. Keying it off `API_BASE_ENV` being set - or off
# any debug flag - would hand the exemption to everyone who merely points the
# plugin at a staging host, which is precisely the person who must not get it:
# their traffic crosses a network, and a downgrade there is the bearer token on
# the wire. Two deliberate acts are required to publish over plain HTTP, and
# the second one still only permits a loopback address.
ALLOW_HTTP_LOOPBACK_ENV = "NIKA_ALLOW_INSECURE_LOOPBACK"
ALLOW_HTTP_LOOPBACK_VALUE = "1"

# Metadata calls are small and should fail fast; an upload carries the map.
REQUEST_TIMEOUT_SECONDS = 30
UPLOAD_TIMEOUT_SECONDS = 300

# A presigned PUT that fails is usually a dropped connection or a storage node
# briefly refusing, both of which succeed on a retry. Three attempts total,
# because a fourth mostly means the network is gone and the user is watching a
# progress bar that will never move.
UPLOAD_ATTEMPTS = 3

# Verification is digest work over bytes the server already has, so it is fast
# for a small map and not instant for a large one. Two seconds is short enough
# that a quick release feels immediate and long enough that a slow one is not
# polled a hundred times; five minutes is the point past which something is
# wrong on the server rather than merely slow.
POLL_INTERVAL_SECONDS = 2.0
POLL_TIMEOUT_SECONDS = 300.0

# The states `GET /maps/releases/{id}` reports. Only two of them are an ending.
STATE_VERIFYING = "verifying"
STATE_LIVE = "live"
STATE_FAILED = "failed"
TERMINAL_STATES = (STATE_LIVE, STATE_FAILED)

USER_AGENT = "QGIS2WebMap-by-NIKA"


class HostingError(RuntimeError):
    """Publishing failed, with a message written for the person publishing."""


class AuthRequiredError(HostingError):
    """The token was missing, expired or revoked.

    Its own type because the caller must do something different about it: sign
    in again rather than retry. `hosting.auth.clear_token` is the other half -
    a token the server has rejected must not stay on disk waiting to fail again.
    """


# The refusal codes `POST /maps/publish/start` answers with, other than the
# release conflict, which has its own type because the dialog asks a question
# about it. Named here because two of them arrive on statuses that otherwise
# mean something else entirely: `map_limit_reached` is a 403, which without
# this list would be read as an expired sign-in and send the publisher off to
# authenticate a token that was never the problem.
REFUSAL_MAP_LIMIT_REACHED = "map_limit_reached"
REFUSAL_MAP_TOO_LARGE = "map_too_large"
REFUSAL_RATE_LIMITED = "publish_rate_limited"

# The 409 that IS a question for the user. It shares its status with at least
# one refusal that is not - `map_taken_down` - so the status alone cannot decide
# which of the two arrived, and reading it as the conflict was a real bug: a
# taken-down map produced "this map is at release 0, published by someone else,
# publishing will replace release 0 with your version" over a "Publish anyway"
# button, for a map nobody had touched and a release that does not exist. The
# zero and the phantom colleague were an absent `currentRelease` and an absent
# `publishedBy` read as data. Reported 2026-09-18.
REFUSAL_RELEASE_CONFLICT = "release_conflict"

# Codes that are a refusal to publish rather than a refusal to authenticate.
# A whitelist, not a rule about which statuses may carry one: anything else on
# a 401 or 403 stays an authentication failure, because guessing wrong in that
# direction hides a genuinely expired token behind a quota message.
NON_AUTH_REFUSAL_CODES = frozenset({REFUSAL_MAP_LIMIT_REACHED})


class PublishRefusedError(HostingError):
    """The server refused to publish and said why in a form we can act on.

    The message is the server's own and is already written for the publisher.
    What this adds is the machine-readable half - `error.details` - because a
    person told "you are over the limit" without the number, or "try later"
    without the time, has been told less than we know.

    One type with one optional field per code, rather than three types: no
    caller yet does anything different about which refusal it is, and every one
    of them ends the same way - the publish stops and the user reads a
    sentence. `code` is carried so a caller that wants to branch can, and so an
    unrecognised refusal still arrives as itself rather than as a bare string.
    """

    def __init__(
        self,
        message: str,
        code: str,
        limit: int | None = None,
        limit_mb: float | None = None,
        actual_mb: float | None = None,
        retry_at: str = "",
    ) -> None:
        super().__init__(message)
        self.code = code
        # `map_limit_reached`: how many maps the plan hosts.
        self.limit = limit
        # `map_too_large`: the per-map cap and what this map weighs, in MB.
        self.limit_mb = limit_mb
        self.actual_mb = actual_mb
        # `publish_rate_limited`: when a slot frees, as an ISO 8601 instant.
        # The server's message carries a clock time to the minute; this is the
        # precise one, and the only form a caller can compute with.
        self.retry_at = retry_at


class PublishConflictError(HostingError):
    """Someone else published this map since this plugin last looked.

    Its own type, and carrying the server's account of what happened as typed
    attributes, because the only sensible response is a question for the user:
    overwrite a colleague's release, or stop. The retry is the same `start`
    call with `force=True`; the dialog that asks lives in `ui/`, and putting
    the decision anywhere near this module would make the overwrite automatic.
    """

    def __init__(
        self,
        message: str,
        current_release: int,
        published_by: str = "",
        published_at: str = "",
        was_rollback: bool = False,
        rolled_back_to: int | None = None,
    ) -> None:
        super().__init__(message)
        self.current_release = current_release
        self.published_by = published_by
        self.published_at = published_at
        # A rollback is not a stale client: the live release deliberately went
        # backwards, so "your version is older" is the expected state and the
        # warning has to read differently.
        self.was_rollback = was_rollback
        self.rolled_back_to = rolled_back_to


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

# Pauses for a number of seconds. Injected only so the poll loop is testable
# without a test that actually waits five minutes.
Sleeper = Callable[[float], None]


@dataclass(frozen=True)
class UploadFile:
    """A file to publish: its name and how big it is.

    Not part of the handshake any more - the manifest is - but still what
    `hosting.consent` describes to the user before anything is sent.
    """

    filename: str
    size_bytes: int


# How the bytes are meant to travel, as the server states it. `presigned` is
# the deployed answer; `direct` exists only because the local dev stack cannot
# presign. A closed set: an unrecognised mode is a version mismatch and is
# refused by name, never quietly treated as one of these.
UploadMode = Literal["presigned", "direct"]
UPLOAD_MODE_PRESIGNED = "presigned"
UPLOAD_MODE_DIRECT = "direct"


@dataclass(frozen=True)
class UploadTarget:
    """Where one file's bytes go. `url` is presigned and short-lived.

    Keyed by `sha256` rather than by name because the store is content
    addressed: two files with the same bytes are one object, and the digest is
    the only thing both ends agree on.

    `headers` are the server's, sent verbatim. They are usually part of what
    the signature covers, so editing them turns a working upload into a 403.

    `url` is always absolute by the time it reaches here: a `direct` target
    arrives as a path relative to the API base and is joined to it - and
    https-checked - while the reply is being read, so nothing downstream has to
    know which mode produced it. `mode` survives that join for exactly one
    reason: it is what decides whether the PUT carries the bearer token.
    """

    sha256: str
    url: str
    mode: UploadMode = UPLOAD_MODE_PRESIGNED
    headers: Mapping[str, str] = field(default_factory=dict)

    @property
    def is_authenticated(self) -> bool:
        """Whether the PUT goes to our own API rather than to storage."""
        return self.mode == UPLOAD_MODE_DIRECT


@dataclass(frozen=True)
class PublishStart:
    """The reservation. Nothing is live yet and nothing has been uploaded.

    `uploads` holds a target **only** for files the server does not already
    hold. An empty tuple is the ordinary answer to republishing a map nothing
    changed in, and it means every byte is already stored - not that the server
    forgot to answer.

    `license_key` is `None` on the free tier. That is a correct answer, not an
    error, and the one signal the client gets about which caps the hosted map
    will render under.
    """

    release_id: str
    map_id: str
    release_n: int
    uploads: tuple[UploadTarget, ...] = ()
    license_key: str | None = None

    @property
    def is_free_tier(self) -> bool:
        return self.license_key is None

    def target_for(self, sha256: str) -> UploadTarget | None:
        """The upload slot for a digest, or `None` when it needs no upload.

        `None` is not an error and must not be treated as one: it is the server
        saying it already holds those exact bytes. Uploading anyway would spend
        the user's bandwidth on a file that is already there.
        """
        for target in self.uploads:
            if target.sha256 == sha256:
                return target
        return None


@dataclass(frozen=True)
class ReleaseStatus:
    """One answer from `GET /maps/releases/{id}`."""

    state: str
    map_id: str = ""
    release_n: int = 0
    public_url: str = ""
    # The server's own account of why verification failed. Surfaced verbatim:
    # it names the file or digest that did not match, which no message written
    # here could.
    error: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    @property
    def is_live(self) -> bool:
        return self.state == STATE_LIVE


@dataclass(frozen=True)
class PublishResult:
    """A live map."""

    map_id: str
    public_url: str
    release_n: int


# Called with (bytes sent, total bytes) as an upload proceeds.
ProgressCallback = Callable[[int, int], None]


def resolve_api_base(explicit: str | None = None) -> str:
    """Which deployment to talk to, most specific source first.

    Same shape as `core.settings.resolve_license_key`, and for the same reason:
    one documented precedence beats three places to look when a request lands
    somewhere unexpected.
    """
    return require_secure_url(_configured_api_base(explicit))


def _configured_api_base(explicit: str | None = None) -> str:
    """The base as configured, before any scheme gate is applied.

    Split out of `resolve_api_base` so that the wording shown to the user can
    ask *which* base is about to be used without having to survive - or
    swallow - the refusal that an unacceptable one raises.
    """
    # Most specific first. The environment still outranks the build stamp so a
    # developer can point a dev-built plugin at a local stack without
    # repackaging; the stamp outranks only the production default, which is
    # what makes a dev zip publish to dev without anyone remembering to set
    # anything.
    for candidate in (explicit, os.environ.get(API_BASE_ENV), BUILD_API_BASE):
        text = (candidate or "").strip().rstrip("/")
        if text:
            return text
    return PROD_API_BASE


def loopback_http_allowed() -> bool:
    """Whether the developer has explicitly opted plain-HTTP loopback in.

    An exact-string comparison, not a truthiness test: `0`, `false` and `no`
    are things people write when they mean off, and a variable that treats
    them as on is a foot-gun aimed at the one property this module defends.
    """
    return os.environ.get(ALLOW_HTTP_LOOPBACK_ENV, "") == ALLOW_HTTP_LOOPBACK_VALUE


def is_loopback_http_url(url: str) -> bool:
    """Whether `url` is plain HTTP to an address that cannot leave this machine.

    Parsed, never matched as text. Every interesting attack on a check like
    this is a string that *contains* a loopback name without *being* one -
    `localhost.evil.com`, `127.0.0.1.evil.com`, or `http://localhost@evil.com/`
    where the loopback part is userinfo and the real host is after the `@`. So
    the host is taken from the parser, which discards userinfo, strips the port
    and unwraps IPv6 brackets, and is then compared whole.

    Only the literal name `localhost` and addresses that are themselves
    loopback qualify. A name that merely *resolves* to 127.0.0.1 does not:
    resolution is the attacker's to control via DNS, and re-resolving here
    would not even bind the answer that `urlopen` later gets.
    """
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        # A malformed authority - an unclosed IPv6 bracket, a non-numeric port.
        return False
    if parsed.scheme.lower() != "http":
        return False
    # Credentials in the URL are refused outright rather than parsed past. The
    # dev stack never needs them, and `http://localhost@evil.com/` reads as
    # loopback to a human long after the parser has decided otherwise.
    if parsed.username is not None or parsed.password is not None:
        return False
    try:
        host = parsed.hostname
        # Reading the port is what validates it: `urlsplit` accepts any text
        # after the colon and only complains when asked. An authority this
        # module cannot fully account for is not one it should be vouching for.
        _port = parsed.port
    except ValueError:
        return False
    if not host:
        return False
    # `hostname` is already lower-cased; the trailing dot of a fully qualified
    # name is not, and `localhost.` names the same host as `localhost`.
    host = host.rstrip(".")
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        # Not an address at all, so not one this function can vouch for. That
        # covers `127.0.0.1.evil.com` and every other name-shaped lookalike.
        return False


def require_secure_url(url: str) -> str:
    """The gate for the API base and for the URLs built from it.

    HTTPS always passes. Plain HTTP passes only when both halves of the
    exemption hold - opted in *and* addressed to loopback - and is refused with
    the ordinary message otherwise, so a staging host over HTTP reads exactly
    as it did before this existed.
    """
    if loopback_http_allowed() and is_loopback_http_url(url):
        return url
    return require_https(url)


def require_https(url: str) -> str:
    """The one gate every URL in this module passes through."""
    if not url.lower().startswith("https://"):
        raise HostingError(
            f"Refusing to publish over {url}. Hosting requires HTTPS, because "
            "anything else puts your map and your sign-in token in front of "
            "whoever is on the network between you and the server."
        )
    return url


def insecure_loopback_base(explicit: str | None = None) -> str | None:
    """The plain-HTTP loopback base a publish would use, or `None`.

    Exists so the confirmation the user reads can say that this publish is
    leaving the machine unencrypted. Silent insecure transport is how an
    exemption meant for one afternoon on a laptop ends up switched on in front
    of real users, so the one thing this must never do is stay quiet.

    Returns `None` rather than raising for a base that is simply unacceptable:
    the refusal belongs to `require_secure_url`, at the moment of the request,
    and duplicating it here would mean two places to disagree about it.
    """
    base = _configured_api_base(explicit)
    if loopback_http_allowed() and is_loopback_http_url(base):
        return base
    return None


def urllib_transport(request: HttpRequest) -> HttpResponse:
    """The stdlib transport. Knows nothing about QGIS, so it stays testable."""
    require_secure_url(request.url)
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
        # `require_secure_url` is what stands there now. It admits exactly one
        # thing beyond https - an opted-in plain-HTTP loopback base - and
        # `file://`, `ftp://` and a custom handler are none of them, so what
        # B310 is looking for is still impossible here.
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


def error_envelope(payload: JsonObject) -> JsonObject:
    """The `error` object a refused request carries, or an empty one.

    Every route in the control plane answers a refusal through one helper, and
    it produces `{"error": {"code", "message", "details"}}`. So this is the
    envelope for all of them, not a shape particular to publishing.
    """
    value = payload.get("error")
    return value if isinstance(value, dict) else {}


def error_details(payload: JsonObject) -> JsonObject:
    """The machine-readable half of a refusal: `error.details`.

    **The canonical location, and the only one anything new should read.** The
    alternative considered was spreading these fields at the top level, which
    is what the 409 release conflict did alone. Nesting won for a reason worth
    keeping written down: a flat spread shares a namespace with the envelope,
    so the first detail field somebody names `error` makes the whole refusal
    unparseable. That makes flat the wrong shape rather than merely the older
    one.
    """
    value = error_envelope(payload).get("details")
    return value if isinstance(value, dict) else {}


def server_message(response: HttpResponse) -> str:
    """The server's own explanation of a failure, if it sent one.

    `error.message` first, because that is where the API puts it. The bare
    top-level keys after it are for everything that is not our own API
    answering: a proxy, a gateway, or a load balancer refusing on its way
    through will not use our envelope, and its message is still the most
    useful thing we can show.
    """
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return response.body.decode("utf-8", "replace").strip()[:400]
    if isinstance(payload, dict):
        message = _text(error_envelope(payload), "message")
        if message:
            return message
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


def _optional_number(data: JsonObject, key: str) -> float | None:
    """A size in megabytes, which the server rounds to one decimal place.

    Separate from `_optional_int` rather than folded into it: truncating 10.4
    to 10 in a message that also names a 10 MB limit would produce "this map is
    10 MB and the limit is 10 MB", which reads as a server bug.
    """
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _required_int(data: JsonObject, key: str, what: str) -> int:
    """A whole number from the reply, or a readable failure.

    `releaseN` arrives as a JSON *number*, not a string, so a string-only
    reader silently sees it as absent - which failed every publish on the first
    call with a message blaming the server for omitting a field it had sent.
    Accepts a numeric string too, since the cost of being lenient in one
    direction here is nothing and the cost of being wrong is a dead feature.
    """
    value = data.get(key)
    if isinstance(value, bool):  # bool is an int subclass; never a release
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


def _string_map(value: object) -> dict[str, str]:
    """The server's upload headers, keeping only what is actually a header."""
    if not isinstance(value, dict):
        return {}
    return {str(key): item for key, item in value.items() if isinstance(item, str)}


def _upload_target(entry: JsonObject, digest: str, api_base: str) -> UploadTarget:
    """One `uploads[]` entry, read through its `upload` discriminant.

    The mode is taken from the server's answer and nothing else. Inferring it
    from whether the URL looks relative would work right up until a presigned
    URL changes shape, and the failure would be a bearer token sent to a
    storage host - so an unrecognised mode stops the publish by name instead.
    """
    raw = entry.get("upload")
    if not isinstance(raw, dict):
        raise HostingError(
            f"The hosting server offered {digest[:12]} for upload without "
            "saying how to send it. Nothing was published."
        )
    mode = _text(raw, "mode")
    url = _required_text(raw, "url", "starting the upload")
    if mode == UPLOAD_MODE_PRESIGNED:
        return UploadTarget(
            sha256=digest,
            url=require_https(url),
            mode=UPLOAD_MODE_PRESIGNED,
            headers=_string_map(raw.get("headers")),
        )
    if mode == UPLOAD_MODE_DIRECT:
        # A path, not a URL: the local stack's stand-in for a presigned PUT is
        # a route on the API itself, so it is joined to the base this client is
        # already talking to and passes the same HTTPS gate afterwards. That is
        # the whole of the relaxation - the relative form is not checked for a
        # scheme it cannot have, and the base it lands on still is, so a
        # plain-HTTP deployment is refused here exactly as it is at
        # `resolve_api_base`.
        #
        # The server now names this URL in full rather than relatively - it
        # builds it from its own idea of where it lives, which is one place
        # instead of two - so the join below is a fallback for the older shape
        # and most `direct` targets arrive absolute. That is why the exemption
        # is keyed on the mode the server stated and never on whether the URL
        # looked relative: both modes are absolute now, and "looks relative"
        # would have quietly become "no check at all". A `direct` URL still has
        # to clear the same loopback gate as the base, so a control plane that
        # named some other host - misconfigured, or not ours any more - is
        # refused here whether or not the exemption is switched on.
        joined = url if "://" in url else f"{api_base}/{url.lstrip('/')}"
        return UploadTarget(
            sha256=digest,
            url=require_secure_url(joined),
            mode=UPLOAD_MODE_DIRECT,
            headers=_string_map(raw.get("headers")),
        )
    named = f"'{mode}'" if mode else "no upload mode at all"
    raise HostingError(
        f"The hosting server asked for {digest[:12]} to be uploaded using "
        f"{named}, which this version of the plugin cannot do. Nothing was "
        "published. Update QGIS2WebMap and try again."
    )


def _refusal_payload(response: HttpResponse) -> JsonObject:
    """The refusal body, or an empty object if it was not JSON at all.

    Unlike `decode_json` this never raises. A failure path that could fail
    again while reading the failure would replace the server's explanation with
    a parser complaint, which is the least useful thing we could say.
    """
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _structured_refusal(
    response: HttpResponse, payload: JsonObject
) -> PublishRefusedError | None:
    """The refusal as a typed error, or `None` if the server sent no code.

    Every field comes out of `error.details`, which is where the API puts the
    machine-readable half of every refusal it has. The three this reads are the
    three `start` can answer with besides the conflict, and each one carries
    something the user needs in order to act: the plan's map limit, the size
    cap against the actual size, the instant a rate limit lifts.
    """
    envelope = error_envelope(payload)
    code = _text(envelope, "code")
    if not code:
        return None

    details = error_details(payload)
    message = _text(envelope, "message") or (
        f"The hosting server refused this publish (HTTP {response.status})."
    )
    return PublishRefusedError(
        message,
        code=code,
        limit=_optional_int(details, "limit"),
        limit_mb=_optional_number(details, "limitMb"),
        actual_mb=_optional_number(details, "actualMb"),
        retry_at=_text(details, "retryAt"),
    )


def _raise_for_status(response: HttpResponse, what: str) -> None:
    if 200 <= response.status < 300:
        return

    payload = _refusal_payload(response)
    refusal = _structured_refusal(response, payload)

    if response.status in (401, 403):
        # A 403 is not always about the token: `map_limit_reached` arrives on
        # one, and telling somebody who filled their plan to sign in again
        # sends them to fix a session that was never broken. Only a code we
        # know to be a publishing refusal takes this exit; everything else on
        # these two statuses stays an authentication failure.
        if refusal is not None and refusal.code in NON_AUTH_REFUSAL_CODES:
            raise refusal
        raise AuthRequiredError(
            "Your NIKA sign-in is no longer valid. Sign in again and republish."
        )

    if refusal is not None:
        raise refusal

    detail = server_message(response)
    suffix = f"\n\n{detail}" if detail else ""
    raise HostingError(
        f"The hosting server refused {what} (HTTP {response.status}).{suffix}"
    )


def _is_release_conflict(response: HttpResponse) -> bool:
    """Whether this 409 is the stale-client conflict rather than some other one.

    Decided by `error.code`, because the status does not decide it: the server
    answers 409 for a stale release AND for a republish of a map that has been
    taken down, and only the first is a question worth putting a "Publish
    anyway" button under. The second is a refusal with its own instructions,
    and rendering it as the first told the publisher a colleague had overwritten
    work nobody had touched.

    A 409 carrying NO code is treated as the conflict, which is the behaviour
    this had before there was a code to read. That is the conservative way round
    for a client that may be talking to an older server: the conflict is the
    only 409 such a server sends, and its dialog can be cancelled, whereas
    misreading a genuine conflict as a flat refusal would drop the one screen
    that lets a publisher rescue their work with `force`.
    """
    try:
        payload = json.loads(response.body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return True
    if not isinstance(payload, dict):
        return True
    code = _text(error_envelope(payload), "code")
    return code in ("", REFUSAL_RELEASE_CONFLICT)


def _raise_conflict(response: HttpResponse) -> None:
    """Turn a 409 at `start` into the typed question the dialog has to ask.

    **`error.details` is canonical; the top level is a deprecated duplicate.**
    The server emits both today: this refusal was the one endpoint that spread
    its fields at the top level, and moving it alone would have blank-screened
    every plugin already in the field - a client reading the old place would
    see release 0 and no attribution, and would tell a publisher a colleague
    had overwritten their work when nobody had. That is the refusal hardest to
    reproduce and most alarming to get wrong, so the duplicate stays until the
    oldest plugin in the field reads the nested form.

    Reading the canonical location first is what starts that clock. The merge
    below - and this half of the docstring - goes when the duplicate does.
    """
    data = decode_json(response, "starting the upload")
    # Canonical wins on every key it carries; the flat copy answers only for
    # keys `error.details` does not have, which after the duplicate is removed
    # will be none of them.
    fields: JsonObject = {**data, **error_details(data)}
    current = _optional_int(fields, "currentRelease") or 0
    published_by = _text(fields, "publishedBy")
    who = f" by {published_by}" if published_by else ""
    published_at = _text(fields, "publishedAt")
    when = f" on {published_at}" if published_at else ""
    was_rollback = fields.get("wasRollback") is True
    if was_rollback:
        summary = (
            f"This map was rolled back to release {current}"
            f"{when}. Publishing now would move it forward again."
        )
    else:
        summary = (
            f"This map is already at release {current}, published{who}{when}. "
            "Your copy is older."
        )
    raise PublishConflictError(
        f"{summary}\n\nNothing was published.",
        current_release=current,
        published_by=published_by,
        published_at=published_at,
        was_rollback=was_rollback,
        rolled_back_to=_optional_int(fields, "rolledBackTo"),
    )


class HostingClient:
    """The publish handshake against one API base, for one signed-in user."""

    def __init__(
        self,
        token: str,
        api_base: str | None = None,
        transport: Transport | None = None,
        sleeper: Sleeper | None = None,
    ) -> None:
        self.token = token
        self.api_base = resolve_api_base(api_base)
        self._transport: Transport = transport or urllib_transport
        self._sleep: Sleeper = sleeper or time.sleep

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _post_raw(self, path: str, payload: JsonObject) -> HttpResponse:
        url = require_secure_url(f"{self.api_base}{path}")
        return self._transport(
            HttpRequest(
                method="POST",
                url=url,
                body=json.dumps(payload).encode("utf-8"),
                headers=self._headers(),
            )
        )

    def _post(self, path: str, payload: JsonObject, what: str) -> JsonObject:
        response = self._post_raw(path, payload)
        _raise_for_status(response, what)
        return decode_json(response, what)

    def _get(self, path: str, what: str) -> JsonObject:
        url = require_secure_url(f"{self.api_base}{path}")
        response = self._transport(
            HttpRequest(method="GET", url=url, headers=self._headers())
        )
        _raise_for_status(response, what)
        return decode_json(response, what)

    def start_publish(
        self,
        manifest: PublishManifest,
        map_id: str | None = None,
        release_n: int | None = None,
        force: bool = False,
    ) -> PublishStart:
        """State the manifest and collect presigned URLs for what is missing.

        The manifest is sent as it was built. Nothing here edits it: the digests
        in it are what the server verifies against after the upload, so a client
        that "corrected" a field would be promising bytes nobody has.
        """
        if not manifest.get("files"):
            raise HostingError("There is nothing to publish.")

        payload: JsonObject = {"manifest": manifest}
        # Omitted rather than sent as null on a first publish: `mapId` present
        # is what tells the server this is a republication of an existing map.
        if map_id:
            payload["mapId"] = map_id
        # The release this plugin believes is current. Sending it is what makes
        # a stale client a 409 rather than a silent overwrite of a colleague's
        # work, so it is omitted only when there is no earlier release at all.
        if release_n is not None:
            payload["releaseN"] = release_n
        # Only ever set by an explicit answer to the conflict question. Never a
        # default, never a retry this module decides on by itself.
        if force:
            payload["force"] = True

        response = self._post_raw("/maps/publish/start", payload)
        if response.status == 409 and _is_release_conflict(response):
            _raise_conflict(response)
        _raise_for_status(response, "starting the upload")
        data = decode_json(response, "starting the upload")

        targets: list[UploadTarget] = []
        raw_targets = data.get("uploads")
        if isinstance(raw_targets, list):
            for entry in raw_targets:
                if not isinstance(entry, dict):
                    continue
                digest = _text(entry, "sha256")
                if digest:
                    targets.append(_upload_target(entry, digest, self.api_base))

        return PublishStart(
            release_id=_required_text(data, "releaseId", "starting the upload"),
            map_id=_required_text(data, "mapId", "starting the upload"),
            release_n=_required_int(data, "releaseN", "starting the upload"),
            uploads=tuple(targets),
            license_key=_optional_text(data, "licenseKey"),
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

        The one exception is a `direct` target, which is a route on our own API
        rather than a storage host: there the bearer token is what authorizes
        the write, and withholding it would make every local-stack upload a
        401. The condition is the mode the server stated, so the token can
        never follow a presigned URL off to a third party.

        The scheme gate is picked by the same discriminant, and deliberately
        not by the loopback rule. A `presigned` URL is the server's, absolute,
        and points at a storage host; one arriving as plain HTTP is a
        misconfigured server or someone rewriting the reply, and neither is
        made acceptable by the developer having a local stack running. Only a
        `direct` target - which by construction is a path joined onto the base
        this client is already talking to - can be loopback HTTP.
        """
        if target.mode == UPLOAD_MODE_PRESIGNED:
            require_https(target.url)
        else:
            require_secure_url(target.url)
        # The server's headers win: they are what the signature was computed
        # over, so a Content-Type of ours in place of theirs is a 403 with no
        # explanation. Ours fills in only where the server named nothing.
        headers: dict[str, str] = {
            "Content-Type": content_type,
            "Content-Length": str(len(payload)),
        }
        if target.is_authenticated:
            headers["Authorization"] = f"Bearer {self.token}"
        headers.update(dict(target.headers))
        last = ""
        for _attempt in range(UPLOAD_ATTEMPTS):
            try:
                response = self._transport(
                    HttpRequest(
                        method="PUT",
                        url=target.url,
                        body=payload,
                        headers=headers,
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
            f"Uploading {target.sha256[:12]} failed after {UPLOAD_ATTEMPTS} "
            f"attempts.\n\n{last}\n\nNothing was published; the map you have on "
            "disk is unchanged. Check your connection and try again."
        )

    def complete(self, release_id: str) -> str:
        """Tell the server every promised byte is now in place.

        Carries the release id and nothing else. The manifest was stated at
        `start` and is what the server verifies against; a second copy here
        could only ever disagree with the first.
        """
        data = self._post(
            "/maps/publish/complete", {"releaseId": release_id}, "publishing the map"
        )
        return _text(data, "releaseId") or release_id

    def release_status(self, release_id: str) -> ReleaseStatus:
        """One look at where the release has got to."""
        what = "checking the release"
        data = self._get(f"/maps/releases/{release_id}", what)
        return ReleaseStatus(
            state=_required_text(data, "state", what),
            map_id=_text(data, "mapId"),
            release_n=_optional_int(data, "releaseN") or 0,
            public_url=_text(data, "publicUrl"),
            error=_optional_text(data, "error"),
        )

    def await_release(
        self,
        release_id: str,
        on_progress: Callable[[ReleaseStatus], None] | None = None,
    ) -> PublishResult:
        """Poll until the release is live, or say why it will never be.

        The ceiling exists because a release stuck in `verifying` is a server
        problem, and a plugin that waits on it forever is a QGIS window the
        user has to kill.
        """
        deadline = time.monotonic() + POLL_TIMEOUT_SECONDS
        status = self.release_status(release_id)
        while True:
            if on_progress is not None:
                on_progress(status)
            if status.state == STATE_LIVE:
                if not status.public_url:
                    raise HostingError(
                        "The hosting server reported the map as live but sent "
                        "no address for it. Check your maps at nika.eco."
                    )
                return PublishResult(
                    map_id=status.map_id,
                    public_url=status.public_url,
                    release_n=status.release_n,
                )
            if status.state == STATE_FAILED:
                # Verbatim, and never replaced by a friendlier sentence: it
                # names the file or digest that did not verify.
                detail = f"\n\n{status.error}" if status.error else ""
                raise HostingError(
                    f"The hosting server could not publish this map.{detail}"
                )
            if time.monotonic() >= deadline:
                raise HostingError(
                    "The hosting server is still verifying this map after "
                    f"{int(POLL_TIMEOUT_SECONDS)} seconds. It may still come "
                    "up; check your maps at nika.eco before republishing."
                )
            self._sleep(POLL_INTERVAL_SECONDS)
            status = self.release_status(release_id)
