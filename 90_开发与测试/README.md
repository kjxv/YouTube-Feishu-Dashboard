# 90 开发与测试

这里保存自动化测试和后续开发辅助材料，不参与生产业务运行。

测试会覆盖公共数据库、API 适配、调度锁与恢复、飞书幂等写入，以及最新视频模块的完整纵向流程。运行 `pytest` 时，根目录 `pyproject.toml` 会自动定位这里的测试。

Windows 不使用 Python editable install，因为部分 Python 版本会按系统代码页读取包含中文路径的 `.pth` 文件。修改源码后重新执行 `python -m pip install --upgrade .` 即可，正式运行与 Linux/VPS 不受影响。
