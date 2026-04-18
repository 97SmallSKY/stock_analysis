#!/usr/bin/env python
"""scripts/dump_openapi.py — W12-6 导出 OpenAPI JSON

用法:
    python scripts/dump_openapi.py > docs/openapi.json
    python scripts/dump_openapi.py --out docs/openapi.json
    python scripts/dump_openapi.py --yaml > docs/openapi.yaml

输出:
    默认打印到 stdout (JSON).
    --yaml 输出 YAML (需 PyYAML).

设计:
    - 动态 import backend.api.main, 取 app.openapi() dict
    - 注入版本/title 元信息
    - 不依赖真 DB 连接 (main 里只 include_router, 不走 pg_conn)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

# 让脚本可直接 `python scripts/dump_openapi.py` 运行 (无需 PYTHONPATH)
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

# 关键: 必须在 import backend.api.main 之前劫持 configure_logging,
# 否则默认 stream=sys.stdout 会污染 JSON 输出.
import backend.core.startup as _bs
_bs.configure_logging = lambda level="WARNING": logging.basicConfig(
    level=logging.WARNING, stream=sys.stderr, force=True,
)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description="导出 FastAPI OpenAPI spec")
    p.add_argument("--out", help="写入文件 (默认 stdout)")
    p.add_argument("--yaml", action="store_true", help="YAML 输出 (需 PyYAML)")
    p.add_argument("--indent", type=int, default=2)
    args = p.parse_args(argv)

    from backend.api.main import app
    spec = app.openapi()

    if args.yaml:
        try:
            import yaml  # type: ignore
        except ImportError:
            print("[dump_openapi] ERROR: 需要 pip install pyyaml", file=sys.stderr)
            return 2
        text = yaml.safe_dump(spec, allow_unicode=True, sort_keys=False)
    else:
        text = json.dumps(spec, ensure_ascii=False, indent=args.indent)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text)
        print(f"[dump_openapi] wrote -> {args.out}", file=sys.stderr)
    else:
        sys.stdout.write(text)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
