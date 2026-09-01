#!/bin/sh
set -eu

python -m youtube_feishu_dashboard db upgrade head
exec "$@"
