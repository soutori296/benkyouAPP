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

RANK_LABELS = {"A": "🟢 基本", "B": "🟡 発展", "C": "🔴 上級"}

# --- JST（日本標準時）の設定 ---
JST = timezone(timedelta(hours=+9), "JST")

# --- 1. st.set_page_config & CSS (PDF・5mロール紙最適化版) ---
st.set_page_config(
    page_title="2027 高校入試攻略：STRATEGY",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    .stMarkdown p { word-wrap: break-word; overflow-wrap: break-word; line-height: 1.7; }
    .katex { font-size: 1.1em !important; }

    /* 🔑 解答ロック解除キーを「●」にする設定 */
    input[aria-label="🗝️ 解答ロック解除キー"] {
        -webkit-text-security: disc;
    } /* 💡 ここに閉じカッコが必要でした */

    /* 📏 サイドバーの幅設定（ここを書き換えました） */
    [data-testid="stSidebar"] {
        min-width: 260px !important; /* デフォルトより狭い260pxに固定 */
        max-width: 260px !important;
    }

    /* 🖨️ 印刷用設定 */
    @media print {
        @page { size: 210mm 5000mm; margin: 15mm; }
        
        section[data-testid="stSidebar"], header, .stButton, iframe, 
        div[data-testid="stToolbar"], div[data-testid="stSidebarUserContent"], 
        [data-testid="collapsedControl"] { 
            display: none !important; 
        }
        
        .main .block-container, div[data-testid="stMainBlockContainer"], .stMain { 
            max-width: 100% !important; width: 100% !important; padding: 0 !important; margin: 0 !important;
        }

        .answer-box { 
            border: none; 
            height: 80px; 
            width: 100%; 
            margin-top: 5px;
            margin-bottom: 15px;
            background: #fff;
        }
    }
    </style>
""",
    unsafe_allow_html=True,
)

# --- 2. 共通・ユーティリティ関数 ---


def get_creds():
    try:
        if "gcp_service_account" in st.secrets:
            return Credentials.from_service_account_info(
                st.secrets["gcp_service_account"],
                scopes=[
                    "https://www.googleapis.com/auth/spreadsheets",
                    "https://www.googleapis.com/auth/drive",
                ],
            )
    except Exception:
        pass
    return None


def format_time(total_seconds):
    m = total_seconds // 60
    h, rem_m = m // 60, m % 60
    return f"{h}時間{rem_m}分" if h > 0 else f"{m}分"


def sync_timer(elapsed_to_add=0):
    try:
        sh = gspread.authorize(get_creds()).open("study_stats_db").worksheet("timer")
        records = sh.get_all_records()
        today_str = datetime.now(JST).strftime("%Y/%m/%d")

        if not records:
            sh.update_cell(1, 1, "date")
            sh.update_cell(1, 2, "seconds")
            sh.update_cell(1, 3, "total")
            sh.update_cell(2, 1, today_str)
            sh.update_cell(2, 2, elapsed_to_add)
            sh.update_cell(2, 3, elapsed_to_add)
            st.session_state.total_seconds = elapsed_to_add
            return elapsed_to_add

        db_date = str(records[0].get("date", ""))
        try:
            db_sec = int(records[0].get("seconds", 0))
        except Exception:
            db_sec = 0

        # 💡 総時間（total）の読み込みと、列がない場合の自動作成
        if "total" not in records[0]:
            sh.update_cell(1, 3, "total")
            db_total = db_sec
        else:
            try:
                db_total = int(records[0].get("total", 0))
            except Exception:
                db_total = db_sec

        if db_date != today_str:
            sh.update_cell(2, 1, today_str)
            sh.update_cell(2, 2, elapsed_to_add)
            new_total = db_total + elapsed_to_add
            sh.update_cell(2, 3, new_total)
            st.session_state.total_seconds = new_total
            return elapsed_to_add
        else:
            new_sec = db_sec + elapsed_to_add
            new_total = db_total + elapsed_to_add
            if elapsed_to_add > 0:
                sh.update_cell(2, 2, new_sec)
                sh.update_cell(2, 3, new_total)
            st.session_state.total_seconds = new_total
            return new_sec
    except Exception:
        st.session_state.total_seconds = (
            st.session_state.get("total_seconds", 0) + elapsed_to_add
        )
        return st.session_state.get("daily_seconds", 0) + elapsed_to_add


def queue_sound(file_name):
    if st.session_state.get("sound_enabled", True):
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
    try:

        def norm(s):
            return re.sub(
                r"[\s\u3000\t\n\r\xa0\$\{\}\\\.,\?\!。？！\'\"、，]", "", str(s).lower()
            )

        return norm(u) == norm(c)
    except Exception:
        return False


def parse_order_question(text, category):
    raw = str(text).strip()
    en, jp, choices_in_q = raw, "", []
    try:
        if "数学" in str(category):
            return raw, "", []

        # 💡 修正：英語の時だけ、半角(英語)と全角(日本語訳)を分割する
        if "英語" in str(category):
            m_jp = re.search(r"[^\x00-\x7F]+", raw)
            if m_jp:
                split_idx = m_jp.start()
                if split_idx > 0:
                    en, jp = raw[:split_idx].strip(), raw[split_idx:].strip()
                else:
                    m_end = re.search(r"([。？！])", raw)
                    if m_end:
                        sp = m_end.end()
                        jp, en = raw[:sp].strip(), raw[sp:].strip()
        else:
            # 英語以外（社会や理科）は分割せずにそのまま1つの文として扱う
            en = raw

        # カッコ内の選択肢 (A/B/C) や (ア/イ/ウ) を抽出
        matches = re.findall(r"\(([^)]*?/[^)]*?)\)", en)
        for m in matches:
            choices_in_q.extend([w.strip() for w in m.split("/") if w.strip()])
    except Exception:
        pass
    return en, jp, choices_in_q


def get_skip_indices(text):
    indices = set()
    if not text:
        return []
    try:
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
    except Exception:
        pass
    return sorted(list(indices))


def find_row_by_id(ws, tid):
    try:
        col = ws.col_values(7)
        if str(tid) in col:
            return col.index(str(tid)) + 1
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
                rn = i + 2
                if new_rank is not None:
                    sh.update_cell(rn, 2, new_rank)
                sh.update_cell(rn, 3, new_q)
                sh.update_cell(rn, 4, new_a)
                if new_dummy is not None:
                    sh.update_cell(rn, 6, new_dummy)
                return True
        return False
    except Exception:
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

        sh_hist = ss.worksheet("history")
        mode = custom_mode if custom_mode else st.session_state.mode
        qs = (custom_qs if custom_qs else st.session_state.questions)[:30]
        today = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
        tid = st.session_state.get("active_mission_id")
        cheat = " ⚠️早解き" if st.session_state.is_cheating_flagged else ""

        if not custom_qs and tid:
            rn = find_row_by_id(sh_hist, tid)
            if rn:
                # 💡 120%バグ修正ロジック
                att = st.session_state.index + (
                    1 if st.session_state.get("show_result") else 0
                )
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
        # 💡 末尾に除外設定用の空欄 "" を追加
        sh_hist.append_row(
            [
                today,
                mode,
                "未実施",
                json.dumps([q["q"] for q in qs], ensure_ascii=False),
                "",
                0,
                uid,
                "",
            ]
        )
        st.cache_data.clear()
        return True
    except Exception:
        return False


@st.cache_data(ttl=60)
def load_db():
    try:
        gc = gspread.authorize(get_creds())
        ss = gc.open("study_stats_db")

        # 1. 全問題の母数（rankも取得）
        q_rows = ss.worksheet("questions").get_all_records()
        org = {}
        cat_total_counts = {}
        for r in q_rows:
            c = str(r.get("category", "共通")).strip()
            # 💡 rankを辞書に追加
            org.setdefault(c, []).append(
                {
                    "q": str(r["q"]),
                    "a": str(r["a"]),
                    "rank": str(r.get("rank", "B")).upper().strip(),  # デフォルトはB
                    "orig_cat": c,
                }
            )
            cat_total_counts[c] = cat_total_counts.get(c, 0) + 1

        # 2. 攻略済みデータ（積み上げ版：前回と同じ）
        try:
            m_rows = ss.worksheet("mastery").get_all_records()
        except Exception:
            m_rows = []

        conquered_map = {}
        for m in m_rows:
            c = str(m.get("category", "")).strip()
            if int(m.get("score", 0)) >= 1:
                conquered_map[c] = conquered_map.get(c, 0) + 1

        # 3. 履歴（history）取得
        try:
            h_rows = ss.worksheet("history").get_all_records()
        except Exception:
            h_rows = []

        # 4. 分析テーブル作成（前回と同じ）
        st_list = []
        for cat, total_in_db in cat_total_counts.items():
            done = conquered_map.get(cat, 0)
            rate = round((done / total_in_db) * 100, 1)
            st_list.append(
                {
                    "カテゴリ": cat,
                    "開拓状況": f"{done} / {total_in_db}",
                    "到達率": f"{rate}%",
                }
            )

        overall_avg = (
            round(
                (sum(conquered_map.values()) / sum(cat_total_counts.values())) * 100, 1
            )
            if cat_total_counts
            else 0.0
        )

        try:
            t_rows = ss.worksheet("timer").get_all_records()
            t_val = t_rows[0].get("total", 0)
            if t_val == "" or t_val == 0:
                t_val = t_rows[0].get("seconds", 0)
        except Exception:
            t_val = 0

        return org, {
            "cat_stats": st_list,
            "history": h_rows,
            "overall_avg": overall_avg,
            "overall_delta": 0.0,
            "total_time": int(t_val),
        }
    except Exception:
        return {}, {
            "total_time": 0,
            "overall_avg": 0,
            "overall_delta": 0,
            "cat_stats": [],
        }


all_q, db = load_db()


# --- 3. セッション初期化 ---
def init_session():
    defaults = {
        "questions": [],
        "index": 0,
        "mode": None,
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
        "is_saving": False,
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if "daily_seconds" not in st.session_state:
        st.session_state.daily_seconds = sync_timer(0)


init_session()
elapsed = time.time() - st.session_state.last_action_time
st.session_state.last_action_time = time.time()
if 0 < elapsed < 600:
    st.session_state.unsynced_seconds += int(elapsed)
    st.session_state.daily_seconds += int(elapsed)
    # 💡 修正：総勉強時間もリアルタイムで増やす
    st.session_state.total_seconds = st.session_state.get("total_seconds", 0) + int(
        elapsed
    )

if st.session_state.unsynced_seconds >= 600:
    # 💡 修正：ぐるぐるサインを表示
    with st.spinner("⏳ 学習時間を保存中..."):
        st.session_state.daily_seconds = sync_timer(st.session_state.unsynced_seconds)
        st.session_state.unsynced_seconds = 0

# --- 4. サイドバー ---
with st.sidebar:
    st.title("📊 STATUS")

    # 💡 1段目
    col1, col2 = st.columns(2)
    col1.metric("🕰️ 合計時間", format_time(st.session_state.total_seconds))
    col2.metric("⌚ 本日時間", format_time(st.session_state.daily_seconds))

    # 💡 2段目（バランス調整のために1つ追加）
    col3, col4 = st.columns(2)

    # 総合到達率
    col3.metric(
        "🎯 総合到達",
        f"{db.get('overall_avg', 0.0)}%",
        delta=f"{db.get('overall_delta', 0.0)}%",
    )

    # 📝 総解答数（新設：これまで解いた全問題数）
    # db['history'] から合計を計算するか、db['total_q_count'] を作成して表示
    total_q = sum(
        int(re.search(r"\((\d+)問中\)", str(h.get("得点", ""))).group(1))
        for h in db.get("history", [])
        if "(" in str(h.get("得点", ""))
    )

    col4.metric("📝 総解答数", f"{total_q}問")

    # 📈 カテゴリ別分析（常時表示）
    st.divider()
    st.subheader("📈 カテゴリ別分析")
    if db.get("cat_stats"):
        st.dataframe(
            pd.DataFrame(db["cat_stats"]), hide_index=True, use_container_width=True
        )
    else:
        st.caption("⚠️ 履歴データがまだありません")

    # 🔄 同期ボタン
    if st.button("🔄 同期", use_container_width=True):
        st.cache_data.clear()
        st.rerun()

    st.divider()

    # 🖨️ PDF出力機能（印刷モード時のみ表示）
    if st.session_state.print_data:
        pd_dat = st.session_state.print_data
        time_str = datetime.now(JST).strftime("%Y%m%d%H%M")
        file_name = f"{pd_dat['mode']}_{time_str}"

        components.html(
            f"""
            <button onclick="
                var oldTitle = window.parent.document.title; 
                window.parent.document.title = '{file_name}'; 
                window.parent.print(); 
                setTimeout(function(){{ window.parent.document.title = oldTitle; }}, 1500);
            " style="width:100%; background:#ff4b4b; color:white; padding:12px; border:none; border-radius:5px; font-weight:bold; cursor:pointer;">
                🖨️ PDF保存
            </button>
        """,
            height=60,
        )

        if st.button("⬅️ 本部へ戻る", type="primary", use_container_width=True):
            st.session_state.print_data = None
            st.rerun()

    # 🔊 各種設定
    st.session_state.sound_enabled = st.toggle(
        "🔊 サウンド有効", value=st.session_state.sound_enabled
    )

    unlock_key = st.text_input(
        "🗝️ 解答ロック解除キー", placeholder="キーを入力...", type="password"
    )

    # 🏳️ 中断・不備報告（特訓モード中のみ表示）
    if st.session_state.mode:
        if st.button(
            "🏳️ 中断セーブして終了",
            use_container_width=True,
            type="primary",
            disabled=st.session_state.is_saving,
        ):
            with st.status("💾 セーブ中...", expanded=False):
                st.session_state.is_saving = True
                queue_sound("correct.mp3")
                execute_queued_sound()
                batch_save_to_db()
                st.session_state.mode = None
                st.rerun()

        # 🚨 不備報告機能
        idx_s = st.session_state.index
        if idx_s < len(st.session_state.questions):
            cur = st.session_state.questions[idx_s]
            with st.expander("🚨 不備報告"):
                msg = st.text_input("不備内容（誤植など）", key=f"rpt_{idx_s}")
                if st.button("送信", key=f"btn_rpt_{idx_s}", use_container_width=True):
                    if msg:
                        try:
                            sh_r = (
                                gspread.authorize(get_creds())
                                .open("study_stats_db")
                                .worksheet("reports")
                            )
                            sh_r.append_row(
                                [
                                    datetime.now(JST).strftime("%Y/%m/%d %H:%M"),
                                    cur.get("orig_cat", "不明"),
                                    cur.get("q", "不明"),
                                    cur.get("a", "不明"),
                                    msg,
                                ]
                            )
                            st.toast("報告を受理しました", icon="✅")
                        except Exception:
                            st.error("送信に失敗しました")
                    else:
                        st.warning("内容を入力してください")

# --- 5. メイン画面：PDFモード ---
if st.session_state.print_data:
    pd_dat, pt = st.session_state.print_data, st.session_state.print_type
    today_jst = datetime.now(JST).strftime("%Y/%m/%d")

    st.markdown(
        f"### {'📖 問題' if pt == 'q' else '🔑 解答マスター'}: {pd_dat['mode']}"
    )
    st.markdown(f"**実施日: {today_jst}**　　**ID: {pd_dat['id']}**")
    st.divider()

    for i, q_p in enumerate(pd_dat["qs"]):
        st.markdown(f"#### Mission {i + 1}")
        st.markdown(q_p["q"])

        if pt == "q":
            st.markdown('<div class="answer-box"></div>', unsafe_allow_html=True)
        else:
            st.success(f"正解: {q_p['a']}")
            st.divider()
    st.stop()

# --- 6. メイン画面：本部 ---
if not st.session_state.mode:
    st.session_state.consecutive_speeding = 0
    st.session_state.is_cheating_flagged = False
    st.title("📖 2027 高校入試攻略：STRATEGY")

    if db.get("reports"):
        for r_idx, rep in enumerate(db["reports"]):
            if len(rep) >= 5:
                with st.expander(f"⚠️ 報告あり: {rep[1]}"):
                    nq, na = (
                        st.text_area("問題", rep[2], key=f"rq_{r_idx}"),
                        st.text_input("正解", rep[3], key=f"ra_{r_idx}"),
                    )
                    c1, c2 = st.columns(2)
                    if c1.button(
                        "✅ 修正",
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
                    # 💡 安定版から復活：問題自体の抹消ボタン
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
        with st.expander("🚀 通常ミッション生成", expanded=(not db["history"])):
            subj = st.selectbox(
                "教科", ["数学", "英語", "理科", "地理", "歴史", "現代文", "古文・漢文"]
            )
            year = st.radio("範囲", ["1年", "2年", "総合"], horizontal=True)
            if st.button("生成", use_container_width=True, type="primary"):
                pool = [
                    q
                    for k, ql in all_q.items()
                    if subj in k and (year == "総合" or year in k)
                    for q in ql
                ]

                # 💡 内部的には A, B, C で仕分け
                rank_a = [
                    q for q in pool if str(q.get("rank", "")).upper() == "A"
                ]  # 基本
                rank_b = [
                    q for q in pool if str(q.get("rank", "")).upper() == "B"
                ]  # 発展
                rank_c = [
                    q for q in pool if str(q.get("rank", "")).upper() == "C"
                ]  # 上級
                others = [
                    q
                    for q in pool
                    if str(q.get("rank", "")).upper() not in ["A", "B", "C"]
                ]

                # シャッフルして 🟢基本 を最優先で詰め込む
                random.shuffle(rank_a)
                random.shuffle(rank_b)
                random.shuffle(rank_c)
                random.shuffle(others)

                final_selection = (rank_a + rank_b + rank_c + others)[:30]

                batch_save_to_db(custom_mode=f"{year}{subj}", custom_qs=final_selection)
                st.rerun()
    with c2:
        with st.expander("🔥 弱点克服"):
            w_subj = st.selectbox(
                "教科選択",
                ["数学", "英語", "理科", "地理", "歴史", "現代文", "古文・漢文"],
                key="w_s",
            )
            if st.button("特訓開始", use_container_width=True):
                weak_txts = {
                    r["q"]
                    for r in db["mastery"]
                    if int(r.get("score", 0)) < 5 and int(r.get("wrong_total", 0)) >= 1
                }
                w_pool = [
                    q
                    for k, ql in all_q.items()
                    if w_subj in k
                    for q in ql
                    if q["q"] in weak_txts
                ]
                random.shuffle(w_pool)
                final = w_pool[:30]
                if batch_save_to_db(custom_mode=f"復習-{w_subj}", custom_qs=final):
                    st.rerun()

    st.divider()
    st.subheader("📅 MISSION LOG")
    h_list = db.get("history", [])

    if h_list:
        # 1. 日付でグループ分け（今週・先週・アーカイブ）
        now_d = datetime.now(JST).date()
        start_w = now_d - timedelta(days=now_d.weekday())  # 月曜日
        gps = {"📌 今週": [], "📌 先週": [], "📌 アーカイブ": []}

        # 💡 修正：h_rows ではなく h_list を使用
        for h in h_list[::-1]:
            try:
                dt_str = str(h.get("日付", "")).split()[0]
                dt = datetime.strptime(dt_str, "%Y/%m/%d").date()
                if dt >= start_w:
                    gps["📌 今週"].append(h)
                elif dt >= start_w - timedelta(days=7):
                    gps["📌 先週"].append(h)
                else:
                    gps["📌 アーカイブ"].append(h)
            except Exception:  # 💡 修正：bare except を回避
                gps["📌 アーカイブ"].append(h)

        # 💡 修正：flat_all のリスト内包表記を正しく修正
        flat_all = [q for q_list in all_q.values() for q in q_list]

        # 2. グループごとに表示
        for lbl, items in gps.items():
            if items:
                with st.expander(
                    f"{lbl} ({len(items)}件)", expanded=(lbl == "📌 今週")
                ):
                    for h in items:
                        tid = h.get("ID")
                        with st.container(border=True):
                            c_info, c_go, c_sp, c_pq, c_pa, c_del = st.columns(
                                [3.0, 1.2, 1.0, 0.8, 0.8, 0.5]
                            )
                            c_info.markdown(
                                f"**{h['日付']}** | 🆔 `{tid}`<br>{h['教科']} ({h['得点']})",
                                unsafe_allow_html=True,
                            )

                            def load_h_qs(h_item):
                                skip = get_skip_indices(h_item.get("除外", ""))
                                try:
                                    s_txts = json.loads(
                                        h_item.get("問題リスト(JSON)", "[]")
                                    )
                                except Exception:
                                    s_txts = []
                                b_qs = [
                                    next((q for q in flat_all if q["q"] == t), None)
                                    for t in s_txts
                                ]
                                return [
                                    q
                                    for i, q in enumerate(b_qs[:30])
                                    if q and (i + 1) not in skip
                                ]

                            if c_go.button(
                                "🔄 特訓開始",
                                key=f"go_{tid}",
                                type="primary",
                                use_container_width=True,
                            ):
                                st.session_state.active_mission_id = tid
                                st.session_state.questions = load_h_qs(h)
                                st.session_state.index = 0
                                st.session_state.correct_count = 0
                                st.session_state.mode = h["教科"]
                                st.session_state.question_start_time = time.time()
                                st.rerun()

                            if c_pq.button(
                                "📄 問題", key=f"pq_{tid}", use_container_width=True
                            ):
                                st.session_state.print_data = {
                                    "mode": h["教科"],
                                    "id": tid,
                                    "qs": load_h_qs(h),
                                }
                                st.session_state.print_type = "q"
                                st.rerun()

                            if c_pa.button(
                                "🔑 解答", key=f"pa_{tid}", use_container_width=True
                            ):
                                if unlock_key == "7777":
                                    st.session_state.print_data = {
                                        "mode": h["教科"],
                                        "id": tid,
                                        "qs": load_h_qs(h),
                                    }
                                    st.session_state.print_type = "a"
                                    st.rerun()
                                else:
                                    st.toast("キーが違います", icon="🔒")

                            if c_del.button(
                                "🗑️", key=f"dl_{tid}", use_container_width=True
                            ):
                                rown = find_row_by_id(
                                    gspread.authorize(get_creds())
                                    .open("study_stats_db")
                                    .worksheet("history"),
                                    tid,
                                )
                                if rown:
                                    gspread.authorize(get_creds()).open(
                                        "study_stats_db"
                                    ).worksheet("history").delete_rows(rown)
                                    st.cache_data.clear()
                                    st.rerun()

                            c_m1, c_m2, c_m3 = st.columns([3, 2, 1])
                            memo_val = c_m1.text_input(
                                "📝 メモ", value=h.get("メモ", ""), key=f"memo_{tid}"
                            )
                            skip_val = c_m2.text_input(
                                "✂️ 除外(例: 1-5, 10)",
                                value=h.get("除外", ""),
                                key=f"skip_{tid}",
                            )

                            if c_m3.button(
                                "💾 保存", key=f"sv_{tid}", use_container_width=True
                            ):
                                rown = find_row_by_id(
                                    gspread.authorize(get_creds())
                                    .open("study_stats_db")
                                    .worksheet("history"),
                                    tid,
                                )
                                if rown:
                                    sh_h = (
                                        gspread.authorize(get_creds())
                                        .open("study_stats_db")
                                        .worksheet("history")
                                    )
                                    sh_h.update_cell(rown, 5, memo_val)
                                    sh_h.update_cell(rown, 8, skip_val)
                                    st.cache_data.clear()
                                    st.toast("内容を更新しました", icon="✅")

else:  # --- 特訓モード ---
    idx, qs = st.session_state.index, st.session_state.questions
    if idx >= len(qs):
        st.balloons()
        st.title("MISSION COMPLETE!")
        sc = (
            round((st.session_state.correct_count / len(qs)) * 100, 1)
            if len(qs) > 0
            else 0
        )
        st.markdown(f"# 到達率: {sc}%")

        # 💡 安定版から復活：チート警告表示
        if st.session_state.is_cheating_flagged:
            st.error("⚠️ 警告：連続で極端に早いスキップが検知されました。")

        c_re, c_sv = st.columns(2)
        if c_re.button("🔄 最初から解き直す", use_container_width=True):
            (
                st.session_state.index,
                st.session_state.correct_count,
                st.session_state.show_result,
            ) = 0, 0, False
            st.session_state.show_options, st.session_state.current_opts = False, []
            st.rerun()
        if c_sv.button(
            "💾 保存して本部へ戻る",
            type="primary",
            use_container_width=True,
            disabled=st.session_state.is_saving,
        ):
            # 1. プレースホルダー作成
            msg_area = st.empty()

            # 2. メッセージを表示
            msg_area.warning("⚠️ 保存中... ブラウザを閉じずにお待ちください")

            # 💡 【重要】ここで0.1秒だけ待つ！
            # これにより、Streamlitが「保存中...」という文字をブラウザに送る時間が稼げます
            time.sleep(0.1)

            st.session_state.is_saving = True
            queue_sound("correct.mp3")
            execute_queued_sound()

            # 3. 通信実行（ここで画面が白っぽくなりますが、上のメッセージは残ります）
            batch_save_to_db()

            # 4. 完了表示
            msg_area.success("✅ 保存が完了しました！")
            time.sleep(0.8)

            st.session_state.mode = None
            st.rerun()
    else:
        q = qs[idx]
        en_display, jp_display, choices_from_q = parse_order_question(
            q["q"], q["orig_cat"]
        )
        ans_raw = str(q["a"]).strip()

        def get_correct_parts(ans, choices):
            ans_clean = ans.replace("(", "").replace(")", "").rstrip(".")
            if "/" in ans_clean:
                return [w.strip() for w in ans_clean.split("/") if w.strip()]
            temp_words = ans_clean.split()
            parts = []
            i = 0
            sorted_choices = sorted(choices, key=len, reverse=True)
            while i < len(temp_words):
                found = False
                for c in sorted_choices:
                    cw = c.split()
                    if len(cw) > 1 and temp_words[i : i + len(cw)] == cw:
                        parts.append(" ".join(cw))
                        i += len(cw)
                        found = True
                        break
                if not found:
                    parts.append(temp_words[i])
                    i += 1
            return parts

        correct_words = get_correct_parts(ans_raw, choices_from_q)

        # 💡 最新版の成果：社会の(ア/イ/ウ)バグ修正
        is_order = False
        if "英語" in q["orig_cat"]:
            is_order = (
                (len(choices_from_q) > 0)
                or ("/" in ans_raw)
                or (" " in ans_raw and len(correct_words) >= 2)
            )
        else:
            is_order = "/" in ans_raw

        # 💡 修正後
        r_code = str(q.get("rank", "B")).upper()  # A, B, C を取得
        r_label = RANK_LABELS.get(r_code, "⚪ その他")  # 日本語に変換

        st.caption(
            f"Mission {idx + 1}/{len(qs)} | ⭕️ {st.session_state.correct_count} | 🏷️ ランク: {r_label}"
        )
        st.markdown(f"### {en_display}")
        if jp_display:
            st.markdown(f"#### {jp_display}")
        tool = st.radio(
            "Tool",
            ["🖋️ ペン", "🧽 消しゴム"],
            horizontal=True,
            label_visibility="collapsed",
            key=f"tl_{idx}",
        )
        p_c, p_w = ("#000000", 5) if tool == "🖋️ ペン" else ("#f8f9fb", 35)
        st_canvas(
            stroke_width=p_w,
            stroke_color=p_c,
            height=300,
            width=1200,
            key=f"cv_{idx}",
            background_color="#f8f9fb",
        )

        if st.session_state.show_result:
            if st.session_state.last_is_correct:
                st.success(f"SUCCESS: {q['a']}")
                if st.button("次へ進む ➡️", use_container_width=True):
                    st.session_state.index += 1
                    st.session_state.show_result = False
                    st.session_state.show_options = False
                    st.session_state.current_opts = []
                    st.session_state.question_start_time = time.time()
                    st.rerun()
            else:
                st.error(f"FAILURE: {q['a']}")
                c_re, c_next = st.columns(2)
                if c_re.button("🔄 今の問題を解き直す", use_container_width=True):
                    (
                        st.session_state.show_result,
                        st.session_state.show_options,
                        st.session_state.current_opts,
                    ) = False, True, []
                    st.rerun()
                if c_next.button("次へ進む ➡️", use_container_width=True):
                    st.session_state.index += 1
                    st.session_state.show_result = False
                    st.session_state.show_options = False
                    st.session_state.question_start_time = time.time()
                    st.rerun()
        elif st.session_state.show_options:
            try:
                if is_order:
                    if not st.session_state.current_opts:
                        st.session_state.current_opts = (
                            choices_from_q if choices_from_q else correct_words.copy()
                        )
                        random.shuffle(st.session_state.current_opts)
                        st.session_state["user_ans_order"] = []
                    rem = len(correct_words) - len(st.session_state["user_ans_order"])
                    st.info(
                        f"Answer: {' '.join(st.session_state['user_ans_order'])} (残り {rem} 個)"
                    )
                    cols = st.columns(len(st.session_state.current_opts))
                    for i, w in enumerate(st.session_state.current_opts):
                        if st.session_state["user_ans_order"].count(w) < (
                            correct_words.count(w) if w in correct_words else 1
                        ):
                            if cols[i].button(
                                w, key=f"wbtn_{idx}_{i}", use_container_width=True
                            ):
                                st.session_state["user_ans_order"].append(w)
                                if len(st.session_state["user_ans_order"]) == len(
                                    correct_words
                                ):
                                    ok = [
                                        x.lower()
                                        for x in st.session_state["user_ans_order"]
                                    ] == [x.lower() for x in correct_words]
                                    queue_sound("correct.mp3" if ok else "wrong.mp3")
                                    st.session_state.last_is_correct = ok
                                    if ok:
                                        st.session_state.correct_count += 1
                                    st.session_state.session_results.append(
                                        {
                                            "q": q["q"],
                                            "cat": q["orig_cat"],
                                            "correct": ok,
                                        }
                                    )
                                    st.session_state.show_result = True
                                st.rerun()
                    c1, c2 = st.columns(2)
                    if c1.button("⬅️ 戻る", key=f"u_{idx}"):
                        st.session_state["user_ans_order"].pop()
                        st.rerun()
                    if c2.button("🗑️ 消去", key=f"c_{idx}"):
                        st.session_state["user_ans_order"] = []
                        st.rerun()
                else:
                    # 💡 安定版から復活：超・高性能ダミー生成ロジック統合版
                    if not st.session_state.current_opts:
                        opts = [ans_raw]

                        # 1. スプレッドシートのdummy列
                        dummy_val = str(q.get("dummy", "")).strip()
                        if dummy_val:
                            opts.extend(
                                [
                                    x.strip()
                                    for x in re.split(r"[,/、]", dummy_val)
                                    if x.strip()
                                ]
                            )

                        # 1.5 問題文の (ア/イ/ウ) を拾う（最新版の社会バグ修正）
                        if "英語" not in q["orig_cat"] and len(choices_from_q) > 0:
                            opts.extend(choices_from_q)

                        # 2. 英語文法ルール
                        if len(opts) < 4 and "英語" in q["orig_cat"]:
                            eng_rules = {
                                "am": "is, are, was",
                                "is": "are, am, was",
                                "are": "is, am, were",
                                "was": "were, is, am",
                                "were": "was, are, is",
                                "can": "will, must, should",
                                "Can": "Will, Do, Does",
                                "Who": "When, Where, What",
                                "who": "when, where, what",
                                "How many": "How much, How long, How often",
                                "going to": "will, must, should",
                            }
                            if ans_raw in eng_rules:
                                opts.extend(
                                    [x.strip() for x in eng_rules[ans_raw].split(",")]
                                )

                        # 3. 数学数値ロジック
                        if len(opts) < 4 and "数学" in q["orig_cat"]:
                            nums = re.findall(r"^-?\d+$", ans_raw)
                            if len(nums) == 1:
                                n = int(nums[0])
                                pot = (
                                    [str(-n), str(n + 1), str(n * 2)]
                                    if n != 0
                                    else ["1", "-1", "2"]
                                )
                                opts.extend(pot)
                            m_eq = re.match(r"^([a-zA-Z])\s*=\s*(-?\d+)$", ans_raw)
                            if m_eq:
                                v, n = m_eq.group(1), int(m_eq.group(2))
                                opts.extend(
                                    [f"{v}={-n}", f"{v}={n + 1}", f"{v}={n - 1}"]
                                )

                        # 4. 🔥 キーワード（語尾）連動検索
                        if len(opts) < 4:
                            suffixes = [
                                "気圧",
                                "地方",
                                "時代",
                                "事件",
                                "条約",
                                "法則",
                                "山脈",
                                "平野",
                                "川",
                            ]
                            matched_suffix = next(
                                (s for s in suffixes if s in ans_raw), None
                            )
                            if matched_suffix:
                                suffix_cands = [
                                    str(x["a"])
                                    for x in all_q.get(q["orig_cat"], [])
                                    if matched_suffix in str(x["a"])
                                    and str(x["a"]) != ans_raw
                                ]
                                random.shuffle(suffix_cands)
                                opts.extend(suffix_cands)

                        # 5. 小分類(sub_category)からの補充
                        if len(opts) < 4 and q.get("sub"):
                            sub_cands = [
                                str(x["a"])
                                for x in all_q.get(q["orig_cat"], [])
                                if x.get("sub") == q["sub"] and str(x["a"]) != ans_raw
                            ]
                            random.shuffle(sub_cands)
                            opts.extend(sub_cands)

                        # 6. 最終補充（同一教科内からランダム）
                        if len(opts) < 4:
                            cat_cands = [
                                str(x["a"])
                                for x in all_q.get(q["orig_cat"], [])
                                if str(x["a"]) != ans_raw
                            ]
                            random.shuffle(cat_cands)
                            opts.extend(cat_cands)

                        # 重複を消して4つに絞り、シャッフル
                        opts = list(dict.fromkeys(opts))
                        if len(opts) > 4:
                            opts = opts[:4]
                        random.shuffle(opts)
                        st.session_state.current_opts = opts

                    cols = st.columns(len(st.session_state.current_opts))
                    for i, o in enumerate(st.session_state.current_opts):
                        if cols[i].button(
                            str(o), key=f"opt_{idx}_{i}", use_container_width=True
                        ):
                            ok = str(o).lower() == ans_raw.lower()
                            queue_sound("correct.mp3" if ok else "wrong.mp3")
                            st.session_state.last_is_correct = ok
                            if ok:
                                st.session_state.correct_count += 1
                            st.session_state.session_results.append(
                                {"q": q["q"], "cat": q["orig_cat"], "correct": ok}
                            )
                            st.session_state.show_result = True
                            st.rerun()
            except Exception:
                st.error("表示エラー")
        else:
            if st.button(
                "判定 ＆ オプション表示", use_container_width=True, type="primary"
            ):
                # 💡 安定版から復活：サボり（早解き）検知 5.0秒
                if time.time() - st.session_state.question_start_time < 5.0:
                    st.session_state.consecutive_speeding += 1
                    if st.session_state.consecutive_speeding >= 3:
                        st.session_state.is_cheating_flagged = True
                else:
                    st.session_state.consecutive_speeding = 0

                st.session_state.show_options = True
                st.rerun()
    execute_queued_sound()
