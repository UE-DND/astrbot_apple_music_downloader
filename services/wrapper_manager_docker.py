"""
Wrapper Manager Docker Auto-Deployment Module

Handles automatic clone, build, and version management for wrapper-manager Docker images.
Uses git clone to fetch source code and compile within Docker.
"""

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple

from astrbot.api import logger


@dataclass
class VersionInfo:
    """Local version information."""
    commit_hash: str
    installed_at: str
    branch: str = "main"


class WrapperManagerDockerManager:
    """
    Manages wrapper-manager Docker deployment with automatic source clone and build.

    Features:
    - Auto-clone wrapper-manager source from GitHub
    - Build within Docker container
    - Version tracking via git commit hash
    - gRPC server for multi-instance management and runtime login
    """

    GITHUB_REPO = "https://github.com/WorldObservationLog/wrapper-manager"
    GITHUB_PROXY = "https://gh-proxy.com"

    VERSION_FILE = "version.json"
    SOURCE_DIR = "wrapper-manager-src"

    def __init__(
        self,
        assets_dir: Path,
        use_proxy: bool = True,
        proxy_url: str = None,
    ):
        """
        Initialize the wrapper-manager Docker manager.

        Args:
            assets_dir: Directory to store wrapper-manager assets
            use_proxy: Whether to use GitHub proxy for clone
            proxy_url: Custom proxy URL (default: gh-proxy.com)
        """
        self.assets_dir = assets_dir
        self.manager_dir = assets_dir / "wrapper-manager"
        self.source_dir = self.manager_dir / self.SOURCE_DIR
        self.use_proxy = use_proxy
        self.proxy_url = proxy_url or self.GITHUB_PROXY

        self._ensure_directories()

    def _ensure_directories(self) -> None:
        """Create necessary directories."""
        self.manager_dir.mkdir(parents=True, exist_ok=True)
        (self.manager_dir / "data").mkdir(parents=True, exist_ok=True)

    def _get_clone_url(self) -> str:
        """Get clone URL with optional proxy."""
        if self.use_proxy:
            return f"{self.proxy_url}/{self.GITHUB_REPO}"
        return self.GITHUB_REPO

    def get_local_version(self) -> Optional[VersionInfo]:
        """Get locally installed wrapper-manager version."""
        version_file = self.manager_dir / self.VERSION_FILE

        if not version_file.exists():
            return None

        try:
            with open(version_file, "r") as f:
                data = json.load(f)
                return VersionInfo(
                    commit_hash=data.get("commit_hash", ""),
                    installed_at=data.get("installed_at", ""),
                    branch=data.get("branch", "main"),
                )
        except Exception as e:
            logger.warning(f"Failed to read version file: {e}")
            return None

    def _save_version_info(self, commit_hash: str, branch: str = "main") -> None:
        """Save version information to file."""
        version_data = {
            "commit_hash": commit_hash,
            "installed_at": datetime.now().isoformat(),
            "branch": branch,
        }

        version_file = self.manager_dir / self.VERSION_FILE
        with open(version_file, "w") as f:
            json.dump(version_data, f, indent=2)

    async def _run_command(
        self,
        cmd: list,
        cwd: Path = None,
        timeout: int = 600
    ) -> Tuple[int, str, str]:
        """Run a shell command."""
        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=timeout
            )

            return (
                process.returncode,
                stdout.decode("utf-8", errors="replace"),
                stderr.decode("utf-8", errors="replace")
            )

        except asyncio.TimeoutError:
            if process:
                process.kill()
            return -1, "", "命令执行超时"
        except Exception as e:
            return -1, "", str(e)

    async def clone_or_update_source(self) -> Tuple[bool, str]:
        """
        Clone or update wrapper-manager source code.

        Returns:
            Tuple of (success, message)
        """
        clone_url = self._get_clone_url()

        if self.source_dir.exists():
            # Check if it's a valid git repo
            if not (self.source_dir / ".git").exists():
                # Directory exists but not a git repo - remove and re-clone
                logger.info("源码目录存在但不是有效的 git 仓库，删除后重新克隆...")
                import shutil
                try:
                    shutil.rmtree(self.source_dir)
                    logger.info("已删除无效目录，准备重新克隆...")
                except Exception as e:
                    return False, f"无法删除无效目录: {e}"
                # Fall through to clone below
            else:
                # Update existing repo
                logger.info("更新 wrapper-manager 源码...")

                # Fetch and reset to latest
                code, _, stderr = await self._run_command(
                    ["git", "fetch", "--all"],
                    cwd=self.source_dir,
                    timeout=120
                )
                if code != 0:
                    return False, f"git fetch 失败: {stderr}"

                code, _, stderr = await self._run_command(
                    ["git", "reset", "--hard", "origin/main"],
                    cwd=self.source_dir,
                    timeout=30
                )
                if code != 0:
                    return False, f"git reset 失败: {stderr}"

                return True, "源码更新完成"

        # Clone new repo (directory doesn't exist or was removed)
        logger.info("克隆 wrapper-manager 源码...")

        code, _, stderr = await self._run_command(
            ["git", "clone", clone_url, str(self.source_dir)],
            cwd=self.manager_dir,
            timeout=300
        )

        if code != 0:
            return False, f"git clone 失败: {stderr}"

        return True, "源码克隆完成"

    async def get_current_commit(self) -> Optional[str]:
        """Get current commit hash of source code."""
        if not self.source_dir.exists():
            return None

        code, stdout, _ = await self._run_command(
            ["git", "rev-parse", "HEAD"],
            cwd=self.source_dir,
            timeout=10
        )

        if code == 0:
            return stdout.strip()[:12]
        return None

    async def check_for_updates(self) -> Tuple[bool, Optional[str]]:
        """
        Check if updates are available.

        Returns:
            Tuple of (update_available, latest_commit_hash)
        """
        local_version = self.get_local_version()

        if not self.source_dir.exists():
            return True, None

        # Fetch latest
        code, _, _ = await self._run_command(
            ["git", "fetch", "origin"],
            cwd=self.source_dir,
            timeout=60
        )

        if code != 0:
            return False, None

        # Get remote HEAD
        code, stdout, _ = await self._run_command(
            ["git", "rev-parse", "origin/main"],
            cwd=self.source_dir,
            timeout=10
        )

        if code != 0:
            return False, None

        remote_commit = stdout.strip()[:12]

        if not local_version:
            return True, remote_commit

        if local_version.commit_hash != remote_commit:
            return True, remote_commit

        return False, remote_commit

    def is_source_cloned(self) -> bool:
        """Check if source code is cloned."""
        return self.source_dir.exists() and (self.source_dir / ".git").exists()

    def create_dockerfile(self, use_go_proxy: bool = True) -> Tuple[bool, str]:
        """
        Create Dockerfile for building wrapper-manager from source.

        Args:
            use_go_proxy: Whether to use Chinese Go proxy (goproxy.cn) for faster downloads

        Returns:
            Tuple of (success, message)
        """
        # Build Go proxy configuration
        if use_go_proxy:
            go_proxy_line = "RUN go env -w GO111MODULE=on && go env -w GOPROXY=https://goproxy.cn,direct"
        else:
            go_proxy_line = "# Go proxy disabled"

        # Based on official Dockerfile but with source compilation
        dockerfile_content = f'''FROM golang:1.23 AS builder

WORKDIR /app

# Copy source code
COPY wrapper-manager-src /app

# Build (enable Go module and optionally use proxy for Chinese users)
{go_proxy_line}
RUN go mod tidy
RUN GOOS=linux go build -o wrapper-manager

# Runtime image
FROM ubuntu:latest

WORKDIR /root/

# Copy built binary from builder
COPY --from=builder /app/wrapper-manager .

# Install ca-certificates for HTTPS
RUN apt-get update && apt-get install -y ca-certificates && rm -rf /var/lib/apt/lists/*

# Make executable
RUN chmod +x ./wrapper-manager

# Expose gRPC port
EXPOSE 8080

# Entry point with configurable args
ENTRYPOINT ["./wrapper-manager"]
CMD ["--host", "0.0.0.0", "--port", "8080"]
'''

        try:
            dockerfile_path = self.manager_dir / "Dockerfile"
            with open(dockerfile_path, "w") as f:
                f.write(dockerfile_content)

            return True, "Dockerfile 创建成功"

        except Exception as e:
            return False, f"创建 Dockerfile 失败: {str(e)}"

    async def build_image(
        self,
        image_name: str = "apple-music-wrapper-manager",
        tag: str = "latest",
        progress_callback: callable = None
    ) -> Tuple[bool, str]:
        """
        Build Docker image from source code.

        Args:
            image_name: Name for the Docker image (may include tag like "name:tag")
            tag: Image tag (ignored if image_name already contains a tag)
            progress_callback: Optional callback for progress updates

        Returns:
            Tuple of (success, message)
        """
        # Check if source is cloned
        if not self.is_source_cloned():
            return False, "源码未克隆，请先执行 clone_or_update_source"

        # Create Dockerfile with Go proxy setting based on use_proxy config
        success, msg = self.create_dockerfile(use_go_proxy=self.use_proxy)
        if not success:
            return False, msg

        # Build image - handle case where image_name already includes tag
        if ":" in image_name:
            image_tag = image_name
        else:
            image_tag = f"{image_name}:{tag}"

        logger.info(f"开始构建 Docker 镜像 {image_tag}（首次构建可能需要 5-10 分钟）...")

        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "build",
                "--tag", image_tag,
                str(self.manager_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=1200  # 20 minutes timeout for compilation
            )

            if process.returncode != 0:
                error_msg = stderr.decode("utf-8", errors="replace")
                logger.error(f"镜像构建失败: {error_msg}")
                return False, f"镜像构建失败: {error_msg[-500:]}"

            # Save version info
            commit_hash = await self.get_current_commit()
            if commit_hash:
                self._save_version_info(commit_hash)

            return True, f"镜像 {image_tag} 构建成功"

        except asyncio.TimeoutError:
            return False, "镜像构建超时（超过20分钟）"
        except Exception as e:
            return False, f"构建失败: {str(e)}"

    async def auto_setup(
        self,
        image_name: str = "apple-music-wrapper-manager",
        force_update: bool = False,
        progress_callback: callable = None
    ) -> Tuple[bool, str]:
        """
        Automatically setup wrapper-manager: clone source and build image.

        Args:
            image_name: Name for the Docker image
            force_update: Force rebuild even if up-to-date
            progress_callback: Optional callback for progress updates

        Returns:
            Tuple of (success, message)
        """
        messages = []

        # Clone or update source
        if progress_callback:
            await progress_callback("正在获取 wrapper-manager 源码...")

        success, msg = await self.clone_or_update_source()
        if not success:
            return False, msg
        messages.append(msg)

        # Check if rebuild is needed
        image_exists = await self._check_image_exists(image_name)
        local_version = self.get_local_version()
        current_commit = await self.get_current_commit()

        need_rebuild = (
            force_update
            or not image_exists
            or not local_version
            or local_version.commit_hash != current_commit
        )

        if need_rebuild:
            if progress_callback:
                await progress_callback("正在构建 Docker 镜像...")

            success, msg = await self.build_image(image_name, progress_callback=progress_callback)
            if not success:
                return False, msg
            messages.append(msg)
        else:
            messages.append(f"镜像已是最新版本 (commit: {current_commit})")

        return True, "\n".join(messages)

    async def _check_image_exists(self, image_name: str) -> bool:
        """Check if Docker image exists."""
        try:
            process = await asyncio.create_subprocess_exec(
                "docker", "images", "-q", image_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, _ = await process.communicate()
            return bool(stdout.strip())

        except Exception:
            return False

    def get_status(self) -> dict:
        """Get wrapper-manager installation status."""
        local_version = self.get_local_version()

        return {
            "source_cloned": self.is_source_cloned(),
            "commit_hash": local_version.commit_hash if local_version else None,
            "installed_at": local_version.installed_at if local_version else None,
            "manager_dir": str(self.manager_dir),
        }
