FROM ubuntu:24.04

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        asterisk \
        ca-certificates \
        gettext-base \
        python3 \
        python3-cffi \
        python3-numpy \
        python3-scipy \
        python3-soapysdr \
        soapysdr-tools \
        soapysdr0.8-module-sdrplay3 \
        soapysdr0.8-module-rtlsdr \
        soapysdr0.8-module-plutosdr \
        udev \
    && rm -rf /var/lib/apt/lists/*

COPY vendor/sdrplay_api.run /tmp/sdrplay_api.run
RUN chmod +x /tmp/sdrplay_api.run \
    && /tmp/sdrplay_api.run --noexec --target /tmp/sdrplay_api_extracted \
    && sh -c 'cd /tmp/sdrplay_api_extracted && ./install_lib.sh' \
    && rm -rf /tmp/sdrplay_api.run /tmp/sdrplay_api_extracted

COPY config/asterisk.conf /etc/asterisk/asterisk.conf
COPY config/extensions.conf /etc/asterisk/extensions.conf
COPY config/logger.conf /etc/asterisk/logger.conf
COPY config/modules.conf /etc/asterisk/modules.conf
COPY config/musiconhold.conf /etc/asterisk/musiconhold.conf
COPY config/pjsip.conf.template /opt/sip-sdr-service/pjsip.conf.template
COPY config/rtp.conf.template /opt/sip-sdr-service/rtp.conf.template
COPY scripts/sdr_env.py /opt/sip-sdr-service/sdr_env.py
COPY scripts/sdr_demod.py /opt/sip-sdr-service/sdr_demod.py
COPY scripts/sdr_stream.py /opt/sip-sdr-service/sdr_stream.py
COPY scripts/sdr_backends/ /opt/sip-sdr-service/sdr_backends/
COPY scripts/test_sdr.py /usr/local/bin/test-sdr-receiver
COPY scripts/healthcheck.py /usr/local/bin/sip-sdr-healthcheck
COPY docker-entrypoint.sh /usr/local/bin/sip-sdr-entrypoint

# sdr_stream.py lives only in /opt/sip-sdr-service (alongside sdr_env.py,
# sdr_demod.py, and sdr_backends/, which it imports); /usr/local/bin/sdr-stream
# is a symlink to it rather than a second copy, so there's exactly one file
# to keep in sync.
RUN chmod 0755 \
        /opt/sip-sdr-service/sdr_stream.py \
        /usr/local/bin/test-sdr-receiver \
        /usr/local/bin/sip-sdr-healthcheck \
        /usr/local/bin/sip-sdr-entrypoint \
    && ln -s /opt/sip-sdr-service/sdr_stream.py /usr/local/bin/sdr-stream \
    && mkdir -p /run/asterisk /run/sip-sdr /var/log/asterisk \
    && chown -R asterisk:asterisk \
        /etc/asterisk /run/asterisk /run/sip-sdr /var/lib/asterisk /var/log/asterisk

# No USER directive here: the entrypoint needs root to start
# sdrplay_apiService (when SDR_DRIVER=sdrplay) and access USB device nodes.
# Asterisk itself still drops privilege via runuser/rungroup=asterisk in
# config/asterisk.conf, so SIP/media handling and the sdr-stream process it
# spawns run unprivileged, same as before.
ENTRYPOINT ["/usr/local/bin/sip-sdr-entrypoint"]
