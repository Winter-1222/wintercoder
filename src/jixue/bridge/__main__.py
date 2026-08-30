"""支持 ``python -m jixue.bridge`` 启动。

Python 使用 ``-m`` 运行包时会执行这个文件；真正的组装与事件循环入口在 server.main。
"""

from jixue.bridge.server import main

# 入口文件故意只转发一次，业务实现放在可导入、可测试的 server.py 中。
main()
