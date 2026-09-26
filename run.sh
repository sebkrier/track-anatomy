#!/usr/bin/env bash
# Start the Track Anatomy server in the foreground (Ctrl-C to stop).
# Address: TRACK_ANATOMY_HOST / TRACK_ANATOMY_PORT (default 127.0.0.1:8102), or --host / --port.
cd "$(dirname "$0")"
exec .venv/bin/python -m track_anatomy serve "$@"
