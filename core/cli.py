"""
终端 CLI 入口
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from getpass import getpass
from pathlib import Path
from typing import Any, Dict, Optional

from services import DownloaderService, DownloadQuality, WrapperService
from .config import PluginConfig
from .url import URLType, AppleMusicURL
from .utils import playlist_write_song_index


def _deep_merge(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """合并配置字典，优先使用 updates 覆盖。"""
    merged = dict(base)
    for key, value in updates.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _schema_node_default(node: Dict[str, Any]) -> Optional[Any]:
    """解析 schema 节点默认值。"""
    if node.get("type") == "object" and isinstance(node.get("items"), dict):
        items = {
            key: _schema_node_default(value)
            for key, value in node["items"].items()
        }
        return {key: value for key, value in items.items() if value is not None}
    if "default" in node:
        return node["default"]
    return None


def _load_schema_defaults(schema_path: Path) -> Dict[str, Any]:
    """从 _conf_schema.json 加载默认配置。"""
    if not schema_path.exists():
        print(f"× 未找到配置 schema: {schema_path}")
        return {}

    try:
        raw = schema_path.read_text(encoding="utf-8")
        schema = json.loads(raw)
    except Exception as exc:
        print(f"× 读取配置 schema 失败: {exc}")
        return {}

    if not isinstance(schema, dict):
        print("× 配置 schema 格式错误")
        return {}

    defaults: Dict[str, Any] = {}
    for key, node in schema.items():
        if not isinstance(node, dict):
            continue
        value = _schema_node_default(node)
        if value is not None:
            defaults[key] = value
    return defaults


def _load_config(path: Optional[str], overrides: Dict[str, Any]) -> PluginConfig:
    """加载配置文件并合并 CLI 覆盖项。"""
    plugin_dir = Path(__file__).resolve().parents[1]
    schema_path = plugin_dir / "_conf_schema.json"
    config_data: Dict[str, Any] = _load_schema_defaults(schema_path)

    if path:
        config_path = Path(path).expanduser()
        if not config_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {config_path}")
        raw = config_path.read_text(encoding="utf-8")
        file_data = json.loads(raw)
        if not isinstance(file_data, dict):
            raise ValueError("配置文件必须为 JSON 对象")
        config_data = _deep_merge(config_data, file_data)

    merged = _deep_merge(config_data, overrides)
    return PluginConfig.from_astrbot_config(merged, plugin_dir=plugin_dir)


def _build_overrides(args: argparse.Namespace) -> Dict[str, Any]:
    """构建 CLI 覆盖配置。"""
    overrides: Dict[str, Any] = {}

    if args.wrapper_url:
        overrides["wrapper_url"] = args.wrapper_url
    if args.wrapper_secure is not None:
        overrides["wrapper_secure"] = args.wrapper_secure

    region_cfg: Dict[str, Any] = {}
    if args.storefront:
        region_cfg["storefront"] = args.storefront
    if args.language:
        region_cfg["language"] = args.language
    if region_cfg:
        overrides["region_config"] = region_cfg

    path_cfg: Dict[str, Any] = {}
    if args.download_dir:
        path_cfg["download_dir"] = args.download_dir
    if path_cfg:
        overrides["path_config"] = path_cfg

    download_cfg: Dict[str, Any] = {}
    if args.default_quality:
        download_cfg["default_quality"] = args.default_quality
    if download_cfg:
        overrides["download_config"] = download_cfg

    if args.debug_mode is not None:
        overrides["debug_mode"] = args.debug_mode

    return overrides


def _parse_quality(value: str) -> DownloadQuality:
    """解析音质参数。"""
    value = value.strip().lower()
    mapping = {item.value: item for item in DownloadQuality}
    mapping.update({
        "atmos": DownloadQuality.EC3,
        "aac-he": DownloadQuality.AAC_BINAURAL,
    })
    return mapping.get(value, DownloadQuality.ALAC)


def _build_song_url(storefront: str, song_id: str) -> str:
    """构建单曲链接。"""
    return f"https://music.apple.com/{storefront}/song/{song_id}"


async def _resolve_url(api_client, raw_url: str) -> Optional[AppleMusicURL]:
    """解析链接，必要时跟随重定向。"""
    url_obj = AppleMusicURL.parse_url(raw_url)
    if url_obj:
        return url_obj

    try:
        real_url = await api_client.get_real_url(raw_url)
    except Exception as exc:
        print(f"× 无法解析链接: {exc}")
        return None

    return AppleMusicURL.parse_url(real_url)


async def _check_album_existence(
    wrapper_service: WrapperService,
    api_client,
    album_id: str,
    storefront: str
) -> bool:
    """检查专辑是否存在于可用地区。"""
    status = await wrapper_service.get_status()
    regions = status.regions or [storefront]

    for region in regions:
        try:
            if await api_client.exist_on_storefront_by_album_id(album_id, storefront, region):
                return True
        except Exception:
            continue

    return False


async def _prompt_input(prompt: str) -> str:
    """异步读取用户输入。"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: input(prompt))


async def _handle_status(config: PluginConfig) -> int:
    """显示服务状态。"""
    wrapper_service = WrapperService(config)
    downloader_service = DownloaderService(config=config, wrapper_service=wrapper_service)
    try:
        success, msg = await downloader_service.init()
        if not success:
            print(f"× {msg}")
            return 1

        service_status = await downloader_service.get_status()
        wrapper_status = await wrapper_service.get_status()

        lines = [
            "服务状态",
            "-" * 20,
            f"Wrapper 地址: {service_status.wrapper_url}",
            f"连接状态: {'√ 已连接' if wrapper_status.connected else '× 未连接'}",
            f"服务就绪: {'√ 是' if wrapper_status.ready else '× 否'}",
            f"已登录账户数: {wrapper_status.client_count}",
            f"可用地区: {', '.join(wrapper_status.regions) if wrapper_status.regions else '-'}",
            f"API 可用: {'√ 是' if service_status.api_available else '× 否'}",
        ]
        if service_status.error:
            lines.append(f"错误: {service_status.error}")
        print("\n".join(lines))
        return 0
    finally:
        await downloader_service.close()


async def _handle_accounts(config: PluginConfig) -> int:
    """查看已登录账户状态。"""
    wrapper_service = WrapperService(config)
    try:
        success, msg = await wrapper_service.init()
        if not success:
            print(f"× {msg}")
            return 1

        status = await wrapper_service.get_status()
        lines = [
            "账户状态",
            "-" * 20,
            f"服务状态: {'√ 已连接' if status.connected else '× 未连接'}",
            f"服务就绪: {'√ 是' if status.ready else '× 否'}",
            f"已登录账户数: {status.client_count}",
            f"可用地区: {', '.join(status.regions) if status.regions else '-'}",
        ]
        if status.error:
            lines.append(f"错误: {status.error}")
        print("\n".join(lines))
        return 0
    finally:
        await wrapper_service.close()


async def _handle_login(config: PluginConfig, username: str, password: str) -> int:
    """登录 Apple Music 账户。"""
    wrapper_service = WrapperService(config)
    try:
        if not username:
            username = input("Apple ID: ").strip()
        if not password:
            password = getpass("密码: ")

        success, msg = await wrapper_service.init()
        if not success:
            print(f"× {msg}")
            return 1

        manager = await wrapper_service.get_manager()
        if not manager:
            print("× 无法获取服务管理器")
            return 1

        async def on_2fa(uname: str, pwd: str) -> str:
            print("需要双因素验证码")
            code = await _prompt_input("请输入 6 位验证码: ")
            return code.strip()

        await manager.login(username, password, on_2fa)
        print("√ 登录成功")
        return 0
    except Exception as exc:
        print(f"× 登录失败: {exc}")
        return 1
    finally:
        await wrapper_service.close()


async def _handle_logout(config: PluginConfig, username: str) -> int:
    """登出 Apple Music 账户。"""
    wrapper_service = WrapperService(config)
    try:
        success, msg = await wrapper_service.init()
        if not success:
            print(f"× {msg}")
            return 1

        manager = await wrapper_service.get_manager()
        if not manager:
            print("× 无法获取服务管理器")
            return 1

        if not username:
            print("× 请提供用户名")
            return 1

        await manager.logout(username)
        print("√ 已登出")
        return 0
    except Exception as exc:
        print(f"× 登出失败: {exc}")
        return 1
    finally:
        await wrapper_service.close()


async def _handle_download(
    config: PluginConfig,
    url: str,
    quality: Optional[str],
    force: bool,
    include_participate_songs: bool,
) -> int:
    """下载单曲或批量内容。"""
    wrapper_service = WrapperService(config)
    downloader_service = DownloaderService(config=config, wrapper_service=wrapper_service)
    try:
        success, msg = await downloader_service.init()
        if not success:
            print(f"× {msg}")
            return 1

        quality_value = quality or config.download.default_quality
        download_quality = _parse_quality(quality_value)

        def progress_callback(status, message: str = ""):
            if message:
                print(f"[{status.value}] {message}")
            else:
                print(f"[{status.value}]")

        api_client = downloader_service._api
        if not api_client:
            print("× API 客户端未初始化")
            return 1

        url_obj = await _resolve_url(api_client, url)
        if not url_obj:
            print("× 无效的 Apple Music 链接")
            return 1

        if url_obj.type == URLType.Song:
            return await _download_single(
                downloader_service,
                url_obj.url,
                download_quality,
                force,
                progress_callback,
            )

        if url_obj.type == URLType.Album:
            return await _download_album(
                downloader_service,
                wrapper_service,
                api_client,
                url_obj,
                download_quality,
                force,
                progress_callback,
                config,
            )

        if url_obj.type == URLType.Playlist:
            return await _download_playlist(
                downloader_service,
                api_client,
                url_obj,
                download_quality,
                force,
                progress_callback,
                config,
            )

        if url_obj.type == URLType.Artist:
            return await _download_artist(
                downloader_service,
                wrapper_service,
                api_client,
                url_obj,
                download_quality,
                force,
                progress_callback,
                include_participate_songs,
                config,
            )

        print("× 不支持的链接类型")
        return 1
    finally:
        await downloader_service.close()


async def _download_single(
    downloader_service: DownloaderService,
    url: str,
    download_quality: DownloadQuality,
    force: bool,
    progress_callback,
    playlist: Optional[Any] = None,
) -> int:
    """执行单曲下载。"""
    result = await downloader_service.download(
        url=url,
        quality=download_quality,
        force=force,
        progress_callback=progress_callback,
        playlist=playlist,
    )

    if not result.success:
        print(f"× {result.message}")
        if result.error:
            print(f"错误: {result.error}")
        return 1

    print(f"√ {result.message}")
    if result.track_info:
        title = result.track_info.get("title") or "-"
        artist = result.track_info.get("artist") or "-"
        album = result.track_info.get("album") or "-"
        print(f"歌曲: {title} - {artist}")
        print(f"专辑: {album}")
    if result.file_paths:
        print("音频文件:")
        for path in result.file_paths:
            print(f"- {path}")
    if result.cover_path:
        print(f"封面: {result.cover_path}")
    if result.lyrics_path:
        print(f"歌词: {result.lyrics_path}")
    return 0


async def _download_batch(
    downloader_service: DownloaderService,
    song_urls: list[str],
    download_quality: DownloadQuality,
    force: bool,
    progress_callback,
    config: PluginConfig,
    playlist: Optional[Any] = None,
) -> int:
    """批量下载单曲。"""
    if not song_urls:
        print("× 未找到可下载的单曲")
        return 1

    semaphore = asyncio.Semaphore(max(1, config.queue.max_queue_size))

    async def bounded_download(song_url: str) -> int:
        async with semaphore:
            return await _download_single(
                downloader_service,
                song_url,
                download_quality,
                force,
                progress_callback,
                playlist,
            )

    tasks = [asyncio.create_task(bounded_download(song_url)) for song_url in song_urls]
    results = await asyncio.gather(*tasks)
    failed = sum(1 for code in results if code != 0)

    if failed:
        print(f"\n× 批量下载完成，失败 {failed} 首")
        return 1
    print("\n√ 批量下载完成")
    return 0


async def _download_album(
    downloader_service: DownloaderService,
    wrapper_service: WrapperService,
    api_client,
    url_obj: AppleMusicURL,
    download_quality: DownloadQuality,
    force: bool,
    progress_callback,
    config: PluginConfig,
) -> int:
    """下载专辑。"""
    storefront = url_obj.storefront or config.region.storefront
    language = config.region.language

    album_info = await api_client.get_album_info(url_obj.id, storefront, language)
    if not album_info.data:
        print("× 无法获取专辑信息")
        return 1

    exists = await _check_album_existence(wrapper_service, api_client, url_obj.id, storefront)
    if not exists:
        print("× 专辑不存在或不可用")
        return 1

    album_name = album_info.data[0].attributes.name or "专辑"
    tracks = album_info.data[0].relationships.tracks.data or []
    song_urls = [
        _build_song_url(storefront, track.id)
        for track in tracks
        if getattr(track, "id", None)
    ]

    print(f"○ 批量任务: {album_name}，共 {len(song_urls)} 首")
    return await _download_batch(
        downloader_service,
        song_urls,
        download_quality,
        force,
        progress_callback,
        config,
    )


async def _download_playlist(
    downloader_service: DownloaderService,
    api_client,
    url_obj: AppleMusicURL,
    download_quality: DownloadQuality,
    force: bool,
    progress_callback,
    config: PluginConfig,
) -> int:
    """下载歌单。"""
    storefront = url_obj.storefront or config.region.storefront
    language = config.region.language

    playlist_info = await api_client.get_playlist_info_and_tracks(url_obj.id, storefront, language)
    if not playlist_info.data:
        print("× 无法获取歌单信息")
        return 1

    playlist_info = playlist_write_song_index(playlist_info)
    playlist_name = playlist_info.data[0].attributes.name or "歌单"
    tracks = playlist_info.data[0].relationships.tracks.data or []
    song_urls = [
        _build_song_url(storefront, track.id)
        for track in tracks
        if getattr(track, "id", None)
    ]

    print(f"○ 批量任务: {playlist_name}，共 {len(song_urls)} 首")
    return await _download_batch(
        downloader_service,
        song_urls,
        download_quality,
        force,
        progress_callback,
        config,
        playlist=playlist_info,
    )


async def _download_artist(
    downloader_service: DownloaderService,
    wrapper_service: WrapperService,
    api_client,
    url_obj: AppleMusicURL,
    download_quality: DownloadQuality,
    force: bool,
    progress_callback,
    include_participate_songs: bool,
    config: PluginConfig,
) -> int:
    """下载艺术家。"""
    storefront = url_obj.storefront or config.region.storefront
    language = config.region.language

    if include_participate_songs:
        song_urls = await api_client.get_songs_from_artist(url_obj.id, storefront, language)
        print(f"○ 批量任务: 艺术家单曲，共 {len(song_urls)} 首")
        return await _download_batch(
            downloader_service,
            song_urls,
            download_quality,
            force,
            progress_callback,
            config,
        )

    album_urls = await api_client.get_albums_from_artist(url_obj.id, storefront, language)
    if not album_urls:
        print("× 未找到可下载的专辑")
        return 1

    async def download_album_url(album_url: str) -> int:
        album_obj = AppleMusicURL.parse_url(album_url)
        if not album_obj:
            return 1
        return await _download_album(
            downloader_service,
            wrapper_service,
            api_client,
            album_obj,
            download_quality,
            force,
            progress_callback,
            config,
        )

    tasks = [asyncio.create_task(download_album_url(album_url)) for album_url in album_urls]
    results = await asyncio.gather(*tasks)
    failed = sum(1 for code in results if code != 0)
    if failed:
        print(f"\n× 艺术家专辑下载完成，失败 {failed} 项")
        return 1
    print("\n√ 艺术家专辑下载完成")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    """构建 CLI 参数解析器。"""
    parser = argparse.ArgumentParser(
        prog="python -m core",
        description="AstrBot Apple Music Downloader CLI",
    )
    parser.add_argument("--config", help="配置文件路径(JSON)")
    parser.add_argument("--wrapper-url", help="Wrapper 服务地址")
    parser.add_argument("--wrapper-secure", dest="wrapper_secure", action="store_true")
    parser.add_argument("--wrapper-insecure", dest="wrapper_secure", action="store_false")
    parser.set_defaults(wrapper_secure=None)
    parser.add_argument("--storefront", help="默认地区代码")
    parser.add_argument("--language", help="默认语言")
    parser.add_argument("--download-dir", help="下载目录")
    parser.add_argument("--default-quality", choices=[q.value for q in DownloadQuality], help="默认音质")
    parser.add_argument("--debug", dest="debug_mode", action="store_true")
    parser.add_argument("--no-debug", dest="debug_mode", action="store_false")
    parser.set_defaults(debug_mode=None)

    subparsers = parser.add_subparsers(dest="command")

    subparsers.add_parser("status", help="查看服务状态")
    subparsers.add_parser("accounts", help="查看账户状态")

    login_parser = subparsers.add_parser("login", help="登录账户")
    login_parser.add_argument("-u", "--username", default="")
    login_parser.add_argument("-p", "--password", default="")

    logout_parser = subparsers.add_parser("logout", help="登出账户")
    logout_parser.add_argument("-u", "--username", required=True)

    download_parser = subparsers.add_parser("download", help="下载歌曲/专辑/歌单/艺术家")
    download_parser.add_argument("-l", "--url", required=True, help="Apple Music 链接")
    download_parser.add_argument(
        "-q",
        "--quality",
        choices=[q.value for q in DownloadQuality],
        help="音质(alac/ec3/ac3/aac/aac-binaural/aac-downmix/aac-legacy)",
    )
    download_parser.add_argument("--force", action="store_true", help="强制重新下载")
    download_parser.add_argument(
        "--include-participate-songs",
        dest="include_participate_songs",
        action="store_true",
        help="艺术家链接时包含单曲",
    )

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    """命令行主入口。"""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2

    overrides = _build_overrides(args)
    try:
        config = _load_config(args.config, overrides)
    except Exception as exc:
        print(f"× 配置加载失败: {exc}")
        return 1

    log_level = logging.DEBUG if config.debug_mode else logging.INFO
    logging.basicConfig(level=log_level)
    logging.getLogger().setLevel(log_level)

    if args.command == "status":
        return asyncio.run(_handle_status(config))
    if args.command == "accounts":
        return asyncio.run(_handle_accounts(config))
    if args.command == "login":
        return asyncio.run(_handle_login(config, args.username, args.password))
    if args.command == "logout":
        return asyncio.run(_handle_logout(config, args.username))
    if args.command == "download":
        return asyncio.run(
            _handle_download(
                config,
                args.url,
                args.quality,
                args.force,
                args.include_participate_songs,
            )
        )

    print("× 未知命令")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
