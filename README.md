# AstrBot Apple Music Downloader

> ⚠️ 为避免服务器过载和封号风险，本插件仅允许下载单曲，不支持专辑、播放列表等批量下载功能。

## 📋 要求

- Docker（推荐）或远程 Wrapper-Manager 服务

## 安装

```bash
cd AstrBot/data/plugins
git clone https://gh-proxy.com/https://github.com/UE-DND/astrbot_apple_music_downloader.git
```

重启 AstrBot 后即可使用。

## 🚀 快速开始

### 1. 检查服务状态

```
/am_status
```

### 2. 登录 Apple Music 账户

```
/am_login 你的AppleID 密码
```

如需 2FA 验证，输入收到的 6 位验证码：

```
/am_2fa 123456
```

### 3. 下载音乐

```
/am https://music.apple.com/cn/album/xxx/123?i=456
```

指定音质（可选）：

```
/am https://music.apple.com/cn/album/xxx/123?i=456 atmos
```

## 📖 命令一览

| 命令 | 说明 |
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
| `alac` | 无损（默认） |
| `aac` | AAC |
| `atmos` | 杜比全景声 |
| `aac-he` | Binaural |

## ⚙️ 使用公共实例

如果没有 Docker 环境，可使用公共 Wrapper-Manager 实例：

1. 在 AstrBot WebUI 中修改配置：
   - `wrapper_mode`
   - `wrapper_url`
   - `wrapper_secure`

用于测试的包装器管理器实例：

```toml
[instance]
url = "wm.wol.moe"
secure = true
```

2. 重启插件

> 💡 使用公共实例时无需登录账户，也无需 Apple Music 订阅即可下载。

## ⚠️ 注意

- 本地部署需要有效的 Apple Music 订阅
- 部分曲目可能因地区限制不可用
- 此项目仅供技术交流，下载文件默认 24 小时后自动删除

## 感谢所有上游开发者的贡献
