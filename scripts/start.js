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

  // 等待服务就绪后打开浏览器 (轮询 /api/health, 最多等 30 秒)
  const http = require('http');
  const start = Date.now();
  const maxWait = 30000;
  const poll = setInterval(() => {
    const req = http.get('http://localhost:8000/api/health', { timeout: 2000 }, (res) => {
      if (res.statusCode === 200) {
        clearInterval(poll);
        openBrowser('http://localhost:8000');
      }
      res.resume(); // 消费响应体
    });
    req.on('error', () => {}); // 忽略连接错误 (服务还没启动)
    req.on('timeout', () => { req.destroy(); });
    if (Date.now() - start > maxWait) {
      clearInterval(poll);
      // 超时也尝试打开, 让用户看到浏览器错误提示
      openBrowser('http://localhost:8000');
    }
  }, 1000);

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
