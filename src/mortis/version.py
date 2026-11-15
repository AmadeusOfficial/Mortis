from importlib.metadata import PackageNotFoundError, version


def get_version() -> str:
    """已安装包的版本号；未安装（例如直接从源码目录运行）时为 0.0.0。"""
    try:
        return version("mortis")
    except PackageNotFoundError:
        return "0.0.0"
