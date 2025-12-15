FROM python:3.9-slim-bullseye
# Add non-root user

ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ssh graphviz git curl vim wget build-essential zsh locales sqlite3 \
    && locale-gen en_US.UTF-8 \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Create a non-root user
# RUN useradd -ms /bin/zsh devuser && usermod -aG sudo devuser

# Install Oh My Zsh and plugins
RUN sh -c "$(curl -fsSL https://raw.githubusercontent.com/ohmyzsh/ohmyzsh/master/tools/install.sh)" "" --unattended \
    && git clone https://github.com/zsh-users/zsh-autosuggestions ${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-autosuggestions \
    && git clone https://github.com/zsh-users/zsh-syntax-highlighting ${ZSH_CUSTOM:-$HOME/.oh-my-zsh/custom}/plugins/zsh-syntax-highlighting \ 
    && sed -i 's/ZSH_THEME=".*"/ZSH_THEME="simple"/' $HOME/.zshrc \
    && sed -i 's/plugins=(git)/plugins=(git zsh-autosuggestions zsh-syntax-highlighting)/' ~/.zshrc

SHELL ["/bin/zsh", "-c"]

RUN curl -sSL https://install.python-poetry.org | python3 - \
    && echo 'export PATH="$HOME/.local/bin:$PATH"' >> ~/.zshrc

# RUN echo 'if [ -f ~/.bashrc ]; then source ~/.bashrc; fi' >> ${HOME}/.zshrc
WORKDIR /workspace

CMD ["zsh"]