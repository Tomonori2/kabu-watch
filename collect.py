# -*- coding: utf-8 -*-
"""
株ウォッチ（公開版）のデータ集め係。

GitHub Actions（GitHub が毎晩うごかしてくれる無料のロボット）がこれを実行し、
東証のほぼ全銘柄ぶんの「終値・前日比・信用買い残・機関の空売り」を
data/latest.json にまとめて置く。アプリ（index.html）はそのファイルを読むだけ。

  python collect.py                 … ふつうに集める
  python collect.py --seed ファイル … 以前の空売り記録（PC版 karauri_rireki.json）を引き継ぐ
  python collect.py --no-price      … 株価は取らない（試験用）

元にしたのは PC 版「気になる株ウォッチャー」(stock-analysis/get_shinyo.py) の読み方。
"""
import os
import re
import sys
import json
import time
import argparse
from datetime import datetime, timezone, timedelta

import requests

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, "data")
WORK = os.path.join(BASE, "_work")          # 一時ファイル置き場（公開しない）
JST = timezone(timedelta(hours=9))

JPX = "https://www.jpx.co.jp"
残高ページ = JPX + "/markets/statistics-equities/margin/01.html"
空売り残高ページ = JPX + "/markets/public/short-selling/index.html"

# 東証のサイトは「ブラウザっぽくない」アクセスを断ることがあるので、ふつうのブラウザを名乗る
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Accept-Language": "ja",
}

if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass


def log(msg):
    print(msg, flush=True)


def 取得(url, timeout=60):
    r = requests.get(url, headers=HEADERS, timeout=timeout)
    r.raise_for_status()
    return r


def コードを4桁に(コード5):
    """東証の5桁コード（72030, 285A0）を 4桁（7203, 285A）にそろえる"""
    コード5 = str(コード5).strip()
    if len(コード5) == 5 and コード5.endswith("0"):
        return コード5[:4]
    return コード5


def 数字にする(tok):
    tok = str(tok).replace(",", "").replace("△", "").strip()
    if tok in ("", "-", "－"):
        return None
    try:
        return int(tok)
    except ValueError:
        try:
            return int(float(tok))
        except ValueError:
            return None


def 数字の列を読む(文字列):
    """'100,800 ▲ 700 …' → [100800, -700, …]（東証のPDFでは ▲ がマイナス）"""
    結果, 負 = [], False
    for tok in 文字列.split():
        if tok == "▲":
            負 = True
            continue
        if tok.startswith("▲"):
            負, tok = True, tok[1:]
        v = 数字にする(tok)
        if v is None:
            負 = False
            continue
        結果.append(-v if 負 else v)
        負 = False
    return 結果


def 読む(path, 既定):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return 既定


def 書く(path, obj):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, separators=(",", ":"))
    os.replace(tmp, path)


# ---------------------------------------------------------------
# ① 信用残（東証 PDF・全銘柄・毎営業日）
# ---------------------------------------------------------------

def 信用残を取る():
    """最新の『銘柄別信用取引残高』PDF を読んで {基準日, 公表日, 銘柄:{コード:{...}}} を返す"""
    import pdfplumber

    html = 取得(残高ページ, timeout=30).text
    候補 = sorted({日: JPX + p for p, 日 in re.findall(r'href="([^"]*?/(\d{8})_mtall\.pdf)"', html)}.items(),
                reverse=True)
    if not 候補:
        raise RuntimeError("信用残のPDFが見つかりません（東証がページの形を変えた可能性）")
    日付, url = 候補[0]
    log(f"🏦 信用残 {日付} をダウンロード中…")
    os.makedirs(WORK, exist_ok=True)
    pdf_path = os.path.join(WORK, f"mtall{日付}.pdf")
    with open(pdf_path, "wb") as f:
        f.write(取得(url, timeout=180).content)

    銘柄, 基準日, 公表日 = {}, None, None
    行の形 = re.compile(r"^(\S*)\s*(.+?)\s+([0-9A-Z]{5})\s+(JP[0-9A-Z]{10})\s*(.*)$")
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            for line in (page.extract_text() or "").split("\n"):
                if 基準日 is None:
                    m = re.search(r"(\d{4})/(\d{1,2})/(\d{1,2})\s*申込み現在.*?(\d{4})/(\d{1,2})/(\d{1,2})\s*$", line)
                    if m:
                        基準日 = f"{m.group(1)}/{int(m.group(2)):02d}/{int(m.group(3)):02d}"
                        公表日 = f"{m.group(4)}/{int(m.group(5)):02d}/{int(m.group(6)):02d}"
                m = 行の形.match(line)
                if not m:
                    continue
                残り = m.group(5)
                コード = コードを4桁に(m.group(3))
                if "金額" in 残り or "Val." in 残り or "Yen" in 残り:
                    continue
                if コード in 銘柄:
                    continue
                数 = 数字の列を読む(残り)
                if len(数) < 4:
                    continue
                種 = re.search(r"(貸|制)\s*$", m.group(2))
                名前 = re.sub(r"\s*(普通株式|受益証券|優先株式|投資証券|優先出資証券).*$", "", m.group(2))
                名前 = re.sub(r"\s*(プライム|スタンダード|グロース|その他)\s*(貸|制)?\s*$", "", 名前).strip()
                名前 = re.sub(r"^[A-Z]\s+", "", 名前)   # 行頭の区分記号（B など）が残ったときは外す
                銘柄[コード] = {
                    "名前": 名前,
                    "区分": {"貸": "貸借", "制": "制度信用"}.get(種.group(1)) if 種 else None,
                    "売残": 数[0], "売残前日比": 数[1],
                    "買残": 数[2], "買残前日比": 数[3],
                }
    os.remove(pdf_path)
    log(f"   ✅ {len(銘柄)}銘柄（{基準日}申込み現在・{公表日}公表）")
    return {"基準日": 基準日, "公表日": 公表日, "銘柄": 銘柄}


# ---------------------------------------------------------------
# ② 機関の空売り（東証「空売り残高報告」・0.5%以上）
#
#   ★ 落とし穴：毎日のファイルは【その日に増減の報告があった分だけ】。
#     いま残っている空売りの全部ではない。そこで機関ごとの「いちばん新しい報告」を
#     data/karauri_kiroku.json に貯め続け、それを足して「いまの空売り」とする。
#     記録を始めた日より前から動いていない空売りは見えない（画面に書いておく）。
# ---------------------------------------------------------------

def _xls日付(v, datemode):
    import xlrd
    try:
        return xlrd.xldate_as_datetime(float(v), datemode).strftime("%Y/%m/%d")
    except Exception:
        return str(v)


def _空売りファイルを読む(path):
    import xlrd
    wb = xlrd.open_workbook(path)
    sh = wb.sheets()[0]
    出 = []
    for r in range(sh.nrows):
        v = sh.row_values(r)
        if len(v) < 12:
            continue
        コード = str(int(v[2])) if isinstance(v[2], float) else str(v[2]).strip()
        if not re.fullmatch(r"[0-9A-Z]{4}", コード):
            continue
        割合 = v[10] if isinstance(v[10], (int, float)) else 数字にする(str(v[10]))
        数量 = v[11] if isinstance(v[11], (int, float)) else 数字にする(str(v[11]))
        if 割合 is None:
            continue
        出.append({
            "コード": コード,
            "機関": re.sub(r"\s+", " ", str(v[5])).strip() or "（名称なし）",
            "割合": round(float(割合) * 100, 2),
            "数量": int(数量 or 0),
            "計算日": _xls日付(v[1], wb.datemode),
        })
    return 出


def _記録に入れる(記録, コード, 機関, 計算日, 割合, 数量):
    """機関ごとに『いちばん新しい計算日の報告』だけを持つ（古い報告で上書きしない）"""
    枠 = 記録["銘柄"].setdefault(コード, {})
    いま = 枠.get(機関)
    if いま is None or 計算日 >= いま[0]:
        枠[機関] = [計算日, 割合, 数量]
        return True
    return False


def 空売りを貯める(seed=None):
    path = os.path.join(DATA, "karauri_kiroku.json")
    記録 = 読む(path, {"処理済み": [], "銘柄": {}})

    if seed:
        # PC版の記録 {"銘柄":{コード:{機関:{計算日:{割合,数量}}}}} を引き継ぐ
        古 = 読む(seed, {})
        n = 0
        for コード, 機関たち in (古.get("銘柄") or {}).items():
            for 機関, 日々 in 機関たち.items():
                for 日, r in 日々.items():
                    n += _記録に入れる(記録, コード, 機関, 日, r["割合"], r["数量"])
        if 古.get("処理済み"):
            記録["処理済み"] = sorted(set(記録["処理済み"]) | set(古["処理済み"]))
        記録["引き継ぎ"] = sorted(set(記録.get("引き継ぎ", [])) | set((古.get("銘柄") or {}).keys()))
        log(f"📥 以前の空売り記録を引き継ぎ：{n}件")

    html = 取得(空売り残高ページ, timeout=30).text
    一覧 = sorted(set(re.findall(r'href="([^"]*/(\d{8})_Short_Positions\.xls)"', html)), key=lambda t: t[1])
    if not 一覧:
        raise RuntimeError("空売り残高報告のファイルが見つかりません（東証がページの形を変えた可能性）")
    os.makedirs(WORK, exist_ok=True)
    追加 = 0
    for p, 公表 in 一覧:
        if 公表 in 記録["処理済み"]:
            continue
        tmp = os.path.join(WORK, f"short_{公表}.xls")
        try:
            with open(tmp, "wb") as f:
                f.write(取得(JPX + p).content)
            for row in _空売りファイルを読む(tmp):
                追加 += _記録に入れる(記録, row["コード"], row["機関"], row["計算日"], row["割合"], row["数量"])
            記録["処理済み"].append(公表)
        except Exception as e:
            log(f"   ⚠️ 空売り {公表} が読めません: {e}")
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
    記録["処理済み"] = sorted(set(記録["処理済み"]))
    書く(path, 記録)
    log(f"🔻 機関の空売り：{追加}件を更新（読んだ日数 {len(記録['処理済み'])}日・報告のある銘柄 {len(記録['銘柄'])}）")
    return 記録


def 空売りのいま(記録, コード):
    """機関ごとの最新報告のうち、0.5%以上（＝まだ報告義務がある）を足す"""
    内訳 = []
    for 機関, (日, 割合, 数量) in (記録["銘柄"].get(コード) or {}).items():
        if 割合 >= 0.5:
            内訳.append([機関, 割合, 数量, 日])
    内訳.sort(key=lambda x: -x[1])
    return 内訳


# ---------------------------------------------------------------
# ②' 正しい銘柄名と市場（東証「上場銘柄一覧」）
#   信用残PDFは長い名前のうしろに「普通株式 プライム」が混ざって読めてしまうので、名前はこちらを使う
# ---------------------------------------------------------------

上場一覧ページ = JPX + "/markets/statistics-equities/misc/01.html"


def 上場一覧を取る():
    html = 取得(上場一覧ページ, timeout=30).text
    m = re.search(r'href="([^"]*data_j\.(xlsx?))"', html)
    if not m:
        raise RuntimeError("上場銘柄一覧のリンクが見つかりません")
    os.makedirs(WORK, exist_ok=True)
    path = os.path.join(WORK, "data_j." + m.group(2))
    with open(path, "wb") as f:
        f.write(取得(JPX + m.group(1), timeout=120).content)
    行たち = []
    if m.group(2) == "xlsx":
        import openpyxl
        wb = openpyxl.load_workbook(path, read_only=True)
        行たち = [list(v) for v in wb.active.iter_rows(values_only=True)]
        wb.close()
    else:
        import xlrd
        sh = xlrd.open_workbook(path).sheets()[0]
        行たち = [sh.row_values(r) for r in range(sh.nrows)]
    os.remove(path)
    銘柄 = {}
    for v in 行たち:
        if not v or len(v) < 6 or v[1] is None:
            continue
        コード = str(int(v[1])) if isinstance(v[1], float) else str(v[1]).strip()
        if not re.fullmatch(r"[0-9A-Z]{4}", コード):
            continue
        市場 = re.sub(r"（.*?）", "", str(v[3] or "")).strip()
        業種 = str(v[5] or "").strip()
        銘柄[コード] = [str(v[2] or "").strip(), 市場, "" if 業種 == "-" else 業種]
    log(f"🏷️ 上場銘柄一覧 {len(銘柄)}銘柄")
    return 銘柄


def 名前を掃除(名前):
    """一覧に無かった銘柄用：PDFから読んだ名前のうしろの混ざりものを落とす"""
    return re.split(r"\s+(普通|受益|投信|優先|投資|Ｅ投|ＥＴ投)", 名前)[0].strip()


# ---------------------------------------------------------------
# ③ 株価（終値と前日比）… yfinance でまとめて取る
# ---------------------------------------------------------------

def 株価を取る(コード一覧, まとめ=200):
    import yfinance as yf
    import math
    結果 = {}
    # 取引時間中（15:40より前）に動いたときは、今日の行は「途中の値段」なので使わない（終値ではないため）
    いま = datetime.now(JST)
    今日 = いま.strftime("%Y/%m/%d")
    途中 = (いま.hour, いま.minute) < (15, 40)
    コード一覧 = list(コード一覧)
    for i in range(0, len(コード一覧), まとめ):
        組 = コード一覧[i:i + まとめ]
        tickers = [c + ".T" for c in 組]
        for 回 in range(3):
            try:
                df = yf.download(tickers, period="7d", interval="1d", group_by="ticker",
                                 auto_adjust=False, threads=True, progress=False)
                break
            except Exception as e:
                log(f"   ⚠️ 株価 {i}〜 取得失敗（{e}）→ 少し待ってやり直し")
                time.sleep(10 * (回 + 1))
        else:
            continue
        for c, t in zip(組, tickers):
            try:
                s = (df[t]["Close"] if len(tickers) > 1 else df["Close"]).dropna()
            except Exception:
                continue
            if 途中 and len(s) and s.index[-1].strftime("%Y/%m/%d") == 今日:
                s = s.iloc[:-1]
            if len(s) == 0:
                continue
            終値 = float(s.iloc[-1])
            前 = float(s.iloc[-2]) if len(s) >= 2 else None
            if math.isnan(終値):
                continue
            結果[c] = [round(終値, 1), round(前, 1) if 前 else None, s.index[-1].strftime("%Y/%m/%d")]
        log(f"   📈 株価 {min(i + まとめ, len(コード一覧))}/{len(コード一覧)}（取れた {len(結果)}）")
        time.sleep(2)
    return 結果


# ---------------------------------------------------------------
# まとめて data/latest.json へ
# ---------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed")
    ap.add_argument("--no-price", action="store_true")
    a = ap.parse_args()
    os.makedirs(DATA, exist_ok=True)
    出力 = os.path.join(DATA, "latest.json")
    前回 = 読む(出力, {})

    信用 = 信用残を取る()
    記録 = 空売りを貯める(a.seed)
    try:
        一覧 = 上場一覧を取る()
    except Exception as e:
        log(f"⚠️ 上場銘柄一覧が取れません（{e}）。PDFの名前を掃除して使います")
        一覧 = {}

    コード一覧 = sorted(信用["銘柄"].keys())
    株価 = {} if a.no_price else 株価を取る(コード一覧)
    # 株価が取れなかった銘柄は、前回の値を残す（1日だけ取り損ねても空にならないように）
    前の株価 = {c: v[2:5] for c, v in (前回.get("s") or {}).items() if len(v) >= 5 and v[2] is not None}

    行 = {}
    株価の日 = {}
    for c in コード一覧:
        d = 信用["銘柄"][c]
        p = 株価.get(c) or 前の株価.get(c) or [None, None, None]
        if p[2]:
            株価の日[p[2]] = 株価の日.get(p[2], 0) + 1
        空 = 空売りのいま(記録, c)
        名, 市場, 業種 = 一覧.get(c) or [名前を掃除(d["名前"]), "", ""]
        # 並び：0名前, 1区分, 2終値, 3前日終値, 4株価の日付, 5買残, 6買残前日比, 7売残, 8売残前日比,
        #       9機関の空売り[[機関,%,株数,計算日],...], 10市場, 11業種
        行[c] = [名, d["区分"], p[0], p[1], p[2], d["買残"], d["買残前日比"], d["売残"], d["売残前日比"], 空, 市場, 業種]

    処理済み = 記録["処理済み"]
    f = lambda s: f"{s[:4]}/{s[4:6]}/{s[6:]}"
    結果 = {
        "v": 1,
        "更新": datetime.now(JST).strftime("%Y/%m/%d %H:%M"),
        "信用基準日": 信用["基準日"], "信用公表日": 信用["公表日"],
        "株価の日": max(株価の日, key=株価の日.get) if 株価の日 else None,
        "空売り公表日": f(処理済み[-1]) if 処理済み else None,
        "空売り記録開始": f(処理済み[0]) if 処理済み else None,
        "引き継ぎ": 記録.get("引き継ぎ", []),
        "s": 行,
    }
    書く(出力, 結果)
    log(f"💾 data/latest.json を書きました（{len(行)}銘柄・株価あり {sum(1 for v in 行.values() if v[2] is not None)}）")


if __name__ == "__main__":
    main()
