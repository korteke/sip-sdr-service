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
        soapysdr0.8-module-rtlsdr \
        udev \
        cmake \
        make \
        g++ \
        git \
        pkg-config \
        libsoapysdr-dev \
        libiio-dev \
        libad9361-dev \
        libusb-1.0-0-dev \
    && rm -rf /var/lib/apt/lists/*

# Ubuntu doesn't package soapysdr0.8-module-plutosdr (unlike -rtlsdr above),
# so it's built from source here against a pinned release tag. Unlike
# SDRplay below, PlutoSDR support is built unconditionally since it needs
# no proprietary vendor file.
RUN git clone --branch soapy-plutosdr-0.2.2 --depth 1 \
        https://github.com/pothosware/SoapyPlutoSDR.git /tmp/SoapyPlutoSDR \
    && cmake -S /tmp/SoapyPlutoSDR -B /tmp/SoapyPlutoSDR/build \
    && cmake --build /tmp/SoapyPlutoSDR/build -j"$(nproc)" \
    && cmake --install /tmp/SoapyPlutoSDR/build \
    && rm -rf /tmp/SoapyPlutoSDR \
    && ldconfig

# vendor/sdrplay_api.run is optional: only needed if you intend to use
# SDR_DRIVER=sdrplay at runtime. The wildcard COPY (paired with the always-
# present vendor/.gitkeep) lets the build succeed with or without it; the
# RUN step below detects which case applies. Ubuntu doesn't package
# soapysdr0.8-module-sdrplay3 either, so when the API is present this also
# builds that module from source (pinned release tag) against it. If the
# API is absent and SDR_DRIVER=sdrplay is set at runtime,
# docker-entrypoint.sh fails fast with a clear error instead of hanging.
COPY vendor/sdrplay_api.run* vendor/.gitkeep /tmp/vendor-stage/
RUN if [ -f /tmp/vendor-stage/sdrplay_api.run ]; then \
        echo "Installing SDRplay API from vendor/sdrplay_api.run" \
        && chmod +x /tmp/vendor-stage/sdrplay_api.run \
        && /tmp/vendor-stage/sdrplay_api.run --noexec --target /tmp/sdrplay_api_extracted \
        && sh -c 'cd /tmp/sdrplay_api_extracted && ./install_lib.sh' \
        && rm -rf /tmp/sdrplay_api_extracted \
        && ldconfig \
        && echo "Building soapysdr0.8-module-sdrplay3 against the installed API" \
        && git clone --branch soapy-sdrplay3-0.5.2 --depth 1 \
               https://github.com/pothosware/SoapySDRPlay3.git /tmp/SoapySDRPlay3 \
        && cmake -S /tmp/SoapySDRPlay3 -B /tmp/SoapySDRPlay3/build \
        && cmake --build /tmp/SoapySDRPlay3/build -j"$(nproc)" \
        && cmake --install /tmp/SoapySDRPlay3/build \
        && rm -rf /tmp/SoapySDRPlay3 \
        && ldconfig; \
    else \
        echo "vendor/sdrplay_api.run not found - skipping SDRplay API + module install (fine unless SDR_DRIVER=sdrplay)"; \
    fi \
    && rm -rf /tmp/vendor-stage

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
        /run/asterisk /run/sip-sdr /var/lib/asterisk /var/log/asterisk \
    && chown root:root /etc/asterisk \
    && rm -f /etc/asterisk/pjsip.conf /etc/asterisk/rtp.conf \
    && usermod -aG plugdev asterisk

# No USER directive here: the entrypoint needs root to start
# sdrplay_apiService (when SDR_DRIVER=sdrplay) and access USB device nodes.
# Asterisk itself still drops privilege via runuser/rungroup=asterisk in
# config/asterisk.conf, so SIP/media handling and the sdr-stream process it
# spawns run unprivileged, same as before.
ENTRYPOINT ["/usr/local/bin/sip-sdr-entrypoint"]
