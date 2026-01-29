# Repository Guidelines

## 项目定位与架构
- AstrBot 插件，入口 `main.py` 负责注册指令、初始化队列、连接 wrapper 服务与下载服务。
- 交互层：`handlers/`，处理 `/am*` 指令、交互式下载、登录与 2FA、队列与文件管理。
- 编排层：`services/`，封装下载流程、队列、日志与 wrapper 管理。
- 领域核心：`core/`，Apple Music API、下载流水线、封装与落盘。
- Wrapper 体系：仅保留 `remote` 模式，`services/wrapper_service.py` 统一管理并连接远程 wrapper-manager。
- 开发文档：位于 `docs/Astrbot`，在进行任何改动前，阅读开发文档以符合 Astrbot 规范。

## 功能边界与运行模式
- **AstrBot 指令仅支持单曲链接**（带 `?i=` 参数或 `/song/` 路径）。专辑/歌单/艺术家仅在 CLI 模式支持。
- 下载通过队列串行处理（可配置并发与队列上限），支持取消、排队通知与任务查询。
- 下载文件会定时清理（默认 24 小时 TTL），可手动清理。

## 下载流程要点
- 调用链：`DownloaderService.download()` -> `core.rip.rip_song()`。
- 流程：metadata/album -> lyrics -> m3u8 -> 下载加密音频 -> gRPC 解密 -> 封装 -> 完整性校验 -> `save_all()`。
- 文件发送与清理由 `handlers/file_manager.py` 管理。

## AstrBot 指令概览
- `/am`：交互式下载（仅单曲）。
- `/am <链接> [音质]`：直接下载（仅单曲，音质 `alac`/`aac`）。
- `/am_queue`：查看队列。
- `/am_mytasks`：查看我的任务。
- `/am_cancel [任务ID|all]`：取消任务。
- `/am_status`：服务状态。
- `/am_start` / `/am_stop`：启动/停止 wrapper 服务。
- `/am_clean [sudo]`：手动清理下载文件（`sudo` 强制清理）。
- `/am_login <账号> <密码>` / `/am_2fa <验证码>` / `/am_logout <账号>` / `/am_accounts`：账户管理。

## CLI 模式
- 入口：`python -m core`（使用仓库根目录执行）。
- 支持下载单曲/专辑/歌单/艺术家：`python -m core download -l <链接> [-q 音质] [--force] [--include-participate-songs]`。
- 音质选项：`alac`/`ec3`/`ac3`/`aac`/`aac-binaural`/`aac-downmix`/`aac-legacy`。
- CLI 会读取 `_conf_schema.json` 默认值，并与 `--config` 指定 JSON 配置合并。

## 配置要点（见 `_conf_schema.json` / `core/config.py`）
- Wrapper：`wrapper_url`、`wrapper_secure`。
- 队列：`queue_config`（队列长度、超时、通知、每用户上限等）。
- 区域：`region_config`（storefront/language）。
- 下载：`download_config`（默认音质、歌词/封面、转换格式、解密超时等）。
- 元数据：`metadata_config`（嵌入字段）。
- 路径：`path_config`（下载目录与命名模板）。
- 文件：`file_config`（发送限制、清理间隔、TTL）。

## Project Structure & Module Organization
- `handlers/`：命令与会话状态机、队列与文件管理。
- `services/`：下载编排、wrapper 管理、日志；`services/queue/` 为队列实现。
- `core/`：API、下载、封装、配置、模型；`core/grpc/` 为 wrapper-manager gRPC 客户端。
- `bin/`：wrapper 资产与 rootfs，除非更新 wrapper 否则不动。
- `ref-code/`：上游代码参考，不在生产路径 import。

## Build, Test, and Development Commands
- 依赖：`python -m pip install -r "requirements.txt"`。
- 系统依赖：`ffmpeg`、`gpac`（`MP4Box`）、`Bento4`（`mp4extract/mp4edit/mp4decrypt`），可用 `scripts/install-deps.sh` 安装。
- 测试：`pytest -q`，或 `pytest "tests/test_core_modules.py" -q`。
- Lint：`ruff check .`。

## Coding Style & Naming Conventions
- 4 空格缩进，保持函数短小，异步边界清晰。
- 命名：`snake_case` / `PascalCase` / `UPPER_SNAKE_CASE`。
- 配置统一走 `core/config.py`，避免直接读取 AstrBot 原始 dict。
- 注释与 docstring 使用中文，与现有代码一致。

## Testing Guidelines
- 使用 `pytest` + `pytest.mark.asyncio`，目前以 unit + mock 为主。
- 命名规则：`tests/test_*.py`。

## Commit & Pull Request Guidelines
- Commit 格式：`type: summary`（如 `feat: refine wrapper status`）。
- PR 描述需包含行为变化、配置影响、运行/测试命令。

## Security & Configuration Notes
- 禁止提交下载产物。
- 路径模板在 `path_config`（如 `{album_artist}/{album}`），请保持与下载逻辑一致。
