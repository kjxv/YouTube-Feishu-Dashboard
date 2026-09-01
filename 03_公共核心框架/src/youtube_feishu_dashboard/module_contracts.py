"""功能模块与公共核心框架之间的稳定契约。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    """模块只通过清单声明身份、能力需求和实施状态。"""

    module_id: str
    version: str
    cn_name: str
    description: str
    api_field_ids: tuple[str, ...]
    output_field_ids: tuple[str, ...] = ()
    implemented: bool = False
