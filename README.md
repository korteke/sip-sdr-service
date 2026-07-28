# SIP SDR Service

Original version by Jouni / OH3CUF: <https://codeberg.org/jii/sip-kiwisdr-3699>

This standalone Docker project registers a dedicated SIP number, answers
incoming calls, and plays live audio from a local USB SDR — SDRplay
RSP2pro, RTL-SDR, or PlutoSDR, selected via `SDR_DRIVER`. The frequency
and sideband are fully configurable — this project is not tied to any
single band or to one specific radio. Its default host SIP port is 5062
and its RTP range is 11000-11100.

## How it works

`scripts/sdr_stream.py` opens the selected SDR via SoapySDR, tunes it, and
demodulates LSB or USB directly from the raw IQ samples using a small
NumPy/SciPy-based single-sideband demodulator (`scripts/sdr_demod.py`).
Hardware-specific details (antenna selection, gain control, sample-rate
strategy) live in small per-backend adapter modules under
`scripts/sdr_backends/` (`sdrplay.py`, `rtlsdr.py`, `plutosdr.py`) — the
demodulator and the pacing/reconnect logic around it are identical
regardless of which backend is active. A Python supervisor paces the
resulting 8kHz PCM for Asterisk, limits buffering to preserve live
latency, emits silence during interruptions, and reconnects automatically
on device errors. Asterisk shares one receiver stream among simultaneous
callers.

The selected SDR must be physically connected to the same Linux host that
runs this Docker container (via USB for SDRplay/RTL-SDR, or via USB or
network for PlutoSDR — see the PlutoSDR note below).

## Listen-only group behavior

The number supports multiple simultaneous listeners. Everyone hears the
same shared live SDR stream. Calls are deliberately not placed in a voice
conference or bridged to each other, and Asterisk explicitly applies
`MUTEAUDIO(in)=on` to every answered channel. A caller's microphone audio
therefore cannot reach the other listeners.

Active calls are assigned to the `sdr-service@sdr-listeners` group.
Inspect the current listener count with:

```bash
make listeners
```

`CALL_START` and `CALL_END` records include `listener_mode=listen_only` and
listener-count information.

## Choosing and setting up a backend

Set `SDR_DRIVER` in `.env` to `sdrplay`, `rtlsdr`, or `plutosdr`. Only the
env vars prefixed for your chosen driver (`SDRPLAY_*`/`RTLSDR_*`/
`PLUTOSDR_*`) are actually read — the others are ignored.

### SDRplay RSP2pro

SDRplay's Linux API is proprietary and gated behind their EULA, so it
can't be fetched automatically by the Dockerfile:

1. Download the Linux SDRplay API installer for the RSP2pro from SDRplay's
   official downloads page (accepting their EULA).
2. Save it as `vendor/sdrplay_api.run`.
3. Find the RSP2pro's USB vendor/product ID with `lsusb`, then install a
   udev rule that creates a stable symlink:

   ```
   # /etc/udev/rules.d/70-sdr-radio.rules
   SUBSYSTEM=="usb", ATTR{idVendor}=="1df7", ATTR{idProduct}=="3010", MODE="0660", GROUP="plugdev", SYMLINK+="sdr-radio"
   ```

   (Confirm the actual vendor/product ID for your unit with `lsusb`.)
   Reload udev rules with `udevadm control --reload && udevadm trigger`.

### RTL-SDR

On Linux, the kernel's built-in `dvb_usb_rtl28xxu` driver commonly
auto-claims RTL-SDR dongles as a DVB-T TV tuner before userspace
(SoapySDR) can access them — this is the single most common RTL-SDR
failure on Linux/Docker hosts. The standard fix is blacklisting that
kernel module, e.g. by adding `blacklist dvb_usb_rtl28xxu` to a file
under `/etc/modprobe.d/` (such as `/etc/modprobe.d/blacklist-rtlsdr.conf`),
then re-plugging the device.

No proprietary installer needed — the `soapysdr0.8-module-rtlsdr` package
handles it. Find your dongle's USB vendor/product ID with `lsusb` and set
up a udev rule the same way as above (adjust the vendor/product ID and,
if you like, the rule filename).

### PlutoSDR

No proprietary installer needed — the `soapysdr0.8-module-plutosdr`
package handles it. PlutoSDR commonly connects either as a USB device
(needing the same udev/`SDR_DEVICE` treatment as above) or over its
default network interface (`ip:192.168.2.1`, no USB device node at all).
If you're using the network connection, the `devices:` entry in
`compose.yaml` doesn't apply to your setup — this is confirmed during
PlutoSDR bring-up.

In all cases, set `SDR_DEVICE` in `.env` if your symlink path differs from
the default `/dev/sdr-radio`.

### Container privileges

The container's entrypoint runs as root (no `USER` directive in the
Dockerfile) because starting `sdrplay_apiService` (when `SDR_DRIVER=sdrplay`)
and accessing USB device nodes need root-level access. Asterisk itself
still drops privilege via `runuser`/`rungroup = asterisk` in
`config/asterisk.conf`, so SIP signaling, RTP media, and the `sdr-stream`
demodulator process it spawns all run unprivileged.

If `SDR_CONNECT` in the logs is immediately followed by a device-open
permissions error, check the udev rule's `MODE`/`GROUP` settings: it's the
unprivileged `asterisk` user that actually opens the device (since Asterisk
drops privilege via `runuser`/`rungroup=asterisk` before spawning the
`sdr-stream` process), so that user's group membership needs to match the
`GROUP` the rule assigns to the device node. This applies to all three
backends, not just SDRplay.

## Configure

```bash
cp .env.example .env
```

Edit `.env` and supply:

```dotenv
SIP_SERVER=YOUR_SIP_SERVER
SIP_NUMBER=YOUR_SIP_NUMBER
SIP_AUTH_NAME=YOUR_AUTH_USERNAME
SIP_PASSWORD=YOUR_PASSWORD
SDR_DRIVER=rtlsdr
SDR_FREQUENCY_KHZ=3699
SDR_MODE=lsb
```

`SDR_MODE` can also be `auto`, which resolves to LSB below 10,000 kHz and
USB at/above 10,000 kHz (standard ham convention) — useful since this
project isn't tied to one band. `SDR_LOW_CUT_HZ`/`SDR_HIGH_CUT_HZ`
describe the passband edge magnitudes; the resolved sideband determines
the sign automatically, so you don't need to flip them by hand when
retuning across the 10MHz boundary.

The project is SIP-provider independent. Set `SIP_SERVER`, the account
fields, ports, and any external address to values supplied by your own
provider and network administrator.

Keep `.env` private. It is ignored by Git.

## Run and verify

```bash
make test
make up
make status
make logs
```

After startup, `pjsip show registrations` should report `Registered`. The
custom receiver source may start immediately when Asterisk loads the
Music-on-Hold class. Its logs include:

```text
SDR_CONNECT ...
SDR_AUDIO_ACTIVE
SDR_DISCONNECTED reason=... retry_seconds=...
```

Test receiver access independently (20-second default timeout, overridable
via `SDR_TEST_TIMEOUT_SECONDS`):

```bash
make test-stream
```

This command succeeds only after receiving at least one complete PCM audio
frame and prints `RECEIVER_TEST_OK`. It opens a temporary additional device
stream and closes it immediately after the test. SDRplay's API service can
support this second, simultaneous stream open while the main service is
already running. RTL-SDR and PlutoSDR typically claim the USB device
exclusively, so for those two backends, stop the main service first
(`make down`) before running `make test-stream`, or the open will fail
with a busy/in-use error.

Every call produces matching records containing caller information and
duration:

```text
CALL_START service=rtlsdr id=...
CALL_END service=rtlsdr id=... total_seconds=... answered_seconds=...
```

Show only call records with `make calls`.

These records include the caller's telephone number and, when supplied by
the SIP provider, the caller's name. Treat the logs as personal data:
restrict access, retain them only as long as needed, and follow the privacy
requirements that apply in your jurisdiction.

If `SDR_MODE=auto`, `CALL_START`/`CALL_END` log lines show `mode=auto`
rather than the resolved sideband — check the container logs' `SDR_CONNECT`
line for the actual LSB/USB choice made at startup.

## Failure behavior

- A device error does not terminate active calls. Callers hear silence
  while the supervisor reconnects.
- Audio older than `SDR_STALE_SECONDS` causes a reconnect (device
  close/reopen).
- Reconnect delay grows from `SDR_RETRY_INITIAL_SECONDS` to
  `SDR_RETRY_MAX_SECONDS`.
- A jitter buffer absorbs small timing variations. If it reaches
  `SDR_BUFFER_MAX_MS`, one resynchronization returns it to
  `SDR_BUFFER_TARGET_MS`; this avoids repeated tiny sample drops.
- All callers share one receiver channel and hear the same point in the
  live stream.
- Caller microphone audio is muted inbound and callers are never bridged
  together.

`Spawn extension ... exited non-zero` immediately after `Stopped music on
hold` is Asterisk's normal hangup path: `MusicOnHold()` returns `-1` when
the caller disconnects. It does not indicate a failed call.

## Networking

If signaling works but audio does not, set `EXTERNAL_ADDRESS` to the Docker
host's public IP or hostname and forward these defaults to the Docker host:

- UDP/TCP 5062 for SIP
- UDP 11000-11100 for RTP

Only change `RTP_START` and `RTP_END` together. Docker Compose publishes
the same range that Asterisk advertises.

## Responsible operation

Use a dedicated SIP account. Stop the project with `make down` when it
should not answer calls.
