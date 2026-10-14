#!/usr/bin/env bash
# LangHuan Suite 套件级安装器：一次安装全部技能 + 单一 env 入口管理 API key。
#
# 用法（Git Bash）：
#   scripts/install_plugin.sh install [--target <技能目录>] [--build]
#       解压 dist/ 下全部技能 zip 到技能目录（--build 先重新构建），
#       并在技能目录创建统一的 langhuan.env（已存在则不覆盖），
#       经硬链接分发给各 langhuan-*/.env（同卷硬链接 = 改一处全生效）。
#   scripts/install_plugin.sh sync-env [--target <技能目录>]
#       编辑中央 langhuan.env 后，重新分发给全部 langhuan-* 技能
#       （硬链接失效/跨卷时会自动退化为复制）。
#
# 环境变量回填优先级：各技能 .env > 进程环境；本脚本只做分发，不读 key 内容。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUITE_ENV="langhuan.env"
SKILL_PREFIX="langhuan-"

MEMBERS=(
  "lit-scout-harvester"
  "archive-plate-assembler"
  "anchor-verify-strategizer"
  "peer-collab-strategizer"
)

log()  { printf '[LangHuan] %s\n' "$*"; }
die()  { printf '[LangHuan] 错误: %s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 参数解析
TARGET=""
DO_BUILD=0
CMD="${1:-}"
shift || true
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --build)  DO_BUILD=1; shift ;;
    *) die "未知参数: $1（可用: install / sync-env；选项: --target <dir> --build）" ;;
  esac
done
TARGET="${TARGET:-$HOME/.agents/skills}"
[[ "$TARGET" == *:* && "$TARGET" != /* ]] && TARGET="/${TARGET}"  # C:/... -> /c/... 之外的兜底
TARGET="$(cygpath -u "$TARGET" 2>/dev/null || echo "$TARGET")"

# ---------------------------------------------------------------- 中央 env 模板
env_template() {
cat <<'EOF'
# ============================================================
# LangHuan Suite 统一密钥配置（单一事实来源）
# 本文件经硬链接分发给各 langhuan-*/.env —— 改这里即改全部。
# 若硬链接失效（跨卷/复制后），重跑: scripts/install_plugin.sh sync-env
# 不配置任何 key 也能运行：技能自动走 agent_bridge 零 key 模式。
# ============================================================

# ---- DeepSeek（LSH 标注/抽取 · AVS 锚点验证 · PCS 合作分析）----
DEEPSEEK_API_KEY=
DEEPSEEK_BASE_URL=https://api.deepseek.com/v1

# ---- MiniMax（APA 图版识别 · 角色 A 视觉）----
MINIMAX_API_KEY=
MINIMAX_BASE_URL=https://api.minimaxi.com/v1
MINIMAX_MODEL=

# ---- Qwen（APA 角色 B 视觉，需阿里云百炼开通）----
QWEN_API_KEY=
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-vl-max

# ---- APA 其它运行时参数（可调）----
MODEL_REQUEST_TIMEOUT_SECONDS=120
MODEL_MAX_RETRIES=3
PDF_RENDER_DPI=300

# ---- 在线源礼貌池（OpenAlex 免费额度建议填写）----
AGENTKIT_OPENALEX_MAILTO=

# ---- 宿主能力租约（DSH Capsule 接入后由宿主注入，勿手填）----
# AGENTKIT_HOST_LEASE=
EOF
}

# ---------------------------------------------------------------- 子命令
propagate_env() {
  local central="$TARGET/$SUITE_ENV"
  [[ -f "$central" ]] || die "未找到中央配置: $central（先运行 install）"
  local n=0
  for m in "${MEMBERS[@]}"; do
    local d="$TARGET/$SKILL_PREFIX$m"
    [[ -d "$d" ]] || continue
    if ln -f "$central" "$d/.env" 2>/dev/null; then
      : # 硬链接成功：编辑 central 即实时生效
    else
      cp -f "$central" "$d/.env"
    fi
    n=$((n+1))
  done
  log "已分发 $SUITE_ENV → $n 个技能（硬链接优先，失败自动复制）"
}

cmd_install() {
  [[ -d "$ROOT/dist" ]] || DO_BUILD=1
  if [[ "$DO_BUILD" == 1 ]]; then
    log "构建全部技能 zip ..."
    (cd "$ROOT" && python build_plugin.py build) || die "构建失败"
  fi
  mkdir -p "$TARGET"
  for m in "${MEMBERS[@]}"; do
    local zip="$ROOT/dist/${m}-skill.zip"
    [[ -f "$zip" ]] || die "缺少构建产物: $zip"
    unzip -qo "$zip" -d "$TARGET/$SKILL_PREFIX$m"
    log "已安装 $SKILL_PREFIX$m"
  done

  local central="$TARGET/$SUITE_ENV"
  if [[ -f "$central" ]]; then
    log "保留既有 $SUITE_ENV（不覆盖你的 key）"
  else
    env_template > "$central"
    log "已创建统一配置: $central（填入 API key 后生效）"
  fi
  propagate_env

  cat <<EOF

完成。技能目录: $TARGET
  - 4 个 langhuan-* 技能已就绪，重启宿主 Agent 后按各自 triggers 唤起。
  - key 只需填一处: $central
  - 查看分发状态: ls -li "$central" "$TARGET"/$SKILL_PREFIX*/.env | awk '{print \$1, \$NF}'
EOF
}

cmd_sync_env() { propagate_env; }

case "$CMD" in
  install)  cmd_install ;;
  sync-env) cmd_sync_env ;;
  *) die "用法: install_plugin.sh install [--target <dir>] [--build] | sync-env [--target <dir>]" ;;
esac
