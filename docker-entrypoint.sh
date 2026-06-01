#!/bin/sh
set -e

case "$1" in
    gui|"")
        exec phenoscribe-gui
        ;;
    cli)
        shift
        exec phenoscribe "$@"
        ;;
    *)
        # Passthrough: allow `docker run image python ...`, bash, etc.
        exec "$@"
        ;;
esac
