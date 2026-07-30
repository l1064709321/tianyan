FROM python:3.10-slim

WORKDIR /app

# 系统依赖（ebooklib 等需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（清华源）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple

# 复制项目
COPY . .

# 数据持久化目录
RUN mkdir -p /data

ENV NOVEL_AGENT_DATA_DIR=/data
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.server:app", "--host", "0.0.0.0", "--port", "8000"]