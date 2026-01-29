"""
Apple Music API 客户端。
提供 Apple Music API 请求的 HTTP 客户端能力。
"""

import asyncio
import logging
from io import BytesIO
from ssl import SSLError
from typing import Optional, Any

import httpx
import regex
from httpx import Request, Response, AsyncHTTPTransport
from tenacity import (
    retry,
    retry_if_exception_type,
    wait_random_exponential,
    stop_after_attempt,
    before_sleep_log,
)

# 优先使用 hishel 缓存，失败则退回 httpx
try:
    import hishel
    HAS_HISHEL = hasattr(hishel, 'AsyncCacheClient')
except ImportError:
    HAS_HISHEL = False

from .config import PluginConfig, DownloadConfig
from .models import (
    AlbumMeta,
    AlbumTracks,
    PlaylistInfo,
    PlaylistTracks,
    ArtistAlbums,
    ArtistSongs,
    ArtistInfo,
    SongData,
)


logger = logging.getLogger(__name__)

# 启用调试日志
logger.setLevel(logging.DEBUG)


class NameSolver:
    """用于覆盖 Apple CDN IP 的自定义 DNS 解析器。"""

    def __init__(self, cdn_ip: str = ""):
        self.cdn_ip = cdn_ip

    def get(self, name: str) -> str:
        if name == "aod.itunes.apple.com" and self.cdn_ip:
            return self.cdn_ip
        return ""

    def resolve(self, request: Request) -> Request:
        host = request.url.host
        ip = self.get(host)

        if ip:
            request.extensions["sni_hostname"] = host
            request.url = request.url.copy_with(host=ip)

        return request


class AsyncCustomHost(AsyncHTTPTransport):
    """支持 DNS 覆盖的自定义 HTTP 传输。"""

    def __init__(self, solver: NameSolver, *args, **kwargs) -> None:
        self.solver = solver
        super().__init__(*args, **kwargs)

    async def handle_async_request(self, request: Request) -> Response:
        request = self.solver.resolve(request)
        return await super().handle_async_request(request)


class WebAPI:
    """用于 Apple Music 的 Web API 客户端。"""

    client: Optional[httpx.AsyncClient]
    download_lock: asyncio.Semaphore
    request_lock: asyncio.Semaphore
    token: Optional[str]
    cdn_ip: str
    _proxy: str
    _token_lock: asyncio.Lock
    _initialized: bool

    def __init__(self, proxy: str = "", parallel_num: int = 1, cdn_ip: str = ""):
        """初始化 Web API 客户端。"""
        self.cdn_ip = cdn_ip
        self._proxy = proxy
        self.token = None
        self.client = None
        self._initialized = False
        self._token_lock = asyncio.Lock()

        self.download_lock = asyncio.Semaphore(parallel_num)
        self.request_lock = asyncio.Semaphore(256)

        logger.info("[WebAPI] Instance created (lazy initialization)")

    async def _ensure_initialized(self):
        """确保客户端完成初始化并持有有效 token。"""
        if self._initialized:
            return

        async with self._token_lock:
            if self._initialized:
                return

            logger.info("[WebAPI] Starting async initialization...")

            # 异步获取 token
            await self._set_token_async()

            # 使用 token 创建 HTTP 客户端
            client_kwargs: dict[str, Any] = {
                "headers": {
                    "Authorization": f"Bearer {self.token}",
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
                    "Origin": "https://music.apple.com",
                },
                "follow_redirects": True,
                "timeout": 30.0,
            }
            if self._proxy:
                client_kwargs["proxy"] = self._proxy

            if HAS_HISHEL:
                self.client = hishel.AsyncCacheClient(**client_kwargs)
            else:
                self.client = httpx.AsyncClient(**client_kwargs)

            self._initialized = True
            logger.info("[WebAPI] Async initialization complete")

    async def _set_token_async(self):
        """异步获取并设置 Apple Music API token。"""
        logger.info("[WebAPI] Fetching Apple Music API token (async)...")

        max_attempts = 5
        for attempt in range(max_attempts):
            try:
                async with httpx.AsyncClient(timeout=30.0) as client:
                    logger.debug(f"[WebAPI] Attempt {attempt + 1}/{max_attempts}: Requesting https://music.apple.com ...")

                    resp = await client.get("https://music.apple.com", follow_redirects=True)
                    logger.debug(f"[WebAPI] Got response, status={resp.status_code}")

                    if resp.status_code != 200:
                        raise httpx.HTTPError(f"HTTP {resp.status_code}")

                    index_js_uri = regex.findall(r"/assets/index~[^/]+\.js", resp.text)
                    if not index_js_uri:
                        raise ValueError("Could not find index JS file in response")

                    index_js_uri = index_js_uri[0]
                    logger.debug(f"[WebAPI] Found JS file: {index_js_uri}")

                    js_resp = await client.get("https://music.apple.com" + index_js_uri)
                    token_match = regex.search(r'eyJh([^"]*)', js_resp.text)
                    if not token_match:
                        raise ValueError("Could not extract token from JS file")

                    self.token = token_match[0]
                    logger.info(f"[WebAPI] Token obtained: {self.token[:20]}...")
                    return

            except Exception as e:
                logger.warning(f"[WebAPI] Attempt {attempt + 1}/{max_attempts} failed: {e}")
                if attempt < max_attempts - 1:
                    wait_time = min(2 ** attempt, 30)  # 指数退避，最大 30 秒
                    logger.info(f"[WebAPI] Retrying in {wait_time}s...")
                    await asyncio.sleep(wait_time)
                else:
                    logger.error("[WebAPI] All attempts to fetch token failed")
                    raise

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(32),
    )
    def _set_token(self):
        """同步获取并设置 Apple Music API token（已弃用）。"""
        logger.info("[WebAPI] Fetching Apple Music API token (sync)...")
        with httpx.Client(timeout=30.0) as client:
            logger.debug("[WebAPI] Requesting https://music.apple.com ...")
            resp = client.get("https://music.apple.com", follow_redirects=True)
            logger.debug(f"[WebAPI] Got response, status={resp.status_code}")
            index_js_uri = regex.findall(r"/assets/index~[^/]+\.js", resp.text)[0]
            logger.debug(f"[WebAPI] Found JS file: {index_js_uri}")
            js_resp = client.get("https://music.apple.com" + index_js_uri)
            self.token = regex.search(r'eyJh([^"]*)', js_resp.text)[0]
            logger.info(f"[WebAPI] Token obtained: {self.token[:20]}...")

    async def close(self):
        """关闭 HTTP 客户端。"""
        if self.client:
            await self.client.aclose()

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(32),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def _request(self, *args, **kwargs):
        """执行限速 HTTP 请求。"""
        await self._ensure_initialized()
        async with self.request_lock:
            return await self.client.request(*args, **kwargs)

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, SSLError, FileNotFoundError)),
        wait=wait_random_exponential(multiplier=1, max=60),
        stop=stop_after_attempt(32),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
    async def download_song(self, url: str) -> bytes:
        """下载音频数据。"""
        async with self.download_lock:
            result = BytesIO()
            async with httpx.AsyncClient(
                transport=AsyncCustomHost(NameSolver(self.cdn_ip))
            ) as client:
                async with client.stream("GET", url) as response:
                    total = int(
                        response.headers.get("Content-Length")
                        or response.headers.get("X-Apple-MS-Content-Length", 0)
                    )
                    async for chunk in response.aiter_bytes():
                        result.write(chunk)
                    if total and len(result.getvalue()) != total:
                        raise httpx.HTTPError("Incomplete download")
                    return result.getvalue()

    async def get_album_info(self, album_id: str, storefront: str, lang: str) -> AlbumMeta:
        """获取专辑元数据。"""
        req = await self._request(
            "GET",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums/{album_id}",
            params={
                "omit[resource]": "autos",
                "include": "tracks,artists,record-labels",
                "include[songs]": "artists",
                "fields[artists]": "name",
                "fields[albums:albums]": "artistName,artwork,name,releaseDate,url",
                "fields[record-labels]": "name",
                "l": lang,
            },
        )
        album_info_obj = AlbumMeta.model_validate(req.json())

        # 处理曲目分页
        if album_info_obj.data[0].relationships.tracks.next:
            all_tracks = await self.get_album_tracks(album_id, storefront, lang)
            album_info_obj.data[0].relationships.tracks.data = all_tracks

        return album_info_obj

    async def get_album_tracks(
        self, album_id: str, storefront: str, lang: str, offset: int = 0
    ) -> list:
        """分页获取专辑曲目。"""
        req = await self._request(
            "GET",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums/{album_id}/tracks?offset={offset}",
        )
        album_info_obj = AlbumTracks.model_validate(req.json())
        tracks = album_info_obj.data or []

        if album_info_obj.next:
            next_tracks = await self.get_album_tracks(album_id, storefront, lang, offset + 300)
            tracks.extend(next_tracks)

        return tracks

    async def get_playlist_info_and_tracks(
        self, playlist_id: str, storefront: str, lang: str
    ) -> PlaylistInfo:
        """获取歌单信息与全部曲目。"""
        resp = await self._request(
            "GET",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/playlists/{playlist_id}",
            params={"l": lang},
        )
        playlist_info_obj = PlaylistInfo.model_validate(resp.json())

        if playlist_info_obj.data[0].relationships.tracks.next:
            all_tracks = await self.get_playlist_tracks(playlist_id, storefront, lang)
            playlist_info_obj.data[0].relationships.tracks.data = all_tracks

        return playlist_info_obj

    async def get_playlist_tracks(
        self, playlist_id: str, storefront: str, lang: str, offset: int = 0
    ) -> list:
        """分页获取歌单曲目。"""
        resp = await self._request(
            "GET",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/playlists/{playlist_id}/tracks",
            params={"l": lang, "offset": offset},
        )
        playlist_tracks = PlaylistTracks.model_validate(resp.json())
        tracks = playlist_tracks.data

        if playlist_tracks.next:
            next_tracks = await self.get_playlist_tracks(
                playlist_id, storefront, lang, offset + 100
            )
            tracks.extend(next_tracks)

        return tracks

    async def get_cover(self, url: str, cover_format: str, cover_size: str) -> bytes:
        """下载专辑封面。"""
        async with self.request_lock:
            formatted_url = regex.sub("bb.jpg", f"bb.{cover_format}", url)
            req = await self._request("GET", formatted_url.replace("{w}x{h}", cover_size))
            return req.content

    async def get_song_info(self, song_id: str, storefront: str, lang: str) -> Optional[object]:
        """获取歌曲元数据。"""
        logger.info(f"[WebAPI] get_song_info: song_id={song_id}, storefront={storefront}, lang={lang}")
        req = await self._request(
            "GET",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{song_id}",
            params={"extend": "extendedAssetUrls", "include": "albums,explicit", "l": lang},
        )
        logger.debug(f"[WebAPI] get_song_info response status: {req.status_code}")
        song_data_obj = SongData.model_validate(req.json())

        for data in song_data_obj.data:
            if data.id == song_id:
                logger.info(f"[WebAPI] Found song: {data.attributes.name if hasattr(data.attributes, 'name') else 'unknown'}")
                return data

        logger.warning(f"[WebAPI] Song {song_id} not found in response")
        return None

    async def song_exist(self, song_id: str, storefront: str) -> bool:
        """检查歌曲是否存在于指定地区。"""
        req = await self._request(
            "HEAD",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/songs/{song_id}",
        )
        return req.status_code == 200

    async def album_exist(self, album_id: str, storefront: str) -> bool:
        """检查专辑是否存在于指定地区。"""
        req = await self._request(
            "HEAD",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums/{album_id}",
        )
        return req.status_code == 200

    async def get_albums_from_artist(
        self, artist_id: str, storefront: str, lang: str, offset: int = 0
    ) -> list[str]:
        """获取艺人全部专辑链接。"""
        resp = await self._request(
            "GET",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/artists/{artist_id}/albums",
            params={"l": lang, "offset": offset},
        )
        artist_album = ArtistAlbums.model_validate(resp.json())
        albums = [album.attributes.url for album in artist_album.data]

        if artist_album.next:
            next_albums = await self.get_albums_from_artist(
                artist_id, storefront, lang, offset + 25
            )
            albums.extend(next_albums)

        return list(set(albums))

    async def get_songs_from_artist(
        self, artist_id: str, storefront: str, lang: str, offset: int = 0
    ) -> list[str]:
        """获取艺人全部歌曲链接。"""
        resp = await self._request(
            "GET",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/artists/{artist_id}/songs",
            params={"l": lang, "offset": offset},
        )
        artist_song = ArtistSongs.model_validate(resp.json())
        songs = [song.attributes.url for song in artist_song.data]

        if artist_song.next:
            next_songs = await self.get_songs_from_artist(
                artist_id, storefront, lang, offset + 20
            )
            songs.extend(next_songs)

        return list(set(songs))

    async def get_artist_info(self, artist_id: str, storefront: str, lang: str) -> ArtistInfo:
        """获取艺人元数据。"""
        resp = await self._request(
            "GET",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/artists/{artist_id}",
            params={"l": lang},
        )
        return ArtistInfo.model_validate(resp.json())

    async def download_m3u8(self, m3u8_url: str) -> str:
        """下载 M3U8 内容。"""
        resp = await self._request("GET", m3u8_url)
        return resp.text

    async def get_real_url(self, url: str) -> str:
        """跟随重定向获取最终 URL。"""
        req = await self._request("GET", url, follow_redirects=True)
        return str(req.url)

    async def get_album_by_upc(self, upc: str, storefront: str) -> Optional[dict]:
        """按 UPC 查询专辑。"""
        req = await self._request(
            "GET",
            f"https://amp-api.music.apple.com/v1/catalog/{storefront}/albums",
            params={"filter[upc]": upc},
        )
        resp = req.json()
        try:
            if resp["data"]:
                return req.json()
            else:
                return None
        except KeyError:
            return None

    async def exist_on_storefront_by_song_id(
        self, song_id: str, storefront: str, check_storefront: str
    ) -> bool:
        """检查歌曲是否存在于其他地区。"""
        if storefront.upper() == check_storefront.upper():
            return True
        return await self.song_exist(song_id, check_storefront)

    async def exist_on_storefront_by_album_id(
        self, album_id: str, storefront: str, check_storefront: str
    ) -> bool:
        """检查专辑是否存在于其他地区。"""
        if storefront.upper() == check_storefront.upper():
            return True
        return await self.album_exist(album_id, check_storefront)
