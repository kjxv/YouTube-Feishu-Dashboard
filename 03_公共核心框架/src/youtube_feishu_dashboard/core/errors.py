"""系统可识别的异常分类，供调度器决定是否重试。"""


class DashboardError(Exception):
    """项目公共异常基类。"""


class ConfigurationError(DashboardError):
    """配置缺失或不合法。通常需要人工修正，不应盲目重试。"""


class AuthenticationError(DashboardError):
    """OAuth、Token 或应用凭证无效。"""


class ExternalServiceError(DashboardError):
    """外部服务请求失败。"""

    def __init__(self, message: str, *, retryable: bool = True) -> None:
        super().__init__(message)
        self.retryable = retryable


class LockNotAcquiredError(DashboardError):
    """另一个进程或节点已持有任务锁。"""


class FieldCatalogError(DashboardError):
    """字段目录或模块字段需求不一致。"""


class StorageError(DashboardError):
    """数据库读写失败。"""
