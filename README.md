# AstrBot Apple Music Downloader

> ⚠️ 为避免服务器过载和封号风险，本插件仅允许下载单曲，不支持原项目中的专辑、播放列表等批量下载功能。

## 📋 要求

- AstrBot v3.4.0+
- Docker Engine

## 🚀 安装步骤

1. 进入 AstrBot 目录安装

   ```bash
   cd AstrBot/data/plugins
   git clone --recurse-submodules https://gh.llkk.cc/https://github.com/UE-DND/astrbot_apple_music_downloader.git
   ```

2. 配置 `config.yaml`

   ```bash
   cd astrbot_apple_music_downloader/apple-music-downloader
   mv config.example.yaml config.yaml
   ```

3. 重启 AstrBot

4. 配置 Docker 镜像（首次启动）

   ```bash
   chmod +x ./start.sh && ./start.sh start
   ```

## 📖 使用方法

### 基本指令

| 指令 | 说明 | 示例 |
|------|------|------|
| `/am dl <链接> [音质]` | 下载单曲 | `/am dl https://music.apple.com/cn/album/xxx/123?i=456` |
| `/am clean` | 清理所有下载文件 | `/am clean` |
| `/am status` | 查看服务状态 | `/am status` |
| `/am start` | 启动服务 | `/am start` |
| `/am stop` | 停止服务 | `/am stop` |
| `/am build` | 构建镜像 | `/am build` |
| `/am help` | 显示帮助 | `/am help` |

### 音质选项

| 参数 | 说明 |
|------|------|
| `alac` / `无损` | 无损 ALAC 格式（默认） |
| `aac` | 高品质 AAC 格式 |
| `atmos` / `杜比` | 杜比全景声 |

### 示例

```txt
# 下载单曲（默认无损）
/am dl https://music.apple.com/cn/album/xxx/123456?i=789

# 下载单曲（杜比全景声）
/am dl https://music.apple.com/cn/album/xxx/123456?i=789 atmos

# 下载单曲（AAC）
/am dl https://music.apple.com/cn/album/xxx/123456?i=789 aac
```

## ⚙️ 配置说明

在 AstrBot WebUI 的插件配置中可以设置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `downloader_path` | 下载器目录路径 | `apple-music-downloader` |
| `auto_start_wrapper` | 自动启动服务 | `true` |
| `default_quality` | 默认音质 | `alac` |
| `download_timeout` | 下载超时（秒） | `600` |
| `max_file_size_mb` | 最大文件大小 | `200` |
| `storefront` | Apple Music 区域 | `cn` |
| `send_cover` | 发送封面 | `true` |

## 🔧 首次使用

首次使用时，插件会自动构建 Docker 镜像，这可能需要 5-10 分钟。

1. 确保 Docker 已启动
2. 发送 `/am build` 手动构建镜像（可选）
3. 发送 `/am status` 检查服务状态

## ⚠️ 注意事项

- 一次只能进行一个下载任务，其他用户需排队等待
- 下载文件每 24 小时自动清理
- 部分曲目可能因地区限制不可用
- 文件过大时，将保存到服务器而不会发送
