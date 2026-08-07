FROM python:3.10-slim

WORKDIR /app

# ============================================================
# 系统级依赖
# - firejail: 沙箱代码执行隔离 (断网+降权)
# - pypy3: 独立 Python 解释器 (沙箱内代码执行)
# - libxml2/libxslt: lxml 依赖 (网页解析)
# ============================================================
RUN apt-get update && apt-get install -y --no-install-recommends \
    firejail \
    pypy3 \
    libxml2 \
    libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

# ============================================================
# 创建虚拟环境 /app/venv
# 所有 Python 依赖安装到虚拟环境内, 不污染系统 Python
# ============================================================
RUN python -m venv /app/venv
ENV PATH="/app/venv/bin:$PATH"
ENV VIRTUAL_ENV="/app/venv"

# ============================================================
# 安装 Python 依赖 (清华镜像加速, 国内构建快)
# ============================================================
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt \
    -i https://pypi.tuna.tsinghua.edu.cn/simple \
    --trusted-host pypi.tuna.tsinghua.edu.cn

# ============================================================
# 复制源码
# ============================================================
COPY . .

# ============================================================
# 编译为 .pyc (代码保护, 保留 run.py 和 __init__.py)
# ============================================================
RUN python -m compileall -b app/ run.py && \
    find /app -name "*.py" \
        ! -name "run.py" \
        ! -name "__init__.py" \
        ! -path "*/venv/*" \
        ! -path "*/data/*" \
        -delete

# ============================================================
# 创建非 root 用户 appuser (安全: 容器内不使用 root)
# ============================================================
RUN useradd -m -s /bin/bash appuser && \
    chown -R appuser:appuser /app
USER appuser

# ============================================================
# 数据持久化目录
# ============================================================
RUN mkdir -p /data /app/logs
ENV NOVEL_AGENT_DATA_DIR=/data
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

EXPOSE 8000

# ============================================================
# 启动命令: 使用虚拟环境的 Python 执行 run.py
# CMD ["python", "run.py"] 中 python 已在 PATH 中指向 /app/venv/bin/python
# ============================================================
CMD ["python", "run.py"]
