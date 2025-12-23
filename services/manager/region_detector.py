"""
地区检测器。
检测地区与歌曲可用性。
"""

import asyncio
from typing import Dict, List, Optional, Set
from dataclasses import dataclass, field
from datetime import datetime, timedelta
import aiohttp

from ..logger import LoggerInterface, get_logger
logger = get_logger()


@dataclass
class RegionInfo:
    """地区信息。"""
    code: str  # 地区代码（如 "us"、"cn"、"jp"）
    name: str  # 展示名称
    available: bool = True
    last_check: datetime = field(default_factory=datetime.now)


@dataclass
class SongAvailability:
    """歌曲跨地区可用性。"""
    adam_id: str
    available_regions: Set[str] = field(default_factory=set)
    unavailable_regions: Set[str] = field(default_factory=set)
    last_check: datetime = field(default_factory=datetime.now)


class RegionDetector:
    """地区可用性检测与缓存管理。"""

    # 常见 Apple Music 店面
    KNOWN_REGIONS = {
        "us": "United States",
        "cn": "China",
        "jp": "Japan",
        "hk": "Hong Kong",
        "tw": "Taiwan",
        "gb": "United Kingdom",
        "de": "Germany",
        "fr": "France",
        "au": "Australia",
        "ca": "Canada",
        "kr": "South Korea",
        "sg": "Singapore",
        "th": "Thailand",
        "in": "India",
        "br": "Brazil",
        "mx": "Mexico",
        "es": "Spain",
        "it": "Italy",
        "nl": "Netherlands",
        "se": "Sweden",
    }

    def __init__(
        self,
        cache_ttl: int = 3600,  # 默认 1 小时
        api_timeout: int = 10,
    ):
        """初始化地区检测器。"""
        self.cache_ttl = cache_ttl
        self.api_timeout = api_timeout

        # 缓存
        self._regions: Dict[str, RegionInfo] = {}
        self._song_availability: Dict[str, SongAvailability] = {}

        # 初始化已知地区
        for code, name in self.KNOWN_REGIONS.items():
            self._regions[code] = RegionInfo(code=code, name=name)

        logger.info(f"Region detector initialized with {len(self._regions)} known regions")

    def get_all_regions(self) -> List[RegionInfo]:
        """获取全部已知地区。"""
        return list(self._regions.values())

    def get_available_regions(self) -> List[str]:
        """获取可用地区代码列表。"""
        return [
            region.code
            for region in self._regions.values()
            if region.available
        ]

    def get_region_name(self, code: str) -> Optional[str]:
        """获取地区显示名。"""
        region = self._regions.get(code)
        return region.name if region else None

    async def check_song_availability(
        self,
        adam_id: str,
        regions: Optional[List[str]] = None,
        force_refresh: bool = False
    ) -> SongAvailability:
        """检查歌曲在多个地区的可用性。"""
        # 检查缓存
        if not force_refresh and adam_id in self._song_availability:
            cached = self._song_availability[adam_id]
            age = (datetime.now() - cached.last_check).total_seconds()

            if age < self.cache_ttl:
                logger.debug(f"Using cached availability for {adam_id}")
                return cached

        # 确定待检测地区
        if regions is None:
            regions = self.get_available_regions()

        logger.info(f"Checking availability for {adam_id} in {len(regions)} regions")

        # 并发检测可用性
        availability = SongAvailability(adam_id=adam_id)

        tasks = []
        for region in regions:
            task = self._check_song_in_region(adam_id, region)
            tasks.append((region, task))

        # 汇总结果
        for region, task in tasks:
            try:
                is_available = await task
                if is_available:
                    availability.available_regions.add(region)
                else:
                    availability.unavailable_regions.add(region)
            except Exception as e:
                logger.error(f"Error checking {adam_id} in {region}: {e}")
                availability.unavailable_regions.add(region)

        # 缓存结果
        self._song_availability[adam_id] = availability

        logger.info(
            f"Song {adam_id} available in {len(availability.available_regions)} regions, "
            f"unavailable in {len(availability.unavailable_regions)}"
        )

        return availability

    async def _check_song_in_region(self, adam_id: str, region: str) -> bool:
        """检查歌曲在指定地区的可用性。"""
        try:
            # 使用 Apple Music API 查询接口
            url = f"https://itunes.apple.com/lookup"
            params = {
                "id": adam_id,
                "country": region,
                "entity": "song",
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    url,
                    params=params,
                    timeout=aiohttp.ClientTimeout(total=self.api_timeout)
                ) as response:
                    if response.status != 200:
                        logger.warning(f"API returned {response.status} for {adam_id} in {region}")
                        return False

                    data = await response.json()

                    # 检查歌曲是否在结果中
                    result_count = data.get("resultCount", 0)
                    return result_count > 0

        except asyncio.TimeoutError:
            logger.warning(f"Timeout checking {adam_id} in {region}")
            return False

        except Exception as e:
            logger.error(f"Error checking {adam_id} in {region}: {e}")
            return False

    def get_cached_availability(self, adam_id: str) -> Optional[SongAvailability]:
        """获取歌曲可用性缓存。"""
        return self._song_availability.get(adam_id)

    def is_available_in_region(self, adam_id: str, region: str) -> Optional[bool]:
        """从缓存判断歌曲在地区内是否可用。"""
        availability = self.get_cached_availability(adam_id)
        if not availability:
            return None

        if region in availability.available_regions:
            return True
        elif region in availability.unavailable_regions:
            return False
        else:
            return None

    async def suggest_alternative_regions(
        self,
        adam_id: str,
        preferred_region: str
    ) -> List[str]:
        """在首选地区不可用时推荐替代地区。"""
        # 检查全地区可用性
        availability = await self.check_song_availability(adam_id)

        # 首选地区可用则无需备选
        if preferred_region in availability.available_regions:
            return []

        # 返回所有可用地区
        return list(availability.available_regions)

    def clear_cache(self, adam_id: Optional[str] = None):
        """清理可用性缓存。"""
        if adam_id:
            self._song_availability.pop(adam_id, None)
            logger.info(f"Cleared cache for {adam_id}")
        else:
            self._song_availability.clear()
            logger.info("Cleared all availability cache")

    def get_cache_stats(self) -> Dict:
        """获取缓存统计信息。"""
        now = datetime.now()
        fresh_count = 0
        stale_count = 0

        for availability in self._song_availability.values():
            age = (now - availability.last_check).total_seconds()
            if age < self.cache_ttl:
                fresh_count += 1
            else:
                stale_count += 1

        return {
            "total_cached": len(self._song_availability),
            "fresh": fresh_count,
            "stale": stale_count,
            "cache_ttl": self.cache_ttl,
            "known_regions": len(self._regions),
        }

    async def refresh_stale_cache(self):
        """刷新过期缓存条目。"""
        now = datetime.now()
        to_refresh = []

        for adam_id, availability in self._song_availability.items():
            age = (now - availability.last_check).total_seconds()
            if age >= self.cache_ttl:
                to_refresh.append(adam_id)

        if to_refresh:
            logger.info(f"Refreshing {len(to_refresh)} stale cache entries")

            for adam_id in to_refresh:
                try:
                    await self.check_song_availability(adam_id, force_refresh=True)
                except Exception as e:
                    logger.error(f"Error refreshing {adam_id}: {e}")

    def add_custom_region(self, code: str, name: str):
        """添加自定义地区。"""
        if code not in self._regions:
            self._regions[code] = RegionInfo(code=code, name=name)
            logger.info(f"Added custom region: {code} ({name})")

    def mark_region_unavailable(self, code: str):
        """标记地区为不可用。"""
        if code in self._regions:
            self._regions[code].available = False
            self._regions[code].last_check = datetime.now()
            logger.warning(f"Marked region {code} as unavailable")

    def mark_region_available(self, code: str):
        """标记地区为可用。"""
        if code in self._regions:
            self._regions[code].available = True
            self._regions[code].last_check = datetime.now()
            logger.info(f"Marked region {code} as available")
