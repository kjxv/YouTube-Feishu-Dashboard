import unittest
from unittest.mock import patch

import youtube_lark


def history_record(record_id, video_id, day, **fields):
    values = {
        "Video ID": video_id,
        "日期": youtube_lark.date_to_ms(day),
        "发布后天数": fields.pop("发布后天数", 0),
        **fields,
    }
    return {
        "record_id": record_id,
        "fields": values,
    }


class DashboardHelperTests(unittest.TestCase):

    def test_release_pair_ignores_leading_emoji_and_live(self):
        videos = [
            {
                "video_id": "long",
                "title": "🦐 同一期内容",
                "published_at": "2026-08-28T01:00:00Z",
                "video_type": youtube_lark.VIDEO_TYPE_LONG,
            },
            {
                "video_id": "short",
                "title": "同一期内容",
                "published_at": "2026-08-28T01:03:00Z",
                "video_type": youtube_lark.VIDEO_TYPE_SHORT,
            },
            {
                "video_id": "live",
                "title": "稍后直播",
                "published_at": "2026-08-29T01:00:00Z",
                "video_type": youtube_lark.VIDEO_TYPE_LIVE,
            },
        ]

        label = youtube_lark.assign_content_releases(videos)

        self.assertTrue(label)
        self.assertEqual(
            videos[0]["content_release"],
            videos[1]["content_release"]
        )
        self.assertTrue(videos[0]["is_latest_release"])
        self.assertTrue(videos[1]["is_latest_release"])
        self.assertFalse(videos[2]["is_latest_release"])
        self.assertEqual(videos[2]["content_release"], "")

    def test_publish_day_is_zero_and_pre_publish_row_is_skipped(self):
        video = {
            "video_id": "video",
            "title": "标题",
            "published_at": "2026-08-28T23:30:00Z",
            "video_type": youtube_lark.VIDEO_TYPE_LONG,
            "content_release": "一期",
            "is_latest_release": True,
        }

        publish_day = youtube_lark.iso_to_analytics_date(
            video["published_at"]
        )
        row = [
            str(publish_day),
            10,
            2.5,
            15,
            50,
            2,
            1,
            0.25,
        ]
        fields = youtube_lark.make_video_history_fields(video, row)
        before = list(row)
        before[0] = str(publish_day - youtube_lark.timedelta(days=1))

        self.assertEqual(fields["发布后天数"], 0)
        self.assertEqual(fields["净增订阅"], 1)
        self.assertIsNone(
            youtube_lark.make_video_history_fields(video, before)
        )

    def test_cumulative_metrics_and_latest_flags(self):
        videos = [
            {
                "video_id": "long",
                "title": "同一期内容",
                "published_at": "2026-08-28T01:00:00Z",
                "video_type": youtube_lark.VIDEO_TYPE_LONG,
            },
            {
                "video_id": "short",
                "title": "同一期内容",
                "published_at": "2026-08-28T01:03:00Z",
                "video_type": youtube_lark.VIDEO_TYPE_SHORT,
            },
        ]
        history = [
            history_record(
                "h1",
                "long",
                "2026-08-28",
                当日播放量="10",
                **{
                    "观看时长（分钟）": "2.5",
                    "新增订阅": "2",
                    "取消订阅": "1",
                    "预估收入（USD）": "0.25",
                },
            ),
            history_record(
                "h2",
                "long",
                "2026-08-29",
                发布后天数=1,
                当日播放量="15",
                **{
                    "观看时长（分钟）": "3.5",
                    "新增订阅": "1",
                    "取消订阅": "0",
                    "预估收入（USD）": "0.50",
                },
            ),
        ]
        main = [
            {"record_id": "m1", "fields": {"Video ID": "long"}},
            {"record_id": "m2", "fields": {"Video ID": "short"}},
        ]
        writes = []

        def fake_get_all_records(token, table_id):
            if table_id == youtube_lark.HISTORY_TABLE_ID:
                return history
            if table_id == youtube_lark.VIDEO_TABLE_ID:
                return main
            raise AssertionError(table_id)

        def fake_batch_update(token, table_id, records):
            writes.append((table_id, records))

        with patch.object(
            youtube_lark,
            "get_all_records",
            side_effect=fake_get_all_records
        ), patch.object(
            youtube_lark,
            "batch_update",
            side_effect=fake_batch_update
        ):
            youtube_lark.sync_dashboard_video_helpers(
                "token",
                videos
            )

        history_updates = dict(
            (item["record_id"], item["fields"])
            for table_id, records in writes
            if table_id == youtube_lark.HISTORY_TABLE_ID
            for item in records
        )
        main_updates = dict(
            (item["record_id"], item["fields"])
            for table_id, records in writes
            if table_id == youtube_lark.VIDEO_TABLE_ID
            for item in records
        )

        self.assertEqual(
            history_updates["h2"]["发布后累计播放量"],
            25
        )
        self.assertEqual(
            history_updates["h2"]["发布后累计观看时长"],
            6.0
        )
        self.assertEqual(
            history_updates["h2"]["发布后累计净增订阅"],
            2
        )
        self.assertEqual(
            history_updates["h2"]["发布后累计收入"],
            0.75
        )
        self.assertTrue(history_updates["h2"]["是否最新一期"])
        self.assertEqual(
            main_updates["m1"]["发布后累计播放量"],
            25
        )
        self.assertTrue(main_updates["m2"]["是否最新一期"])


if __name__ == "__main__":
    unittest.main()
