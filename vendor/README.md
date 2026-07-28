# vendor/

Place the SDRplay API Linux installer here before building the image.

1. Download the Linux SDRplay API installer for your RSP2pro from SDRplay's
   official downloads page (requires accepting SDRplay's EULA).
2. Save it in this directory as `sdrplay_api.run`.
3. Do not commit this file — it's proprietary and ignored by Git.

The Dockerfile fails the build with a clear error if this file is missing.
