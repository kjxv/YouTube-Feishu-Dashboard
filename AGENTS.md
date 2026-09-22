# AI working contract

Read this file first, then `00_项目文档/AI项目导航.md` and `ai_project_manifest.json`.

## System in one minute

This repository collects YouTube Data API and Analytics API data, persists operational
state in one database, and synchronizes normalized fields to Feishu Bitable. The root CLI
is `python -m youtube_feishu_dashboard` (installed command: `yfd`). Shared infrastructure
lives in `03_公共核心框架`; business logic lives in independent packages under
`04_功能模块`.

Runtime flow:

1. CLI/scheduler loads local settings and the remote Feishu configuration center.
2. A module requests YouTube data through shared API clients.
3. The module emits standard field IDs, never hard-coded Feishu column names.
4. A validated runtime mapping converts standard IDs to the actual Feishu schema.
5. Database bindings and business keys make Feishu writes idempotent.

## Non-negotiable invariants

- Never commit `.env`, `secrets/`, `data/`, `logs/`, `runtime/`, OAuth files, or databases.
- Never run Windows and VPS production schedulers simultaneously.
- A business module must not create its own credentials, HTTP client, database, scheduler,
  or direct Feishu request layer. Reusable infrastructure belongs in the shared framework.
- Feishu writes must use standard field IDs plus validated mappings. Do not write by a
  guessed visible column name.
- Preserve stable business keys. A display-date policy change must not silently change
  existing unique keys or create duplicates.
- Missing Analytics rows mean “not returned/not settled”; they are not zero. Store zero
  only when the API explicitly returned zero.
- Destructive migrations require preview counts, exact confirmation counts, and a backup.

## Channel-history time and date policy

- Exact Data API and Analytics API timestamps exposed by this module are Pacific time,
  using IANA zone `America/Los_Angeles`. Offsets are `-07:00` or `-08:00` according to DST;
  never apply a fixed UTC offset.
- `统计日期` has standard ID `DAILY_DATA_DATE_PACIFIC` and is the only active business-date
  column in both history tables.
- On Data/snapshot rows, `统计日期` is the Pacific calendar date of the actual Data API
  acquisition timestamp.
- On `Analytics日统计` rows, `统计日期` is the official Pacific `day` returned by the
  Analytics API, i.e. the data-through date.
- `数据截止日期（旧版停用）` and `记录日期（旧版停用）` are migration remnants only. Do
  not use them in new logic or dashboards.
- The exact Analytics cutoff timestamp is the end of that official Pacific day. It is not
  the latest timestamp among videos in a batch.

## Where to change things

- CLI/application composition: `03_公共核心框架/src/youtube_feishu_dashboard/cli.py`
  and `app.py`.
- API time conversion: `03_公共核心框架/src/youtube_feishu_dashboard/services/api_time_fields.py`.
- Versioned field truth: `03_公共核心框架/src/youtube_feishu_dashboard/catalog/`.
- Channel-history field contract: `04_功能模块/02_频道历史数据/src/yfd_channel_history/manifest.py`.
- Runtime validation: same package, `runtime.py`.
- Collection and row semantics: same package, `service.py`.
- Feishu schema/mapping migration: same package, `feishu_setup.py`.
- One-time historical repair: same package, `date_backfill.py`.
- Tests: `90_开发与测试/tests/`.

When adding or renaming a field, update the catalog, module manifest, runtime allow-list,
writer, migration, tests, and module documentation together.

## Verification

Use the project virtual environment when available. This repository is installed as a
normal wheel in production-like checks, so reinstall after source edits before running
tests:

```text
runtime/venv/Scripts/python.exe -m pip install . --no-deps --force-reinstall
runtime/venv/Scripts/python.exe -m pytest -q
runtime/venv/Scripts/python.exe -m ruff check .
runtime/venv/Scripts/python.exe -m mypy
```

On Linux use `runtime/venv/bin/python` instead. Do not use an editable install from a
Windows path containing Chinese characters; its generated `.pth` may be decoded with the
wrong locale.

