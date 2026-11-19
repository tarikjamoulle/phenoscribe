#!/bin/sh
# Start Phenoscribe. Double-click on a Mac, or run `sh "Start Phenoscribe.command"` on Linux.
cd "$(dirname "$0")" || exit 1
export PHENOSCRIBE_OUTPUT_DIR="$HOME/Documents/Phenoscribe results"
export PATH="$HOME/.local/bin:$PATH"

fail() {
    echo
    echo "Phenoscribe stopped with an error. Read the message above, then press Enter."
    read -r _
    exit 1
}

if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv (one time only)..."
    curl -LsSf https://astral.sh/uv/0.6.17/install.sh | sh || fail
fi
uv run --frozen --no-dev phenoscribe setup || fail
uv run --frozen --no-dev phenoscribe-gui || fail
