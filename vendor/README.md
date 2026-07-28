# vendor/

Place the SDRplay API Linux installer here if you intend to use
`SDR_DRIVER=sdrplay` at runtime.

1. Download the Linux SDRplay API installer for your RSP2pro from SDRplay's
   official downloads page (requires accepting SDRplay's EULA).
2. Save it in this directory as `sdrplay_api.run`.
3. Do not commit this file — it's proprietary and ignored by Git.

This file is optional: the Dockerfile builds successfully with or without
it, since RTL-SDR and PlutoSDR setups don't need it. If it's absent and you
later set `SDR_DRIVER=sdrplay` at runtime, `docker-entrypoint.sh` fails fast
with a clear error (SDRplay API service not ready) instead of hanging.
