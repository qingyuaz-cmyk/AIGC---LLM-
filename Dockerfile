FROM python:3.9-slim

WORKDIR /app

# 系统依赖：ffmpeg + Playwright Chromium 运行时 + curl
RUN apt-get update && apt-get install -y \
    build-essential \
    ffmpeg \
    curl \
    wget \
    gnupg \
    libnss3 \
    libatk-bridge2.0-0 \
    libdrm2 \
    libxkbcommon0 \
    libgbm1 \
    libasound2 \
    libxshmfence1 \
    && rm -rf /var/lib/apt/lists/*

# Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 安装 Playwright Chromium（爬虫模块需要）
RUN playwright install chromium

# 复制项目
COPY . .

# 确保持久化目录存在
RUN mkdir -p data videos config temp_creator seedance_output Generated_Prompts

EXPOSE 8501

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8
ENV PYTHONUNBUFFERED=1

CMD ["streamlit", "run", "app.py", \
     "--server.port=8501", \
     "--server.address=0.0.0.0", \
     "--server.headless=true", \
     "--server.fileWatcherType=none"]
