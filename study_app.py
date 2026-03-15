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

# --- JST（日本標準時）の設定 ---
JST = timezone(timedelta(hours=+9), "JST")

# --- 1. st.set_page_config & UIレイアウト設定 ---
st.set_page_config(
    page_title="2027 高校入試攻略：STRATEGY",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    section[data-testid="stSidebar"] { min-width: 380px !important; }
    @media print {
        @page { size: A4 portrait; margin: 15mm; }
        .no-print, .stButton, div[data-testid="stSidebar"], header { display: none !important; }
        .print-container { width: 100%; color: black !important; background: white !important; }
        .page-break { page-break-before: always; }
        ::-webkit-scrollbar { display: none; }
    }
    </style>
""",
    unsafe_allow_html=True,
)


# --- 2. API・データ連携ロジック ---
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


# --- 3. セッション・タイマー初期化 ---
def init_session():
    defaults = {
        "questions": [],
        "index": 0,
        "mode": None,
        "diff": "ミックス",
        "show_options": False,
        "show_result": False,
        "last_is_correct": False,
        "user_ans_list": [],
        "retry_count": 0,
        "session_streak": 0,
        "correct_count": 0,
        "all_ans_pool": [],
        "current_opts": [],
        "results_buffer": [],
        "sound_enabled": True,
        "play_this": None,
        "last_action_time": time.time(),
        "unsynced_seconds": 0,
        "print_data": None,
        "print_type": None,
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


# --- 4. 判定・サウンド・DB操作 ---
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
                sh.update_cell(r_num, 2, new_rank)
                sh.update_cell(r_num, 3, new_q)
                sh.update_cell(r_num, 4, new_a)
                sh.update_cell(r_num, 6, new_dummy)
                return True
        return False
    except Exception as e:
        st.error(e)
        return False


def delete_db_question(target_cat, target_q):
    try:
        sh = (
            gspread.authorize(get_creds()).open("study_stats_db").worksheet("questions")
        )
        recs = sh.get_all_records()
        for i, row in enumerate(recs):
            if str(row.get("category")) == str(target_cat) and str(row.get("q")) == str(
                target_q
            ):
                sh.delete_rows(i + 2)
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
        ss = gspread.authorize(get_creds()).open("study_stats_db")
        sh_hist = ss.worksheet("history")
        uid = f"id_{uuid.uuid4().hex[:8]}"
        mode = custom_mode if custom_mode else st.session_state.mode
        qs = (custom_qs if custom_qs else st.session_state.questions)[:30]
        today_dt = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
        q_json = json.dumps([q["q"] for q in qs], ensure_ascii=False)
        if custom_qs:
            row = [today_dt, mode, "未実施", q_json, "", 0, uid]
        else:
            total = len(qs)
            correct = min(st.session_state.correct_count, total)
            score = round((correct / total) * 100, 1) if total > 0 else 0.0
            row = [today_dt, mode, f"{score}点 ({total}問中)", q_json, "", 0, uid]
        sh_hist.append_row(row)
        st.cache_data.clear()
        return True
    except Exception as e:
        st.error(e)
        return False


def extract_score(s):
    m = re.search(r"([\d\.]+)点", str(s))
    return float(m.group(1)) if m else None


@st.cache_data(ttl=60)
def load_db():
    try:
        gc = gspread.authorize(get_creds())
        ss = gc.open("study_stats_db")
        m_rows, q_rows = (
            ss.sheet1.get_all_records(),
            ss.worksheet("questions").get_all_records(),
        )
        cat_agg = {}
        t_c = t_t = 0
        for r in m_rows:
            cat = str(r.get("subject", "その他"))
            c, w = int(r.get("correct", 0)), int(r.get("wrong", 0))
            cat_agg.setdefault(cat, {"c": 0, "t": 0})
            cat_agg[cat]["c"] += c
            cat_agg[cat]["t"] += c + w
            t_c += c
            t_t += c + w
        overall_avg = round((t_c / t_t) * 100, 1) if t_t > 0 else 0.0
        try:
            h_rows = ss.worksheet("history").get_all_records()
        except Exception:
            h_rows = []
        h_recs = h_rows[::-1]
        s_scores = {}
        for h in h_recs:
            cat, sc = h.get("教科"), extract_score(h.get("得点", ""))
            if cat and sc is not None:
                s_scores.setdefault(cat, []).append(sc)
        cat_stats = []
        for k, v in cat_agg.items():
            rate = round((v["c"] / v["t"]) * 100, 1) if v["t"] > 0 else 0.0
            diff_s = "➖ ±0.0"
            if k in s_scores and len(s_scores[k]) >= 2:
                d = round(s_scores[k][0] - s_scores[k][1], 1)
                diff_s = f"🔺 +{d}" if d > 0 else f"🔻 {d}"
            cat_stats.append(
                {
                    "カテゴリ": k,
                    "解析数": f"{v['t']}問",
                    "到達率": f"{rate}%",
                    "前回比": diff_s,
                }
            )
        org = {}
        for r in q_rows:
            cat = str(r.get("category", "共通"))
            org.setdefault(cat, []).append(
                {
                    "q": str(r["q"]),
                    "a": str(r["a"]),
                    "rank": str(r.get("rank", "-")),
                    "orig_cat": cat,
                    "dummy": str(r.get("dummy", "")),
                }
            )
        return org, {
            "cat_stats": cat_stats,
            "history": h_recs,
            "all_ans": [str(r["a"]) for r in q_rows],
            "overall_avg": overall_avg,
        }
    except Exception:
        return {}, {"cat_stats": [], "history": [], "all_ans": [], "overall_avg": 0.0}


all_q, db = load_db()


def get_skip_indices(text):
    indices = set()
    for p in re.split(r"[,\s]+", str(text)):
        if "-" in p:
            try:
                s, e = map(int, p.split("-"))
                indices.update(range(s, e + 1))
            except Exception:
                continue
        elif p.isdigit():
            indices.add(int(p))
    return sorted(list(indices))


# --- 5. サイドバー (STRATEGY UI) ---
with st.sidebar:
    st.title("📊 CURRENT STATUS")
    st.metric("⏳ 本日の稼働時間", format_time(st.session_state.daily_seconds))
    st.metric("🎯 総合到達率", f"{db.get('overall_avg', 0.0)} %")
    with st.expander("📈 カテゴリ別データ分析", expanded=False):
        if db.get("cat_stats"):
            st.dataframe(pd.DataFrame(db["cat_stats"]), hide_index=True)
    if st.button("🔄 データを同期", use_container_width=True):
        st.cache_data.clear()
        st.rerun()
    st.divider()
    st.session_state.sound_enabled = st.toggle(
        "🔊 サウンド効果", value=st.session_state.sound_enabled
    )
    if st.session_state.print_data:
        if st.button("⬅️ 本部へ戻る", type="primary", use_container_width=True):
            st.session_state.print_data = None
            st.session_state.print_type = None
            st.rerun()
    if st.session_state.mode:
        if st.button("🏳️ 中断してセーブ", use_container_width=True, type="primary"):
            batch_save_to_db()
            st.session_state.mode = None
            st.rerun()
        idx = st.session_state.index
        if idx < len(st.session_state.questions):
            cur = st.session_state.questions[idx]
            with st.expander("🚨 システム不備を報告"):
                msg = st.text_input("不備内容", key=f"rpt_{idx}")
                if st.button("報告を送信", key=f"btn_rpt_{idx}"):
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
                        st.toast("報告を受理しました")
                    except Exception as e:
                        st.error(e)
            st.divider()
            if st.checkbox("⚙️ システム・エディタ"):
                nq = st.text_area("問題を編集", value=cur["q"], key=f"edit_q_{idx}")
                na = st.text_input("正解を編集", value=cur["a"], key=f"edit_a_{idx}")
                nr = st.text_input("ランク編集", value=cur["rank"], key=f"edit_r_{idx}")
                nd = st.text_input(
                    "ダミー編集", value=cur.get("dummy", ""), key=f"edit_d_{idx}"
                )
                c1, c2 = st.columns(2)
                if c1.button("✅ 適用"):
                    if update_db_question_master(
                        cur["orig_cat"], cur["q"], nr, nq, na, nd
                    ):
                        st.session_state.questions[idx].update(
                            {"q": nq, "a": na, "rank": nr, "dummy": nd}
                        )
                        st.session_state.current_opts = []
                        st.cache_data.clear()
                        st.success("適用済")
                        time.sleep(0.5)
                        st.rerun()
                if c2.button("🗑️ 削除"):
                    if delete_db_question(cur["orig_cat"], cur["q"]):
                        st.session_state.questions.pop(idx)
                        st.cache_data.clear()
                        st.success("削除済")
                        time.sleep(0.5)
                        st.rerun()

# --- 6. 画面制御ロジック ---

# A. 印刷用表示モード
if st.session_state.print_data:
    pd_dat, pt, t_str = (
        st.session_state.print_data,
        st.session_state.print_type,
        datetime.now(JST).strftime("%Y/%m/%d"),
    )
    st.markdown('<div class="print-container">', unsafe_allow_html=True)
    if pt == "q":
        st.markdown(
            f"<h2>📖 2027 高校入試対策 実力テスト: {pd_dat['mode']}</h2>",
            unsafe_allow_html=True,
        )
        st.write(
            f"実施日: {t_str} 　　管理ID: {pd_dat['id']} 　　氏名: __________________________"
        )
        st.markdown("<hr/>", unsafe_allow_html=True)
        for i, q in enumerate(pd_dat["qs"]):
            st.markdown(f"**Mission {i + 1}**")
            st.markdown(q["q"])
            st.markdown(
                '<div style="height: 60px; border-bottom: 1px solid #ddd; margin-bottom: 30px;"></div>',
                unsafe_allow_html=True,
            )
    else:
        st.markdown(f"<h2>🔑 解答キー: {pd_dat['mode']}</h2>", unsafe_allow_html=True)
        st.write(f"出力日: {t_str} 　　管理ID: {pd_dat['id']}")
        st.markdown("<hr/>", unsafe_allow_html=True)
        cols = st.columns(3)
        for i, q in enumerate(pd_dat["qs"]):
            with cols[i % 3]:
                st.markdown(f"**Mission {i + 1}**")
                st.markdown(
                    f"<span style='font-size: 13pt; font-weight: bold; color: #d32f2f;'>{q['a']}</span>",
                    unsafe_allow_html=True,
                )
                st.markdown('<div style="height: 15px;"></div>', unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)
    st.stop()

# B. メイン画面
if not st.session_state.mode:
    st.title("📖 2027 高校入試攻略：STRATEGY")
    with st.expander(
        "🚀 新規ミッションをロード（30問選出）", expanded=(not db["history"])
    ):
        sub = st.selectbox(
            "対象カテゴリ", ["数学総合", "英語総合"] + sorted(list(all_q.keys()))
        )
        if st.button(
            "ミッションを生成して保存", use_container_width=True, type="primary"
        ):
            target = [sub]
            if sub == "数学総合":
                target = [k for k in all_q.keys() if "数学" in k]
            elif sub == "英語総合":
                target = [k for k in all_q.keys() if "英語" in k]
            pool = []
            for c in target:
                pool.extend(all_q.get(c, []))

            # 【強化パッチ】実行のたびに完全に異なる乱数シードを設定
            random.seed(time.time_ns())
            random.shuffle(pool)

            if batch_save_to_db(custom_mode=sub, custom_qs=pool[:30]):
                st.rerun()

    st.divider()

    # --- 週間グループ化ロジック (JST日付ベースの厳密判定・日曜対応版) ---
    st.subheader("📅 MISSION LOG")
    h_list = db["history"]

    if not h_list:
        st.info("まだミッション履歴がありません。")
    else:
        now_dt = datetime.now(JST)
        now_date = now_dt.date()
        # 今週の月曜日の00:00:00を取得
        start_of_week = now_date - timedelta(days=now_date.weekday())
        # 先週の月曜日
        start_of_last_week = start_of_week - timedelta(days=7)

        groups = {
            "📌 今週のミッション": [],
            "📌 先週のミッション": [],
            "📌 それ以前のアーカイブ": [],
        }

        for h in h_list:
            try:
                h_date = datetime.strptime(h["日付"].split()[0], "%Y/%m/%d").date()
                if h_date >= start_of_week:
                    groups["📌 今週のミッション"].append(h)
                elif h_date >= start_of_last_week:
                    groups["📌 先週のミッション"].append(h)
                else:
                    groups["📌 それ以前のアーカイブ"].append(h)
            except Exception:
                groups["📌 それ以前のアーカイブ"].append(h)

        for label, items in groups.items():
            if items:
                with st.expander(
                    f"{label} ({len(items)}件)",
                    expanded=(label == "📌 今週のミッション"),
                ):
                    for h in items:
                        t_id = h.get("ID")
                        if not t_id:
                            continue
                        with st.container(border=True):
                            m_val, s_txts, r_cnt = (
                                h.get("メモ", ""),
                                json.loads(h.get("問題リスト(JSON)", "[]")),
                                h.get("復習回数", 0),
                            )
                            if not str(r_cnt).isdigit():
                                r_cnt = 0
                            score_val = h.get("得点", "未実施")
                            curr_s = extract_score(score_val)

                            diff_h = " <span style='color:gray; font-weight:bold;'>[±0.0]</span>"
                            if curr_s is not None:
                                for old_h in h_list[h_list.index(h) + 1 :]:
                                    if old_h.get("教科") == h.get("教科"):
                                        prv_s = extract_score(old_h.get("得点", ""))
                                        if prv_s is not None:
                                            d = round(curr_s - prv_s, 1)
                                            diff_h = (
                                                f" <span style='color:#d32f2f; font-weight:bold;'>[+{d}]</span>"
                                                if d > 0
                                                else f" <span style='color:#1976d2; font-weight:bold;'>[{d}]</span>"
                                            )
                                            break

                            c1, c2, c3, c4, c5 = st.columns([2.5, 1, 0.8, 0.8, 0.4])
                            c1.markdown(
                                f"**{h['日付']}** | 🔁 {r_cnt}回 | 🆔 `{t_id}`<br>{h['教科']} ({score_val}){diff_h}",
                                unsafe_allow_html=True,
                            )

                            if c2.button(
                                "🔄 特訓を開始",
                                key=f"go_{t_id}",
                                use_container_width=True,
                            ):
                                st.session_state.correct_count = 0
                                st.session_state.index = 0
                                skip = get_skip_indices(
                                    st.session_state.get(f"mi_{t_id}", m_val)
                                )
                                flat = [
                                    item
                                    for sublist in all_q.values()
                                    for item in sublist
                                ]
                                b_qs = [
                                    next(q for q in flat if q["q"] == t)
                                    for t in s_txts
                                    if any(q["q"] == t for q in flat)
                                ]
                                st.session_state.questions = [
                                    q
                                    for idx, q in enumerate(b_qs[:30])
                                    if (idx + 1) not in skip
                                ]
                                st.session_state.mode = h["教科"]
                                st.rerun()

                            if c3.button(
                                "📄 問題出力",
                                key=f"pq_{t_id}",
                                use_container_width=True,
                            ):
                                flat = [
                                    item
                                    for sublist in all_q.values()
                                    for item in sublist
                                ]
                                skip = get_skip_indices(
                                    st.session_state.get(f"mi_{t_id}", m_val)
                                )
                                b_qs = [
                                    next(q for q in flat if q["q"] == t)
                                    for t in s_txts
                                    if any(q["q"] == t for q in flat)
                                ]
                                st.session_state.print_data = {
                                    "date": h["日付"],
                                    "mode": h["教科"],
                                    "id": t_id,
                                    "qs": [
                                        q
                                        for idx, q in enumerate(b_qs[:30])
                                        if (idx + 1) not in skip
                                    ],
                                }
                                st.session_state.print_type = "q"
                                st.rerun()

                            if c4.button(
                                "🔑 解答出力",
                                key=f"pa_{t_id}",
                                use_container_width=True,
                            ):
                                flat = [
                                    item
                                    for sublist in all_q.values()
                                    for item in sublist
                                ]
                                skip = get_skip_indices(
                                    st.session_state.get(f"mi_{t_id}", m_val)
                                )
                                b_qs = [
                                    next(q for q in flat if q["q"] == t)
                                    for t in s_txts
                                    if any(q["q"] == t for q in flat)
                                ]
                                st.session_state.print_data = {
                                    "date": h["日付"],
                                    "mode": h["教科"],
                                    "id": t_id,
                                    "qs": [
                                        q
                                        for idx, q in enumerate(b_qs[:30])
                                        if (idx + 1) not in skip
                                    ],
                                }
                                st.session_state.print_type = "a"
                                st.rerun()

                            if c5.button("🗑️", key=f"dl_{t_id}"):
                                try:
                                    gc = (
                                        gspread.authorize(get_creds())
                                        .open("study_stats_db")
                                        .worksheet("history")
                                    )
                                    idx = find_row_by_id(gc, t_id)
                                    if idx:
                                        gc.delete_rows(idx)
                                        st.cache_data.clear()
                                        st.rerun()
                                except Exception:
                                    pass

                            m_c1, m_c2 = st.columns([4, 1])
                            new_m = m_c1.text_input(
                                "除外ミッション (例: 1-5, 12)",
                                value=m_val,
                                key=f"mi_{t_id}",
                                label_visibility="collapsed",
                            )
                            if m_c2.button(
                                "💾 保存", key=f"sv_{t_id}", use_container_width=True
                            ):
                                try:
                                    gc = (
                                        gspread.authorize(get_creds())
                                        .open("study_stats_db")
                                        .worksheet("history")
                                    )
                                    idx = find_row_by_id(gc, t_id)
                                    if idx:
                                        gc.update_cell(idx, 5, new_m)
                                        st.cache_data.clear()
                                        st.toast("セーブしました")
                                        time.sleep(0.5)
                                        st.rerun()
                                except Exception as e:
                                    st.error(e)

# C. デジタル特訓中
else:
    idx, qs = st.session_state.index, st.session_state.questions
    if idx >= len(qs):
        st.balloons()
        st.title("ミッション・コンプリート！")
        sc = (
            round((st.session_state.correct_count / len(qs)) * 100, 1)
            if len(qs) > 0
            else 0.0
        )
        st.markdown(f"# 今回の到達率: {sc}%")
        if st.button(
            "記録を同期して本部へ戻る", type="primary", use_container_width=True
        ):
            batch_save_to_db()
            st.session_state.mode = None
            st.rerun()
    else:
        q = qs[idx]
        st.caption(
            f"Mission {idx + 1} / {len(qs)} | ⭕️ {st.session_state.correct_count} SUCCESS"
        )
        en, jp, ow = parse_order_question(q["q"], q["orig_cat"])
        st.markdown(f"### {en if not ow else '( ' + ' / '.join(ow) + ' )'}")
        if jp:
            st.markdown(f"#### {jp}")
        st_canvas(
            stroke_width=5,
            height=300,
            width=800,
            key=f"cv_{idx}",
            background_color="#f8f9fb",
            update_streamlit=False,
        )
        if st.session_state.show_result:
            if st.session_state.last_is_correct:
                st.success(f"SUCCESS: {q['a']}")
            else:
                st.error(f"FAILURE: 正解は {q['a']}")
            if st.button("次へ進む ➡️", type="primary", use_container_width=True):
                st.session_state.index += 1
                st.session_state.show_result = False
                st.session_state.show_options = False
                st.session_state.current_opts = []
                st.rerun()
        elif st.session_state.show_options:
            if not st.session_state.current_opts:
                opts = [str(q["a"])]
                d_list = [
                    d.strip() for d in str(q.get("dummy", "")).split(",") if d.strip()
                ]
                opts.extend(d_list[:3])
                if len(opts) < 4:
                    others = random.sample(db["all_ans"], min(len(db["all_ans"]), 10))
                    opts.extend(
                        [str(o) for o in others if str(o) != str(q["a"])][
                            : 4 - len(opts)
                        ]
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
                    st.session_state.show_result = True
                    st.rerun()
        else:
            if st.button(
                "判定 ＆ オプションを表示", type="primary", use_container_width=True
            ):
                st.session_state.show_options = True
                st.rerun()

execute_queued_sound()
