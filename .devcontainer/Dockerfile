ARG PYTHON_VERSION=3.10
ARG POETRY_VERSION=2.2.1
ARG MINICONDA_VERSION=24.9.2-0

FROM continuumio/miniconda3:${MINICONDA_VERSION}

ENV DEBIAN_FRONTEND=noninteractive
# ENV LANG=en_US.UTF-8
# ENV LC_ALL=en_US.UTF-8

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ssh graphviz git curl vim wget build-essential zsh locales sqlite3 \
    && locale-gen en_US.UTF-8 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
# RUN useradd -ms /bin/zsh devuser && usermod -aG sudo devuser

SHELL ["/bin/bash", "-c"]

RUN conda init bash
RUN conda create -n py310 python=3.10 -y && \
    conda clean -afy && \
    conda config --system --set auto_activate_base false 

ENV CONDA_DEFAULT_ENV=py310

# Install Oh My Zsh and plugins
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended \
    && git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-autosuggestions \
    && git clone https://github.com/zsh-users/zsh-syntax-highlighting ${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting \ 
    && sed -i 's/ZSH_THEME=".*"/ZSH_THEME="simple"/' $HOME/.zshrc \
    && sed -i 's/plugins=(git)/plugins=(git zsh-autosuggestions zsh-syntax-highlighting)/' ~/.zshrc

# 5. Source conda.sh and activate py310 in zsh safely
RUN conda init zsh && \
    echo 'conda activate py310' >> ~/.zshrc

RUN curl -sSL https://install.python-poetry.org | python3 - --version "${POETRY_VERSION}" \
    && export PATH="$HOME/.local/bin:$PATH" \ 
    && poetry config virtualenvs.create false \
    && echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc

RUN curl -fsSL https://deb.nodesource.com/setup_24.x | bash - \
    && apt-get install -y nodejs \
    && npm install -g pnpm

RUN pip install --upgrade pip setuptools wheel

# RUN echo 'if [ -f ~/.bashrc ]; then source ~/.bashrc; fi' >> ${HOME}/.zshrc
WORKDIR /workspace

SHELL ["/bin/zsh", "-c"]