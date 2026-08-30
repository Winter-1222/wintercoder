"""Electron 与 Python 之间的 NDJSON 桥接层。

这里只导出不依赖真实 stdin/stdout 的 BridgeApplication，调用方无需知道内部文件布局。
"""

from jixue.bridge.application import BridgeApplication

__all__ = ["BridgeApplication"]
