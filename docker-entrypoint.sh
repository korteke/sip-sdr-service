#!/bin/sh
set -eu

required_vars="SIP_SERVER SIP_NUMBER SIP_AUTH_NAME SIP_PASSWORD"
missing=""

for var_name in $required_vars; do
    eval "var_value=\${$var_name:-}"
    if [ -z "$var_value" ]; then
        missing="$missing $var_name"
    fi
done

if [ -n "$missing" ]; then
    echo "Missing required environment variables:$missing" >&2
    exit 64
fi

export SIP_PORT="${SIP_PORT:-5060}"
export HOST_SIP_PORT="${HOST_SIP_PORT:-5062}"
export RTP_START="${RTP_START:-11000}"
export RTP_END="${RTP_END:-11100}"

for var_name in SIP_PORT HOST_SIP_PORT RTP_START RTP_END; do
    eval "var_value=\${$var_name:-}"
    case "$var_value" in
        *[!0-9]*|'') echo "$var_name must be numeric" >&2; exit 64 ;;
    esac
done

case "${SIP_TRANSPORT:-udp}" in
    udp|tcp) ;;
    *) echo "SIP_TRANSPORT must be udp or tcp" >&2; exit 64 ;;
esac

if [ "$RTP_START" -gt "$RTP_END" ]; then
    echo "RTP_START must not be greater than RTP_END" >&2
    exit 64
fi

export SIP_TRANSPORT="${SIP_TRANSPORT:-udp}"
export SIP_CODEC_ALLOW="${SIP_CODEC_ALLOW:-ulaw,alaw}"
export LOCAL_NET="${LOCAL_NET:-192.168.0.0/16}"
export REGISTRATION_RETRY_INTERVAL="${REGISTRATION_RETRY_INTERVAL:-30}"
export REGISTRATION_FORBIDDEN_RETRY_INTERVAL="${REGISTRATION_FORBIDDEN_RETRY_INTERVAL:-300}"
export SDR_DRIVER="${SDR_DRIVER:-sdrplay}"
SDR_DRIVER=$(printf '%s' "$SDR_DRIVER" | tr 'A-Z' 'a-z')
export SDR_DRIVER
export SDR_FREQUENCY_KHZ="${SDR_FREQUENCY_KHZ:-3699}"
export SDR_MODE="${SDR_MODE:-lsb}"

case "$SDR_DRIVER" in
    sdrplay|rtlsdr|plutosdr) ;;
    *) echo "SDR_DRIVER must be sdrplay, rtlsdr, or plutosdr" >&2; exit 64 ;;
esac

external_transport_settings=""
if [ -n "${EXTERNAL_ADDRESS:-}" ]; then
    external_transport_settings="external_media_address=$EXTERNAL_ADDRESS
external_signaling_address=$EXTERNAL_ADDRESS
external_signaling_port=$HOST_SIP_PORT"
fi
export EXTERNAL_TRANSPORT_SETTINGS="$external_transport_settings"

envsubst < /opt/sip-sdr-service/pjsip.conf.template > /etc/asterisk/pjsip.conf
envsubst < /opt/sip-sdr-service/rtp.conf.template > /etc/asterisk/rtp.conf
chmod 0600 /etc/asterisk/pjsip.conf

if [ "$SDR_DRIVER" = "sdrplay" ]; then
    echo "Starting SDRplay API service"
    sdrplay_apiService &
    sdrplay_api_pid=$!

    api_ready=""
    for _ in $(seq 1 30); do
        if SoapySDRUtil --find="driver=sdrplay" >/dev/null 2>&1; then
            api_ready=1
            break
        fi
        sleep 1
    done
    if [ -z "$api_ready" ]; then
        echo "SDRplay API service did not become ready within 30 seconds" >&2
        exit 1
    fi
    echo "SDRplay API service ready (pid $sdrplay_api_pid)"
fi

echo "Starting SIP SDR service: driver=$SDR_DRIVER ${SDR_FREQUENCY_KHZ} kHz ${SDR_MODE}"
exec /usr/sbin/asterisk -f -vvv
