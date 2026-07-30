# Resolve SDR_CALLER_TUNING (and, when it's "on", SDR_MODE) into
# SDR_ENTRY_CONTEXT -- the dialplan context [incoming-sdr] hands inbound
# calls off to (see config/extensions.conf.template).
#
# This is deliberately factored out of docker-entrypoint.sh into its own
# sourceable file (not executed standalone -- it exports into its caller's
# environment) so this one decision can be exercised by
# tests/test_entrypoint.py without booting the rest of container startup
# (runuser, envsubst, the SDRplay API service, Asterisk itself), none of
# which are available/meaningful outside the built image.
#
# Requires nothing pre-set: SDR_CALLER_TUNING and SDR_MODE both default
# here the same way docker-entrypoint.sh's own earlier `export
# SDR_MODE="${SDR_MODE:-lsb}"` does, so sourcing this file with a clean
# environment resolves exactly like a fresh container would.
#
# Exits 64 (matching this project's other startup-validation failures) on
# an invalid SDR_CALLER_TUNING value, or on SDR_CALLER_TUNING=on paired
# with a non-SSB SDR_MODE -- this feature is SSB-only (lsb/usb/auto), per
# docs/superpowers/plans/2026-07-30-caller-tuning.md's Global Constraints;
# nothing else in the dialplan/entrypoint enforced that until now, so
# SDR_CALLER_TUNING=on with e.g. SDR_MODE=wfm used to leave the IVR fully
# reachable but silently non-functional (the control file gets written but
# sdr_stream.py never reads it outside SSB_MODES).

export SDR_CALLER_TUNING="${SDR_CALLER_TUNING:-off}"
case "$SDR_CALLER_TUNING" in
    off|on) ;;
    *) echo "SDR_CALLER_TUNING must be off or on" >&2; exit 64 ;;
esac

if [ "$SDR_CALLER_TUNING" = "on" ]; then
    case "${SDR_MODE:-lsb}" in
        lsb|usb|auto) ;;
        *) echo "SDR_CALLER_TUNING=on requires SDR_MODE=lsb, usb, or auto (SSB only)" >&2; exit 64 ;;
    esac
    export SDR_ENTRY_CONTEXT="tune-menu"
else
    export SDR_ENTRY_CONTEXT="play-sdr"
fi
