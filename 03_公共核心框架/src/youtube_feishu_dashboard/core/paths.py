"""跨平台项目路径发现。"""

from pathlib import Path


def discover_project_root(start: Path | None = None) -> Path:
    """从当前位置向上寻找 pyproject.toml；安装后找不到时使用当前目录。"""
    current = (start or Path.cwd()).resolve()
    for candidate in (current, *current.parents):
        if (candidate / "pyproject.toml").is_file():
            return candidate
    return current


def resolve_from_root(path: Path, root: Path) -> Path:
    return path if path.is_absolute() else (root / path).resolve()
