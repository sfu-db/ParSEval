FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    curl \
    vim \
    build-essential \
    openssh-client \
    graphviz \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir --upgrade pip setuptools wheel

# Optional: Set working directory
WORKDIR /workspace

# Optional: Create a non-root user for development
RUN useradd -ms /bin/bash devuser
USER devuser
RUN echo 'export PATH="$HOME/.local/bin:$PATH"' >> /home/devuser/.bashrc

# Set default shell
CMD ["bash"]