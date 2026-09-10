// Agent 运行循环 — ReAct 模式 (Reason + Act)
// 支持: 工具调用 / 子 agent 委派 / 并行工具 / 心跳 / 循环检测
import { chat, stream as llmStream, friendlyError, THINK_PREFIX, TOOLCALL_PREFIX } from "../llm.js";
import { getSettings } from "../config.js";
import { isToolAllowedInPhase, advancePhase, getPhaseInfo } from "../workflow.js";
import * as store from "../store.js";
import * as memory from "../memory/index.js";
import { getMeta, isValid, isReadonly, AGENT_TOOLS, getMaxDelegateDepth, DEFAULT_AGENT, getAgentPrompt } from "./index.js";
import { getAgentSkillPrompts } from "../skills.js";
import { isPromptLeakage } from "../think_filter.js";
import type { AgentName, SSEEvent, ToolResult, ToolCall, ToolDefinition } from "../types.js";
import { precheckCode } from "../security/sandbox.js";
import { randomBytes } from "node:crypto";
import { browserSearch, browserFetch, browserScreenshot } from "../browser.js";

// ===== Python 技能引擎桥接 =====
import { callSkill, type SkillAction } from "../skills/engine.js";

// TypeScript 技能引擎 — 直接调用，无需 Python
async function callSkillBridge(action: string, args: Record<string, unknown> = {}): Promise<Record<string, unknown>> {
  return callSkill(action as SkillAction, args);
}

// ===== 工具调度 =====
// ===== 工具调度 =====
// 将 Agent 的工具调用路由到具体实现
// 完整的 36 个工具 (从 Python 版 tools.py 迁移)

export async function dispatchTool(
  pid: string,
  name: string,
  args: Record<string, unknown>,
  agentName: string,
): Promise<string> {
  // 沙箱检查: 只读 agent 不允许写入工具
  if (isReadonly(agentName)) {
    const writeTools = new Set([
      "generate_outline", "continue_writing", "polish", "add_element",
      "manage_character", "manage_world", "manage_milestone", "manage_outline",
      "scan_bestseller", "analyze_novel", "ghostwrite", "imitate_style",
    ]);
    if (writeTools.has(name)) {
      return JSON.stringify({ error: `只读 agent (${agentName}) 不能调用写入工具 ${name}` });
    }
  }

  try {
    switch (name) {
      // ===== 项目查询 =====
      case "query_project": {
        const proj = store.getProject(pid);
        if (!proj) return JSON.stringify({ error: "项目不存在" });
        return JSON.stringify({
          project: proj,
          stats: store.stats(pid),
          chapters: store.listChapters(pid).map((c) => ({
            id: c.id, title: c.title, idx: c.idx, status: c.status,
            contentChars: c.content?.length || 0, hasContent: !!c.content, hasOutline: !!c.outline,
          })),
          elements: store.listElements(pid),
          foreshadowings: store.listForeshadowings(pid),
          characterStates: store.listCharacterStates(pid),
        });
      }

      // ===== 工作流模式切换 =====
      case "switch_workflow_mode": {
        const mode = String(args.mode || "state_machine");
        if (mode !== "state_machine" && mode !== "crewai") {
          return JSON.stringify({ error: `无效模式: ${mode}，可选: state_machine, crewai` });
        }
        try {
          const { saveSettings } = await import("../config.js");
          const s = getSettings();
          s.workflowMode = mode as "state_machine" | "crewai";
          saveSettings();
          return JSON.stringify({ 
            success: true, 
            mode, 
            message: `工作流模式已切换为: ${mode === "state_machine" ? "状态机（严格阶段执行）" : "CrewAI（灵活协作）"}` 
          });
        } catch (e) {
          return JSON.stringify({ error: `切换失败: ${(e as Error).message}` });
        }
      }

      // ===== 查询工作流状态 =====
      case "query_workflow": {
        const ws = store.getProjectState(pid);
        const { getPhaseInfo } = await import("../workflow.js");
        const phaseInfo = getPhaseInfo(ws.currentPhase as import("../workflow.js").Phase);
        return JSON.stringify({
          mode: getSettings().workflowMode || "state_machine",
          currentPhase: ws.currentPhase,
          phaseName: phaseInfo.name,
          phaseDesc: phaseInfo.description,
          allowedNext: phaseInfo.allowedNext,
          hasCharacters: ws.hasCharacters,
          hasWorld: ws.hasWorld,
          hasOutline: ws.hasOutline,
          hasContent: ws.hasContent,
          hasChapters: ws.hasChapters,
        });
      }

      // ===== 导出项目数据 =====
      case "export_project_data": {
        const fmt = String(args.format || "txt");
        const exportData = store.getExportData(pid, fmt);
        if (fmt === "json") return JSON.stringify(exportData);
        const text = (exportData as { text: string }).text;
        // 保存到文件
        const { writeFileSync, mkdirSync } = await import("node:fs");
        const { join } = await import("node:path");
        const dataDir = getSettings().dataDir;
        mkdirSync(join(dataDir, "exports"), { recursive: true });
        const exportProj = store.getProject(pid);
        const filename = `${exportProj?.name || "未命名"}_${Date.now()}.txt`;
        writeFileSync(join(dataDir, "exports", filename), text, "utf-8");
        return JSON.stringify({
          success: true,
          format: fmt,
          filename,
          path: `/exports/${filename}`,
          chars: text.length,
          preview: text.slice(0, 500),
        });
      }

      // ===== 扫榜调研 =====
      case "scan_bestseller": {
        const platform = String(args.platform || "起点中文网");
        const genre = String(args.genre || "");
        // 支持三江 / 往期三江
        const board = String(args.board || args.rank || "");
        const rawCrawl = await crawlRanking(platform, genre, board);
        let analysis = rawCrawl.rawText;
        // 若有成功抓取的正文,直接分析
        const resp = await chat([
          { role: "system", content: "你是网文市场分析师。根据扫榜数据,分析当前热门题材、读者偏好、流量趋势。给出具体的创作建议。如果是三江推荐则重点分析推荐位的风格风向。" },
          { role: "user", content: `平台: ${platform}\n题材方向: ${genre || "全品类"}\n榜单: ${board || "默认畅销/推荐"}\n\n榜单数据:\n${(analysis || "").slice(0, 6000)}\n\n请分析市场趋势并给出创作建议。` },
        ]);
        return JSON.stringify({
          platform, genre, board: rawCrawl.board,
          fromUrl: rawCrawl.url,
          source: rawCrawl.source,
          items: rawCrawl.items,
          analysis: resp.content,
        });
      }

      // ===== 拆书解构 =====
      case "analyze_novel": {
        const text = String(args.text || args.content || "");
        const title = String(args.title || "未知作品");
        if (!text && !title) return JSON.stringify({ error: "需要提供小说文本或书名" });
        let inputText = text;
        // 如果只给了书名,先搜索获取内容
        if (!text && title) {
          let sr: any = null;
          try { sr = await browserSearch(`${title} 小说 精彩片段 经典段落`, 5); } catch { sr = await webSearch(`${title} 小说 精彩片段 经典段落`, 5); }
          for (const r of (sr.results || []).slice(0, 2)) {
            try {
              const page = await webFetch(r.url, 4000);
              if (page.content) inputText += page.content + "\n";
            } catch { /* skip */ }
          }
        }
        const resp = await chat([
          { role: "system", content: `你是资深网文编辑,擅长拆解小说结构。
从以下维度分析:
1. 钩子设计:开篇如何抓住读者
2. 节奏把控:爽点/低谷分布
3. 人设塑造:主角/配角的立体度
4. 文风特点:句式/叙事视角/对话风格
5. 世界观:设定的自洽性与创新度
6. 伏笔布局:线索埋设与回收
给出具体可借鉴的写作技巧。` },
          { role: "user", content: `作品: ${title}\n\n文本内容:\n${inputText.slice(0, 8000)}\n\n请深度拆解这部作品。` },
        ]);
        return JSON.stringify({ title, analysis: resp.content, textLength: inputText.length });
      }

      // ===== 大纲生成 =====
      case "generate_outline": {
        const premise = String(args.premise || "");
        const numChapters = Number(args.num_chapters) || 12;
        const genre = String(args.genre || "");
        // 用 LLM 生成大纲
        const s = getSettings();
        const resp = await chat([
          { role: "system", content: "你是小说大纲架构师。根据核心设定生成详细大纲。返回 JSON 格式。\n格式: {\"premise\":\"...\",\"chapters\":[{\"title\":\"第X章 标题\",\"outline\":\"该章剧情概要\"}]}" },
          { role: "user", content: `核心设定: ${premise}\n类型: ${genre}\n章节数: ${numChapters}\n请生成大纲。` },
        ]);
        // 解析 JSON 大纲
        let chapterTitles: Array<{ title: string; outline: string }> = [];
        try {
          const parsed = JSON.parse(resp.content);
          if (Array.isArray(parsed.chapters) && parsed.chapters.length) {
            chapterTitles = parsed.chapters.map((c: any) => ({ title: String(c.title || "").trim(), outline: String(c.outline || "").trim() }));
          }
        } catch { /* 非JSON则尝试文本解析 */ }
        if (chapterTitles.length === 0) {
          // 文本格式: 匹配 "第X章 xxx" 或 "一、xxx"
          for (const line of resp.content.split("\n")) {
            const m = line.match(/^(第[\d一二三四五六七八九十百千]+章[\s：:]*|[\d一二三四五六七八九十]+\.\s*)(.+)/);
            if (m) {
              chapterTitles.push({ title: line.trim().slice(0, 40), outline: "" });
              if (chapterTitles.length >= numChapters) break;
            }
          }
        }
        // 确保有章节骨架
        const existing = store.listChapters(pid);
        if (existing.length === 0) {
          if (chapterTitles.length === 0) {
            for (let i = 1; i <= Math.min(numChapters, 12); i++) {
              chapterTitles.push({ title: `第${i}章`, outline: "" });
            }
          }
          chapterTitles.forEach((c, idx) => {
            store.addChapter(pid, c.title || `第${idx+1}章`, idx, c.outline || "", "");
          });
        }
        return JSON.stringify({
          outline: resp.content,
          chapters: chapterTitles.length || numChapters,
          chapterTitles: chapterTitles.slice(0, 20),
          createdChapters: existing.length === 0 ? chapterTitles.length : 0,
        });
      }

      // ===== 续写 =====
      case "continue_writing": {
        const chapterId = String(args.chapter_id || "");
        const instruction = String(args.instruction || "");
        const length = Number(args.length) || 2000;
        // 章节解析: 优先指定ID, 无效时自动用最后章节, 无章节则报错
        let ch = chapterId ? store.getChapter(chapterId) : undefined;
        const chapters = store.listChapters(pid);
        const lastChapter = chapters[chapters.length - 1];
        if (!ch && lastChapter) {
          ch = store.getChapter(lastChapter.id);
        }
        if (!ch) {
          // 自动创建第一章 (自愈: 即使 agent 忘记先建大纲也能写作)
          const newChId = store.addChapter(pid, "第一章", 0, String(instruction).slice(0, 200), "");
          ch = store.getChapter(newChId);
          if (!ch) return JSON.stringify({ error: "章节创建失败" });
        }
        const prevContent = ch.content || "";

        // === FIX 4: 加载完整上下文 ===
        const projCtx = store.getProject(pid);
        const profiles = store.listCharacterProfiles(pid);
        const worldEntries = store.listWorldEntries(pid);
        const elements = store.listElements(pid);
        const outline = ch?.outline || "";

        let contextBlock = "";
        if (projCtx) contextBlock += `项目: ${projCtx.name} | 类型: ${projCtx.genre} | 文风: ${projCtx.style}\n核心设定: ${projCtx.premise}\n\n`;
        if (profiles.length) {
          contextBlock += "【角色档案】\n" + profiles.map(p => `- ${p.name}(${p.role || "?"}): ${p.personality || ""} | 说话风格: ${p.speechStyle || ""} | 动机: ${p.motivation || ""}`).join("\n") + "\n\n";
        }
        if (worldEntries.length) {
          contextBlock += "【世界观设定】\n" + worldEntries.map(w => `- [${w.category}] ${w.name}: ${w.description || ""}`).join("\n") + "\n\n";
        }
        if (elements.length) {
          contextBlock += "【其他设定】\n" + elements.map(e => `- [${e.kind}] ${e.name}: ${e.detail || ""}`).join("\n") + "\n\n";
        }
        if (outline) contextBlock += `【本章大纲】\n${outline}\n\n`;

        const s = getSettings();
        const resp = await chat([
          { role: "system", content: "你是专业小说主笔。根据上下文和指令续写小说正文。\n必须保持角色性格一致、设定自洽、文风统一。\n禁止出现AI味词汇(如:不由得、仿佛、一股暖流、心中暗道)。" },
          { role: "user", content: `${contextBlock}上文:\n${prevContent.slice(-3000)}\n\n指令: ${instruction}\n请续写约 ${length} 字。` },
        ]);
        // 保存到章节
        const merged = (ch.content || "") + (ch.content ? "\n" : "") + resp.content;
        store.updateChapter(ch.id, { content: merged, status: "done" });
        return JSON.stringify({ text: resp.content, chars: resp.content.length, chapterId: ch.id, saved: true });
      }

      // ===== 润色 =====
      case "polish": {
        const chapterId = String(args.chapter_id || "");
        const mode = String(args.mode || "polish");
        const instruction = String(args.instruction || "");
        const ch = store.getChapter(chapterId);
        if (!ch) return JSON.stringify({ error: "章节不存在" });
        const s = getSettings();
        const modeMap: Record<string, string> = {
          polish: "润色改进", rewrite: "重写", expand: "扩写", condense: "缩写",
        };
        // === FIX 4: 加载角色上下文 ===
        const plProfiles = store.listCharacterProfiles(pid);
        let plCtx = "";
        if (plProfiles.length) plCtx = "【角色参考】\n" + plProfiles.map(p => `- ${p.name}: ${p.speechStyle || p.personality || ""}`).join("\n") + "\n\n";
        const resp = await chat([
          { role: "system", content: `你是专业编辑。对小说正文进行${modeMap[mode] || "润色"}。保持角色说话风格一致。禁止AI味词汇。` },
          { role: "user", content: `${plCtx}原文:\n${ch.content}\n\n${instruction ? "指令: " + instruction + "\n" : ""}请${modeMap[mode] || "润色"}。` },
        ]);
        store.updateChapter(chapterId, { content: resp.content, status: "done" });
        return JSON.stringify({ text: resp.content, chars: resp.content.length });
      }

      // ===== 角色管理 =====
      case "manage_character": {
        const action = String(args.action || "list");
        if (action === "list") {
          return JSON.stringify({ profiles: store.listCharacterProfiles(pid) });
        }
        const cname = String(args.name || "");
        if (!cname) return JSON.stringify({ error: "需要 name 参数" });
        if (action === "query") {
          const p = store.getCharacterProfile(pid, cname);
          return JSON.stringify(p || { error: `未找到角色 ${cname}` });
        }
        // create/update
        const fields: Record<string, string> = {};
        for (const k of ["role", "personality", "speechStyle", "behaviorLogic", "motivation", "arc", "growthState"]) {
          if (args[k] !== undefined) fields[k] = String(args[k]);
        }
        const id = store.upsertCharacterProfile(pid, cname, fields as any);
        return JSON.stringify({ id, name: cname, action });
      }

      // ===== 设定管理 =====
      case "add_element": {
        const kind = String(args.kind || "");
        const elName = String(args.name || "");
        const detail = String(args.detail || "");
        if (!kind || !elName) return JSON.stringify({ error: "需要 kind 和 name" });
        const id = store.addElement(pid, kind, elName, detail);
        return JSON.stringify({ id, kind, name: elName });
      }

      // ===== 世界观 =====
      case "manage_world": {
        const action = String(args.action || "list");
        if (action === "list") {
          const category = args.category ? String(args.category) : undefined;
          return JSON.stringify({ entries: store.listWorldEntries(pid, category) });
        }
        if (action === "add") {
          const category = String(args.category || "");
          const name = String(args.name || "");
          const description = String(args.description || "");
          if (!category || !name) return JSON.stringify({ error: "需要 category 和 name" });
          const id = store.addWorldEntry(pid, category, name, description);
          return JSON.stringify({ id, category, name });
        }
        if (action === "update") {
          const id = String(args.id || "");
          if (!id) return JSON.stringify({ error: "需要 id" });
          store.updateWorldEntry(id, { description: args.description as string, attributes: args.attributes as string });
          return JSON.stringify({ id, status: "updated" });
        }
        if (action === "delete") {
          const id = String(args.id || "");
          if (!id) return JSON.stringify({ error: "需要 id" });
          store.deleteWorldEntry(id);
          return JSON.stringify({ id, status: "deleted" });
        }
        return JSON.stringify({ error: "未知 action" });
      }

      // ===== 里程碑 =====
      case "manage_milestone": {
        const action = String(args.action || "list");
        if (action === "list") {
          return JSON.stringify({ milestones: store.listMilestones(pid) });
        }
        if (action === "add") {
          const chapterIdx = Number(args.chapter_idx) || 0;
          const title = String(args.title || "");
          const description = String(args.description || "");
          if (!title) return JSON.stringify({ error: "需要 title" });
          const id = store.addMilestone(pid, chapterIdx, title, description);
          return JSON.stringify({ id, chapter_idx: chapterIdx, title });
        }
        if (action === "update") {
          const id = String(args.id || "");
          if (!id) return JSON.stringify({ error: "需要 id" });
          store.updateMilestone(id, { status: args.status as string, reached_chapter: args.reached_chapter as number });
          return JSON.stringify({ id, status: "updated" });
        }
        if (action === "delete") {
          const id = String(args.id || "");
          if (!id) return JSON.stringify({ error: "需要 id" });
          store.deleteMilestone(id);
          return JSON.stringify({ id, status: "deleted" });
        }
        return JSON.stringify({ error: "未知 action" });
      }

      // ===== 大纲管理 =====
      case "manage_outline": {
        const action = String(args.action || "list");
        if (action === "list") {
          const chapters = store.listChapters(pid);
          return JSON.stringify({ outlines: chapters.map(c => ({ id: c.id, title: c.title, idx: c.idx, outline: c.outline })) });
        }
        if (action === "update") {
          const chapterId = String(args.chapter_id || "");
          const outline = String(args.outline || "");
          if (!chapterId) return JSON.stringify({ error: "需要 chapter_id" });
          store.updateChapterOutline(chapterId, outline);
          return JSON.stringify({ chapter_id: chapterId, status: "updated" });
        }
        return JSON.stringify({ error: "未知 action" });
      }

      // ===== 毒舌审稿 =====
      case "review_chapter": {
        const chapterId = String(args.chapter_id || "");
        const ch = store.getChapter(chapterId);
        if (!ch) return JSON.stringify({ error: "章节不存在" });
        const s = getSettings();
        const resp = await chat([
          { role: "system", content: "你是毒舌总编。以严苛标准审稿。评分 1-10, <7 打回。给出具体修改意见。\n输出格式: [评分: X/10] 然后给出意见。" },
          { role: "user", content: `章节: ${ch.title}\n正文:\n${ch.content.slice(0, 3000)}\n\n请审稿评分。` },
        ]);
        // 自动解析评分并更新章节状态
        const scoreMatch = resp.content.match(/\[?评分[：:]?\s*(\d+)/i) || resp.content.match(/(\d+)\s*\/\s*10/);
        const score = scoreMatch ? parseInt(scoreMatch[1]) : 0;
        if (score >= 7) {
          store.updateChapter(chapterId, { status: "reviewed" });
        } else if (score > 0) {
          store.updateChapter(chapterId, { status: "revision_needed" });
        }
        return JSON.stringify({ review: resp.content, chapterId, score, status: score >= 7 ? "reviewed" : "revision_needed" });
      }

      // ===== 网络搜索 =====
      case "web_search": {
        const query = String(args.query || "");
        const maxResults = Number(args.max_results) || 8;
        const results = await webSearch(query, maxResults);
        return JSON.stringify(results);
      }

      case "web_fetch": {
        const url = String(args.url || "");
        const maxChars = Number(args.max_chars) || 8000;
        const result = await webFetch(url, maxChars);
        return JSON.stringify(result);
      }

      case "browser_search": {
        const query = String(args.query || "");
        const maxResults = Number(args.max_results) || 10;
        const results = await browserSearch(query, maxResults);
        return JSON.stringify(results);
      }

      case "browser_fetch": {
        const url = String(args.url || "");
        const maxChars = Number(args.max_chars) || 12000;
        const waitMs = Number(args.wait_ms) || 1500;
        if (!url.startsWith("http")) return JSON.stringify({ error: "url 必须以 http(s):// 开头" });
        const result = await browserFetch(url, maxChars, waitMs);
        return JSON.stringify(result);
      }

      case "browser_screenshot": {
        const url = String(args.url || "");
        const name = String(args.name || "shot");
        const width = Number(args.width) || 1280;
        const height = Number(args.height) || 800;
        if (!url.startsWith("http")) return JSON.stringify({ error: "url 必须以 http(s):// 开头" });
        const result = await browserScreenshot(url, name, width, height);
        return JSON.stringify(result);
      }

      // ===== 技能工具 (LLM 驱动) =====
      case "match_author": {
        const genre = String(args.genre || "");
        const style = String(args.style || "");
        const premise = String(args.premise || "");
        const bridgeResult = await callSkillBridge("match_author", { genre, style, premise });
        if (bridgeResult.error) {
          const resp = await chat([
            { role: "system", content: "你是小说创作顾问。根据用户需求推荐 1-3 位最适合的网文作家,说明推荐理由。" },
            { role: "user", content: `推荐作家: 题材=${genre} 文风=${style}\n请推荐并说明理由。` },
          ]);
          return JSON.stringify({ recommendation: resp.content });
        }
        return JSON.stringify({ recommendations: bridgeResult });
      }
      case "get_author_reference": {
        const author = String(args.author || "");
        const scene = String(args.scene || "");
        const bridgeResult = await callSkillBridge("get_author_reference", { author, scene });
        if (bridgeResult.error) {
          const resp = await chat([
            { role: "system", content: `你是${author}的写作风格分析专家。分析其${scene || "通用"}场景下的写作风格特征。` },
            { role: "user", content: `分析${author}的${scene || "通用"}写作风格,给出句式节奏、信息密度、断句习惯等特征。` },
          ]);
          return JSON.stringify({ author, scene, analysis: resp.content });
        }
        return JSON.stringify(bridgeResult);
      }
      case "list_authors": {
        const bridgeResult = await callSkillBridge("list_authors");
        if (bridgeResult.error) {
          const resp = await chat([
            { role: "system", content: "你是网文知识专家。" },
            { role: "user", content: "列出中国网文界 20 位代表性作家,按流派分类。" },
          ]);
          return JSON.stringify({ authors: resp.content });
        }
        return JSON.stringify(bridgeResult);
      }
      case "deconstruct": {
        const query = String(args.query || args.input || "");
        const bridgeResult = await callSkillBridge("deconstruct", { input: query });
        if (bridgeResult.error) {
          // 降级到 LLM
          const resp = await chat([
            { role: "system", content: "你是顶级网文拆书师。从钩子/节奏/人设/文风/世界观等维度深度拆解作品。" },
            { role: "user", content: query },
          ]);
          return JSON.stringify({ deconstruction: resp.content });
        }
        return JSON.stringify(bridgeResult);
      }
      case "analyze_style": {
        const text = String(args.text || "");
        const bridgeResult = await callSkillBridge("analyze_style", { text });
        if (bridgeResult.error) {
          const resp = await chat([
            { role: "system", content: "你是文风分析专家。分析文本的句式节奏、信息密度、断句习惯、对话比例、修辞手法。" },
            { role: "user", content: `分析以下文本的文风特征:\n\n${text.slice(0, 3000)}` },
          ]);
          return JSON.stringify({ style_analysis: resp.content });
        }
        return JSON.stringify(bridgeResult);
      }
      case "imitate_style": {
        const refText = String(args.reference_text || "");
        const topic = String(args.topic || "");
        const wordCount = Number(args.word_count) || 800;
        const bridgeResult = await callSkillBridge("imitate_style", { reference_text: refText, topic, word_count: wordCount });
        if (bridgeResult.error) {
          const resp = await chat([
            { role: "system", content: "你是文风仿写大师。严格模仿参考文本的句式节奏、断句习惯、信息密度来创作。" },
            { role: "user", content: `参考原文:\n${refText.slice(0, 2000)}\n\n请模仿以上文风,写一段关于「${topic}」的${wordCount}字内容。` },
          ]);
          return JSON.stringify({ imitated_text: resp.content, topic, word_count: wordCount });
        }
        return JSON.stringify(bridgeResult);
      }
      case "diagnose_stuck": {
        const text = String(args.text || "");
        const lastSummary = String(args.last_chapter_summary || "");
        const bridgeResult = await callSkillBridge("diagnose_stuck", { text, last_chapter_summary: lastSummary });
        if (bridgeResult.error) {
          const resp = await chat([
            { role: "system", content: "你是写作诊断专家。分析卡文原因并给出续写方向建议。" },
            { role: "user", content: `以下是我写的正文,但写不下去了:\n\n${text.slice(0, 3000)}\n\n请诊断原因并给续写建议。` },
          ]);
          return JSON.stringify({ diagnosis: resp.content });
        }
        return JSON.stringify(bridgeResult);
      }
      case "audit_novel": {
        const text = String(args.text || "");
        const outline = String(args.outline || "");
        const bridgeResult = await callSkillBridge("audit", { text, outline });
        if (bridgeResult.error) {
          const auditSystemPrompt = `你是资深小说质检员。按35维逐项审计，输出结构化JSON。

严重度分级(对齐oh-story S1-S4)：
S1-严重：影响主线/角色动机/世界规则/读者信任
S2-中等：影响留存/节奏/章节效果/人物可信度
S3-轻微：局部质量/格式/措辞/轻微节奏问题
S4-建议：风格建议或可选增强

35维度：
【角色一致性(1-7)】1.主角性格矛盾(S1) 2.配角工具化(S3) 3.关系进展自然度(S2) 4.角色智商在线(S2) 5.口头禅统一(S3) 6.外貌一致性(S2) 7.角色时间线(S1)
【物资与战力(8-13)】8.法宝遗忘(S2) 9.战力崩坏(S1) 10.物资矛盾(S2) 11.资源合理(S3) 12.突破代价(S2) 13.货币混乱(S2)
【伏笔与逻辑(14-20)】14.伏笔遗忘(S1) 15.回收生硬(S2) 16.逻辑漏洞(S1) 17.巧合过多(S2) 18.时间线(S1) 19.信息获取(S2) 20.反派逻辑(S2)
【文风与表达(21-27)】21.AI味(S2) 22.描写冗长(S3) 23.战斗枯燥(S2) 24.情绪到位(S2) 25.幽默自然(S3) 26.对话质量(S2) 27.感官丰富(S3)
【结构与节奏(28-32)】28.章节钩子(S2) 29.爽点密度(S2) 30.开篇抓人(S2) 31.高潮燃度(S3) 32.支线挤压(S2)
【大纲与偏离(33-35)】33.偏离大纲(S1) 34.对话密集(S3) 35.背景过多(S3)

每个finding必须包含：severity(严重度), dimension_id(维度号), dimension(维度名), location(位置), evidence(证据), issue(问题), fix(修复建议)。
判定：有S1→REJECT，有S2→CONCERNS，否则→APPROVE。
输出：{"findings":[{"severity":"S1","dimension_id":1,"dimension":"主角性格矛盾","location":"第X段","evidence":"原文片段","issue":"问题描述","fix":"修复建议"}],"highlights":[{"dimension_id":N,"dimension":"名称","issue":"亮点"}],"verdict":"APPROVE/CONCERNS/REJECT","summary":"总评","platform":"generic"}`;
          const resp = await chat([
            { role: "system", content: auditSystemPrompt },
            { role: "user", content: `审计正文:\n\n${text.slice(0, 8000)}${outline ? "\n\n大纲参考:\n" + outline.slice(0, 3000) : ""}\n\n请严格按35维逐项审计，输出JSON。` },
          ]);
          return JSON.stringify({ audit_report: resp.content, text_length: text.length, dimensions: 35 });
        }
        return JSON.stringify(bridgeResult);
      }
      case "detect_ai": {
        const text = String(args.text || "");
        const bridgeResult = await callSkillBridge("detect_ai", { text });
        if (bridgeResult.error) {
          const aiDetectPrompt = `你是去AI味检测专家。按三遍法检测，输出结构化JSON。

第一遍-模式扫描：检测AI写作指纹(高频套话/情绪告知/章末总结体/叠加式描写/排比堆砌/比喻滥用/引号滥用)
第二遍-深度分析：结构性问题(情绪告知密度/段落长度均匀度/连续排比)
第三遍-综合评估：AI味浓度(0-10) + 等级(轻度/中度/重度) + 删除上限(15%/25%/35%)

每个trace必须包含：category(类别), count(数量), reason(原因), fix(修改建议), evidence(证据:原文片段+位置)。
输出：{"ai_score":0-10,"level":"light/moderate/heavy","traces":[{"category":"类别","count":N,"reason":"原因","fix":"修改","evidence":"证据"}],"depth_issues":[{"type":"类型","count":N,"severity":"S1-S4","fix":"修改","evidence":"证据"}],"verdict":"APPROVE/CONCERNS/REJECT","summary":"总评"}`;
          const resp = await chat([
            { role: "system", content: aiDetectPrompt },
            { role: "user", content: `检测以下文本的AI味:\n\n${text.slice(0, 5000)}\n\n请逐项检测，输出JSON。` },
          ]);
          return JSON.stringify({ ai_detection: resp.content, text_length: text.length });
        }
        return JSON.stringify(bridgeResult);
      }
      case "diagnose_opening": {
        const text = String(args.text || "");
        if (text.length < 50) return JSON.stringify({ error: "文本过短,至少需要 50 字" });
        const bridgeResult = await callSkillBridge("diagnose_opening", { text });
        if (bridgeResult.error) {
          const openingPrompt = `你是开篇诊断专家。从以下5个维度诊断开篇质量，输出JSON：
1.钩子(前200字是否抓人) 2.角色(主角是否鲜明) 3.冲突(是否有核心矛盾) 4.世界观(设定是否清晰) 5.节奏(信息密度是否合适)
输出：{"scores":{"hook":评分(1-10),"character":评分,"conflict":评分,"worldbuilding":评分,"pacing":评分},"issues":[{"dim":"维度","msg":"问题"}],"advice":"改进建议"}`;
          const resp = await chat([
            { role: "system", content: openingPrompt },
            { role: "user", content: `诊断开篇质量:\n\n${text.slice(0, 5000)}\n\n请逐维度诊断，输出JSON。` },
          ]);
          return JSON.stringify({ opening_diagnosis: resp.content, text_length: text.length });
        }
        return JSON.stringify(bridgeResult);
      }
      case "full_audit": {
        const text = String(args.text || "");
        const outline = String(args.outline || "");
        const bridgeResult = await callSkillBridge("full_audit", { text, outline });
        if (bridgeResult.error) {
          const fullAuditPrompt = `你是资深小说质检员+AI味检测员。执行完整审计：35维质量检查(S1-S4) + AI味三遍法检测，输出结构化JSON。

35维度同audit_novel，AI味检测同detect_ai。
综合判定规则：
- 有S1发现 → REJECT
- 有S2发现 或 AI味≥7 → CONCERNS
- 否则 → APPROVE

输出：{"findings":[{"severity":"S1","dimension_id":N,"dimension":"名称","location":"位置","evidence":"证据","issue":"问题","fix":"修复"}],"highlights":[...],"ai_detection":{"ai_score":0-10,"level":"light/moderate/heavy","traces":[...]},"verdict":"APPROVE/CONCERNS/REJECT","summary":"综合报告","platform":"generic"}`;
          const resp = await chat([
            { role: "system", content: fullAuditPrompt },
            { role: "user", content: `全面审计:\n\n${text.slice(0, 8000)}${outline ? "\n\n大纲:\n" + outline.slice(0, 3000) : ""}\n\n请执行35维+AI味完整审计，输出JSON。` },
          ]);
          return JSON.stringify({ full_audit: resp.content, text_length: text.length, dimensions: 35 });
        }
        return JSON.stringify(bridgeResult);
      }
      case "ghostwrite": {
        const outlineText = String(args.outline_text || args.outline || "");
        const wordCount = Number(args.words || args.word_count) || 3000;
        const authorName = String(args.author_name || "");

        // === FIX 4: 加载完整上下文 ===
        const gwProj = store.getProject(pid);
        const gwProfiles = store.listCharacterProfiles(pid);
        const gwWorld = store.listWorldEntries(pid);
        let gwContext = "";
        if (gwProj) gwContext += `项目: ${gwProj.name} | 类型: ${gwProj.genre} | 文风: ${gwProj.style}\n设定: ${gwProj.premise}\n\n`;
        if (gwProfiles.length) gwContext += "【角色】\n" + gwProfiles.map(p => `- ${p.name}: ${p.personality || ""}`).join("\n") + "\n\n";
        if (gwWorld.length) gwContext += "【世界观】\n" + gwWorld.map(w => `- ${w.name}: ${w.description || ""}`).join("\n") + "\n\n";

        const bridgeResult = await callSkillBridge("ghostwrite", { outline: outlineText, word_count: wordCount, author_name: authorName });
        let written: string;
        if (!bridgeResult.error && bridgeResult.text) {
          written = String(bridgeResult.text);
        } else {
          const resp = await chat([
            { role: "system", content: `你是专业网文写手。根据大纲写正文,约${wordCount}字。${authorName ? `模仿${authorName}的文风。` : ""}\n开篇抓人,章末留钩子,避免AI味。禁止使用:不由得、仿佛、一股暖流、心中暗道。` },
            { role: "user", content: `${gwContext}大纲:\n${outlineText}\n\n请写约${wordCount}字正文。` },
          ]);
          written = resp.content;
        }
        // 章节解析: 优先指定ID, 无效时自动用最后章节
        let gwCh = args.chapter_id ? store.getChapter(String(args.chapter_id)) : undefined;
        if (!gwCh) {
          const gwChapters = store.listChapters(pid);
          if (gwChapters.length) gwCh = store.getChapter(gwChapters[gwChapters.length - 1].id);
        }
        if (gwCh) {
          const gwMerged = (gwCh.content || "") + (gwCh.content ? "\n" : "") + written;
          store.updateChapter(gwCh.id, { content: gwMerged, status: "done" });
        }
        return JSON.stringify({ ghostwritten_text: written, chars: written.length, chapter: gwCh ? gwCh.id : null, saved: !!gwCh });
      }

      // ===== 质检 =====
      case "four_check":
      case "quality_check": {
        const text = String(args.text || "");
        const chapterIdQC = String(args.chapter_id || "");
        const resp = await chat([
          { role: "system", content: "你是小说质检员。做四重校验:1.逻辑伏笔冲突 2.文笔风格一致性 3.主线推进度 4.角色OOC。\n输出格式:\n[结论: 通过/不通过]\n[问题: ...]（如不通过）" },
          { role: "user", content: `四重校验:\n\n${text.slice(0, 5000)}` },
        ]);
        const passed = /通过/.test(resp.content) && !/不通过/.test(resp.content);
        if (chapterIdQC && passed) {
          store.updateChapter(chapterIdQC, { status: "reviewed" });
        }
        return JSON.stringify({ quality_check: resp.content, text_length: text.length, passed, chapterId: chapterIdQC });
      }

      // ===== 上下文加载 =====
      case "load_context": {
        const proj = store.getProject(pid);
        const chapters = store.listChapters(pid);
        const elements = store.listElements(pid);
        const profiles = store.listCharacterProfiles(pid);
        const world = store.listWorldEntries(pid);
        const milestones = store.listMilestones(pid);
        const foreshadowings = store.listForeshadowings(pid);
        const styleCache = store.getDb().prepare("SELECT * FROM style_cache WHERE project_id=? ORDER BY chapter_idx DESC LIMIT 3").all(pid);
        return JSON.stringify({
          project: proj ? { name: proj.name, genre: proj.genre, premise: proj.premise, style: proj.style } : null,
          chapters_count: chapters.length,
          latest_chapter: chapters.length ? { title: chapters[chapters.length - 1]!.title, content_preview: (chapters[chapters.length - 1]!.content || "").slice(0, 500) } : null,
          elements: elements.slice(0, 20),
          character_profiles: profiles.slice(0, 10),
          world_entries: world.slice(0, 20),
          milestones: milestones.slice(0, 10),
          foreshadowings: foreshadowings.slice(0, 10),
          style_cache: styleCache,
        });
      }

      // ===== 风格缓存 =====
      case "cache_style": {
        const cidx = Number(args.chapter_idx);
        if (isNaN(cidx)) return JSON.stringify({ error: "需要 chapter_idx" });
        store.upsertStyleCache(pid, cidx, String(args.features || ""), String(args.keywords || ""));
        return JSON.stringify({ chapter_idx: cidx, status: "cached" });
      }

      // ===== 对抗式审查 =====
      case "challenge_review": {
        const targetAgent = String(args.target_agent || "");
        const issue = String(args.issue || "");
        const resp = await chat([
          { role: "system", content: `你是对抗式审查员。对 ${targetAgent} 的工作提出质疑和挑战。` },
          { role: "user", content: `挑战 ${targetAgent}: ${issue}` },
        ]);
        return JSON.stringify({ challenge: resp.content, target_agent: targetAgent });
      }
      case "resolve_challenge": {
        const challengeId = String(args.challenge_id || "");
        const response = String(args.response || "");
        return JSON.stringify({ challenge_id: challengeId, response, status: "resolved" });
      }

      // ===== 交付报告 =====
      case "generate_delivery_report": {
        const proj2 = store.getProject(pid);
        const chaps = store.listChapters(pid);
        const totalChars = chaps.reduce((sum, c) => sum + (c.content?.length || 0), 0);
        const doneChaps = chaps.filter(c => c.status === "done" || c.status === "reviewed");
        const resp = await chat([
          { role: "system", content: "你是项目交付报告生成器。生成项目进度、质量、风格一致性的可视化报告。" },
          { role: "user", content: `项目: ${proj2?.name || "未知"}\n类型: ${proj2?.genre || "未知"}\n总章数: ${chaps.length}\n已完成: ${doneChaps.length}\n总字数: ${totalChars}\n请生成交付报告。` },
        ]);
        return JSON.stringify({ report: resp.content, stats: { total_chapters: chaps.length, done: doneChaps.length, total_chars: totalChars } });
      }

      // ===== 技能侦察 =====
      case "skill_scout": {
        const genre = String(args.genre || "");
        const resp = await chat([
          { role: "system", content: "你是网文市场分析师。分析当前网文市场热门题材、读者偏好、流量赛道。" },
          { role: "user", content: `分析${genre || "通用"}题材的市场趋势和创作建议。` },
        ]);
        return JSON.stringify({ market_analysis: resp.content });
      }

      // ===== 委派 =====
      case "delegate_to_agent": {
        return JSON.stringify({ error: "delegate_to_agent 必须由 agent 运行时处理" });
      }

      default:
        return JSON.stringify({ error: `未知工具: ${name}` });
    }
  } catch (e) {
    return JSON.stringify({ error: `工具 ${name} 执行异常: ${(e as Error).message}` });
  }
}

// ===== 网络工具 =====

// ===== 平台榜单直抓 (真实浏览器) =====
// 支持平台 + 多榜单URL映射 + 智能提取

interface PlatformConfig {
  match: string[];
  boards: Record<string, string>;  // board名 -> URL
  defaultBoard: string;
  extract: (text: string, board: string) => string;
}

const PLATFORMS: PlatformConfig[] = [
  // ---- 起点中文网 ----
  {
    match: ["起点", "qidian"],
    defaultBoard: "畅销榜",
    boards: {
      "畅销榜": "https://www.qidian.com/rank/hotsales/",
      "月票榜": "https://www.qidian.com/rank/yuepiao/",
      "人气榜": "https://www.qidian.com/rank/readIndex/",
      "追读榜": "https://www.qidian.com/rank/readIndex/",  // 同人气
      "收藏榜": "https://www.qidian.com/rank/collect/",
      "推荐榜": "https://www.qidian.com/rank/recom/",
      "新书榜": "https://www.qidian.com/rank/newBook/",
      "更新榜": "https://www.qidian.com/rank/update/",
      "三江": "https://www.qidian.com/",
      "往期三江": "https://www.qidian.com/sanjiang/",
    },
    extract: (text, board) => {
      // 三江·网文新风 板块
      if (board === "三江") {
        const sIdx = text.indexOf("三江·网文新风");
        if (sIdx >= 0) {
          const seg = text.slice(sIdx, sIdx + 2000);
          return "三江·网文新风(当前推荐):\n" + seg;
        }
        // 尝试其他关键词
        const s2 = text.indexOf("三江推荐");
        if (s2 >= 0) return "三江推荐:\n" + text.slice(s2, s2 + 2000);
        return "三江板块(未找到具体数据，返回全文摘要):\n" + text.slice(0, 2000);
      }
      // 往期三江
      if (board === "往期三江") {
        // 找日期段落
        const dateMatch = text.match(/\d{4}\.\d{2}\.\d{2}[-–]\d{4}\.\d{2}\.\d{2}/);
        const startIdx = dateMatch ? text.indexOf(dateMatch[0]) : 0;
        return "往期三江推荐:\n" + text.slice(startIdx, startIdx + 3000);
      }
      // 畅销/月票/人气等榜单: 提取排名+书名+作者
      const out: string[] = [];
      const clean = text.replace(/\s+/g, " ");
      // 模式1: 数字序号 + 书名
      for (const m of clean.matchAll(/(\d{1,2})\s*[.、．]\s*([\u4e00-\u9fa5A-Za-z0-9：:《》\-]{2,30})\s*([\u4e00-\u9fa5]{2,10})/g)) {
        out.push(`第${m[1]}名: ${m[2]} (作者: ${m[3]})`);
        if (out.length >= 15) break;
      }
      // 模式2: 纯数字行 (传统起点布局)
      if (out.length === 0) {
        const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
        for (let i = 0; i < lines.length; i++) {
          if (/^\d{1,2}$/.test(lines[i]) && parseInt(lines[i], 10) <= 30) {
            out.push(`第${lines[i]}名: ${lines[i+1] || "?"} (作者: ${lines[i+2] || "?"})`);
            if (out.length >= 15) break;
          }
        }
      }
      return out.length > 0 ? out.join("\n") : clean.slice(0, 2000);
    },
  },

  // ---- 起点女生网 ----
  {
    match: ["起点女生", "qdmm", "女频"],
    defaultBoard: "畅销榜",
    boards: {
      "畅销榜": "https://www.qidian.com/rank/hotsales/fin/",
      "月票榜": "https://www.qidian.com/rank/yuepiao/fin/",
      "人气榜": "https://www.qidian.com/rank/readIndex/fin/",
      "收藏榜": "https://www.qidian.com/rank/collect/fin/",
      "新书榜": "https://www.qidian.com/rank/newBook/fin/",
    },
    extract: (text, board) => {
      const out: string[] = [];
      const clean = text.replace(/\s+/g, " ");
      for (const m of clean.matchAll(/(\d{1,2})\s*[.、．]\s*([\u4e00-\u9fa5A-Za-z0-9：:《》\-]{2,30})\s*([\u4e00-\u9fa5]{2,10})/g)) {
        out.push(`第${m[1]}名: ${m[2]} (作者: ${m[3]})`);
        if (out.length >= 15) break;
      }
      if (out.length === 0) {
        const lines = text.split("\n").map(l => l.trim()).filter(Boolean);
        for (let i = 0; i < lines.length; i++) {
          if (/^\d{1,2}$/.test(lines[i]) && parseInt(lines[i], 10) <= 30) {
            out.push(`第${lines[i]}名: ${lines[i+1] || "?"} (作者: ${lines[i+2] || "?"})`);
            if (out.length >= 15) break;
          }
        }
      }
      return out.length > 0 ? "起点女生网 " + board + ":\n" + out.join("\n") : clean.slice(0, 2000);
    },
  },

  // ---- 番茄小说 ----
  {
    match: ["番茄", "fanqie"],
    defaultBoard: "阅读榜",
    boards: {
      "阅读榜": "https://fanqienovel.com/rank",
      "新书榜": "https://fanqienovel.com/rank",
      "热度榜": "https://fanqienovel.com/rank",
      // 番茄有男频/女频 + 多题材子榜, URL共用 /rank
    },
    extract: (text, board) => {
      const clean = text.replace(/\s+/g, " ");
      const out: string[] = [];
      // 番茄格式: "01 - 书名 作者 (类型)"
      for (const m of clean.matchAll(/(\d{1,2})\s*[-–]\s*([\u4e00-\u9fa5A-Za-z0-9：:《》\-]{2,40})\s*([\u4e00-\u9fa5]{2,10})\s*[（(]?([^)）]*)?[)）]?/g)) {
        out.push(`第${m[1]}名: ${m[2]} (作者: ${m[3]}${m[4] ? ' 类型: ' + m[4] : ''})`);
        if (out.length >= 15) break;
      }
      // 备选: 中文数字序号
      if (out.length === 0) {
        const cnNum = "一二三四五六七八九十";
        for (const m of clean.matchAll(/([一二三四五六七八九十])\s*[、.\-]\s*([\u4e00-\u9fa5A-Za-z0-9：:《》\-]{2,40})/g)) {
          out.push(`${m[1]}、${m[2]}`);
          if (out.length >= 15) break;
        }
      }
      return out.length > 0 ? "番茄小说 " + board + ":\n" + out.join("\n") : clean.slice(0, 2000);
    },
  },

  // ---- 晋江文学城 ----
  {
    match: ["晋江", "jjwxc"],
    defaultBoard: "月票榜",
    boards: {
      "月票榜": "https://www.jjwxc.net/topten.php?orderstr=3&t=0",
      "新晋作者榜": "https://www.jjwxc.net/topten.php?orderstr=9&t=0",
      "收藏榜": "https://www.jjwxc.net/topten.php?orderstr=5&t=0",
      "点击榜": "https://www.jjwxc.net/topten.php?orderstr=2&t=0",
      "推荐榜": "https://www.jjwxc.net/topten.php?orderstr=4&t=0",
      "VIP榜": "https://www.jjwxc.net/topten.php?orderstr=7&t=0",
    },
    extract: (text, _board) => {
      // 晋江页面是GBK编码的中文, 保持原文
      return text.replace(/\s+/g, " ").slice(0, 2500);
    },
  },

  // ---- 飞卢小说网 ----
  {
    match: ["飞卢", "faloo", "b.faloo"],
    defaultBoard: "排行榜",
    boards: {
      "排行榜": "https://b.faloo.com/y_0_0_0_0_3_1_0.html",
      "月票榜": "https://b.faloo.com/y_0_0_0_0_3_2_0.html",
      "收藏榜": "https://b.faloo.com/y_0_0_0_0_3_3_0.html",
      "点击榜": "https://b.faloo.com/y_0_0_0_0_3_5_0.html",
      "更新榜": "https://b.faloo.com/y_0_0_0_0_3_6_0.html",
    },
    extract: (text, _board) => {
      const clean = text.replace(/\s+/g, " ");
      const out: string[] = [];
      // 飞卢格式: 书名 + 作者 通常连续出现
      for (const m of clean.matchAll(/([\u4e00-\u9fa5A-Za-z0-9：:《》\-]{3,30})\s*作家[：:]\s*([\u4e00-\u9fa5]{2,10})/g)) {
        out.push(`${m[1]} (作者: ${m[2]})`);
        if (out.length >= 15) break;
      }
      return out.length > 0 ? "飞卢小说 " + _board + ":\n" + out.join("\n") : clean.slice(0, 2000);
    },
  },

  // ---- 书旗小说 ----
  {
    match: ["书旗", "shuqi"],
    defaultBoard: "点击榜",
    boards: {
      "点击榜": "https://www.shuqi.com/rank",
    },
    extract: (text, _board) => {
      const clean = text.replace(/\s+/g, " ");
      const out: string[] = [];
      // 书旗格式: "1 书名 作者" 
      for (const m of clean.matchAll(/(\d{1,3})\s+([\u4e00-\u9fa5A-Za-z0-9：:《》\-]{2,30})\s+([\u4e00-\u9fa5]{2,10})/g)) {
        const rank = parseInt(m[1]);
        if (rank > 0 && rank <= 50) {
          out.push(`第${m[1]}名: ${m[2]} (作者: ${m[3]})`);
        }
        if (out.length >= 15) break;
      }
      return out.length > 0 ? "书旗小说 " + _board + ":\n" + out.join("\n") : clean.slice(0, 2000);
    },
  },

  // ---- 豆瓣阅读 ----
  {
    match: ["豆瓣", "douban", "豆瓣阅读"],
    defaultBoard: "畅销榜",
    boards: {
      "畅销榜": "https://read.douban.com/charts/bestseller/novel",
      "新书榜": "https://read.douban.com/charts/bestseller/novel",
    },
    extract: (text, _board) => {
      return text.replace(/\s+/g, " ").slice(0, 2000);
    },
  },

  // ---- QQ阅读 ----
  {
    match: ["QQ阅读", "qq阅读", "qq阅读", "book.qq"],
    defaultBoard: "热门榜",
    boards: {
      "热门榜": "https://book.qq.com/rank/hot",
    },
    extract: (text, _board) => {
      return text.replace(/\s+/g, " ").slice(0, 2000);
    },
  },
];

/** 根据平台名和board名匹配平台配置 */
function findPlatform(platform: string): PlatformConfig | undefined {
  return PLATFORMS.find(p => p.match.some(m => platform.includes(m)));
}

/** 根据board名模糊匹配到具体URL */
function resolveBoardUrl(conf: PlatformConfig, board: string): { url: string; boardName: string } {
  if (!board) return { url: conf.boards[conf.defaultBoard]!, boardName: conf.defaultBoard };
  // 精确匹配
  if (conf.boards[board]) return { url: conf.boards[board], boardName: board };
  // 模糊匹配 (包含关系)
  for (const [name, url] of Object.entries(conf.boards)) {
    if (board.includes(name) || name.includes(board)) return { url, boardName: name };
  }
  // 没找到就用默认
  return { url: conf.boards[conf.defaultBoard]!, boardName: conf.defaultBoard };
}

async function crawlRanking(
  platform: string,
  genre: string,
  board: string,
): Promise<{ source: string; board: string; url: string; items: string[]; rawText: string }> {
  const conf = findPlatform(platform) || PLATFORMS[0]!;
  const { url, boardName } = resolveBoardUrl(conf, board);

  let text = "";
  let err = "";
  try {
    const r = await browserFetch(url, 15000, 6000);
    text = r.content || "";
  } catch (e) {
    err = (e as Error).message;
  }
  if (!text && !err) return { source: "browser", board: boardName, url, items: [], rawText: "" };
  const extracted = err ? "抓取失败: " + err : conf.extract(text, boardName);
  const items = err ? [] : extracted.split("\n").filter(l => l.trim()).slice(0, 30);
  return {
    source: err ? "error" : "browser",
    board: boardName,
    url,
    items,
    rawText: extracted,
  };
}


async function webSearch(query: string, maxResults = 8) {
  const headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9",
  };
  try {
    const resp = await fetch(
      `https://www.bing.com/search?q=${encodeURIComponent(query)}&mkt=zh-CN&setlang=zh-Hans`,
      { headers, signal: AbortSignal.timeout(15000) },
    );
    const html = await resp.text();
    // 正则提取搜索结果
    const results: Array<{ title: string; url: string; snippet: string }> = [];
    const re = /<h2[^>]*><a[^>]+href="(https?:\/\/[^"]+)"[^>]*>([^<]+)<\/a><\/h2>/gi;
    let m: RegExpExecArray | null;
    while ((m = re.exec(html)) !== null) {
      const url = m[1]!;
      const title = m[2]!;
      if (url.includes("bing.com") || url.includes("microsoft.com")) continue;
      // 提取摘要
      const after = html.slice(m.index + m[0].length, m.index + m[0].length + 500);
      const snipM = /<p[^>]*>([^<]+)<\/p>/i.exec(after);
      results.push({ title: title.trim(), url, snippet: snipM?.[1]?.trim()?.slice(0, 200) || "" });
      if (results.length >= maxResults) break;
    }
    return { query, results, count: results.length, source: "bing" };
  } catch (e) {
    return { query, results: [], error: (e as Error).message };
  }
}

async function webFetch(url: string, maxChars = 8000) {
  if (!url.startsWith("http")) return { error: "url 必须以 http(s):// 开头" };
  try {
    const resp = await fetch(url, {
      headers: { "User-Agent": "Mozilla/5.0 Chrome/131.0.0.0 Safari/537.36" },
      signal: AbortSignal.timeout(15000),
    });
    const html = await resp.text();
    // 去 HTML 标签提取正文
    let text = html
      .replace(/<script[^>]*>[\s\S]*?<\/script>/gi, "")
      .replace(/<style[^>]*>[\s\S]*?<\/style>/gi, "")
      .replace(/<[^>]+>/g, " ")
      .replace(/\s+/g, " ")
      .trim();
    if (text.length > maxChars) text = text.slice(0, maxChars) + "\n… (已截断)";
    // 提取 title
    const titleM = /<title[^>]*>([^<]+)<\/title>/i.exec(html);
    return { url, title: titleM?.[1]?.trim() || "", content: text, chars: text.length };
  } catch (e) {
    return { error: (e as Error).message, url };
  }
}

// ===== 共享: 写作工具执行后强制审查钩子 =====
// 写正文的工具 (continue_writing/ghostwrite/polish) 执行后自动触发 review_chapter
// 评分<7 打回重写; >=7 推进阶段。原生 tool_calls 与 JSON 降级路径共用。
async function enforceReviewAfterWrite(
  pid: string, runId: string, agentName: AgentName,
  toolName: string, toolArgs: Record<string, unknown>, result: string,
  messages: Array<{ role: string; content: string; [key: string]: unknown }>,
  emit: (evt: SSEEvent) => void,
): Promise<void> {
  if (toolName !== "continue_writing" && toolName !== "ghostwrite" && toolName !== "polish") return;
  try {
    const chapterIdForReview = String(toolArgs.chapter_id || "");
    if (!chapterIdForReview) return;
    const reviewResult = await dispatchTool(pid, "review_chapter", { chapter_id: chapterIdForReview }, agentName);
    emit({ type: "observation", agent: agentName, tool: "review_chapter(auto)", result: reviewResult.slice(0, 2000) });
    messages.push({ role: "tool", content: reviewResult });
    const reviewParsed = (() => { try { return JSON.parse(reviewResult); } catch { return {}; } })();
    const reviewScore = reviewParsed.score ?? 0;
    if (reviewScore > 0 && reviewScore < 7) {
      const revisionMsg = JSON.stringify({
        type: "forced_revision",
        reason: `审查评分 ${reviewScore}/10 < 7，不合格，必须打回重写。`,
        review: reviewParsed.review || "",
        chapter_id: chapterIdForReview,
        instruction: "请将以下审查意见转达给主笔，要求重写: " + (reviewParsed.review || "评分不达标"),
      });
      messages.push({ role: "assistant", content: revisionMsg });
      emit({ type: "forced_revision", agent: agentName, score: reviewScore, review: reviewParsed.review || "", chapterId: chapterIdForReview, reason: `评分 ${reviewScore}/10 < 7，不合格，必须打回重写。` });
    } else if (reviewScore >= 7) {
      try {
        const projState = store.getProjectState(pid);
        const advanceResult = advancePhase(projState.currentPhase as import("../workflow.js").Phase, 8, projState);
        if (advanceResult.success && advanceResult.newPhase) {
          store.updateProjectPhase(pid, advanceResult.newPhase);
          emit({ type: "review_passed", agent: agentName, score: reviewScore, newPhase: advanceResult.newPhase, chapterId: chapterIdForReview });
        }
      } catch { /* 阶段推进失败不阻塞 */ }
    }
  } catch { /* 审查失败不阻塞主流程 */ }
}

// ===== Agent 循环 =====

interface RunState {
  pid: string;
  agentName: AgentName;
  messages: Array<{ role: string; content: string; [key: string]: unknown }>;
  depth: number;
  runId: string;
  delegationLog: Array<{ to: string; task: string; result: string; durationMs: number }>;
  emit: (evt: SSEEvent) => void;
  sharedSteps?: { value: number };
}

// 从 LLM 输出中提取工具调用 (兼容 JSON / <function="x" /> / <invoke name="x">)
function extractToolCall(content: string): { tool: string; args: Record<string, unknown> } | null {
  if (!content) return null;
  // 1. JSON 块: 花括号计数逐个尝试
  const jsonCandidates: string[] = [];
  for (let i = 0; i < content.length; i++) {
    if (content[i] === '{') {
      let depth = 0;
      let end = -1;
      for (let j = i; j < content.length; j++) {
        if (content[j] === '{') depth++;
        else if (content[j] === '}') {
          depth--;
          if (depth === 0) { end = j; break; }
        }
      }
      if (end > i) jsonCandidates.push(content.slice(i, end + 1));
    }
  }
  for (let k = jsonCandidates.length - 1; k >= 0; k--) {
    try {
      const parsed = JSON.parse(jsonCandidates[k]);
      if (parsed && typeof parsed.tool === "string" && (parsed.args === undefined || typeof parsed.args === "object")) {
        return { tool: parsed.tool, args: parsed.args || {} };
      }
    } catch { /* skip malformed */ }
  }
  // 2. <function="name" key="value" /> 格式
  const fnMatches = [...content.matchAll(/<function\s*=\s*["']?([^"'\s>]+)["']?\s*([^>]*)\s*\/?\s*>/g)];
  if (fnMatches.length) {
    const last = fnMatches[fnMatches.length - 1];
    const args: Record<string, unknown> = {};
    const attrRe = /([a-zA-Z_][\w-]*)\s*=\s*["']([^"']*)["']/g;
    let am: RegExpExecArray | null;
    while ((am = attrRe.exec(last[2])) !== null) {
      args[am[1]] = am[2];
    }
    return { tool: last[1], args };
  }
  // 3. <invoke name="x"><parameter name="y">z</parameter></invoke> 格式
  const invokeMatches = [...content.matchAll(/<invoke\s+name\s*=\s*["']?([^"'\s>]+)["']?\s*>([\s\S]*?)<\/invoke>/g)];
  if (invokeMatches.length) {
    const last = invokeMatches[invokeMatches.length - 1];
    const args: Record<string, unknown> = {};
    const pRe = /<parameter\s+name\s*=\s*["']([^"']+)["']\s*>([\s\S]*?)<\/parameter>/g;
    let pm: RegExpExecArray | null;
    while ((pm = pRe.exec(last[2])) !== null) {
      args[pm[1]] = pm[2].trim();
    }
    return { tool: last[1], args };
  }
  const invokeBare = [...content.matchAll(/<invoke\s+name\s*=\s*["']?([^"'\s>]+)["']?\s*>/g)];
  if (invokeBare.length) {
    return { tool: invokeBare[invokeBare.length - 1][1], args: {} };
  }
  // 4. <tool_call> 格式 (兼容 agnes 输出)
  const tcMatches = [...content.matchAll(/<tool_call>\s*<tool_name>\s*([^<]+?)\s*<\/tool_name>\s*<\/tool_call>/g)];
  if (tcMatches.length) {
    const last = tcMatches[tcMatches.length - 1];
    const toolName = last[1].trim();
    // 尝试从前后文提取 JSON args
    const afterTc = content.slice(last.index! + last[0].length);
    const argMatch = afterTc.match(/\{[\s\S]*?\}/);
    if (argMatch) {
      try {
        const args = JSON.parse(argMatch[0]);
        if (typeof args === "object") return { tool: toolName, args };
      } catch { /* skip */ }
    }
    return { tool: toolName, args: {} };
  }
  // 5. 三重反引号 JSON 块 (```json ... ```)
  const mdJsonMatch = content.match(/```json\s*\n(\{[\s\S]*?\})\s*\n```/);
  if (mdJsonMatch) {
    try {
      const parsed = JSON.parse(mdJsonMatch[1]);
      if (parsed && typeof parsed.tool === "string") return { tool: parsed.tool, args: parsed.args || {} };
    } catch { /* skip */ }
  }
  return null;
}


// ===== OpenAI 原生 function calling 工具 schema =====
const TOOL_SCHEMAS: Record<string, ToolDefinition> = {
  delegate_to_agent: {
    type: "function",
    function: { name: "delegate_to_agent", description: "委派任务给子 agent", parameters: { type: "object", properties: { agent: { type: "string", description: "目标 agent 名" }, task: { type: "string", description: "任务描述" } }, required: ["agent", "task"] } },
  },
  query_project: {
    type: "function",
    function: { name: "query_project", description: "查询当前项目状态、统计信息", parameters: { type: "object", properties: {}, required: [] } },
  },
  review_chapter: {
    type: "function",
    function: { name: "review_chapter", description: "毒舌审稿，评分1-10", parameters: { type: "object", properties: { chapter_id: { type: "string" } }, required: ["chapter_id"] } },
  },
  generate_outline: {
    type: "function",
    function: { name: "generate_outline", description: "生成大纲", parameters: { type: "object", properties: { premise: { type: "string" }, genre: { type: "string" }, num_chapters: { type: "number" } }, required: ["premise"] } },
  },
  manage_outline: {
    type: "function",
    function: { name: "manage_outline", description: "管理/更新大纲", parameters: { type: "object", properties: { action: { type: "string" }, chapter_id: { type: "string" }, outline: { type: "string" } }, required: ["action"] } },
  },
  manage_character: {
    type: "function",
    function: { name: "manage_character", description: "创建/更新角色档案", parameters: { type: "object", properties: { action: { type: "string" }, name: { type: "string" }, personality: { type: "string" }, speechStyle: { type: "string" }, motivation: { type: "string" } }, required: ["action", "name"] } },
  },
  add_element: {
    type: "function",
    function: { name: "add_element", description: "添加设定到侧边栏设定集。kind必须是以下之一: character(人物设定), world(世界观/修炼体系/势力规则), outline(大纲/卷纲/细纲), style(文风参考/对标作品), foreshadow(伏笔/线索), plot(剧情节点/爽点/钩子), location(地点), timeline(时间线), milestone(里程碑)", parameters: { type: "object", properties: { kind: { type: "string", description: "分类: character/world/outline/style/foreshadow/plot/location/timeline/milestone" }, name: { type: "string", description: "设定名称" }, detail: { type: "string", description: "设定详细内容" } }, required: ["kind", "name"] } },
  },
  manage_world: {
    type: "function",
    function: { name: "manage_world", description: "管理世界观条目", parameters: { type: "object", properties: { action: { type: "string" }, name: { type: "string" }, category: { type: "string" }, description: { type: "string" } }, required: ["action", "name"] } },
  },
  continue_writing: {
    type: "function",
    function: { name: "continue_writing", description: "续写正文", parameters: { type: "object", properties: { chapter_id: { type: "string" }, instruction: { type: "string" }, length: { type: "number" } }, required: ["chapter_id", "instruction"] } },
  },
  quality_check: {
    type: "function",
    function: { name: "quality_check", description: "35维质检", parameters: { type: "object", properties: { chapter_id: { type: "string" } }, required: ["chapter_id"] } },
  },
  web_search: {
    type: "function",
    function: { name: "web_search", description: "联网搜索", parameters: { type: "object", properties: { query: { type: "string" } }, required: ["query"] } },
  },
  web_fetch: {
    type: "function",
    function: { name: "web_fetch", description: "抓取网页内容", parameters: { type: "object", properties: { url: { type: "string" } }, required: ["url"] } },
  },
  scan_bestseller: {
    type: "function",
    function: {
      name: "scan_bestseller",
      description: "扫描小说平台榜单。支持: 起点/番茄/晋江/飞卢/书旗/豆瓣/QQ阅读。board可选: 畅销榜/月票榜/人气榜/追读榜/收藏榜/三江(当前)/往期三江/阅读榜/新书榜等。不指定board则默认抓畅销榜。用户要求三江就抓三江，要求往期就抓往期。",
      parameters: {
        type: "object",
        properties: {
          platform: { type: "string", description: "平台名称，如: 起点/番茄/晋江/飞卢/书旗/豆瓣/QQ阅读" },
          genre: { type: "string", description: "题材/类型，如: 仙侠/都市/玄幻/言情，不指定则全品类" },
          board: { type: "string", description: "榜单名称，如: 畅销榜/月票榜/人气榜/三江/往期三江/阅读榜/新书榜/追读榜/收藏榜。默认: 畅销榜" },
        },
        required: ["platform"],
      },
    },
  },
  audit_novel: {
    type: "function",
    function: { name: "audit_novel", description: "35维审计", parameters: { type: "object", properties: { chapter_id: { type: "string" } }, required: ["chapter_id"] } },
  },
  detect_ai: {
    type: "function",
    function: { name: "detect_ai", description: "AI味检测", parameters: { type: "object", properties: { text: { type: "string" } }, required: ["text"] } },
  },
  polish: {
    type: "function",
    function: { name: "polish", description: "润色/改写", parameters: { type: "object", properties: { chapter_id: { type: "string" }, mode: { type: "string" }, instruction: { type: "string" } }, required: ["chapter_id"] } },
  },
  match_author: {
    type: "function",
    function: { name: "match_author", description: "匹配最佳作家风格", parameters: { type: "object", properties: { genre: { type: "string" }, style: { type: "string" } }, required: ["genre"] } },
  },
  query_workflow: {
    type: "function",
    function: { name: "query_workflow", description: "查询工作流阶段状态", parameters: { type: "object", properties: {}, required: [] } },
  },
  export_project_data: {
    type: "function",
    function: { name: "export_project_data", description: "导出项目全部章节为txt或json文件，返回下载路径。创作完成后用于交付最终成稿。", parameters: { type: "object", properties: { format: { type: "string", description: "导出格式: txt 或 json，默认txt" } }, required: [] } },
  },
  challenge_review: {
    type: "function",
    function: { name: "challenge_review", description: "发起挑战审查", parameters: { type: "object", properties: { target_agent: { type: "string" }, reason: { type: "string" } }, required: ["reason"] } },
  },
  resolve_challenge: {
    type: "function",
    function: { name: "resolve_challenge", description: "响应挑战审查", parameters: { type: "object", properties: { challenge_id: { type: "string" }, response: { type: "string" } }, required: ["challenge_id", "response"] } },
  },
};

function getToolDefinitions(agentName: string): ToolDefinition[] {
  const isValidAgent = agentName in AGENT_TOOLS;
  if (!isValidAgent) return [];
  const toolNames = AGENT_TOOLS[agentName as keyof typeof AGENT_TOOLS] || [];
  return toolNames
    .map((name: string) => TOOL_SCHEMAS[name])
    .filter((def): def is ToolDefinition => !!def);
}


function isReadonlyTool(toolName: string): boolean {
  const readonlyTools = [
    "read_file", "list_files", "search_files", "web_search", "web_fetch",
    "query_characters", "query_world", "query_chapters", "quality_check",
    "list_authors", "list_elements", "bash",
  ];
  return readonlyTools.includes(toolName);
}

export async function runAgentLoop(state: RunState): Promise<string> {
  const s = getSettings();
  const { pid, agentName, depth, runId, emit } = state;
  let messages = [...state.messages];
  let step = 0;
  const loopDetect: Record<string, number> = {};
  let round = 0;
  let bestContent = "";  // 保留最佳内容作为返回值

  const runStart = Date.now();
  const maxDurationMs = s.runMaxDuration * 1000;

  // 心跳: 每 5 秒发一次
  const heartbeatTimer = setInterval(() => {
    emit({ type: "heartbeat", ts: Date.now(), runId });
  }, 5000);

  // 每个 agent 有独立的 maxSteps / maxTurns
  const agentCfg = s.agents?.[agentName];
  const agentMaxSteps = agentCfg?.maxSteps ?? s.maxSteps;
  const agentMaxTurns = agentCfg?.maxTurns ?? 8;

  try {
  while (step < agentMaxSteps && round <= agentMaxTurns) {
    step++;
    if (state.sharedSteps) state.sharedSteps.value = step;
    round++;
    // 超时检查
    if (Date.now() - runStart > maxDurationMs) {
      const msg = `运行超时 (${s.runMaxDuration}s), 已强制停止`;
      emit({ type: "error", message: msg });
      break;
    }

    // ===== 思考阶段: 流式 LLM 调用 =====
    let fullContent = "";
    let thinkingText = "";
    let thinkDone = false;
    let inThinking = false;
    let nativeToolCalls: ToolCall[] = [];
    // Thinking缓冲区: 累积thinking内容, content到来时统一发送（过滤system prompt泄漏）
    let thinkBuffer = "";
    let thinkFlushed = false;
    const THINK_BUFFER_MAX = 2000;

    try {
      let agentTools = getToolDefinitions(agentName);
      const msgRoles = messages.map(m => m.role);
      const toolMsgCount = msgRoles.filter(r => r === 'tool').length;
      const assistantMsgCount = msgRoles.filter(r => r === 'assistant').length;
      const totalMsgLen = messages.reduce((sum, m) => sum + m.content.length, 0);
      // 子agent超过4步时禁用工具，强制生成最终回答
      // 或者消息总长度超过12000时也禁用（agnes在上下文过大时返回空响应）
      if (depth > 0 && (step >= 4 || totalMsgLen > 12000)) {
        const reason = step >= 4 ? `step>=4` : `total_len=${totalMsgLen}>12000`;
        agentTools = [];
      }
      // 如果消息过大(>15000 chars), 截断旧的tool消息防止agnes返回空响应
      if (totalMsgLen > 15000) {
        let reduced = 0;
        for (let i = 0; i < messages.length - 2; i++) {
          if (messages[i].role === 'tool' && messages[i].content.length > 1000) {
            const orig = messages[i].content;
            messages[i] = { ...messages[i], content: orig.slice(0, 800) + '\n...(已截断)' };
            reduced += orig.length - messages[i].content.length;
          }
        }
        if (reduced > 0) {
          const newLen = messages.reduce((s, m) => s + m.content.length, 0);
        }
      }
      const streamGen = llmStream(messages, undefined, { tools: agentTools });
      for await (const chunk of streamGen) {
        // TOOLCALL_PREFIX: 原生 tool_calls 由 llm.ts 累积后在末尾输出
        if (chunk.startsWith(TOOLCALL_PREFIX)) {
          try {
            const rawTcs = JSON.parse(chunk.slice(TOOLCALL_PREFIX.length));
            nativeToolCalls = (rawTcs as Array<{ id: string; name: string; arguments: string }>).map((t) => ({
              id: t.id,
              name: t.name,
              arguments: (() => { try { return JSON.parse(t.arguments); } catch { return {}; } })(),
            }));
          } catch { /* skip malformed */ }
          continue;
        }
        // THINK_PREFIX: agnes reasoning_content 分离传输
        if (chunk.startsWith(THINK_PREFIX)) {
          const thinkText = chunk.slice(THINK_PREFIX.length);
          thinkingText += thinkText;
          if (!thinkDone && !inThinking) {
            inThinking = true;
          }
          // 缓冲thinking内容, 不立即发送到前端
          thinkBuffer += thinkText;
          // 缓冲区超过阈值时提前判断是否为system prompt泄漏
          if (!thinkFlushed && thinkBuffer.length >= THINK_BUFFER_MAX) {
            if (isPromptLeakage(thinkBuffer)) {
              console.log("[think_filter] 已过滤system prompt泄漏, 长度:", thinkBuffer.length);
              thinkFlushed = true;
              thinkBuffer = "";
            }
          }
          continue; // thinking 不加入 fullContent
        }
        // 普通 content
        if (inThinking && !thinkDone) {
          // thinking 结束, 第一个 content chunk → Flush缓冲
          thinkDone = true;
          if (!thinkFlushed) {
            if (thinkBuffer.length > 0 && !isPromptLeakage(thinkBuffer)) {
              emit({ type: "think_start", agent: agentName, round });
              emit({ type: "think_token", agent: agentName, text: thinkBuffer });
            }
            thinkFlushed = true;
            thinkBuffer = "";
          }
          emit({ type: "think_end", agent: agentName, feasible: true, reason: "思考完成", plan: [], missing: "" });
          emit({ type: "answer_start", agent: agentName });
        }
        if (!inThinking) {
          // 没有 thinking, 兼容 <thinking> 标签方式
          fullContent += chunk;
          const thinkOpen = fullContent.indexOf("<thinking>");
          const thinkClose = fullContent.indexOf("</thinking>");
          if (thinkOpen >= 0 && thinkClose > thinkOpen) {
            thinkingText = fullContent.slice(thinkOpen + 10, thinkClose);
            thinkDone = true;
            emit({ type: "think_token", agent: agentName, text: thinkingText });
            emit({ type: "think_end", agent: agentName, feasible: true, reason: "思考完成", plan: [], missing: "" });
            const answerPart = fullContent.slice(thinkClose + 11).trim();
            if (answerPart) {
              emit({ type: "answer_start", agent: agentName });
              emit({ type: "token", agent: agentName, content: answerPart });
              fullContent = answerPart;
            } else {
              fullContent = "";
            }
            inThinking = false;
          } else if (thinkOpen >= 0) {
            inThinking = true;
            emit({ type: "think_start", agent: agentName, round });
            emit({ type: "think_token", agent: agentName, text: chunk });
          } else {
            emit({ type: "token", agent: agentName, content: chunk });
          }
        } else {
          // thinking 已完成, 流式输出回答
          fullContent += chunk;
          emit({ type: "token", agent: agentName, content: chunk });
        }
      }
    } catch (e) {
      clearInterval(heartbeatTimer);
      // think_end 补发: 防止前端卡死
      emit({ type: "think_end", agent: agentName, feasible: false, reason: "出错中断", plan: [], missing: "" });
      const errMsg = friendlyError(e as Error, s.defaultModel);
      emit({ type: "error", message: errMsg });
      store.addRunEvent(runId, "error", { error: errMsg });
      return errMsg;
    }

    // 如果没有 thinking 标签, 发简短 think_end
    if (!thinkDone) {
      emit({ type: "think_end", agent: agentName, feasible: true, reason: "直接回复", plan: [], missing: "" });
      emit({ type: "answer_start", agent: agentName });
      emit({ type: "token", agent: agentName, content: fullContent });
    }

    const content = fullContent;
    if (content.trim()) bestContent = content;  // 记录最佳内容
    // agnes有时在content中只有空白(\n\n), 但reasoning_content有实质内容
    // 如果content只有空白但thinkingText有内容, 也把thinkingText作为bestContent
    if (!bestContent && thinkingText.trim().length > 100) {
      bestContent = thinkingText.trim();
    }
    // DEBUG: 追踪子agent每轮LLM输出

    // 持久化思考文本 (tool_name='think'), 前端历史渲染时可恢复折叠面板
    if (thinkingText.trim()) {
      store.addMessage(pid, "assistant", thinkingText.trim(), { toolName: "think", toolCallId: agentName });
    }
    // ===== 优先使用原生 tool_calls =====
    if (nativeToolCalls.length > 0) {
      // 存储 tool_calls 消息 (tool_name='tool_calls'), 前端用于渲染折叠面板
      const tcSummary = nativeToolCalls.map((tc) => ({
        function: { name: tc.name },
        arguments: tc.arguments,
      }));
      store.addMessage(pid, "assistant", JSON.stringify(tcSummary), { toolName: "tool_calls", toolCallId: agentName });
      // 将 assistant 消息以 OpenAI tool_calls 格式加入 messages
      const assistantMsg: any = { role: "assistant", content: content || null };
      assistantMsg.tool_calls = nativeToolCalls.map((tc) => ({
        id: tc.id,
        type: "function",
        function: { name: tc.name, arguments: JSON.stringify(tc.arguments) },
      }));
      messages.push(assistantMsg);

      for (const tc of nativeToolCalls) {
        emit({ type: "step", agent: agentName, tool: tc.name, args: tc.arguments });
        const toolKey = `${tc.name}:${JSON.stringify(tc.arguments)}`;
        loopDetect[toolKey] = (loopDetect[toolKey] || 0) + 1;
        if (loopDetect[toolKey] >= s.loopDetectCount) {
          const msg = `检测到循环: ${tc.name} 已连续调用 ${s.loopDetectCount} 次`;
          emit({ type: "error", message: msg });
          break;
        }
        // ===== 委派: 走子 agent =====
        if (tc.name === "delegate_to_agent") {
          const target = String(tc.arguments.agent || tc.arguments.target || "");
          const task = String(tc.arguments.task || tc.arguments.prompt || "");
          if (!isValid(target) || target === DEFAULT_AGENT) {
            const err = `无效委派目标: ${target}`;
            messages.push({ role: "tool", content: JSON.stringify({ error: err }), tool_call_id: tc.id });
            emit({ type: "observation", agent: agentName, tool: tc.name, result: err });
            continue;
          }
          if (depth >= getMaxDelegateDepth()) {
            const err = `已达最大委派深度 ${getMaxDelegateDepth()}`;
            messages.push({ role: "tool", content: JSON.stringify({ error: err }), tool_call_id: tc.id });
            emit({ type: "observation", agent: agentName, tool: tc.name, result: err });
            continue;
          }
          // 内联委派逻辑
          emit({ type: "delegate", from: agentName, to: target as AgentName, task, depth: depth + 1 });
          emit({ type: "sub_agent_start", agent: target as AgentName, task, depth: depth + 1 });
          const proj = store.getProject(pid);
          const subSkillPrompts = getAgentSkillPrompts(target);
          const subSkillBlock = subSkillPrompts.length ? "\n\n" + subSkillPrompts.join("\n\n") : "";
          const subMsgs: Array<{ role: string; content: string }> = [
            { role: "system", content: getAgentPrompt(target) + subSkillBlock + "\n\n可用工具: " + AGENT_TOOLS[target as AgentName].join(", ") + "\n如果需要调用工具，必须只输出一行JSON: {\"tool\": \"工具名\", \"args\": {...}}\n否则直接回复。" },
          ];
          if (proj) {
            let ctxBlock = `项目: ${proj.name} | 类型: ${proj.genre} | 文风: ${proj.style}\n核心设定: ${proj.premise}`;
            const subProfiles = store.listCharacterProfiles(pid);
            if (subProfiles.length) ctxBlock += "\n\n【角色档案】\n" + subProfiles.map((p: any) => `- ${p.name}(${p.role || "?"}): ${p.personality || ""}`).join("\n");
            const subWorld = store.listWorldEntries(pid);
            if (subWorld.length) ctxBlock += "\n\n【世界观】\n" + subWorld.map((w: any) => `- [${w.category}] ${w.name}: ${w.description || ""}`).join("\n");
            subMsgs.push({ role: "system", content: ctxBlock });
          }
          subMsgs.push({ role: "user", content: task });
          const subT0 = Date.now();
          const subResult = await runAgentLoop({
            pid, agentName: target as AgentName, messages: subMsgs,
            depth: depth + 1, runId, delegationLog: state.delegationLog, emit,
          });
          const subDur = Date.now() - subT0;
          state.delegationLog.push({ to: target, task, result: subResult, durationMs: subDur });
          emit({ type: "delegate_done", from: agentName, to: target as AgentName, task, result: subResult, durationMs: subDur });
          emit({ type: "sub_agent_done", agent: target as AgentName, result: subResult, durationMs: subDur });
          // 发送子agent最终回答到前端子气泡
          if (subResult && subResult.trim()) {
            emit({ type: "sub_answer", agent: target as AgentName, content: subResult });
          }
          // 工具结果包含子 agent 返回的 JSON
          // 工具结果包含子 agent 返回的 JSON + 推进指引
          const subResultTruncated = subResult.length > 5000 ? subResult.slice(0, 5000) + "\n...(已截断)" : subResult;
          const pushHint = agentName === "orchestrator"
            ? `\n\n[系统指令] 上一步已完成。你是总编，请立即用delegate_to_agent委派下一个创作步骤（参照7步流程：扫榜→拆书→角色→大纲→写作→审核→导出）。不要等用户确认，直接继续！`
            : "";
          messages.push({ role: "tool", content: subResultTruncated + pushHint, tool_call_id: tc.id });
          emit({ type: "observation", agent: agentName, tool: tc.name, result: subResult.slice(0, 2000) });
          continue;
        }
        // ===== 只读 agent 检查 =====
        if (isReadonly(agentName) && !isReadonlyTool(tc.name)) {
          const err = `只读Agent (${agentName}) 不能调用写入工具 ${tc.name}`;
          messages.push({ role: "tool", content: JSON.stringify({ error: err }), tool_call_id: tc.id });
          emit({ type: "observation", agent: agentName, tool: tc.name, result: err });
          continue;
        }
        // ===== 普通工具 =====
        const t0 = Date.now();
        const result = await dispatchTool(pid, tc.name, tc.arguments, agentName);
        const durMs = Date.now() - t0;
        store.addRunEvent(runId, "tool_call", { agent: agentName, tool: tc.name, input_: tc.arguments, duration_ms: durMs });
        // 截断工具结果防止 token 溢出
        const truncatedResult = result.length > 4000 ? result.slice(0, 4000) + "\n...(结果已截断)" : result;
        messages.push({ role: "tool", content: truncatedResult, tool_call_id: tc.id });
        emit({ type: "observation", agent: agentName, tool: tc.name, result: result.slice(0, 2000) });
        // 自动保存记忆
        try {
          const sessionId = runId;
          if (tc.name === "manage_character" || tc.name === "add_element") {
            memory.saveSessionMemory(pid, sessionId, "context", tc.name, result.slice(0, 500), "agent");
          }
          if (tc.name === "continue_writing" || tc.name === "ghostwrite" || tc.name === "polish") {
            memory.saveSessionMemory(pid, sessionId, "plot", `chapter_${tc.name}`, result.slice(0, 300), "auto", 0.5);
            // === 强制审查钩子：写作工具执行后自动触发审查 (共享实现) ===
            await enforceReviewAfterWrite(pid, runId, agentName, tc.name, tc.arguments, result, messages, emit);
          }
        } catch { /* ignore */ }
      }
      // 循环继续，LLM 看到 tool 结果后生成下一轮
      // 特殊情况: agnes返回了thinking但没有content也没有tool_calls
      // 这说明agnes已经完成了工具调用并给出了最终思考，但content为空
      if (nativeToolCalls.length === 0 && content.trim().length === 0 && thinkingText.trim().length > 50) {
        // agnes把答案放在了reasoning_content里（content为空），用thinking作为最终结果
        const thinkingAsAnswer = thinkingText.trim();
        store.addMessage(pid, "assistant", thinkingAsAnswer);
        store.addRunEvent(runId, "end", { agent: agentName, output: thinkingAsAnswer.slice(0, 500) });
        store.finishRun(runId, "done");
        clearInterval(heartbeatTimer);
        return thinkingAsAnswer;
      }
      continue;
    }

    // ===== 降级: JSON 解析旧模式 (原生 tool_calls 不可用时) =====
    let toolCall: { tool: string; args: Record<string, unknown> } | null = extractToolCall(content);

    if (!toolCall) {
      // 安全网: 如果 content 为空但有思考文本, 用思考文本作为回复
      const finalContent = content || bestContent || (thinkingText ? thinkingText.slice(0, 2000) : "");
      if (!finalContent) {
        // 真正的空回复: 发通知给前端, 继续循环尝试
        // 2次空回复即退出——agnes在消息过多时返回空响应是已知问题
        if (step >= 3 && bestContent.length > 0) {
          store.addMessage(pid, "assistant", bestContent);
          store.addRunEvent(runId, "end", { agent: agentName, output: bestContent.slice(0, 500) });
          store.finishRun(runId, "done");
          clearInterval(heartbeatTimer);
          return bestContent;
        }
        // 连续3次空回复即强制退出
        if (step >= 5) {
          const fallback = bestContent || "子agent在工具调用后未能生成最终回答，请简化任务重试";
          store.addMessage(pid, "assistant", fallback);
          store.addRunEvent(runId, "end", { agent: agentName, output: fallback.slice(0, 500) });
          store.finishRun(runId, "done");
          clearInterval(heartbeatTimer);
          return fallback;
        }
        emit({ type: "observation", agent: agentName, tool: "empty_response", result: "LLM返回空内容, 重试中..." });
        continue;
      }
      // 存储最终回答
      store.addMessage(pid, "assistant", finalContent);
      store.addRunEvent(runId, "end", { agent: agentName, output: finalContent.slice(0, 500) });
      store.finishRun(runId, "done");
      clearInterval(heartbeatTimer);
      return finalContent;
    }

    const { tool: toolName, args: toolArgs } = toolCall;
    emit({ type: "step", agent: agentName, tool: toolName, args: toolArgs });

    // 循环检测
    const key = `${toolName}:${JSON.stringify(toolArgs)}`;
    loopDetect[key] = (loopDetect[key] || 0) + 1;
    if (loopDetect[key] >= s.loopDetectCount) {
      const msg = `检测到循环: ${toolName} 已连续调用 ${s.loopDetectCount} 次`;
      emit({ type: "error", message: msg });
      break;
    }

    // 委派: 走子 agent
    if (toolName === "delegate_to_agent") {
      const target = String(toolArgs.agent || toolArgs.target || "");
      const task = String(toolArgs.task || toolArgs.prompt || "");
      if (!isValid(target) || target === DEFAULT_AGENT) {
        const err = `无效委派目标: ${target}`;
        messages.push({ role: "user", content: `[工具错误]\n${err}` });
                    emit({ type: "observation", agent: agentName, tool: toolName, result: err });
        continue;
      }
      if (depth >= getMaxDelegateDepth()) {
        const err = `已达最大委派深度 ${getMaxDelegateDepth()}`;
        messages.push({ role: "user", content: `[委派错误]\n${err}` });
                    continue;
      }
      emit({ type: "delegate", from: agentName, to: target as AgentName, task, depth: depth + 1 });
      emit({ type: "sub_agent_start", agent: target as AgentName, task, depth: depth + 1 });

      const subMeta = getMeta(target);
      const proj = store.getProject(pid);
      const subSkillPrompts = getAgentSkillPrompts(target);
      const subSkillBlock = subSkillPrompts.length ? "\n\n" + subSkillPrompts.join("\n\n") : "";
      const subMessages: Array<{ role: string; content: string }> = [
        { role: "system", content: getAgentPrompt(target) + subSkillBlock + "\n\n可用工具: " + AGENT_TOOLS[target as AgentName].join(", ") },
      ];
      if (proj) {
        let ctxBlock = `项目: ${proj.name} | 类型: ${proj.genre} | 文风: ${proj.style}\n核心设定: ${proj.premise}`;
        // 注入角色档案
        const subProfiles = store.listCharacterProfiles(pid);
        if (subProfiles.length) {
          ctxBlock += "\n\n【角色档案】\n" + subProfiles.map(p => `- ${p.name}(${p.role || "?"}): ${p.personality || ""} | 说话: ${p.speechStyle || ""}`).join("\n");
        }
        // 注入世界观
        const subWorld = store.listWorldEntries(pid);
        if (subWorld.length) {
          ctxBlock += "\n\n【世界观】\n" + subWorld.map(w => `- [${w.category}] ${w.name}: ${w.description || ""}`).join("\n");
        }
        // 注入章节大纲
        const subChapters = store.listChapters(pid);
        const nextCh = subChapters.find(c => c.status === "draft" || c.status === "writing");
        if (nextCh?.outline) {
          ctxBlock += `\n\n【当前章节大纲】${nextCh.title}: ${nextCh.outline}`;
        }
        subMessages.push({ role: "system", content: ctxBlock });
      }
      subMessages.push({ role: "user", content: task });

      const t0 = Date.now();
      const subResult = await runAgentLoop({
        pid, agentName: target as AgentName, messages: subMessages,
        depth: depth + 1, runId, delegationLog: state.delegationLog, emit,
      });
      const durMs = Date.now() - t0;
      const subResultTrunc = subResult.length > 5000 ? subResult.slice(0, 5000) + "\n...(已截断)" : subResult;
      const legacyHint = agentName === "orchestrator"
        ? `\n\n[系统指令] 上一步已完成。你是总编，请立即用delegate_to_agent委派下一个创作步骤（参照7步流程：扫榜→拆书→角色→大纲→写作→审核→导出）。不要等用户确认，直接继续！`
        : "";
      state.delegationLog.push({ to: target, task, result: subResult, durationMs: durMs });
      emit({ type: "delegate_done", from: agentName, to: target as AgentName, task, result: subResult, durationMs: durMs });
      emit({ type: "sub_agent_done", agent: target as AgentName, result: subResult, durationMs: durMs });
      messages.push({ role: "user", content: `[子Agent ${target} 返回结果]\n${subResultTrunc}${legacyHint}` });
      continue;
    }

    // === 工作流阶段检查（根据模式决定是否强制执行） ===
    const workflowMode = getSettings().workflowMode || "state_machine";
    const projState = store.getProjectState(pid);
    const currentPhase = projState.currentPhase as import("../workflow.js").Phase;
    
    if (workflowMode === "state_machine") {
      // 状态机模式：强制阶段门禁检查
      const toolCheck = isToolAllowedInPhase(toolName, currentPhase);
      if (!toolCheck.allowed) {
        const errMsg = JSON.stringify({ error: `工作流限制: ${toolCheck.reason}` });
                    messages.push({ role: "user", content: `[工具错误]\n${errMsg}` });
        emit({ type: "observation", agent: agentName, tool: toolName, result: errMsg });
        continue;
      }
    } else {
      // CrewAI模式：灵活调度，跳过阶段门禁
      emit({ type: "observation", agent: agentName, tool: toolName, result: `[CrewAI模式] 跳过阶段检查，允许调用 ${toolName}` });
    }

    // 普通工具
    const t0 = Date.now();
    const result = await dispatchTool(pid, toolName, toolArgs, agentName);
    const durMs = Date.now() - t0;
    store.addRunEvent(runId, "tool_call", { agent: agentName, tool: toolName, input_: toolArgs, duration_ms: durMs });
    messages.push({ role: "user", content: `[工具 ${toolName} 返回结果]\n${result}` });
    emit({ type: "observation", agent: agentName, tool: toolName, result: result.slice(0, 2000) });

    // === FIX 1: 自动保存记忆 ===
    try {
      const sessionId = runId; // 用 runId 作为 sessionId
      if (toolName === "manage_character" || toolName === "add_element") {
        memory.saveSessionMemory(pid, sessionId, "context", toolName, result.slice(0, 500), "agent");
      }
      if (toolName === "continue_writing" || toolName === "ghostwrite" || toolName === "polish") {
        memory.saveSessionMemory(pid, sessionId, "plot", `chapter_${toolName}`, result.slice(0, 300), "auto", 0.5);
        // === 强制审查钩子：写作工具执行后自动触发审查 (共享实现) ===
        await enforceReviewAfterWrite(pid, runId, agentName, toolName, toolArgs, result, messages, emit);
      }
      if (toolName === "review_chapter" || toolName === "quality_check") {
        memory.saveSessionMemory(pid, sessionId, "feedback", toolName, result.slice(0, 500), "agent");
      }
      // 用户偏好的长期记忆
      if (toolName === "query_project") {
        memory.saveLongTermMemory(pid, "context", "项目概况", result.slice(0, 500), "auto", 3);
      }
    } catch { /* 记忆保存失败不影响主流程 */ }
  }
  } finally {
    clearInterval(heartbeatTimer);
  }

  store.addRunEvent(runId, "end", { agent: agentName, output: "达到最大步骤数" });
  store.finishRun(runId, "done");
  return "达到最大步骤数, 请继续指示。";
}

// ===== 主入口: 构建上下文 + 启动循环 =====

export async function run(
  pid: string,
  userInput: string,
  agentName: AgentName = DEFAULT_AGENT,
): Promise<{ events: SSEEvent[]; result: string }> {
  const s = getSettings();
  const runId = store.createRun(pid, userInput, agentName);
  store.addMessage(pid, "user", userInput);

  const emit = (evt: SSEEvent) => { /* will be replaced by caller */ };

  // 构建 agent 上下文
  const proj = store.getProject(pid);
  const memoryCtx = memory.getMemoryContext(pid);
  const messages: Array<{ role: string; content: string }> = [];

  if (proj) {
    messages.push({
      role: "system",
      content: `当前项目: ${proj.name}\n频道: ${proj.audience || "未指定"}\n类型: ${proj.genre}\n文风: ${proj.style}\n核心设定: ${proj.premise}`,
    });
  }
  if (memoryCtx) {
    messages.push({ role: "system", content: memoryCtx });
  }

  const meta = getMeta(agentName);
  messages.push({
    role: "system",
    content: getAgentPrompt(agentName) + "\n\n可用工具: " + AGENT_TOOLS[agentName].join(", ") + "\n如果需要调用工具，必须只输出一行JSON（不要使用<tool_call>或<function>标签）: {\"tool\": \"工具名\", \"args\": {...}}\n示例: {\"tool\": \"query_project\", \"args\": {}}\n否则直接回复。",
  });
  messages.push({ role: "user", content: userInput });

  // === FIX 1: 保存用户意图到短期记忆 ===
  try {
    const userSid = randomBytes(6).toString("hex");
    memory.saveSessionMemory(pid, userSid, "user_intent", userInput.slice(0, 100), userInput.slice(0, 500), "user", 1.0);
  } catch { /* ignore */ }

  const events: SSEEvent[] = [];
  const collectEmit = (evt: SSEEvent) => events.push(evt);
  events.push({ type: "start", agent: agentName, input: userInput.slice(0, 200) });

  const sharedSteps = { value: 0 };
  let result = "";
  try {
    result = await runAgentLoop({
      pid, agentName, messages, depth: 0, runId,
      delegationLog: [], emit: collectEmit, sharedSteps,
    });
    events.push({ type: "done", agent: agentName, steps: sharedSteps.value, stats: store.stats(pid), runId });
  } catch (e) {
    events.push({ type: "error", message: friendlyError(e as Error, s.defaultModel) });
  }
  return { events, result };
}
