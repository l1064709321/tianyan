// 小说Agent 前端:Codex 式聊天 + 步骤展示 + 多格式 + 系统打磨
const $ = (s) => document.querySelector(s);
const $$ = (s) => document.querySelectorAll(s);

let currentProject = null;
let chatHistory = [];
let config = { default: "", models: [], ready: true };
let agentsList = [];
let currentAgent = "orchestrator";        // 入口 agent(固定为 orchestrator,用户不能手选)
let activeAgent = "orchestrator";           // 当前活跃 agent(由 delegate 事件自动更新)
let workflowPhases = [];
let readonlyAgents = [];

// ---- 主题 (浅色/深色/护眼/暗夜紫/羊皮纸) ----
const THEMES = {
  light:   { label: "浅色白",  vars: { "--paper":"#ffffff", "--paper2":"#f7f7f8", "--paper3":"#eef0f2", "--paper4":"#e4e7ea", "--ink":"#1a1a1a", "--ink2":"#4a4a4a", "--muted":"#8a8a8a", "--border":"#e2e2e2", "--border2":"#d0d0d0", "--accent":"#c43d3d", "--blue":"#3d6b8b", "--green":"#2d7a4a", "--yellow":"#9a6b1f", "--red":"#c43d3d" } },
  dark:    { label: "深色夜",  vars: { "--paper":"#1e1f22", "--paper2":"#18191c", "--paper3":"#2a2b2f", "--paper4":"#36373b", "--ink":"#e6e6e6", "--ink2":"#b0b0b0", "--muted":"#7a7a7a", "--border":"#3a3b3f", "--border2":"#4a4b4f", "--accent":"#e57070", "--blue":"#7ab0d4", "--green":"#5fb878", "--yellow":"#d4a256", "--red":"#e57070" } },
  eye:     { label: "护眼绿",  vars: { "--paper":"#c7e3c7", "--paper2":"#b8dab8", "--paper3":"#a8d0a8", "--paper4":"#98c698", "--ink":"#1f3a1f", "--ink2":"#3a5a3a", "--muted":"#6a8a6a", "--border":"#9ac89a", "--border2":"#88b488", "--accent":"#8a3d3d", "--blue":"#3d5a7a", "--green":"#2d6a4a", "--yellow":"#8a6b1f", "--red":"#8a3d3d" } },
  sepia:   { label: "羊皮纸",  vars: { "--paper":"#f6efd9", "--paper2":"#efe5c2", "--paper3":"#e6d9a8", "--paper4":"#dcc88a", "--ink":"#2e1f15", "--ink2":"#5a4530", "--muted":"#8a7a5a", "--border":"#d8c890", "--border2":"#c8b870", "--accent":"#a13d3d", "--blue":"#3d6b8b", "--green":"#2d7a4a", "--yellow":"#9a6b1f", "--red":"#a13d3d" } },
  purple:  { label: "暗夜紫",  vars: { "--paper":"#221a2e", "--paper2":"#1a1424", "--paper3":"#2e2440", "--paper4":"#3a2e52", "--ink":"#e8dff5", "--ink2":"#b8a8d4", "--muted":"#7a6a9a", "--border":"#3a2e52", "--border2":"#4a3a68", "--accent":"#c47a9a", "--blue":"#7a9ad4", "--green":"#5fb878", "--yellow":"#d4a256", "--red":"#c47a7a" } },
};
let currentTheme = localStorage.getItem("na-theme") || "sepia";
let currentFontScale = parseFloat(localStorage.getItem("na-font-scale") || "1");

function applyTheme(name) {
  const t = THEMES[name] || THEMES.sepia;
  const root = document.documentElement;
  for (const [k, v] of Object.entries(t.vars)) root.style.setProperty(k, v);
  localStorage.setItem("na-theme", name);
  currentTheme = name;
}

function applyFontScale(s) {
  const root = document.documentElement;
  root.style.setProperty("--font-scale", String(s));
  // CSS 变量 (供 fixed 浮层 settings-panel/modal/cmdk 用,因为它们在 .app 之外,不受 .app zoom 影响)
  root.style.setProperty("--base-font", `${(14 * s).toFixed(2)}px`);
  root.style.setProperty("--chat-msg-font", `${(15.5 * s).toFixed(2)}px`);
  // line-height 必须是无单位的比例值 (如 1.85), 不能带 px。
  // 若写成 1.85px 则每行仅 1.85 像素高, 字体 (15.5px) 会全部叠压在一起。
  // 且 --chat-msg-font 已按 s 缩放, line-height 作为倍数会自动跟随, 无需再乘 s。
  root.style.setProperty("--chat-line", "1.85");
  root.style.setProperty("--fs-sm", `${(12 * s).toFixed(2)}px`);
  root.style.setProperty("--fs-xs", `${(11 * s).toFixed(2)}px`);
  root.style.setProperty("--fs-md", `${(13 * s).toFixed(2)}px`);
  // 主页整体缩放:zoom 会等比缩放 .app 内所有元素 (按钮/字号/间距/图标/卡片)
  // 配合反向算 width/height:zoom s 倍后视觉宽度=100/s * s = 100vw,正好占满视口,
  // 不会溢出导致按钮被推出屏幕 / 卡片被裁
  const app = document.querySelector(".app");
  if (app) {
    app.style.zoom = String(s);
    app.style.width = `${(100 / s).toFixed(4)}vw`;
    app.style.height = `${(100 / s).toFixed(4)}vh`;
  }
  localStorage.setItem("na-font-scale", String(s));
  currentFontScale = s;
  const lbl = $("#sp-fontscale-label");
  if (lbl) lbl.textContent = `字号缩放 (当前 ${s.toFixed(2)}x)`;
}
const AGENT_LABELS = {
  orchestrator: "总编",
  "story-architect": "架构师",
  "narrative-writer": "主笔",
  "character-designer": "角色师",
  "consistency-checker": "质检员",
  "story-explorer": "资料员",
  worldbuilder: "设定管理员",
  // 兼容旧名
  planner: "策划师", writer: "主笔", editor: "编辑",
};
const AGENT_ICONS = {
  orchestrator: "🎯",
  "story-architect": "📐",
  "narrative-writer": "✍️",
  "character-designer": "👤",
  "consistency-checker": "🔍",
  "story-explorer": "📊",
  worldbuilder: "🌐",
  // 兼容旧名
  planner: "📐", writer: "✍️", editor: "🔧",
};

// 工具名 → 中文标签
const TOOL_CN = {
  generate_outline: "生成大纲",
  continue_writing: "续写正文",
  polish: "润色改写",
  add_element: "添加设定",
  query_project: "查询项目",
  delegate_to_agent: "委派专家",
  manage_outline: "细纲管理",
  load_context: "加载上下文",
  quality_check: "一致性检查",
  scan_bestseller: "扫榜调研",
  analyze_novel: "拆书解构",
  review_chapter: "毒舌审稿",
  list_authors: "列出作家",
  match_author: "匹配作家",
  get_author_reference: "取作家参考",
  deconstruct: "拆书解构",
  audit_novel: "33维审计",
  detect_ai: "AI味检测",
  diagnose_opening: "黄金三章诊断",
  analyze_style: "文风分析",
  imitate_style: "文风仿写",
  diagnose_stuck: "卡文诊断",
  ghostwrite: "枪手代笔",
  full_audit: "完整审计",
  web_fetch: "网页抓取",
  web_search: "网页搜索",
  browser_fetch: "浏览器抓取",
  browser_screenshot: "浏览器截图",
};

function cnTool(name) {
  return TOOL_CN[name] || name.replace(/_/g, " ");
}

// ---------- 工具 ----------
async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      headers: { "Content-Type": "application/json" },
      ...opts,
    });
  } catch (e) {
    toast(`网络请求失败: ${e.message}`, "warn");
    throw e;
  }
  const ct = res.headers.get("content-type") || "";
  if (!ct.includes("application/json")) {
    // 后端返回了非 JSON (通常是 HTML 错误页或纯文本),不要让浏览器跳转
    const text = await res.text();
    toast(`接口异常 (HTTP ${res.status}): ${text.slice(0, 120)}`, "warn");
    throw new Error(`接口 ${path} 返回非 JSON: HTTP ${res.status}`);
  }
  const data = await res.json();
  if (!res.ok) {
    // 422/500 等: 显示后端错误信息
    const msg = data.detail || data.error || JSON.stringify(data).slice(0, 200);
    toast(`请求失败 (${res.status}): ${msg}`, "warn");
    throw new Error(`接口 ${path} HTTP ${res.status}: ${msg}`);
  }
  return data;
}

function esc(s) {
  return String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
}

// ---------- 全局 button 类型修正 ----------
// 动态生成的 <button> 默认 type="submit",在某些浏览器/情况下会触发默认提交行为,
// 导致页面跳转到 /api/... 显示 {"ok":true} 这种 JSON ("乱码代码")。
// 用 MutationObserver 监听所有新增 button,强制改为 type="button"。
(function fixButtonType() {
  const fix = (root) => {
    root.querySelectorAll("button:not([type])").forEach((b) => b.setAttribute("type", "button"));
    root.querySelectorAll('button[type="submit"]').forEach((b) => b.setAttribute("type", "button"));
  };
  fix(document);
  const obs = new MutationObserver((muts) => {
    for (const m of muts) {
      for (const n of m.addedNodes) {
        if (n.nodeType === 1) {
          if (n.tagName === "BUTTON") n.setAttribute("type", "button");
          else if (n.querySelectorAll) fix(n);
        }
      }
    }
  });
  obs.observe(document.documentElement, { childList: true, subtree: true });
})();

// 极简 markdown 渲染 (无需外部依赖)
function renderMd(src) {
  if (!src) return "";
  let s = esc(src);
  // 代码块
  s = s.replace(/```([\s\S]*?)```/g, (_, c) => `<pre><code>${c.replace(/^\n/, "")}</code></pre>`);
  // 标题
  s = s.replace(/^### (.*)$/gm, "<h3>$1</h3>")
       .replace(/^## (.*)$/gm, "<h3>$1</h3>")
       .replace(/^# (.*)$/gm, "<h3>$1</h3>");
  // 粗体/斜体/行内代码
  s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
       .replace(/`([^`]+)`/g, "<code>$1</code>");
  // 引用
  s = s.replace(/^&gt; (.*)$/gm, "<blockquote>$1</blockquote>");
  // 列表
  const lines = s.split("\n");
  let out = [], inUl = false, inOl = false;
  for (const ln of lines) {
    if (/^- /.test(ln)) {
      if (!inUl) { out.push("<ul>"); inUl = true; }
      out.push(`<li>${ln.slice(2)}</li>`);
    } else if (/^\d+\. /.test(ln)) {
      if (!inOl) { out.push("<ol>"); inOl = true; }
      out.push(`<li>${ln.replace(/^\d+\. /, "")}</li>`);
    } else {
      if (inUl) { out.push("</ul>"); inUl = false; }
      if (inOl) { out.push("</ol>"); inOl = false; }
      out.push(ln);
    }
  }
  if (inUl) out.push("</ul>");
  if (inOl) out.push("</ol>");
  s = out.join("\n");
  // 段落 (连续两个换行)
  s = s.split(/\n{2,}/).map((b) => /^<(h3|ul|ol|pre|blockquote)/.test(b.trim()) ? b : `<p>${b.replace(/\n/g, "<br>")}</p>`).join("\n");
  return s;
}

// ---------- toast ----------
function toast(msg, type = "ok", ms = 3000) {
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.textContent = msg;
  $("#toast-wrap").appendChild(t);
  setTimeout(() => {
    t.style.opacity = "0";
    t.style.transition = ".3s";
    setTimeout(() => t.remove(), 300);
  }, ms);
}

// ---------- 配置/模型 ----------
async function loadConfig() {
  config = await api("/api/config");
  refreshModelChip();
  // 就绪提示
  if (!config.ready) {
    toast(`模型 ${config.default} 未配置 API Key,请设置环境变量或编辑 config.yaml`, "warn", 6000);
  }
  refreshStatus();
}

// 顶栏模型按钮已删除 — 模型快速切换走 Composer 内的 model-chip
// 顶栏齿轮按钮(id=settings-btn)只负责打开完整设置面板(密钥/参数/添加模型)

// 刷新 Composer 中的模型 chip 标签
function refreshModelChip() {
  const el = $("#model-chip-label");
  if (!el || !config) return;
  const m = config.default || "";
  // 显示模型名去掉 provider 前缀,太长截断
  let label = m.split("/").pop();
  if (label.length > 18) label = label.slice(0, 17) + "…";
  el.textContent = label;
  el.parentElement.parentElement.classList.toggle("warn", !config.ready);
}

// Composer 模型 chip 下拉:列出已配置模型 + 管理入口
$("#model-chip").addEventListener("click", async (e) => {
  e.stopPropagation();
  const wrap = $("#model-switch");
  const open = wrap.classList.toggle("open");
  if (!open) return;
  // 拉一次最新 settings(可能用户在 settings-panel 加过模型)
  let s;
  try { s = await api("/api/settings"); } catch { s = null; }
  const menu = $("#model-menu");
  const models = s?.models || (config ? [{ model: config.default, ready: config.ready, is_default: true }] : []);
  const cur = config?.default;
  menu.innerHTML = models.map((m) => {
    const isCur = m.model === cur;
    const shortName = m.model.split("/").pop();
    const provider = m.model.split("/")[0] || "";
    return `<button class="mm-item ${isCur ? "active" : ""}" data-model="${esc(m.model)}">
      <span class="mm-name">${esc(shortName)}</span>
      <span class="mm-prov">${esc(provider)}</span>
      <span class="mm-badge ${m.ready ? "ok" : "warn"}">${m.ready ? "✓" : "!"}</span>
      ${isCur ? '<span class="mm-check">●</span>' : ""}
    </button>`;
  }).join("") + `<button class="mm-manage" data-act="manage">⚙ 管理全部模型…</button>`;
  // 绑定点击
  $$("#model-menu .mm-item").forEach((b) => {
    b.onclick = async (ev) => {
      ev.stopPropagation();
      const m = b.dataset.model;
      if (m === cur) { wrap.classList.remove("open"); return; }
      try {
        await api("/api/config/model", { method: "PUT", body: JSON.stringify({ model: m }) });
        config.default = m;
        // 同步 ready 状态(从 settings 拿)
        if (s) {
          const found = s.models.find((x) => x.model === m);
          if (found) config.ready = found.ready;
        }
        refreshModelChip();
        refreshStatus();
        toast(`已切换到 ${m.split("/").pop()}`, "ok");
      } catch (e) {
        toast("切换失败: " + e.message, "err");
      }
      wrap.classList.remove("open");
    };
  });
  $("#model-menu .mm-manage").onclick = (ev) => {
    ev.stopPropagation();
    wrap.classList.remove("open");
    $("#settings-btn").click();
  };
});
document.addEventListener("click", () => $("#model-switch")?.classList.remove("open"));

function refreshStatus() {
  const sb = $("#status-bar");
  if (!currentProject) {
    sb.innerHTML = config.ready ? "" : `<span class="pill warn">⚠ 未配置 Key</span>`;
    return;
  }
  const st = currentProject.stats || {};
  const parts = [
    `<span class="pill">${st.chapters || 0} 章</span>`,
    `<span class="pill"><b>${(st.total_chars || 0).toLocaleString()}</b> 字</span>`,
  ];
  if (!config.ready) parts.push(`<span class="pill warn">⚠ Key</span>`);
  sb.innerHTML = parts.join("");
}

// ---------- 项目 ----------
async function loadProjects() {
  const list = await api("/api/projects");
  const menu = $("#proj-menu");
  menu.innerHTML = `<button class="proj-opt" id="new-project-opt">+ 新建项目…</button>` +
    list.map((p) => `<button class="proj-opt" data-id="${p.id}">${esc(p.name)}</button>`).join("");
  $$("#proj-menu .proj-opt").forEach((el) => {
    if (el.id === "new-project-opt") {
      el.onclick = () => $("#proj-modal").classList.add("show");
    } else {
      el.onclick = () => { selectProject(el.dataset.id); closeSidebar(); };
    }
  });
  // 当前项目标签
  if (currentProject) {
    $("#proj-select-label").textContent = currentProject.name;
  } else if (list.length) {
    $("#proj-select-label").textContent = "选择项目 ▾";
  }
}

// 项目选择器下拉
$("#proj-select-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  $("#proj-select-btn").parentElement.classList.toggle("open");
});
// 加号按钮: 直接打开新建项目弹窗
$("#proj-new-btn").addEventListener("click", (e) => {
  e.stopPropagation();
  $("#proj-select-btn").parentElement.classList.remove("open");
  $("#proj-modal").classList.add("show");
});
document.addEventListener("click", () => {
  $("#proj-select-btn")?.parentElement.classList.remove("open");
});

async function selectProject(pid) {
  const p = await api(`/api/projects/${pid}`);
  currentProject = p;
  $("#proj-info").textContent = p.name + (p.audience ? ` · ${p.audience}` : "") + (p.genre ? ` · ${p.genre}` : "");
  $("#proj-select-label").textContent = p.name;
  // 拉取已上传素材
  let sources = [];
  try { sources = await api(`/api/projects/${pid}/sources`); } catch { sources = []; }
  currentProject.sources = sources;
  renderTree();
  loadProjects();
  await loadMessages(pid);
  refreshStatus();
}

// ---------- 文件树 ----------
const KIND_META = {
  character: { label: "角色", icon: "👤" },
  location: { label: "地点", icon: "📍" },
  lore: { label: "世界观", icon: "🌐" },
  timeline: { label: "时间线", icon: "⏱" },
};
const FILE_ICON = { chapter: "📄", source: "📚" };

function renderTree() {
  const tree = $("#tree");
  if (!currentProject) {
    tree.innerHTML = `<div class="tree-empty"><div class="tree-empty-icon">📂</div>
      <p>选择或新建一个项目</p><p class="muted">章节、设定、素材将在此以文件树形式展示</p></div>`;
    return;
  }
  const p = currentProject;
  const chs = (p.chapters || []).slice().sort((a, b) => a.idx - b.idx);
  const elems = p.elements || [];
  const srcs = p.sources || [];

  // 按设定类型分组
  const byKind = {};
  for (const e of elems) {
    (byKind[e.kind] ||= []).push(e);
  }

  const parts = [];
  // 项目信息节点
  parts.push(`<div class="tree-file" data-act="info">
    <span class="ico">⚙️</span><span class="name">项目设置</span>
    <span class="meta">${p.audience ? p.audience + " · " : ""}${p.genre || ""}</span></div>`);

  // 章节文件夹
  parts.push(`<div class="tree-folder open" id="f-chapters">
    <div class="tree-folder-head" data-toggle="f-chapters">
      <span class="chev">▸</span><span class="ico">📁</span>
      <span class="name">章节</span><span class="cnt">${chs.length}</span>
    </div><div class="tree-folder-children">`);
  if (!chs.length) {
    parts.push(`<div class="tree-file" style="opacity:.5;cursor:default"><span class="ico">·</span><span class="name">(暂无章节,点对话让 Agent 生成)</span></div>`);
  }
  chs.forEach((c) => {
    const n = (c.content || "").length;
    // 状态徽章: draft=不显示 (简洁), generating=脉冲黄, written=绿✓, failed=红✗
    const st = c.status || "draft";
    let badge = "";
    if (st === "generating") badge = `<span class="ch-badge generating" title="生成中">●</span>`;
    else if (st === "written") badge = `<span class="ch-badge written" title="已写完">✓</span>`;
    else if (st === "failed") badge = `<span class="ch-badge failed" title="生成失败,可重试">✗</span>`;
    parts.push(`<div class="tree-file${st === "generating" ? " is-generating" : ""}${st === "failed" ? " is-failed" : ""}" data-chapter="${c.id}">
      <span class="ico">${FILE_ICON.chapter}</span>
      <span class="name">${String(c.idx + 1).padStart(2, "0")}. ${esc(c.title)}</span>
      ${badge}
      <span class="meta">${n ? n + "字" : "草稿"}</span></div>`);
  });
  parts.push(`</div></div>`);

  // 设定文件夹
  parts.push(`<div class="tree-folder open" id="f-elements">
    <div class="tree-folder-head" data-toggle="f-elements">
      <span class="chev">▸</span><span class="ico">📁</span>
      <span class="name">设定</span><span class="cnt">${elems.length}</span>
    </div><div class="tree-folder-children">`);
  if (!elems.length) {
    parts.push(`<div class="tree-file" style="opacity:.5;cursor:default"><span class="ico">·</span><span class="name">(暂无设定,点底部 + 设定 添加)</span></div>`);
  }
  for (const [kind, meta] of Object.entries(KIND_META)) {
    const list = byKind[kind] || [];
    if (!list.length) continue;
    parts.push(`<div class="tree-folder open" id="f-elem-${kind}">
      <div class="tree-folder-head" data-toggle="f-elem-${kind}">
        <span class="chev">▸</span><span class="ico">${meta.icon}</span>
        <span class="name">${meta.label}</span><span class="cnt">${list.length}</span>
      </div><div class="tree-folder-children">`);
    list.forEach((e) => {
      parts.push(`<div class="tree-file" data-element="${e.id}">
        <span class="ico">${meta.icon}</span>
        <span class="name">${esc(e.name)}</span>
        <button class="edel" data-del-elem="${e.id}" title="删除">✕</button></div>`);
    });
    parts.push(`</div></div>`);
  }
  // 未知 kind
  const others = elems.filter((e) => !KIND_META[e.kind]);
  if (others.length) {
    parts.push(`<div class="tree-folder open" id="f-elem-other">
      <div class="tree-folder-head" data-toggle="f-elem-other">
        <span class="chev">▸</span><span class="ico">📁</span>
        <span class="name">其他</span><span class="cnt">${others.length}</span>
      </div><div class="tree-folder-children">`);
    others.forEach((e) => {
      parts.push(`<div class="tree-file" data-element="${e.id}">
        <span class="ico">📄</span><span class="name">${esc(e.name)}</span>
        <button class="edel" data-del-elem="${e.id}" title="删除">✕</button></div>`);
    });
    parts.push(`</div></div>`);
  }
  parts.push(`</div></div>`);

  // 素材库文件夹
  parts.push(`<div class="tree-folder open" id="f-sources">
    <div class="tree-folder-head" data-toggle="f-sources">
      <span class="chev">▸</span><span class="ico">📁</span>
      <span class="name">素材库</span><span class="cnt">${srcs.length}</span>
    </div><div class="tree-folder-children">`);
  if (!srcs.length) {
    parts.push(`<div class="tree-file" style="opacity:.5;cursor:default"><span class="ico">·</span><span class="name">(点顶栏「上传」导入 txt/md/docx/pdf/epub)</span></div>`);
  }
  srcs.forEach((s) => {
    parts.push(`<div class="tree-file" data-source="${esc(s.source)}">
      <span class="ico">${FILE_ICON.source}</span>
      <span class="name">${esc(s.source)}</span>
      <span class="meta">${s.chunks}块</span></div>`);
  });
  parts.push(`</div></div>`);

  tree.innerHTML = parts.join("");

  // 绑定文件夹展开/收起
  tree.querySelectorAll(".tree-folder-head").forEach((h) => {
    h.addEventListener("click", (e) => {
      if (e.target.closest(".edel") || e.target.closest("button")) return;
      h.parentElement.classList.toggle("open");
    });
  });
  // 章节点击
  tree.querySelectorAll("[data-chapter]").forEach((el) => {
    el.addEventListener("click", () => openChapter(el.dataset.chapter));
  });
  // 设定点击 → 打开抽屉预览
  tree.querySelectorAll("[data-element]").forEach((el) => {
    el.addEventListener("click", () => openElement(el.dataset.element));
  });
  // 素材点击 → 预览片段
  tree.querySelectorAll("[data-source]").forEach((el) => {
    el.addEventListener("click", () => openSource(el.dataset.source));
  });
  // 设定删除
  tree.querySelectorAll("[data-del-elem]").forEach((el) => {
    el.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      if (!confirm("删除该设定?")) return;
      await api(`/api/elements/${el.dataset.delElem}`, { method: "DELETE" });
      await selectProject(currentProject.id);
      toast("已删除设定", "ok");
    });
  });
}

// 打开设定预览
function openElement(eid) {
  const e = currentProject.elements.find((x) => x.id === eid);
  if (!e) return;
  const meta = KIND_META[e.kind] || { icon: "📄", label: "设定" };
  drawerChapterId = null;
  $("#drawer-title").textContent = `${meta.label} · ${e.name}`;
  $("#drawer-body").innerHTML =
    `<h3>${meta.icon} ${esc(e.name)}</h3><p class="muted">${esc(e.kind)}</p>` +
    `<h3>详情</h3>${esc(e.detail || "(无)")}`;
  $("#drawer-edit-area").classList.add("hidden");
  $("#drawer-body").classList.remove("hidden");
  $("#drawer-edit").classList.add("hidden");
  $("#drawer-save").classList.add("hidden");
  $("#drawer").classList.add("open");
}

// 打开素材预览(首块文本)
async function openSource(source) {
  drawerChapterId = null;
  $("#drawer-title").textContent = `素材 · ${source}`;
  $("#drawer-body").innerHTML = `<p class="muted">加载中…</p>`;
  $("#drawer").classList.add("open");
  $("#drawer-edit-area").classList.add("hidden");
  $("#drawer-body").classList.remove("hidden");
  // 复用 search 拿片段
  try {
    const r = await api(`/api/projects/${currentProject.id}/search?q=${encodeURIComponent(source.slice(0, 8))}`);
    const snips = (r.results || []).map((x) => esc(x.text)).join("\n\n---\n\n");
    $("#drawer-body").innerHTML = `<h3>📚 ${esc(source)}</h3>${snips || "<p class='muted'>(无内容)</p>"}`;
  } catch {
    $("#drawer-body").innerHTML = `<p class="err">读取失败</p>`;
  }
}

async function loadMessages(pid) {
  const msgs = await api(`/api/projects/${pid}/messages`);
  $("#chat").innerHTML = "";
  chatHistory = [];
  if (msgs.length === 0) {
    showEmpty();
    return;
  }
  for (const m of msgs) {
    if (m.role === "tool") continue;
    if (m.role === "assistant" && m.tool_name === "tool_calls") continue;
    appendMessage(m.role, m.content, false);
  }
  scrollChat();
}

function showEmpty() {
  $("#chat").innerHTML = "";
  const div = document.createElement("div");
  div.className = "empty";
  div.id = "empty-state";
  div.innerHTML = `<div class="empty-icon">✦</div><h2>小说创作 Agent</h2>
    <p>类似 Codex 的协作式小说写作助手。它会自主规划并调用工具完成创作。</p>`;
  $("#chat").appendChild(div);
  rebuildSuggestions();
}

function rebuildSuggestions() {
  const suggs = [
    "查看当前项目状态与进度",
    "继续续写最近一章,2000字",
    "对最近一章做质量检查",
  ];
  const wrap = document.createElement("div");
  wrap.className = "suggestions";
  suggs.forEach((t) => {
    const b = document.createElement("button");
    b.className = "sugg";
    b.textContent = t;
    b.onclick = () => send(t);
    wrap.appendChild(b);
  });
  const es = $("#empty-state");
  if (es) es.appendChild(wrap);
}

// ---------- 对话 ----------
function appendMessage(role, content, streaming) {
  const es = $("#empty-state");
  if (es) es.remove();
  const msg = { role, content: content || "", steps: [], streaming };
  chatHistory.push(msg);
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  div.innerHTML = `<div class="role ${role}">${role === "user" ? "你" : "✦ 小说Agent"}</div>
    <div class="bubble"></div>`;
  $("#chat").appendChild(div);
  msg.el = div.querySelector(".bubble");
  if (content) {
    if (role === "assistant") msg.el.innerHTML = `<div class="md">${renderMd(content)}</div>`;
    else msg.el.textContent = content;
  }
  scrollChat();
  return msg;
}

function scrollChat() {
  const c = $("#chat");
  c.scrollTop = c.scrollHeight;
}

async function send(text) {
  if (!currentProject) {
    toast("请先创建或选择一个项目", "warn");
    return;
  }
  if (!config.ready) {
    toast("模型未配置 Key,可在顶栏切换已配置的模型", "warn", 5000);
  }
  text = text || $("#input").value.trim();
  if (!text) return;
  $("#input").value = "";
  autoGrow();
  appendMessage("user", text);

  // 左侧：隐藏文件树，显示思考面板
  showThinkPanel();

  const assistant = appendMessage("assistant", "", true);

  try {
    const res = await fetch(`/api/projects/${currentProject.id}/chat`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ input: text, agent: "orchestrator" }),
    });
    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      const lines = buf.split("\n");
      buf = lines.pop();
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        let evt;
        try { evt = JSON.parse(line.slice(6)); } catch { continue; }
        handleEvent(evt, assistant);
      }
    }
    // 清除 loading
    removeThinkLoading();
  } catch (e) {
    removeThinkLoading();
    assistant.el.innerHTML = `<span class="err">连接失败: ${esc(e.message)}</span>`;
    hideThinkPanel();
  }
}

// ============ 左侧思考面板 ============
function showThinkPanel() {
  const tree = $("#tree");
  const panel = $("#think-panel");
  const stream = $("#tp-stream");
  if (tree) tree.classList.add("hidden");
  if (panel) panel.classList.remove("hidden");
  // 重置
  if (stream) {
    stream.innerHTML = `<div class="tp-entry tp-loading">
      <span class="tp-dot"></span>
      <span class="tp-text">正在思考<span class="thinking-dots"></span></span>
    </div>`;
  }
}

function hideThinkPanel() {
  const tree = $("#tree");
  const panel = $("#think-panel");
  if (tree) tree.classList.remove("hidden");
  if (panel) panel.classList.add("hidden");
}

function removeThinkLoading() {
  const loading = $("#tp-stream .tp-loading");
  if (loading) loading.remove();
}

function appendThink(html) {
  removeThinkLoading();
  const stream = $("#tp-stream");
  if (!stream) return;
  const div = document.createElement("div");
  div.innerHTML = html;
  stream.appendChild(div.firstElementChild);
  stream.scrollTop = stream.scrollHeight;
}

function handleEvent(evt, assistant) {
  const bubble = assistant.el;
  switch (evt.type) {
    case "start":
      window.__naRunStartTs = Date.now();
      break;
    case "delegate": {
      // 委派: 左侧思考面板显示
      const toLbl = AGENT_LABELS[evt.to] || evt.to;
      const toIcon = AGENT_ICONS[evt.to] || "↪";
      if (evt.to) updateActiveAgent(evt.to);
      const task = (evt.task || "").slice(0, 60);
      appendThink(`<div class="tp-entry tp-delegate">
        <span class="tp-icon">${toIcon}</span>
        <span class="tp-text"><span class="tp-label delegate">委派</span> → <b>${toLbl}</b>${task ? "：" + esc(task) : ""}</span>
      </div>`);
      scrollChat();
      break;
    }
    case "step": {
      const ag = evt.agent || "";
      const agLbl = ag ? (AGENT_LABELS[ag] || ag) : "";
      const agIcon = ag ? (AGENT_ICONS[ag] || "") : "";
      const step = { tool: evt.tool, args: evt.args, thinking: evt.thinking || "", agent: ag };
      assistant.steps.push(step);

      // 思考过程: 左侧面板
      if (evt.thinking) {
        const agTag = agIcon ? `${agIcon} ${esc(agLbl)}` : "";
        appendThink(`<div class="tp-entry tp-think">
          <span class="tp-dot"></span>
          <span class="tp-text">${agTag ? `<b>${agTag}</b>：` : ""}${esc(evt.thinking)}</span>
        </div>`);
      }

      // 工具调用: 左侧面板 (紧凑条目)
      const toolName = cnTool(evt.tool || "");
      appendThink(`<div class="tp-entry tp-tool" data-toolstep="${assistant.steps.length - 1}">
        <span class="tp-dot"></span>
        ${agIcon ? `<span class="tp-icon">${agIcon}</span>` : ""}
        <span class="tp-text"><span class="tp-label tool">调用</span> <b>${esc(toolName)}</b>${agIcon ? " · " + esc(agLbl) : ""}</span>
      </div>`);

      // 工具调用卡片: 右侧紧凑展示 (可折叠)
      const toolDiv = document.createElement("div");
      toolDiv.className = "step-card";
      const depthIndent = evt.depth ? `style="margin-left:${evt.depth * 12}px"` : "";
      const toolName2 = cnTool(evt.tool || "");
      toolDiv.innerHTML = `<div class="sc-head" ${depthIndent}>
        <span class="sc-dot"></span>
        ${agIcon ? `<span class="sc-agent">${agIcon} ${esc(agLbl)}</span>` : ""}
        <span class="sc-icon">🔧</span>
        <span class="sc-tool">${esc(toolName2)}</span>
        <span class="sc-status">执行中…</span>
      </div>
      <div class="sc-body">
        <div class="sc-args"><span class="sc-label">参数</span>${esc(JSON.stringify(evt.args, null, 2))}</div>
        <div class="sc-result">⏳ 等待结果…</div>
      </div>`;
      bubble.appendChild(toolDiv);
      step.resultEl = toolDiv.querySelector(".sc-result");
      step.headEl = toolDiv.querySelector(".sc-head");
      step.statusEl = toolDiv.querySelector(".sc-status");
      step.cardEl = toolDiv;
      scrollChat();
      break;
    }
    case "observation": {
      const step = assistant.steps[assistant.steps.length - 1];
      if (step) {
        step.resultEl.innerHTML = `<span class="sc-label">结果</span>${prettyResult(evt.result)}`;
        step.headEl.classList.add("done");
        if (step.statusEl) step.statusEl.textContent = "✓ 完成";
        step.cardEl.classList.add("collapsed");

        // 左侧面板: 标记对应工具条目为完成
        const tpTool = $(`#tp-stream [data-toolstep="${assistant.steps.length - 1}"]`);
        if (tpTool) {
          tpTool.classList.add("tp-done");
          tpTool.querySelector(".tp-dot").style.background = "var(--green)";
        }
      }
      scrollChat();
      break;
    }
    case "answer_start": {
      removeThinkLoading();
      assistant.answerEl = document.createElement("div");
      assistant.answerEl.className = "md answer-body";
      bubble.appendChild(assistant.answerEl);
      assistant.rawBuf = "";
      const caret = document.createElement("span");
      caret.className = "caret";
      bubble.appendChild(caret);
      break;
    }
    case "token": {
      if (!assistant.answerEl) {
        assistant.answerEl = document.createElement("div");
        assistant.answerEl.className = "md answer-body";
        assistant.rawBuf = "";
        bubble.appendChild(assistant.answerEl);
      }
      assistant.rawBuf += evt.text;
      assistant.answerEl.innerHTML = renderMd(assistant.rawBuf);
      scrollChat();
      break;
    }
    case "answer_end": {
      const caret = bubble.querySelector(".caret");
      if (caret) caret.remove();
      break;
    }
    case "heartbeat":
      if (window.__naRunStartTs) {
        const elapsed = Math.max(0, Math.round((Date.now() - window.__naRunStartTs) / 1000));
        const hd = document.getElementById("run-elapsed");
        if (hd) {
          hd.hidden = false;
          hd.textContent = elapsed > 0 ? `已运行 ${elapsed}s` : "";
        }
      }
      break;
    case "error": {
      const errMsg = evt.message || "";
      const isApiKeyErr = errMsg.includes("API Key") || errMsg.includes("api_key") || errMsg.includes("Missing credentials");
      const isLoop = evt.reason === "loop_detected" || errMsg.includes("超限") || errMsg.includes("卡循环");
      // 左侧面板也显示错误
      appendThink(`<div class="tp-entry tp-error">
        <span class="tp-dot"></span>
        <span class="tp-text"><span class="tp-label error">错误</span> ${esc(errMsg)}</span>
      </div>`);
      if (isApiKeyErr) {
        bubble.innerHTML += `<div class="err err-apikey">
          <div class="err-icon">🔑</div>
          <div class="err-text">${esc(errMsg)}</div>
          <button class="btn primary sm" onclick="$('#settings-btn').click()" style="margin-top:8px">去设置面板配置 API Key</button>
        </div>`;
        toast("未配置 API Key，请在设置面板中配置", "warn", 8000);
      } else if (isLoop) {
        bubble.innerHTML += `<div class="err err-warn">
          <div class="err-icon">⚠️</div>
          <div class="err-text">${esc(errMsg)}</div>
        </div>`;
        toast(`⚠️ ${errMsg}`, "warn", 6000);
      } else {
        bubble.innerHTML += `<div class="err">
          <div class="err-icon">❌</div>
          <div class="err-text">${esc(errMsg)}</div>
        </div>`;
      }
      window.__naRunStartTs = null;
      const hdErr = document.getElementById("run-elapsed");
      if (hdErr) { hdErr.textContent = ""; hdErr.hidden = true; }
      hideThinkPanel();
      break;
    }
    case "done":
      removeThinkLoading();
      // 左侧面板: 完成徽章
      appendThink(`<div class="tp-done-badge">
        <span class="tp-done-icon">✓</span> 完成 · ${evt.steps} 步
        ${evt.stats ? ` · ${evt.stats.chapters}章/${evt.stats.total_chars}字` : ""}
      </div>`);
      // 1.5 秒后恢复文件树
      setTimeout(() => hideThinkPanel(), 1500);

      if (!assistant.answerEl) {
        const note = document.createElement("div");
        note.className = "done-note";
        note.textContent = `✓ 完成 (${evt.steps} 步)` + (evt.note ? ` · ${evt.note}` : "");
        bubble.appendChild(note);
      } else {
        const note = document.createElement("div");
        note.className = "done-note muted";
        note.textContent = `✓ 完成 · ${evt.steps} 步` +
          (evt.stats ? ` · ${evt.stats.chapters}章/${evt.stats.total_chars}字` : "");
        bubble.appendChild(note);
      }
      updateActiveAgent("orchestrator");
      window.__naRunStartTs = null;
      const hd = document.getElementById("run-elapsed");
      if (hd) { hd.textContent = ""; hd.hidden = true; }
      if (currentProject) {
        api(`/api/projects/${currentProject.id}`).then((p) => {
          if (p && currentProject) {
            currentProject.chapters = p.chapters || [];
            currentProject.elements = p.elements || currentProject.meta;
            currentProject.meta = p.meta || currentProject.meta;
            renderTree();
          }
        }).catch(() => {});
      }
      scrollChat();
      break;
  }
}

function prettyResult(r) {
  try {
    const o = typeof r === "string" ? JSON.parse(r) : r;
    return JSON.stringify(o, null, 2);
  } catch {
    return r;
  }
}

// ---------- 章节抽屉 (可编辑) ----------
let drawerChapterId = null;

async function openChapter(cid) {
  const p = currentProject;
  const ch = p.chapters.find((c) => c.id === cid);
  if (!ch) return;
  drawerChapterId = cid;
  $("#drawer-title").textContent = ch.title;
  let body = `<h3>梗概</h3>${esc(ch.outline || "(无)")}`;
  body += `<h3>正文 (${(ch.content || "").length} 字)</h3>${esc(ch.content || "(尚未撰写)")}`;
  $("#drawer-body").innerHTML = body;
  $("#drawer-edit-area").value = ch.content || "";
  $("#drawer-body").classList.remove("hidden");
  $("#drawer-edit-area").classList.add("hidden");
  $("#drawer-edit").classList.remove("hidden");
  $("#drawer-save").classList.add("hidden");
  $("#drawer").classList.add("open");
}

$("#drawer-edit").addEventListener("click", () => {
  $("#drawer-body").classList.add("hidden");
  $("#drawer-edit-area").classList.remove("hidden");
  $("#drawer-edit").classList.add("hidden");
  $("#drawer-save").classList.remove("hidden");
});
$("#drawer-save").addEventListener("click", async () => {
  const content = $("#drawer-edit-area").value;
  await api(`/api/chapters/${drawerChapterId}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
  toast("章节已保存", "ok");
  // 刷新
  await selectProject(currentProject.id);
  openChapter(drawerChapterId);
});
$("#drawer-close").addEventListener("click", () => $("#drawer").classList.remove("open"));
$("#view-chapter-btn").addEventListener("click", () => {
  const p = currentProject;
  if (!p || !p.chapters?.length) return toast("暂无章节", "warn");
  openChapter(p.chapters[p.chapters.length - 1].id);
});

// ---------- 新建章节 ----------
$("#new-chapter-btn").addEventListener("click", async () => {
  if (!currentProject) return toast("先选择项目", "warn");
  const title = prompt("章节标题:");
  if (!title) return;
  const idx = (currentProject.chapters || []).length;
  const c = await api(`/api/projects/${currentProject.id}/chapters`, {
    method: "POST",
    body: JSON.stringify({ title, idx, outline: "", content: "" }),
  });
  await selectProject(currentProject.id);
  if (c.id) openChapter(c.id);
  toast("章节已创建", "ok");
});

// ---------- 上传 (toast) ----------
$("#upload-btn").addEventListener("click", () => $("#upload-input").click());
$("#upload-input").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file || !currentProject) return;
  const fd = new FormData();
  fd.append("file", file);
  toast(`正在导入 ${file.name}…`, "ok", 1500);
  const res = await fetch(`/api/projects/${currentProject.id}/upload`, {
    method: "POST",
    body: fd,
  });
  const data = await res.json();
  if (data.error) {
    toast(data.error, "err", 5000);
  } else {
    toast(`已导入 ${data.source}: ${data.chunks} 块 / ${data.chars} 字`, "ok", 4000);
  }
  e.target.value = "";
});

// ---------- 导出 ----------
const exportBtn = $("#export-btn");
exportBtn.addEventListener("click", (e) => {
  e.stopPropagation();
  if (!currentProject) return toast("先选择项目", "warn");
  exportBtn.parentElement.classList.toggle("open");
});
document.addEventListener("click", () => {
  exportBtn.parentElement.classList.remove("open");
});
$("#export-menu").addEventListener("click", (e) => {
  const btn = e.target.closest("button[data-fmt]");
  if (!btn) return;
  exportBtn.parentElement.classList.remove("open");
  const fmt = btn.dataset.fmt;
  const pid = currentProject.id;
  const url = `/api/projects/${pid}/export?fmt=${fmt}`;
  if (fmt === "html") {
    window.open(url, "_blank");
  } else {
    const a = document.createElement("a");
    a.href = url;
    a.download = "";
    document.body.appendChild(a);
    a.click();
    a.remove();
    toast(`已导出 ${fmt.toUpperCase()}`, "ok");
  }
});

// ---------- 新建项目 ----------

// 频道切换
let selectedAudience = "男频";
document.querySelectorAll("#p-audience .aud-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll("#p-audience .aud-btn").forEach(b => b.classList.remove("active"));
    btn.classList.add("active");
    selectedAudience = btn.dataset.val;
  });
});

$("#proj-cancel").addEventListener("click", () => $("#proj-modal").classList.remove("show"));
$("#proj-ok").addEventListener("click", async () => {
  const body = {
    name: $("#p-name").value.trim(),
    genre: $("#p-genre").value.trim(),
    style: $("#p-style").value.trim(),
    premise: $("#p-premise").value.trim(),
    audience: selectedAudience,
  };
  if (!body.name) return toast("请填名称", "warn");
  const p = await api("/api/projects", { method: "POST", body: JSON.stringify(body) });
  $("#proj-modal").classList.remove("show");
  ["p-name", "p-genre", "p-style", "p-premise"].forEach((i) => ($("#" + i).value = ""));
  await loadProjects();
  await selectProject(p.id);
  closeSidebar();
  toast("项目已创建", "ok");
});

// ---------- 添加设定 ----------
$("#add-element-btn").addEventListener("click", () => {
  if (!currentProject) return toast("先选择项目", "warn");
  $("#elem-modal").classList.add("show");
});
$("#elem-cancel").addEventListener("click", () => $("#elem-modal").classList.remove("show"));
$("#elem-ok").addEventListener("click", async () => {
  const body = {
    kind: $("#e-kind").value,
    name: $("#e-name").value.trim(),
    detail: $("#e-detail").value.trim(),
  };
  if (!body.name) return toast("请填名称", "warn");
  await api(`/api/projects/${currentProject.id}/elements`, {
    method: "POST",
    body: JSON.stringify(body),
  });
  $("#elem-modal").classList.remove("show");
  $("#e-name").value = "";
  $("#e-detail").value = "";
  await selectProject(currentProject.id);
  toast("设定已添加", "ok");
});

// ---------- 清空对话 ----------
$("#clear-chat-btn").addEventListener("click", async () => {
  if (!currentProject || !confirm("清空当前项目对话历史?")) return;
  await api(`/api/projects/${currentProject.id}/messages`, { method: "DELETE" });
  showEmpty();
  toast("已清空对话", "ok");
});

// ---------- 响应式侧栏 ----------
function openSidebar() {
  $("#sidebar").classList.add("open");
  $("#scrim").classList.add("show");
}
function closeSidebar() {
  $("#sidebar").classList.remove("open");
  $("#scrim").classList.remove("show");
}
$("#menu-btn").addEventListener("click", openSidebar);
$("#sidebar-close").addEventListener("click", closeSidebar);
$("#scrim").addEventListener("click", closeSidebar);

// ---------- composer ----------
$("#send-btn").addEventListener("click", () => send());
$("#input").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
function autoGrow() {
  const t = $("#input");
  t.style.height = "auto";
  t.style.height = Math.min(t.scrollHeight, 140) + "px";
}
$("#input").addEventListener("input", autoGrow);
// 斜杠命令:输入 / 触发 popup
$("#input").addEventListener("input", (e) => {
  const v = e.target.value;
  if (v.startsWith("/") && !v.includes("\n")) renderSlashPopup(v);
  else { $("#slash-popup").classList.add("hidden"); slashOpen = false; }
});
$("#input").addEventListener("keydown", (e) => {
  if (slashOpen && (e.key === "Enter" || e.key === "Tab")) {
    const sel = $("#slash-popup .slash-item.sel") || $("#slash-popup .slash-item");
    if (sel) { e.preventDefault(); sel.click(); return; }
  }
  if (slashOpen && (e.key === "ArrowDown" || e.key === "ArrowUp")) {
    e.preventDefault();
    const items = [...$$("#slash-popup .slash-item")];
    if (!items.length) return;
    let cur = items.findIndex((x) => x.classList.contains("sel"));
    cur = e.key === "ArrowDown" ? (cur + 1) % items.length : (cur - 1 + items.length) % items.length;
    items.forEach((x) => x.classList.remove("sel"));
    items[cur].classList.add("sel");
  }
});

// 导出辅助(给命令面板用)
async function doExport(fmt) {
  if (!currentProject) return toast("先选择项目", "warn");
  window.location.href = `/api/projects/${currentProject.id}/export?fmt=${fmt}`;
}

document.addEventListener("click", (e) => {
  if (e.target.classList.contains("sugg")) send(e.target.dataset.prompt || e.target.textContent);
  // 点击 step-card 头部切换折叠
  const scHead = e.target.closest(".sc-head");
  if (scHead) {
    scHead.closest(".step-card")?.classList.toggle("collapsed");
  }
});

// init
applyTheme(currentTheme);
applyFontScale(currentFontScale);
loadConfig();
loadProjects();
loadAgents();

// ---------- 多 agent 选择器 ----------
function agentBadgesHtml(a) {
  const parts = [];
  if (a.is_entry) parts.push(`<span class="abadge entry">入口</span>`);
  if (a.phase) parts.push(`<span class="abadge phase">阶段 ${esc(a.phase)}</span>`);
  if (a.sandbox === "read-only") {
    parts.push(`<span class="abadge ro" title="只读沙盒,不可写入">🔒 只读</span>`);
  } else if (a.sandbox === "read-write") {
    parts.push(`<span class="abadge rw" title="可读可写">✏️ 可写</span>`);
  }
  if (a.model_tier) {
    const tier = a.model_tier === "high" ? "强" : a.model_tier === "mid" ? "中" : "轻";
    parts.push(`<span class="abadge tier tier-${a.model_tier}" title="模型层级">${tier}</span>`);
  }
  return parts.join("");
}

async function loadAgents() {
  try {
    const data = await api("/api/agents");
    agentsList = data.agents || [];
    currentAgent = data.default || "orchestrator";
    activeAgent = currentAgent;
    workflowPhases = data.workflow_phases || [];
    readonlyAgents = data.readonly_agents || [];
    updateActiveAgent(currentAgent);   // 初始化芯片
    renderWorkflowPhases();
  } catch (e) {
    console.warn("load agents failed", e);
  }
}

// OpenClaw 模式:更新活跃 agent 状态(前端不显示,纯内部状态,orchestrator 自动委派时用)
function updateActiveAgent(name) {
  activeAgent = name;
}

// 打开 Agent Panel(展示当前活跃 agent 的 memory/tools/指令栈,只读)
function openAgentPanel() {
  const a = agentsList.find((x) => x.name === activeAgent);
  const body = $("#ap-body");
  const title = $("#ap-title");
  if (!a) {
    title.textContent = "Agent";
    body.innerHTML = `<p class="muted">暂无活跃 agent 信息</p>`;
  } else {
    title.innerHTML = `<span style="font-size:18px">${a.icon}</span> ${esc(a.label)}`;
    const toolsList = (a.tools || []).map((t) => `<span class="ap-tool">${esc(t)}</span>`).join("");
    body.innerHTML = `
      <div class="ap-sec">
        <div class="ap-sec-title">角色</div>
        <div class="ap-role">${esc(a.role || "(无描述)")}</div>
      </div>
      <div class="ap-sec">
        <div class="ap-sec-title">阶段</div>
        <div class="ap-meta">${esc(a.phase || "全局")}</div>
      </div>
      <div class="ap-sec">
        <div class="ap-sec-title">沙盒</div>
        <div class="ap-meta">
          <span class="abadge ${a.sandbox === "read-only" ? "ro" : "rw"}">
            ${a.sandbox === "read-only" ? "🔒 只读" : "✏️ 可写"}
          </span>
          <span class="abadge tier tier-${a.model_tier || "mid"}">
            ${a.model_tier === "high" ? "强" : a.model_tier === "mid" ? "中" : "轻"}模型
          </span>
        </div>
      </div>
      <div class="ap-sec">
        <div class="ap-sec-title">可用工具 (${(a.tools || []).length})</div>
        <div class="ap-tools">${toolsList || "<span class='muted'>(无)</span>"}</div>
      </div>
      <div class="ap-sec">
        <div class="ap-sec-title">说明</div>
        <div class="ap-note">入口固定为「总编 orchestrator」,它会根据任务自动委派给对应专家 agent。这里显示的是当前活跃 agent 的实时状态。</div>
      </div>
    `;
  }
  $("#agent-panel").classList.add("open");
}

function renderWorkflowPhases() {
  const el = $("#workflow-phases");
  if (!el) return;
  if (!workflowPhases.length) { el.innerHTML = ""; return; }
  el.innerHTML = workflowPhases.map((p, i) => {
    const agents = p.agents || (p.agent ? [p.agent] : []);
    const icons = agents.map((n) => {
      const a = agentsList.find((x) => x.name === n);
      return a ? `<span class="wf-agent" title="${esc(a.label)}">${a.icon}</span>` : "";
    }).join("");
    // 循环标识:打回重写 / 下一章循环
    const loopBadge = p.loop === "reject"
      ? `<span class="wf-loop reject" title="不通过则打回重写">↺ 打回</span>`
      : p.loop === "next-chapter"
      ? `<span class="wf-loop next" title="通过则推进下一章">↻ 循环</span>`
      : "";
    // 阶段间箭头
    const arrow = i < workflowPhases.length - 1 ? `<div class="wf-arrow">↓</div>` : "";
    return `<div class="wf-item">
      <span class="wf-no">${p.phase}</span>
      <span class="wf-body">
        <span class="wf-name">${esc(p.name)} ${loopBadge}</span>
        <span class="wf-desc">${esc(p.description || "")}</span>
        <span class="wf-agents">${icons}</span>
      </span>
    </div>${arrow}`;
  }).join("");
}

// Agent toggle 已从前端移除 — 用户不直接选 agent,orchestrator 自动委派
// openAgentPanel/openAgentPanel 函数保留(命令面板 ⌘K 仍可调出 Agent 详情查看)
$("#ap-close").addEventListener("click", () => $("#agent-panel").classList.remove("open"));

// ==================== 命令面板 (⌘K / Ctrl+K) ====================
const CMDK_ITEMS = [
  { id: "new-project", icon: "📁", label: "新建项目", action: () => $("#proj-modal").classList.add("show") },
  { id: "switch-project", icon: "📂", label: "切换项目", action: () => $("#proj-select-btn").click() },
  { id: "upload", icon: "📤", label: "上传素材", action: () => $("#upload-input").click() },
  { id: "export-txt", icon: "📄", label: "导出 TXT", action: () => doExport("txt") },
  { id: "export-docx", icon: "📘", label: "导出 Word", action: () => doExport("docx") },
  { id: "view-chapters", icon: "📑", label: "查看章节列表", action: () => $("#view-chapter-btn").click() },
  { id: "settings", icon: "⚙️", label: "打开设置", action: () => $("#settings-btn").click() },
  { id: "agent-panel", icon: "🎯", label: "查看活跃 Agent", action: () => openAgentPanel() },
  { id: "clear-chat", icon: "🗑", label: "清空对话", action: () => $("#clear-chat-btn").click() },
  { id: "cmd-continue", icon: "✍️", label: "续写最近一章", action: () => send("继续续写最近一章,2000字") },
  { id: "cmd-status", icon: "📊", label: "查看项目状态", action: () => send("查看当前项目状态与进度") },
  { id: "cmd-check", icon: "🔍", label: "质量检查", action: () => send("对最近一章做质量检查") },
];

function openCmdk() {
  $("#cmdk-overlay").classList.remove("hidden");
  $("#cmdk-input").value = "";
  renderCmdk("");
  setTimeout(() => $("#cmdk-input").focus(), 50);
}
function closeCmdk() {
  $("#cmdk-overlay").classList.add("hidden");
}
function renderCmdk(q) {
  const ql = q.toLowerCase().trim();
  const items = !ql ? CMDK_ITEMS : CMDK_ITEMS.filter((it) =>
    it.label.toLowerCase().includes(ql) || it.id.includes(ql));
  $("#cmdk-list").innerHTML = items.map((it, i) =>
    `<button class="cmdk-item${i === 0 ? " sel" : ""}" data-id="${it.id}">
      <span class="cmdk-icon">${it.icon}</span><span class="cmdk-text">${esc(it.label)}</span>
    </button>`).join("") || `<div class="cmdk-empty">无匹配</div>`;
  $("#cmdk-list").querySelectorAll(".cmdk-item").forEach((el) => {
    el.onclick = () => {
      const it = CMDK_ITEMS.find((x) => x.id === el.dataset.id);
      if (it) { closeCmdk(); it.action(); }
    };
  });
}
document.addEventListener("keydown", (e) => {
  if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
    e.preventDefault();
    $("#cmdk-overlay").classList.contains("hidden") ? openCmdk() : closeCmdk();
  }
  if (e.key === "Escape") closeCmdk();
});
$("#cmdk-input").addEventListener("input", (e) => renderCmdk(e.target.value));
$("#cmdk-input").addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    const sel = $("#cmdk-list .cmdk-item.sel") || $("#cmdk-list .cmdk-item");
    if (sel) sel.click();
  } else if (e.key === "ArrowDown" || e.key === "ArrowUp") {
    e.preventDefault();
    const items = [...$$("#cmdk-list .cmdk-item")];
    if (!items.length) return;
    let cur = items.findIndex((x) => x.classList.contains("sel"));
    cur = e.key === "ArrowDown" ? (cur + 1) % items.length : (cur - 1 + items.length) % items.length;
    items.forEach((x) => x.classList.remove("sel"));
    items[cur].classList.add("sel");
    items[cur].scrollIntoView({ block: "nearest" });
  }
});
$("#cmdk-overlay").addEventListener("click", (e) => {
  if (e.target.id === "cmdk-overlay") closeCmdk();
});

// ==================== 斜杠命令 (在输入框输入 / 触发) ====================
const SLASH_COMMANDS = [
  { cmd: "/续写", desc: "续写最近一章", fill: "继续续写最近一章,2000字" },
  { cmd: "/状态", desc: "查看项目状态", fill: "查看当前项目状态与进度" },
  { cmd: "/质检", desc: "质量检查", fill: "对最近一章做质量检查" },
  { cmd: "/大纲", desc: "生成大纲", fill: "帮我构思一个大纲,12章" },
  { cmd: "/设定", desc: "添加设定", fill: "帮我添加一个角色设定" },
  { cmd: "/润色", desc: "润色最近一章", fill: "润色最近一章,增强感染力" },
  { cmd: "/清空", desc: "清空对话", fill: null, action: () => $("#clear-chat-btn").click() },
  { cmd: "/设置", desc: "打开设置", fill: null, action: () => $("#settings-btn").click() },
  { cmd: "/导出", desc: "导出", fill: null, action: () => $("#export-btn").click() },
];
let slashOpen = false;
function renderSlashPopup(q) {
  const items = SLASH_COMMANDS.filter((s) => !q || s.cmd.includes(q.slice(1)) || s.desc.includes(q));
  const pop = $("#slash-popup");
  if (!items.length) { pop.classList.add("hidden"); slashOpen = false; return; }
  pop.classList.remove("hidden");
  slashOpen = true;
  pop.innerHTML = items.map((s, i) =>
    `<button class="slash-item${i === 0 ? " sel" : ""}" data-fill="${esc(s.fill || "")}" data-cmd="${esc(s.cmd)}">
      <span class="slash-cmd">${esc(s.cmd)}</span><span class="slash-desc">${esc(s.desc)}</span>
    </button>`).join("");
  pop.querySelectorAll(".slash-item").forEach((el) => {
    el.onclick = () => {
      const cmd = SLASH_COMMANDS.find((s) => s.cmd === el.dataset.cmd);
      if (cmd) {
        if (cmd.action) { cmd.action(); $("#input").value = ""; }
        else if (cmd.fill) { $("#input").value = cmd.fill; }
        pop.classList.add("hidden"); slashOpen = false;
        $("#input").focus(); autoGrow();
      }
    };
  });
}

// ==================== 设置面板 ====================
let spData = null; // 当前设置数据
let spSelectedProvider = null; // 添加模型时选中的厂商
let spBaseMap = {}; // 模型 -> 厂商默认 base

$("#settings-btn").addEventListener("click", async () => {
  $("#settings-panel").classList.add("open");
  await loadSettings();
});
$("#sp-close").addEventListener("click", () => $("#settings-panel").classList.remove("open"));

// ---------- 技能市场 (Skill Market) ----------
let skData = null; // {skills: [...], status: {...}}

$("#skills-btn").addEventListener("click", async () => {
  $("#skills-panel").classList.add("open");
  await loadSkills();
});
$("#skp-close").addEventListener("click", () => $("#skills-panel").classList.remove("open"));

// Tab 切换
$$(".skp-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    $$(".skp-tab").forEach((t) => t.classList.remove("active"));
    tab.classList.add("active");
    const which = tab.dataset.tab;
    $("#skp-builtin").classList.toggle("hidden", which !== "builtin");
    $("#skp-custom").classList.toggle("hidden", which !== "custom");
    $("#skp-add").classList.toggle("hidden", which !== "add");
  });
});

// 添加自定义技能表单
$("#sk-save").addEventListener("click", async () => {
  const name = $("#sk-name").value.trim();
  const label = $("#sk-label").value.trim();
  const icon = $("#sk-icon").value.trim() || "⭐";
  const description = $("#sk-desc").value.trim();
  const prompt = $("#sk-prompt").value.trim();
  const agents = Array.from($$("#sk-agents input:checked")).map((c) => c.value);
  if (!name || !label || !prompt) {
    toast("请填写技能名、显示名、Prompt", "err");
    return;
  }
  if (!/^[a-zA-Z0-9_]+$/.test(name)) {
    toast("技能名只能含英文/数字/下划线", "err");
    return;
  }
  if (agents.length === 0) {
    toast("至少选一个适用 Agent", "err");
    return;
  }
  $("#sk-save").disabled = true;
  try {
    const res = await api("/api/skills/custom", {
      method: "POST",
      body: JSON.stringify({ name, label, icon, description, prompt, agents }),
    });
    if (res.error) {
      toast(res.error, "err");
      return;
    }
    toast(`已添加技能: ${label}`, "ok");
    // 清空表单
    $("#sk-name").value = "";
    $("#sk-label").value = "";
    $("#sk-icon").value = "";
    $("#sk-desc").value = "";
    $("#sk-prompt").value = "";
    // 切回自定义 Tab 并刷新
    document.querySelector('.skp-tab[data-tab="custom"]').click();
    await loadSkills();
  } catch (e) {
    toast("保存失败: " + e.message, "err");
  } finally {
    $("#sk-save").disabled = false;
  }
});
$("#sk-reset").addEventListener("click", () => {
  $("#sk-name").value = "";
  $("#sk-label").value = "";
  $("#sk-icon").value = "";
  $("#sk-desc").value = "";
  $("#sk-prompt").value = "";
});

async function loadSkills() {
  try {
    skData = await api("/api/skills");
    renderSkills();
  } catch (e) {
    $("#skp-status").textContent = "加载失败: " + e.message;
  }
}

function renderSkills() {
  if (!skData) return;
  const skills = skData.skills || [];
  const status = skData.status || {};
  const builtin = skills.filter((s) => s.kind !== "custom");
  const custom = skills.filter((s) => s.kind === "custom");

  // 状态汇总
  const enabledCount = skills.filter((s) => s.enabled).length;
  const totalCount = skills.length;
  const totalUsage = skills.reduce((sum, s) => sum + (s.usage || 0), 0);
  $("#skp-status").innerHTML =
    `已启用 <b>${enabledCount}</b> / ${totalCount} 项　·　累计调用 <b>${totalUsage}</b> 次` +
    (status.builtin_total != null ? `　·　内置 ${status.builtin_total} / 自定义 ${status.custom_total}` : "");

  // 内置 grid
  $("#skp-builtin-grid").innerHTML = builtin.map(renderSkillCard).join("");

  // 自定义 grid
  if (custom.length === 0) {
    $("#skp-custom-grid").innerHTML = "";
    $("#skp-custom-empty").classList.remove("hidden");
  } else {
    $("#skp-custom-grid").innerHTML = custom.map(renderSkillCard).join("");
    $("#skp-custom-empty").classList.add("hidden");
  }

  // 绑定开关 + 删除按钮
  $$("#skp-builtin-grid .skill-toggle input").forEach((inp) => {
    inp.addEventListener("change", () => toggleSkill(inp.dataset.name));
  });
  $$("#skp-custom-grid .skill-toggle input").forEach((inp) => {
    inp.addEventListener("change", () => toggleSkill(inp.dataset.name));
  });
  $$("#skp-custom-grid .sc-del").forEach((btn) => {
    btn.addEventListener("click", () => deleteCustomSkill(btn.dataset.name));
  });
}

function renderSkillCard(s) {
  const agents = (s.agents || []).map((a) => `<span class="a-chip">${esc(a)}</span>`).join("");
  const catLabel = s.category || (s.kind === "custom" ? "custom" : "");
  const usage = s.usage || 0;
  const toggle = `<label class="skill-toggle">
    <input type="checkbox" data-name="${esc(s.name)}" ${s.enabled ? "checked" : ""} />
    <span class="track"></span>
  </label>`;
  const delBtn = s.kind === "custom"
    ? `<button class="sc-del" data-name="${esc(s.name)}" title="删除">🗑</button>`
    : "";
  return `<div class="skill-card ${s.enabled ? "enabled" : "disabled"}">
    <div class="sc-head">
      <span class="sc-icon">${esc(s.icon || "⭐")}</span>
      <span class="sc-name">${esc(s.label || s.name)}</span>
      <span class="sc-cat ${esc(catLabel)}">${esc(catLabel || "skill")}</span>
    </div>
    ${s.description ? `<div class="sc-desc">${esc(s.description)}</div>` : ""}
    <div class="sc-meta">
      <div class="agents">${agents}</div>
      <span class="usage">×${usage}</span>
    </div>
    <div class="sc-actions">
      ${delBtn}
      ${toggle}
    </div>
  </div>`;
}

async function toggleSkill(name) {
  try {
    const res = await api(`/api/skills/${encodeURIComponent(name)}/toggle`, { method: "POST" });
    if (res.error) {
      toast(res.error, "err");
      await loadSkills();
      return;
    }
    // 局部更新 (避免整列表闪烁)
    if (skData) {
      const s = skData.skills.find((x) => x.name === name);
      if (s) s.enabled = res.enabled;
      renderSkills();
    }
    toast(`${name} 已${res.enabled ? "启用" : "禁用"}`, "ok", 1500);
  } catch (e) {
    toast("切换失败: " + e.message, "err");
    await loadSkills();
  }
}

async function deleteCustomSkill(name) {
  if (!confirm(`确定删除技能「${name}」?`)) return;
  try {
    const res = await api(`/api/skills/custom/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (res.error) {
      toast(res.error, "err");
      return;
    }
    toast(`已删除: ${name}`, "ok");
    await loadSkills();
  } catch (e) {
    toast("删除失败: " + e.message, "err");
  }
}

async function loadSettings() {
  spData = await api("/api/settings");
  spSelectedProvider = null;
  // 构建模型 -> 厂商预设 base 的反查表
  spBaseMap = {};
  for (const p of (spData.providers || [])) {
    if (!p.api_base) continue;
    for (const m of p.models) spBaseMap[m] = p.api_base;
  }
  renderSettings();
}

// 根据模型名找厂商默认 base
function defaultBaseFor(model) {
  if (!spBaseMap) return "";
  if (spBaseMap[model]) return spBaseMap[model];
  // 按 provider 前缀匹配
  const prov = model.split("/", 1)[0];
  const p = (spData?.providers || []).find((x) => x.provider === prov);
  return p?.api_base || "";
}

function renderSettings() {
  const d = spData;
  const parts = [];

  // ---- 外观 (主题 + 字号) ----
  parts.push(`<div class="sp-sec"><div class="sp-sec-title">外观</div>`);
  parts.push(`<div class="sp-field"><span class="lbl">主题配色</span>
    <div class="theme-grid">`);
  for (const [key, t] of Object.entries(THEMES)) {
    const active = key === currentTheme ? "active" : "";
    const sw = t.vars["--paper"];
    const sw2 = t.vars["--accent"];
    parts.push(`<button class="theme-swatch ${active}" data-theme="${key}" title="${t.label}">
      <span class="sw-color" style="background:${sw};border-color:${sw2}"></span>
      <span class="sw-label">${t.label}</span>
    </button>`);
  }
  parts.push(`</div></div>`);
  parts.push(`<div class="sp-field"><span class="lbl" id="sp-fontscale-label">字号缩放 (当前 ${currentFontScale.toFixed(2)}x)</span>
    <div class="font-scale-row">
      <input type="range" id="sp-fontscale" min="0.8" max="1.6" step="0.05" value="${currentFontScale}" />
      <span class="font-scale-hint">0.8x 小 / 1.0x 默认 / 1.6x 大 (拖动实时预览)</span>
    </div>
  </div>`);
  parts.push(`</div>`);

  // ---- 当前模型 ----
  parts.push(`<div class="sp-sec"><div class="sp-sec-title">当前模型</div>`);
  parts.push(`<div class="sp-row"><label>${esc(d.default)}</label>
    <span class="mc-badge ${d.ready ? "ok" : "warn"}">${d.ready ? "就绪" : "缺 Key"}</span></div>`);
  parts.push(`</div>`);

  // ---- 已配置模型列表 ----
  parts.push(`<div class="sp-sec"><div class="sp-sec-title">已配置模型 (${d.models.length})</div>`);
  if (d.models.length === 0) {
    // 删空后: 显示空状态提示,引导用户去下方"+ 添加模型"区块添加
    parts.push(`<div class="mc-empty">
      <div class="mc-empty-icon">📭</div>
      <p>暂无已配置模型</p>
      <p class="muted">在下方"+ 添加模型"区块选择厂商或填自定义模型名</p>
    </div>`);
  }
  for (const m of d.models) {
    const badges = [];
    if (m.is_default) badges.push(`<span class="mc-badge def">默认</span>`);
    badges.push(`<span class="mc-badge ${m.ready ? "ok" : "warn"}">${m.ready ? "就绪" : "缺Key"}</span>`);
    const defBase = defaultBaseFor(m.model);
    const curBase = m.api_base || "";
    const isOfficial = !curBase || (defBase && curBase === defBase);
    // 官方默认地址不显示输入框,只展示只读徽章;自定义时才显示可编辑输入框
    const baseField = isOfficial
      ? `<div class="sp-field"><span class="lbl" style="color:var(--green)">API Base · 官方默认 ✓</span>
          <div class="sp-readonly">${esc(defBase || "—")}</div></div>`
      : `<div class="sp-field"><span class="lbl">API Base · 自定义</span>
          <input class="sp-input" data-base="${esc(m.model)}" placeholder="${esc(defBase || "")}" value="${esc(curBase)}" /></div>`;
    parts.push(`<div class="model-card ${m.is_default ? "default" : ""}">
      <div class="mc-head">
        <span class="mc-name">${esc(m.model)}</span>
        ${badges.join("")}
        <button class="mc-setdef" data-model="${esc(m.model)}" title="设为默认">★</button>
        <button class="mc-del" data-model="${esc(m.model)}" title="删除">✕</button>
      </div>
      <div class="mc-fields">
        <div class="sp-field">
          <span class="lbl">API Key ${m.api_key_set ? "(已设置)" : ""}</span>
          <input class="sp-input" type="password" data-key="${esc(m.model)}" placeholder="${m.api_key_set ? "•••••• (留空保留)" : "粘贴 API Key"}" value="" />
        </div>
        ${baseField}
        <div class="sp-row">
          <div class="sp-field" style="flex:1"><span class="lbl">Temperature</span>
            <input class="sp-input small" type="number" step="0.1" min="0" max="2" data-temp="${esc(m.model)}" value="${m.temperature}" /></div>
          <div class="sp-field" style="flex:1"><span class="lbl">Max Tokens</span>
            <input class="sp-input small" type="number" step="256" data-tok="${esc(m.model)}" value="${m.max_tokens}" /></div>
        </div>
        <button class="btn primary sm mc-save" data-model="${esc(m.model)}">保存此模型配置</button>
      </div>
    </div>`);
  }
  parts.push(`</div>`);

  // ---- 添加自定义模型 ----
  parts.push(`<div class="sp-sec"><div class="sp-sec-title">+ 添加模型</div>`);
  parts.push(`<div class="add-model">`);
  // 厂商快捷
  parts.push(`<div class="provider-pick">`);
  for (const p of d.providers) {
    parts.push(`<button class="pp-btn" data-provider="${p.provider}">${p.label}</button>`);
  }
  parts.push(`</div>`);
  // 选中厂商后显示预设模型
  if (spSelectedProvider) {
    const p = d.providers.find((x) => x.provider === spSelectedProvider);
    if (p) {
      if (p.api_base) parts.push(`<p class="hint" style="margin-bottom:6px">端点(已自动填入,无需修改):<code>${esc(p.api_base)}</code></p>`);
      parts.push(`<div class="pp-models">`);
      for (const m of p.models) {
        parts.push(`<button class="pm-btn" data-addmodel="${esc(m)}" data-base="${esc(p.api_base || "")}">+ ${esc(m)}</button>`);
      }
      parts.push(`</div>`);
      if (p.env) parts.push(`<p class="hint">环境变量 ${p.env} 也可配置 Key</p>`);
    }
  }
  // 自定义模型入口:可填模型名 + 自定义地址 + 密钥
  parts.push(`<div class="sp-field custom-model-box" style="margin-top:12px">
    <span class="lbl">+ 自定义模型 (填模型名/地址/密钥)</span>
    <div class="sp-row">
      <input class="sp-input" id="sp-custom-model" placeholder="模型名 (如 openai/my-model 或 ollama/llama3)" />
    </div>
    <div class="sp-row">
      <input class="sp-input" id="sp-custom-base" placeholder="API Base 自定义端点 (可留空走厂商默认)" />
    </div>
    <div class="sp-row">
      <input class="sp-input" id="sp-custom-key" type="password" placeholder="API Key 密钥 (可留空走环境变量)" />
      <button class="btn primary sm" id="sp-add-custom">添加自定义</button>
    </div>
    <p class="hint">格式: provider/model。密钥留空时后端会读对应环境变量;地址留空时用厂商默认。</p>
  </div>`);
  parts.push(`</div></div>`);

  // ---- Agent 参数 ----
  parts.push(`<div class="sp-sec"><div class="sp-sec-title">Agent 参数</div>`);
  parts.push(`<div class="sp-row"><label>最大步数 (工具调用轮次)</label>
    <input class="sp-input small" type="number" id="sp-maxsteps" value="${d.max_steps}" min="1" max="30" /></div>`);
  parts.push(`<div class="sp-row"><label>素材分块大小 (字符)</label>
    <input class="sp-input small" type="number" id="sp-chunksize" value="${d.chunk_size}" min="200" step="200" /></div>`);
  parts.push(`<div class="sp-row"><label>续写检索块数</label>
    <input class="sp-input small" type="number" id="sp-retrievek" value="${d.retrieve_k}" min="1" max="20" /></div>`);
  parts.push(`<button class="btn primary" id="sp-save-agent" style="margin-top:8px">保存 Agent 参数</button>`);
  parts.push(`</div>`);

  $("#sp-body").innerHTML = parts.join("");
  bindSettings();
}

function bindSettings() {
  // 主题切换
  $$("#sp-body .theme-swatch").forEach((b) => {
    b.onclick = () => {
      applyTheme(b.dataset.theme);
      renderSettings();
    };
  });
  // 字号滑块 (拖动实时预览,不重新渲染面板)
  const fs = $("#sp-fontscale");
  if (fs) {
    fs.oninput = (e) => applyFontScale(parseFloat(e.target.value));
  }
  // 选中厂商
  $$("#sp-body .pp-btn").forEach((b) => {
    b.onclick = () => {
      spSelectedProvider = b.dataset.provider;
      renderSettings();
    };
  });
  // 添加预设模型
  $$("#sp-body .pm-btn").forEach((b) => {
    b.onclick = async () => {
      const model = b.dataset.addmodel;
      const defBase = b.dataset.base || "";
      try {
        await api("/api/settings/model", {
          method: "PUT",
          body: JSON.stringify({ model, api_key: "", api_base: defBase, temperature: 0.8, max_tokens: 4096 }),
        });
        toast(`已添加 ${model}`, "ok");
        await loadSettings();
      } catch (e) {
        // api() 已 toast 错误,这里只兜底防止 await 链中断导致按钮看似"卡死"
      }
    };
  });
  // 添加自定义模型 (模型名 + 自定义地址 + 密钥)
  const addBtn = $("#sp-add-custom");
  if (addBtn) {
    addBtn.onclick = async () => {
      const modelEl = $("#sp-custom-model");
      const baseEl = $("#sp-custom-base");
      const keyEl = $("#sp-custom-key");
      const model = modelEl ? modelEl.value.trim() : "";
      if (!model) return toast("请填写模型名", "warn");
      if (!model.includes("/")) return toast("格式: provider/model", "warn");
      const customBase = baseEl ? baseEl.value.trim() : "";
      const customKey = keyEl ? keyEl.value.trim() : "";
      // 用户填了自定义 base 就用,否则用厂商默认
      const defBase = defaultBaseFor(model);
      const api_base = customBase || defBase || "";
      try {
        await api("/api/settings/model", {
          method: "PUT",
          body: JSON.stringify({
            model,
            api_key: customKey,
            api_base,
            temperature: 0.8,
            max_tokens: 4096,
          }),
        });
        toast(`已添加自定义模型 ${model}`, "ok");
        // 清空输入
        if (modelEl) modelEl.value = "";
        if (baseEl) baseEl.value = "";
        if (keyEl) keyEl.value = "";
        await loadSettings();
      } catch (e) {
        // api() 已 toast,兜底防止按钮卡死
      }
    };
  }
  // 保存单个模型配置
  $$("#sp-body .mc-save").forEach((b) => {
    b.onclick = async () => {
      const m = b.dataset.model;
      const keyEl = $(`#sp-body [data-key="${CSS.escape(m)}"]`);
      const baseEl = $(`#sp-body [data-base="${CSS.escape(m)}"]`);
      const tempEl = $(`#sp-body [data-temp="${CSS.escape(m)}"]`);
      const tokEl = $(`#sp-body [data-tok="${CSS.escape(m)}"]`);
      // 留空或等于厂商默认时,统一存空串(由后端兜底填默认)
      let baseVal = baseEl ? baseEl.value.trim() : "";
      const def = defaultBaseFor(m);
      if (!baseVal || (def && baseVal === def)) baseVal = "";
      const body = {
        model: m,
        api_key: keyEl ? keyEl.value : null,
        api_base: baseVal,
        temperature: tempEl ? parseFloat(tempEl.value) : null,
        max_tokens: tokEl ? parseInt(tokEl.value) : null,
      };
      try {
        await api("/api/settings/model", { method: "PUT", body: JSON.stringify(body) });
        toast(`${m} 配置已保存`, "ok");
        await loadSettings();
        await loadConfig();
      } catch (e) {
        // api() 已 toast,兜底
      }
    };
  });
  // 设为默认
  $$("#sp-body .mc-setdef").forEach((b) => {
    b.onclick = async () => {
      try {
        await api("/api/config/model", { method: "PUT", body: JSON.stringify({ model: b.dataset.model }) });
        toast(`已设为默认: ${b.dataset.model}`, "ok");
        await loadSettings();
        await loadConfig();
      } catch (e) {
        // 兜底
      }
    };
  });
  // 删除模型
  $$("#sp-body .mc-del").forEach((b) => {
    b.onclick = async () => {
      const model = b.dataset.model;
      // 二次确认: 用 toast 替代 confirm(),避免在某些浏览器环境 (无头/受限) 不弹窗导致按钮看似卡死
      if (b.dataset.confirming === "1") {
        // 第二次点击: 真正删除
        b.dataset.confirming = "0";
        b.textContent = "✕";
        b.classList.remove("warn");
        try {
          await api("/api/settings/model", { method: "DELETE", body: JSON.stringify({ model }) });
          toast(`已删除 ${model}`, "ok");
          await loadSettings();
          await loadConfig();
        } catch (e) {
          // api() 已 toast (如"至少保留一个模型"),兜底
        }
      } else {
        // 第一次点击: 标红 + 提示再点一次确认
        b.dataset.confirming = "1";
        b.textContent = "再点删除";
        b.classList.add("warn");
        toast(`再点一次确认删除 ${model}`, "warn", 3000);
        // 3 秒后自动取消确认状态
        setTimeout(() => {
          if (b.dataset.confirming === "1") {
            b.dataset.confirming = "0";
            b.textContent = "✕";
            b.classList.remove("warn");
          }
        }, 3000);
      }
    };
  });
  // 保存 Agent 参数
  const saveAgent = $("#sp-save-agent");
  if (saveAgent) {
    saveAgent.onclick = async () => {
      await api("/api/settings/agent", {
        method: "PUT",
        body: JSON.stringify({
          max_steps: parseInt($("#sp-maxsteps").value),
          chunk_size: parseInt($("#sp-chunksize").value),
          retrieve_k: parseInt($("#sp-retrievek").value),
        }),
      });
      toast("Agent 参数已保存", "ok");
    };
  }
}

// ==================== 运行历史面板 (trace 回放) ====================
const runsPanel = $("#runs-panel");
const scrimEl = $("#scrim");

function _fmtTime(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  return d.toLocaleString("zh-CN", { hour12: false, month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}
function _fmtDuration(sec) {
  if (!sec || sec < 0) return "—";
  if (sec < 60) return `${sec.toFixed(1)}s`;
  return `${Math.floor(sec / 60)}m${Math.floor(sec % 60)}s`;
}
function _fmtTokens(n) {
  if (!n) return "0";
  if (n < 1000) return String(n);
  return `${(n / 1000).toFixed(1)}k`;
}
function _fmtCost(c) {
  if (!c) return "$0";
  if (c < 0.01) return `$${c.toFixed(4)}`;
  return `$${c.toFixed(2)}`;
}
// SSE 事件类型 → 中文标签 + 颜色
const EVENT_META = {
  start:         { label: "开始",     color: "var(--blue)" },
  llm_call:      { label: "LLM 调用", color: "var(--accent)" },
  tool_call:     { label: "调工具",   color: "var(--yellow)" },
  tool_result:   { label: "工具结果", color: "var(--green)" },
  delegate:      { label: "委派",     color: "var(--accent)" },
  delegate_done: { label: "委派完成", color: "var(--green)" },
  error:         { label: "错误",     color: "var(--red)" },
  end:           { label: "结束",     color: "var(--muted)" },
};

async function openRunsPanel() {
  if (!currentProject) return toast("请先选择项目", "warn");
  runsPanel.classList.add("open");
  scrimEl?.classList.add("show");
  $("#rp-detail").classList.add("hidden");
  $("#rp-runs").classList.remove("hidden");
  $("#rp-metrics").textContent = "加载中…";
  $("#rp-runs").innerHTML = '<div class="rp-loading">加载中…</div>';
  try {
    const [metrics, runs] = await Promise.all([
      api(`/api/projects/${currentProject.id}/metrics`),
      api(`/api/projects/${currentProject.id}/runs`),
    ]);
    renderProjectMetrics(metrics);
    renderRunsList(runs);
  } catch (e) { /* api 已 toast */ }
}

function renderProjectMetrics(m) {
  const html = `
    <div class="rp-metric"><div class="rp-metric-num">${m.total_runs}</div><div class="rp-metric-lbl">总运行</div></div>
    <div class="rp-metric"><div class="rp-metric-num">${_fmtTokens(m.total_tokens)}</div><div class="rp-metric-lbl">tokens</div></div>
    <div class="rp-metric"><div class="rp-metric-num">${_fmtCost(m.total_cost_usd)}</div><div class="rp-metric-lbl">成本</div></div>
    <div class="rp-metric"><div class="rp-metric-num">${_fmtDuration(m.avg_run_duration_sec)}</div><div class="rp-metric-lbl">平均耗时</div></div>
    <div class="rp-metric"><div class="rp-metric-num">${m.total_tool_calls}</div><div class="rp-metric-lbl">工具调用</div></div>
  `;
  $("#rp-metrics").innerHTML = html;
}

function renderRunsList(runs) {
  if (!runs.length) {
    $("#rp-runs").innerHTML = '<div class="rp-empty"><div class="rp-empty-icon">📭</div><p>暂无运行记录</p><p class="muted">每次发消息触发 agent loop 都会自动记录</p></div>';
    return;
  }
  const html = runs.map((r) => {
    const statusCls = r.status === "done" ? "ok" : (r.status === "error" ? "err" : "running");
    const statusIcon = r.status === "done" ? "✓" : (r.status === "error" ? "✗" : "…");
    return `<button class="rp-item" data-rid="${r.id}">
      <div class="rp-item-main">
        <span class="rp-item-status ${statusCls}">${statusIcon}</span>
        <span class="rp-item-input">${esc((r.user_input || "").slice(0, 60))}${(r.user_input||"").length > 60 ? "…" : ""}</span>
      </div>
      <div class="rp-item-meta">
        <span>${_fmtTime(r.started_at)}</span>
        <span>${r.total_steps} 步</span>
        <span>${_fmtTokens(r.total_tokens)} tok</span>
        <span>${_fmtCost(r.total_cost)}</span>
      </div>
    </button>`;
  }).join("");
  $("#rp-runs").innerHTML = html;
  $$("#rp-runs .rp-item").forEach((el) => {
    el.onclick = async () => {
      const rid = el.dataset.rid;
      await showRunDetail(rid);
    };
  });
}

async function showRunDetail(runId) {
  $("#rp-runs").classList.add("hidden");
  $("#rp-detail").classList.remove("hidden");
  $("#rp-detail-title").textContent = "加载中…";
  $("#rp-timeline").innerHTML = "";
  try {
    const data = await api(`/api/runs/${runId}`);
    const r = data.run;
    const events = data.events || [];
    const dur = r.ended_at ? (r.ended_at - r.started_at) : 0;
    $("#rp-detail-title").textContent = r.user_input.slice(0, 50) + (r.user_input.length > 50 ? "…" : "");
    const headHtml = `
      <div class="rp-run-head">
        <span class="rp-badge ${r.status}">${r.status}</span>
        <span>入口: ${esc(r.entry_agent)}</span>
        <span>${r.total_steps} 步</span>
        <span>${_fmtTokens(r.total_tokens)} tokens</span>
        <span>${_fmtCost(r.total_cost)}</span>
        <span>${_fmtDuration(dur)}</span>
        ${r.error ? `<span class="rp-err">${esc(r.error)}</span>` : ""}
      </div>`;
    const timelineHtml = events.map((e) => {
      const meta = EVENT_META[e.type] || { label: e.type, color: "var(--muted)" };
      let body = "";
      if (e.input) body += `<div class="rp-ev-in"><b>输入:</b> ${esc(e.input)}</div>`;
      if (e.output) body += `<div class="rp-ev-out"><b>输出:</b> ${esc(e.output)}</div>`;
      if (e.error) body += `<div class="rp-ev-err"><b>错误:</b> ${esc(e.error)}</div>`;
      const meta2 = [];
      if (e.agent) meta2.push(`<span class="rp-ev-tag">${esc(e.agent)}</span>`);
      if (e.tool) meta2.push(`<span class="rp-ev-tag">🔧 ${esc(e.tool)}</span>`);
      if (e.tokens) meta2.push(`<span class="rp-ev-tag">${_fmtTokens(e.tokens)} tok</span>`);
      if (e.cost) meta2.push(`<span class="rp-ev-tag">${_fmtCost(e.cost)}</span>`);
      if (e.duration_ms) meta2.push(`<span class="rp-ev-tag">${(e.duration_ms / 1000).toFixed(2)}s</span>`);
      return `<div class="rp-ev" style="--ev-color:${meta.color}">
        <div class="rp-ev-dot"></div>
        <div class="rp-ev-body">
          <div class="rp-ev-head">
            <span class="rp-ev-type">${meta.label}</span>
            ${meta2.join("")}
          </div>
          ${body}
        </div>
      </div>`;
    }).join("");
    $("#rp-timeline").innerHTML = headHtml + timelineHtml;
  } catch (e) { /* api 已 toast */ }
}

// 绑定按钮
$("#runs-btn")?.addEventListener("click", () => {
  if (runsPanel.classList.contains("open")) {
    closeRunsPanel();
  } else {
    openRunsPanel();
  }
});
$("#rp-close")?.addEventListener("click", closeRunsPanel);
$("#rp-back")?.addEventListener("click", () => {
  $("#rp-detail").classList.add("hidden");
  $("#rp-runs").classList.remove("hidden");
});
function closeRunsPanel() {
  runsPanel.classList.remove("open");
  // 同时关闭 scrim (仅当没其他面板打开时)
  if (!$("#settings-panel").classList.contains("open") &&
      !$("#skills-panel").classList.contains("open")) {
    scrimEl?.classList.remove("show");
  }
}
