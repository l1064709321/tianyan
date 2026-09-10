// ===== 天衍 (Tianyan) TypeScript 核心类型定义 =====

// ----- 配置 -----
export interface ModelConfig {
  model: string;
  apiKey?: string;
  apiBase?: string;
  temperature: number;
  maxTokens: number;
}


// ----- Agent 配置 -----
export interface AgentConfig {
  /** 最大委派深度 (总编→X→Y，最多几层) */
  maxDepth: number;
  /** 单个 agent 最大轮次 */
  maxTurns: number;
  /** 单个 agent 最大步数 */
  maxSteps: number;
  /** 模型档位: high/mid/low */
  modelTier: ModelTier;
  /** 是否只读 */
  readonly: boolean;
}

/** Agent 标准配置 */
export const DEFAULT_AGENT_CONFIG: AgentConfig = {
  maxDepth: 2,
  maxTurns: 6,
  maxSteps: 8,
  modelTier: "mid",
  readonly: false,
};
export type WorkflowMode = "state_machine" | "crewai";

export interface Settings {
  dataDir: string;
  dbPath: string;
  uploadDir: string;
  defaultModel: ModelConfig;
  models: ModelConfig[];
  maxSteps: number;
  runMaxTokens: number;
  runMaxCost: number;
  loopDetectCount: number;
  runMaxDuration: number;    // 秒
  sseHeartbeatInterval: number; // 秒
  chunkSize: number;
  chunkOverlap: number;
  retrieveK: number;
  serverHost: string;
  serverPort: number;
  proxy?: string;
  workflowMode: WorkflowMode;  // 默认状态机模式，需要时切多agent协作
  agents: Record<string, Partial<AgentConfig>>;
}

// ----- Agent -----
export type AgentName =
  | "orchestrator"
  | "story-architect"
  | "narrative-writer"
  | "character-designer"
  | "consistency-checker"
  | "story-explorer"
  | "presenter";

export type ModelTier = "high" | "mid" | "low";
export type SandboxMode = "read-write" | "read-only";

export interface AgentMeta {
  name: AgentName;
  label: string;
  role: string;
  icon: string;
  phase: string;
  modelTier: ModelTier;
  sandbox: SandboxMode;
  tools: string[];
}

export interface AgentPrompt {
  system: string;
  tools: string[];
}

// ----- 工具 -----
export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
}

export interface ToolDefinition {
  type: "function";
  function: {
    name: string;
    description: string;
    parameters: {
      type: "object";
      properties: Record<string, unknown>;
      required?: string[];
    };
  };
}

export interface ToolResult {
  [key: string]: unknown;
  error?: string;
}

export type ToolHandler = (
  pid: string,
  args: Record<string, unknown>,
) => Promise<ToolResult | string>;

// ----- 记忆 -----
export type MemoryCategory =
  | "character" | "plot" | "style" | "preference"
  | "world" | "lesson" | "context" | "decision" | "feedback";

export type MemorySource = "user" | "agent" | "auto";
export type MemoryTier = "short" | "long";

export interface SessionMemory {
  id: string;
  projectId: string;
  sessionId: string;
  category: string;
  topic: string;
  content: string;
  source: MemorySource;
  relevance: number;
  createdAt: number;
  expiresAt: number;
}

export interface LongTermMemory {
  id: string;
  projectId: string;
  category: string;
  topic: string;
  content: string;
  source: MemorySource;
  priority: number;       // 1-10
  supersededBy?: string;
  createdAt: number;
  updatedAt: number;
}

export interface MemoryConflict {
  id: string;
  projectId: string;
  oldMemoryId: string;
  newMemoryId: string;
  resolution: "superseded" | "merged" | "kept_both" | "kept_old";
  reason: string;
  createdAt: number;
}

// ----- 数据模型 -----
export interface Project {
  id: string;
  name: string;
  genre: string;
  premise: string;
  style: string;
  audience: string;
  meta: string;
  currentPhase: number;
  createdAt: number;
}

export interface Chapter {
  id: string;
  projectId: string;
  title: string;
  idx: number;
  outline: string;
  content: string;
  status: string;
  createdAt: number;
  updatedAt: number;
}

export interface Element {
  id: string;
  projectId: string;
  kind: "character" | "location" | "lore" | "timeline";
  name: string;
  detail: string;
  createdAt: number;
}

export interface Chunk {
  id: string;
  projectId: string;
  source: string;
  idx: number;
  text: string;
  embedding?: string;
  createdAt: number;
}

export interface Message {
  id: string;
  projectId: string;
  role: "user" | "assistant" | "tool";
  content: string;
  toolName?: string;
  toolCallId?: string;
  createdAt: number;
}

export interface Run {
  id: string;
  projectId: string;
  userInput: string;
  agentName: string;
  status: string;
  createdAt: number;
  finishedAt?: number;
}

export interface CharacterProfile {
  id: string;
  projectId: string;
  name: string;
  role?: string;
  personality?: string;
  speechStyle?: string;
  behaviorLogic?: string;
  motivation?: string;
  arc?: string;
  growthState?: string;
  createdAt: number;
  updatedAt: number;
}

// ----- SSE 事件 -----
export type SSEEvent =
  | { type: "start"; agent: AgentName; input: string }
  | { type: "think_start"; agent: AgentName; round: number }
  | { type: "think_token"; agent: AgentName; text: string }
  | { type: "think_end"; agent: AgentName; feasible: boolean; reason: string; plan: string[]; missing: string }
  | { type: "answer_start"; agent: AgentName }
  | { type: "answer_end"; agent: AgentName }
  | { type: "step"; agent: AgentName; tool: string; args: Record<string, unknown>; thinking?: string }
  | { type: "delegate"; from: AgentName; to: AgentName; task: string; depth: number }
  | { type: "delegate_done"; from: AgentName; to: AgentName; task: string; result: string; durationMs: number }
  | { type: "sub_agent_start"; agent: AgentName; task: string; depth: number }
  | { type: "sub_agent_done"; agent: AgentName; result: string; durationMs: number }
  | { type: "sub_answer"; agent: AgentName; content: string }
  | { type: "sub_agent_error"; agent: AgentName; error: string }
  | { type: "observation"; agent: AgentName; tool: string; result: string }
  | { type: "token"; agent: AgentName; content: string }
  | { type: "done"; agent: AgentName; steps: number; stats: Record<string, unknown>; runId: string }
  | { type: "error"; message: string }
  | { type: "heartbeat"; ts: number; runId: string }
  | { type: "sandbox_validate"; agent: AgentName; tool: string; passed: boolean; issues: string[] }
  | { type: "forced_revision"; agent: AgentName; score: number; review: string; chapterId: string; reason: string }
  | { type: "review_passed"; agent: AgentName; score: number; newPhase: number; chapterId: string };

// ----- LLM -----
export interface LLMResponse {
  content: string;
  model?: string;
  toolCalls?: ToolCall[];
  usage?: { promptTokens: number; completionTokens: number; totalTokens: number };
}

// ----- 工作流阶段 -----
export interface WorkflowPhase {
  phase: number;
  name: string;
  agent?: AgentName;
  agents?: AgentName[];
  description: string;
  loop?: "reject" | "next-chapter";
}
