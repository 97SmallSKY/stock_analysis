"""
Wanqing LLM Chat 模块 - 配置常量
"""

# API 端点
BASE_URL = "https://wanqing.corp.kuaishou.com"
API_GATEWAY = f"{BASE_URL}/api/gateway/v1/models/model-experience"
API_CONVERSATIONS = f"{BASE_URL}/api/console/v1/models/conversations"
API_MODEL_INSTANCE = f"{BASE_URL}/api/console/v1/model-instances"
API_MODEL_CAPABILITIES = f"{BASE_URL}/api/console/v1/model-instance-capabilities/comparison"

# 默认请求头
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Content-Type": "application/json",
    "Authorization": "Bearer default-key",
    "Open-API-Product": "WANQING",
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36",
    "Origin": BASE_URL,
    "Referer": BASE_URL,
}

# 默认用户 ID
DEFAULT_USER_ID = "wb_zhangxiaotian03"

# 默认项目 ID
DEFAULT_PROJECT_ID = "proj-e0txp2-1765975764638779355"

# 续写相关配置
CONTINUATION_KEYWORDS = ["finished", "完成", "结束"]
MAX_CONTINUATIONS = 10
CONTINUATION_MESSAGE = "请继续"

# Cookies 文件路径
DEFAULT_COOKIES_FILE = "cookies.txt"

# Markdown 记录目录
DEFAULT_RECORDS_DIR = "chat_records"

# 登录页面 URL
LOGIN_URL = f"{BASE_URL}/ai-square/model-preview/mi-dphy15-1770365346089864936"

# 超时配置
REQUEST_TIMEOUT = 120  # 秒
MAX_RETRIES = 3

# SSE 事件类型
EVENT_MODEL_MESSAGE = "model_message"
EVENT_DONE = "done"
EVENT_WEB_SEARCH = "web_search"
