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

# ===== 한글 폰트 (Nanum 설치) =====
os.system("apt-get -qq update > /dev/null 2>&1; apt-get -qq install -y fonts-nanum > /dev/null 2>&1; fc-cache -f > /dev/null 2>&1")
NANUM = "/usr/share/fonts/truetype/nanum/NanumGothic.ttf"
if os.path.exists(NANUM):
    fm.fontManager.addfont(NANUM); matplotlib.rc("font", family="NanumGothic")
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
    ax1.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=90, counterclock=False,
            colors=colors, wedgeprops={"edgecolor": "white", "linewidth": 1.5})
    ax1.set_title("보유주식 분류별 비율", fontsize=14, fontweight="bold"); ax1.axis("equal")
    ax2.axis("off"); ax2.set_title("분류별 평가금액", fontsize=14, fontweight="bold")
    rows, cc = [], []
    for i, k in enumerate(labels):
        rows.append([k, f"{int(round(cat_val[k])):,}원", f"{cat_val[k]/total*100:.1f}%"]); cc.append([_tint(colors[i]), "white", "white"])
    rows.append(["합계", f"{int(round(total)):,}원", "100%"]); cc.append(["#e6e6e6", "#f2f2f2", "#f2f2f2"])
    tbl = ax2.table(cellText=rows, colLabels=["분류", "평가금액", "비율"], cellColours=cc,
                    colColours=["#cfcfe8"]*3, cellLoc="center", loc="center")
    tbl.auto_set_font_size(False); tbl.set_fontsize(11); tbl.scale(1, 1.7)
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

print("=== 전체 완료 ===")
