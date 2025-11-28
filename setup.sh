#!/bin/bash
#
# Apple Music Downloader - Docker 环境初始化脚本
#
#
# 使用方法:
#   chmod +x setup.sh
#   ./setup.sh [选项]
#
# 选项:
#   --force     强制重新构建（即使镜像已存在）
#   --no-proxy  不使用 GitHub 代理
#   --help      显示帮助信息
#

set -e

# ==================== 配置 ====================
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ASSETS_DIR="${SCRIPT_DIR}/assets"
MANAGER_DIR="${ASSETS_DIR}/wrapper-manager"
SOURCE_DIR="${MANAGER_DIR}/wrapper-manager-src"
DATA_DIR="${MANAGER_DIR}/data"

GITHUB_REPO="https://github.com/WorldObservationLog/wrapper-manager"
GITHUB_PROXY="https://gh-proxy.com"
IMAGE_NAME="apple-music-wrapper-manager"
CONTAINER_NAME="apple-music-wrapper-manager"
GRPC_PORT=18923

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# ==================== 函数 ====================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[OK]${NC} $1"
}

log_warn() {
    echo -e "${YELLOW}[WARN]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

show_help() {
    echo "Apple Music Downloader - Docker 环境初始化脚本"
    echo ""
    echo "用法: ./setup.sh [选项]"
    echo ""
    echo "选项:"
    echo "  --force      强制重新构建（即使镜像已存在）"
    echo "  --no-proxy   不使用 GitHub 代理（海外用户）"
    echo "  --start      构建完成后自动启动容器"
    echo "  --help       显示帮助信息"
    echo ""
    echo "示例:"
    echo "  ./setup.sh                 # 首次安装"
    echo "  ./setup.sh --force         # 强制重新构建"
    echo "  ./setup.sh --start         # 构建并启动"
    echo "  ./setup.sh --no-proxy      # 不使用代理（海外）"
}

check_docker() {
    log_info "检查 Docker 环境..."

    if ! command -v docker &> /dev/null; then
        log_error "Docker 未安装，请先安装 Docker"
        exit 1
    fi

    if ! docker info &> /dev/null; then
        log_error "Docker 服务未运行，请启动 Docker"
        exit 1
    fi

    log_success "Docker 环境正常"
}

check_git() {
    log_info "检查 Git..."

    if ! command -v git &> /dev/null; then
        log_error "Git 未安装，请先安装 Git"
        exit 1
    fi

    log_success "Git 已安装"
}

create_directories() {
    log_info "创建目录结构..."

    mkdir -p "${MANAGER_DIR}"
    mkdir -p "${DATA_DIR}"

    log_success "目录创建完成: ${MANAGER_DIR}"
}

clone_source() {
    local use_proxy=$1
    local clone_url="${GITHUB_REPO}"

    if [ "$use_proxy" = true ]; then
        clone_url="${GITHUB_PROXY}/${GITHUB_REPO}"
        log_info "使用代理克隆源码: ${clone_url}"
    else
        log_info "克隆源码: ${clone_url}"
    fi

    if [ -d "${SOURCE_DIR}" ]; then
        log_info "源码目录已存在，尝试更新..."
        cd "${SOURCE_DIR}"

        if git fetch --all 2>&1; then
            git reset --hard origin/main 2>&1
            log_success "源码更新完成"
        else
            log_warn "更新失败，删除并重新克隆..."
            cd "${MANAGER_DIR}"
            rm -rf "${SOURCE_DIR}"

            if git clone "${clone_url}" "${SOURCE_DIR}" 2>&1; then
                log_success "源码克隆完成"
            else
                log_error "克隆失败，请检查网络连接"
                exit 1
            fi
        fi
    else
        log_info "克隆 wrapper-manager 源码..."

        if git clone "${clone_url}" "${SOURCE_DIR}" 2>&1; then
            log_success "源码克隆完成"
        else
            log_error "克隆失败"
            if [ "$use_proxy" = true ]; then
                log_info "尝试不使用代理..."
                if git clone "${GITHUB_REPO}" "${SOURCE_DIR}" 2>&1; then
                    log_success "源码克隆完成（无代理）"
                else
                    log_error "克隆失败，请检查网络连接"
                    exit 1
                fi
            else
                exit 1
            fi
        fi
    fi

    # 获取当前 commit
    cd "${SOURCE_DIR}"
    local commit_hash=$(git rev-parse --short HEAD)
    log_info "当前版本: ${commit_hash}"
}

create_dockerfile() {
    local use_proxy=$1
    log_info "生成 Dockerfile..."

    if [ "$use_proxy" = true ]; then
        log_info "使用国内镜像加速构建..."
        cat > "${MANAGER_DIR}/Dockerfile" << 'EOF'
FROM golang:1.23 AS builder

WORKDIR /app

# Use Go module proxy for faster downloads in China
ENV GOPROXY=https://goproxy.cn,direct

# Copy source code
COPY wrapper-manager-src /app

# Build
RUN go mod tidy
RUN GOOS=linux go build -o wrapper-manager

# Runtime image
FROM debian:bookworm-slim

WORKDIR /root/

# Use Aliyun mirror for faster apt downloads in China
RUN sed -i 's/deb.debian.org/mirrors.aliyun.com/g' /etc/apt/sources.list.d/debian.sources

# Copy built binary from builder
COPY --from=builder /app/wrapper-manager .

# Install ca-certificates for HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

# Make executable
RUN chmod +x ./wrapper-manager

# Expose gRPC port
EXPOSE 8080

# Entry point with configurable args
ENTRYPOINT ["./wrapper-manager"]
CMD ["--host", "0.0.0.0", "--port", "8080"]
EOF
    else
        log_info "使用默认源构建..."
        cat > "${MANAGER_DIR}/Dockerfile" << 'EOF'
FROM golang:1.23 AS builder

WORKDIR /app

# Copy source code
COPY wrapper-manager-src /app

# Build
RUN go mod tidy
RUN GOOS=linux go build -o wrapper-manager

# Runtime image
FROM debian:bookworm-slim

WORKDIR /root/

# Copy built binary from builder
COPY --from=builder /app/wrapper-manager .

# Install ca-certificates for HTTPS
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates && rm -rf /var/lib/apt/lists/*

# Make executable
RUN chmod +x ./wrapper-manager

# Expose gRPC port
EXPOSE 8080

# Entry point with configurable args
ENTRYPOINT ["./wrapper-manager"]
CMD ["--host", "0.0.0.0", "--port", "8080"]
EOF
    fi

    log_success "Dockerfile 生成完成"
}

build_image() {
    local force=$1

    # 检查镜像是否存在
    if docker images -q "${IMAGE_NAME}" 2>/dev/null | grep -q .; then
        if [ "$force" = true ]; then
            log_warn "强制重新构建镜像..."
        else
            log_success "镜像已存在: ${IMAGE_NAME}"
            return 0
        fi
    fi

    log_info "构建 Docker 镜像（首次构建可能需要 5-10 分钟）..."
    echo ""

    cd "${MANAGER_DIR}"

    if docker build -t "${IMAGE_NAME}:latest" . 2>&1; then
        echo ""
        log_success "镜像构建完成: ${IMAGE_NAME}:latest"
    else
        echo ""
        log_error "镜像构建失败"
        exit 1
    fi

    # 保存版本信息
    cd "${SOURCE_DIR}"
    local commit_hash=$(git rev-parse --short HEAD)
    local install_date=$(date -Iseconds)

    cat > "${MANAGER_DIR}/version.json" << EOF
{
  "commit_hash": "${commit_hash}",
  "installed_at": "${install_date}",
  "branch": "main"
}
EOF

    log_info "版本信息已保存"
}

start_container() {
    log_info "启动容器..."

    # 停止并删除已存在的容器
    if docker ps -a --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log_info "停止并删除旧容器..."
        docker stop "${CONTAINER_NAME}" 2>/dev/null || true
        docker rm "${CONTAINER_NAME}" 2>/dev/null || true
    fi

    # 启动新容器
    docker run -d \
        --name "${CONTAINER_NAME}" \
        -p "${GRPC_PORT}:8080" \
        -v "${DATA_DIR}:/root/data" \
        "${IMAGE_NAME}:latest" \
        --host 0.0.0.0 --port 8080 --mirror

    log_success "容器已启动: ${CONTAINER_NAME}"
    log_info "gRPC 服务地址: 127.0.0.1:${GRPC_PORT}"

    # 等待服务就绪
    log_info "等待服务初始化..."
    sleep 5

    # 检查容器状态
    if docker ps --format '{{.Names}}' | grep -q "^${CONTAINER_NAME}$"; then
        log_success "服务已就绪"
        echo ""
        log_info "查看日志: docker logs -f ${CONTAINER_NAME}"
    else
        log_error "容器启动失败，查看日志:"
        docker logs "${CONTAINER_NAME}" 2>&1 | tail -20
        exit 1
    fi
}

show_summary() {
    echo ""
    echo "=========================================="
    echo -e "${GREEN}初始化完成！${NC}"
    echo "=========================================="
    echo ""
    echo "镜像名称: ${IMAGE_NAME}:latest"
    echo "数据目录: ${DATA_DIR}"
    echo ""
    echo "下一步操作:"
    echo "  1. 启动容器: docker run -d --name ${CONTAINER_NAME} -p ${GRPC_PORT}:8080 -v ${DATA_DIR}:/root/data ${IMAGE_NAME}:latest --host 0.0.0.0 --port 8080 --mirror"
    echo "  2. 在插件配置中设置 wrapper_mode 为 'docker'"
    echo "  3. 设置 wrapper_url 为 '127.0.0.1:${GRPC_PORT}'"
    echo "  4. 重启 AstrBot"
    echo ""
    echo "常用命令:"
    echo "  查看日志: docker logs -f ${CONTAINER_NAME}"
    echo "  停止服务: docker stop ${CONTAINER_NAME}"
    echo "  重启服务: docker restart ${CONTAINER_NAME}"
    echo ""
}

# ==================== 主流程 ====================

main() {
    local force=false
    local use_proxy=true
    local auto_start=false

    # 解析参数
    while [[ $# -gt 0 ]]; do
        case $1 in
            --force)
                force=true
                shift
                ;;
            --no-proxy)
                use_proxy=false
                shift
                ;;
            --start)
                auto_start=true
                shift
                ;;
            --help|-h)
                show_help
                exit 0
                ;;
            *)
                log_error "未知参数: $1"
                show_help
                exit 1
                ;;
        esac
    done

    echo ""
    echo "=========================================="
    echo "Apple Music Downloader - Docker 初始化"
    echo "=========================================="
    echo ""

    # 执行初始化步骤
    check_docker
    check_git
    create_directories
    clone_source "$use_proxy"
    create_dockerfile "$use_proxy"
    build_image "$force"

    if [ "$auto_start" = true ]; then
        start_container
    else
        show_summary
    fi
}

main "$@"
