"""
Wanqing LLM Chat 模块 - 使用示例
"""

from chat import WanqingClient, Session, CookiesManager, MarkdownRecorder, CookiesExpiredError


def example_basic_usage():
    """基本用法"""
    print("=" * 60)
    print("示例 1: 基本用法")
    print("=" * 60)
    
    # 1. 初始化 Cookies 管理器
    cookies_mgr = CookiesManager()
    
    # 2. 获取 cookies（如果无效会自动弹出登录）
    cookies = cookies_mgr.get_with_validation()
    
    # 3. 初始化客户端
    # model_id: 模型实例 ID，从 URL 中获取，如 mi-dphy15-1770365346089864936
    # project_id: 项目 ID，如 proj-e0txp2-1765975764638779355
    client = WanqingClient(
        cookies=cookies,
        model_id="mi-zg405a-1772527268195191820",
        project_id="proj-e0txp2-1765975764638779355"
    )
    
    # 4. 创建会话
    session = Session(client)
    
    # 5. 发送消息（自动续写）
    print("\n🤖 助手回复:")
    response = session.send_message("你好，请介绍一下你自己")
    print(f"\n\n📝 完整回复: {response}")
    
    # 6. 继续对话
    response2 = session.send_message("请继续详细说说你的能力")
    print(f"\n📝 回复2: {response2}")


def example_auto_record():
    """自动记录到 Markdown"""
    print("\n" + "=" * 60)
    print("示例 2: 自动记录到 Markdown")
    print("=" * 60)
    
    cookies_mgr = CookiesManager()
    cookies = cookies_mgr.load()
    client = WanqingClient(
        cookies=cookies,
        model_id="mi-zg405a-1772527268195191820",
        project_id="proj-e0txp2-1765975764638779355"
    )
    
    session = Session(client, auto_record=True)
    
    # 发送消息
    session.send_message("请解释什么是机器学习")
    
    # 记录到 Markdown
    output_path = session.record_to_markdown()
    print(f"\n📄 已记录到: {output_path}")


def example_list_conversations():
    """获取会话列表"""
    print("\n" + "=" * 60)
    print("示例 3: 获取会话列表")
    print("=" * 60)
    
    cookies_mgr = CookiesManager()
    cookies = cookies_mgr.load()
    client = WanqingClient(
        cookies=cookies,
        model_id="mi-zg405a-1772527268195191820",
        project_id="proj-e0txp2-1765975764638779355"
    )
    
    # 获取会话列表
    result = client.list_conversations(page=1, page_size=10)
    print(f"\n会话列表: {result}")


def example_manual_cookies():
    """手动提供 cookies"""
    print("\n" + "=" * 60)
    print("示例 4: 手动提供 cookies")
    print("=" * 60)
    
    # 如果你已经有 cookies，可以直接使用
    cookies = """
    weblogger_did=web_xxx;
    coolux-userInfo-token=eyJxxx;
    username=wb_zhangxiaotian03;
    """
    
    # 保存 cookies
    cookies_mgr = CookiesManager()
    cookies_mgr.save(cookies)
    
    # 使用
    client = WanqingClient(
        cookies=cookies,
        model_id="mi-zg405a-1772527268195191820",
        project_id="proj-e0txp2-1765975764638779355"
    )
    
    print("✅ Cookies 已设置")


def example_stream_output():
    """自定义流式输出"""
    print("\n" + "=" * 60)
    print("示例 5: 自定义流式输出回调")
    print("=" * 60)
    
    cookies_mgr = CookiesManager()
    cookies = cookies_mgr.load()
    client = WanqingClient(
        cookies=cookies,
        model_id="mi-zg405a-1772527268195191820",
        project_id="proj-e0txp2-1765975764638779355"
    )
    
    session = Session(client)
    
    def my_callback(chunk):
        """自定义处理每个 chunk"""
        print(f"[收到] {chunk}", end="", flush=True)
    
    session.send_message(
        "讲一个关于AI的简短故事",
        print_stream=False,  # 禁用默认打印
        stream_callback=my_callback
    )


def example_new_conversation():
    """创建新会话"""
    print("\n" + "=" * 60)
    print("示例 6: 创建新会话")
    print("=" * 60)
    
    cookies_mgr = CookiesManager()
    cookies = cookies_mgr.load()
    client = WanqingClient(
        cookies=cookies,
        model_id="mi-zg405a-1772527268195191820",
        project_id="proj-e0txp2-1765975764638779355"
    )
    
    # 创建新会话（重置 conversation_id）
    client.create_conversation()
    session = Session(client)
    
    print(f"新会话 ID: {session.conversation_id}")
    
    response = session.send_message("这是一个新对话的开始")
    print(f"回复: {response}")


def example_long_content_with_continuation():
    """长内容自动续写"""
    print("\n" + "=" * 60)
    print("示例 7: 长内容自动续写")
    print("=" * 60)
    
    cookies_mgr = CookiesManager()
    cookies = cookies_mgr.load()
    client = WanqingClient(
        cookies=cookies,
        model_id="mi-zg405a-1772527268195191820",
        project_id="proj-e0txp2-1765975764638779355"
    )
    
    session = Session(client)
    
    # 发送长内容请求
    prompt = """
    请写一篇详细的文章介绍以下主题：
    1. Python 编程语言的历史
    2. Python 的主要特性和应用场景
    3. Python 在数据科学领域的应用
    4. Python 的未来发展趋势
    
    请确保内容详尽完整，回答完成后请在最后加上 'finished' 标识。
    """
    
    print("📝 发送长内容请求（会自动续写直到检测到 finished）...")
    response = session.send_message(prompt, max_continuations=5)
    print(f"\n\n📄 最终回复长度: {len(response)} 字符")
    
    # 记录
    path = session.record_to_markdown()
    print(f"📄 已记录到: {path}")


if __name__ == "__main__":
    # 选择要运行的示例
    print("\n请选择要运行的示例：")
    print("1. 基本用法")
    print("2. 自动记录到 Markdown")
    print("3. 获取会话列表")
    print("4. 手动提供 cookies")
    print("5. 自定义流式输出")
    print("6. 创建新会话")
    print("7. 长内容自动续写")
    
    choice = input("\n请输入选项 (1-7): ").strip()
    
    examples = {
        "1": example_basic_usage,
        "2": example_auto_record,
        "3": example_list_conversations,
        "4": example_manual_cookies,
        "5": example_stream_output,
        "6": example_new_conversation,
        "7": example_long_content_with_continuation,
    }
    
    if choice in examples:
        try:
            examples[choice]()
        except CookiesExpiredError:
            print("\n⚠️ Cookies 已过期，请重新登录后重试")
        except Exception as e:
            print(f"\n❌ 发生错误: {e}")
    else:
        print("无效的选项")
