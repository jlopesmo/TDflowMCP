# Spike S5: compatibility of TouchDesigner 2025.32440 (older build)

**Date:** 2026-11-22 · **Status:** Executed. Evidence below; corrected after [independent review 3](2026-11-22-s5-review.md).
**Superseding note:** the conclusion of §2 applies to the **Web Server DAT transport only**. After owner decision D10, spike [S6](2026-11-22-s6-embedded-python-transport.md) showed that an embedded Python transport meets the security requirements on this same build, so 2025.32440 is supported.
**Build under test:** the installed **TouchDesigner 2025.32440**, which predates the provisional 2025.33230+ target.
**Spike code:** [spikes/s5/](../../spikes/s5/README.md). It is disposable and is not TDflowMCP source. The committed copy was **corrected after run 1** (see §3); run 1 used the earlier version, and the full mode was not re-run afterwards.

**Method:**
- A disposable `s5.toe` was generated from text with Derivative's `toecollapse` under `.tmp/s5/build/` (git-ignored). It contains only one boot Execute DAT.
- TouchDesigner was launched with that file only. An external harness probed from outside the process and, on timeout, killed the process tree it had started (`taskkill /T`).
- No existing project was opened or modified. Nothing was installed or upgraded. No system setting was changed.
- Raw result JSON stays in `.tmp/s5/results_run1/` (full run) and `.tmp/s5/results/` (params-only run), because it contains host network details. This record summarizes it without addresses.

**Runs:**
1. **Full run:** TD exited by itself with code 0, about 20 s after the harness started (harness start → results written ≈ 20.5 s).
2. **Params-only run, with no Web Server DAT:** code 0, about 10 s.

Two earlier attempts never executed the boot DAT, because the hand-authored DAT text lacked the storage header (§4). The harness killed those processes, and its console summaries reported readiness timeouts of 150 s and 90 s. Their result files were overwritten by later runs, so this is session-log evidence only. Both used the same disposable file.

Result scale: **pass** · **warn** · **fail** · **unknown** (not observed).

## 1. Results

| Check | Result | Evidence |
|---|---|---|
| Runtime build | **pass** (identified) | `app.build` = `2025.32440`, `app.version` = `099`, `app.product` = `TouchDesigner`. TD reports `osName` Windows / `osVersion` 10; the host is Windows 11 |
| Build vs. provisional target 2025.33230+ | **warn** | Older than the target |
| Embedded Python | **pass** | `3.11.10 (heads/3.11-Derivative-dirty …)` |
| Required libraries | **pass** | `sqlite3` (SQLite 3.45.1), `ssl` (OpenSSL 3.0.15), `hmac`, `hashlib`, `secrets`, `json`, `asyncio`, `unittest`, `ctypes`, `zipfile`, `urllib.request`, `threading`, `socket`, `webbrowser` all import. **`jsonschema` 4.23.0** imports, and a validation smoke test passed |
| Heavy SDK dependencies | **pass** (absent, as ADR-0003 expects) | `pydantic`, `mcp`, `starlette`, `uvicorn` are not importable |
| Custom parameter APIs | **pass** | `appendCustomPage`, `appendStr/Pulse/Toggle/Int/Menu` all work. `readOnly`, `enable`, `enableExpr`, `help`, `startSection`, `default`, `clampMin`, `normMin` and `pulse()` all exist. A `readOnly` Str can still be set by script |
| Menu default | **warn** (API nuance) | Setting `.default` after creation does not change the current value (`default=info`, `val=error`). Builders must set `.val` |
| Pulse callback (Parameter Execute DAT `onPulse`) | **pass**, with a caveat | `par.pulse()` from script on the **same frame** the DAT is created is **not** delivered. A pulse on a later frame fires `onPulse` (params-only run). `onValueChange` fires |
| Project pre-save hook | **warn**: exists, firing unknown | The Execute DAT has `projectpresave`/`projectpostsave` parameters. Saving was deliberately not exercised |
| `onStart` on project load | **pass** (fires); **unknown** as a reliable boot hook | `onStart` wrote its marker file with launcher environment variables visible. The boot itself completed from the `onFrameStart` fallback; why the `onStart` path did not complete is unresolved (suspect: the `me.fetch(..., storeDefault=…)` call) |
| Clipboard | **pass** (text) | The `ui.clipboard` text round-trip succeeded and the previous **text** content was restored (never recorded). Non-text clipboard formats would not be preserved; the corrected spike restores in `finally` |
| Browser launching | **unknown** | `ui.viewFile` exists. An end-to-end launch was **not exercised**: no verified loopback listener can exist on this build (§2), and a `file://` launch was not tried. Move to S1 |
| Web Server DAT callback thread | **pass** | `onHTTPRequest`, `onServerStart` and `onServerStop` all ran on `MainThread` |
| Web Server DAT parameters | **fail** (security-critical) | Parameters: `active, restart, port, callbacks, secure, privatekey, certificate, password, verifyclient, minprotocol` (+ common). **There is no `localaddress` parameter** |
| **Effective binding** | **fail** | With the DAT active on 9480, the OS listener table showed **`0.0.0.0:9480`** for this TD process (all IPv4 interfaces; no IPv6 listener), and a TCP connection to the machine's non-loopback IPv4 address **connected** |
| **Bind-verification gate** (security-model §8.2) | **pass** | Every request before the verdict returned `503 TDFLOW_VERIFYING`: 1 request was served in that state, and **no 200 at any point**. `onServerStart` came about 3.07 s after `active=True`; the verdict `fail` came about 0.48 s after `onServerStart`. The DAT was deactivated **in the same callback**, `onServerStop` followed about 0.06 s later, and no listener remained when the process quit |
| Close timing | **warn** | Right after `active=False`, in the same callback, the OS table still listed the socket. The close is asynchronous, so it must be confirmed on a later frame (now modelled as `CLOSING → CLOSED`, with escalation if the socket stays) |
| Same-frame verification | **unknown** (as expected) | In the same frame as `active=True` no socket existed yet (`ambiguous`). It was absent at 2.02 s and present at 3.55 s; `onServerStart` (≈3.07 s) marks the bind |
| Deferred HTTP response / SSE | **warn** (inference) | No send/deferral method appears in the DAT's attribute listing (`dir()`); this was not tested by calling anything. WebSocket push methods exist (`webSocketSendText/Binary/Ping/Pong`, `webSocketConnections`) |
| Port conflict (two DATs, same port) | **unknown** | Not validly tested; see §3 |
| Request/response headers, status/header setting, body limits, latency | **unknown** | Not observable without a verified listener. **Deferred to S1** on 2025.33230+ |
| Non-loopback probe as evidence | **warn** | With nothing listening, the probe **timed out** (not refused). While listening on `0.0.0.0` it **connected**. A timeout is not proof of loopback-only, so the OS socket table is the primary evidence |
| OS listener-table reader (`GetExtendedTcpTable` via `ctypes`) | **pass** | Before the TD runs, the reader was cross-checked in this session against `netstat` for an existing system listener on port 135 (`0.0.0.0` / `::`), and against a test loopback listener (`127.0.0.1`). That output is session-log evidence, not stored in the result files. It is now covered by unit tests (`tests/unit/test_oslisteners.py`) |

## 2. Can 2025.32440 satisfy TDflowMCP's security requirements?

**No.** The Web Server DAT on this build cannot be bound to loopback (there is no `localaddress`) and listens on all interfaces. The gate did its job: it never served application data and closed the listener about 0.5 s after it came up. But any listener on this build exposes a TCP port on every interface for that window. A read-only `netsh` query also found a pre-existing Windows Firewall rule for this TouchDesigner build that allows inbound traffic on all profiles, so the exposure was reachable from the network.

**Consequences (no security relaxation):**
- On builds whose Web Server DAT lacks `localaddress`, TDflowMCP **never activates the listener** (step 0 of the bind-verification gate, error `BIND_UNSUPPORTED`). MCP and the web Setup Assistant are unavailable on those builds, and guidance is given only through parameters and an internal DAT.
- The runtime gate (§8.2, steps 1–5) stays mandatory on every build, including 2025.33230+.
- At the time, the `td.build` check reported `warn` and **`net.capability`** failed with remediation "install TouchDesigner 2025.33230 or newer". **Superseded (D10, S6):** `td.build` now passes on 2025.32440, `net.capability` passes for the default embedded transport, and it fails only if the Web Server DAT transport is selected. `net.bind` stays `unknown`, because no listener is ever started.

## 3. Deviation recorded: unsafe step in run 1

After the gate had correctly closed, the run-1 script still ran its port-conflict probe. It created a second Web Server DAT on the same port, with no callbacks DAT and no gate, and the OS table showed it listening on `0.0.0.0`. It stayed active for a scheduled 30 frames; there are no timestamps. **No requests were sent to it**, and its default response behaviour without callbacks is unknown. This was a test-design error: the probe must only ever run after `SERVING`. The committed script skips it whenever the gate closes, and run 2 had no Web Server DAT at all. Port-conflict behaviour remains **unknown** and moves to S1.

## 4. Side findings useful for S2/S3

- **A `.toe` can be generated from text without a GUI:** `toecollapse` on a directory plus `.toc`. DAT text in the expanded format is stored as `b"2\n*"`, then six big-endian int32 values `(1, 1, 1, 1, 2, len)`, then UTF-8 bytes. This was inferred from Derivative's expanded sample and the bridge `.tox`, and confirmed when TD executed the generated DAT.
- **TouchDesigner can run unattended.** The launcher passes environment variables, Execute DAT callbacks run the boot, `td.run(..., delayFrames=n)` sequences phases, and `project.quit(force=True)` exits with code 0.
- The external harness pattern works: results go to JSON files, and on timeout the harness kills only the process tree it started.

## 5. Side effects on the host
- TouchDesigner was launched four times, and two of those processes were killed by the harness. No crash or backup files were found under the disposable folder.
- No browser was launched.
- The clipboard **text** was restored.
- No system settings were changed; the firewall rule was only read.
- TouchDesigner may update its own recent-files preference when it opens a file. That is TD's normal behaviour and was not inspected.
