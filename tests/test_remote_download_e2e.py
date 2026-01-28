"""
远程模式真实下载链路测试

使用固定的远程实例与歌曲链接，避免环境变量依赖。
"""

import sys
from pathlib import Path

import pytest

# 将项目根目录加入路径
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from core.config import PluginConfig
from services.downloader import DownloaderService, DownloadQuality
from services.wrapper_service import WrapperService

REMOTE_INSTANCE_URL = "wm1.wol.moe"
REMOTE_INSTANCE_SECURE = True
DEFAULT_TEST_SONG_URL = "https://music.apple.com/cn/song/neo-aspect/1799894983"
DEFAULT_STOREFRONT = "cn"
DEFAULT_LANGUAGE = "zh-Hans-CN"
# 运行测试前请确保 PATH 可解析到 mp4extract（如 /usr/bin/mp4extract-bento4 或 ~/.local/bin/mp4extract）。


@pytest.mark.asyncio
async def test_remote_download_full_chain(tmp_path):
    remote_url = REMOTE_INSTANCE_URL
    song_url = DEFAULT_TEST_SONG_URL
    secure = REMOTE_INSTANCE_SECURE
    storefront = DEFAULT_STOREFRONT
    language = DEFAULT_LANGUAGE

    config = PluginConfig()
    config.wrapper.mode = "remote"
    config.wrapper.url = remote_url
    config.wrapper.secure = secure
    config.region.storefront = storefront
    config.region.language = language
    config.plugin_dir = tmp_path

    wrapper_service = WrapperService(config)
    downloader = DownloaderService(config, wrapper_service)

    try:
        success, message = await downloader.init()
        if not success:
            pytest.skip(f"Wrapper 初始化失败: {message}")

        wrapper_status = await wrapper_service.get_status()
        if not wrapper_status.connected:
            pytest.skip("Wrapper 未连接，无法进行远程下载测试")
        if not wrapper_status.ready or wrapper_status.client_count <= 0:
            pytest.skip("Wrapper 未就绪或无可用账号，无法进行远程下载测试")

        result = await downloader.download(
            song_url,
            quality=DownloadQuality.ALAC,
            force=True
        )

        assert result.success, result.error or result.message
        assert result.file_paths, "下载结果未返回文件路径"

        output_path = Path(result.file_paths[0])
        assert output_path.exists(), f"下载文件不存在: {output_path}"
        assert output_path.stat().st_size > 0, "下载文件为空"

    finally:
        await downloader.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
