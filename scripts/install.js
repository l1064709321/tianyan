#!/usr/bin/env node
/**
 * 天衍 - 一键安装脚本
 * 
 * 用法: npm install (自动执行)
 * 
 * 功能:
 *   1. 检测 Python 环境
 *   2. 配置 pip 镜像源 (国内加速)
 *   3. 安装全部 Python 依赖 (每个源 10 分钟超时, 自动切换)
 *   4. 下载 Playwright 浏览器内核 (可选)
 */

const { execSync, spawn } = require('child_process');
const os = require('os');
const path = require('path');
const fs = require('fs');

// 颜色输出
const colors = {
  reset: '\x1b[0m',
  red: '\x1b[31m',
  green: '\x1b[32m',
  yellow: '\x1b[33m',
  blue: '\x1b[34m',
  cyan: '\x1b[36m',
};

function log(color, ...args) {
  console.log(`${color}`, ...args, colors.reset);
}

function logInfo(...args) { log(colors.cyan, ...args); }
function logSuccess(...args) { log(colors.green, ...args); }
function logWarn(...args) { log(colors.yellow, ...args); }
function logError(...args) { log(colors.red, ...args); }

// ==================== 镜像源列表 ====================
// 国内源优先, 国内全部失败再切国外
const MIRRORS = [
  // ---- 国内源 ----
  { name: '清华源',   url: 'https://pypi.tuna.tsinghua.edu.cn/simple' },
  { name: '阿里云',   url: 'https://mirrors.aliyun.com/pypi/simple/' },
  { name: '华为云',   url: 'https://repo.huaweicloud.com/repository/pypi/simple/' },
  { name: '中科大',   url: 'https://pypi.mirrors.ustc.edu.cn/simple/' },
  { name: '腾讯云',   url: 'https://mirrors.cloud.tencent.com/pypi/simple/' },
  { name: '豆瓣',     url: 'https://pypi.douban.com/simple/' },
  { name: '网易',     url: 'https://mirrors.163.com/pypi/simple/' },
  // ---- 国外源 (国内全挂时兜底) ----
  { name: '官方源',   url: 'https://pypi.org/simple/' },
  { name: 'Google 源', url: 'https://pypi.googlesource.com/pypi/web/simple' },
];

// 单个源的下载超时: 10 分钟 (600 秒)
// 超过这个时间 pip 没有任何输出/进度, 判定为卡死, 自动切下一个源
const MIRROR_TIMEOUT_MS = 10 * 60 * 1000;

// 连通性测试超时: 5 秒
const CONNECT_TIMEOUT_SEC = 5;

// ==================== 工具函数 ====================

// 检测 Python 命令 (要求 3.10+)
function findPython() {
  const candidates = ['python3', 'python'];
  for (const cmd of candidates) {
    try {
      const result = execSync(`${cmd} --version`, { encoding: 'utf8', stdio: 'pipe' });
      if (result.includes('Python 3.')) {
        const version = result.trim().split(' ')[1];
        const [major, minor] = version.split('.').map(Number);
        if (major === 3 && minor >= 10) {
          return cmd;
        }
      }
    } catch (e) {}
  }
  return null;
}

// 静默执行
function runSilent(cmd, options = {}) {
  try {
    execSync(cmd, { 
      stdio: 'pipe', 
      cwd: path.resolve(__dirname, '..'),
      ...options 
    });
    return true;
  } catch (e) {
    return false;
  }
}

/**
 * 测试单个镜像源的连通性 (快速, 5 秒超时)
 */
function testMirror(python, mirror) {
  const hostname = new URL(mirror.url).hostname;
  const cmd = `${python} -m pip install --dry-run pip -i ${mirror.url} --trusted-host ${hostname} --timeout ${CONNECT_TIMEOUT_SEC}`;
  return runSilent(cmd);
}

/**
 * 用指定镜像源安装包 (带 10 分钟超时)
 * 
 * 核心逻辑: 用 spawn 替代 execSync, 监控子进程输出.
 * 如果 10 分钟内没有任何输出 (stderr/stdout 都没动), 判定为卡死, 杀掉进程.
 * pip 下载时会持续输出进度条, 只要还在动就不算超时.
 * 
 * @returns {boolean} 是否安装成功
 */
function installWithMirror(python, packages, mirror) {
  const hostname = new URL(mirror.url).hostname;
  const args = [
    '-m', 'pip', 'install',
    ...packages,
    '-i', mirror.url,
    '--trusted-host', hostname,
    '--timeout', String(CONNECT_TIMEOUT_SEC),
    '--retries', '2',
  ];

  return new Promise((resolve) => {
    const proc = spawn(python, args, {
      cwd: path.resolve(__dirname, '..'),
      stdio: ['ignore', 'pipe', 'pipe'],
      env: { ...process.env, LITELLM_LOCAL_MODEL_COST_MAP: 'True' },
    });

    let lastOutputTime = Date.now();
    let killed = false;

    // 有任何输出就刷新计时器 (pip 进度条、下载信息等)
    const onData = () => { lastOutputTime = Date.now(); };
    proc.stdout.on('data', onData);
    proc.stderr.on('data', onData);

    // 定时检查: 10 分钟没输出就杀
    const timer = setInterval(() => {
      if (killed) return;
      const idle = Date.now() - lastOutputTime;
      if (idle >= MIRROR_TIMEOUT_MS) {
        killed = true;
        proc.kill('SIGTERM');
        // 给 3 秒优雅退出, 否则强杀
        setTimeout(() => { try { proc.kill('SIGKILL'); } catch(e) {} }, 3000);
      }
    }, 10000); // 每 10 秒检查一次

    proc.on('close', (code) => {
      clearInterval(timer);
      if (killed) {
        logWarn(`      ⏱ 10 分钟无响应, 自动跳过`);
        resolve(false);
      } else {
        resolve(code === 0);
      }
    });

    proc.on('error', () => {
      clearInterval(timer);
      resolve(false);
    });
  });
}

/**
 * 核心安装流程: 逐源尝试, 每个源 10 分钟超时
 * 
 * 1. 先快速测试所有源的连通性 (5 秒/个)
 * 2. 按连通性排序, 通的排前面
 * 3. 逐个尝试安装, 每个源最多 10 分钟
 * 4. 成功就返回, 全部失败就报错
 */
async function installPackages(python, packages, label) {
  // 第一步: 快速测试所有源的连通性
  logInfo(`    测试镜像源连通性...`);
  const alive = [];
  const dead = [];
  for (const mirror of MIRRORS) {
    if (testMirror(python, mirror)) {
      alive.push(mirror);
      logSuccess(`      ✓ ${mirror.name}`);
    } else {
      dead.push(mirror);
      logWarn(`      ✗ ${mirror.name} 不通`);
    }
  }

  // 通的源优先, 不通的也加入队列 (万一测试时临时故障)
  const queue = [...alive, ...dead];

  if (queue.length === 0) {
    logError(`    所有镜像源均不可用`);
    return false;
  }

  logInfo(`    可用源: ${alive.length} 个, 开始安装 ${label}...`);

  // 第二步: 逐源尝试安装
  for (let i = 0; i < queue.length; i++) {
    const mirror = queue[i];
    const tag = `[${i + 1}/${queue.length}]`;
    logInfo(`    ${tag} 尝试 ${mirror.name}...`);

    const ok = await installWithMirror(python, packages, mirror);
    if (ok) {
      logSuccess(`    ✓ ${label} 安装成功 (来源: ${mirror.name})`);
      // 记住这个好用的源, 后续步骤直接用
      return mirror.url;
    }
    if (i < queue.length - 1) {
      logWarn(`    ! ${mirror.name} 安装失败, 切换下一个源...`);
    }
  }

  logError(`    ✗ ${label} 安装失败: 所有源均不可用`);
  return false;
}

// ==================== 主流程 ====================

async function main() {
  console.log('');
  logInfo('============================================================');
  logInfo('  天衍 - 一键安装');
  logInfo('============================================================');
  console.log('');

  // 1. 检测 Python
  logInfo('[1/6] 检测 Python 环境...');
  const python = findPython();
  if (!python) {
    logError('  ✗ 未找到 Python 3.10+');
    logError('    请先安装 Python: https://www.python.org/downloads/');
    logError('    安装时勾选 "Add Python to PATH"');
    process.exit(1);
  }
  const pyVersion = execSync(`${python} --version`, { encoding: 'utf8' }).trim();
  logSuccess(`  ✓ ${pyVersion}`);

  // 2. 升级 pip
  logInfo('[2/6] 升级 pip...');
  runSilent(`${python} -m pip install --upgrade pip -q`);
  logSuccess('  ✓ pip 已更新');

  // 3. 安装核心依赖 (逐源尝试, 每源 10 分钟超时)
  logInfo('[3/6] 安装核心依赖...');
  process.env.LITELLM_LOCAL_MODEL_COST_MAP = 'True';
  const corePackages = [
    'fastapi', 'uvicorn', 'litellm', 'openai',
    'pydantic', 'pydantic-settings', 'PyYAML',
    'python-multipart', 'httpx', 'python-dotenv',
  ];
  const goodMirror = await installPackages(python, corePackages, '核心依赖');
  if (!goodMirror) {
    logError('');
    logError('  核心依赖安装失败, 请手动运行:');
    logError('    pip install fastapi uvicorn litellm openai pydantic pydantic-settings');
    logError('    或检查网络连接后重试');
    process.exit(1);
  }

  // 4. 安装扩展依赖 (用上一步验证过的源, 失败不阻断)
  logInfo('[4/6] 安装扩展依赖...');
  const extPackages = [
    'python-docx', 'pypdf', 'ebooklib', 'beautifulsoup4', 'Markdown',
    'readability-lxml', 'lxml',
    'chromadb',
    'redis', 'psycopg2-binary',
    'RestrictedPython',
  ];
  const extResult = await installPackages(python, extPackages, '扩展依赖');
  if (!extResult) {
    logWarn('  ! 部分扩展依赖安装失败, 核心功能仍可用');
    logWarn('    向量检索/记忆系统可能降级');
  }

  // 5. 浏览器抓取 (可选)
  logInfo('[5/6] 浏览器抓取 (可选)...');
  if (runSilent(`${python} -m pip install playwright`)) {
    logSuccess('  ✓ playwright 已安装');
    logInfo('    正在下载 Chromium 内核 (约 180MB)...');
    const proc = spawn(python, ['-m', 'playwright', 'install', 'chromium'], {
      cwd: path.resolve(__dirname, '..'),
      stdio: 'inherit',
    });
    const exitCode = await new Promise(r => proc.on('close', r));
    if (exitCode === 0) {
      logSuccess('  ✓ Chromium 内核下载完成');
    } else {
      logWarn('  ! Chromium 下载失败, 浏览器抓取功能不可用');
      logWarn('    手动安装: python -m playwright install chromium');
    }
  } else {
    logWarn('  ! playwright 安装失败, 浏览器抓取功能不可用');
  }

  // 完成
  console.log('');
  logSuccess('============================================================');
  logSuccess('  安装完成！');
  logSuccess('');
  logSuccess('  启动命令:');
  logSuccess('    npm start');
  logSuccess('    或');
  logSuccess('    python run.py');
  logSuccess('');
  logSuccess('  然后浏览器打开: http://localhost:8000');
  logSuccess('');
  logSuccess('  首次使用需要在设置面板配置 API Key');
  logSuccess('  推荐 DeepSeek (国内直连): https://platform.deepseek.com');
  logSuccess('============================================================');
  console.log('');
}

main().catch(err => {
  logError('安装失败:', err.message);
  process.exit(1);
});
