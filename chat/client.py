"""
Wanqing LLM Chat 模块 - WanqingClient 主客户端
支持流式对话、Cookies 管理和会话操作
"""

import json
import re
import time
import uuid
from typing import Optional, Generator, Dict, Any, Callable

import requests

from .config import (
    BASE_URL, API_GATEWAY, API_CONVERSATIONS, API_MODEL_INSTANCE,
    API_MODEL_CAPABILITIES, DEFAULT_HEADERS, DEFAULT_USER_ID,
    DEFAULT_PROJECT_ID, REQUEST_TIMEOUT, MAX_RETRIES, EVENT_MODEL_MESSAGE,
    EVENT_DONE
)
from .cookies_manager import CookiesManager, CookiesExpiredError


class WanqingClient:
    """Wanqing LLM 客户端"""
    
    def __init__(
        self,
        cookies: str,
        model_id: str,
        project_id: str = DEFAULT_PROJECT_ID,
        user_id: str = DEFAULT_USER_ID,
        cookies_manager: CookiesManager = None
    ):
        """
        初始化 WanqingClient
        
        Args:
            cookies: Cookies 字符串
            model_id: 模型实例 ID (如 mi-zg405a-1772527268195191820)
            project_id: 项目 ID
            user_id: 用户 ID
            cookies_manager: Cookies 管理器（用于过期时重新登录）
        """
        self.cookies = cookies
        self.model_id = model_id
        self.project_id = project_id
        self.user_id = user_id
        self.cookies_manager = cookies_manager
        
        # 会话相关的 ID
        self.conversation_id: Optional[str] = None
        self.conversation_group_id: Optional[str] = None
        self.parent_message_id: Optional[str] = None
        
        # 创建请求会话
        self.session = requests.Session()
    
    def _get_headers(self, additional_headers: dict = None) -> dict:
        """构建请求头"""
        headers = DEFAULT_HEADERS.copy()
        headers["Cookie"] = self.cookies
        
        # 添加会话相关的 headers
        if self.conversation_id:
            headers["x-ks-wq-conversation-id"] = self.conversation_id
        if self.conversation_group_id:
            headers["x-ks-wq-conversation-group-id"] = self.conversation_group_id
        if self.parent_message_id:
            headers["x-ks-wq-parent-message-id"] = self.parent_message_id
        
        headers["x-ks-wq-end-user-id"] = self.user_id
        headers["x-ks-wq-message-channel"] = "web"
        
        # 添加 ktrace-str (可以随机生成或保持固定)
        headers["ktrace-str"] = f"1|MS.7265767945876966.{uuid.uuid4().hex[:8]}|MS.7265767945876966.{uuid.uuid4().hex[:8]}|0|halo-web|infra|true|NA"
        
        if additional_headers:
            headers.update(additional_headers)
        
        return headers
    
    def _build_message_payload(self, message: str) -> dict:
        """构建消息 payload"""
        return {
            "model": self.model_id,
            "stream": True,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": message}
                    ]
                }
            ],
            "enableWebSearch": True,
            "modelParameters": {},
            "projectId": self.project_id
        }
    
    def _parse_sse_line(self, line: str) -> Optional[dict]:
        """
        解析 SSE 行
        
        格式: message {"event":"...", ...}
        """
        if not line.startswith("message "):
            return None
        
        try:
            data_str = line[8:].strip()  # 去掉 "message " 前缀
            return json.loads(data_str)
        except json.JSONDecodeError:
            return None
    
    def _parse_stream_response(self, response) -> Generator[dict, None, None]:
        """解析流式响应"""
        for line in response.iter_lines(decode_unicode=True):
            if not line:
                continue
            
            # 处理 SSE 格式
            data = self._parse_sse_line(line)
            if data:
                yield data
    
    def _stream_chat(self, message: str) -> Generator[str, None, None]:
        """
        发送消息并获取流式响应
        
        Args:
            message: 用户消息
            
        Yields:
            增量文本内容
        """
        url = API_GATEWAY
        payload = self._build_message_payload(message)
        headers = self._get_headers()
        
        # 构建 Referer
        referer = f"{BASE_URL}/ai-square/model-preview/{self.model_id}"
        if self.conversation_id:
            referer += f"?conversationId=%22{self.conversation_id}%22"
            if self.conversation_group_id:
                referer += f"&conversationGroupId=%22{self.conversation_group_id}%22"
        headers["Referer"] = referer
        
        response = self.session.post(
            url,
            json=payload,
            headers=headers,
            stream=True,
            timeout=REQUEST_TIMEOUT
        )
        
        # 检查响应状态
        if response.status_code in (401, 403):
            raise CookiesExpiredError("Cookies 已过期，请重新登录")
        
        if response.status_code != 200:
            raise RuntimeError(f"请求失败: HTTP {response.status_code}")
        
        # 解析流式响应
        buffer = ""
        for data in self._parse_stream_response(response):
            event = data.get("event")
            
            if event == EVENT_MODEL_MESSAGE:
                choices = data.get("choices", [])
                if choices:
                    delta = choices[0].get("delta", {})
                    content = delta.get("content")
                    
                    if content:
                        buffer += content
                        yield content
                    
                    # 检测是否结束
                    finish_reason = choices[0].get("finish_reason")
                    if finish_reason == "stop":
                        break
                    
                    # 更新会话 ID
                    if not self.conversation_id:
                        self.conversation_id = data.get("conversation_id")
                    if not self.conversation_group_id:
                        self.conversation_group_id = data.get("conversation_id", "").split("-")[0]
                    
                    # 更新父消息 ID
                    self.parent_message_id = data.get("message_id")
            
            elif event == EVENT_DONE:
                break
        
        # 返回完整的 buffer
        return buffer
    
    def chat(self, message: str) -> str:
        """
        发送消息并返回完整响应
        
        Args:
            message: 用户消息
            
        Returns:
            完整的响应文本
        """
        buffer = ""
        for chunk in self._stream_chat(message):
            buffer += chunk
        
        return buffer
    
    def chat_stream(self, message: str) -> Generator[str, None, None]:
        """
        发送消息并流式返回响应（增量式）
        
        Args:
            message: 用户消息
            
        Yields:
            增量文本内容
        """
        yield from self._stream_chat(message)
    
    def validate_cookies(self) -> bool:
        """
        验证 cookies 是否有效
        
        Returns:
            True 如果有效，False 否则
        """
        try:
            url = f"{API_MODEL_INSTANCE}/{self.model_id}"
            headers = self._get_headers({
                "Accept": "application/json, text/plain, */*",
                "X-Requested-With": "XMLHttpRequest"
            })
            headers["Referer"] = f"{BASE_URL}/ai-square/model-preview/{self.model_id}"
            
            response = self.session.get(url, headers=headers, timeout=10)
            
            if response.status_code in (401, 403):
                return False
            
            return response.status_code == 200
        except Exception:
            return False
    
    def create_conversation(self) -> Dict[str, Any]:
        """
        创建新会话
        
        Returns:
            创建的会话信息
        """
        # 重置会话 ID
        self.conversation_id = None
        self.conversation_group_id = None
        self.parent_message_id = None
        
        return {
            "conversation_id": None,
            "conversation_group_id": None,
            "parent_message_id": None
        }
    
    def list_conversations(
        self,
        page: int = 1,
        page_size: int = 20,
        query: str = ""
    ) -> Dict[str, Any]:
        """
        获取会话列表
        
        Args:
            page: 页码
            page_size: 每页数量
            query: 搜索关键词
            
        Returns:
            会话列表数据
        """
        url = API_CONVERSATIONS
        params = {
            "projectId": self.project_id,
            "modelInstanceIds": self.model_id,
            "query": query,
            "page": page,
            "pageSize": page_size
        }
        
        headers = self._get_headers({
            "Accept": "application/json, text/plain, */*",
            "X-Requested-With": "XMLHttpRequest"
        })
        headers["Referer"] = f"{BASE_URL}/ai-square/model-preview/{self.model_id}"
        
        response = self.session.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )
        
        if response.status_code in (401, 403):
            raise CookiesExpiredError("Cookies 已过期，请重新登录")
        
        if response.status_code != 200:
            raise RuntimeError(f"请求失败: HTTP {response.status_code}")
        
        return response.json()
    
    def get_conversation_messages(self, conversation_id: str) -> list:
        """
        获取指定会话的消息历史
        
        Args:
            conversation_id: 会话 ID
            
        Returns:
            消息列表
        """
        # 这个 API 可能需要根据实际接口调整
        # 目前暂不支持，需要后续补充
        return []
    
    def set_conversation(self, conversation_id: str, conversation_group_id: str = None) -> None:
        """
        设置当前会话
        
        Args:
            conversation_id: 会话 ID
            conversation_group_id: 会话组 ID
        """
        self.conversation_id = conversation_id
        self.conversation_group_id = conversation_group_id or conversation_id.split("-")[0]
        self.parent_message_id = None  # 新会话没有父消息
    
    def reset_conversation(self) -> None:
        """重置当前会话，创建新对话"""
        self.conversation_id = None
        self.conversation_group_id = None
        self.parent_message_id = None
