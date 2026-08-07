"""沙箱代码执行器 — 使用 firejail + pypy3 隔离执行用户代码.

设计:
- 系统级隔离: firejail --net=none --noroot (断网 + 降权)
- Python 级隔离: pypy3 (独立解释器, 不共享 GIL)
- 超时控制: subprocess timeout (默认 30s)
- 安全限制: RestrictedPython 预检 (拦截危险 import/eval/exec)

降级策略:
- firejail 不可用 → 仅用 pypy3 (无系统级隔离, 记录警告)
- pypy3 不可用 → 用 venv 内 python3 + RestrictedPython (无系统级隔离)
- RestrictedPython 不可用 → 仅用 subprocess + 超时 (最弱隔离)
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from typing import Optional

logger = logging.getLogger("tianyan.sandbox")

# 沙箱配置 (从环境变量读取, 有默认值)
SANDBOX_ENABLED = os.environ.get("SANDBOX_ENABLED", "true").lower() in ("true", "1", "yes")
SANDBOX_TIMEOUT = int(os.environ.get("SANDBOX_TIMEOUT", "30"))


def _check_firejail() -> bool:
    """检查 firejail 是否可用."""
    return shutil.which("firejail") is not None


def _check_pypy3() -> bool:
    """检查 pypy3 是否可用."""
    return shutil.which("pypy3") is not None


def _check_restrictedpython() -> bool:
    """检查 RestrictedPython 是否已安装."""
    try:
        import RestrictedPython  # noqa: F401
        return True
    except ImportError:
        return False


def _precheck_code(code: str) -> Optional[str]:
    """用 RestrictedPython 预检代码, 返回错误消息或 None.

    拦截:
    - RestrictedPython 编译器: eval, exec, __import__ 等
    - 手动检查: import os/subprocess/shutil/socket 等危险模块
    """
    # 1. 手动检查危险 import (RestrictedPython 不拦截普通 import)
    dangerous_modules = {
        'os', 'subprocess', 'shutil', 'socket', 'http', 'urllib',
        'requests', 'httpx', 'aiohttp', 'ctypes', 'signal',
        'multiprocessing', 'threading', 'importlib', 'code',
        'codeop', 'compileall', 'zipimport', 'pkgutil',
    }
    import re as _re
    # 匹配 `import xxx` 和 `from xxx import yyy` (支持缩进)
    for m in _re.finditer(r'^\s*(?:import|from)\s+(\w+)', code, _re.MULTILINE):
        mod = m.group(1)
        if mod in dangerous_modules:
            return f"代码预检失败: 禁止导入模块 '{mod}' (沙箱安全限制)"

    # 额外检查: __import__ 调用 (绕过普通 import 语句的方式)
    if _re.search(r'__import__\s*\(', code):
        return "代码预检失败: 禁止使用 __import__ (沙箱安全限制)"

    # 2. RestrictedPython 编译器检查 (eval, exec, __import__ 等)
    if not _check_restrictedpython():
        return None  # RestrictedPython 不可用, 跳过编译检查
    try:
        from RestrictedPython import compile_restricted
        bytecode = compile_restricted(code, "<sandbox>", "exec")
        if hasattr(bytecode, "errors") and bytecode.errors:
            return f"代码预检失败: {'; '.join(bytecode.errors)}"
        return None
    except Exception as e:
        return f"代码预检异常: {e}"


def execute_code(
    code: str,
    *,
    timeout: Optional[int] = None,
    stdin: Optional[str] = None,
) -> dict:
    """在沙箱中执行 Python 代码.

    参数:
        code: 要执行的 Python 代码
        timeout: 超时秒数 (默认从 SANDBOX_TIMEOUT 环境变量读取)
        stdin: 传给代码的标准输入

    返回:
        {
            "ok": bool,           # 是否成功 (exit code 0)
            "stdout": str,        # 标准输出
            "stderr": str,        # 标准错误
            "duration_ms": int,   # 执行耗时 (毫秒)
            "timed_out": bool,    # 是否超时
            "sandbox": str,       # 使用的沙箱级别
        }
    """
    if not SANDBOX_ENABLED:
        return {
            "ok": False,
            "stdout": "",
            "stderr": "沙箱已禁用 (SANDBOX_ENABLED=false)",
            "duration_ms": 0,
            "timed_out": False,
            "sandbox": "disabled",
        }

    timeout = timeout or SANDBOX_TIMEOUT

    # 1. RestrictedPython 预检
    precheck_err = _precheck_code(code)
    if precheck_err:
        return {
            "ok": False,
            "stdout": "",
            "stderr": precheck_err,
            "duration_ms": 0,
            "timed_out": False,
            "sandbox": "restrictedpython-precheck",
        }

    # 2. 写入临时文件
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".py", prefix="sandbox_", delete=False, encoding="utf-8"
    ) as f:
        f.write(code)
        script_path = f.name

    try:
        # 3. 构建执行命令 (根据可用工具选择沙箱级别)
        has_firejail = _check_firejail()
        has_pypy3 = _check_pypy3()

        if has_firejail and has_pypy3:
            # 最强隔离: firejail (断网+降权) + pypy3 (独立解释器)
            cmd = [
                "firejail",
                "--net=none",       # 断网
                "--noroot",          # 非 root 运行
                "--quiet",
                "pypy3", script_path,
            ]
            sandbox_level = "firejail+pypy3"
        elif has_firejail:
            # firejail + venv python
            venv_python = os.environ.get("VIRTUAL_ENV", "/app/venv") + "/bin/python"
            if not os.path.exists(venv_python):
                venv_python = sys.executable
            cmd = [
                "firejail",
                "--net=none",
                "--noroot",
                "--quiet",
                venv_python, script_path,
            ]
            sandbox_level = "firejail+python"
        elif has_pypy3:
            # pypy3 (无 firejail 系统级隔离)
            cmd = ["pypy3", script_path]
            sandbox_level = "pypy3-only"
            logger.warning("[sandbox] firejail 不可用, 仅用 pypy3 (无系统级隔离)")
        else:
            # 最弱: 直接用 venv python (有超时控制)
            venv_python = os.environ.get("VIRTUAL_ENV", "/app/venv") + "/bin/python"
            if not os.path.exists(venv_python):
                venv_python = sys.executable
            cmd = [venv_python, script_path]
            sandbox_level = "python-only"
            logger.warning("[sandbox] firejail 和 pypy3 均不可用, 仅用 subprocess 超时控制")

        # 4. 执行
        t0 = time.time()
        try:
            result = subprocess.run(
                cmd,
                input=stdin,
                capture_output=True,
                text=True,
                timeout=timeout,
                env={
                    "PATH": "/usr/local/bin:/usr/bin:/bin",
                    "HOME": "/tmp",
                    "LANG": "C.UTF-8",
                },
            )
            duration_ms = int((time.time() - t0) * 1000)
            return {
                "ok": result.returncode == 0,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration_ms": duration_ms,
                "timed_out": False,
                "sandbox": sandbox_level,
            }
        except subprocess.TimeoutExpired as e:
            duration_ms = int((time.time() - t0) * 1000)
            return {
                "ok": False,
                "stdout": e.stdout or "",
                "stderr": (e.stderr or "") + f"\n[沙箱] 执行超时 ({timeout}s)",
                "duration_ms": duration_ms,
                "timed_out": True,
                "sandbox": sandbox_level,
            }
        except Exception as e:
            duration_ms = int((time.time() - t0) * 1000)
            return {
                "ok": False,
                "stdout": "",
                "stderr": f"[沙箱] 执行异常: {e}",
                "duration_ms": duration_ms,
                "timed_out": False,
                "sandbox": sandbox_level,
            }
    finally:
        # 清理临时文件
        try:
            os.unlink(script_path)
        except OSError:
            pass


async def execute_code_async(
    code: str,
    *,
    timeout: Optional[int] = None,
    stdin: Optional[str] = None,
) -> dict:
    """异步包装: 在线程池中执行沙箱代码 (不阻塞事件循环)."""
    loop = asyncio.get_event_loop()
    return await loop.run_in_executor(
        None, lambda: execute_code(code, timeout=timeout, stdin=stdin)
    )
