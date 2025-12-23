"""
下载队列格式化器。
负责格式化队列状态与任务信息。
"""

from __future__ import annotations
import time
from typing import List, Optional, TYPE_CHECKING
from abc import ABC, abstractmethod

if TYPE_CHECKING:
    from .task import DownloadTask
    from .stats import QueueStats


class QueueFormatter(ABC):
    """队列格式化器抽象基类。"""

    @abstractmethod
    def format_queue_status(
        self,
        tasks: List["DownloadTask"],
        current_task: Optional["DownloadTask"],
        stats: "QueueStats",
    ) -> str:
        """格式化完整队列状态。"""
        pass

    @abstractmethod
    def format_task_info(self, task: "DownloadTask", position: int = 0) -> str:
        """格式化单个任务信息。"""
        pass

    @abstractmethod
    def format_user_tasks(
        self,
        tasks: List["DownloadTask"],
        user_name: str,
    ) -> str:
        """格式化用户任务列表。"""
        pass


class ChineseFormatter(QueueFormatter):
    """中文队列显示格式化器。"""

    STATUS_DISPLAY = {
        "pending": "等待中",
        "processing": "下载中",
        "completed": "已完成",
        "failed": "失败",
        "cancelled": "已取消",
        "timeout": "超时",
    }

    PRIORITY_DISPLAY = {
        "LOW": "低",
        "NORMAL": "普通",
        "HIGH": "高",
        "URGENT": "紧急",
    }

    def format_queue_status(
        self,
        tasks: List["DownloadTask"],
        current_task: Optional["DownloadTask"],
        stats: "QueueStats",
    ) -> str:
        """格式化完整队列状态。"""
        lines = ["📊 **下载队列状态**", ""]

        if current_task:
            lines.append("🔄 **正在下载：**")
            lines.append(self._format_task_brief(current_task, processing=True))
            lines.append("")

        lines.append(f"📋 **队列概览：**")
        lines.append(f"• 队列中任务：{len(tasks)} 个")
        lines.append(f"• 队列容量：{stats.queue_size}/{stats.max_queue_size}")
        lines.append("")

        if stats.total_tasks > 0:
            lines.append("📈 **统计信息：**")
            lines.append(f"• 总任务数：{stats.total_tasks}")
            lines.append(f"• 已完成：{stats.completed_tasks}")
            lines.append(f"• 失败：{stats.failed_tasks}")
            lines.append(f"• 成功率：{stats.success_rate:.1%}")

            if stats.avg_wait_time > 0:
                lines.append(f"• 平均等待：{self._format_duration(stats.avg_wait_time)}")
            if stats.avg_process_time > 0:
                lines.append(f"• 平均处理：{self._format_duration(stats.avg_process_time)}")
            if stats.throughput > 0:
                lines.append(f"• 吞吐量：{stats.throughput:.1f} 任务/分钟")
            lines.append("")

        if tasks:
            lines.append("📝 **等待队列：**")
            for i, task in enumerate(tasks[:10], 1):
                lines.append(f"{i}. {self._format_task_brief(task)}")

            if len(tasks) > 10:
                lines.append(f"   ... 还有 {len(tasks) - 10} 个任务")
        else:
            lines.append("📝 **等待队列：** 空")

        return "\n".join(lines)

    def format_task_info(self, task: "DownloadTask", position: int = 0) -> str:
        """格式化任务详情。"""
        lines = [f"🎵 **任务详情** (ID: {task.task_id})", ""]

        lines.append(f"**URL：** {self._truncate_url(task.url, 40)}")
        lines.append(f"**音质：** {task.quality_display or task.quality}")
        if task.song_name:
            lines.append(f"**歌曲：** {task.song_name}")
        lines.append(f"**用户：** {task.user_name}")
        lines.append("")

        status_text = self.STATUS_DISPLAY.get(task.status.value, task.status.value)
        status_emoji = self._get_status_emoji(task.status.value)
        lines.append(f"**状态：** {status_emoji} {status_text}")

        if position > 0:
            lines.append(f"**队列位置：** 第 {position} 位")

        priority_text = self.PRIORITY_DISPLAY.get(task.priority.name, task.priority.name)
        lines.append(f"**优先级：** {priority_text}")
        lines.append("")

        lines.append("**时间信息：**")
        lines.append(f"• 创建时间：{self._format_timestamp(task.created_at)}")

        if task.started_at:
            lines.append(f"• 开始时间：{self._format_timestamp(task.started_at)}")
            lines.append(f"• 等待时长：{self._format_duration(task.wait_time)}")

        if task.completed_at:
            lines.append(f"• 完成时间：{self._format_timestamp(task.completed_at)}")
            lines.append(f"• 处理时长：{self._format_duration(task.process_time)}")
        elif task.started_at:
            lines.append(f"• 已处理：{self._format_duration(task.process_time)}")

        if task.error:
            lines.append("")
            lines.append(f"**错误信息：** {task.error}")

        return "\n".join(lines)

    def format_user_tasks(
        self,
        tasks: List["DownloadTask"],
        user_name: str,
    ) -> str:
        """格式化用户任务列表。"""
        if not tasks:
            return f"📋 **{user_name}** 没有进行中的任务"

        lines = [f"📋 **{user_name} 的任务** ({len(tasks)} 个)", ""]

        for i, task in enumerate(tasks, 1):
            status_emoji = self._get_status_emoji(task.status.value)
            status_text = self.STATUS_DISPLAY.get(task.status.value, task.status.value)

            task_desc = task.song_name or self._truncate_url(task.url, 30)
            lines.append(f"{i}. {status_emoji} **{task_desc}**")
            lines.append(f"   ID: {task.task_id} | {status_text} | {task.quality}")

            if task.status.value == "pending":
                lines.append(f"   等待时间：{self._format_duration(task.wait_time)}")
            elif task.status.value == "processing":
                lines.append(f"   处理时间：{self._format_duration(task.process_time)}")

            lines.append("")

        return "\n".join(lines)

    def format_enqueue_result(
        self,
        task: "DownloadTask",
        position: int,
        queue_size: int,
    ) -> str:
        """格式化任务入队结果。"""
        lines = [
            "✅ **已加入下载队列**",
            "",
            f"**任务 ID：** {task.task_id}",
            f"**队列位置：** 第 {position} 位",
            f"**当前队列：** {queue_size} 个任务",
        ]

        if task.song_name:
            lines.insert(2, f"**歌曲：** {task.song_name}")

        return "\n".join(lines)

    def format_cancel_result(
        self,
        task_id: str,
        success: bool,
        message: str,
    ) -> str:
        """格式化任务取消结果。"""
        if success:
            return f"✅ 任务 {task_id} 已取消"
        else:
            return f"❌ 无法取消任务 {task_id}：{message}"


    def _format_task_brief(
        self,
        task: "DownloadTask",
        processing: bool = False,
    ) -> str:
        """格式化任务简要信息。"""
        desc = task.song_name or self._truncate_url(task.url, 25)
        info_parts = [
            f"ID:{task.task_id}",
            f"用户:{task.user_name}",
            task.quality,
        ]

        if processing:
            info_parts.append(f"已处理:{self._format_duration(task.process_time)}")
        else:
            info_parts.append(f"等待:{self._format_duration(task.wait_time)}")

        return f"**{desc}** ({' | '.join(info_parts)})"

    def _format_duration(self, seconds: float) -> str:
        """格式化时长。"""
        if seconds < 60:
            return f"{seconds:.0f}秒"
        elif seconds < 3600:
            minutes = seconds / 60
            return f"{minutes:.1f}分钟"
        else:
            hours = seconds / 3600
            return f"{hours:.1f}小时"

    def _format_timestamp(self, timestamp: float) -> str:
        """格式化时间戳为本地时间字符串。"""
        import datetime
        dt = datetime.datetime.fromtimestamp(timestamp)
        return dt.strftime("%H:%M:%S")

    def _truncate_url(self, url: str, max_length: int = 40) -> str:
        """截断 URL 便于显示。"""
        if len(url) <= max_length:
            return url
        return url[:max_length - 3] + "..."

    def _get_status_emoji(self, status: str) -> str:
        """获取状态对应的表情。"""
        emoji_map = {
            "pending": "⏳",
            "processing": "🔄",
            "completed": "✅",
            "failed": "❌",
            "cancelled": "🚫",
            "timeout": "⏰",
        }
        return emoji_map.get(status, "❓")


class MinimalFormatter(QueueFormatter):
    """紧凑输出格式化器。"""

    def format_queue_status(
        self,
        tasks: List["DownloadTask"],
        current_task: Optional["DownloadTask"],
        stats: "QueueStats",
    ) -> str:
        """格式化紧凑队列状态。"""
        lines = []

        if current_task:
            lines.append(f"[处理中] {current_task.task_id}")

        lines.append(f"队列: {len(tasks)}/{stats.max_queue_size}")
        lines.append(f"完成/失败: {stats.completed_tasks}/{stats.failed_tasks}")

        return " | ".join(lines)

    def format_task_info(self, task: "DownloadTask", position: int = 0) -> str:
        """格式化紧凑任务信息。"""
        parts = [
            f"ID:{task.task_id}",
            task.status.value,
            task.quality,
        ]
        if position > 0:
            parts.append(f"位置:{position}")
        return " | ".join(parts)

    def format_user_tasks(
        self,
        tasks: List["DownloadTask"],
        user_name: str,
    ) -> str:
        """格式化紧凑用户任务列表。"""
        if not tasks:
            return f"{user_name}: 无任务"

        task_strs = [
            f"{t.task_id}({t.status.value})"
            for t in tasks
        ]
        return f"{user_name}: {', '.join(task_strs)}"


default_formatter = ChineseFormatter()
