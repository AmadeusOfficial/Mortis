# syntax=docker/dockerfile:1
# 莫提斯。三段：构建前端 → 装后端 → 运行镜像。
# 用法见 README 的「用 Docker 跑」，镜像只监听容器内的 0.0.0.0，对外由 compose 绑到宿主机回环。

# --- 前端构建阶段 ---
FROM node:24-slim AS web-builder
WORKDIR /app/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# --- Python 依赖阶段 ---
FROM python:3.14-slim AS base
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
COPY pyproject.toml ./
COPY src ./src
# 装完把源码目录删掉，免得 /app/src 下的旧副本和新装的那份同时能被 import。
RUN pip install . && rm -rf /app/src /app/build /app/*.egg-info

# --- 最终镜像 ---
FROM base
# 不再 apt-get：python:3.14-slim 自带 tzdata，Asia/Shanghai 靠 TZ 环境变量直接生效。
COPY plugins ./plugins
COPY --from=web-builder /app/web/dist ./web/dist

ENV TZ=Asia/Shanghai \
    MORTIS_HOST=0.0.0.0 \
    MORTIS_PORT=8100 \
    MORTIS_DATA_DIR=/data \
    MORTIS_WEB_DIST=/app/web/dist \
    MORTIS_PLUGINS_DIR=/app/plugins

EXPOSE 8100
VOLUME ["/data", "/media"]
CMD ["python", "-m", "mortis.server"]
