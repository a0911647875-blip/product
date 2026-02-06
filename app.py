import os
import glob
import streamlit as st
import pandas as pd

st.set_page_config(page_title="多商品總保費計算器", layout="wide")
st.title("多商品總保費計算器（可多附約合併、逐年明細、匯出）")

RATES_DIR = "rates"

@st.cache_data
def load_rates(rates_dir: str) -> pd.DataFrame:
    if not os.path.isdir(rates_dir):
        return pd.DataFrame()

    files = glob.glob(os.path.join(rates_dir, "*.csv"))
    if not files:
        return pd.DataFrame()

    dfs = []

    for f in files:
        df = None

        # 依序嘗試常見編碼
        for enc in ("utf-8", "utf-8-sig", "cp950", "big5"):
            try:
                df = pd.read_csv(f, encoding=enc)
                break
            except UnicodeDecodeError:
                pass

        if df is None:
            raise ValueError(f"檔案無法讀取（編碼不支援）：{os.path.basename(f)}")

        df["source_file"] = os.path.basename(f)
        dfs.append(df)

    all_df = pd.concat(dfs, ignore_index=True)

    required = {"product_code", "product_name", "unit", "age", "sex", "rate"}
    missing_cols = required - set(all_df.columns)
    if missing_cols:
        raise ValueError(f"費率檔欄位缺少：{missing_cols}")

    all_df["age"] = all_df["age"].astype(int)
    all_df["sex"] = all_df["sex"].astype(str).str.upper().str.strip()
    all_df["unit"] = all_df["unit"].astype(str).str.strip()
    all_df["product_code"] = all_df["product_code"].astype(str).str.strip()
    all_df["product_name"] = all_df["product_name"].astype(str).str.strip()
    all_df["rate"] = pd.to_numeric(all_df["rate"], errors="coerce")

    bad = all_df["rate"].isna()
    if bad.any():
        raise ValueError("有 rate 無法轉成數字，請檢查 CSV")

    return all_df

def unit_to_multiplier(unit: str, amount: float) -> float:
    """把保額換成「費率表單位數」：per_10k 表示每 1 萬元為 1 單位。"""
    if unit == "per_10k":
        return amount / 10_000
    elif unit == "per_1k":
        return amount / 1_000
    elif unit == "per_1":
        return amount
    else:
        raise ValueError(f"不支援的 unit：{unit}（目前支援 per_10k / per_1k / per_1）")

rates = load_rates(RATES_DIR)

if rates.empty:
    st.warning("找不到費率檔。請在同資料夾建立 rates/，並放入商品 CSV（例如 XDE.csv）。")
    st.stop()

# 商品清單（下拉選）
products = (
    rates[["product_code", "product_name"]]
    .drop_duplicates()
    .sort_values(["product_code", "product_name"])
)
product_options = [f"{r.product_code}｜{r.product_name}" for r in products.itertuples(index=False)]

# 基本條件
colA, colB, colC, colD = st.columns(4)
with colA:
    sex_ui = st.selectbox("性別", ["男", "女"])
    sex = "M" if sex_ui == "男" else "F"
with colB:
    start_age = st.number_input("起算年齡", min_value=0, max_value=80, value=16, step=1)
with colC:
    end_age = st.number_input("結束年齡（含該歲）", min_value=0, max_value=80, value=50, step=1)
with colD:
    include_end = st.checkbox("包含結束年齡當年度", value=True)

st.divider()

st.subheader("加入要計算的商品（可多個）")
st.caption("每一列是一個商品/附約：設定保額與份數後加入清單，最後一鍵計算加總。")

# 清單狀態
if "items" not in st.session_state:
    st.session_state["items"] = []

add_col1, add_col2, add_col3, add_col4 = st.columns([4, 2, 2, 2])
with add_col1:
    pick = st.selectbox("選商品", product_options)
with add_col2:
    amount = st.number_input("保額（元）", min_value=1_000, value=1_000_000, step=10_000)
with add_col3:
    qty = st.number_input("份數（同商品多份）", min_value=1, value=1, step=1)
with add_col4:
    st.write("")
    st.write("")
    add_btn = st.button("➕ 加入清單")

if add_btn:
    code, name = pick.split("｜", 1)
    st.session_state["items"].append(
        {"product_code": code, "product_name": name, "amount": float(amount), "qty": int(qty)}
    )

items = st.session_state["items"]

# 顯示清單
if items:
    df_items = pd.DataFrame(items)
    st.dataframe(df_items, use_container_width=True)

    colR1, colR2 = st.columns([1, 6])
    with colR1:
        if st.button("🗑️ 清空清單"):
            st.session_state["items"] = []
            st.rerun()
    with colR2:
        st.info("提示：你可以把常見「主約+附約套餐」都加入清單，用同一套年齡/性別一鍵試算。")
else:
    st.info("先加入至少 1 個商品再計算。")

st.divider()

def calc_total(rates_df: pd.DataFrame, items: list, sex: str, start_age: int, end_age: int, include_end: bool):
    if end_age < start_age:
        raise ValueError("結束年齡必須 >= 起算年齡")

    ages = list(range(int(start_age), int(end_age) + (1 if include_end else 0)))
    rows_year = []

    for item in items:
        code = item["product_code"]
        amt = item["amount"]
        qty = item["qty"]

        sub = rates_df[(rates_df["product_code"] == code) & (rates_df["sex"] == sex)]
        if sub.empty:
            raise ValueError(f"商品 {code} 找不到性別 {sex} 的費率資料（請確認 CSV 有 M/F）")

        # unit 應在同商品一致
        units = sub["unit"].unique().tolist()
        if len(units) != 1:
            raise ValueError(f"商品 {code} 的 unit 不一致：{units}（請統一）")
        unit = units[0]

        multiplier = unit_to_multiplier(unit, amt) * qty

        rate_map = sub.set_index("age")["rate"].to_dict()
        missing = [a for a in ages if a not in rate_map]
        if missing:
            raise ValueError(f"商品 {code} 費率表缺少年齡：{missing}")

        for a in ages:
            rate = rate_map[a]
            premium = rate * multiplier
            rows_year.append({
                "年齡": a,
                "商品代碼": code,
                "商品名稱": item["product_name"],
                "unit": unit,
                "保額(元)": int(amt),
                "份數": int(qty),
                "每單位費率": float(rate),
                "當年保費(元)": float(premium),
            })

    df_detail = pd.DataFrame(rows_year)

    df_year_sum = (
        df_detail.groupby("年齡", as_index=False)["當年保費(元)"]
        .sum()
        .sort_values("年齡")
    )
    df_year_sum["累計(元)"] = df_year_sum["當年保費(元)"].cumsum()

    total = float(df_year_sum["當年保費(元)"].sum())
    return total, df_detail, df_year_sum

calc_btn = st.button("✅ 計算總保費", type="primary", disabled=(len(items) == 0))

if calc_btn:
    try:
        total, df_detail, df_year = calc_total(
            rates, items, sex, int(start_age), int(end_age), include_end
        )

        st.subheader("結果")
        st.metric("總繳保費（元）", f"{round(total):,}")

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("逐年合計（所有商品加總）")
            show_year = df_year.copy()
            show_year["當年保費(元)"] = show_year["當年保費(元)"].round(0).astype(int)
            show_year["累計(元)"] = show_year["累計(元)"].round(0).astype(int)
            st.dataframe(show_year, use_container_width=True)
        with c2:
            st.subheader("逐年×商品明細")
            show_detail = df_detail.copy()
            show_detail["當年保費(元)"] = show_detail["當年保費(元)"].round(0).astype(int)
            st.dataframe(show_detail.sort_values(["年齡", "商品代碼"]), use_container_width=True)

        st.subheader("匯出")
        csv_year = df_year.to_csv(index=False).encode("utf-8-sig")
        csv_detail = df_detail.to_csv(index=False).encode("utf-8-sig")

        st.download_button("下載：逐年合計 CSV", data=csv_year, file_name="year_sum.csv", mime="text/csv")
        st.download_button("下載：逐年×商品明細 CSV", data=csv_detail, file_name="detail_by_product.csv", mime="text/csv")

    except Exception as e:
        st.error(str(e))

st.divider()
st.caption("需要我幫你：加『上傳費率 CSV』、加『密碼登入』、加『輸出客戶版 PDF』都可以。")
