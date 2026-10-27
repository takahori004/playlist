#!/usr/bin/env python3
"""
マネーフォワードの「口座」画面を全選択コピーしたテキストから、
カード引き落とし予定額に対して銀行口座の残高が足りるかをチェックする。

使い方:
  pbpaste | python3 mf_check.py            # クリップボードから直接
  python3 mf_check.py paste.txt            # 保存したテキストから
  python3 mf_check.py paste.txt --notify   # 不足時に macOS 通知も出す

終了コード: 0=不足なし / 1=不足あり / 2=入力・設定エラー
"""
import argparse
import configparser
import re
import subprocess
import sys
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DEFAULT_CONFIG = BASE_DIR / "mf_alert.ini"

# 「登録金融機関」以下に出てくるカテゴリ見出し
CATEGORIES = {
    "銀行", "証券", "投信", "カード", "暗号資産・FX・貴金属",
    "電子マネー・プリペイド", "携帯", "通販", "ポイント", "年金", "保険", "その他",
}
SITE_SUFFIX = "金融機関サービスサイトへ"
RE_AMOUNT = re.compile(r"^(-?[\d,]+)円$")
RE_DUE = re.compile(r"^引き落とし日:\((\d{4})/(\d{2})/(\d{2})\)$")
RE_FETCHED = re.compile(r"^取得日時\((\d{2})/(\d{2}) \d{2}:\d{2}\)$")


def norm(name):
    """全角/半角スペースの揺れを吸収するため空白をすべて除去"""
    return re.sub(r"\s+", "", name)


def pad(s, width):
    """全角文字を幅2として左寄せ"""
    w = sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in s)
    return s + " " * max(width - w, 0)


def to_yen(s):
    return int(s.replace(",", ""))


def guess_year(month, day, today):
    """取得日時は月日のみなので、未来日になるなら前年とみなす"""
    d = date(today.year, month, day)
    return d if d <= today else date(today.year - 1, month, day)


def parse(text, today):
    """口座画面のテキストを [{'category','name','balance','fetched','status','dues','undetermined'}] に変換"""
    lines = [l.strip() for l in text.splitlines()]
    lines = [l for l in lines if l]
    try:
        start = lines.index("登録金融機関") + 1
    except ValueError:
        raise SystemExit("ERROR: 「登録金融機関」が見つかりません。口座画面を全選択コピーしたテキストか確認してください。")

    accounts = []
    category = None
    acc = None
    pending_amount = None
    for line in lines[start:]:
        if line == "カンタン入力":  # 口座一覧の終わり
            break
        if line in CATEGORIES:
            category = line
            acc = None
            continue
        if line.endswith(SITE_SUFFIX):
            acc = {
                "category": category,
                "name": line[: -len(SITE_SUFFIX)].strip(),
                "balance": None,
                "fetched": None,
                "status": None,
                "dues": [],            # [(date, amount)]  amount は正の数
                "undetermined": False,
            }
            accounts.append(acc)
            pending_amount = None
            continue
        if acc is None:
            continue

        m = RE_FETCHED.match(line)
        if m:
            acc["fetched"] = guess_year(int(m[1]), int(m[2]), today)
            continue
        if line.startswith("ステータス:"):
            acc["status"] = line.split(":", 1)[1]
            continue
        if line == "引き落とし額未確定":
            acc["undetermined"] = True
            continue
        m = RE_AMOUNT.match(line)
        if m:
            amount = to_yen(m[1])
            if acc["balance"] is None:
                acc["balance"] = amount
            pending_amount = amount
            continue
        m = RE_DUE.match(line)
        if m and pending_amount is not None:
            acc["dues"].append((date(int(m[1]), int(m[2]), int(m[3])), abs(pending_amount)))
            pending_amount = None
            continue
    return accounts


def load_config(path):
    cp = configparser.ConfigParser(inline_comment_prefixes=("#", ";"))
    cp.optionxform = str  # キー（カード名）の大文字小文字を保持
    if not cp.read(path, encoding="utf-8"):
        raise SystemExit(f"ERROR: 設定ファイルが読めません: {path}\n"
                         f"       mf_alert.ini.example をコピーして作成してください。")
    card_to_bank = {norm(k): v.strip() for k, v in cp.items("card_to_bank")}
    s = cp["settings"] if cp.has_section("settings") else {}
    return {
        "card_to_bank": card_to_bank,
        "days_ahead": int(s.get("days_ahead", 31)),
        "margin": int(s.get("margin", 0)),
        "stale_days": int(s.get("stale_days", 3)),
    }


def check(accounts, cfg, today):
    banks = {norm(a["name"]): a for a in accounts if a["category"] == "銀行"}
    cards = [a for a in accounts if a["category"] == "カード"]

    alerts, warnings = [], []
    per_bank = defaultdict(list)  # bank_key -> [(date, amount, card)]

    for c in cards:
        if c["undetermined"]:
            warnings.append(f"[未確定] {c['name']}: 引き落とし額未確定（ステータス:{c['status']}）")
        upcoming = [(d, amt) for d, amt in c["dues"]
                    if 0 <= (d - today).days <= cfg["days_ahead"]]
        if not upcoming:
            continue
        bank_name = cfg["card_to_bank"].get(norm(c["name"]))
        if bank_name is None:
            total = sum(a for _, a in upcoming)
            warnings.append(f"[設定なし] {c['name']}: 引き落とし {total:,}円 の口座が mf_alert.ini に未登録")
            continue
        if norm(bank_name) not in banks:
            warnings.append(f"[口座なし] {c['name']} → 「{bank_name}」がテキスト中の銀行に見つかりません")
            continue
        for d, amt in upcoming:
            per_bank[norm(bank_name)].append((d, amt, c["name"]))

    report = []
    for key, dues in sorted(per_bank.items(), key=lambda kv: min(d for d, _, _ in kv[1])):
        bank = banks[key]
        balance = bank["balance"] or 0
        dues.sort()
        report.append(f"■ {bank['name']}  残高 {balance:,}円"
                      f"（取得 {bank['fetched'] or '不明'} / {bank['status']}）")

        # データの鮮度・状態チェック
        if bank["status"] != "正常":
            warnings.append(f"[取得エラー] {bank['name']}: ステータス {bank['status']}。残高が古い可能性")
        elif bank["fetched"] and (today - bank["fetched"]).days > cfg["stale_days"]:
            warnings.append(f"[古い残高] {bank['name']}: 取得日 {bank['fetched']}")

        running = 0
        short_at = None
        for d, amt, card in dues:
            running += amt
            note = ""
            if bank["fetched"] and bank["fetched"] >= d:
                note = "  ※残高取得が引き落とし日以降。引き落とし済みの可能性"
            mark = ""
            if short_at is None and running + cfg["margin"] > balance:
                short_at = d
                mark = "  ← ここで不足"
            report.append(f"    {d:%m/%d} {pad(card, 24)} {amt:>10,}円  累計 {running:>10,}円{mark}{note}")

        need = running + cfg["margin"] - balance
        if need > 0:
            days = (short_at - today).days
            when = "本日" if days == 0 else f"{days}日後"
            alerts.append(f"{bank['name']}: {need:,}円 不足（{short_at:%m/%d} {when}・"
                          f"引き落とし計 {running:,}円 / 残高 {balance:,}円）")
            report.append(f"    → 不足 {need:,}円")
        else:
            report.append(f"    → OK（余裕 {-need:,}円）")

    return alerts, warnings, report


def notify(alerts):
    msg = " / ".join(alerts).replace('"', "'")
    subprocess.run(["osascript", "-e",
                    f'display notification "{msg}" with title "引き落とし残高不足" sound name "Basso"'],
                   check=False)


def main():
    ap = argparse.ArgumentParser(description="MF口座画面テキストから引き落とし残高不足を検出")
    ap.add_argument("file", nargs="?", help="コピペしたテキスト（省略時は標準入力）")
    ap.add_argument("-c", "--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--today", help="基準日 YYYY-MM-DD（テスト用）")
    ap.add_argument("--notify", action="store_true", help="不足時に macOS 通知を出す")
    args = ap.parse_args()

    today = datetime.strptime(args.today, "%Y-%m-%d").date() if args.today else date.today()
    text = Path(args.file).read_text(encoding="utf-8") if args.file else sys.stdin.read()

    try:
        cfg = load_config(args.config)
        accounts = parse(text, today)
    except SystemExit as e:
        print(e, file=sys.stderr)
        return 2

    alerts, warnings, report = check(accounts, cfg, today)

    print(f"=== 引き落とし残高チェック {today}（{cfg['days_ahead']}日先まで / 余裕額 {cfg['margin']:,}円）===")
    print("\n".join(report) if report else "対象期間の引き落とし予定なし")
    if warnings:
        print("\n--- 注意 ---")
        print("\n".join(warnings))
    if alerts:
        print("\n!!! 残高不足 !!!")
        print("\n".join(alerts))
        if args.notify:
            notify(alerts)
        return 1
    print("\n残高不足なし")
    return 0


if __name__ == "__main__":
    sys.exit(main())
