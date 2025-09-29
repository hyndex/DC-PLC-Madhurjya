FROM ubuntu:22.04
RUN apt-get update && apt-get install -y \
    git cmake build-essential python3 python3-pip python3-venv \
    libssl-dev libboost-all-dev \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /opt/everest
# Placeholder: clone/build everest-core during CI or use submodule in bind mount
COPY . /opt/everest
# Install Python requirements for Python modules if any
RUN pip3 install --no-cache-dir everestpy pyserial
CMD ["/bin/bash"]
