#!/usr/bin/env node
/**
 * 天衍 - 启动脚本
 * 
 * 用法: npm start
 * 
 * 功能:
 *   1. 检测 Python 环境
 *   2. 设置环境变量 (避免 litellm 超时)
 *   3. 启动服务
 *   4. 自动打开浏览器
 */

const { execSync, spawn } = require('child_process');
const os = require('os');
const path = require('path');

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
        return cmd;
      }
    } catch (e) {}
  }
  return null;
}

// 打开浏览器
function openBrowser(url) {
  const platform = os.platform();
  let cmd;
  if (platform === 'win32') {
    cmd = `start ${url}`;
  } else if (platform === 'darwin') {
    cmd = `open ${url}`;
  } else {
    cmd = `xdg-open ${url}`;
  }
  try {
    execSync(cmd, { stdio: 'pipe' });
  } catch (e) {}
}

// 主流程
async function main() {
  console.log('');
  logInfo('============================================================');
  logInfo('  天衍 启动中...');
  logInfo('============================================================');
  console.log('');

  // 1. 检测 Python
  const python = findPython();
  if (!python) {
    logError('  ✗ 未找到 Python 3.10+');
    logError('    请先安装 Python: https://www.python.org/downloads/');
    process.exit(1);
  }

  // 2. 检查依赖
  logInfo('检查依赖...');
  try {
    execSync(`${python} -c "import fastapi"`, { stdio: 'pipe' });
  } catch (e) {
    logWarn('  ! 依赖未安装，正在安装...');
    execSync(`node scripts/install.js`, { stdio: 'inherit', cwd: path.resolve(__dirname, '..') });
  }

  // 3. 设置环境变量
  process.env.LITELLM_LOCAL_MODEL_COST_MAP = 'True';

  // 4. 启动服务
  logInfo('启动服务...');
  console.log('');
  logSuccess('============================================================');
  logSuccess('  天衍 启动中...');
  logSuccess('  访问地址: http://localhost:8000/');
  logSuccess('  按 Ctrl+C 停止服务');
  logSuccess('============================================================');
  console.log('');

  // 等待服务启动后打开浏览器
  const serverProcess = spawn(python, ['run.py'], {
    cwd: path.resolve(__dirname, '..'),
    stdio: 'inherit',
    env: { ...process.env, LITELLM_LOCAL_MODEL_COST_MAP: 'True' },
  });

  // 延迟打开浏览器
  setTimeout(() => {
    openBrowser('http://localhost:8000');
  }, 3000);

  // 处理退出信号
  process.on('SIGINT', () => {
    serverProcess.kill('SIGINT');
    process.exit(0);
  });

  process.on('SIGTERM', () => {
    serverProcess.kill('SIGTERM');
    process.exit(0);
  });

  serverProcess.on('exit', (code) => {
    process.exit(code || 0);
  });
}

main().catch(err => {
  logError('启动失败:', err.message);
  process.exit(1);
});
