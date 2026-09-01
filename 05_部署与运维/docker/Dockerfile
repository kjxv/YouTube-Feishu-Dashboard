FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY pyproject.toml README.md alembic.ini ./
COPY 03_公共核心框架 ./03_公共核心框架
COPY 04_功能模块 ./04_功能模块
COPY 05_部署与运维/docker/entrypoint.sh /usr/local/bin/yfd-entrypoint
RUN python -m pip install ".[postgres]"
RUN chmod +x /usr/local/bin/yfd-entrypoint

ENTRYPOINT ["/usr/local/bin/yfd-entrypoint"]
CMD ["python", "-m", "youtube_feishu_dashboard", "scheduler", "tick"]
