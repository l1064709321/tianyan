"""FastAPI 服务:REST + SSE 流式。挂载静态前端。"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

from fastapi import FastAPI, File, Form, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, store, tools
from .agent import run
from .config import get_settings, reload_settings
from .exporter import export_project, parse_bytes

WEB_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "web")

app = FastAPI(title="天衍", version=__version__)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------- API 认证 (可选) ----------
# 设置 TIANYAN_API_KEY 环境变量后, 所有 /api/* 请求必须带 Authorization: Bearer <key>
# 不设置则无认证 (向后兼容, 适合本地开发)
_API_KEY = os.environ.get("TIANYAN_API_KEY", "").strip()

@app.middleware("http")
async def _auth_middleware(request, call_next):
    if _API_KEY and request.url.path.startswith("/api/"):
        auth = request.headers.get("authorization", "")
        if not auth.endswith(_API_KEY):
            from fastapi.responses import JSONResponse
            return JSONResponse({"error": "Unauthorized"}, status_code=401)
    return await call_next(request)


@app.on_event("startup")
def _startup() -> None:
    store.init_db()
    # 启动时检查 API Key 配置状态, 未配置则打印友好提示
    s = get_settings()
    if not _model_ready(s.default_model):
        print("\n" + "=" * 60)
        print("⚠️  当前模型尚未配置 API Key!")
        print(f"   默认模型: {s.default_model.model}")
        print("   配置方式 (任选其一):")
        print("     1. 在 Web 界面右上角 ⚙️ 设置 → 添加模型 → 填入 API Key")
        print("     2. 在 .env 文件中设置环境变量 (如 DEEPSEEK_API_KEY=sk-xxx)")
        print("        参考 .env.example 模板")
        print("     3. 在 config.yaml 中填写 api_key 字段")
        print("   推荐用 DeepSeek (国内直连, 无需代理): https://platform.deepseek.com")
        print("=" * 60 + "\n")


# ---------- projects ----------
class ProjectIn(BaseModel):
    name: str
    genre: str = ""
    premise: str = ""
    style: str = ""
    audience: str = ""


@app.post("/api/projects")
def create_project(p: ProjectIn):
    pid = store.create_project(p.name, p.genre, p.premise, p.style, p.audience)
    return {"id": pid, **p.dict()}


@app.get("/api/projects")
def list_projects():
    return store.list_projects()


@app.get("/api/projects/{pid}")
def get_project(pid: str):
    p = store.get_project(pid)
    if not p:
        return {"error": "not found"}, 404
    p["stats"] = store.stats(pid)
    p["chapters"] = store.list_chapters(pid)
    p["elements"] = store.list_elements(pid)
    return p


@app.delete("/api/projects/{pid}")
def delete_project(pid: str):
    store.delete_project(pid)
    return {"ok": True}


# ---------- chapters ----------
class ChapterIn(BaseModel):
    title: str
    idx: Optional[int] = None
    outline: str = ""
    content: str = ""


@app.post("/api/projects/{pid}/chapters")
def add_chapter(pid: str, c: ChapterIn):
    chapters = store.list_chapters(pid)
    idx = c.idx if c.idx is not None else (max([x["idx"] for x in chapters], default=-1) + 1)
    cid = store.add_chapter(pid, c.title, idx, c.outline, c.content)
    return {"id": cid, "idx": idx}


@app.put("/api/chapters/{cid}")
def update_chapter(cid: str, body: dict):
    store.update_chapter(cid, **{k: v for k, v in body.items() if k in ("title", "outline", "content", "status")})
    return {"ok": True}


@app.delete("/api/chapters/{cid}")
def delete_chapter(cid: str):
    store.delete_chapter(cid)
    return {"ok": True}


@app.get("/api/projects/{pid}/chapters")
def list_chapters(pid: str):
    return store.list_chapters(pid)


@app.get("/api/projects/{pid}/sources")
def list_sources(pid: str):
    """已上传素材文件列表(去重 source)。"""
    chunks = store.list_chunks(pid)
    seen = {}
    for ck in chunks:
        src = ck["source"]
        if src not in seen:
            seen[src] = {"source": src, "chunks": 0, "chars": 0}
        seen[src]["chunks"] += 1
        seen[src]["chars"] += len(ck.get("text") or "")
    return list(seen.values())


@app.get("/api/projects/{pid}/search")
def search_chunks(pid: str, q: str = "", k: int = 8):
    """在已上传素材分块中检索与 q 相关的内容。"""
    results = tools.search_chunks(pid, q, k=k)
    return {"results": results, "total": len(store.list_chunks(pid))}


# ---------- elements ----------
class ElementIn(BaseModel):
    kind: str
    name: str
    detail: str


@app.post("/api/projects/{pid}/elements")
def add_element(pid: str, e: ElementIn):
    eid = store.add_element(pid, e.kind, e.name, e.detail)
    return {"id": eid, **e.dict()}


@app.get("/api/projects/{pid}/elements")
def list_elements(pid: str, kind: Optional[str] = None):
    return store.list_elements(pid, kind)


@app.delete("/api/elements/{eid}")
def delete_element(eid: str):
    store.delete_element(eid)
    return {"ok": True}


# ---------- messages ----------
@app.get("/api/projects/{pid}/messages")
def list_messages(pid: str):
    return store.list_messages(pid, limit=100)


@app.delete("/api/projects/{pid}/messages")
def clear_messages(pid: str):
    store.clear_messages(pid)
    return {"ok": True}


# ---------- runs / run_events (评测可观测性) ----------
@app.get("/api/projects/{pid}/runs")
def list_runs(pid: str, limit: int = 50):
    """列出项目的 agent loop run 历史 (最近在前)。"""
    return store.list_runs(pid, limit=limit)


@app.get("/api/runs/{run_id}")
def get_run_detail(run_id: str):
    """取单个 run 的元数据 + 全部事件 (按 seq 顺序,用于回放)。"""
    run = store.get_run(run_id)
    if not run:
        return {"error": "run not found"}, 404
    events = store.list_run_events(run_id)
    return {"run": run, "events": events}


@app.get("/api/projects/{pid}/metrics")
def project_metrics(pid: str):
    """项目级聚合指标: 总 run 数 / token / 成本 / 平均耗时 / 工具调用次数。"""
    return store.aggregate_project_metrics(pid)


@app.delete("/api/runs/{run_id}")
def delete_run(run_id: str):
    """删除单条 run + 其全部 events (CASCADE)。"""
    from .store import get_conn, _lock
    with _lock, get_conn() as c:
        c.execute("DELETE FROM run_events WHERE run_id=?", (run_id,))
        c.execute("DELETE FROM runs WHERE id=?", (run_id,))
    return {"ok": True}


# ---------- agent (SSE 流式) ----------
class AgentIn(BaseModel):
    input: str
    agent: Optional[str] = None  # 入口 agent,默认 orchestrator


@app.post("/api/projects/{pid}/chat")
async def chat_sse(pid: str, body: AgentIn):
    from . import agents as agents_mod
    agent_name = body.agent or agents_mod.DEFAULT_AGENT
    if not agents_mod.is_valid(agent_name):
        agent_name = agents_mod.DEFAULT_AGENT

    async def gen():
        # ===== 顶层异常捕获: run() 内部任何未捕获异常都在此兜底 =====
        # 确保后端协程"零崩溃": 异常转为 error 事件发给前端, 连接不中断
        import traceback as _tb
        try:
            async for evt in run(pid, body.input, agent_name=agent_name):
                yield evt
        except asyncio.CancelledError:
            # 客户端主动断开, 正常行为, 不报错
            pass
        except Exception as e:
            _tb.print_exc()
            # 把异常转为 SSE error 事件, 前端收到后显示错误而非白屏
            err_payload = {
                "type": "error",
                "message": f"内部错误: {e}",
                "fatal": False,
                "traceback": _tb.format_exc()[-500:],
            }
            yield f"data: {json.dumps(err_payload, ensure_ascii=False)}\n\n"
            # 确保 done 事件发出, 前端能正常结束 loading 状态
            yield f'data: {json.dumps({"type": "done", "agent": agent_name, "error": True}, ensure_ascii=False)}\n\n'

    # SSE 必需的响应头: 防止代理缓冲/浏览器缓存导致连接中断
    sse_headers = {
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",          # 禁用 Nginx 反代缓冲
        "Connection": "keep-alive",
        "X-Content-Type-Options": "nosniff",
    }
    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers=sse_headers,
    )


# ---------- 多 agent 配置 ----------
@app.get("/api/agents")
def list_agents():
    from . import agents as agents_mod
    return {
        "agents": agents_mod.AGENT_META,
        "default": agents_mod.DEFAULT_AGENT,
        "max_delegate_depth": agents_mod.MAX_DELEGATE_DEPTH,
        "workflow_phases": agents_mod.WORKFLOW_PHASES,
        "readonly_agents": list(agents_mod.SANDBOX_READONLY),
    }


# ---------- 技能市场 (Skill Market) ----------
@app.get("/api/skills")
def list_skills():
    """列出所有技能 (内置 + 自定义) + 启用状态 + 调用次数。"""
    from . import skill_market
    return {
        "skills": skill_market.list_skills(),
        "status": skill_market.skill_market_status(),
    }


@app.post("/api/skills/{name}/toggle")
def toggle_skill(name: str):
    """切换某技能的启用/禁用状态。"""
    from . import skill_market
    return skill_market.toggle_skill(name)


@app.post("/api/skills/custom")
def add_custom_skill(body: dict):
    """添加自定义技能 (前端表单: name + label + description + prompt + agents + icon)。"""
    from . import skill_market
    name = body.get("name", "")
    label = body.get("label", "")
    description = body.get("description", "")
    prompt = body.get("prompt", "")
    agents = body.get("agents", ["orchestrator", "narrative-writer"])
    icon = body.get("icon", "⭐")
    return skill_market.add_custom_skill(
        name=name, label=label, description=description,
        prompt=prompt, agents=agents, icon=icon,
    )


@app.delete("/api/skills/custom/{name}")
def delete_custom_skill(name: str):
    """删除自定义技能 (内置技能不可删)。"""
    from . import skill_market
    return skill_market.remove_custom_skill(name)


# ---------- 上传小说 (多格式,供续写) ----------
@app.post("/api/projects/{pid}/upload")
async def upload_novel(
    pid: str,
    file: UploadFile = File(...),
    source_name: Optional[str] = Form(None),
):
    data = await file.read()
    src = source_name or file.filename or "upload"
    try:
        text = parse_bytes(src, data)
    except ValueError as e:
        return {"error": str(e)}
    if not text.strip():
        return {"error": "解析后内容为空"}
    res = tools.ingest_text(pid, text, src)
    res["chars"] = len(text)
    return res


# ---------- 导出 ----------
@app.get("/api/projects/{pid}/export")
def export(pid: str, fmt: str = "txt"):
    p = store.get_project(pid)
    if not p:
        return {"error": "project not found"}, 404
    chapters = store.list_chapters(pid)
    try:
        filename, content, ctype = export_project(p, chapters, fmt)
    except ValueError as e:
        return {"error": str(e)}, 400
    from fastapi.responses import Response
    from urllib.parse import quote

    ascii_name = filename.encode("ascii", "ignore").decode("ascii") or "novel"
    headers = {
        "Content-Disposition": (
            f'attachment; filename="{ascii_name}"; '
            f"filename*=UTF-8''{quote(filename)}"
        )
    }
    return Response(content=content, media_type=ctype, headers=headers)


# ---------- 配置 ----------
def _model_ready(cfg) -> bool:
    """模型是否已配置可用 key(或本地 ollama)。"""
    from .llm import _normalize_model
    norm = _normalize_model(cfg.model, cfg.api_base)
    provider = norm.split("/", 1)[0].lower() if "/" in norm else "openai"
    if provider == "ollama":
        return True
    if cfg.api_key:
        return True
    env_map = {
        "openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY",
        "gemini": "GEMINI_API_KEY", "deepseek": "DEEPSEEK_API_KEY",
        "dashscope": "DASHSCOPE_API_KEY",
        "zai": "ZAI_API_KEY", "zhipu": "ZAI_API_KEY", "glm": "ZAI_API_KEY",
        "moonshot": "MOONSHOT_API_KEY",
        "xai": "XAI_API_KEY", "mistral": "MISTRAL_API_KEY",
        "volcengine": "VOLCENGINE_API_KEY", "ark": "ARK_API_KEY",
        "baidu": "ERNIE_API_KEY", "ernie": "ERNIE_API_KEY", "qianfan": "ERNIE_API_KEY",
    }
    return bool(os.environ.get(env_map.get(provider, "")))


@app.get("/api/config")
def get_config():
    s = get_settings()
    return {
        "default": s.default_model.model,
        "models": [m.model for m in s.models],
        "ready": _model_ready(s.default_model),
        "max_steps": s.max_steps,
    }


@app.get("/api/config/models")
def get_models():
    s = get_settings()
    return {
        "default": s.default_model.model,
        "models": [m.model for m in s.models],
        "ready": _model_ready(s.default_model),
    }


class ModelSwitch(BaseModel):
    model: str


@app.put("/api/config/model")
def switch_model(body: ModelSwitch):
    from .config import set_default_model

    set_default_model(body.model)
    s = get_settings()
    return {"default": s.default_model.model, "ready": _model_ready(s.default_model)}


# ---------- 连接测试 (前端"测试连接"按钮用) ----------
class TestConnIn(BaseModel):
    model: Optional[str] = None
    api_key: Optional[str] = None
    api_base: Optional[str] = None


@app.post("/api/config/test-connection")
async def test_connection(body: TestConnIn):
    """测试模型连接是否正常。发一个 1-token 请求快速验证。

    不传 model 则用当前默认配置。返回 {ok, message}。
    """
    from .llm import test_connection as _test
    from .config import ModelConfig

    s = get_settings()
    if body.model:
        # 用传入的参数构建临时配置
        # 如果只传了 model 名, 从已有 models 列表里找 key/base
        api_key = body.api_key
        api_base = body.api_base
        if not api_key:
            for m in s.models:
                if m.model == body.model:
                    api_key = m.api_key
                    api_base = m.api_base
                    break
        cfg = ModelConfig(
            model=body.model,
            api_key=api_key,
            api_base=api_base,
        )
    else:
        cfg = s.default_model
    result = await _test(cfg)
    return result


# ---------- 完整设置 (右侧面板用) ----------
@app.get("/api/settings")
def get_settings_full():
    from .config import PROVIDER_PRESETS

    s = get_settings()
    models = []
    for m in s.models:
        models.append({
            "model": m.model,
            "api_key": m.api_key or "",
            "api_key_set": bool(m.api_key),
            "api_base": m.api_base or "",
            "temperature": m.temperature,
            "max_tokens": m.max_tokens,
            "ready": _model_ready(m),
            "is_default": m.model == s.default_model.model,
        })
    return {
        "default": s.default_model.model,
        "models": models,
        "providers": PROVIDER_PRESETS,
        "max_steps": s.max_steps,
        "chunk_size": s.chunk_size,
        "retrieve_k": s.retrieve_k,
        "ready": _model_ready(s.default_model),
    }


class ModelConfigIn(BaseModel):
    model: str
    api_key: Optional[str] = None
    api_base: Optional[str] = None
    temperature: Optional[float] = None
    max_tokens: Optional[int] = None


@app.put("/api/settings/model")
def save_model_cfg(body: ModelConfigIn):
    from .config import update_model_config, PROVIDER_PRESETS

    # 传空 api_base 时,自动用厂商预设默认 base
    api_base = body.api_base
    if not api_base:
        provider = body.model.split("/", 1)[0] if "/" in body.model else ""
        for p in PROVIDER_PRESETS:
            if p["provider"] == provider and p.get("api_base"):
                api_base = p["api_base"]
                break

    update_model_config(
        body.model,
        api_key=body.api_key,
        api_base=api_base,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
    )
    return {"ok": True}


class ModelDelIn(BaseModel):
    model: str


@app.delete("/api/settings/model")
def del_model_cfg(body: ModelDelIn):
    from .config import remove_model_config

    remove_model_config(body.model)
    return {"ok": True}


class AgentParamsIn(BaseModel):
    max_steps: Optional[int] = None
    chunk_size: Optional[int] = None
    retrieve_k: Optional[int] = None


@app.put("/api/settings/agent")
def save_agent_params(body: AgentParamsIn):
    from .config import update_agent_params

    update_agent_params(
        max_steps=body.max_steps,
        chunk_size=body.chunk_size,
        retrieve_k=body.retrieve_k,
    )
    return {"ok": True}


# ---------- 记忆 (Memory) ----------
@app.get("/api/projects/{pid}/memory")
def get_project_memory(pid: str):
    """获取项目长期记忆"""
    from .memory import get_memory, search_memory
    return {
        "memory": get_memory(pid),
    }


@app.get("/api/projects/{pid}/memory/search")
def search_project_memory(pid: str, q: str = "", k: int = 5):
    """检索项目记忆"""
    from .memory import search_memory
    return {"results": search_memory(pid, q, k)}


# ---------- 动态规划 (Planner) ----------
@app.get("/api/projects/{pid}/plan")
def get_project_plan(pid: str):
    """获取当前执行计划"""
    from .planner import get_plan, get_plan_summary
    plan = get_plan(pid)
    return {
        "has_plan": plan is not None,
        "summary": get_plan_summary(pid),
        "steps": [
            {"id": s.id, "description": s.description, "agent": s.agent,
             "tool": s.tool, "status": s.status, "result": s.result}
            for s in (plan.steps if plan else [])
        ],
    }


class PlanIn(BaseModel):
    goal: str


@app.post("/api/projects/{pid}/plan")
async def create_project_plan(pid: str, body: PlanIn):
    """生成动态执行计划"""
    from .planner import generate_plan
    plan = await generate_plan(pid, body.goal)
    return {
        "steps": [
            {"id": s.id, "description": s.description, "agent": s.agent,
             "tool": s.tool, "status": s.status}
            for s in plan.steps
        ],
    }


# ---------- 审批门 (Approval) ----------
@app.get("/api/projects/{pid}/approvals")
def list_approvals(pid: str):
    """列出待审批请求"""
    from .approval import list_pending
    return {"approvals": list_pending()}


class ApprovalAction(BaseModel):
    response: str = ""


@app.post("/api/approvals/{request_id}/approve")
def approve_request(request_id: str, body: ApprovalAction):
    """审批通过"""
    from .approval import approve
    ok = approve(request_id, body.response or "approved")
    return {"ok": ok}


@app.post("/api/approvals/{request_id}/reject")
def reject_request(request_id: str, body: ApprovalAction):
    """审批拒绝"""
    from .approval import reject
    ok = reject(request_id, body.response or "rejected")
    return {"ok": ok}


# ---------- 检查点 (Checkpoint) ----------
@app.get("/api/projects/{pid}/checkpoints")
def list_checkpoints(pid: str):
    """列出检查点"""
    from .checkpoint import list_checkpoints
    return {"checkpoints": list_checkpoints(pid)}


@app.get("/api/projects/{pid}/checkpoints/latest")
def get_latest_checkpoint(pid: str):
    """获取最近检查点"""
    from .checkpoint import get_latest_checkpoint
    return {"checkpoint": get_latest_checkpoint(pid)}


@app.post("/api/projects/{pid}/checkpoints")
def create_checkpoint(pid: str):
    """手动创建检查点"""
    from .checkpoint import save_checkpoint
    cid = save_checkpoint(pid, label="manual")
    return {"id": cid}


@app.get("/api/health")
def health():
    return {"ok": True}


# ---------- 静态前端 ----------
if os.path.isdir(WEB_DIR):
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
def index():
    idx = os.path.join(WEB_DIR, "index.html")
    if not os.path.exists(idx):
        return "<h1>天衍</h1><p>web/ 目录未找到</p>"
    with open(idx, "r", encoding="utf-8") as f:
        html = f.read()
    # 静态资源缓存破坏: 把 /static/<file>?v=<任意> 替换成
    # ?v=<应用版本号>.<文件mtime>, 这样:
    #   1. 版本号统一来自 __version__ (单一来源), 前端 HTML 不再手动维护 ?v=N
    #   2. 文件改动后 mtime 变化, 浏览器自动拉新; 不改文件则缓存稳定
    def _asset_v(m: "re.Match[str]") -> str:
        fname = m.group(1)
        try:
            mt = int(os.path.getmtime(os.path.join(WEB_DIR, fname)))
        except OSError:
            mt = 0
        return f"/static/{fname}?v={__version__}.{mt}"

    return re.sub(r"/static/([^\"'\s?]+)\?v=[^\"'\s]+", _asset_v, html)


# ---------- 卡片启动器 (http://localhost:8000/launcher) ----------
LAUNCHER_HTML = """<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>天衍 · 启动卡片</title>
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
  .logo { font-size: 64px; line-height: 1; margin-bottom: 18px; color: #a13d3d; font-weight: 700; }
  .title { font-size: 28px; color: #2e1f15; margin-bottom: 6px; font-weight: 700; letter-spacing: .5px; }
  .subtitle { color: #9a8062; margin-bottom: 28px; font-size: 13px; }
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
    text-decoration: none;
  }
  .btn-primary { background: #a13d3d; color: #fff; }
  .btn-primary:hover:not(:disabled) { background: #8b2e2e; transform: translateY(-1px); box-shadow: 0 6px 16px rgba(161,61,61,.3); }
  .btn-secondary { background: #5a6b3f; color: #fff; margin-top: 10px; }
  .btn-secondary:hover { background: #4a5b2f; transform: translateY(-1px); }
  .btn:disabled { opacity: .6; cursor: not-allowed; transform: none; }
  .hint { margin-top: 18px; font-size: 12px; color: #9a8062; min-height: 16px; }
  .spinner {
    display: inline-block; width: 12px; height: 12px;
    border: 2px solid #fff; border-top-color: transparent;
    border-radius: 50%; animation: spin .8s linear infinite;
    vertical-align: middle; margin-right: 6px;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
  @media (max-width: 480px) { .card { padding: 36px 28px; } .logo { font-size: 56px; } .title { font-size: 24px; } }
</style>
</head>
<body>
<div class="card">
  <div class="logo">✦</div>
  <div class="title">天衍</div>
  <div class="subtitle">小说创作 8 阶段工作流 · 技能市场</div>
  <div class="status checking" id="status">● 检查中...</div>
  <button class="btn btn-primary" id="enterBtn" onclick="enter()" style="display:none">直接进入应用</button>
  <button class="btn btn-secondary" id="restartBtn" onclick="restart()" style="display:none">重启服务</button>
  <div class="hint" id="hint"></div>
</div>
<script>
async function checkStatus() {
  const s = document.getElementById('status');
  const enterBtn = document.getElementById('enterBtn');
  const restartBtn = document.getElementById('restartBtn');
  s.className = 'status checking';
  s.textContent = '● 检查中...';
  try {
    const r = await fetch('/api/health');
    if (r.ok) {
      s.className = 'status running';
      s.textContent = '● 服务运行中';
      enterBtn.style.display = 'block';
      restartBtn.style.display = 'block';
    } else {
      s.className = 'status';
      s.textContent = '○ 服务异常';
      restartBtn.style.display = 'block';
    }
  } catch (e) {
    s.className = 'status';
    s.textContent = '○ 服务未启动';
    restartBtn.style.display = 'block';
  }
}
function enter() { window.location.href = '/'; }
async function restart() {
  const btn = document.getElementById('restartBtn');
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>重启中...';
  document.getElementById('hint').textContent = '约需 3-5 秒,请稍候';
  try {
    await fetch('/api/launcher/restart', {method:'POST'});
    await new Promise(r => setTimeout(r, 4000));
    await checkStatus();
    btn.disabled = false;
    btn.innerHTML = '重启服务';
    document.getElementById('hint').textContent = '已重启,可点上方按钮进入';
  } catch (e) {
    btn.disabled = false;
    btn.innerHTML = '重启服务';
    document.getElementById('hint').textContent = '错误: ' + e.message;
  }
}
checkStatus();
</script>
</body>
</html>"""


@app.get("/launcher", response_class=HTMLResponse)
def launcher_card():
    """卡片启动器页面。"""
    return LAUNCHER_HTML


@app.post("/api/launcher/restart")
def launcher_restart():
    """通过卡片触发服务重启。优先用 na 脚本，否则走 Python 子进程。"""
    import subprocess
    import sys

    na_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "na")

    # 优先走 na 脚本（Linux/macOS）
    if os.path.exists(na_path) and sys.platform != "win32":
        subprocess.Popen(
            ["bash", na_path, "restart"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
        return {"ok": True, "message": "重启指令已发送 (na)"}

    # 跨平台回退：起一个新的 uvicorn 子进程
    run_py = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "run.py")
    subprocess.Popen(
        [sys.executable, run_py],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    return {"ok": True, "message": "重启指令已发送 (Python)"}


def main() -> None:
    import uvicorn

    s = get_settings()
    uvicorn.run(
        "app.server:app",
        host=s.server_host,
        port=s.server_port,
        reload=False,
        timeout_keep_alive=600,           # SSE 长连接保活 600s, 远大于任何 LLM 思考时间
        timeout_graceful_shutdown=30,     # 优雅关闭给 30s 让在途请求完成
        # 关键: 不设 limit_concurrency, 让 SSE 长连接不占用并发配额
    )


if __name__ == "__main__":
    main()
