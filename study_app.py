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
import numpy as np
from PIL import Image, ImageDraw, ImageFont

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
    /* 通常画面：サイドバー幅固定 */
    [data-testid="stSidebar"] { min-width: 260px !important; max-width: 260px !important; }

    /* 🖨️ 印刷用設定（2mロール紙 復活版） */
    @media print {
        @page { 
            size: 210mm 3000mm; /* 💡 3mのロングロール設定 */
            margin: 15mm; 
        }
        
        /* 💡 修正：iframe は消さない（命令系統を維持） */
        section[data-testid="stSidebar"], header, .stButton, 
        div[data-testid="stToolbar"], [data-testid="collapsedControl"] { 
            display: none !important; 
        }
        
        /* 💡 真っ白対策：メインコンテンツを強制的に可視化 */
        .main .block-container, div[data-testid="stMainBlockContainer"], .stMain { 
            display: block !important;
            visibility: visible !important;
            max-width: 100% !important; 
            width: 100% !important; 
            padding: 0 !important; 
            margin: 0 !important;
        }

        /* 解答枠（ロール紙で書きやすいサイズ） */
        .answer-box { 
            border: 1.5px solid #000 !important; 
            height: 200px; 
            width: 100%; 
            margin-top: 10px;
            margin-bottom: 25px;
            background: #fff !important;
            -webkit-print-color-adjust: exact;
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


def get_kanji_score(canvas_result, char, correct_strokes):
    """
    漢字判定エンジン（最新版）
    - 画数チェック（±2ガード）
    - IPAexゴシック対応（ウェブ/ローカル両用）
    - 厳格判定：線幅8 / 合格ライン0.70
    """
    # 1. キャンバスのデータが空の場合は 0点
    if canvas_result.json_data is None:
        return 0

    # 2. ユーザーが書いた画数（線の数）を取得
    user_strokes = len(canvas_result.json_data["objects"])

    # 3. 画数ガード：設定された画数と±2以上離れていたら即エラー(-1)
    try:
        if correct_strokes and str(correct_strokes).strip():
            target_s = int(float(str(correct_strokes).strip()))
            if abs(user_strokes - target_s) > 2:
                return -1
    except Exception:
        # スプシの画数データが読み取れない場合はガードをスキップ
        pass

    # 4. ユーザーの描画データをマスク（白黒画像）に変換
    # 透過チャネル(3)が 0 より大きい場所を「書かれた部分」とする
    user_mask = canvas_result.image_data[:, :, 3] > 0
    if not np.any(user_mask):
        return 0

    # 5. お手本（正解）マスクの作成
    size = 300
    target_img = Image.new("L", (size, size), 0)

    # フォントの読み込み（GitHubのfontsフォルダ ＞ Windows標準 ＞ デフォルト の順）
    font = None
    font_path = os.path.join("fonts", "ipaexg.ttf")
    win_font = r"C:\Windows\Fonts\msmincho.ttc"

    try:
        if os.path.exists(font_path):
            font = ImageFont.truetype(font_path, 230)
        elif os.path.exists(win_font):
            font = ImageFont.truetype(win_font, 230)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    # お手本を黒背景に白文字で描画
    draw = ImageDraw.Draw(target_img)
    # stroke_width=8 は「厳格」な設定です。丁寧に書く必要があります。
    draw.text(
        (size // 2, size // 2), char, font=font, fill=255, anchor="mm", stroke_width=8
    )
    target_mask = np.array(target_img) > 0

    # 6. 一致度（F-score）の計算
    # 重なり部分を抽出
    overlap = np.logical_and(target_mask, user_mask).sum()

    # 再現率（お手本をどれだけなぞれたか）
    recall = overlap / target_mask.sum() if target_mask.sum() > 0 else 0
    # 適合率（余計なはみ出しがないか）
    precision = overlap / user_mask.sum() if user_mask.sum() > 0 else 0

    # F値（バランススコア）
    if (recall + precision) > 0:
        f_score = (2 * recall * precision) / (recall + precision)
    else:
        f_score = 0

    # 7. スコア判定
    # 0.70以上：一発合格（100点）
    if f_score > 0.70:
        return 100
    # 0.25以上：だいたい合っている（34点 = 3回で合格）
    if f_score > 0.25:
        return 34

    # それ以下は「形が違います」
    return 0


def save_mastery_batch(worksheet, updates):
    """
    セッション中に溜まった正解数をスプレッドシートに一括反映する
    updates: {row_index: added_count} の辞書
    """
    if not updates:
        return

    with st.spinner("学習データをスプレッドシートに保存中..."):
        # 全データ（mastery列）を一度取得
        all_data = worksheet.get_all_records()

        for row_idx, added_val in updates.items():
            # 現在の習熟度を取得して加算
            # row_idx は 0始まりのデータ行（ヘッダー除外）を想定
            current_mastery = int(all_data[row_idx].get("mastery", 0))
            new_mastery = current_mastery + added_val

            # mastery列（H列と想定：8列目）を更新
            # worksheet.update_cell(行, 列, 値) ※スプシの行番号は row_idx + 2
            worksheet.update_cell(row_idx + 2, 8, new_mastery)

    st.success("開拓状況を保存しました！")


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
        # タイマー同期
        if st.session_state.unsynced_seconds > 0:
            st.session_state.daily_seconds = sync_timer(
                st.session_state.unsynced_seconds
            )
            st.session_state.unsynced_seconds = 0

        gc = gspread.authorize(get_creds())
        ss = gc.open("study_stats_db")
        today_full = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")

        # --- 1. masteryシート（習熟度）の一括更新 ---
        if st.session_state.session_results:
            try:
                sh_m = ss.worksheet("mastery")
                m_recs = sh_m.get_all_records()
            except Exception:
                sh_m = ss.add_worksheet(title="mastery", rows="1000", cols="5")
                sh_m.append_row(["category", "q", "score", "wrong_total", "last_date"])
                m_recs = []

            # 💡 修正ポイント：空文字 "" を 0 に変換する安全な読み込み
            m_dict = {}
            for i, r in enumerate(m_recs):
                q_key = str(r.get("q", ""))
                if not q_key:
                    continue  # 問題文が空ならスキップ

                # get("列名") が空文字 "" だったら 0 を入れる処理
                raw_s = r.get("score")
                raw_w = r.get("wrong_total")

                m_dict[q_key] = {
                    "row": i + 2,
                    "s": int(raw_s) if str(raw_s).strip() != "" else 0,
                    "w": int(raw_w) if str(raw_w).strip() != "" else 0,
                }

            updates = []
            new_rows = []

            for res in st.session_state.session_results:
                q_txt, cat, ok = res["q"], res["cat"], res["correct"]

                if q_txt in m_dict:
                    idx_m = m_dict[q_txt]["row"]

                    # 💡 漢字ならペナルティなし(0)、それ以外は -1
                    penalty = 0 if "漢字" in str(cat) else -1

                    ns = min(5, max(0, m_dict[q_txt]["s"] + (1 if ok else penalty)))
                    nw = m_dict[q_txt]["w"] + (0 if ok else 1)

                    updates.append({"range": f"C{idx_m}", "values": [[ns]]})  # score
                    updates.append({"range": f"D{idx_m}", "values": [[nw]]})  # wrong
                    updates.append({"range": f"E{idx_m}", "values": [[today_full]]})
                else:
                    new_rows.append(
                        [cat, q_txt, 1 if ok else 0, 0 if ok else 1, today_full]
                    )

            if updates:
                sh_m.batch_update(updates)
            if new_rows:
                sh_m.append_rows(new_rows)

            st.session_state.session_results = []

        # --- 2. 履歴（history）への保存処理 ---
        sh_hist = ss.worksheet("history")
        mode = custom_mode if custom_mode else st.session_state.mode
        qs = (custom_qs if custom_qs else st.session_state.questions)[:30]
        today = datetime.now(JST).strftime("%Y/%m/%d %H:%M")
        tid = st.session_state.get("active_mission_id")
        cheat = " ⚠️早解き" if st.session_state.is_cheating_flagged else ""

        if not custom_qs and tid:
            rn = find_row_by_id(sh_hist, tid)
            if rn:
                att = st.session_state.index + (
                    1 if st.session_state.get("show_result") else 0
                )
                cor = min(st.session_state.correct_count, att)
                sc_str = (
                    f"{round((cor / att) * 100, 1)}点 ({att}問中){cheat}"
                    if att > 0
                    else "未実施"
                )

                hist_updates = [
                    {"range": f"A{rn}", "values": [[today]]},
                    {"range": f"C{rn}", "values": [[sc_str]]},
                ]
                sh_hist.batch_update(hist_updates)
                st.cache_data.clear()
                return True

        uid = f"id_{uuid.uuid4().hex[:8]}"
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
    except Exception as e:
        st.error(f"保存エラー: {e}")
        return False


@st.cache_data(ttl=3600)
def load_db():
    try:
        gc = gspread.authorize(get_creds())
        ss = gc.open("study_stats_db")

        # 1. 全問題の母数取得（strokes列も読み込む）
        q_rows = ss.worksheet("questions").get_all_records()
        org = {}
        cat_total_counts = {}
        for r in q_rows:
            c = str(r.get("category", "共通")).strip()
            rank_val = str(r.get("rank", "B")).upper().strip()

            org.setdefault(c, []).append(
                {
                    "q": str(r["q"]),
                    "a": str(r["a"]),
                    "rank": rank_val,
                    "orig_cat": c,
                    "strokes": str(r.get("strokes", "")),  # 💡 画数データを保持
                }
            )
            cat_total_counts[c] = cat_total_counts.get(c, 0) + 1

        # 2. 攻略済みデータ判定
        try:
            m_rows = ss.worksheet("mastery").get_all_records()
        except Exception:
            m_rows = []

        conquered_map = {}
        total_opened_count = 0
        for m in m_rows:
            c = str(m.get("category", "共通")).strip()
            last_date_val = str(m.get("last_date", "")).strip()
            if last_date_val != "":
                conquered_map[c] = conquered_map.get(c, 0) + 1
                total_opened_count += 1

        # 3. 履歴・分析テーブル作成
        st_list = []
        for cat, total_in_db in cat_total_counts.items():
            done = conquered_map.get(cat, 0)
            rate = round((done / total_in_db) * 100, 1) if total_in_db > 0 else 0.0
            st_list.append(
                {
                    "カテゴリ": cat,
                    "開拓状況": f"{done} / {total_in_db}",
                    "到達率": f"{rate}%",
                }
            )

        # 完成したデータを辞書形式で返す
        return org, {
            "cat_stats": st_list,
            "overall_avg": round(
                (total_opened_count / sum(cat_total_counts.values())) * 100, 1
            )
            if cat_total_counts
            else 0,
            "history": ss.worksheet("history").get_all_records()
            if "history" in [w.title for w in ss.worksheets()]
            else [],
            "reports": ss.worksheet("reports").get_all_records()
            if "reports" in [w.title for w in ss.worksheets()]
            else [],
            "total_time": int(
                ss.worksheet("timer").get_all_records()[0].get("total", 0)
            )
            if "timer" in [w.title for w in ss.worksheets()]
            else 0,
        }
    except Exception as e:
        st.error(f"DB読み込みエラー: {e}")
        return {}, {"cat_stats": [], "overall_avg": 0, "history": [], "reports": []}


# 💡 関数の実行
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

    # 💡 1段目：時間
    col1, col2 = st.columns(2)
    col1.metric("🕰️ 全累計時間", format_time(st.session_state.total_seconds))
    col2.metric("⌚ 本日勉強", format_time(st.session_state.daily_seconds))

    # 💡 2段目：到達率と解答数
    col3, col4 = st.columns(2)
    col3.metric(
        "🎯 総合到達",
        f"{db.get('overall_avg', 0.0)}%",
        delta=f"{db.get('overall_delta', 0.0)}%",
    )

    # --- 累計解答数の計算（強制ダイレクト読み込み版） ---
    # --- 💡 修正後：キャッシュされたデータ(db)から計算する軽量版 ---
    total_q_all = 0
    # すでに load_db() で取得済みのデータを利用します
    if "cat_stats" in db:
        # 各カテゴリの「開拓状況」から分子の数字を合計する
        for stat in db["cat_stats"]:
            try:
                # "3 / 265" のような文字列から "3" を取り出す
                opened_val = int(stat["開拓状況"].split(" / ")[0])
                total_q_all += opened_val
            except Exception:
                continue

    col4.metric("📝 累計解答", f"{total_q_all}問")

    st.divider()

    # 1. 同期ボタン
    if st.button("🔄 同期", width="stretch", key="sync_button_top"):
        st.cache_data.clear()
        st.rerun()

    # 2. 特訓中のみ出現するボタン（同期ボタンの直下）
    if st.session_state.mode:
        st.write("")
        # --- 🏳️ 中断・終了ボタン ---
        if st.button(
            "🏳️ 中断セーブして終了",
            key="save_exit_btn",
            width="stretch",
            type="primary",
            disabled=st.session_state.is_saving,
        ):
            with st.status("💾 データを一括セーブ中...", expanded=False) as status:
                st.session_state.is_saving = True

                # 音声演出
                queue_sound("correct.mp3")
                execute_queued_sound()

                # 💡 通信1回で済む爆速版の保存処理を実行
                success = batch_save_to_db()

                if success:
                    # 💡 保存成功時のみキャッシュをクリア（4問→5問の反映用）
                    st.cache_data.clear()

                    status.update(
                        label="✅ セーブ完了！", state="complete", expanded=False
                    )
                    st.session_state.mode = None
                    st.session_state.is_saving = False
                    st.rerun()
                else:
                    status.update(label="❌ セーブ失敗", state="error")
                    st.session_state.is_saving = False
                    st.error(
                        "通信エラーが発生しました。時間を置いて再度お試しください。"
                    )

        # --- 🚨 不備報告機能（軽量版） ---
        idx_s = st.session_state.index
        if idx_s < len(st.session_state.questions):
            cur = st.session_state.questions[idx_s]
            with st.expander("🚨 不備報告"):
                msg = st.text_input("内容", key=f"rpt_input_{idx_s}")
                if st.button("送信", key=f"rpt_send_btn_{idx_s}", width="stretch"):
                    if msg:
                        # 💡 認証処理を「if msg:」の中に移動。これで普段は一切通信しません。
                        try:
                            # 送信時のみ認証・接続
                            gc_rpt = gspread.authorize(get_creds())
                            sh_r = gc_rpt.open("study_stats_db").worksheet("reports")

                            sh_r.append_row(
                                [
                                    datetime.now(JST).strftime("%Y/%m/%d %H:%M"),
                                    cur.get("orig_cat", "不明"),
                                    cur.get("q", "不明"),
                                    cur.get("a", "不明"),
                                    msg,
                                ]
                            )
                            st.toast("報告受理", icon="✅")
                        except Exception as e:
                            st.toast(f"報告失敗: {e}", icon="⚠️")

    # 3. カテゴリ別分析（常時表示）
    st.subheader("📈 カテゴリ別分析")
    if db.get("cat_stats"):
        st.dataframe(pd.DataFrame(db["cat_stats"]), hide_index=True, width="stretch")

    st.divider()

    # 4. 印刷戻るボタン
    if st.session_state.print_data:
        if st.button(
            "⬅️ 本部へ戻る",
            type="secondary",
            width="stretch",
            key="back_to_main_btn",
        ):
            st.session_state.print_data = None
            st.rerun()

    # 5. 設定（サウンド有効）
    # 💡 修正：重複エラー回避のため key を指定
    st.session_state.sound_enabled = st.toggle(
        "🔊 サウンド有効",
        value=st.session_state.sound_enabled,
        key="side_sound_toggle_unique",
    )

    # 6. 解答ロック解除キー（一番下）
    # 💡 CSSを追加して、特定のキーを持つ入力欄の文字を「●」にする
    st.markdown(
        """
        <style>
        /* parent_unlock_key というキーを持つ入力欄の文字をドットにする */
        input[aria-label="🗝️ 解答ロック解除キー"] {
            -webkit-text-security: disc !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.write("")
    unlock_key = st.text_input(
        "🗝️ 解答ロック解除キー",
        placeholder="キーを入力...",
        # 💡 type="password" は書かない（Chrome対策）
        key="parent_unlock_key",
    )

    # 🏳️ 中断・不備報告（特訓モード中のみ表示）
    if st.session_state.mode:
        if st.button(
            "🏳️ 中断セーブして終了",
            width="stretch",
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
                if st.button(
                    "送信", key=f"btn_rpt_{idx_s}", width="stretch"
                ):  # ← ここを修正
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
# --- 印刷用表示モード ---
if st.session_state.print_data:
    pd_dat = st.session_state.print_data
    pt = st.session_state.print_type

    # 💡 1. ファイル名に「時分秒」まで含める（例: 20260316_023545）
    now_fn = datetime.now(JST).strftime("%Y%m%d_%H%M%S")

    # 「問題/解答」の区別をファイル名の先頭に付けるとさらに整理しやすくなります
    type_label = "問題" if pt == "q" else "解答"
    file_title = f"{type_label}_{pd_dat['mode']}_{now_fn}_{pd_dat['id']}"

    # 💡 2. ブラウザの親画面に対して「名前変更」と「印刷」を命令する
    components.html(
        f"""
        <script>
        // PDF保存時のデフォルト名になるようにタイトルを書き換え
        window.parent.document.title = "{file_title}";
        
        // メイン画面（親）を印刷する関数
        function triggerPrint() {{
            window.parent.focus();
            window.parent.print();
        }}

        // 描画完了を待つ（1.5秒後に実行）
        setTimeout(triggerPrint, 1500);
        </script>
        """,
        height=0,
    )

    # 💡 3. 画面の中身を描画
    st.markdown(
        f"### {'📖 問題' if pt == 'q' else '🔑 解答マスター'}: {pd_dat['mode']}"
    )
    st.caption(
        f"実施日: {datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')} | ID: {pd_dat['id']}"
    )
    st.divider()

    for i, q_p in enumerate(pd_dat["qs"]):
        with st.container():
            st.markdown(f"#### Mission {i + 1}")
            st.markdown(q_p["q"])
            if pt == "q":
                # 解答枠（以前設定した answer-box スタイルが適用されます）
                st.markdown('<div class="answer-box"></div>', unsafe_allow_html=True)
            else:
                st.success(f"正解: {q_p['a']}")
                st.divider()

    st.stop()

if not st.session_state.mode:
    st.session_state.consecutive_speeding = 0
    st.session_state.is_cheating_flagged = False
    st.title("📖 2027 高校入試攻略：STRATEGY")

    # --- 1. 不備報告（管理者用） ---
    if db.get("reports"):
        for r_idx, rep in enumerate(db["reports"]):
            if len(rep) >= 5:
                with st.expander(f"⚠️ 報告あり: {rep[1]}"):
                    nq = st.text_area("問題", rep[2], key=f"rq_{r_idx}")
                    na = st.text_input("正解", rep[3], key=f"ra_{r_idx}")
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
                    if c2.button(
                        "🗑️ 抹消", key=f"dbtn_{r_idx}", use_container_width=True
                    ):
                        gc = gspread.authorize(get_creds())
                        ss = gc.open("study_stats_db")
                        sh_q = ss.worksheet("questions")
                        recs_q = sh_q.get_all_records()
                        for i_q, r_q in enumerate(recs_q):
                            if str(r_q.get("category")) == str(rep[1]) and str(
                                r_q.get("q")
                            ) == str(rep[2]):
                                sh_q.delete_rows(i_q + 2)
                                break
                        ss.worksheet("reports").delete_rows(r_idx + 2)
                        st.cache_data.clear()
                        st.rerun()

    # --- 2. ミッション生成エリア ---
    # --- 💡 カテゴリ一覧をスプレッドシートから取得 ---
    available_cats = sorted(list(all_q.keys()))

    c1, c2 = st.columns(2)
    with c1:
        with st.expander("🚀 通常ミッション生成", expanded=(not db["history"])):
            # 💡 教科をスプレッドシートのカテゴリから直接選べるように変更
            subj = st.selectbox("教科", available_cats)

            # 漢字などの場合、範囲（1年/2年）の絞り込みが不要なら「総合」でOK
            year = st.radio("範囲", ["1年", "2年", "総合"], horizontal=True)

            if st.button("生成", use_container_width=True, type="primary"):
                pool = [
                    q
                    for k, ql in all_q.items()
                    if subj in k and (year == "総合" or year in k)
                    for q in ql
                ]
                rank_a = [q for q in pool if str(q.get("rank", "")).upper() == "A"]
                rank_b = [q for q in pool if str(q.get("rank", "")).upper() == "B"]
                rank_c = [q for q in pool if str(q.get("rank", "")).upper() == "C"]
                others = [
                    q
                    for q in pool
                    if str(q.get("rank", "")).upper() not in ["A", "B", "C"]
                ]
                random.shuffle(rank_a)
                random.shuffle(rank_b)
                random.shuffle(rank_c)
                random.shuffle(others)
                final_selection = (rank_a + rank_b + rank_c + others)[:30]
                batch_save_to_db(custom_mode=f"{year}{subj}", custom_qs=final_selection)
                st.rerun()

    with c2:
        with st.expander("🔥 弱点克服"):
            # 💡 弱点克服の教科選択もスプレッドシートと連動
            w_subj = st.selectbox(
                "教科選択",
                available_cats,
                key="w_s",
            )
            if st.button("特訓開始"):
                # 💡 まずスプレッドシートにアクセスする準備をします
                gc = gspread.authorize(get_creds())
                ss = gc.open("study_stats_db")

                # 1. 全問題を読み込み、行番号(row_idx)を刻みます
                df_all = pd.DataFrame(ss.worksheet("questions").get_all_records())
                df_all["row_idx"] = df_all.index

                # 2. 習熟度5以上の問題を除外する
                try:
                    # masteryシートを読み込む
                    df_m = pd.DataFrame(ss.worksheet("mastery").get_all_records())
                    # score列が 5 以上の問題(q)をリストにする
                    graduated = df_m[df_m["score"].astype(int) >= 5]["q"].tolist()
                    # メインの問題から卒業済みを除外
                    df_all = df_all[~df_all["q"].isin(graduated)]
                except Exception:
                    pass  # masteryシートがまだ無い時は全出し

                # 3. セッションにセットして特訓開始
                st.session_state.questions = df_all.to_dict(orient="records")
                st.session_state.mode = "training"
                st.session_state.index = 0
                st.rerun()

    st.divider()
    st.subheader("📅 MISSION LOG")
    h_list = db.get("history", [])

    if h_list:
        now_d = datetime.now(JST).date()
        start_w = now_d - timedelta(days=now_d.weekday())
        gps = {"📌 今週": [], "📌 先週": [], "📌 アーカイブ": []}

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
            except Exception:
                gps["📌 アーカイブ"].append(h)

        flat_all = [q for q_list in all_q.values() for q in q_list]

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
                                gc = gspread.authorize(get_creds())
                                rown = find_row_by_id(
                                    gc.open("study_stats_db").worksheet("history"), tid
                                )
                                if rown:
                                    gc.open("study_stats_db").worksheet(
                                        "history"
                                    ).delete_rows(rown)
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
                            c_m3.markdown(
                                '<div style="margin-top: 28px;"></div>',
                                unsafe_allow_html=True,
                            )

                            if c_m3.button(
                                "💾 保存", key=f"sv_{tid}", use_container_width=True
                            ):
                                gc = gspread.authorize(get_creds())
                                sh_h = gc.open("study_stats_db").worksheet("history")
                                rown = find_row_by_id(sh_h, tid)
                                if rown:
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

        if st.session_state.is_cheating_flagged:
            st.error("⚠️ 警告：連続で極端に早いスキップが検知されました。")

        c_re, c_sv = st.columns(2)
        if c_re.button("🔄 最初から解き直す", width="stretch"):
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
            width="stretch",
            disabled=st.session_state.is_saving,
        ):
            msg_area = st.empty()
            msg_area.warning("⚠️ 保存中... ブラウザを閉じずにお待ちください")
            time.sleep(0.1)
            st.session_state.is_saving = True
            queue_sound("correct.mp3")
            execute_queued_sound()
            batch_save_to_db()
            msg_area.success("✅ 保存が完了しました！")
            time.sleep(0.8)
            st.session_state.mode = None
            st.rerun()
    else:
        q = qs[idx]
        cat = q.get("orig_cat", "")
        # 💡 カテゴリ名に「漢字」が含まれるかチェック
        is_kanji_mode = "漢字" in cat

        if is_kanji_mode:
            # --- 🖋️ 漢字特訓専用UI（スキップ機能付き） ---
            ans_str = str(q["a"]).strip()

            # 画数データの読み込み（strokes列がない場合は 1画固定で回避）
            try:
                s_list = [
                    int(x.strip()) for x in str(q.get("strokes", "1,1")).split(",")
                ]
            except Exception:
                s_list = [1] * len(ans_str)

            # セッション状態の初期化
            if (
                "kanji_scores" not in st.session_state
                or st.session_state.get("kanji_q_key") != f"{idx}_{cat}"
            ):
                st.session_state.kanji_scores = [0] * len(ans_str)
                st.session_state.kanji_resets = [0] * len(ans_str)
                st.session_state.kanji_q_key = f"{idx}_{cat}"

            st.caption(
                f"Mission {idx + 1}/{len(qs)} | ⭕️ {st.session_state.correct_count} | 🏷️ {cat}"
            )
            st.markdown(f"### {q['q']}")

            # 漢字一文字ずつの入力ユニットを表示
            cols = st.columns(len(ans_str))
            for i, char in enumerate(ans_str):
                with cols[i]:
                    score_now = st.session_state.kanji_scores[i]

                    # 巨大表示ボックス
                    st.markdown(
                        f"""
                        <div style='text-align:center; padding:15px; background-color:#f0f8ff; border:3px solid #4A90E2; border-radius:15px; margin-bottom:10px;'>
                            <div style='font-size:160px; font-weight:normal; color:#1E3A5F; line-height:1.2;'>{char}</div>
                            <div style='color:#4A90E2; font-size:18px;'>{s_list[i]}画 (習得率: {min(100, score_now)}%)</div>
                        </div>
                    """,
                        unsafe_allow_html=True,
                    )

                    st.progress(min(100, score_now) / 100)

                    if score_now < 100:
                        # 描画キャンバス
                        can = st_canvas(
                            stroke_width=12,
                            stroke_color="#000",
                            background_color="#fff",
                            height=300,
                            width=300,
                            key=f"k_can_v4_{idx}_{i}_{st.session_state.kanji_resets[i]}",
                            display_toolbar=False,
                        )
                        c1, c2 = st.columns(2)
                        if c1.button(
                            "採点",
                            key=f"k_sbtn_{idx}_{i}",
                            type="primary",
                            use_container_width=True,
                        ):
                            res = get_kanji_score(can, char, s_list[i])
                            if res == -1:
                                st.error(
                                    f"画数エラー({len(can.json_data['objects'])}画)"
                                )
                            elif res > 0:
                                st.session_state.kanji_scores[i] += res
                                st.session_state.kanji_resets[i] += 1
                                queue_sound("correct.mp3")
                                st.rerun()
                            else:
                                st.error("形が違います")
                        if c2.button(
                            "消す", key=f"k_rbtn_{idx}_{i}", use_container_width=True
                        ):
                            st.session_state.kanji_resets[i] += 1
                            st.rerun()
                    else:
                        st.success("Mastered!")
                        st.markdown(
                            "<h1 style='text-align:center; font-size:150px; color:#FF4B4B;'>💮</h1>",
                            unsafe_allow_html=True,
                        )

            st.divider()

            # 💡 修正ポイント：ボタンを横に並べて「進む」と「スキップ」を配置
            col_next, col_skip = st.columns(2)

            # 💡 ここで「次に進める状態か（全文字100点か）」を計算します
            can_proceed = all(s >= 100 for s in st.session_state.kanji_scores)

            # 「次の問題へ進む」ボタンの中身
            if col_next.button(
                "次の問題へ進む ➡️",
                use_container_width=True,
                type="primary",
                disabled=not can_proceed,
            ):
                # 保存予約用の辞書がなければ作る
                if "pending_mastery" not in st.session_state:
                    st.session_state.pending_mastery = {}

                # 現在の問題の行番号（row_idx）をキーにして、正解数をカウントアップ
                row_key = q.get("row_idx")
                if row_key is not None:
                    st.session_state.pending_mastery[row_key] = (
                        st.session_state.pending_mastery.get(row_key, 0) + 1
                    )

                # 以下、既存の処理
                st.session_state.session_results.append(
                    {"q": q["q"], "cat": cat, "correct": True}
                )
                st.session_state.correct_count += 1
                st.session_state.index += 1
                st.rerun()

            # 2. 💡 スキップボタン（いつでも押せる）
            if col_skip.button("この問題をスキップ ⏩", use_container_width=True):
                # 習熟度（correct_count）は増やさず、結果を False として記録
                st.session_state.session_results.append(
                    {"q": q["q"], "cat": cat, "correct": False}
                )
                st.session_state.index += 1
                st.rerun()
        else:
            # --- 📖 通常の選択肢モード（提供された既存コードの移植） ---
            en_display, jp_display, choices_from_q = parse_order_question(
                q["q"], q["orig_cat"]
            )
            ans_raw = str(q["a"]).strip()

            def get_correct_parts(ans, choices):
                ans_clean = ans.replace("(", "").replace(")", "").rstrip(".")
                if "/" in ans_clean:
                    return [w.strip() for w in ans_clean.split("/") if w.strip()]
                temp_words = ans_clean.split()
                parts, i = [], 0
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
            is_order = (
                (len(choices_from_q) > 0)
                or ("/" in ans_raw)
                or (" " in ans_raw and len(correct_words) >= 2)
                if "英語" in q["orig_cat"]
                else "/" in ans_raw
            )

            st.caption(
                f"Mission {idx + 1}/{len(qs)} | ⭕️ {st.session_state.correct_count} | 🏷️ ランク: {RANK_LABELS.get(str(q.get('rank', 'B')).upper(), '⚪ その他')}"
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
                    if st.button("次へ進む ➡️", width="stretch"):
                        st.session_state.index += 1
                        st.session_state.show_result = False
                        st.session_state.show_options, st.session_state.current_opts = (
                            False,
                            [],
                        )
                        st.session_state.question_start_time = time.time()
                        st.rerun()
                else:
                    st.error(f"FAILURE: {q['a']}")
                    c_re, c_next = st.columns(2)
                    if c_re.button("🔄 今の問題を解き直す", width="stretch"):
                        (
                            st.session_state.show_result,
                            st.session_state.show_options,
                            st.session_state.current_opts,
                        ) = False, True, []
                        st.rerun()
                    if c_next.button("次へ進む ➡️", width="stretch"):
                        st.session_state.index += 1
                        st.session_state.show_result = False
                        (
                            st.session_state.show_options,
                            st.session_state.question_start_time,
                        ) = False, time.time()
                        st.rerun()
            elif st.session_state.show_options:
                try:
                    if is_order:
                        if not st.session_state.current_opts:
                            st.session_state.current_opts = (
                                choices_from_q
                                if choices_from_q
                                else correct_words.copy()
                            )
                            random.shuffle(st.session_state.current_opts)
                            st.session_state["user_ans_order"] = []
                        rem = len(correct_words) - len(
                            st.session_state["user_ans_order"]
                        )
                        st.info(
                            f"Answer: {' '.join(st.session_state['user_ans_order'])} (残り {rem} 個)"
                        )
                        cols = st.columns(len(st.session_state.current_opts))
                        for i, w in enumerate(st.session_state.current_opts):
                            if st.session_state["user_ans_order"].count(w) < (
                                correct_words.count(w) if w in correct_words else 1
                            ):
                                if cols[i].button(
                                    w, key=f"wbtn_{idx}_{i}", width="stretch"
                                ):
                                    st.session_state["user_ans_order"].append(w)
                                    if len(st.session_state["user_ans_order"]) == len(
                                        correct_words
                                    ):
                                        ok = [
                                            x.lower()
                                            for x in st.session_state["user_ans_order"]
                                        ] == [x.lower() for x in correct_words]
                                        queue_sound(
                                            "correct.mp3" if ok else "wrong.mp3"
                                        )
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
                        if not st.session_state.current_opts:
                            opts = [ans_raw]
                            dummy_val = str(q.get("dummy", "")).strip()
                            if dummy_val:
                                opts.extend(
                                    [
                                        x.strip()
                                        for x in re.split(r"[,/、]", dummy_val)
                                        if x.strip()
                                    ]
                                )
                            if "英語" not in q["orig_cat"] and len(choices_from_q) > 0:
                                opts.extend(choices_from_q)
                            if len(opts) < 4:
                                cat_cands = [
                                    str(x["a"])
                                    for x in all_q.get(q["orig_cat"], [])
                                    if str(x["a"]) != ans_raw
                                ]
                                random.shuffle(cat_cands)
                                opts.extend(cat_cands)
                            opts = list(dict.fromkeys(opts))[:4]
                            random.shuffle(opts)
                            st.session_state.current_opts = opts
                        cols = st.columns(len(st.session_state.current_opts))
                        for i, o in enumerate(st.session_state.current_opts):
                            if cols[i].button(
                                str(o), key=f"opt_{idx}_{i}", width="stretch"
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
                if st.button("判定 ＆ オプション表示", width="stretch", type="primary"):
                    if time.time() - st.session_state.question_start_time < 5.0:
                        st.session_state.consecutive_speeding += 1
                        if st.session_state.consecutive_speeding >= 3:
                            st.session_state.is_cheating_flagged = True
                    else:
                        st.session_state.consecutive_speeding = 0
                    st.session_state.show_options = True
                    st.rerun()
    execute_queued_sound()
