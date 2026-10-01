"""业务逻辑层。

每个功能模块一个 xxx_service.py，承载业务规则与数据库操作。
service 接收外部传入的 Session，不自己开会话，也不抛 HTTPException
（AGENTS.md 3.4）。
"""
