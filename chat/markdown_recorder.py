"""
Wanqing LLM Chat 模块 - Markdown 记录器
支持对话内容去重记录到 Markdown 文件
"""

import os
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import DEFAULT_RECORDS_DIR


class MarkdownRecorder:
    """Markdown 记录器"""
    
    def __init__(self, output_dir: str = DEFAULT_RECORDS_DIR):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
    
    def _generate_filename(self, prefix: str = "chat") -> str:
        """生成带时间戳的文件名"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        return f"{prefix}_{timestamp}.md"
    
    def _deduplicate_content(self, content: str) -> str:
        """
        去除重复字符
        
        检测并去除连续重复的字符，例如：
        - "好好好好" -> "好"
        - "啊啊啊" -> "啊"
        - "123123" -> "123"
        """
        if not content:
            return ""
        
        result = []
        prev_char = None
        count = 0
        
        for char in content:
            if char == prev_char:
                count += 1
                # 只保留第一个连续字符，最多保留2个用于强调
                if count <= 1:
                    result.append(char)
            else:
                result.append(char)
                prev_char = char
                count = 0
        
        return "".join(result)
    
    def _deduplicate_words(self, content: str) -> str:
        """
        去除重复的词语/短语
        
        检测并去除连续重复的词语，例如：
        - "你好 你好 你好" -> "你好"
        """
        if not content:
            return ""
        
        # 按空格分割成词语
        words = content.split()
        if not words:
            return content
        
        result = [words[0]]
        for i in range(1, len(words)):
            # 检查是否与前一个词重复
            if words[i] != words[i-1]:
                result.append(words[i])
            # 如果相同，检查是否连续重复超过2次
            elif len(result) >= 2 and result[-1] == words[i]:
                # 保留，但标记需要去重
                pass
            else:
                result.append(words[i])
        
        return " ".join(result)
    
    def _deduplicate_sentences(self, content: str) -> str:
        """
        去除重复的句子
        
        检测并去除连续重复的完整句子
        """
        if not content:
            return ""
        
        lines = content.split("\n")
        result = []
        seen = set()
        
        for line in lines:
            # 清理空白字符
            cleaned = line.strip()
            if not cleaned:
                result.append(line)  # 保留空行
                continue
            
            # 句子去重（去重前30个字符作为key）
            key = cleaned[:30]
            if key not in seen:
                seen.add(key)
                result.append(line)
        
        return "\n".join(result)
    
    def _clean_content(self, content: str) -> str:
        """
        清理内容：
        1. 去除重复字符
        2. 去除重复词语
        3. 去除重复句子
        4. 清理多余空白
        """
        if not content:
            return ""
        
        # 1. 去除重复字符
        content = self._deduplicate_content(content)
        
        # 2. 去除重复词语
        content = self._deduplicate_words(content)
        
        # 3. 去除重复句子
        content = self._deduplicate_sentences(content)
        
        # 4. 清理多余空白
        content = re.sub(r'\n{3,}', '\n\n', content)  # 超过2个换行改为2个
        content = re.sub(r' {2,}', ' ', content)  # 多个空格改为1个
        
        return content.strip()
    
    def _format_message(self, role: str, content: str, timestamp: str = None) -> str:
        """格式化单条消息"""
        if timestamp is None:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        role_display = {
            "user": "👤 用户",
            "assistant": "🤖 助手",
            "system": "⚙️ 系统"
        }.get(role, role)
        
        return f"### {role_display} ({timestamp})\n\n{content}\n\n---\n"
    
    def _generate_title(self, first_message: str) -> str:
        """根据第一条消息生成标题"""
        # 取前50个字符作为标题
        title = first_message[:50] if len(first_message) > 50 else first_message
        # 清理特殊字符
        title = re.sub(r'[<>:"/\\|?*]', '', title)
        return f"# {title}\n\n"
    
    def record(self, conversation_id: str, messages: list, output_path: str = None) -> str:
        """
        记录对话到 Markdown 文件
        
        Args:
            conversation_id: 会话 ID
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}, ...]
            output_path: 输出文件路径，None 则自动生成
            
        Returns:
            输出的文件路径
        """
        if not messages:
            return ""
        
        # 清理所有消息内容
        cleaned_messages = []
        for msg in messages:
            cleaned_content = self._clean_content(msg.get("content", ""))
            if cleaned_content:
                cleaned_messages.append({
                    "role": msg.get("role", "assistant"),
                    "content": cleaned_content
                })
        
        # 确定输出文件
        if output_path is None:
            output_path = self.output_dir / self._generate_filename(conversation_id[:20])
        else:
            output_path = Path(output_path)
        
        # 生成内容
        lines = []
        
        # 添加标题
        if cleaned_messages:
            first_user_msg = next((m for m in cleaned_messages if m["role"] == "user"), None)
            if first_user_msg:
                lines.append(self._generate_title(first_user_msg["content"]))
        
        # 添加元信息
        lines.append(f"**会话ID**: `{conversation_id}`\n")
        lines.append(f"**记录时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        lines.append(f"**消息数**: {len(cleaned_messages)}\n")
        lines.append("\n---\n\n")
        
        # 添加消息
        for msg in cleaned_messages:
            lines.append(self._format_message(msg["role"], msg["content"]))
        
        # 写入文件
        content = "".join(lines)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(content, encoding="utf-8")
        
        return str(output_path)
    
    def append(self, output_path: str, role: str, content: str) -> None:
        """
        追加单条消息到已有文件
        
        Args:
            output_path: 文件路径
            role: 角色 (user/assistant)
            content: 消息内容
        """
        output_path = Path(output_path)
        if not output_path.exists():
            raise FileNotFoundError(f"文件不存在: {output_path}")
        
        cleaned_content = self._clean_content(content)
        formatted_msg = self._format_message(role, cleaned_content)
        
        with open(output_path, "a", encoding="utf-8") as f:
            f.write(formatted_msg)
    
    def list_records(self) -> list:
        """列出所有记录文件"""
        if not self.output_dir.exists():
            return []
        
        files = sorted(
            self.output_dir.glob("chat_*.md"),
            key=lambda x: x.stat().st_mtime,
            reverse=True
        )
        
        return [
            {
                "path": str(f),
                "name": f.name,
                "size": f.stat().st_size,
                "modified": datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            }
            for f in files
        ]
