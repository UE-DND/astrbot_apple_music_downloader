# Repository Guidelines

## 项目定位与架构
- 本项目是 AstrBot 插件，入口 `main.py` 负责注册命令、初始化队列、连接 wrapper 服务与下载服务。
- 交互层在 `handlers/`，处理 `/am*` 指令、交互式下载、登录与 2FA；编排层在 `services/`。
- Wrapper 体系分为 `native` 与 `remote`：`services/wrapper_service.py` 统一管理，`services/manager/` 提供原生 gRPC 服务器、实例管理、解密调度与健康监控。
- 领域核心在 `core/`：`api.py` 获取 token 并请求 Apple Music，`rip.py` 负责完整下载流水线，`mp4.py` 负责封装与 metadata，`save.py` 落盘。

## 下载流程要点
- 调用链：`DownloaderService.download()` -> `core.rip.rip_song()`。
- 流程：metadata/album -> lyrics -> m3u8 -> 下载加密音频 -> gRPC 解密 -> 封装 -> 完整性校验 -> `save_all()`。
- 仅支持单曲 URL，专辑与歌单在解析阶段直接拒绝。

## Project Structure & Module Organization
- `handlers/`：命令与会话状态机。
- `services/`：下载编排、wrapper 管理、日志。
- `services/queue/`：模块化队列，统一通过 `services/__init__.py` 暴露的 API 使用。
- `core/`：API、下载、封装、配置、模型。
- `core/grpc/`：wrapper-manager gRPC 客户端。
- `bin/`：wrapper 资产与 rootfs，除非更新 wrapper 否则不动。
- `ref-code/`：上游代码参考。

## Build, Test, and Development Commands
- 依赖：`python -m pip install -r "requirements.txt"`。
- 测试：`pytest -q`，或 `pytest "tests/test_core_modules.py" -q`。
- Lint：`ruff check .`。

## Coding Style & Naming Conventions
- 4 空格缩进，保持函数短小，异步边界清晰。
- 命名：`snake_case` / `PascalCase` / `UPPER_SNAKE_CASE`。
- 配置统一走 `core/config.py`，避免直接读取 AstrBot 原始 dict。
- 注释与 docstring 使用中文，与现有代码一致。

## Testing Guidelines
- 使用 `pytest` + `pytest.mark.asyncio`，目前以 unit + mock 为主。
- 若补充集成测试，需要可用 wrapper 与 Apple Music token，避免在 CI 直连真实账号。
- 命名规则：`tests/test_*.py`。

## Commit & Pull Request Guidelines
- Commit 格式：`type: summary`（如 `feat: refine wrapper status`）。
- PR 描述需包含行为变化、配置影响、运行/测试命令。

## Branch & Reference Notes
- 当前 `dev` 为重构主线，本地 `v1` 已移除。
- `ref-code/` 仅作对照，不要在生产路径直接 import。

## Security & Configuration Notes
- 禁止提交 Apple Music 账号、token、下载产物。
- 关键配置：`wrapper_*`、`queue_config`、`region_config`、`download_config`、`metadata_config`、`path_config`、`file_config`。
- 路径模板在 `path_config`（如 `{album_artist}/{album}`），请保持与下载逻辑一致。
