FROM ubuntu:22.04

ARG DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y \
    git cmake build-essential python3 python3-pip python3-venv \
    libssl-dev libboost-all-dev libsqlite3-dev \
    clang-tidy cppcheck openjdk-17-jdk nodejs npm \
    libpcap-dev libevent-dev pkg-config libcap-dev curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/everest
COPY . /opt/everest

# Install EVerest Dependency Manager (EDM)
RUN python3 -m pip install --no-cache-dir --upgrade pip setuptools wheel jstyleson jsonschema \
    && git clone https://github.com/EVerest/everest-dev-environment.git /opt/everest/everest-dev-environment \
    && python3 -m pip install --no-cache-dir /opt/everest/everest-dev-environment/dependency_manager
ENV PATH="/root/.local/bin:${PATH}"
ENV CPM_SOURCE_CACHE="/root/.cache/CPM"

# Build and install everest-core
RUN cmake -B /opt/everest/everest-core/build -S /opt/everest/everest-core \
    && cmake --build /opt/everest/everest-core/build -j2 \
    && cmake --install /opt/everest/everest-core/build
ENV PATH="/opt/everest/everest-core/build/dist/bin:${PATH}"
ENV CMAKE_PREFIX_PATH=/opt/everest/everest-core/build/dist

# Install Python deps for HAL (prefer apt to avoid PyPI timeouts)
RUN apt-get update && apt-get install -y python3-serial && rm -rf /var/lib/apt/lists/*

# Install our Python modules directly (runtime only)
RUN mkdir -p /usr/local/libexec/everest/modules/esp32_hal_adapter \
    && mkdir -p /usr/local/libexec/everest/modules/evse_params_provider \
    && mkdir -p /opt/everest/everest-core/build/dist/libexec/everest/modules/esp32_hal_adapter \
    && mkdir -p /opt/everest/everest-core/build/dist/libexec/everest/modules/evse_params_provider
COPY modules/esp32_hal_adapter/manifest.yaml /usr/local/libexec/everest/modules/esp32_hal_adapter/manifest.yaml
COPY modules/esp32_hal_adapter/python /usr/local/libexec/everest/modules/esp32_hal_adapter/python
COPY modules/evse_params_provider/manifest.yaml /usr/local/libexec/everest/modules/evse_params_provider/manifest.yaml
COPY modules/evse_params_provider/python /usr/local/libexec/everest/modules/evse_params_provider/python
COPY modules/esp32_hal_adapter/manifest.yaml /opt/everest/everest-core/build/dist/libexec/everest/modules/esp32_hal_adapter/manifest.yaml
COPY modules/esp32_hal_adapter/python /opt/everest/everest-core/build/dist/libexec/everest/modules/esp32_hal_adapter/python
COPY modules/evse_params_provider/manifest.yaml /opt/everest/everest-core/build/dist/libexec/everest/modules/evse_params_provider/manifest.yaml
COPY modules/evse_params_provider/python /opt/everest/everest-core/build/dist/libexec/everest/modules/evse_params_provider/python

CMD ["/bin/bash", "-lc", "/opt/everest/everest-core/build/dist/bin/manager --config /opt/everest/config/plc_only_sim.yaml"]
