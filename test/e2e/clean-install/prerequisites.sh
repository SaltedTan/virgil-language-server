#!/usr/bin/env bash
# Copyright 2026 The Virgil Language Server Authors.
# SPDX-License-Identifier: Apache-2.0
# Test infrastructure only: README-named editors/tools, never the server or VSIX.
set -euo pipefail
case ${1:-} in
  container)
    apt-get update
    DEBIAN_FRONTEND=noninteractive apt-get install --yes --no-install-recommends \
      ca-certificates curl tar gzip coreutils grep python3 nodejs git build-essential \
      xvfb xauth libasound2t64 libatk-bridge2.0-0 libgtk-3-0t64 libnss3 libgbm1 \
      libx11-xcb1 libxkbfile1 libsecret-1-0
    mkdir -p /opt/editors
    curl --fail --location --retry 3 https://github.com/neovim/neovim/releases/download/v0.11.6/nvim-linux-x86_64.tar.gz -o /opt/editors/nvim.tar.gz
    echo '2fc90b962327f73a78afbfb8203fd19db8db9cdf4ee5e2bef84704339add89cc  /opt/editors/nvim.tar.gz' | sha256sum --check --strict
    tar -xzf /opt/editors/nvim.tar.gz -C /opt/editors
    curl --fail --location --retry 3 https://update.code.visualstudio.com/1.142.0/linux-x64/stable -o /opt/editors/code.tar.gz
    tar -xzf /opt/editors/code.tar.gz -C /opt/editors
    ln -s /opt/editors/nvim-linux-x86_64/bin/nvim /usr/local/bin/nvim
    ln -s /opt/editors/VSCode-linux-x64/bin/code /usr/local/bin/code
    ;;
  lazyvim)
    : "${CLEAN_BUNDLE:?standalone bundle directory required}"
    git clone --quiet https://github.com/LazyVim/starter "$HOME/.config/nvim"
    git -C "$HOME/.config/nvim" checkout --quiet 803bc181d7c0d6d5eeba9274d9be49b287294d99
    rm -rf "$HOME/.config/nvim/.git"
    # Install the README's named LazyVim prerequisite at the full suite's pins.
    lazy_dir=$HOME/.local/share/nvim/lazy
    mkdir -p "$lazy_dir"
    for plugin in lazy.nvim LazyVim; do
      url=https://github.com/folke/lazy.nvim
      [[ $plugin != LazyVim ]] || url=https://github.com/LazyVim/LazyVim
      revision=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[sys.argv[2]]["commit"])' "$CLEAN_BUNDLE/test/e2e/lazyvim/lazy-lock.json" "$plugin")
      git clone --quiet --filter=blob:none "$url" "$lazy_dir/$plugin"
      git -C "$lazy_dir/$plugin" checkout --quiet "$revision"
    done
    cp "$CLEAN_BUNDLE/test/e2e/lazyvim/lazy-lock.json" "$HOME/.config/nvim/lazy-lock.json"
    timeout --kill-after=10 300 nvim --headless "+Lazy! restore" +qa
    ;;
  *) echo 'usage: prerequisites.sh container|lazyvim' >&2; exit 2 ;;
esac
