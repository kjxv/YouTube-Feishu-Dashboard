#!/usr/bin/env bash
set -euo pipefail

cd /opt/YouTube-Feishu-Dashboard
runtime/venv/bin/python -m youtube_feishu_dashboard \
  feishu delete-channel-placeholder-zero-day 2026-09-11 \
  --expected-count 68
runtime/venv/bin/python -m youtube_feishu_dashboard \
  feishu delete-channel-placeholder-zero-day 2026-09-11 \
  --expected-count 68 --confirm
