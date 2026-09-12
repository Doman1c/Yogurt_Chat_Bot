# Yogurt Apple client — S3-A

A SwiftUI client for macOS 13+, iOS 16+ Simulator and iPadOS 16+ Simulator.
The existing Python Native Gateway remains authoritative. This directory contains
no Ollama/Memory/SQLite client or identity-selection API.

## Build on a Mac

Install Xcode (with iOS Simulator runtime) and XcodeGen (`brew install xcodegen`).
From this directory:

```sh
swift test
xcodegen generate
open YogurtNative.xcodeproj
```

Choose `YogurtMac` and My Mac, or `YogurtSimulator` and an iPhone/iPad simulator.
Signing is disabled for these development targets. No physical-device target is
supported; the networking implementation also rejects physical iOS devices.
The server must run on the Mac hosting the simulator at `127.0.0.1:17622`.
Run the existing Gateway with its server-side configuration, then save its bearer
token using the client's Settings > Gateway credential > Save to Keychain.
No credential is shipped with the app. PhotosPicker needs no full photo-library
permission. All custom images stay in the client's Application Support directory.

The optional actual URLSession integration test uses a loopback fixture:

```sh
python3 Tests/Fixtures/gateway.py &
YOGURT_RUN_LOOPBACK_TESTS=1 swift test
```

The fixture has a deliberately fake test credential, no Core or provider connection,
and is never part of the application. Stop the fixture before running the real Gateway.
GitHub's separate Apple Client workflow runs Swift tests, builds both app targets,
and launches iPhone/iPad simulators. The existing Python version matrix is unchanged.

## Protocol and recovery

Intentional sends create one client_message_id and a frozen JSON body persisted
before transmission. Automatic transport/503 retries and the Retry same send button
reuse both. Conflict and terminated-before-response are not automatically retried.
Dismiss pending send affects only the local projection and explicitly warns that an
unknown send may already have reached Core. Display names/assets never enter requests.

202 acceptance creates a user row and assistant placeholder. Bounded SSE framing
preserves Unicode and whitespace exactly; duplicate sequences are ignored, while
gaps and malformed events fail closed without advancing the cursor. Each text/cursor
pair is atomically cached. Reconnect sends Last-Event-ID. Closing a consumer never
calls a cancellation API. Finite automatic retries leave explicit reconnect/reload
controls available rather than generating another message.

On launch the cached conversation list is reconciled with the server; selecting a
conversation loads authoritative history. Terminal history replaces local text.
For active responses, history has no event sequence, so the client discards its
assembled prefix and replays from zero into a fresh accumulator. It never concatenates
a history prefix with the same replayed prefix. Unknown pending sends retry their
original body after launch. User/Persona/Memory identity remains entirely server-side.

## Appearance and assets

Settings provides display names, independent avatars, background image/focal point/
zoom/blur/dim, bubble style/opacity/corners, font size and system/light/dark theme.
Changes update the chat and preview immediately. No custom asset is required.

ImageIO normalizes EXIF orientation when decoding, while original source bytes remain
unchanged. Avatars use an interactive square crop and a 512×512 JPEG derivative;
SwiftUI applies the circular display mask. Backgrounds retain the original and
normalized 0…1 focal coordinates plus zoom, and use a GeometryReader aspect-fill
frame for every viewport. Drag, zoom slider/pinch, ratio previews, reset and confirm
are available. Re-crop operates from the original, never the derivative.

Files live in Application Support/Yogurt/AppearanceAssets/{originals,avatars}.
Only references/preferences are serialized; image bytes never enter UserDefaults,
network bodies, model context or Memory. Writes are atomic; names are validated and
symlinks cannot escape the managed root. Missing/corrupt images use neutral defaults.
Imports are limited to 32 MB and 100 MP, originals to 200 MB total, decoded images to
4096 pixels per dimension, and decoded cache to 64 MB/16 entries. Originals are kept
when changing/resetting appearance; no original is silently deleted. No sync is added;
local support data is excluded from device backup.

Tokens are Keychain generic-password entries with ThisDeviceOnly accessibility and
synchronization disabled. Networking is fixed to the development loopback endpoint,
uses ephemeral URLSession without cookies/cache/proxies, and refuses redirects.
Errors expose only sanitized categories/known server codes. ATS is not globally
disabled: generated Info.plists use only NSAllowsLocalNetworking. See Apple's
[local networking ATS documentation](https://developer.apple.com/documentation/bundleresources/information-property-list/nsapptransportsecurity/nsallowslocalnetworking).

Physical iPhone networking, LAN/public exposure, Swift UI polish, Voice, Vision,
Memory UI, and R3-D/R3-E are outside this foundation.
