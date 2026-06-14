import os, io, base64, datetime, re
import requests
import pandas as pd
import yfinance as yf
import matplotlib; matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.colors as mc
import matplotlib.font_manager as fm
from collections import defaultdict

# ===== 설정 (환경변수에서 읽음) =====
NOTION_TOKEN  = os.environ["NOTION_TOKEN"]
IMGBB_API_KEY = os.environ["IMGBB_API_KEY"]
NOTION_VERSION = "2022-06-28"

DB_TRADES   = "02a7112ea57849bbb84c0265d1f80aa9"   # 매매일지
DB_HOLDINGS = "fa685555e48b44648f1ae6f2e7a081e4"   # 보유주식
DB_ASSETS   = "108a8c565e6241019b6b5d6f589914b8"   # 총자산
PAGE_ID     = "37eebf1d-d092-81e2-ab7e-cc5d44c19865"
CHART_HEADING = "분류별 비율"

H = {"Authorization": f"Bearer {NOTION_TOKEN}", "Notion-Version": NOTION_VERSION, "Content-Type": "application/json"}

# ===== 한글 폰트: 직접 다운로드 후 FontProperties 객체로 보관 =====
import urllib.request
import matplotlib.font_manager as fm

FONT_PATH = "/tmp/NanumGothic.ttf"
if not os.path.exists(FONT_PATH):
    for url in [
        "https://github.com/google/fonts/raw/main/ofl/nanumgothic/NanumGothic-Regular.ttf",
        "https://cdn.jsdelivr.net/gh/google/fonts/ofl/nanumgothic/NanumGothic-Regular.ttf",
    ]:
        try:
            urllib.request.urlretrieve(url, FONT_PATH)
            if os.path.getsize(FONT_PATH) > 100000:  # 정상 폰트 파일인지 크기 확인
                print("폰트 다운로드 완료:", url); break
        except Exception as e:
            print("폰트 다운로드 시도 실패:", e)

KFONT = fm.FontProperties(fname=FONT_PATH) if os.path.exists(FONT_PATH) else None
if KFONT:
    fm.fontManager.addfont(FONT_PATH)
    matplotlib.rcParams["font.family"] = KFONT.get_name()
    print("폰트 적용:", KFONT.get_name())
else:
    print("⚠️ 폰트 없음 — 한글이 깨질 수 있음")
matplotlib.rcParams["axes.unicode_minus"] = False

# ===== 공통 함수 =====
def notion_query(db_id):
    out, cursor = [], None
    while True:
        payload = {"page_size": 100}
        if cursor: payload["start_cursor"] = cursor
        r = requests.post(f"https://api.notion.com/v1/databases/{db_id}/query", headers=H, json=payload)
        r.raise_for_status(); data = r.json()
        out += data["results"]
        if not data.get("has_more"): break
        cursor = data["next_cursor"]
    return out

def rt(t): return [{"type": "text", "text": {"content": str(t)}}]
def p_title(pg, n):
    a = pg["properties"][n]["title"]; return a[0]["plain_text"] if a else ""
def p_text(pg, n):
    a = pg["properties"][n]["rich_text"]; return a[0]["plain_text"] if a else ""
def p_num(pg, n): return pg["properties"][n]["number"]
def p_select(pg, n):
    s = pg["properties"][n]["select"]; return s["name"] if s else None
def p_date(pg, n):
    v = pg["properties"][n].get("date"); return v["start"] if v else None

def is_domestic(cat): return (cat or "").startswith("국내") or "코스피" in (cat or "")
def yf_syms(ticker, cat):
    if is_domestic(cat) or str(ticker).isdigit(): return [f"{ticker}.KS", f"{ticker}.KQ"]
    return [ticker]

def get_price(ticker, cat):
    for s in yf_syms(ticker, cat):
        try:
            h = yf.Ticker(s).history(period="1d")
            if len(h): return float(h["Close"].iloc[-1])
        except Exception: pass
    return None

def close_on_date(ticker, cat, date_str):
    d = datetime.date.fromisoformat(date_str[:10])
    start = (d - datetime.timedelta(days=7)).isoformat(); end = (d + datetime.timedelta(days=1)).isoformat()
    for sym in yf_syms(ticker, cat):
        try:
            h = yf.download(sym, start=start, end=end, progress=False, auto_adjust=True)["Close"]
            s = pd.Series(h.squeeze()).dropna()
            if len(s):
                s.index = pd.to_datetime(s.index).date
                upto = [v for dt, v in s.items() if dt <= d]
                return float(upto[-1] if upto else s.iloc[-1])
        except Exception: pass
    return None

def find_kr_ticker(name):
    key = name.replace(" ", "")
    try:
        r = requests.get("https://ac.stock.naver.com/ac", params={"q": name, "target": "stock,index"},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        items = r.json().get("items", [])
        kr = [it for it in items if it.get("typeCode") in ("KOSPI", "KOSDAQ", "stock")]
        exact = [it for it in kr if it.get("name", "").replace(" ", "") == key]
        pick = (exact or kr)
        if pick:
            code = re.sub(r"\D", "", pick[0].get("code") or pick[0].get("reutersCode", ""))[:6]
            if len(code) == 6: return code
    except Exception as e:
        print("네이버 조회 오류:", e)
    return None

def find_us_ticker(name):
    try:
        r = requests.get("https://query2.finance.yahoo.com/v1/finance/search",
                         params={"q": name, "quotesCount": 5, "newsCount": 0},
                         headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
        for q in r.json().get("quotes", []):
            if q.get("symbol"): return q["symbol"]
    except Exception: pass
    return None

# ===== 0a) 티커 자동조회 =====
for t in notion_query(DB_TRADES):
    if p_text(t, "티커"): continue
    name = p_title(t, "종목이름"); cat = p_select(t, "분류")
    if not name: continue
    tk = find_kr_ticker(name) if is_domestic(cat) else find_us_ticker(name)
    if not tk:
        print(f"티커 조회 실패: {name}"); continue
    requests.patch(f"https://api.notion.com/v1/pages/{t['id']}", headers=H,
                   json={"properties": {"티커": {"rich_text": rt(tk)}}}).raise_for_status()
    print(f"티커 입력: {name} -> {tk}")

# ===== 0b) 단가 자동채움 =====
for t in notion_query(DB_TRADES):
    if p_num(t, "단가"): continue
    ticker = p_text(t, "티커"); cat = p_select(t, "분류"); date_str = p_date(t, "날짜")
    if not (ticker and date_str): continue
    px = close_on_date(ticker, cat, date_str)
    if px is None: continue
    requests.patch(f"https://api.notion.com/v1/pages/{t['id']}", headers=H,
                   json={"properties": {"단가": {"number": round(px)}}}).raise_for_status()
    print(f"단가 입력: {p_title(t,'종목이름')} {date_str[:10]} -> {round(px):,}")

# ===== 1) 평균원가법: 현재 포지션 + 실현손익 =====
trades = defaultdict(list)
for t in notion_query(DB_TRADES):
    tk = p_text(t, "티커")
    if not tk: continue
    trades[tk].append({"date": p_date(t, "날짜") or "", "side": p_select(t, "매수/매도"),
                       "qty": p_num(t, "수량") or 0, "price": p_num(t, "단가") or 0,
                       "name": p_title(t, "종목이름"), "cat": p_select(t, "분류")})

positions, total_buy_cost = {}, 0.0
for tk, lst in trades.items():
    lst.sort(key=lambda x: x["date"])
    qty = cost = realized = 0.0; name = ""; cat = None
    for tr in lst:
        name = tr["name"] or name; cat = tr["cat"] or cat
        if tr["side"] == "매도":
            avg = (cost/qty) if qty else tr["price"]
            realized += (tr["price"] - avg) * tr["qty"]
            qty -= tr["qty"]; cost -= avg * tr["qty"]
            if qty <= 0: qty = cost = 0.0
        else:
            qty += tr["qty"]; cost += tr["qty"] * tr["price"]; total_buy_cost += tr["qty"] * tr["price"]
    positions[tk] = {"name": name, "cat": cat, "qty": qty, "avg": (cost/qty) if qty else 0, "realized": realized}

# ===== 2) 보유주식 upsert + 평가손익 =====
by_ticker = {p_text(h, "티커"): h for h in notion_query(DB_HOLDINGS)}
tot_eval = tot_unreal = tot_realized = 0.0
for tk, d in positions.items():
    tot_realized += d["realized"]
    if d["qty"] > 0:
        price = get_price(tk, d["cat"]) or d["avg"]
        ev = price*d["qty"]; co = d["avg"]*d["qty"]; pf = ev-co; rate = (pf/co) if co else 0
        tot_eval += ev; tot_unreal += pf
        props = {"종목이름": {"title": rt(d["name"])}, "티커": {"rich_text": rt(tk)},
                 "평가금액": {"number": round(ev)}, "수익": {"number": round(pf)},
                 "수익률": {"number": round(rate, 4)}, "보유수량": {"number": d["qty"]},
                 "매입가": {"number": round(d["avg"])}}
        if d["cat"]: props["분류"] = {"select": {"name": d["cat"]}}
    else:
        if tk not in by_ticker: continue
        props = {"보유수량": {"number": 0}, "평가금액": {"number": 0}, "수익": {"number": 0}, "수익률": {"number": 0}}
    if tk in by_ticker:
        requests.patch(f"https://api.notion.com/v1/pages/{by_ticker[tk]['id']}", headers=H, json={"properties": props}).raise_for_status()
    else:
        requests.post("https://api.notion.com/v1/pages", headers=H, json={"parent": {"database_id": DB_HOLDINGS}, "properties": props}).raise_for_status()

# ===== 3) 총자산 upsert =====
tot_total = tot_unreal + tot_realized
tot_rate = (tot_total/total_buy_cost) if total_buy_cost else 0
today = datetime.date.today().isoformat()
today_row = next((a for a in notion_query(DB_ASSETS) if p_title(a, "작성일자") == today), None)
ap = {"작성일자": {"title": rt(today)}, "총평가금액": {"number": round(tot_eval)},
      "평가손익": {"number": round(tot_unreal)}, "실현손익": {"number": round(tot_realized)},
      "총수익": {"number": round(tot_total)}, "총수익률": {"number": round(tot_rate, 4)}}
if today_row:
    requests.patch(f"https://api.notion.com/v1/pages/{today_row['id']}", headers=H, json={"properties": ap}).raise_for_status()
else:
    requests.post("https://api.notion.com/v1/pages", headers=H, json={"parent": {"database_id": DB_ASSETS}, "properties": ap}).raise_for_status()
print(f"총자산 갱신: 평가 {round(tot_eval):,} | 평가손익 {round(tot_unreal):,} | 실현손익 {round(tot_realized):,} | 수익률 {tot_rate*100:.2f}%")

# ===== 4) 분류별 파이차트 + 표 → ImgBB → 노션 이미지 블록 =====
cat_val = {}
for h in notion_query(DB_HOLDINGS):
    qty = p_num(h, "보유수량") or 0
    if qty <= 0: continue
    cat = p_select(h, "분류") or "기타"
    ev = p_num(h, "평가금액") or ((p_num(h, "매입가") or 0) * qty)
    if ev and ev > 0: cat_val[cat] = cat_val.get(cat, 0) + ev
cat_val = {k: v for k, v in cat_val.items() if v > 0}

if cat_val:
    total = sum(cat_val.values())
    labels = list(cat_val.keys()); sizes = list(cat_val.values())
    colors = list(plt.cm.Set2.colors)[:len(labels)]
    def _tint(c, f=0.55):
        r, g, b = mc.to_rgb(c); return (r+(1-r)*f, g+(1-g)*f, b+(1-b)*f)
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(12, 5), dpi=130, gridspec_kw={"width_ratios": [1, 1.1]})

    # (좌) 파이 — 라벨/퍼센트에 폰트 직접 지정
    wedges, texts, autotexts = ax1.pie(
        sizes, labels=labels, autopct="%1.1f%%", startangle=90, counterclock=False,
        colors=colors, wedgeprops={"edgecolor": "white", "linewidth": 1.5},
        textprops={"fontproperties": KFONT})
    for at in autotexts: at.set_fontproperties(KFONT)
    ax1.set_title("보유주식 분류별 비율", fontsize=14, fontproperties=KFONT); ax1.axis("equal")

    # (우) 표
    ax2.axis("off"); ax2.set_title("분류별 평가금액", fontsize=14, fontproperties=KFONT)
    rows, cc = [], []
    for i, k in enumerate(labels):
        rows.append([k, f"{int(round(cat_val[k])):,}원", f"{cat_val[k]/total*100:.1f}%"]); cc.append([_tint(colors[i]), "white", "white"])
    rows.append(["합계", f"{int(round(total)):,}원", "100%"]); cc.append(["#e6e6e6", "#f2f2f2", "#f2f2f2"])
    tbl = ax2.table(cellText=rows, colLabels=["분류", "평가금액", "비율"], cellColours=cc,
                    colColours=["#cfcfe8"]*3, cellLoc="center", loc="center")
    tbl.auto_set_font_size(False); tbl.set_fontsize(11); tbl.scale(1, 1.7)
    for cell in tbl.get_celld().values():
        cell.get_text().set_fontproperties(KFONT)
    buf = io.BytesIO(); plt.savefig(buf, format="png", bbox_inches="tight"); plt.close(fig)

    up = requests.post("https://api.imgbb.com/1/upload", params={"key": IMGBB_API_KEY},
                       data={"image": base64.b64encode(buf.getvalue()).decode()})
    up.raise_for_status(); img_url = up.json()["data"]["url"]
    print("차트 업로드:", img_url)

    def page_children(bid):
        out, cur = [], None
        while True:
            pa = {"page_size": 100}
            if cur: pa["start_cursor"] = cur
            r = requests.get(f"https://api.notion.com/v1/blocks/{bid}/children", headers=H, params=pa)
            r.raise_for_status(); dt = r.json(); out += dt["results"]
            if not dt.get("has_more"): break
            cur = dt["next_cursor"]
        return out
    img_id, found = None, False
    for b in page_children(PAGE_ID):
        bt = b["type"]
        if bt in ("heading_1", "heading_2", "heading_3"):
            found = ("".join(x["plain_text"] for x in b[bt]["rich_text"]).strip() == CHART_HEADING)
        elif found and bt == "image":
            img_id = b["id"]; break
    if img_id:
        requests.patch(f"https://api.notion.com/v1/blocks/{img_id}", headers=H,
                       json={"image": {"external": {"url": img_url}}}).raise_for_status()
    else:
        requests.patch(f"https://api.notion.com/v1/blocks/{PAGE_ID}/children", headers=H, json={"children": [
            {"object":"block","type":"heading_2","heading_2":{"rich_text":[{"type":"text","text":{"content":CHART_HEADING}}]}},
            {"object":"block","type":"image","image":{"type":"external","external":{"url":img_url}}}]}).raise_for_status()
    print("차트 갱신 완료")

    def page_children(bid):
        out, cur = [], None
        while True:
            pa = {"page_size": 100}
            if cur: pa["start_cursor"] = cur
            r = requests.get(f"https://api.notion.com/v1/blocks/{bid}/children", headers=H, params=pa)
            r.raise_for_status(); dt = r.json(); out += dt["results"]
            if not dt.get("has_more"): break
            cur = dt["next_cursor"]
        return out
    img_id, found = None, False
    for b in page_children(PAGE_ID):
        bt = b["type"]
        if bt in ("heading_1", "heading_2", "heading_3"):
            found = ("".join(x["plain_text"] for x in b[bt]["rich_text"]).strip() == CHART_HEADING)
        elif found and bt == "image":
            img_id = b["id"]; break
    if img_id:
        requests.patch(f"https://api.notion.com/v1/blocks/{img_id}", headers=H,
                       json={"image": {"external": {"url": img_url}}}).raise_for_status()
    else:
        requests.patch(f"https://api.notion.com/v1/blocks/{PAGE_ID}/children", headers=H, json={"children": [
            {"object":"block","type":"heading_2","heading_2":{"rich_text":[{"type":"text","text":{"content":CHART_HEADING}}]}},
            {"object":"block","type":"image","image":{"type":"external","external":{"url":img_url}}}]}).raise_for_status()
    print("차트 갱신 완료")
# ===== 5) 지수기반 종목분석 (금요일에만 실행) =====
# weekday(): 월=0 ... 금=4 ... 일=6
if datetime.date.today().weekday() == 4:
    print("금요일 → 지수기반 종목분석 실행")
    ANALYSIS_HEADING = "지수기반 종목분석"
    CUTLOSS_GAP = -10.0
    WATCHLIST = [
        ("테슬라", "TSLA", "나스닥100"), ("구글(알파벳)", "GOOG", "나스닥100"), ("엔비디아", "NVDA", "나스닥100"),
        ("SK하이닉스", "000660", "코스피200"), ("현대자동차", "005380", "코스피200"), ("삼성전자", "005930", "코스피200"),
        ("삼성전자우", "005935", "코스피200"), ("삼성전기", "009150", "코스피200"), ("타이거200", "102110", "코스피200"),
        ("월마트", "WMT", "S&P500"), ("존슨앤드존슨", "JNJ", "S&P500"), ("코카콜라", "KO", "S&P500"),
    ]
    INDEX_SYMS = {"코스피200": ["^KS200", "069500.KS"], "S&P500": ["^GSPC"], "나스닥100": ["^NDX"]}
    ORDER = ["코스피200", "S&P500", "나스닥100"]

    def _month_end(df):
        try: return df.resample("ME").last()
        except Exception: return df.resample("M").last()
    def monthly_returns(symbols):
        end = datetime.date.today(); start = end - datetime.timedelta(days=240)
        data = yf.download(symbols, start=start.isoformat(), end=end.isoformat(),
                           progress=False, auto_adjust=True)["Close"]
        if isinstance(data, pd.Series): data = data.to_frame()
        return _month_end(data).pct_change().mul(100).dropna(how="all").tail(6)
    def cum_return(series):
        s = series.dropna()
        import numpy as np
        return (np.prod(1 + s/100) - 1) * 100 if len(s) else float("nan")
    def resolve_index(cands):
        for s in cands:
            try:
                d = yf.download(s, period="1mo", progress=False, auto_adjust=True)["Close"]
                if len(pd.Series(d.squeeze()).dropna()): return s
            except Exception: pass
        return cands[0]

    idx_sym = {name: resolve_index(c) for name, c in INDEX_SYMS.items()}
    idx_ret = monthly_returns(list(idx_sym.values()))
    a_stock_ret = monthly_returns([yf_syms(t, c)[0] for _, t, c in WATCHLIST])
    a_months = [d.strftime("%y-%m") for d in a_stock_ret.index]
    idx_ret = idx_ret.reindex(a_stock_ret.index)

    # 세로 3단 차트 (한 장)
    fig, axes = plt.subplots(3, 1, figsize=(12, 18), dpi=140)
    for ax, idxname in zip(axes, ORDER):
        isym = idx_sym[idxname]
        iser = idx_ret[isym] if isym in idx_ret else pd.Series(index=a_stock_ret.index, dtype=float)
        ax.plot(a_months, iser.values, marker="o", lw=4, color="black", label=f"{idxname}(지수)", zorder=5)
        for n, t, ix in WATCHLIST:
            if ix != idxname: continue
            sym = yf_syms(t, ix)[0]
            if sym in a_stock_ret: ax.plot(a_months, a_stock_ret[sym].values, marker="o", lw=2, label=n)
        ax.axhline(0, color="gray", lw=0.8, ls="--")
        ax.set_title(f"{idxname} 기준 · 6개월 월별수익률 비교", fontsize=16, fontproperties=KFONT)
        ax.set_xlabel("월", fontproperties=KFONT); ax.set_ylabel("월별 수익률 (%)", fontproperties=KFONT)
        ax.grid(True, alpha=0.3); ax.tick_params(axis="x", rotation=45)
        leg = ax.legend(fontsize=11, loc="best", ncol=2)
        for txt in leg.get_texts(): txt.set_fontproperties(KFONT)
    fig.tight_layout(pad=3.0)
    buf = io.BytesIO(); fig.savefig(buf, format="png", bbox_inches="tight"); plt.close(fig)

    up = requests.post("https://api.imgbb.com/1/upload", params={"key": IMGBB_API_KEY},
                       data={"image": base64.b64encode(buf.getvalue()).decode()})
    up.raise_for_status(); a_url = up.json()["data"]["url"]
    print("지수분석 차트 업로드:", a_url)

    # 노션 "지수기반 종목분석" 이미지 블록 갱신
    a_img, a_found = None, False
    for b in page_children(PAGE_ID):
        bt = b["type"]
        if bt in ("heading_1", "heading_2", "heading_3"):
            a_found = ("".join(x["plain_text"] for x in b[bt]["rich_text"]).strip() == ANALYSIS_HEADING)
        elif a_found and bt == "image":
            a_img = b["id"]; break
    if a_img:
        requests.patch(f"https://api.notion.com/v1/blocks/{a_img}", headers=H,
                       json={"image": {"external": {"url": a_url}}}).raise_for_status()
    else:
        requests.patch(f"https://api.notion.com/v1/blocks/{PAGE_ID}/children", headers=H, json={"children": [
            {"object":"block","type":"heading_2","heading_2":{"rich_text":[{"type":"text","text":{"content":ANALYSIS_HEADING}}]}},
            {"object":"block","type":"image","image":{"type":"external","external":{"url":a_url}}}]}).raise_for_status()
    print("지수분석 차트 갱신 완료")
    # ----- 분석 대상 노션 표(인라인 DB) 자동 갱신 -----
    import math
    ANALYSIS_DB_TITLE = "분석 대상"

    def _find_child_db(page_id, title):
        cur = None
        while True:
            pa = {"page_size": 100}
            if cur: pa["start_cursor"] = cur
            r = requests.get(f"https://api.notion.com/v1/blocks/{page_id}/children", headers=H, params=pa)
            r.raise_for_status(); data = r.json()
            for b in data["results"]:
                if b["type"] == "child_database" and b["child_database"]["title"].strip() == title:
                    return b["id"]
            if not data.get("has_more"): break
            cur = data["next_cursor"]
        return None

    a_db = _find_child_db(PAGE_ID, ANALYSIS_DB_TITLE)
    if not a_db:
        r = requests.post("https://api.notion.com/v1/databases", headers=H, json={
            "parent": {"type": "page_id", "page_id": PAGE_ID},
            "title": [{"type": "text", "text": {"content": ANALYSIS_DB_TITLE}}],
            "is_inline": True,
            "properties": {
                "종목": {"title": {}}, "티커": {"rich_text": {}},
                "기준지수": {"select": {"options": [
                    {"name": "코스피200", "color": "blue"}, {"name": "S&P500", "color": "green"}, {"name": "나스닥100", "color": "purple"}]}},
                "6개월수익률(%)": {"number": {"format": "number"}},
                "지수수익률(%)": {"number": {"format": "number"}},
                "차이(%p)": {"number": {"format": "number"}},
                "판정": {"select": {"options": [{"name": "손절 검토", "color": "red"}, {"name": "유지", "color": "green"}]}},
            }})
        r.raise_for_status(); a_db = r.json()["id"]

    a_existing = {}
    for row in notion_query(a_db):
        rtk = row["properties"]["티커"]["rich_text"]
        if rtk: a_existing[rtk[0]["plain_text"]] = row["id"]

    def _num(x):
        return None if (x is None or (isinstance(x, float) and math.isnan(x))) else round(float(x), 2)

    for n, t, ix in WATCHLIST:
        sym = yf_syms(t, ix)[0]
        sret = cum_return(a_stock_ret[sym]) if sym in a_stock_ret else float("nan")
        iret = cum_return(idx_ret[idx_sym[ix]]) if idx_sym[ix] in idx_ret else float("nan")
        gap = sret - iret
        verdict = "손절 검토" if (pd.notna(gap) and gap <= CUTLOSS_GAP) else "유지"
        props = {
            "종목": {"title": [{"type": "text", "text": {"content": n}}]},
            "티커": {"rich_text": [{"type": "text", "text": {"content": t}}]},
            "기준지수": {"select": {"name": ix}},
            "6개월수익률(%)": {"number": _num(sret)},
            "지수수익률(%)": {"number": _num(iret)},
            "차이(%p)": {"number": _num(gap)},
            "판정": {"select": {"name": verdict}},
        }
        if t in a_existing:
            requests.patch(f"https://api.notion.com/v1/pages/{a_existing[t]}", headers=H, json={"properties": props}).raise_for_status()
        else:
            requests.post("https://api.notion.com/v1/pages", headers=H, json={"parent": {"database_id": a_db}, "properties": props}).raise_for_status()
    print(f"분석 대상 표 갱신 완료 ({len(WATCHLIST)}종목)")
else:
    print("오늘은 금요일이 아니라 지수분석은 건너뜀")

print("=== 전체 완료 ===")
