FROM python:3.11-slim

# 防止 Python 写 .pyc、缓冲输出，便于容器内查看日志
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# 先装依赖，利用 Docker 层缓存
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# 拷贝应用代码与启动脚本
COPY app ./app
COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

# 服务端口（可通过环境变量覆盖 uvicorn 实际监听端口）
ENV APP_HOST=0.0.0.0 \
    APP_PORT=8000
EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import json,os,sys,urllib.request; \
port=os.environ.get('APP_PORT','8000'); \
sys.exit(0 if json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=2))['status']=='ok' else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
