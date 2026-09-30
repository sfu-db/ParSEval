
FROM ghcr.io/astral-sh/uv:python3.10-trixie AS dev

ARG USERNAME=user
ARG USER_UID=1000
ARG USER_GID=1000


ENV DEBIAN_FRONTEND=noninteractive
ENV HOME=/home/user

ENV VIRTUAL_ENV=/opt/venv

ENV UV_TOOL_BIN_DIR=/home/user/.local/bin

ENV CARGO_HOME=/home/user/.cargo
ENV RUSTUP_HOME=/home/user/.rustup
ENV PATH=$CARGO_HOME/bin:$VIRTUAL_ENV/bin:$UV_TOOL_BIN_DIR:$PATH
ENV UV_PROJECT_ENVIRONMENT=$VIRTUAL_ENV


ENV LANG=C.UTF-8
ENV LC_ALL=C.UTF-8

RUN --mount=type=cache,target=/var/cache/apt/ \
    --mount=type=cache,target=/var/lib/apt/ \
    apt-get update && \
    apt-get install --no-install-recommends --yes \
      build-essential \
      ca-certificates \
      clang \
      curl \
      git \
      graphviz \
      libssl-dev \
      lld \
      locales \
      pkg-config \
      sqlite3 \
      ssh \
      sudo \
      vim \
      tmux \
      wget && \
    groupadd --gid $USER_GID user && \
    useradd --create-home --no-log-init --gid $USER_GID --uid $USER_UID --shell /usr/bin/bash user && \
    mkdir -p /opt/venv && chown user:user /opt/venv && \
    echo 'user ALL=(root) NOPASSWD:ALL' > /etc/sudoers.d/user && chmod 0440 /etc/sudoers.d/user

RUN git config --system --add safe.directory '*'
RUN sh -c "$(curl -fsSL https://starship.rs/install.sh)" -- --yes
RUN printf 'export PATH="%s:$PATH"\n' "$CARGO_HOME/bin:$VIRTUAL_ENV/bin:$UV_TOOL_BIN_DIR" > /etc/profile.d/devcontainer-path.sh

SHELL ["/bin/bash", "-c"]

USER user
RUN mkdir -p "$HOME/.local/bin" "$HOME/.local/share" "$CARGO_HOME" "$RUSTUP_HOME"

RUN uv venv "${VIRTUAL_ENV}"

ARG RUST_VERSION="stable"
RUN --mount=type=cache,target=/home/user/.cargo/registry \
    --mount=type=cache,target=/home/user/.cargo/git \
    curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y \
      --default-toolchain ${RUST_VERSION} \
      --profile default \
      --no-modify-path && \
    cargo --version && \
    rustc --version && \
    rustup component add rust-analyzer clippy rustfmt

ARG NODE_VERSION="24"
ARG NVM_VERSION="0.40.3"
ARG PNPM_VERSION="latest"
ENV NVM_DIR=/home/user/.nvm
ENV NVM_SYMLINK_CURRENT=true

RUN mkdir -p "$NVM_DIR" \
    && curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v${NVM_VERSION}/install.sh | bash \
    && source "$NVM_DIR/nvm.sh" \
    && nvm install "$NODE_VERSION" \
    && nvm alias default "$NODE_VERSION" \
    && nvm use default \
    && corepack enable \
    && corepack prepare pnpm@${PNPM_VERSION} --activate

# -------------------------------------------------------------------
# 4. Update Shell Profile / PATH configuration for interactive shells
# -------------------------------------------------------------------
RUN mkdir -p "$HOME/.history" && \
    echo 'export HISTFILE=$HOME/.history/.bash_history' >> "$HOME/.bashrc" && \
    echo 'export HISTSIZE=10000'                        >> "$HOME/.bashrc" && \
    echo 'export HISTFILESIZE=20000'                    >> "$HOME/.bashrc" && \
    echo 'export HISTCONTROL=ignoredups:erasedups'      >> "$HOME/.bashrc" && \
    echo 'shopt -s histappend'                          >> "$HOME/.bashrc" && \
    echo 'PROMPT_COMMAND="history -a; history -c; history -r"' >> "$HOME/.bashrc" && \    
    echo '[[ $- == *i* ]] && bind "\e[A":history-search-backward' >> "$HOME/.bashrc" && \
    echo '[[ $- == *i* ]] && bind "\e[B":history-search-forward'  >> "$HOME/.bashrc" && \
    echo 'if [ -f "$HOME/.bashrc" ]; then . "$HOME/.bashrc"; fi'  > "$HOME/.bash_profile" && \
    echo 'export NVM_DIR=/home/user/.nvm'                          >> "$HOME/.bashrc" && \
    echo 'export CARGO_HOME=/home/user/.cargo'                    >> "$HOME/.bashrc" && \
    echo 'export RUSTUP_HOME=/home/user/.rustup'                  >> "$HOME/.bashrc" && \
    echo 'export PATH=$CARGO_HOME/bin:$VIRTUAL_ENV/bin:$UV_TOOL_BIN_DIR:$NVM_DIR/current/bin:$PATH' >> "$HOME/.bashrc" && \
    echo '[ -s "$NVM_DIR/nvm.sh" ] && . "$NVM_DIR/nvm.sh"'          >> "$HOME/.bashrc" && \
    echo '[ -s "$NVM_DIR/bash_completion" ] && . "$NVM_DIR/bash_completion"' >> "$HOME/.bashrc"

SHELL ["/bin/bash", "-lc"]

USER root
RUN mkdir -p /workspaces/parseval && chown user:user /workspaces/parseval
USER user
