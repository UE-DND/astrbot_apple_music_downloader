# AstrBot Apple Music Downloader

> ⚠️ 为避免服务器过载和封号风险，AstrBot 方式下仅允许下载单曲，不支持专辑、播放列表等批量下载功能。

## 安装

```bash
cd AstrBot/data/plugins
git clone https://gh-proxy.com/https://github.com/UE-DND/astrbot_apple_music_downloader.git
```

重启 AstrBot 以自动识别插件，插件重启后可能需要 1 分钟以安装所有依赖

### 项目额外依赖（需手动安装）

AstrBot 只会自动安装 `requirements.txt` 中的 Python 依赖，系统级工具需要手动安装并确保在 `PATH` 中可用：

- `ffmpeg`
- `gpac`（提供 `gpac` 与 `MP4Box`）
- `Bento4`（提供 `mp4extract` / `mp4edit` / `mp4decrypt`）

依赖可通过 `scripts/install-deps.sh` 安装

### 初次启动

## 通过 AstrBot 框架使用

1. **检查服务状态**

   ```bot
   /am_status
   ```

2. **下载音乐**

   ```bot
   /am https://music.apple.com/cn/album/xxx/123?i=456
   ```

   **指定下载音质**

   ```bot
   /am https://music.apple.com/cn/album/xxx/123?i=456 aac
   ```

### 示例

```bash
# 下载单曲（不添加音质参数时，默认为alac）
/am https://music.apple.com/cn/album/青春コンプレックス/1657318546?i=1657318551

# 下载单曲（AAC 音质）
/am https://music.apple.com/cn/album/富士山下/1443345687?i=1443346107 aac
```

### 指令概览

| 指令 | 说明 |
|:-----|:-----|
| `/am <链接> [音质]` | 下载单曲 |
| `/am_login <账号> <密码>` | 登录账户 |
| `/am_2fa <验证码>` | 输入 2FA 验证码 |
| `/am_logout <账号>` | 登出账户 |
| `/am_accounts` | 查看已登录账户 |
| `/am_queue` | 查看下载队列 |
| `/am_cancel <ID>` | 取消任务 |
| `/am_status` | 服务状态 |
| `/am_help` | 显示帮助 |

### 音质选项

| 参数 | 说明 |
|:-----|:-----|
| `alac` | 无损（默认）|
| `aac` | AAC |

> 插件仅支持 `alac` 与 `aac` 音质

### 插件配置项

> 💡 使用公共实例时无需登录账户

1. 在 AstrBot WebUI 中设置 `Wrapper-Manager 服务地址`

2. 热重启插件

用于测试的公共实例：

```toml
[instance] # 由 @WorldObservationLog 维护
url = "wm.wol.moe"
secure = true
# 或
[instance] # 由 @itouakira 维护
url = "wm1.wol.moe"
secure = true
```

## 通过 CLI 使用

由于后端 Python 环境与 AstrBot 隔离，通过 CLI 使用时需使用后端 Python 环境（可能为 `python3`）再次安装依赖。

```bash
python3 -m venv ".venv"
".venv/bin/python" -m pip install -r "requirements.txt"
```

在仓库根目录执行：

CLI 方式会自动读取 `_conf_schema.json` 以获取 Astrbot 配置

若使用其他配置文件，使用此命令切换：

```bash
".venv/bin/python" -m core status --config "./newconfig.json"
```

### CLI 命令与用法

#### 全局参数

```bash
--wrapper-url <host:port>
--wrapper-secure
--wrapper-insecure
--storefront <地区代码>
--language <语言>
--download-dir <下载目录>
--default-quality <alac|ec3|ac3|aac|aac-binaural|aac-downmix|aac-legacy>
--debug
--no-debug
```

> 全局参数仅对当前对话有效，初始化时以 `--config` 指向的配置文件为准

#### 常用命令

1. status：查看服务状态

   ```bash
   ".venv/bin/python" -m core status [全局参数]
   ```

2. accounts：查看账户状态

   ```bash
   ".venv/bin/python" -m core accounts [全局参数]
   ```

3. login：登录账户（支持 2FA 交互）

   ```bash
   ".venv/bin/python" -m core login -u <AppleID> -p <密码> [全局参数]
   ```

4. logout：登出账户

   ```bash
   ".venv/bin/python" -m core logout -u <AppleID> [全局参数]
   ```

5. download：下载歌曲/专辑/歌单/艺术家

   ```bash
   ".venv/bin/python" -m core download -l <链接> [-q <音质>] [--force] [--include-participate-songs] [全局参数]
   ```

#### CLI 音质选项

| 参数 | 说明 |
|:-----|:-----|
| `alac` | 无损（默认） |
| `ec3` | 杜比全景声 |
| `ac3` | 杜比数字 |
| `aac` | AAC |
| `aac-binaural` | AAC Binaural |
| `aac-downmix` | AAC Downmix |
| `aac-legacy` | AAC Legacy |

## ⚠️ 注意

- 部分曲目可能因地区限制不可用
- 文件默认于下载 24 小时后自动删除
- 此项目仅供技术交流，使用此项目即表示完全认识项目功能并对产生的后果承担相关责任

## 致谢

本项目整合了 [AppleMusicDecrypt](https://github.com/WorldObservationLog/AppleMusicDecrypt)、[wrapper-manager](https://github.com/WorldObservationLog/wrapper-manager) 的功能，二进制文件来自 [wrapper](https://github.com/WorldObservationLog/wrapper)。感谢所有上游开发者的贡献！
