#!/usr/bin/env python3
"""天衍 — 启动入口

用法:
  python run.py                # 直接启动
  uvicorn app.server:app      # 或通过 uvicorn 启动

功能:
  1. 加载 .env 文件 (如果存在)
  2. 检查 Python 版本 (最低 3.10)
  3. 检查关键依赖是否已安装
  4. 校验 API Key 配置 (未配置时打印友好提示, 不阻断启动)
  5. 启动 uvicorn 服务
"""
import os
import sys

# 确保项目根目录在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _load_env():
    """加载 .env 文件 (如果 python-dotenv 可用则用它, 否则手动解析)."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        from dotenv import load_dotenv
        load_dotenv(env_path)
        return
    except ImportError:
        pass
    # 手动解析 .env (不依赖 python-dotenv)
    with open(env_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, val = line.partition("=")
            key = key.strip()
            val = val.strip().strip("'\"")
            # 只设置尚未存在的环境变量 (不覆盖已有的)
            if key and key not in os.environ:
                os.environ[key] = val


def _check_python():
    """检查 Python 版本 (最低 3.10, 与 pydantic v2 要求一致)."""
    v = sys.version_info
    if v < (3, 10):
        print(f"[tianyan] 错误: Python {v.major}.{v.minor} 不支持, 最低需要 3.10")
        print("[tianyan] 下载地址: https://www.python.org/downloads/")
        sys.exit(1)


def _check_deps():
    """检查关键依赖是否已安装."""
    critical = ["fastapi", "uvicorn", "httpx", "pydantic"]
    missing = []
    for pkg in critical:
        try:
            __import__(pkg)
        except ImportError:
            missing.append(pkg)
    if missing:
        print(f"[tianyan] 错误: 缺少关键依赖: {', '.join(missing)}")
        print("[tianyan] 请运行: pip install -r requirements.txt")
        sys.exit(1)
    # litellm 是核心 LLM 接入层
    try:
        import litellm  # noqa: F401
    except ImportError:
        print("[tianyan] 警告: litellm 未安装, LLM 调用将不可用")
        print("[tianyan] 请运行: pip install litellm")


def _check_api_key():
    """检查 API Key 配置, 未配置时打印提示 (不阻断启动)."""
    key_vars = [
        "DEEPSEEK_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY",
        "ANTHROPIC_API_KEY", "DASHSCOPE_API_KEY", "ZAI_API_KEY",
        "MOONSHOT_API_KEY", "XAI_API_KEY", "MISTRAL_API_KEY",
        "VOLCENGINE_API_KEY", "CUSTOM_API_KEY",
    ]
    has_key = any(os.environ.get(k) for k in key_vars)
    # Ollama 不需要 Key
    has_ollama = bool(os.environ.get("OLLAMA_API_BASE"))
    if not has_key and not has_ollama:
        print("\n" + "=" * 60)
        print("  当前模型尚未配置 API Key!")
        print("  配置方式 (任选其一):")
        print("    1. 在 .env 文件中设置环境变量")
        print("       参考 .env.example 模板")
        print("    2. 在 Web 界面右上角 设置 -> 添加模型 -> 填入 API Key")
        print("    3. 在 config.yaml 中填写 api_key 字段")
        print("  推荐用 DeepSeek (国内直连): https://platform.deepseek.com")
        print("=" * 60 + "\n")


def main():
    """启动 uvicorn 服务."""
    _load_env()
    _check_python()
    _check_deps()
    _check_api_key()

    from app.server import app  # noqa: F401  — 确保 app 注册到 uvicorn

    import uvicorn

    host = os.environ.get("SERVER_HOST", "0.0.0.0")
    port = int(os.environ.get("SERVER_PORT", "8000"))

    print(f"[tianyan] 天衍启动中... http://{host}:{port}/")
    uvicorn.run(
        "app.server:app",
        host=host,
        port=port,
        reload=False,
        timeout_keep_alive=600,
        timeout_graceful_shutdown=30,
    )


if __name__ == "__main__":
    main()
