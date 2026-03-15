import streamlit as st
import base64
import os
import time
import re
import random
import json
import uuid
from datetime import datetime, timedelta, timezone
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from streamlit_drawable_canvas import st_canvas
import streamlit.components.v1 as components

# --- JST（日本標準時）の設定 ---
JST = timezone(timedelta(hours=+9), "JST")

# --- 1. st.set_page_config & CSS強化 ---
st.set_page_config(
    page_title="2027 高校入試攻略：STRATEGY",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    /* テキストはみ出し防止 & LaTeX対応 */
    .stMarkdown p { word-wrap: break-word; overflow-wrap: break-word; }
    
    /* PCでのサイドバー幅固定 */
    @media (min-width: 768px) {
        section[data-testid="stSidebar"] { min-width: 380px !important; }
    }
    
    /* 🖨️ 印刷/PDF保存用CSS：印刷時のみサイドバーを完全に消し、横幅を100%に広げる */
    @media print {
        @page { size: A4 portrait; margin: 15mm; }
        
        section[data-testid="stSidebar"], 
        header, 
        .stButton, 
        iframe, 
        div[data-testid="stToolbar"], 
        div[data-testid="stSidebarUserContent"],
        [data-testid="collapsedControl"] { 
            display: none !important; 
        }
        
        .main .block-container, 
        div[data-testid="stMainBlockContainer"],
        .stMain {
            max-width: 100% !important;
            width: 100% !important;
            padding: 0 !important;
            margin: 0 !important;
        }
        
        .print-container { width: 100%; color: black !important; background: white !important; }
        .page-break { page-break-before: always; }
        ::-webkit-scrollbar { display: none; }
    }
    </style>
""",
    unsafe_allow_html=True,
)


# --- 2. API・データ連携 ---
def get_creds():
    if "gcp_service_account" in st.secrets:
        return Credentials.from_service_account_info(
            st.secrets["gcp_service_account"],
            scopes=[
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ],
        )
    return None


def sync_timer(elapsed_to_add=0):
    try:
        sh = gspread.authorize(get_creds()).open("study_stats_db").worksheet("timer")
        records = sh.get_all_records()
        today_str = datetime.now(JST).strftime("%Y/%m/%d")
        if not records:
            sh.update_cell(1, 1, "date")
            sh.update_cell(1, 2, "seconds")
            sh.update_cell(2, 1, today_str)
            sh.update_cell(2, 2, elapsed_to_add)
            return elapsed_to_add
        db_date = str(records[0].get("date", ""))
        try:
            db_sec = int(records[0].get("seconds", 0))
        except Exception:
            db_sec = 0
        if db_date != today_str:
            sh.update_cell(2, 1, today_str)
            sh.update_cell(2, 2, elapsed_to_add)
            return elapsed_to_add
        else:
            new_sec = db_sec + elapsed_to_add
            if elapsed_to_add > 0:
                sh.update_cell(2, 2, new_sec)
            return new_sec
    except Exception:
        return st.session_state.get("daily_seconds", 0) + elapsed_to_add


# --- 3. セッション初期化 ---
def init_session():
    defaults = {
        "questions": [],
        "index": 0,
        "mode": None,
        "diff": "ミックス",
        "show_options": False,
        "show_result": False,
        "last_is_correct": False,
        "correct_count": 0,
        "current_opts": [],
        "sound_enabled": True,
        "play_this": None,
        "last_action_time": time.time(),
        "unsynced_seconds": 0,
        "print_data": None,
        "print_type": None,
        "active_mission_id": None,
        "session_results": [],
        "question_start_time": time.time(),
        "consecutive_speeding": 0,
        "is_cheating_flagged": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if "daily_seconds" not in st.session_state:
        st.session_state.daily_seconds = sync_timer(0)


init_session()

now = time.time()
elapsed = now - st.session_state.last_action_time
st.session_state.last_action_time = now
if 0 < elapsed < 600:
    st.session_state.unsynced_seconds += int(elapsed)
    st.session_state.daily_seconds += int(elapsed)
if st.session_state.unsynced_seconds >= 60:
    st.session_state.daily_seconds = sync_timer(st.session_state.unsynced_seconds)
    st.session_state.unsynced_seconds = 0


def format_time(total_seconds):
    m = total_seconds // 60
    h, rem_m = m // 60, m % 60
    return f"{h}時間{rem_m}分" if h > 0 else f"{m}分"


# --- 4. 判定・機能群 ---
def get_skip_indices(text):
    indices = set()
    if not text:
        return []
    patterns = re.findall(r"(\d+-\d+|\d+)", str(text))
    for p in patterns:
        if "-" in p:
            try:
                s, e = map(int, p.split("-"))
                indices.update(range(max(1, s), min(31, e + 1)))
            except Exception:
                continue
        else:
            try:
                val = int(p)
                if 1 <= val <= 30:
                    indices.add(val)
            except Exception:
                continue
    return sorted(list(indices))


def queue_sound(file_name):
    if st.session_state.sound_enabled:
        st.session_state.play_this = file_name


def execute_queued_sound():
    file_name = st.session_state.play_this
    if file_name and os.path.exists(file_name):
        try:
            with open(file_name, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
                st.components.v1.html(
                    f'<script>var a=new Audio("data:audio/mp3;base64,{b64}");a.play();</script>',
                    height=0,
                )
            st.session_state.play_this = None
        except Exception:
            pass


def compare_answers(u, c):
    if not u or not c:
        return False

    def norm(s):
        return re.sub(
            r"[\s\u3000\t\n\r\xa0\$\{\}\\\.,\?\!。？！\'\"、，]", "", str(s).lower()
        )

    return norm(u) == norm(c)


def parse_order_question(text, category):
    en, jp, words = str(text), "", []
    if "英語" in str(category) or "英" in str(category):
        m = re.search(r"([。？！、，])\s*([A-Za-z\(])", en)
        if m:
            jp, en = en[: m.start(1) + 1].strip(), en[m.start(1) + 1 :].strip()
    m_ans = re.search(r"\((.*?/.*?)\)", en)
    if m_ans:
        words = [w.strip() for w in m_ans.group(1).split("/") if w.strip()]
        en = en.replace(f"({m_ans.group(1)})", "{ANS}").strip()
    return en, jp, words


def find_row_by_id(worksheet, target_id):
    try:
        col = worksheet.col_values(7)
        if str(target_id) in col:
            return col.index(str(target_id)) + 1
    except Exception:
        pass
    return None


def update_db_question_master(old_cat, old_q, new_rank, new_q, new_a, new_dummy):
    try:
        sh = (
            gspread.authorize(get_creds()).open("study_stats_db").worksheet("questions")
        )
        recs = sh.get_all_records()
        for i, row in enumerate(recs):
            if str(row.get("category")) == str(old_cat) and str(row.get("q")) == str(
                old_q
            ):
                r_num = i + 2
                if new_rank is not None:
                    sh.update_cell(r_num, 2, new_rank)
                sh.update_cell(r_num, 3, new_q)
                sh.update_cell(r_num, 4, new_a)
                if new_dummy is not None:
                    sh.update_cell(r_num, 6, new_dummy)
                return True
        return False
    except Exception as e:
        st.error(e)
        return False


def batch_save_to_db(custom_mode=None, custom_qs=None):
    try:
        if st.session_state.unsynced_seconds > 0:
            st.session_state.daily_seconds = sync_timer(
                st.session_state.unsynced_seconds
            )
            st.session_state.unsynced_seconds = 0

        gc = gspread.authorize(get_creds())
        ss = gc.open("study_stats_db")

        # mastery 更新
        if st.session_state.session_results:
            try:
                sh_m = ss.worksheet("mastery")
                m_recs = sh_m.get_all_records()
            except Exception:
                sh_m = ss.add_worksheet(title="mastery", rows="1000", cols="4")
                sh_m.append_row(["category", "q", "score", "wrong_total"])
                m_recs = []

            m_dict = {
                str(r["q"]): {
                    "row": i + 2,
                    "s": int(r.get("score", 0)),
                    "w": int(r.get("wrong_total", 0)),
                }
                for i, r in enumerate(m_recs)
            }
            for res in st.session_state.session_results:
                q_txt, cat, ok = res["q"], res["cat"], res["correct"]
                if q_txt in m_dict:
                    idx_m = m_dict[q_txt]["row"]
                    ns = min(5, max(0, m_dict[q_txt]["s"] + (1 if ok else -1)))
                    nw = m_dict[q_txt]["w"] + (0 if ok else 1)
                    sh_m.update_cell(idx_m, 3, ns)
                    sh_m.update_cell(idx_m, 4, nw)
                else:
                    sh_m.append_row([cat, q_txt, 1 if ok else 0, 0 if ok else 1])
            st.session_state.session_results = []

        # history 更新
        sh_hist = ss.worksheet("history")
        mode = custom_mode if custom_mode else st.session_state.mode
        qs = (custom_qs if custom_qs else st.session_state.questions)[:30]
        today = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
        tid = st.session_state.get("active_mission_id")
        cheat = " ⚠️連続適当解答" if st.session_state.is_cheating_flagged else ""

        if not custom_qs and tid:
            rn = find_row_by_id(sh_hist, tid)
            if rn:
                att = st.session_state.index
                cor = min(st.session_state.correct_count, att)
                sc_str = (
                    f"{round((cor / att) * 100, 1)}点 ({att}問中){cheat}"
                    if att > 0
                    else "未実施"
                )
                sh_hist.update_cell(rn, 1, today)
                sh_hist.update_cell(rn, 3, sc_str)
                st.cache_data.clear()
                return True

        uid = f"id_{uuid.uuid4().hex[:8]}"
        q_json = json.dumps([q["q"] for q in qs], ensure_ascii=False)
        sh_hist.append_row([today, mode, "未実施", q_json, "", 0, uid])
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(e)
        return False


@st.cache_data(ttl=60)
def load_db():
    try:
        gc = gspread.authorize(get_creds())
        ss = gc.open("study_stats_db")
        q_rows = ss.worksheet("questions").get_all_records()
        try:
            r_v = ss.worksheet("reports").get_all_values()
        except Exception:
            r_v = []
        try:
            h_rows = ss.worksheet("history").get_all_records()
        except Exception:
            h_rows = []
        try:
            m_rows = ss.worksheet("mastery").get_all_records()
        except Exception:
            m_rows = []

        cat_agg = {}
        tc = tt = 0
        ss_dict = {}
        hr = h_rows[::-1]
        for h in hr:
            c = h.get("教科")
            s_str = str(h.get("得点", ""))
            if not c or "未実施" in s_str:
                continue
            ms, mt = (
                re.search(r"([\d\.]+)点", s_str),
                re.search(r"\((\d+)問中\)", s_str),
            )
            if ms and mt:
                sc, tot = float(ms.group(1)), int(mt.group(1))
                cor = round((sc / 100) * tot)
                cat_agg.setdefault(c, {"c": 0, "t": 0})
                cat_agg[c]["c"] += cor
                cat_agg[c]["t"] += tot
                tc += cor
                tt += tot
                ss_dict.setdefault(c, []).append(sc)

        ov = round((tc / tt) * 100, 1) if tt > 0 else 0.0
        st_list = []
        for k, v in cat_agg.items():
            r = round((v["c"] / v["t"]) * 100, 1) if v["t"] > 0 else 0.0
            df = "➖ ±0.0"
            if k in ss_dict and len(ss_dict[k]) >= 2:
                d = round(ss_dict[k][0] - ss_dict[k][1], 1)
                df = f"🔺 +{d}" if d > 0 else f"🔻 {d}"
            st_list.append(
                {
                    "カテゴリ": k,
                    "解答数": f"{v['t']}問",
                    "到達率": f"{r}%",
                    "前回比": df,
                }
            )

        org = {}
        for r in q_rows:
            c = str(r.get("category", "共通"))
            org.setdefault(c, []).append(
                {
                    "q": str(r["q"]),
                    "a": str(r["a"]),
                    "rank": str(r.get("rank", "-")),
                    "orig_cat": c,
                    "dummy": str(r.get("dummy", "")),
                }
            )
        return org, {
            "cat_stats": st_list,
            "history": hr,
            "mastery": m_rows,
            "all_ans": [str(r["a"]) for r in q_rows],
            "overall_avg": ov,
            "reports": r_v[1:],
        }
    except Exception:
        return {}, {
            "cat_stats": [],
            "history": [],
            "mastery": [],
            "all_ans": [],
            "overall_avg": 0.0,
            "reports": [],
        }


all_q, db = load_db()

# --- 5. サイドバー ---
with st.sidebar:
    st.title("📊 CURRENT STATUS")
    st.metric("⏳ 本日の稼働時間", format_time(st.session_state.daily_seconds))
    st.metric("🎯 総合到達率", f"{db.get('overall_avg', 0.0)} %")
    with st.expander("📈 カテゴリ別データ分析"):
        if db.get("cat_stats"):
            st.dataframe(pd.DataFrame(db["cat_stats"]), hide_index=True)
    if st.button("🔄 データを同期", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.divider()

    if st.session_state.print_data:
        components.html(
            """
            <button onclick="window.parent.print()" style="width:100%; background:#ff4b4b; color:white; padding:12px; border:none; border-radius:5px; font-weight:bold; cursor:pointer; font-size:16px;">
                🖨️ PDFとして保存（印刷）
            </button>
        """,
            height=60,
        )
        if st.button("⬅️ 本部へ戻る", type="primary", use_container_width=True):
            st.session_state.print_data = None
            st.session_state.print_type = None
            st.rerun()

    st.session_state.sound_enabled = st.toggle(
        "🔊 サウンド効果", value=st.session_state.sound_enabled
    )
    if st.session_state.mode:
        if st.button("🏳️ 中断してセーブ", use_container_width=True, type="primary"):
            batch_save_to_db()
            st.session_state.mode = None
            st.rerun()
        idx_s = st.session_state.index
        if idx_s < len(st.session_state.questions):
            cur = st.session_state.questions[idx_s]
            with st.expander("🚨 システム不備を報告"):
                msg = st.text_input("不備内容", key=f"rpt_{idx_s}")
                if st.button("報告を送信", key=f"btn_rpt_{idx_s}"):
                    try:
                        sh_r = (
                            gspread.authorize(get_creds())
                            .open("study_stats_db")
                            .worksheet("reports")
                        )
                        sh_r.append_row(
                            [
                                datetime.now(JST).strftime("%Y/%m/%d %H:%M"),
                                cur["orig_cat"],
                                cur["q"],
                                cur["a"],
                                msg,
                            ]
                        )
                        st.toast("報告受理")
                    except Exception as e:
                        st.error(e)

# --- 6. メインロジック ---

# A. 印刷モード
if st.session_state.print_data:
    pd_dat, pt = st.session_state.print_data, st.session_state.print_type
    st.markdown('<div class="print-container">', unsafe_allow_html=True)
    st.markdown(
        f"<h2>{'📖 問題' if pt == 'q' else '🔑 解答'}: {pd_dat['mode']}</h2>",
        unsafe_allow_html=True,
    )
    st.write(f"ID: {pd_dat['id']} | 日付: {datetime.now(JST).strftime('%Y/%m/%d')}")
    st.markdown("---")
    for i_p, q_p in enumerate(pd_dat["qs"]):
        st.markdown(f"**Mission {i_p + 1}**")
        if pt == "q":
            st.markdown(q_p["q"])
            st.markdown("<br><br><br><br>", unsafe_allow_html=True)
            st.markdown("---")
        else:
            st.markdown(f"**解答:** {q_p['a']}")
            st.markdown("---")
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# B. 本部（トップ）
if not st.session_state.mode:
    st.session_state.consecutive_speeding = 0
    st.session_state.is_cheating_flagged = False
    st.title("📖 2027 高校入試攻略：STRATEGY")

    if db.get("reports"):
        st.error("🚨 未処理のシステム報告があります！")
        for r_idx, rep in enumerate(db["reports"]):
            if len(rep) >= 5:
                with st.expander(f"⚠️ {rep[1]} ({rep[0]})"):
                    nq, na = (
                        st.text_area("問題", rep[2], key=f"rq_{r_idx}"),
                        st.text_input("正解", rep[3], key=f"ra_{r_idx}"),
                    )
                    c1, c2 = st.columns(2)
                    if c1.button(
                        "✅ 修正完了",
                        key=f"rbtn_{r_idx}",
                        type="primary",
                        use_container_width=True,
                    ):
                        if update_db_question_master(
                            rep[1], rep[2], None, nq, na, None
                        ):
                            gc = gspread.authorize(get_creds())
                            gc.open("study_stats_db").worksheet("reports").delete_rows(
                                r_idx + 2
                            )
                            st.cache_data.clear()
                            st.rerun()
                    if c2.button(
                        "🗑️ 抹消", key=f"dbtn_{r_idx}", use_container_width=True
                    ):
                        gc = gspread.authorize(get_creds())
                        sh_q = gc.open("study_stats_db").worksheet("questions")
                        recs_q = sh_q.get_all_records()
                        for i_q, r_q in enumerate(recs_q):
                            if str(r_q.get("category")) == str(rep[1]) and str(
                                r_q.get("q")
                            ) == str(rep[2]):
                                sh_q.delete_rows(i_q + 2)
                                break
                        gc.open("study_stats_db").worksheet("reports").delete_rows(
                            r_idx + 2
                        )
                        st.cache_data.clear()
                        st.rerun()

    c1, c2 = st.columns(2)
    with c1:
        with st.expander("🚀 通常ミッション（学年選択）", expanded=(not db["history"])):
            # 教科リストの更新
            subj = st.selectbox(
                "教科", ["数学", "英語", "理科", "地理", "歴史", "現代文", "古文・漢文"]
            )
            year = st.radio("範囲", ["1年", "2年", "総合"], horizontal=True)
            if st.button("ミッション生成", use_container_width=True, type="primary"):
                mastered = {
                    r["q"] for r in db["mastery"] if int(r.get("score", 0)) >= 5
                }
                pool = []
                for cat_key, qs_list in all_q.items():
                    if subj in cat_key:
                        if year == "総合" or year in cat_key:
                            pool.extend([q for q in qs_list if q["q"] not in mastered])

                pA, pB, pC = (
                    [q for q in pool if q.get("rank") == "A"],
                    [q for q in pool if q.get("rank") == "B"],
                    [q for q in pool if q.get("rank") == "C"],
                )
                random.seed(time.time_ns())
                random.shuffle(pA)
                random.shuffle(pB)
                random.shuffle(pC)
                final = pA[:15] + pB[:12] + pC[:3]
                if len(final) < 30:
                    others = [q for q in pool if q not in final]
                    random.shuffle(others)
                    final.extend(others[: 30 - len(final)])
                random.shuffle(final)
                if batch_save_to_db(custom_mode=f"{year}{subj}", custom_qs=final):
                    st.rerun()

    with c2:
        with st.expander("🔥 弱点克服（ミス復習）"):
            w_subj = st.selectbox(
                "教科選択",
                ["数学", "英語", "理科", "地理", "歴史", "現代文", "古文・漢文"],
                key="w_s",
            )
            if st.button("弱点特訓開始", use_container_width=True):
                weak_txts = {
                    r["q"]
                    for r in db["mastery"]
                    if int(r.get("score", 0)) < 5 and int(r.get("wrong_total", 0)) >= 1
                }
                w_pool = []
                for cat_key, qs_list in all_q.items():
                    if w_subj in cat_key:
                        w_pool.extend([q for q in qs_list if q["q"] in weak_txts])
                random.seed(time.time_ns())
                random.shuffle(w_pool)
                if batch_save_to_db(
                    custom_mode=f"復習-{w_subj}", custom_qs=w_pool[:30]
                ):
                    st.rerun()

    st.divider()
    st.subheader("📅 MISSION LOG")
    h_list = db["history"]
    if h_list:
        now_d = datetime.now(JST).date()
        start_w = now_d - timedelta(days=now_d.weekday())
        gps = {"📌 今週": [], "📌 先週": [], "📌 アーカイブ": []}
        for h in h_list:
            try:
                dt = datetime.strptime(h["日付"].split()[0], "%Y/%m/%d").date()
                if dt >= start_w:
                    gps["📌 今週"].append(h)
                elif dt >= start_w - timedelta(days=7):
                    gps["📌 先週"].append(h)
                else:
                    gps["📌 アーカイブ"].append(h)
            except Exception:
                gps["📌 アーカイブ"].append(h)

        for lbl, items in gps.items():
            if items:
                with st.expander(
                    f"{lbl} ({len(items)}件)", expanded=(lbl == "📌 今週")
                ):
                    for h in items:
                        tid = h.get("ID")
                        with st.container(border=True):
                            c1, c2, c3, c4, c5 = st.columns([2.5, 1, 0.8, 0.8, 0.4])
                            c1.markdown(
                                f"**{h['日付']}** | 🆔 `{tid}`<br>{h['教科']} ({h['得点']})",
                                unsafe_allow_html=True,
                            )
                            if c2.button(
                                "🔄 特訓", key=f"go_{tid}", use_container_width=True
                            ):
                                st.session_state.active_mission_id = tid
                                skip = get_skip_indices(
                                    st.session_state.get(f"mi_{tid}", h.get("メモ", ""))
                                )
                                flat = [q for sl in all_q.values() for q in sl]
                                s_txts = json.loads(h.get("問題リスト(JSON)", "[]"))
                                b_qs = [
                                    next(q for q in flat if q["q"] == t)
                                    for t in s_txts
                                    if any(q["q"] == t for q in flat)
                                ]
                                st.session_state.questions = [
                                    q
                                    for i, q in enumerate(b_qs[:30])
                                    if (i + 1) not in skip
                                ]
                                st.session_state.index = 0
                                st.session_state.correct_count = 0
                                st.session_state.question_start_time = time.time()
                                st.session_state.mode = h["教科"]
                                st.rerun()
                            if c3.button("📄 問題", key=f"pq_{tid}"):
                                flat = [q for sl in all_q.values() for q in sl]
                                skip = get_skip_indices(
                                    st.session_state.get(f"mi_{tid}", h.get("メモ", ""))
                                )
                                b_qs = [
                                    next(q for q in flat if q["q"] == t)
                                    for t in json.loads(h.get("問題リスト(JSON)", "[]"))
                                    if any(q["q"] == t for q in flat)
                                ]
                                st.session_state.print_data = {
                                    "mode": h["教科"],
                                    "id": tid,
                                    "qs": [
                                        q
                                        for i, q in enumerate(b_qs[:30])
                                        if (i + 1) not in skip
                                    ],
                                }
                                st.session_state.print_type = "q"
                                st.rerun()
                            if c4.button("🔑 解答", key=f"pa_{tid}"):
                                flat = [q for sl in all_q.values() for q in sl]
                                skip = get_skip_indices(
                                    st.session_state.get(f"mi_{tid}", h.get("メモ", ""))
                                )
                                b_qs = [
                                    next(q for q in flat if q["q"] == t)
                                    for t in json.loads(h.get("問題リスト(JSON)", "[]"))
                                    if any(q["q"] == t for q in flat)
                                ]
                                st.session_state.print_data = {
                                    "mode": h["教科"],
                                    "id": tid,
                                    "qs": [
                                        q
                                        for i, q in enumerate(b_qs[:30])
                                        if (i + 1) not in skip
                                    ],
                                }
                                st.session_state.print_type = "a"
                                st.rerun()
                            if c5.button("🗑️", key=f"dl_{tid}"):
                                sh = (
                                    gspread.authorize(get_creds())
                                    .open("study_stats_db")
                                    .worksheet("history")
                                )
                                rown = find_row_by_id(sh, tid)
                                if rown:
                                    sh.delete_rows(rown)
                                    st.cache_data.clear()
                                    st.rerun()
                            mv = st.text_input(
                                "除外メモ", value=h.get("メモ", ""), key=f"mi_{tid}"
                            )
                            if st.button("💾 保存", key=f"sv_{tid}"):
                                sh = (
                                    gspread.authorize(get_creds())
                                    .open("study_stats_db")
                                    .worksheet("history")
                                )
                                rown = find_row_by_id(sh, tid)
                                if rown:
                                    sh.update_cell(rown, 5, mv)
                                    st.cache_data.clear()
                                    st.toast("保存済")

# C. 特訓中
else:
    idx, qs = st.session_state.index, st.session_state.questions
    if idx >= len(qs):
        st.balloons()
        st.title("MISSION COMPLETE!")
        sc = (
            round((st.session_state.correct_count / len(qs)) * 100, 1)
            if len(qs) > 0
            else 0.0
        )
        st.markdown(f"# 到達率: {sc}%")
        if st.session_state.is_cheating_flagged:
            st.error("⚠️ 警告：連続で極端に早いスキップが検知されました。")
        if st.button("本部へ戻る", type="primary", use_container_width=True):
            batch_save_to_db()
            st.session_state.mode = None
            st.session_state.active_mission_id = None
            st.rerun()
    else:
        q = qs[idx]
        st.caption(f"Mission {idx + 1}/{len(qs)} | ⭕️ {st.session_state.correct_count}")
        en, jp, ow = parse_order_question(q["q"], q["orig_cat"])
        st.markdown(f"### {en if not ow else '( ' + ' / '.join(ow) + ' )'}")
        if jp:
            st.markdown(f"#### {jp}")

        tool = st.radio(
            "Tool",
            ["🖋️ ペン", "🧽 消しゴム"],
            horizontal=True,
            label_visibility="collapsed",
            key=f"tl_{idx}",
        )
        p_color, p_width = ("#000000", 5) if tool == "🖋️ ペン" else ("#f8f9fb", 35)
        st_canvas(
            stroke_width=p_width,
            stroke_color=p_color,
            height=300,
            width=800,
            key=f"cv_{idx}",
            background_color="#f8f9fb",
        )

        if st.session_state.show_result:
            if st.session_state.last_is_correct:
                st.success(f"SUCCESS: {q['a']}")
            else:
                st.error(f"FAILURE: {q['a']}")
            if st.button("次へ進む ➡️", use_container_width=True):
                st.session_state.index += 1
                st.session_state.show_result = False
                st.session_state.show_options = False
                st.session_state.current_opts = []
                st.session_state.question_start_time = time.time()
                st.rerun()
        elif st.session_state.show_options:
            if not st.session_state.current_opts:
                opts = [str(q["a"])]
                ds = [
                    d.strip() for d in str(q.get("dummy", "")).split(",") if d.strip()
                ]
                opts.extend(ds[:3])
                others = random.sample(db["all_ans"], min(len(db["all_ans"]), 10))
                opts.extend(
                    [str(o) for o in others if str(o) != str(q["a"])][: 4 - len(opts)]
                )
                random.shuffle(opts)
                st.session_state.current_opts = opts
            cols = st.columns(len(st.session_state.current_opts))
            for i, o in enumerate(st.session_state.current_opts):
                if cols[i].button(str(o), key=f"opt_{i}", use_container_width=True):
                    ok = compare_answers(o, q["a"])
                    queue_sound("correct.mp3" if ok else "wrong.mp3")
                    st.session_state.last_is_correct = ok
                    if ok:
                        st.session_state.correct_count += 1
                    st.session_state.session_results.append(
                        {"q": q["q"], "cat": q["orig_cat"], "correct": ok}
                    )
                    st.session_state.show_result = True
                    st.rerun()
        else:
            if st.button(
                "判定 ＆ オプション表示", use_container_width=True, type="primary"
            ):
                if time.time() - st.session_state.question_start_time < 5.0:
                    st.session_state.consecutive_speeding += 1
                    if st.session_state.consecutive_speeding >= 3:
                        st.session_state.is_cheating_flagged = True
                else:
                    st.session_state.consecutive_speeding = 0
                st.session_state.show_options = True
                st.rerun()

execute_queued_sound()
