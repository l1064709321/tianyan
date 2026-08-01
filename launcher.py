#!/usr/bin/env python3
"""天衍 启动器 — 卡片界面

启动后:
1. 提供卡片 HTML 界面 (http://localhost:9999/)
2. 自动打开浏览器到卡片页面
3. 用户点「启动并进入」→ 后端 subprocess 启动主服务 (na start)
4. 启动成功后自动跳转到 http://localhost:8000/
"""
import http.server
import socketserver
import subprocess
import os
import sys
import tempfile
import webbrowser
import time
import json
from urllib.request import urlopen
from urllib.error import URLError

PORT = 9999
APP_PORT = 8000
APP_URL = f"http://localhost:{APP_PORT}/"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
NA = os.path.join(SCRIPT_DIR, "na")


def is_app_running() -> bool:
    try:
        with urlopen(f"{APP_URL}api/health", timeout=1) as r:
            return r.status == 200
    except (URLError, ConnectionError, OSError):
        return False


def start_app() -> bool:
    """启动主服务,最多等待 20 秒。"""
    if is_app_running():
        return True
    if not os.path.exists(NA):
        return False
    # 后台启动 na start
    subprocess.Popen(
        [NA, "start"],
        cwd=SCRIPT_DIR,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # 等待就绪
    for _ in range(20):
        if is_app_running():
            return True
        time.sleep(1)
    return is_app_running()


HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>天衍 启动器</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    font-family: -apple-system, "PingFang SC", "Microsoft YaHei", sans-serif;
    background: linear-gradient(135deg, #f6efd9 0%, #efe4c5 100%);
    min-height: 100vh;
    display: flex; align-items: center; justify-content: center;
    padding: 20px;
  }
  .card {
    background: #fff;
    border-radius: 20px;
    padding: 48px 44px;
    box-shadow: 0 24px 60px rgba(46,31,21,.16), 0 2px 8px rgba(46,31,21,.06);
    text-align: center;
    max-width: 440px;
    width: 100%;
    border: 1px solid #e6d6ac;
  }
  .logo {
    font-size: 64px;
    line-height: 1;
    margin-bottom: 18px;
    color: #a13d3d;
    font-weight: 700;
  }
  .title {
    font-size: 28px;
    color: #2e1f15;
    margin-bottom: 6px;
    font-weight: 700;
    letter-spacing: .5px;
  }
  .subtitle {
    color: #9a8062;
    margin-bottom: 28px;
    font-size: 13px;
  }
  .status {
    display: inline-block;
    padding: 6px 14px;
    border-radius: 16px;
    background: #fde8e8;
    color: #8b2222;
    margin-bottom: 24px;
    font-size: 13px;
    font-weight: 500;
  }
  .status.running { background: #e6f4e6; color: #2d6a2d; }
  .status.checking { background: #fef3d6; color: #9a6b1f; }
  .btn {
    display: block;
    width: 100%;
    padding: 14px 24px;
    border: none;
    border-radius: 12px;
    font-size: 16px;
    font-weight: 600;
    cursor: pointer;
    transition: all .2s;
    font-family: inherit;
  }
  .btn-primary { background: #a13d3d; color: #fff; }
  .btn-primary:hover:not(:disabled) { background: #8b2e2e; transform: translateY(-1px); box-shadow: 0 6px 16px rgba(161,61,61,.3); }
  .btn-secondary { background: #5a6b3f; color: #fff; margin-top: 10px; }
  .btn-secondary:hover { background: #4a5b2f; transform: translateY(-1px); }
  .btn:disabled { opacity: .6; cursor: not-allowed; transform: none; }
  .hint {
    margin-top: 18px;
    font-size: 12px;
    color: #9a8062;
    min-height: 16px;
  }
  .spinner {
    display: inline-block;
    width: 12px; height: 12px;
    border: 2px solid #fff;
    border-top-color: transparent;
    border-radius: 50%;
    animation: spin .8s linear infinite;
    vertical-align: middle;
    margin-right: 6px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (max-width: 480px) {
    .card { padding: 36px 28px; }
    .logo { font-size: 56px; }
    .title { font-size: 24px; }
  }
</style>
</head>
<body>
<div class="card">
  <div class="logo">✦</div>
  <div class="title">天衍</div>
  <div class="subtitle">小说创作 8 阶段工作流 · 技能市场</div>
  <div class="status checking" id="status">● 检查中...</div>
  <button class="btn btn-primary" id="launchBtn" onclick="launch()" style="display:none">
    启动并进入
  </button>
  <button class="btn btn-secondary" id="enterBtn" onclick="enter()" style="display:none">
    直接进入应用
  </button>
  <div class="hint" id="hint"></div>
</div>
<script>
async function checkStatus() {
  const s = document.getElementById('status');
  const launchBtn = document.getElementById('launchBtn');
  const enterBtn = document.getElementById('enterBtn');
  s.className = 'status checking';
  s.textContent = '● 检查中...';
  try {
    const r = await fetch('/api/status');
    const d = await r.json();
    if (d.running) {
      s.className = 'status running';
      s.textContent = '● 服务运行中';
      launchBtn.style.display = 'none';
      enterBtn.style.display = 'block';
    } else {
      s.className = 'status';
      s.textContent = '○ 服务未启动';
      launchBtn.style.display = 'block';
      launchBtn.textContent = '启动并进入';
      enterBtn.style.display = 'none';
    }
  } catch (e) {
    s.className = 'status';
    s.textContent = '✗ 检查失败';
    document.getElementById('hint').textContent = e.message;
  }
}

async function launch() {
  const btn = document.getElementById('launchBtn');
  const s = document.getElementById('status');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>启动中...';
  document.getElementById('hint').textContent = '首次启动约需 3-5 秒,请稍候';
  s.className = 'status checking';
  s.textContent = '● 启动中...';
  try {
    const r = await fetch('/api/launch', {method: 'POST'});
    const d = await r.json();
    if (d.ok) {
      s.className = 'status running';
      s.textContent = '● 启动成功,正在跳转...';
      setTimeout(() => { window.location.href = 'http://localhost:8000/'; }, 500);
    } else {
      btn.disabled = false;
      btn.textContent = '启动并进入';
      s.className = 'status';
      s.textContent = '✗ 启动失败';
      document.getElementById('hint').textContent = d.error || '请查看终端日志';
    }
  } catch (e) {
    btn.disabled = false;
    btn.textContent = '启动并进入';
    document.getElementById('hint').textContent = '错误: ' + e.message;
  }
}

function enter() {
  window.location.href = 'http://localhost:8000/';
}

checkStatus();
</script>
</body>
</html>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_GET(self):
        if self.path == '/':
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.end_headers()
            self.wfile.write(HTML.encode('utf-8'))
        elif self.path == '/api/status':
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'running': is_app_running(),
                'app_url': APP_URL,
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if self.path == '/api/launch':
            ok = start_app()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(json.dumps({
                'ok': ok,
                'app_url': APP_URL,
                'error': None if ok else f'na start 失败,请查看 {os.path.join(tempfile.gettempdir(), "novel-agent.log")}',
            }).encode())
        else:
            self.send_response(404)
            self.end_headers()


def main():
    print("=" * 50)
    print("  天衍 启动器")
    print("=" * 50)
    print(f"\n  卡片界面: http://localhost:{PORT}/")
    print(f"  主应用:   {APP_URL}")
    print(f"\n  按 Ctrl+C 退出启动器 (主服务不受影响)\n")
    # 自动打开浏览器
    time.sleep(0.3)
    try:
        webbrowser.open(f"http://localhost:{PORT}/")
    except Exception:
        pass
    # 启动 HTTP 服务
    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("0.0.0.0", PORT), Handler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n退出启动器")
            httpd.shutdown()


if __name__ == "__main__":
    main()
