"""
Wanqing LLM Chat 模块
====================

快手 Wanqing LLM 网页版的 Python 自动化接口。

功能：
- 支持流式对话
- 支持自动续写（检测 finished 标识）
- 支持对话记录到 Markdown（去重）
- 支持 Cookies 管理（过期自动登录）
- 支持多会话管理

快速开始：
---------

    from chat import WanqingClient, Session, CookiesManager, MarkdownRecorder
    
    # 初始化
    cookies_mgr = CookiesManager()
    cookies = cookies_mgr.get_with_validation()
    client = WanqingClient(cookies, model_id="mi-xxx", project_id="proj-xxx")
    
    # 创建会话
    session = client.create_session()
    
    # 发送消息（自动续写）
    response = session.send_message("请写一篇详细介绍")
    print(f"回复: {response}")
    
    # 记录到 Markdown
    path = session.record_to_markdown()
    print(f"已记录到: {path}")

作者：wb_zhangxiaotian03
"""

from .config import (
    BASE_URL,
    API_GATEWAY,
    API_CONVERSATIONS,
    API_MODEL_INSTANCE,
    API_MODEL_CAPABILITIES,
    DEFAULT_HEADERS,
    DEFAULT_USER_ID,
    DEFAULT_PROJECT_ID,
    DEFAULT_COOKIES_FILE,
    DEFAULT_RECORDS_DIR,
    LOGIN_URL,
    REQUEST_TIMEOUT,
    MAX_RETRIES,
    CONTINUATION_KEYWORDS,
    MAX_CONTINUATIONS,
    CONTINUATION_MESSAGE,
)

from .cookies_manager import CookiesManager, CookiesExpiredError
from .markdown_recorder import MarkdownRecorder
from .client import WanqingClient
from .session import Session, SessionManager

__all__ = [
    # 配置
    "BASE_URL",
    "API_GATEWAY",
    "API_CONVERSATIONS",
    "API_MODEL_INSTANCE",
    "API_MODEL_CAPABILITIES",
    "DEFAULT_HEADERS",
    "DEFAULT_USER_ID",
    "DEFAULT_PROJECT_ID",
    "DEFAULT_COOKIES_FILE",
    "DEFAULT_RECORDS_DIR",
    "LOGIN_URL",
    "REQUEST_TIMEOUT",
    "MAX_RETRIES",
    "CONTINUATION_KEYWORDS",
    "MAX_CONTINUATIONS",
    "CONTINUATION_MESSAGE",
    # 异常
    "CookiesExpiredError",
    # 管理器
    "CookiesManager",
    "MarkdownRecorder",
    # 客户端
    "WanqingClient",
    # 会话
    "Session",
    "SessionManager",
]

__version__ = "0.1.0"
