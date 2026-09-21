#!/bin/bash
set -eu

BASE_DIR="/Users/takashi/batch/sppl"
cd "$BASE_DIR"

LOG_DIR="$BASE_DIR/logs"
mkdir -p "$LOG_DIR"

# 年月単位のログファイル（例: cocofuro_202609.log）
MONTH_STR=$(date "+%Y%m")
LOG_FILE="$LOG_DIR/cocofuro_${MONTH_STR}.log"

{
  echo ""
  echo "======================================================="
  echo "=== [START] Daily Playlist Update: $(date "+%Y-%m-%d %H:%M:%S") ==="
  echo "======================================================="
} | tee -a "$LOG_FILE"

# Spotify APIキー等の環境変数を読み込み（.env.sppl は git 管理外）
if [ -f "$BASE_DIR/.env.sppl" ]; then
  set -a
  source "$BASE_DIR/.env.sppl"
  set +a
fi

# 仮想環境の有効化
source "$BASE_DIR/.venv/bin/activate"

# 実行結果を画面に表示しつつ、ログファイルへ追記
python3 "$BASE_DIR/update_all_daily.py" 2>&1 | tee -a "$LOG_FILE"

{
  echo "=== [END] Daily Playlist Update: $(date "+%Y-%m-%d %H:%M:%S") ==="
  echo "======================================================="
} | tee -a "$LOG_FILE"