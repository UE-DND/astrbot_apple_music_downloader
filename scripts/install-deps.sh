#!/usr/bin/env bash
set -euo pipefail

PREFIX=""
if [ "${EUID:-$(id -u)}" -ne 0 ]; then
  PREFIX="sudo "
fi

need_cmd() {
  command -v "$1" >/dev/null 2>&1
}

install_bento4_from_source() {
  if need_cmd mp4edit && need_cmd mp4extract && need_cmd mp4decrypt; then
    return 0
  fi

  echo "正在从源码安装 Bento4..."
  cd /tmp/ || exit 1
  rm -rf Bento4
  git clone --depth=1 https://github.com/axiomatic-systems/Bento4.git
  mkdir -p Bento4/cmakebuild
  cd Bento4/cmakebuild || exit 1
  cmake -DCMAKE_BUILD_TYPE=Release ..
  make -j"$(nproc)"
  $PREFIX make install
  cd /tmp/ || exit 1
  rm -rf Bento4
}

if need_cmd apt-get; then
  echo "检测到 apt-get（Debian/Ubuntu）"
  $PREFIX apt-get update
  if ! need_cmd gcc || ! need_cmd make; then
    $PREFIX apt-get install -y build-essential
  fi
  if ! need_cmd pkg-config; then
    $PREFIX apt-get install -y pkg-config
  fi
  if ! need_cmd git; then
    $PREFIX apt-get install -y git
  fi
  if ! dpkg -s zlib1g-dev >/dev/null 2>&1; then
    $PREFIX apt-get install -y zlib1g-dev
  fi
  if ! need_cmd cmake; then
    $PREFIX apt-get install -y cmake
  fi

  if ! need_cmd ffmpeg; then
    $PREFIX apt-get install -y ffmpeg
  fi

  if ! need_cmd MP4Box; then
    $PREFIX apt-get install -y gpac
  fi

  if ! need_cmd mp4edit; then
    if apt-cache show bento4 >/dev/null 2>&1; then
      $PREFIX apt-get install -y bento4
    else
      install_bento4_from_source
    fi
  fi

elif need_cmd pacman; then
  echo "检测到 pacman（Arch/Manjaro）"
  if ! need_cmd gcc || ! need_cmd make; then
    $PREFIX pacman -Sy --needed --noconfirm base-devel
  fi
  if ! need_cmd cmake; then
    $PREFIX pacman -Sy --needed --noconfirm cmake
  fi
  if ! need_cmd git; then
    $PREFIX pacman -Sy --needed --noconfirm git
  fi
  if ! pacman -Q zlib >/dev/null 2>&1; then
    $PREFIX pacman -Sy --needed --noconfirm zlib
  fi
  if ! need_cmd ffmpeg; then
    $PREFIX pacman -Sy --needed --noconfirm ffmpeg
  fi
  if ! need_cmd MP4Box; then
    $PREFIX pacman -Sy --needed --noconfirm gpac
  fi

  if ! need_cmd mp4edit; then
    if need_cmd yay; then
      yay -Sy --needed --noconfirm bento4
    else
      install_bento4_from_source
    fi
  fi

else
  echo "未识别的发行版，请手动安装以下依赖："
  echo "  - ffmpeg"
  echo "  - gpac（gpac, MP4Box）"
  echo "  - Bento4（mp4extract, mp4edit, mp4decrypt）"
  exit 1
fi

echo "完成。"
