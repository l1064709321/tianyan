#!/usr/bin/env node
/**
 * 天衍 - 一键安装脚本
 * 
 * 用法: npm install (自动执行)
 * 
 * 功能:
 *   1. 检测 Python 环境
 *   2. 配置 pip 镜像源 (国内加速)
 *   3. 安装全部 Python 依赖
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

// 检测 Python 命令
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

// 执行命令
function run(cmd, options = {}) {
  try {
    execSync(cmd, { 
      stdio: 'inherit', 
      cwd: path.resolve(__dirname, '..'),
      ...options 
    });
    return true;
  } catch (e) {
    return false;
  }
}

// 静默执行
function runSilent(cmd) {
  try {
    execSync(cmd, { 
      stdio: 'pipe', 
      cwd: path.resolve(__dirname, '..') 
    });
    return true;
  } catch (e) {
    return false;
  }
}

// 主流程
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

  // 3. 配置镜像源 (国内加速)
  logInfo('[3/6] 配置 pip 镜像源...');
  const isWindows = os.platform() === 'win32';
  
  // 尝试多个镜像源
  const mirrors = [
    { name: '清华源', url: 'https://pypi.tuna.tsinghua.edu.cn/simple' },
    { name: '阿里云', url: 'https://mirrors.aliyun.com/pypi/simple/' },
    { name: '华为云', url: 'https://repo.huaweicloud.com/repository/pypi/simple/' },
  ];
  
  let mirrorSet = false;
  for (const mirror of mirrors) {
    if (runSilent(`${python} -m pip config set global.index-url ${mirror.url}`)) {
      logSuccess(`  ✓ 使用 ${mirror.name}`);
      mirrorSet = true;
      break;
    }
  }
  if (!mirrorSet) {
    logWarn('  ! 镜像源配置失败，使用默认源 (可能较慢)');
  }

  // 4. 安装核心依赖
  logInfo('[4/6] 安装核心依赖...');
  const corePackages = [
    'fastapi', 'uvicorn', 'litellm', 'openai',
    'pydantic', 'pydantic-settings', 'PyYAML',
    'python-multipart', 'httpx', 'python-dotenv',
  ];
  const coreCmd = `${python} -m pip install ${corePackages.join(' ')}`;
  if (!run(coreCmd)) {
    logError('  ✗ 核心依赖安装失败');
    logError('    请手动运行: pip install fastapi uvicorn litellm');
    process.exit(1);
  }
  logSuccess('  ✓ 核心依赖安装完成');

  // 5. 安装扩展依赖
  logInfo('[5/6] 安装扩展依赖...');
  const extPackages = [
    // 文件格式
    'python-docx', 'pypdf', 'ebooklib', 'beautifulsoup4', 'Markdown',
    // 网页抓取
    'readability-lxml', 'lxml',
    // 向量检索
    'chromadb',
    // 记忆系统
    'redis', 'psycopg2-binary',
    // 沙箱安全
    'RestrictedPython',
  ];
  const extCmd = `${python} -m pip install ${extPackages.join(' ')}`;
  if (!run(extCmd)) {
    logWarn('  ! 部分扩展依赖安装失败，核心功能仍可用');
    logWarn('    向量检索/记忆系统可能降级');
  } else {
    logSuccess('  ✓ 扩展依赖安装完成');
  }

  // 6. 浏览器抓取 (可选)
  logInfo('[6/6] 浏览器抓取 (可选)...');
  if (runSilent(`${python} -m pip install playwright`)) {
    logSuccess('  ✓ playwright 已安装');
    logInfo('    正在下载 Chromium 内核 (约 180MB)...');
    if (run(`${python} -m playwright install chromium`)) {
      logSuccess('  ✓ Chromium 内核下载完成');
    } else {
      logWarn('  ! Chromium 下载失败，浏览器抓取功能不可用');
      logWarn('    手动安装: python -m playwright install chromium');
    }
  } else {
    logWarn('  ! playwright 安装失败，浏览器抓取功能不可用');
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
