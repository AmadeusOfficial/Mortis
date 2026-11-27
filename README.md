<div align="center">

<img src="web/public/favicon.svg" width="96" alt="莫提斯标志">

# 莫提斯 Mortis

**单人使用的本地片库**

TMDB 刮削 · 字段级择优 · 锁定不被覆盖 · 数据私有 · 热加载插件 · 本机播放 · 智能体协同 · Emby NFO 适配

[![Release](https://img.shields.io/github/v/release/AmadeusOfficial/Mortis?label=Release)](https://github.com/AmadeusOfficial/Mortis/releases)
[![Docker](https://img.shields.io/badge/Docker-ghcr.io-2496ED?logo=docker&logoColor=white)](https://github.com/AmadeusOfficial/Mortis/pkgs/container/mortis)
[![License](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)

[下载](https://github.com/AmadeusOfficial/Mortis/releases) · [反馈问题](https://github.com/AmadeusOfficial/Mortis/issues)

</div>

---

莫提斯是一个本地片库，管理电影、电视剧、动漫，也可以通过插件管理 Galgame 等其他类型。它扫描你的媒体目录，从 TMDB 等站点刮削元数据，存进本地 SQLite；需要时把 NFO 和图片写到视频旁边，交给 Emby / Jellyfin 之类的媒体服务器识别。

一个进程同时提供网页、任务和插件，只在本机运行，不转码，不做多用户。

## 亮点

| | |
|---|---|
| **按字段刮削和择优** | 以 TMDB 为主身份，合并 Fanart.tv、TVDB、IMDb 评分和烂番茄；每个来源的结果单独存快照，再次刮削只请求缺失的来源。 |
| **手改的不会被覆盖** | 字段可以手动修改并锁定，刮削会跳过锁定的字段。库是唯一的依据，不读本地已有的 NFO。 |
| **对齐 TMM 的整理选项** | 刮削、图片、字幕、重命名和 NFO 选项照 tinyMediaManager 设计，可以按模板重命名、整理目录。 |
| **剧集和系列** | 剧和动漫按剧、季、集组织，能识别季号、集号、绝对集数和特别篇；系列电影可以在海报墙上收起。 |
| **自定义标签和分类视图** | 按数据类型维护自己的标签，写进 NFO；电视剧库可以按勾选的标签一行一行展示。 |
| **热加载插件** | 数据类型、来源和播放插件放进插件目录即可加载。自带 Galgame 插件（Bangumi、VNDB、月幕 Galgame、CnGal），能启动游戏。 |
| **智能体（可选）** | 侧栏 AI 对话兼容 OpenAI 接口，能检索片库、处理识别错误、加减标签；批量修改和重新匹配都要你批准。 |
| **桌面版自带更新** | Windows 托盘程序看护后端，数据默认放在程序目录下，检查到新版后原地更新；升级数据库前自动备份。 |
| **也能用 Docker 跑** | 官方镜像 `ghcr.io/amadeusofficial/mortis`，一份 compose 就能起，数据目录挂出来即可备份；适合 Linux NAS 或不想装 Python 的机器。 |

## 开始使用

从 [Releases](https://github.com/AmadeusOfficial/Mortis/releases) 下载 `Mortis-v<版本>-windows-x64.zip`，解压后运行 `Mortis.exe`，托盘图标出现后浏览器会打开莫提斯。

- 数据库、图片、日志和设置都在解压目录下的 `data` 文件夹。要放到别处，编辑解压目录下的 `desktop.env`，改 `MORTIS_DATA_DIR`。
- 更新时托盘只替换程序文件，不动 `data` 和 `desktop.env`。不要直接删掉整个解压目录，数据会一起删除。
- 第一次使用先在「设置」里填 TMDB 的 API Key，再添加媒体库并扫描。

## 用 Docker 跑

不想装 Python 和 Node，或者在 NAS、Linux 服务器上跑，用官方镜像：

```sh
docker compose up -d
```

仓库里的 [`compose.yaml`](compose.yaml) 已经写好，改两处就能用：

- `volumes` 里的 `/path/to/media` 换成你自己的媒体目录。容器里一律用 `/media`、`/media2` 这样的挂载点建库，不是宿主机的路径。
- 想给局域网里的其他设备打开海报墙，把 `ports` 改成 `"8100:8100"`。默认只绑 `127.0.0.1`，和「只服务这台电脑」一致。

起来以后打开 http://127.0.0.1:8100 ，和桌面版一样先在「设置」里填 TMDB 的 API Key，再添加媒体库。

数据都在 `./data`（数据库、图片、日志、`config.toml`、`secrets.toml`、插件），备份这个目录就够。想换位置就改左边那半边路径。

几处和桌面版的差别：

- **删除是永久的**。容器里没有 Windows 回收站，「删除条目」直接删磁盘上的文件和目录，确认框里也会这么写。
- **播放和「在资源管理器中显示」用不了**。容器里没有本机播放器，也没有资源管理器。用不到就在「设置 → 插件」里停用 `system_player`。
- **「发现新文件就整理」可能收不到通知**。NFS/CIFS 网络盘和 Docker Desktop 的绑定挂载不产生 inotify 事件，这时在 `compose.yaml` 里打开 `MORTIS_WATCH_POLLING: "1"`，改成按间隔比对文件。
- **定时扫描按容器时区算**。默认 `TZ=Asia/Shanghai`，和你本机一致；不在这个时区就改 `compose.yaml`。

镜像同时发布 amd64 和 arm64，打 `v*` 标签时由 GitHub Actions 构建推送。

## 参与开发

前置依赖：Python 3.14、Node.js、.NET 8 SDK（只有打托盘程序时需要）。

```powershell
$env:MORTIS_PYTHON = "<python 3.14 解释器>"
pip install -e ".[dev]"
cd web; npm install; cd ..

scripts\dev.ps1              # 后端 :8100（热重载）加前端开发服务器 :5174
python -m pytest -q          # 测试
python -m ruff check src tests plugins examples scripts
scripts\build_windows.ps1    # 打 Windows zip 和 SHA256SUMS
```

技术栈：FastAPI · SQLAlchemy · SQLite · React + Mantine · .NET 托盘。开发约定见 [AGENTS.md](AGENTS.md)。

想给莫提斯写来源、数据类型、播放插件或事件钩子，先读 [插件开发指南](docs/插件开发指南.md)；示例在 [`examples/plugins/`](examples/plugins/)，参考文档有 [插件规范](docs/插件规范.md)、[事件参考](docs/事件参考.md) 和 [打包与导入](docs/打包与导入.md)。

## 许可证

[GPL-3.0](LICENSE)
