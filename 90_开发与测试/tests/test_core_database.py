from datetime import UTC, datetime
from pathlib import Path

from youtube_feishu_dashboard.core.settings import Settings
from youtube_feishu_dashboard.db.repositories import SqlAlchemyStorage


def test_settings_resolve_relative_sqlite_path(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        project_root=tmp_path,
        database_url="sqlite:///data/example.db",
    )

    assert settings.sqlalchemy_url == f"sqlite:///{(tmp_path / 'data/example.db').as_posix()}"


def test_repository_round_trip_and_task_lock(storage: SqlAlchemyStorage) -> None:
    now = datetime(2026, 8, 31, 6, 30, tzinfo=UTC)
    with storage.transaction() as repos:
        repos.channels.upsert(
            "UC_TEST",
            title="测试频道",
            uploads_playlist_id="UU_TEST",
            timezone="Asia/Shanghai",
        )
        repos.videos.upsert(
            {
                "id": "video-1",
                "channel_id": "UC_TEST",
                "title": "测试视频",
                "published_at": now,
            }
        )
        repos.videos.add_snapshot(
            video_id="video-1",
            observed_at=now,
            view_count=10,
            like_count=2,
            comment_count=1,
            raw_json={"statistics": {"viewCount": "10"}},
        )
        repos.scheduler.ensure_job(
            task_id="latest-video-tracker",
            module_id="latest_video_tracker",
            interval_seconds=1800,
            first_run_at=now,
        )
        assert repos.scheduler.try_acquire_lock(
            "task:latest-video-tracker", "worker-a", now=now, ttl_seconds=900
        )
        assert not repos.scheduler.try_acquire_lock(
            "task:latest-video-tracker", "worker-b", now=now, ttl_seconds=900
        )

    with storage.transaction() as repos:
        assert repos.channels.get("UC_TEST") is not None
        assert len(repos.videos.list_published_since("UC_TEST", now)) == 1
        assert len(repos.scheduler.due_jobs(now)) == 1
