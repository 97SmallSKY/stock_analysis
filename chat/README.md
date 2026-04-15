# Wanqing LLM Chat 模块

快手 Wanqing LLM 网页版的 Python 自动化接口，支持流式对话、自动续写、Markdown 记录。

## 📦 模块结构

```
chat/
├── __init__.py           # 包导出
├── config.py             # 配置常量
├── client.py             # WanqingClient 主客户端
├── session.py            # Session 会话管理
├── cookies_manager.py    # Cookies 管理器
├── markdown_recorder.py  # Markdown 记录器
├── examples.py           # 使用示例
└── README.md             # 使用说明
```

## 🚀 快速开始

### 1. 安装依赖

```bash
pip install requests
```

### 2. 获取 Cookies

Cookies 是访问 Wanqing LLM 的凭证，有两种方式获取：

**方式一：手动从浏览器复制**

1. 打开浏览器，访问 `https://wanqing.corp.kuaishou.com`
2. 使用企业账号登录
3. 打开开发者工具 (F12) -> Network 标签
4. 刷新页面，点击任意请求
5. 在 Headers 中复制完整的 Cookie 值
6. 保存到 `cookies.txt` 文件

**方式二：程序自动引导登录**

```python
from chat import CookiesManager

cookies_mgr = CookiesManager()
cookies = cookies_mgr.interactive_login()
```

### 3. 基本用法

```python
from chat import WanqingClient, Session

# 初始化客户端
client = WanqingClient(
    cookies="your_cookies_here",
    model_id="mi-zg405a-1772527268195191820",  # 模型实例 ID
    project_id="proj-e0txp2-1765975764638779355"  # 项目 ID
)

# 创建会话
session = Session(client)

# 发送消息
response = session.send_message("你好，请介绍一下你自己")
print(response)

# 记录到 Markdown
path = session.record_to_markdown()
print(f"已记录到: {path}")
```

## 📝 主要功能

### 1. 流式对话

```python
# 实时打印 LLM 的回复
session.send_message("写一首诗")

# 自定义流式输出
def my_callback(chunk):
    print(f"[收到] {chunk}", end="")

session.send_message("写一首诗", stream_callback=my_callback)
```

### 2. 自动续写

当 LLM 回复很长时，可能不会一次说完。模块会自动检测并续写：

```python
# 自动续写（默认检测 "finished" 关键字）
response = session.send_message("请写一篇详细的长文章")
# 回复完成后会自动检测是否需要续写

# 手动控制续写次数
response = session.send_message("请写一篇详细的长文章", max_continuations=5)
```

### 3. Markdown 记录

自动将对话记录到 Markdown 文件，支持去重：

```python
# 记录到文件
path = session.record_to_markdown()

# 指定输出路径
path = session.record_to_markdown("output/my_chat.md")

# 去重功能
# - 去除连续重复的字符（如 "好好好好" -> "好"）
# - 去除连续重复的词语
# - 去除连续重复的句子
```

### 4. 多会话管理

```python
from chat import WanqingClient, SessionManager

client = WanqingClient(cookies=cookies, model_id="mi-xxx", project_id="proj-xxx")
manager = SessionManager(client)

# 创建新会话
session1 = manager.create_session()
session1.send_message("第一个话题")

# 创建另一个会话
session2 = manager.create_session()
session2.send_message("第二个话题")

# 切换会话
manager.switch_session(session1.conversation_id)
```

### 5. Cookies 管理

```python
from chat import CookiesManager, WanqingClient

cookies_mgr = CookiesManager()

# 保存 cookies
cookies_mgr.save("your_cookies_string")

# 加载 cookies
cookies = cookies_mgr.load()

# 验证 cookies 有效性
if cookies_mgr.exists():
    client = WanqingClient(cookies=cookies, model_id="mi-xxx", project_id="proj-xxx")
    if client.validate_cookies():
        print("Cookies 有效")
    else:
        print("Cookies 已过期，需要重新登录")

# 自动处理过期
cookies = cookies_mgr.get_with_validation()  # 验证失败会弹出登录
```

## 📋 API 参考

### WanqingClient

```python
client = WanqingClient(
    cookies: str,              # Cookies 字符串
    model_id: str,             # 模型实例 ID
    project_id: str,           # 项目 ID (可选)
    user_id: str,              # 用户 ID (可选)
    cookies_manager: CookiesManager  # Cookies 管理器 (可选)
)

# 方法
client.chat(message: str) -> str                    # 发送消息并返回完整响应
client.chat_stream(message: str) -> Generator[str]  # 流式发送消息
client.validate_cookies() -> bool                   # 验证 cookies
client.create_conversation() -> dict               # 创建新会话
client.list_conversations(page: int, page_size: int) -> dict  # 获取会话列表
client.set_conversation(conversation_id: str)       # 设置当前会话
client.reset_conversation()                        # 重置会话
```

### Session

```python
session = Session(
    client: WanqingClient,
    conversation_id: str,      # 会话 ID (可选)
    conversation_group_id: str, # 会话组 ID (可选)
    auto_record: bool,        # 是否自动记录 (默认 True)
    recorder: MarkdownRecorder  # 记录器 (可选)
)

# 方法
session.send_message(message: str, **kwargs) -> str  # 发送消息（自动续写）
session.send_message_stream(message: str) -> Generator  # 流式发送
session.continue_reply() -> str  # 继续上一次回复
session.get_history() -> list   # 获取对话历史
session.record_to_markdown(path: str) -> str  # 记录到 Markdown
session.clear_history()         # 清空历史
```

### CookiesManager

```python
cookies_mgr = CookiesManager(cookies_file: str = "cookies.txt")

# 方法
cookies_mgr.load() -> str              # 加载 cookies
cookies_mgr.save(cookies: str)         # 保存 cookies
cookies_mgr.exists() -> bool           # 检查是否存在
cookies_mgr.interactive_login() -> str # 交互式登录
cookies_mgr.get_with_validation() -> str  # 获取并验证 cookies
```

### MarkdownRecorder

```python
recorder = MarkdownRecorder(output_dir: str = "chat_records")

# 方法
recorder.record(conversation_id: str, messages: list, output_path: str) -> str  # 记录对话
recorder.append(output_path: str, role: str, content: str)  # 追加消息
recorder.list_records() -> list  # 列出所有记录
```

## 🔧 配置说明

### 配置文件 (chat/config.py)

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `BASE_URL` | API 基础地址 | `https://wanqing.corp.kuaishou.com` |
| `DEFAULT_USER_ID` | 默认用户 ID | `wb_zhangxiaotian03` |
| `DEFAULT_PROJECT_ID` | 默认项目 ID | `proj-e0txp2-1765975764638779355` |
| `CONTINUATION_KEYWORDS` | 续写检测关键字 | `["finished", "完成", "结束"]` |
| `MAX_CONTINUATIONS` | 最大续写次数 | `10` |
| `REQUEST_TIMEOUT` | 请求超时时间 | `120` 秒 |
| `DEFAULT_COOKIES_FILE` | Cookies 文件路径 | `cookies.txt` |
| `DEFAULT_RECORDS_DIR` | 记录保存目录 | `chat_records` |

### 如何获取 Model ID 和 Project ID

从浏览器 URL 中获取：

```
https://wanqing.corp.kuaishou.com/ai-square/model-preview/mi-zg405a-1772527268195191820
                                                            ^^^^^^^^^^^^^^^^^^^^^^^^
                                                            模型实例 ID (model_id)

?conversationId="mc-xxx"&conversationGroupId="mcg-xxx"
     projectId=proj-e0txp2-1765975764638779355
                                  ^^^^^^^^^^^^^^^^^^^^
                                  项目 ID (project_id)
```

## ⚠️ 注意事项

1. **Cookies 安全**: Cookies 包含认证信息，请妥善保管，不要提交到 Git
2. **Cookies 过期**: Cookies 会过期，使用前建议验证有效性
3. **请求频率**: 避免过于频繁的请求，可能触发限流
4. **续写标识**: 如果需要完整回复，请在请求末尾添加"回答完成后请加上 finished 标识"

## 📄 License

MIT License
