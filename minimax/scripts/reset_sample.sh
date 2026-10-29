#!/usr/bin/env bash
# 在"修改前 / 修改后"两个状态之间切换示例工程，供评审核对红→绿证据。
#
#   ./reset_sample.sh before   # 还原为未实现 ORDER-FILTER-001 的状态（目标测试应当变红）
#   ./reset_sample.sh after    # 恢复为已实现状态（目标测试应当全绿）
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
TARGET="$ROOT/examples/sdd-context-manager/sample-app/app/order.py"

case "${1:-after}" in
  before) cp "$ROOT/minimax/snapshots/order_before.py" "$TARGET"
          echo "order.py -> BEFORE（运行目标测试预期 FAILED：AttributeError: no attribute 'expensive_items'）" ;;
  after)  cp "$ROOT/minimax/snapshots/order_after.py" "$TARGET"
          echo "order.py -> AFTER（运行目标测试预期 OK）" ;;
  *) echo "usage: $0 before|after" >&2; exit 2 ;;
esac
