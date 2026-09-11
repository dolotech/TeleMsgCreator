"""Web 编辑器（FastAPI）。需要安装可选依赖：``pip install 'telemsg[web]'``。"""

from .app import create_app

__all__ = ["create_app"]
