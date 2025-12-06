# AstrBot Apple Music Downloader

> ⚠️ 为避免服务器过载和封号风险，本插件仅允许下载单曲，不支持原项目中的专辑、播放列表等批量下载功能。如有需要，可以单独运行 `start.sh` 脚本

## 📋 要求

- Docker Engine

## 安装步骤

1. 进入 AstrBot 插件目录，克隆仓库

   ```bash
   cd AstrBot/data/plugins
   git clone --recurse-submodules https://gh-proxy.com/https://github.com/UE-DND/astrbot_apple_music_downloader.git
   ```

2. 进入核心下载器，配置 `config.yaml`

   ```bash
   cd astrbot_apple_music_downloader/apple-music-downloader
   mv config.example.yaml config.yaml
   ```

3. 配置 Docker 镜像（首次启动）

   ```bash
   chmod +x ./start.sh && ./start.sh start
   ```

4. 重启 AstrBot 以识别插件

## 📖 使用方法

### 基本指令

| 指令 | 说明 | 示例 |
|:------|:------|:------:|
| `/am` | 交互式下载 | `/am` |
| `/am <链接> [音质]` | 直接下载单曲 | `/am https://music.apple.com/cn/album/青春コンプレックス/1657318546?i=1657318551` |
| `/am_queue` | 查看下载队列 | `/am_queue` |
| `/am_mytasks` | 查看我的任务 | `/am_mytasks` |
| `/am_cancel` | 取消下载任务 | `/am_cancel <任务ID>` |
| `/am_status` | 查看服务状态 | `/am_status` |
| `/am_start` | 启动服务 | `/am_start` |
| `/am_stop` | 停止服务 | `/am_stop` |
| `/am_build` | 构建镜像 | `/am_build` |
| `/am_clean` | 清理下载文件 | `/am_clean` |
| `/am_help` | 显示帮助 | `/am_help` |

### 音质选项

| 参数 | 说明 |
|:------|:------|
| `alac` | 无损 ALAC 格式（默认） |
| `aac` | 高品质 AAC 格式 |
| `atmos`| 杜比全景声 |

### 示例

```bash
# 下载单曲（不添加音质参数时，默认为alac）
/am https://music.apple.com/cn/album/青春コンプレックス/1657318546?i=1657318551

# 下载单曲（杜比全景声）
/am https://music.apple.com/cn/album/才二十三/1764518989?i=1764518990 atmos

# 下载单曲（AAC）
/am https://music.apple.com/cn/album/富士山下/1443345687?i=1443346107 aac
```

## ⚙️ 配置说明

在 AstrBot WebUI 的插件配置中可以设置：

| 配置项 | 说明 | 默认值 |
|--------|------|--------|
| `downloader_path` | 下载器目录路径 | `apple-music-downloader` |
| `auto_start_wrapper` | 自动启动服务 | `true` |
| `default_quality` | 默认下载音质 | `alac` |
| `download_timeout` | 下载超时（秒） | `120` |
| `max_file_size_mb` | 最大文件大小 | `200` |
| `storefront` | Apple Music 区域 | `cn` |
| `send_cover` | 下载完成后发送封面 | `true` |

## 🔧 首次使用

首次使用时，插件会自动构建 Docker 镜像，这可能需要 5-10 分钟。

1. 确保 Docker 已启动
2. 进入后端手动[构建镜像](#安装步骤)并登录账号
3. 发送 `/am_status` 检查服务状态

## 向歌曲中写入歌词

> 歌词下载需要使用 Cookie。具体操作如下：

1. 登录网页版 Apple Music
2. 打开开发者工具，点击 `应用程序` -> `存储` -> `Cookies` -> `https://music.apple.com`
3. 找到名为 `media-user-token` 的 Cookie 并复制
4. 在插件配置中粘贴 Cookie 值

## 更新插件

```bash
# 在仓库根目录运行以下所有命令，或其它安全的更新方式

# 更新插件本体
git fetch && git reset --hard origin/v1

# 某些更新方式可能会删除配置文件，重启 AstrBot 以重新生成
```

## ⚠️ 注意

- 部分曲目可能因地区限制不可用
- 文件过大时，将保存到服务器而不会发送
- 此项目仅供技术交流，下载文件默认将于24小时内自动删除

## 感谢所有上游开发者的贡献
