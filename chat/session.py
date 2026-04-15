"""
Wanqing LLM Chat 模块 - Session 会话管理
支持自动续写、消息历史记录
"""

import time
from typing import Optional, Generator, Dict, Any, Callable

from .client import WanqingClient, CookiesExpiredError
from .config import CONTINUATION_KEYWORDS, MAX_CONTINUATIONS, CONTINUATION_MESSAGE
from .markdown_recorder import MarkdownRecorder


class Session:
    """对话会话管理"""
    
    def __init__(
        self,
        client: WanqingClient,
        conversation_id: str = None,
        conversation_group_id: str = None,
        auto_record: bool = True,
        recorder: MarkdownRecorder = None
    ):
        """
        初始化 Session
        
        Args:
            client: WanqingClient 实例
            conversation_id: 会话 ID (None 表示新会话)
            conversation_group_id: 会话组 ID
            auto_record: 是否自动记录到 Markdown
            recorder: Markdown 记录器实例
        """
        self.client = client
        self.auto_record = auto_record
        self.recorder = recorder or MarkdownRecorder()
        
        # 消息历史
        self.messages: list = []
        
        # 设置会话 ID
        if conversation_id:
            self.client.set_conversation(conversation_id, conversation_group_id)
    
    @property
    def conversation_id(self) -> Optional[str]:
        """获取当前会话 ID"""
        return self.client.conversation_id
    
    @property
    def conversation_group_id(self) -> Optional[str]:
        """获取当前会话组 ID"""
        return self.client.conversation_group_id
    
    def _check_finished(self, content: str) -> bool:
        """
        检查内容是否包含完成标识
        
        Args:
            content: 待检查的内容
            
        Returns:
            True 如果检测到完成标识
        """
        content_lower = content.lower()
        for keyword in CONTINUATION_KEYWORDS:
            if keyword.lower() in content_lower:
                return True
        return False
    
    def send_message(
        self,
        message: str,
        max_continuations: int = MAX_CONTINUATIONS,
        print_stream: bool = True,
        stream_callback: Callable[[str], None] = None
    ) -> str:
        """
        发送消息并获取响应（自动续写）
        
        当响应内容很长时，LLM 可能不会一次说完。
        这个方法会自动检测是否需要续写，直到回复完成。
        
        Args:
            message: 用户消息
            max_continuations: 最大续写次数
            print_stream: 是否打印流式输出
            stream_callback: 流式输出的回调函数
            
        Returns:
            完整的响应文本
        """
        # 记录用户消息
        self.messages.append({
            "role": "user",
            "content": message,
            "timestamp": time.time()
        })
        
        full_response = ""
        continuation_count = 0
        last_content = ""
        
        while continuation_count < max_continuations:
            # 发送消息
            if continuation_count == 0:
                response = self.client.chat(message)
            else:
                # 续写
                response = self.client.chat(CONTINUATION_MESSAGE)
            
            if not response:
                break
            
            # 追加响应
            full_response += response
            
            # 打印/回调
            if print_stream:
                print(response, end="", flush=True)
            
            if stream_callback:
                stream_callback(response)
            
            # 记录助手消息
            self.messages.append({
                "role": "assistant",
                "content": full_response,
                "timestamp": time.time()
            })
            
            # 检查是否完成
            if self._check_finished(full_response):
                print("\n\n✅ 检测到回复完成标识 (finished)")
                break
            
            # 检查是否有新内容
            if response == last_content or not response.strip():
                # 内容没有变化，可能已经结束
                break
            
            last_content = response
            continuation_count += 1
            
            print(f"\n\n🔄 自动续写 ({continuation_count}/{max_continuations})...")
            time.sleep(1)  # 等待一下再续写
        
        if continuation_count >= max_continuations:
            print(f"\n⚠️ 达到最大续写次数 ({max_continuations})")
        
        return full_response
    
    def send_message_stream(
        self,
        message: str,
        stream_callback: Callable[[str], None] = None
    ) -> Generator[str, None, None]:
        """
        发送消息并流式返回响应（不自动续写）
        
        Args:
            message: 用户消息
            stream_callback: 每个 chunk 的回调函数
            
        Yields:
            增量文本内容
        """
        # 记录用户消息
        self.messages.append({
            "role": "user",
            "content": message,
            "timestamp": time.time()
        })
        
        full_response = ""
        for chunk in self.client.chat_stream(message):
            full_response += chunk
            
            if stream_callback:
                stream_callback(chunk)
            
            yield chunk
        
        # 记录助手消息
        self.messages.append({
            "role": "assistant",
            "content": full_response,
            "timestamp": time.time()
        })
    
    def continue_reply(
        self,
        print_stream: bool = True,
        stream_callback: Callable[[str], None] = None
    ) -> str:
        """
        继续上一次未完成的回复
        
        Args:
            print_stream: 是否打印流式输出
            stream_callback: 流式输出的回调函数
            
        Returns:
            继续的响应内容
        """
        response = self.client.chat(CONTINUATION_MESSAGE)
        
        if not response:
            return ""
        
        if print_stream:
            print(response, end="", flush=True)
        
        if stream_callback:
            stream_callback(response)
        
        # 更新最后一条助手消息
        if self.messages and self.messages[-1]["role"] == "assistant":
            self.messages[-1]["content"] += response
        else:
            self.messages.append({
                "role": "assistant",
                "content": response,
                "timestamp": time.time()
            })
        
        return response
    
    def get_history(self) -> list:
        """
        获取对话历史（不含时间戳，用于 Markdown 记录）
        
        Returns:
            消息列表，格式为 [{"role": "user", "content": "..."}, ...]
        """
        return [
            {"role": msg["role"], "content": msg["content"]}
            for msg in self.messages
            if msg.get("content")
        ]
    
    def get_history_with_time(self) -> list:
        """
        获取对话历史（带时间戳）
        
        Returns:
            消息列表，格式为 [{"role": "user", "content": "...", "timestamp": 1234567890}, ...]
        """
        return self.messages.copy()
    
    def clear_history(self) -> None:
        """清空对话历史"""
        self.messages = []
        self.client.reset_conversation()
    
    def record_to_markdown(self, output_path: str = None) -> str:
        """
        将对话记录到 Markdown 文件
        
        Args:
            output_path: 输出文件路径，None 则自动生成
            
        Returns:
            输出的文件路径
        """
        if not self.messages:
            return ""
        
        messages_for_record = self.get_history()
        return self.recorder.record(
            self.conversation_id or "new_conversation",
            messages_for_record,
            output_path
        )
    
    def __repr__(self) -> str:
        return f"<Session conversation_id={self.conversation_id} messages={len(self.messages)}>"


class SessionManager:
    """会话管理器 - 管理多个会话"""
    
    def __init__(self, client: WanqingClient):
        self.client = client
        self.sessions: Dict[str, Session] = {}
        self.current_session: Optional[Session] = None
    
    def create_session(self) -> Session:
        """创建新会话"""
        self.client.create_conversation()
        session = Session(self.client)
        self.sessions[session.conversation_id] = session
        self.current_session = session
        return session
    
    def get_session(self, conversation_id: str) -> Optional[Session]:
        """获取指定会话"""
        return self.sessions.get(conversation_id)
    
    def switch_session(self, conversation_id: str) -> Optional[Session]:
        """切换到指定会话"""
        session = self.sessions.get(conversation_id)
        if session:
            self.current_session = session
            self.client.set_conversation(
                session.conversation_id,
                session.conversation_group_id
            )
        return session
    
    def list_sessions(self) -> list:
        """列出所有会话"""
        return [
            {
                "conversation_id": s.conversation_id,
                "conversation_group_id": s.conversation_group_id,
                "message_count": len(s.messages)
            }
            for s in self.sessions.values()
            if s.conversation_id
        ]
