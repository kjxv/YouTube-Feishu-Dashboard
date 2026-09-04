from __future__ import annotations

import csv
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import TypeAlias

from youtube_feishu_dashboard.catalog.field_catalog import FieldCatalog, FieldDefinition
from youtube_feishu_dashboard.core.errors import FieldCatalogError

ReportingReachValue: TypeAlias = int | float | None

REACH_IMPRESSIONS_COLUMN = "video_thumbnail_impressions"
REACH_CTR_COLUMN = "video_thumbnail_impressions_ctr"
SUPPORTED_REACH_COLUMNS = frozenset({REACH_IMPRESSIONS_COLUMN, REACH_CTR_COLUMN})


@dataclass(frozen=True, slots=True)
class DatedReportFile:
    report_date: date
    path: Path


@dataclass(frozen=True, slots=True)
class ReportingReachExtraction:
    values: dict[str, ReportingReachValue]
    data_through_date: date | None
    matched_row_count: int


class ReportingReachExtractor:
    """把 Reach Basic 日报按视频汇总成累计展示量和加权点击率。"""

    def __init__(self, fields: tuple[FieldDefinition, ...]) -> None:
        self.fields = fields

    @classmethod
    def compile(
        cls,
        catalog: FieldCatalog,
        field_ids: Sequence[str],
    ) -> ReportingReachExtractor:
        unique_ids = tuple(dict.fromkeys(field_ids))
        catalog.validate_requirements(unique_ids)
        fields: list[FieldDefinition] = []
        errors: list[str] = []
        for field_id in unique_ids:
            definition = catalog.get(field_id)
            if definition.api_source != "reporting_api":
                errors.append(
                    f"{field_id} 来自 {definition.api_source}，不是 Reporting API 字段"
                )
                continue
            if definition.official_field not in SUPPORTED_REACH_COLUMNS:
                errors.append(
                    f"{field_id} 的列 {definition.official_field!r} 不属于 Reach Basic 报告"
                )
                continue
            if definition.data_type not in {"integer", "number"}:
                errors.append(f"{field_id} 的类型 {definition.data_type} 不是数值类型")
                continue
            fields.append(definition)
        if not unique_ids:
            errors.append("至少需要一个 Reporting Reach 字段")
        if errors:
            raise FieldCatalogError("Reporting Reach 字段计划不可执行：" + "；".join(errors))
        return cls(tuple(fields))

    @property
    def field_ids(self) -> tuple[str, ...]:
        return tuple(field.standard_field_id for field in self.fields)

    def extract_video(
        self,
        files: Sequence[DatedReportFile],
        *,
        video_id: str,
    ) -> ReportingReachExtraction:
        total_impressions = 0
        weighted_ctr = 0.0
        matched_rows = 0
        matched_dates: list[date] = []

        for item in files:
            with item.path.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle)
                columns = set(reader.fieldnames or ())
                required = {"date", "video_id", REACH_IMPRESSIONS_COLUMN}
                if any(field.official_field == REACH_CTR_COLUMN for field in self.fields):
                    required.add(REACH_CTR_COLUMN)
                missing = sorted(required - columns)
                if missing:
                    raise FieldCatalogError(
                        f"Reporting 文件 {item.path.name} 缺少列：{'、'.join(missing)}"
                    )
                for row in reader:
                    if str(row.get("video_id") or "") != video_id:
                        continue
                    row_date = _date_value(row.get("date"), item.path)
                    if row_date != item.report_date:
                        raise FieldCatalogError(
                            f"Reporting 文件 {item.path.name} 的 date={row_date}，"
                            f"与报告日期 {item.report_date} 不一致"
                        )
                    impressions = _integer_value(
                        row.get(REACH_IMPRESSIONS_COLUMN),
                        REACH_IMPRESSIONS_COLUMN,
                        item.path,
                    )
                    ctr = _number_value(
                        row.get(REACH_CTR_COLUMN),
                        REACH_CTR_COLUMN,
                        item.path,
                    )
                    total_impressions += impressions
                    weighted_ctr += impressions * ctr
                    matched_rows += 1
                    matched_dates.append(row_date)

        # 截止日期必须来自当前视频实际命中的报表行，不能用目录里其他
        # 视频的最新报告日期冒充当前视频的数据截止日期。
        data_through_date = max(matched_dates, default=None)
        values: dict[str, ReportingReachValue] = {}
        for field in self.fields:
            if field.official_field == REACH_IMPRESSIONS_COLUMN:
                values[field.standard_field_id] = total_impressions
            else:
                values[field.standard_field_id] = (
                    round(weighted_ctr / total_impressions, 6)
                    if total_impressions > 0
                    else 0.0
                )
        return ReportingReachExtraction(
            values=values,
            data_through_date=data_through_date,
            matched_row_count=matched_rows,
        )


def _date_value(value: object, path: Path) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError as exc:
        raise FieldCatalogError(
            f"Reporting 文件 {path.name} 包含无效日期：{value!r}"
        ) from exc


def _integer_value(value: object, column: str, path: Path) -> int:
    try:
        return int(str(value or "0"))
    except ValueError as exc:
        raise FieldCatalogError(
            f"Reporting 文件 {path.name} 的 {column} 不是整数：{value!r}"
        ) from exc


def _number_value(value: object, column: str, path: Path) -> float:
    try:
        return float(str(value or "0"))
    except ValueError as exc:
        raise FieldCatalogError(
            f"Reporting 文件 {path.name} 的 {column} 不是数字：{value!r}"
        ) from exc
