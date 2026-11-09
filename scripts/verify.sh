#!/usr/bin/env bash
# 一键质量校验：ruff check、ruff format --check、mypy、pytest --cov 四步串联。
# 刻意不使用 set -e：任何一步失败都记录下来，最后统一汇总后再以非零退出码结束。
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${repo_root}" || exit 1

if [ -x ./.venv/bin/python ]; then
    py="./.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
    py="python3"
elif command -v python >/dev/null 2>&1; then
    py="python"
else
    echo "未找到可用的 Python 解释器（.venv/bin/python、python3 或 python）。" >&2
    exit 1
fi

echo "解释器: ${py}"

failed=()

run_step() {
    local label="$1"
    shift
    echo
    echo "=== ${label} ==="
    "${py}" "$@"
    local code=$?
    if [ "${code}" -eq 0 ]; then
        echo "[通过] ${label}"
    else
        echo "[失败] ${label} (exit ${code})" >&2
        failed+=("${label}")
    fi
}

run_step "[1/4] ruff check" -m ruff check .
run_step "[2/4] ruff format --check" -m ruff format --check .
run_step "[3/4] mypy src/story_tr" -m mypy src/story_tr
run_step "[4/4] pytest --cov" -m pytest -q --cov=story_tr --cov-report=term-missing

echo
if [ "${#failed[@]}" -eq 0 ]; then
    echo "全部检查通过。"
    exit 0
fi

echo "校验失败，共 ${#failed[@]} 项：" >&2
for step in "${failed[@]}"; do
    echo "  - ${step}" >&2
done

exit 1
