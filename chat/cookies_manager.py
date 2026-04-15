"""
Wanqing LLM Chat 模块 - Cookies 管理器
支持自动弹出浏览器登录和手动输入 cookies
"""

import os
import time
from pathlib import Path
from typing import Optional

from .config import DEFAULT_COOKIES_FILE, LOGIN_URL, BASE_URL


class CookiesExpiredError(Exception):
    """Cookies 过期异常"""
    pass


class CookiesManager:
    """Cookies 管理器"""
    
    def __init__(self, cookies_file: str = DEFAULT_COOKIES_FILE):
        self.cookies_file = Path(cookies_file)
    
    def load(self) -> str:
        """加载 cookies"""
        if self.cookies_file.exists():
            cookies = self.cookies_file.read_text().strip()
            if cookies:
                return cookies
        return ""
    
    def save(self, cookies: str) -> None:
        """保存 cookies 到文件"""
        self.cookies_file.parent.mkdir(parents=True, exist_ok=True)
        self.cookies_file.write_text(cookies.strip())
    
    def exists(self) -> bool:
        """检查 cookies 文件是否存在"""
        return self.cookies_file.exists() and bool(self.load())
    
    def interactive_login(self, login_url: str = LOGIN_URL, timeout: int = 300) -> str:
        """
        交互式登录：打开浏览器让用户登录，然后捕获 cookies
        
        Args:
            login_url: 登录页面 URL
            timeout: 超时时间（秒）
            
        Returns:
            捕获的 cookies 字符串
            
        Raises:
            TimeoutError: 等待超时
        """
        import webbrowser
        import selectors
        import sys
        
        print(f"\n{'='*60}")
        print("🔐 Cookies 已过期或不存在，需要重新登录")
        print(f"{'='*60}")
        print(f"\n📌 请按以下步骤操作：")
        print(f"   1. 浏览器将自动打开登录页面")
        print(f"   2. 使用企业账号登录")
        print(f"   3. 登录成功后，在此处输入 'done' 并回车")
        print(f"   4. 或者直接复制 cookies 后输入 'cookies:' 前缀")
        print(f"\n⏱️  超时时间: {timeout} 秒")
        print(f"{'='*60}\n")
        
        # 打开浏览器
        webbrowser.open(login_url)
        
        start_time = time.time()
        selector = selectors.DefaultSelector()
        
        print("⏳ 等待登录完成...")
        
        while time.time() - start_time < timeout:
            elapsed = int(time.time() - start_time)
            remaining = timeout - elapsed
            print(f"\r⏳ 等待中... ({remaining}秒后超时) 输入 'done' 表示登录完成: ", end="", flush=True)
            
            try:
                # 使用 select 等待输入（非阻塞）
                if sys.stdin in selector.get_map():
                    user_input = sys.stdin.readline().strip()
                    if user_input.lower() == "done":
                        print("\n\n✅ 登录完成！")
                        print("\n📋 请选择获取 cookies 的方式：")
                        print("   1. 手动复制浏览器的 cookies（推荐）")
                        print("   2. 如果你已在浏览器中登录，直接按回车")
                        print("\n💡 获取 cookies 方法：")
                        print("   - 打开浏览器开发者工具 (F12)")
                        print("   - 切换到 Network 标签")
                        print("   - 刷新页面，点击任意一个请求")
                        print("   - 在 Headers 中找到 Cookie，复制完整内容")
                        break
                    elif user_input.startswith("cookies:"):
                        # 用户直接提供了 cookies
                        cookies = user_input[8:].strip()
                        self.save(cookies)
                        print(f"\n✅ Cookies 已保存到 {self.cookies_file}")
                        return cookies
                    else:
                        # 当作用户已登录完成
                        break
            except Exception:
                pass
            
            time.sleep(1)
        
        print("\n\n" + "="*60)
        print("📋 手动输入 Cookies")
        print("="*60)
        print("\n请复制浏览器中的完整 cookies 字符串并粘贴：")
        print("(可以在开发者工具 Network 标签中获取)")
        print("\n直接按回车跳过，程序将尝试继续...\n")
        
        try:
            cookies = input(">>> ").strip()
            if cookies:
                self.save(cookies)
                print(f"\n✅ Cookies 已保存到 {self.cookies_file}")
                return cookies
        except EOFError:
            pass
        
        raise TimeoutError("登录超时或未提供 cookies")
    
    def get_with_validation(self, validator_func=None) -> str:
        """
        获取 cookies，如果无效则触发重新登录
        
        Args:
            validator_func: 验证 cookies 有效性的函数
            
        Returns:
            有效的 cookies 字符串
        """
        cookies = self.load()
        
        if not cookies:
            return self.interactive_login()
        
        # 如果提供了验证函数，验证 cookies
        if validator_func and not validator_func(cookies):
            return self.interactive_login()
        
        return cookies
    
    @staticmethod
    def parse_cookies_string(cookies_str: str) -> dict:
        """
        将 cookies 字符串解析为字典
        
        Args:
            cookies_str: "key1=value1; key2=value2" 格式的字符串
            
        Returns:
            {key1: value1, key2: value2}
        """
        result = {}
        for part in cookies_str.split(";"):
            part = part.strip()
            if "=" in part:
                key, value = part.split("=", 1)
                result[key.strip()] = value.strip()
        return result
    
    @staticmethod
    def build_cookie_header(cookies_dict: dict) -> str:
        """
        将 cookies 字典构建为请求头格式
        
        Args:
            cookies_dict: {key: value} 格式的字典
            
        Returns:
            "key1=value1; key2=value2" 格式的字符串
        """
        return "; ".join(f"{k}={v}" for k, v in cookies_dict.items())
