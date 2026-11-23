#!/bin/sh
set -eu
python3 "$(dirname "$0")/prepare_data.py" --fetch-only
