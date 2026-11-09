#!/bin/zsh
set -eu
cd -- "$(dirname -- "$0")"
exec python3 server.py --port 8877
