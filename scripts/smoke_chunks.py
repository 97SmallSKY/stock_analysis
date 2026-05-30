#!/usr/bin/env python3
"""smoke_chunks.py — L2 前端资源完整性检查

用法:
  python3 scripts/smoke_chunks.py [--base-url http://117.72.184.8:8000]

原理:
  用 Playwright 加载前端页面，监听 page.on('response') 事件。
  任何子请求 status >= 400 即视为失败（主要捕获懒加载 chunk 404）。

运行环境:
  本机执行（不在 jd 服务器上跑）。本机需安装 playwright + chromium，
  且能访问 base_url（jd 内网地址 117.72.184.8:8000）。

安装:
  pip install playwright && playwright install chromium

退出码:
  0 = 全部通过
  1 = 有资源 404/错误
  2 = 环境问题（playwright 未安装等）
"""
import argparse
import sys

ROUTES = [
    "/",
    "/lhb",
    "/signals",
    "/lhb-pnl",
    "/backtest",
    "/backtest-run",
    "/backtest-history",
    "/robots",
    "/reports",
    "/data-sources",
]


def check_route(page, base_url: str, route: str) -> list[dict]:
    failures = []

    def on_response(response):
        if response.status >= 400:
            failures.append({
                "url": response.url,
                "status": response.status,
                "route": route,
            })

    page.on("response", on_response)
    try:
        page.goto(f"{base_url}{route}", timeout=15000, wait_until="networkidle")
    except Exception as e:
        failures.append({"url": f"{base_url}{route}", "status": -1, "error": str(e), "route": route})
    finally:
        page.remove_listener("response", on_response)

    return failures


def main():
    parser = argparse.ArgumentParser(description="L2 前端资源完整性 smoke 检查")
    parser.add_argument("--base-url", default="http://117.72.184.8:8000", help="服务地址")
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("ERROR: playwright 未安装。请在本机运行:", file=sys.stderr)
        print("  pip install playwright && playwright install chromium", file=sys.stderr)
        sys.exit(2)

    all_failures = []
    print(f"==> L2 smoke: 检查 {len(ROUTES)} 个路由的前端资源完整性")
    print(f"==> base_url: {args.base_url}")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context()
        page = context.new_page()

        for route in ROUTES:
            failures = check_route(page, args.base_url, route)
            if failures:
                for f in failures:
                    print(f"  FAIL {f['route']} → {f['url']} [{f['status']}]")
                all_failures.extend(failures)
            else:
                print(f"  PASS {route}")

        browser.close()

    if all_failures:
        print(f"\n==> L2 smoke FAILED: {len(all_failures)} 个资源错误")
        for f in all_failures:
            print(f"  {f['status']} {f['url']}")
        sys.exit(1)
    else:
        print(f"\n==> L2 smoke PASSED: 所有 {len(ROUTES)} 个路由的前端资源正常")
        sys.exit(0)


if __name__ == "__main__":
    main()
