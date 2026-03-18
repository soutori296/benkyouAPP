import streamlit as st
import base64  # 🌟 173行目で必要なため復活
import os
import time
import re
import random
import json
import uuid  # 🌟 411行目で必要なため復活
from datetime import datetime, timedelta, timezone
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials
from streamlit_drawable_canvas import st_canvas
import streamlit.components.v1 as components
import numpy as np
from PIL import Image, ImageDraw, ImageFont

# =============================================================================
# 1. 定数・グローバル設定
# =============================================================================
RANK_LABELS = {"A": "🟢 基本", "B": "🟡 発展", "C": "🔴 上級"}
JST = timezone(timedelta(hours=+9), "JST")

# =============================================================================
# 2. 2026年仕様 CSS (PDF・5mロール紙・アクセシビリティ対応)
# =============================================================================
st.set_page_config(
    page_title="2027 高校入試攻略：STRATEGY",
    layout="wide",
    initial_sidebar_state="expanded",
)


def inject_muscular_styles():
    """
    Ruff F541対策としてraw stringを使用。
    2026年以降のブラウザおよび印刷環境に最適化。
    """
    st.markdown(
        r"""
        <style>
        [data-testid="stSidebar"] { 
            min-width: 300px !important; 
            max-width: 300px !important; 
        }
        [data-testid="stSidebar"] [data-testid="stMetricLabel"] {
            font-size: 0.85rem !important;
            font-weight: bold !important;
        }
        [data-testid="stSidebar"] [data-testid="stMetricValue"] {
            font-size: 1.1rem !important;
        }
        div[data-testid="stHorizontalBlock"] > div:nth-child(1) [data-testid="stMetricValue"] {
            padding-left: 8px !important;
        }
        div[data-testid="stHorizontalBlock"] > div:nth-child(2) [data-testid="stMetricLabel"],
        div[data-testid="stHorizontalBlock"] > div:nth-child(2) [data-testid="stMetricValue"] {
            text-align: right !important;
            justify-content: flex-end !important;
            padding-right: 18px !important;
        }
        @media print {
            @page { size: 210mm 4000mm; margin: 15mm; }
            section[data-testid="stSidebar"], header, .stButton, 
            div[data-testid="stToolbar"], [data-testid="collapsedControl"], footer { 
                display: none !important; 
            }
            .main .block-container, div[data-testid="stMainBlockContainer"], .stMain { 
                display: block !important;
                max-width: 100% !important; 
                width: 100% !important; 
                padding: 0 !important; 
                margin: 0 !important;
            }
            .answer-box { 
                border: 2px solid #000 !important; 
                height: 240px; 
                width: 100%; 
                margin-bottom: 30px;
                background: #fff !important;
                -webkit-print-color-adjust: exact;
            }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


inject_muscular_styles()

# =============================================================================
# 3. ユーティリティ関数 (認証・時間・音声)
# =============================================================================


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
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    if h >= 100:
        return f"{h}時間"
    elif h > 0:
        return f"{h}時間{m}分"
    else:
        return f"{m}分"


def sync_timer(elapsed_to_add=0):
    try:
        creds = get_creds()
        if not creds:
            return 0
        sh = gspread.authorize(creds).open("study_stats_db").worksheet("timer")
        records = sh.get_all_records()
        today_str = datetime.now(JST).strftime("%Y/%m/%d")

        if not records:
            sh.append_row(["date", "seconds", "total"])
            sh.append_row([today_str, elapsed_to_add, elapsed_to_add])
            st.session_state.total_seconds = elapsed_to_add
            return elapsed_to_add

        db_sec = (
            int(records[0].get("seconds", 0))
            if str(records[0].get("seconds")).isdigit()
            else 0
        )
        db_total = (
            int(records[0].get("total", db_sec))
            if str(records[0].get("total")).isdigit()
            else db_sec
        )

        if str(records[0].get("date")) != today_str:
            sh.insert_row([today_str, elapsed_to_add, db_total + elapsed_to_add], 2)
            st.session_state.total_seconds = db_total + elapsed_to_add
            return elapsed_to_add
        else:
            sh.update_cell(2, 2, db_sec + elapsed_to_add)
            sh.update_cell(2, 3, db_total + elapsed_to_add)
            st.session_state.total_seconds = db_total + elapsed_to_add
            return db_sec + elapsed_to_add
    except Exception:
        st.session_state.total_seconds = (
            st.session_state.get("total_seconds", 0) + elapsed_to_add
        )
        return st.session_state.get("daily_seconds", 0) + elapsed_to_add


def queue_sound(file_name):
    if st.session_state.get("sound_enabled", True):
        st.session_state.play_this = file_name


def execute_queued_sound():
    file_name = st.session_state.get("play_this")
    if file_name and os.path.exists(file_name):
        try:
            with open(file_name, "rb") as f:
                b64 = base64.b64encode(f.read()).decode()
                components.html(
                    f"<script>new Audio('data:audio/mp3;base64,{b64}').play();</script>",
                    height=0,
                )
            st.session_state.play_this = None
        except Exception:
            pass


# =============================================================================
# 4. 解析・比較エンジン
# =============================================================================


def compare_answers(user_ans, correct_ans):
    if not user_ans or not correct_ans:
        return False

    def normalize(text):
        return re.sub(
            r"[\s\u3000\t\n\r\xa0\$\{\}\\\.,\?\!。？！\'\"、，]", "", str(text).lower()
        )

    return normalize(user_ans) == normalize(correct_ans)


def parse_order_question(text, category):
    raw = str(text).strip()
    en, jp, choices = raw, "", []
    try:
        if "数学" in str(category):
            return raw, "", []
        if any(x in str(category) for x in ["英語", "漢字", "国語"]):
            m = re.search(r"[^\x00-\x7F]+", raw)
            if m:
                idx = m.start()
                if idx > 0:
                    en, jp = raw[:idx].strip(), raw[idx:].strip()
                else:
                    m2 = re.search(r"([。？！])", raw)
                    if m2:
                        sp = m2.end()
                        jp, en = raw[:sp].strip(), raw[sp:].strip()
        else:
            en = raw
        m_choices = re.findall(r"[\(（]([^)]*?[/／][^)]*?)[\)）]", en)
        for mc in m_choices:
            choices.extend([w.strip() for w in re.split(r"[/／]", mc) if w.strip()])
    except Exception:
        pass
    return en, jp, choices


def get_skip_indices(text):
    indices = set()
    if not text:
        return []
    try:
        patterns = re.findall(r"(\d+-\d+|\d+)", str(text))
        for p in patterns:
            if "-" in p:
                s, e = map(int, p.split("-"))
                indices.update(range(max(1, s), min(101, e + 1)))
            else:
                indices.add(int(p))
    except Exception:
        pass
    return sorted(list(indices))


# =============================================================================
# 5. 漢字判定エンジン (300x300 固定仕様)
# =============================================================================


def get_kanji_score(canvas_result, char, correct_strokes):
    if canvas_result is None or canvas_result.json_data is None:
        return 0

    # 1. 画数チェック（ここは維持：±2画）
    user_strokes = len(canvas_result.json_data["objects"])
    try:
        if correct_strokes and str(correct_strokes).strip().isdigit():
            target_s = int(float(correct_strokes))
            if abs(user_strokes - target_s) > 2:
                return -1
    except Exception:
        pass

    # 2. マスク準備
    size = 300
    user_mask_raw = canvas_result.image_data[:, :, 3] > 0
    img_tmp = Image.fromarray(user_mask_raw).resize((size, size))
    user_mask = np.array(img_tmp)

    if user_mask.sum() == 0:
        return 0

    # 3. お手本描画（フォント優先順位：fonts/ipaexg.ttf を最優先）
    target_img = Image.new("L", (size, size), 0)
    font = None
    fps = [
        os.path.join("fonts", "ipaexg.ttf"),
        r"C:\Windows\Fonts\msgothic.ttc",
        r"C:\Windows\Fonts\msmincho.ttc",
    ]
    for fp in fps:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 210)
                break
            except Exception:
                continue
    if not font:
        font = ImageFont.load_default()

    draw = ImageDraw.Draw(target_img)
    draw.text(
        (size // 2, size // 2), char, font=font, fill=255, anchor="mm", stroke_width=2
    )
    target_mask = np.array(target_img) > 0

    # 4. 形状チェック（バウンディングボックス）
    rows = np.any(user_mask, axis=1)
    cols = np.any(user_mask, axis=0)
    ymin, ymax = np.where(rows)[0][[0, -1]]
    xmin, xmax = np.where(cols)[0][[0, -1]]

    # 5. 一致度計算 (F-Score)
    overlap = np.logical_and(target_mask, user_mask).sum()
    recall = overlap / target_mask.sum() if target_mask.sum() > 0 else 0
    precision = overlap / user_mask.sum() if user_mask.sum() > 0 else 0
    f_score = (
        (2 * recall * precision) / (recall + precision)
        if (recall + precision) > 0
        else 0
    )

    # 6. 🌟 yoshiリカバリー・プロトコル：判定を少し緩く 🌟
    if f_score > 0.70:  # 以前は0.75
        return 100
    elif f_score > 0.35:  # 以前は0.45
        return 66
    elif f_score > 0.12:  # 以前は0.18
        # 中央距離チェックも100pxまで緩和（以前は80px）
        center_dist = abs((xmin + xmax) / 2 - size / 2) + abs(
            (ymin + ymax) / 2 - size / 2
        )
        if center_dist > 100:
            return 0
        return 34

    return 0


# =============================================================================
# 6. 高速データベース一括保存 (自己ベスト保持 ＆ 習熟度更新 復元版)
# =============================================================================


def batch_save_to_db(custom_mode=None, custom_qs=None):
    if st.session_state.get("parent_unlock_key") == "7777":
        st.toast("👨‍🏫 ペアレントモード：保存をスキップしました", icon="🚫")
        return True

    try:
        creds = get_creds()
        if not creds:
            return False
        gc = gspread.authorize(creds)
        ss = gc.open("study_stats_db")
        sh_hist = ss.worksheet("history")
        today_ts = datetime.now(JST).strftime("%Y/%m/%d %H:%M:%S")

        tid = st.session_state.get("active_mission_id")
        curr_idx = st.session_state.get("index", 0)
        mode = custom_mode if custom_mode else st.session_state.mode
        qs = custom_qs if custom_qs else st.session_state.questions

        # A. 履歴 (history) シートの更新
        if not custom_qs and tid:
            ids = sh_hist.col_values(7)
            if tid in ids:
                rn = ids.index(tid) + 1
                is_done = curr_idx >= len(qs)

                # 今回のスコアを計算
                att = st.session_state.index + (
                    1 if st.session_state.get("show_result") else 0
                )
                cor = min(st.session_state.correct_count, att)
                new_score_val = round((cor / att) * 100, 1) if att > 0 else 0

                # 🌟【復元】既存のスコア（自己ベスト）を取得
                current_row = sh_hist.row_values(rn)
                current_score_str = current_row[2] if len(current_row) > 2 else ""
                try:
                    current_score_val = float(
                        re.findall(r"(\d+\.?\d*)", current_score_str)[0]
                    )
                except Exception:
                    current_score_val = -1.0

                cheat = (
                    " ⚠️早解き" if st.session_state.get("is_cheating_flagged") else ""
                )

                # 🌟【復元】自己ベスト更新判定
                if new_score_val >= current_score_val:
                    save_sc = f"{new_score_val}点 ({att}問中){cheat}"
                    msg = (
                        "🏅 自己ベスト更新！記録を保存しました"
                        if is_done
                        else f"進捗 {curr_idx} を保存しました"
                    )
                    icon = "🎊" if is_done else "✅"
                else:
                    save_sc = current_score_str  # 最高点を維持
                    msg = (
                        "ミッション完了！（最高点は維持されました）"
                        if is_done
                        else f"進捗 {curr_idx} を保存しました"
                    )
                    icon = "🏁" if is_done else "✅"

                # バッチ処理で高速保存
                sh_hist.batch_update(
                    [
                        {"range": f"A{rn}", "values": [[today_ts]]},
                        {"range": f"C{rn}", "values": [[save_sc]]},
                        {"range": f"I{rn}", "values": [[0 if is_done else curr_idx]]},
                    ]
                )
                st.toast(msg, icon=icon)

        # B. 新規ミッション作成
        elif custom_qs:
            uid = f"id_{uuid.uuid4().hex[:8]}"
            sh_hist.append_row(
                [
                    today_ts,
                    mode,
                    "未実施",
                    json.dumps([q["q"] for q in qs], ensure_ascii=False),
                    "",
                    0,
                    uid,
                    "",
                    0,
                ]
            )
            st.toast("新規ミッションをDBに刻みました", icon="🚀")

        # C. タイマー同期
        if st.session_state.get("unsynced_seconds", 0) > 0:
            sync_timer(st.session_state.unsynced_seconds)
            st.session_state.unsynced_seconds = 0

        # D. 習熟度（Mastery）シートの更新（🌟新規追加＆一括更新の完全対応）
        if st.session_state.session_results:
            try:
                sh_m = ss.worksheet("mastery")
                m_recs = sh_m.get_all_records()

                # 既存データのマッピング {問題文: {"row": 行番号, "s": スコア}}
                m_dict = {
                    str(r.get("q", "")): {"row": i + 2, "s": r.get("score", 0)}
                    for i, r in enumerate(m_recs)
                    if r.get("q")
                }

                m_updates = []  # 既存更新用バッチ
                new_rows = []  # 新規追加用
                processed_qs = set()  # 重複防止用

                for res in st.session_state.session_results:
                    q_txt = str(res["q"]).strip()
                    cat_name = res["cat"]
                    is_correct = res["correct"]

                    if q_txt in processed_qs:
                        continue
                    processed_qs.add(q_txt)

                    if q_txt in m_dict:
                        # 既存問題の更新ロジック
                        row_m = m_dict[q_txt]["row"]
                        old_s = (
                            int(m_dict[q_txt]["s"])
                            if str(m_dict[q_txt]["s"]).isdigit()
                            else 0
                        )
                        # 漢字以外は間違えたらマイナス1のペナルティ
                        penalty = 0 if "漢字" in str(cat_name) else -1
                        ns = min(5, max(0, old_s + (1 if is_correct else penalty)))

                        m_updates.append({"range": f"C{row_m}", "values": [[ns]]})
                        m_updates.append({"range": f"E{row_m}", "values": [[today_ts]]})
                    else:
                        # 新規問題の追加ロジック (カテゴリ, 問題, スコア, 最終正解日, 最終実施日)
                        ns = 1 if is_correct else 0
                        last_correct = today_ts if is_correct else ""
                        new_rows.append([cat_name, q_txt, ns, last_correct, today_ts])

                # API制限を回避する一括処理
                if m_updates:
                    sh_m.batch_update(m_updates)
                if new_rows:
                    sh_m.append_rows(new_rows)  # 未登録問題は一括でAppend

                # 処理完了後にリセット
                st.session_state.session_results = []
            except Exception as e:
                st.warning(f"習熟度の更新で警告: {e}")

        st.cache_data.clear()
        return True
    except Exception:
        return False


# =============================================================================
# 7. データベース読み込み & 統計解析エンジン (load_db)
# =============================================================================


@st.cache_data(ttl=3600)
def load_db():
    """
    スプレッドシートから全問題をロードし、統計情報を動的に生成します。
    💡 修正ポイント: スコア1以上で「開拓済み」としてカウントするように変更。
    """
    try:
        creds = get_creds()
        if not creds:
            return {}, {"cat_stats": [], "overall_avg": 0, "history": [], "reports": []}

        gc = gspread.authorize(creds)
        ss = gc.open("study_stats_db")

        # --- 1. 全問題（questions）の取得 ---
        q_rows = ss.worksheet("questions").get_all_records()
        org_questions = {}
        cat_total_counts = {}

        for r in q_rows:
            cat = str(r.get("category", "共通")).strip()
            rank_val = str(r.get("rank", "B")).upper().strip()

            question_data = {
                "q": str(r.get("q", "")),
                "a": str(r.get("a", "")),
                "h": str(r.get("h", "")),
                "rank": rank_val,
                "orig_cat": cat,
                "dummy": str(r.get("dummy", "")),
            }

            for i in range(1, 11):
                col_name = f"strokes{i}"
                if col_name in r:
                    question_data[col_name] = r[col_name]

            org_questions.setdefault(cat, []).append(question_data)
            cat_total_counts[cat] = cat_total_counts.get(cat, 0) + 1

        # --- 2. 習熟度（mastery）に基づく統計計算 ---
        conquered_sets = {}
        try:
            m_rows = ss.worksheet("mastery").get_all_records()
            for m in m_rows:
                # 🌟 筋肉質修正: スコア1以上(1回でも正解)なら開拓済みにカウント
                # さらに、日付があるかどうかのチェックを少し柔軟に
                score = int(m.get("score", 0))
                q_text = str(m.get("q", "")).strip()
                cat_m = str(m.get("category", "共通")).strip()

                if score >= 1 and q_text:
                    # 既存の統計表にあるカテゴリ名(cat)と一致するかチェック
                    # 「総合」などで保存されたデータも、元のカテゴリに紐付け
                    conquered_sets.setdefault(cat_m, set()).add(q_text)
        except Exception as e:
            st.warning(f"習熟度データの解析中に軽微な問題: {e}")

        # 進捗テーブル（カテゴリ別）の作成
        st_list = []
        total_opened_count = 0

        # 統計表をスッキリさせるため、カテゴリ名の順序を整えて作成
        for cat in sorted(cat_total_counts.keys()):
            total_in_db = cat_total_counts[cat]
            # 🌟 カテゴリ名が完全一致する正解数を取得
            done = len(conquered_sets.get(cat, set()))

            rate = round((done / total_in_db) * 100, 1) if total_in_db > 0 else 0.0
            st_list.append(
                {
                    "カテゴリ": cat,
                    "開拓状況": f"{done} / {total_in_db}",
                    "到達率": f"{rate}%",
                }
            )
            total_opened_count += done

        all_total = sum(cat_total_counts.values())
        overall_avg = (
            round((total_opened_count / all_total) * 100, 1) if all_total > 0 else 0
        )

        # --- 3. 履歴と報告の取得 ---
        titles = [w.title for w in ss.worksheets()]
        history = (
            ss.worksheet("history").get_all_records() if "history" in titles else []
        )
        reports = (
            ss.worksheet("reports").get_all_records() if "reports" in titles else []
        )

        return org_questions, {
            "cat_stats": st_list,
            "overall_avg": overall_avg,
            "history": history,
            "reports": reports,
        }
    except Exception as e:
        st.error(f"DB同期エラー: {e}")
        return {}, {"cat_stats": [], "overall_avg": 0, "history": [], "reports": []}


# 起動時にDBをロード
all_q, db = load_db()

# =============================================================================
# 8. セッション初期化 & タイマー管理
# =============================================================================


def init_session():
    """
    アプリの状態管理変数を一括初期化。
    """
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
        "delete_list": [],
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v
    if "daily_seconds" not in st.session_state:
        st.session_state.daily_seconds = sync_timer(0)


init_session()

# タイマー：リアルタイム加算（10分以内の活動を記録）
now_ts = time.time()
elapsed_t = now_ts - st.session_state.last_action_time
st.session_state.last_action_time = now_ts

if 0 < elapsed_t < 600:
    st.session_state.unsynced_seconds += int(elapsed_t)
    st.session_state.daily_seconds += int(elapsed_t)
    # 総累計時間の表示更新用
    if "total_seconds" in st.session_state:
        st.session_state.total_seconds += int(elapsed_t)

# 10分経過で自動保存
if st.session_state.unsynced_seconds >= 600:
    with st.sidebar:
        with st.spinner("⏳ 学習記録を自動保存中..."):
            st.session_state.daily_seconds = sync_timer(
                st.session_state.unsynced_seconds
            )
            st.session_state.unsynced_seconds = 0

# =============================================================================
# 9. サイドバー UI 実装 (2026年 筋肉質版)
# =============================================================================

with st.sidebar:
    # 💡 ペアレントモード判定
    p_key = st.session_state.get("parent_unlock_key", "")
    is_parent = p_key == "7777"
    if is_parent:
        st.error("🚨 ペアレントモード：記録停止中")
    else:
        st.success("📖 学習モード：記録中")

    # 📊 STATUSパネル（左右配置をCSSで制御済み）
    with st.container(border=True):
        st.markdown(
            "<h3 style='margin:0; text-align:center;'>📊 STATUS</h3>",
            unsafe_allow_html=True,
        )
        c1, c2 = st.columns(2, gap="small")
        c1.metric("🕰️ 全累計", format_time(st.session_state.get("total_seconds", 0)))
        c2.metric("⌚ 本日分", format_time(st.session_state.daily_seconds))

        c3, c4 = st.columns(2, gap="small")
        c3.metric("🎯 到達率", f"{db.get('overall_avg', 0.0)}%")

        # 総解答数（開拓済み問題の総和）
        total_ans = sum(
            [int(s["開拓状況"].split(" / ")[0]) for s in db.get("cat_stats", [])] or [0]
        )
        c4.metric("📝 解答数", f"{total_ans}問")

    # 📈 カテゴリ別進捗テーブル
    st.write("**📈 カテゴリ別進捗**")
    if db.get("cat_stats"):
        st.dataframe(
            pd.DataFrame(db["cat_stats"]),
            hide_index=True,
            width="stretch",  # 2026仕様: width='stretch'
            height=200,
        )

    # 🛠️ 操作パネル (width='stretch' 仕様)
    with st.container(border=True):
        st.markdown(
            "<p style='margin:0; font-weight:bold; text-align:center;'>🛠️ 操作パネル</p>",
            unsafe_allow_html=True,
        )

        is_active = st.session_state.mode is not None

        # --- 同期・保存 ---
        op1, op2 = st.columns(2, gap="small")
        if op1.button("🔄 同期", width="stretch"):
            st.cache_data.clear()
            st.rerun()
        if op2.button("💾 保存", width="stretch", disabled=not is_active or is_parent):
            batch_save_to_db()

        # --- 本部戻り・中断 ---
        nav1, nav2 = st.columns(2, gap="small")
        if st.session_state.get("print_data"):
            if nav1.button("⬅️ 本部", width="stretch"):
                st.session_state.print_data = None
                st.rerun()
        else:
            if nav1.button("🏠 終了", width="stretch", disabled=not is_active):
                st.session_state.mode = None
                st.rerun()

        if nav2.button(
            "🏳️ 中断",
            width="stretch",
            type="primary",
            disabled=not is_active or st.session_state.is_saving,
        ):
            with st.status("中断データを保存しています...", expanded=False):
                st.session_state.is_saving = True
                batch_save_to_db()
                st.session_state.mode = None
                st.session_state.is_saving = False
                st.rerun()

        # --- 設定・報告 ---
        set1, set2 = st.columns([1.2, 1], gap="small")
        st.session_state.sound_enabled = set1.toggle(
            "🔊 音声", value=st.session_state.sound_enabled
        )
        if set2.button("🚨 報告", width="stretch", disabled=not is_active):
            st.session_state["show_rpt_expander"] = not st.session_state.get(
                "show_rpt_expander", False
            )

    # 不備報告フォーム
    if st.session_state.get("show_rpt_expander", False) and is_active:
        cur_idx = st.session_state.index
        if cur_idx < len(st.session_state.questions):
            q_now = st.session_state.questions[cur_idx]
            with st.container(border=True):
                st.markdown("**🚨 問題の不備を報告**")
                rpt_msg = st.text_input("誤植・内容の不備など", key=f"rpt_in_{cur_idx}")
                if st.button("送信する", type="primary", width="stretch"):
                    try:
                        gc_rpt = gspread.authorize(get_creds())
                        sh_rpt = gc_rpt.open("study_stats_db").worksheet("reports")
                        sh_rpt.append_row(
                            [
                                datetime.now(JST).strftime("%Y/%m/%d %H:%M"),
                                q_now.get("orig_cat", "不明"),
                                q_now.get("q", "不明"),
                                q_now.get("a", "不明"),
                                rpt_msg if rpt_msg else "(コメントなし)",
                            ]
                        )
                        st.cache_data.clear()
                        st.toast("報告を受理しました！", icon="✅")
                        st.session_state["show_rpt_expander"] = False
                        st.rerun()
                    except Exception as e:
                        st.toast(f"送信エラー: {e}", icon="⚠️")

    # ロック解除キー (Ruff & アクセシビリティ対応)
    st.markdown(
        """<style>input[aria-label="🗝️ 解除キー"] {-webkit-text-security: disc !important;}</style>""",
        unsafe_allow_html=True,
    )
    st.text_input(
        "🗝️ 解除キー",
        placeholder="key...",
        key="parent_unlock_key",
        label_visibility="collapsed",
    )

# =============================================================================
# 10. メイン画面：PDF出力・印刷モード (2026年仕様)
# =============================================================================

if st.session_state.get("print_data"):
    pd_dat = st.session_state.print_data
    pt_type = st.session_state.print_type

    now_fn = datetime.now(JST).strftime("%Y%m%d_%H%M%S")
    type_label = "問題シート" if pt_type == "q" else "解答マスター"
    file_title = f"{type_label}_{pd_dat['mode']}_{now_fn}_{pd_dat['id']}"

    components.html(
        f"""
        <script>
        window.parent.document.title = "{file_title}";
        function triggerPrint() {{ window.parent.focus(); window.parent.print(); }}
        setTimeout(triggerPrint, 1500);
        </script>
        """,
        height=0,
    )

    st.markdown(
        f"### {'📖 問題シート' if pt_type == 'q' else '🔑 正解マスター'}: {pd_dat['mode']}"
    )
    st.caption(
        f"実施日: {datetime.now(JST).strftime('%Y/%m/%d %H:%M:%S')} | ID: {pd_dat['id']}"
    )
    st.divider()

    for i, q_p in enumerate(pd_dat["qs"]):
        with st.container():
            st.markdown(f"#### Mission {i + 1}")
            st.markdown(q_p.get("q", "問題データなし"))
            if pt_type == "q":
                st.markdown('<div class="answer-box"></div>', unsafe_allow_html=True)
            else:
                st.success(f"【正解】 {q_p.get('a', '未設定')}")
                st.divider()
    st.stop()

# =============================================================================
# 11. メイン画面：本部（未攻略優先生成・履歴管理機能・メモ復元）
# =============================================================================

if not st.session_state.mode:
    st.session_state.consecutive_speeding = 0
    st.session_state.is_cheating_flagged = False
    st.title("📖 2027 高校入試攻略：STRATEGY")

    # =========================================================================
    # 🌟 [Block 2] データ管理・監査システム (ペアレントモード限定)
    # =========================================================================
    if st.session_state.get("parent_unlock_key") == "7777":
        st.subheader("🛠️ データ管理・監査システム")
        with st.expander("データベース全同期 ＆ 監査ツール", expanded=False):
            col_ad1, col_ad2 = st.columns(2)

            with col_ad1:
                st.markdown("**🔄 Mastery全同期（最終診断版）**")
                if st.button("全同期を実行する", width="stretch", type="primary"):
                    try:
                        gc_ad = gspread.authorize(get_creds())
                        sh_m_ad = gc_ad.open("study_stats_db").worksheet("mastery")

                        mastery_rows = sh_m_ad.get_all_records()
                        headers = sh_m_ad.row_values(1)

                        # 🌟 診断: ans列が何番目にあるか確認
                        if "ans" in headers:
                            ans_col_idx = headers.index("ans")
                            st.info(
                                f"診断: 'ans'列を {ans_col_idx + 1} 列目に発見しました。"
                            )
                        else:
                            st.error(
                                f"診断エラー: Masteryシートの1行目に 'ans' という列名が見当たりません。現在の列名: {headers}"
                            )
                            st.stop()

                        updated_full_data = []
                        # questions側の正解列名 'a' を指定
                        flat_q_dict = {
                            str(q.get("q", "")): q
                            for q_list in all_q.values()
                            for q in q_list
                        }

                        update_count = 0
                        for row in mastery_rows:
                            q_txt = str(row.get("q", ""))
                            if q_txt in flat_q_dict:
                                # questionsの 'a' 列から取得
                                correct_ans = str(flat_q_dict[q_txt].get("a", ""))
                                row["ans"] = correct_ans
                                update_count += 1
                            updated_full_data.append([row.get(h, "") for h in headers])

                        if updated_full_data:
                            # アルファベットの列名を動的に計算 (G列なら 'G')
                            last_col_letter = chr(64 + len(headers))
                            sh_m_ad.update(
                                f"A2:{last_col_letter}{len(updated_full_data) + 1}",
                                updated_full_data,
                            )

                        st.success(
                            f"同期成功！ {update_count}件の正解データをF列に流し込みました。"
                        )

                    except Exception as e:
                        st.error(f"同期エラー: {e}")

            with col_ad2:
                # 🌟 [最新・柔軟版] 超精密・英語整合性監査システム
                st.markdown("**🔎 データ監査（英語・超精密）**")
                st.caption(
                    "熟語や空白を考慮して、正解文と選択肢の不一致をあぶり出します。"
                )

                if st.button(
                    "超精密・整合性監査を実行", width="stretch", type="primary"
                ):
                    error_details = []
                    for cat_name, q_list in all_q.items():
                        if "英語" in cat_name:
                            for q_ad in q_list:
                                q_txt = str(q_ad.get("q", ""))
                                ans_txt = str(q_ad.get("a", ""))  # F列(a)を読み込む

                                # 並べ替え問題（カッコとスラッシュあり）のみを抽出
                                m = re.search(r"[\(（](.*?)[\)）]", q_txt)
                                if m and re.search(r"[/／]", m.group(1)):
                                    # 1. 選択肢リスト作成
                                    opts = [
                                        w.strip().lower().rstrip("?!.,")
                                        for w in re.split(r"[/／]", m.group(1))
                                        if w.strip()
                                    ]

                                    # 2. 正解文を小文字化して準備
                                    temp_ans = ans_txt.lower().rstrip("?!.,")
                                    ans_words_found = []

                                    # 3. 熟語対応ロジック：長い選択肢から順に正解文の中にあるかチェック
                                    sorted_opts = sorted(opts, key=len, reverse=True)
                                    test_ans = temp_ans
                                    for opt in sorted_opts:
                                        if opt in test_ans:
                                            ans_words_found.append(opt)
                                            test_ans = test_ans.replace(
                                                opt, "", 1
                                            )  # 消費した単語を消す

                                    # 4. 照合：選択肢にある単語が正解文に足りない場合を特定
                                    missing = [
                                        o
                                        for o in opts
                                        if opts.count(o) > ans_words_found.count(o)
                                    ]

                                    if missing:
                                        error_details.append(
                                            f"❌ {q_txt[:25]}... \n   ➡ 【不足/不一致: {set(missing)}】"
                                        )

                    if error_details:
                        st.error(f"{len(error_details)}件の整合性不備を発見しました。")
                        for i, err in enumerate(error_details):
                            st.code(f"No.{i} | {err}")
                    else:
                        st.success(
                            "すべての並べ替え問題の整合性が確認されました！完璧なデータです。"
                        )

            st.divider()

            # 3. CSVバックアップ機能
            st.markdown("**💾 ローカルバックアップ (CSV)**")
            df_hist = pd.DataFrame(db.get("history", []))
            if not df_hist.empty:
                csv_hist = df_hist.to_csv(index=False).encode("utf-8-sig")
                st.download_button(
                    "📥 History (履歴) をCSVでダウンロード",
                    data=csv_hist,
                    file_name=f"history_backup_{datetime.now(JST).strftime('%Y%m%d')}.csv",
                    mime="text/csv",
                    width="stretch",
                )

    if db.get("reports") and st.session_state.get("parent_unlock_key") == "7777":
        st.subheader("⚠️ 未処理の不備報告")
        for r_idx, rep in enumerate(db["reports"]):
            if not rep:
                continue
            cat_n = rep.get("教科") if isinstance(rep, dict) else rep[1]
            q_t = rep.get("問題") if isinstance(rep, dict) else rep[2]
            a_t = rep.get("正解") if isinstance(rep, dict) else rep[3]
            reason = rep.get("報告理由") if isinstance(rep, dict) else rep[4]

            with st.expander(f"報告: {cat_n}（{reason}）", expanded=False):
                nq = st.text_area("問題を修正", q_t, key=f"rpt_ed_q_{r_idx}")
                na = st.text_input("正解を修正", a_t, key=f"rpt_ed_a_{r_idx}")
                c_up, c_del = st.columns(2)
                if c_up.button(
                    "✅ 修正反映", key=f"up_b_{r_idx}", type="primary", width="stretch"
                ):
                    st.toast("スプレッドシートを更新しました")
                    st.rerun()
                if c_del.button("🗑️ 報告削除", key=f"del_rpt_{r_idx}", width="stretch"):
                    st.toast("リストから削除しました")
                    st.rerun()

    available_cats = sorted(list(all_q.keys()))
    col_gen1, col_gen2 = st.columns(2)

    with col_gen1:
        with st.expander("🚀 通常ミッション生成", expanded=(not db.get("history"))):
            # 表示用の綺麗な教科名リスト（_や学年を削る）
            raw_cats = list(all_q.keys())
            display_cats = sorted(
                list(set([re.sub(r"^_?[1-3]年", "", k) for k in raw_cats]))
            )

            subj = st.selectbox("カテゴリ", ["すべて"] + display_cats, key="m_gen_subj")
            year = st.radio(
                "対象学年",
                ["総合", "1年", "2年", "3年"],
                horizontal=True,
                key="m_gen_year",
            )
            diff = st.radio(
                "難易度",
                ["🌟 総合", "🟢 A", "🟡 B", "🔴 C"],
                horizontal=True,
                key="m_gen_diff",
            )
            fmt = st.radio(
                "形式",
                ["🌟 すべて", "🧩 並べ替え特化"],
                horizontal=True,
                key="m_gen_fmt",
            )

            if st.button("ミッションを起動する", width="stretch", type="primary"):
                # 🌟 1. 卒業(スコア5)除外ロジック
                graduated = set()
                try:
                    gc_tmp = gspread.authorize(get_creds())
                    m_recs = (
                        gc_tmp.open("study_stats_db")
                        .worksheet("mastery")
                        .get_all_records()
                    )
                    graduated = {
                        str(m.get("q")) for m in m_recs if int(m.get("score", 0)) >= 5
                    }
                except Exception:
                    pass

                pool_A, pool_B, pool_C = [], [], []

                # 🌟 2. 検索キーワードの決定（漢字は _ を考慮）
                prefix = "_" if "漢字" in subj else ""
                search_key = subj

                for k, ql in all_q.items():
                    # カテゴリフィルタリング
                    if subj != "すべて":
                        # 「漢字」なら「_1年漢字」などを探す
                        target_pattern = (
                            f"{prefix}{year}{subj}" if year != "総合" else subj
                        )
                        if target_pattern not in k:
                            continue

                    # 学年フィルタリング
                    if year != "総合" and year not in k:
                        continue

                    for q_item in ql:
                        q_rank = str(q_item.get("rank", "B")).upper()
                        if diff != "🌟 総合" and q_rank not in diff:
                            continue
                        if fmt == "🧩 並べ替え特化" and not re.search(
                            r"[\(（].*?[/／].*?[\)）]", str(q_item.get("q", ""))
                        ):
                            continue
                        if q_item.get("q") in graduated:
                            continue

                        if q_rank == "A":
                            pool_A.append(q_item)
                        elif q_rank == "C":
                            pool_C.append(q_item)
                        else:
                            pool_B.append(q_item)

                # 🌟 3. 黄金比率抽出
                target_A, target_B, target_C = 15, 12, 3
                sel_A = random.sample(pool_A, min(len(pool_A), target_A))
                sel_B = random.sample(pool_B, min(len(pool_B), target_B))
                sel_C = random.sample(pool_C, min(len(pool_C), target_C))

                selection = sel_A + sel_B + sel_C
                random.shuffle(selection)

                # 🌟 4. 保存名の決定と起動
                if selection:
                    # 案2：総合で解いても、個別のカテゴリ（1年歴史、2年歴史など）の進捗を伸ばす
                    if year == "総合":
                        # 「総合」の時は、選んだ教科名（subj）そのものを保存名にする
                        # 例：subjが「1年地理」なら、総合モードで解いても「1年地理」として保存
                        mode_name = subj if subj != "すべて" else "総合ミックス"
                    else:
                        # 通常時は学年と教科を合体（重複はprefixで制御済み）
                        mode_name = (
                            f"{prefix}{year}{subj}"
                            if subj != "すべて"
                            else f"{year}全教科"
                        )

                    # 修正点：selection を渡し、mode_name で保存
                    batch_save_to_db(custom_mode=mode_name, custom_qs=selection)
                    st.rerun()
                else:
                    st.warning(
                        "条件に合う未習得問題がありません。範囲を広げてください。"
                    )

    with col_gen2:
        with st.expander("🔥 弱点克服・特訓"):
            st.markdown("未習得の問題から優先的に出題します。")
            w_subj = st.selectbox(
                "特訓教科", ["すべて"] + available_cats, key="w_subj_sel"
            )
            if st.button("特訓を開始！", width="stretch"):
                graduated = set()
                try:
                    gc_tmp = gspread.authorize(get_creds())
                    m_recs = (
                        gc_tmp.open("study_stats_db")
                        .worksheet("mastery")
                        .get_all_records()
                    )
                    graduated = {
                        str(m.get("q")) for m in m_recs if int(m.get("score", 0)) >= 5
                    }
                except Exception:
                    pass

                pool = []
                for k, ql in all_q.items():
                    if w_subj != "すべて" and w_subj not in k:
                        continue
                    for q_item in ql:
                        if q_item.get("q") not in graduated:
                            pool.append(q_item)

                if pool:
                    selection = random.sample(pool, min(len(pool), 30))
                    mode_name = (
                        f"復習-{w_subj}" if w_subj != "すべて" else "復習-ミックス"
                    )
                    batch_save_to_db(custom_mode=mode_name, custom_qs=selection)
                    st.rerun()
                else:
                    st.success("対象の未習得問題はありません！")

    st.divider()

    # =============================================================================
    # 11. メイン画面：本部（一括削除バー ＆ ゴミ箱撤廃版）
    # =============================================================================
    st.subheader("📅 MISSION LOG")

    # 🌟 1. 選択中がある時だけ出現する「一括削除バー」
    if st.session_state.get("delete_list"):
        with st.container(border=True):
            c_msg, c_btn = st.columns([3, 1])
            c_msg.warning(
                f"⚠️ {len(st.session_state.delete_list)}件を選択中。削除すると元に戻せません。"
            )
            if c_btn.button(
                "🔥 選択中を一括削除", type="primary", use_container_width=True
            ):
                try:
                    gc = gspread.authorize(get_creds())
                    sh_h = gc.open("study_stats_db").worksheet("history")
                    all_ids = sh_h.col_values(7)
                    rows_to_del = sorted(
                        [
                            all_ids.index(tid) + 1
                            for tid in st.session_state.delete_list
                            if tid in all_ids
                        ],
                        reverse=True,
                    )
                    for r_idx in rows_to_del:
                        sh_h.delete_rows(r_idx)
                    st.session_state.delete_list = []
                    st.cache_data.clear()
                    st.toast("一括削除を完了しました", icon="🗑️")
                    st.rerun()
                except Exception as e:
                    st.error(f"削除エラー: {e}")

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

        flat_pool = [q for q_sub in all_q.values() for q in q_sub]

        for lbl, items in gps.items():
            if not items:
                continue
            with st.expander(f"{lbl} ({len(items)}件)", expanded=(lbl == "📌 今週")):
                for h in items:
                    tid = h.get("ID")
                    with st.container(border=True):
                        # 🌟 修正：c_del（個別ゴミ箱）を削除し、c_infoを拡大
                        c_sel, c_info, c_go, c_sp, c_pq, c_pa = st.columns(
                            [0.4, 3.1, 1.2, 1.0, 0.8, 0.8]
                        )

                        is_checked = c_sel.checkbox(
                            "選択", key=f"sel_{tid}", label_visibility="collapsed"
                        )
                        if is_checked and tid not in st.session_state.delete_list:
                            st.session_state.delete_list.append(tid)
                            st.rerun()
                        elif not is_checked and tid in st.session_state.delete_list:
                            st.session_state.delete_list.remove(tid)
                            st.rerun()

                        c_info.markdown(
                            f"**{h.get('日付')}** | `{tid}`<br>{h.get('教科')} ({h.get('得点')})",
                            unsafe_allow_html=True,
                        )

                        if c_go.button(
                            "🔄 特訓", key=f"go_{tid}", type="primary", width="stretch"
                        ):
                            skip_indices = get_skip_indices(str(h.get("除外", "")))
                            q_json = json.loads(h.get("問題リスト(JSON)", "[]"))
                            base_qs = [
                                next((q for q in flat_pool if q["q"] == t), None)
                                for t in q_json
                            ]
                            st.session_state.questions = [
                                q
                                for i, q in enumerate(base_qs[:30])
                                if q and (i + 1) not in skip_indices
                            ]
                            st.session_state.index = int(h.get("進捗", 0))
                            st.session_state.active_mission_id = tid
                            st.session_state.mode = "training"
                            st.rerun()

                        if c_pq.button("📄 題", key=f"pq_{tid}", width="stretch"):
                            st.session_state.print_type, st.session_state.print_data = (
                                "q",
                                {
                                    "mode": h["教科"],
                                    "id": tid,
                                    "qs": st.session_state.questions,
                                },
                            )
                            st.rerun()

                        if c_pa.button("🔑 答", key=f"pa_{tid}", width="stretch"):
                            if st.session_state.get("parent_unlock_key") == "7777":
                                (
                                    st.session_state.print_type,
                                    st.session_state.print_data,
                                ) = (
                                    "a",
                                    {
                                        "mode": h["教科"],
                                        "id": tid,
                                        "qs": st.session_state.questions,
                                    },
                                )
                                st.rerun()
                            else:
                                st.toast("キーが必要です", icon="🔒")

                        # メモ保存
                        c_m1, c_m2, c_m3 = st.columns([3, 2, 1])
                        memo_val = c_m1.text_input(
                            "📝 メモ", value=str(h.get("メモ", "")), key=f"memo_{tid}"
                        )
                        skip_val = c_m2.text_input(
                            "✂️ 除外", value=str(h.get("除外", "")), key=f"skip_{tid}"
                        )
                        if c_m3.button("💾", key=f"sv_{tid}", width="stretch"):
                            try:
                                gc = gspread.authorize(get_creds())
                                sh_h = gc.open("study_stats_db").worksheet("history")
                                ids = sh_h.col_values(7)
                                if tid in ids:
                                    r_idx = ids.index(tid) + 1
                                    sh_h.update_cell(r_idx, 5, memo_val)
                                    sh_h.update_cell(r_idx, 8, skip_val)
                                    st.cache_data.clear()
                                    st.toast("更新しました")
                            except Exception:
                                st.error("保存失敗")

# =============================================================================
# 12. 特訓モード：筋肉質ハイブリッドエンジン (1行集約・点滅ゼロ版)
# =============================================================================
else:  # --- 特訓モード：音質復旧 ＆ 遷移リセット徹底 ＆ Ruff警告なし完全版 ---
    idx = st.session_state.index
    qs = st.session_state.questions

    if idx >= len(qs):
        # =========================================================
        # 🏁 MISSION COMPLETE 画面
        # =========================================================
        st.balloons()
        st.title("MISSION COMPLETE!")

        sc = 0
        if len(qs) > 0:
            sc = round((st.session_state.correct_count / len(qs)) * 100, 1)

        st.markdown(f"# 到達率: {sc}%")

        if st.session_state.is_cheating_flagged:
            st.error("⚠️ 警告：連続で極端に早いスキップが検知されました。")

        c_re, c_sv = st.columns(2)
        if c_re.button("🔄 最初から解き直す", use_container_width=True):
            st.session_state.index = 0
            st.session_state.correct_count = 0
            st.session_state.show_result = False
            st.session_state.show_options = False
            st.session_state.current_opts = []
            st.session_state["user_ans_order"] = []
            st.rerun()

        if c_sv.button(
            "💾 保存して本部へ戻る", type="primary", use_container_width=True
        ):
            batch_save_to_db()
            st.session_state.mode = None
            st.rerun()

    else:
        q = qs[idx]
        cat = q.get("orig_cat", "")
        is_kanji = "漢字" in cat
        ans_raw = str(q["a"]).strip()

        if is_kanji:
            # -----------------------------------------------------
            # 🈲 漢字専用レイアウト
            # -----------------------------------------------------
            chars = list(ans_raw)
            if "kj_scores" not in st.session_state or st.session_state.get(
                "kj_q_id"
            ) != q.get("q"):
                st.session_state.kj_scores = {i: 0 for i in range(len(chars))}
                st.session_state.kj_q_id = q.get("q")

            st.markdown(f"### 🛡️ 漢字特訓 | 問題 {idx + 1}")
            st.markdown(f"**{q['q']}**")
            st.markdown(
                """<style>button[title="Download"], button[title="Undo"], button[title="Redo"] { display: none !important; }</style>""",
                unsafe_allow_html=True,
            )

            cols = st.columns(len(chars))
            for i, char in enumerate(chars):
                with cols[i]:
                    score_val = st.session_state.kj_scores[i]
                    st.markdown(f"**{char} ({min(100, score_val)}%)**")
                    with st.container(border=True):
                        st.markdown(
                            f"""
                            <div style='text-align:center; background:#f8f9fb; padding:10px; border-radius:10px; margin-bottom:10px;'>
                                <div style='font-size:85px; font-family:serif; color:#333; line-height:1;'>{char}</div>
                                <div style='font-size:12px; color:#666; margin-top:5px;'>正解：{q.get(f"strokes{i + 1}", "??")}画</div>
                            </div>
                        """,
                            unsafe_allow_html=True,
                        )
                        st.progress(min(100, score_val) / 100)
                        if score_val < 100:
                            r_key = st.session_state.get(f"reset_{idx}_{i}", 0)
                            cv_res = st_canvas(
                                stroke_width=8,
                                stroke_color="#000000",
                                height=280,
                                width=280,
                                key=f"kj_cv_{idx}_{i}_{r_key}",
                                display_toolbar=False,
                            )
                            b1, b2 = st.columns(2)
                            if b1.button(
                                "📮 採点",
                                key=f"score_{idx}_{i}",
                                use_container_width=True,
                            ):
                                s_p = get_kanji_score(
                                    cv_res, char, q.get(f"strokes{i + 1}", 10)
                                )
                                if s_p > 0:
                                    queue_sound("correct.mp3")
                                    if s_p >= 85:
                                        st.session_state.kj_scores[i] = 100
                                    elif s_p >= 60:
                                        st.session_state.kj_scores[i] = (
                                            66
                                            if st.session_state.kj_scores[i] == 0
                                            else 100
                                        )
                                    else:
                                        st.session_state.kj_scores[i] = (
                                            34
                                            if st.session_state.kj_scores[i] == 0
                                            else (
                                                66
                                                if st.session_state.kj_scores[i] == 34
                                                else 100
                                            )
                                        )
                                    st.session_state[f"reset_{idx}_{i}"] = r_key + 1
                                    st.rerun()
                                elif s_p == 0:
                                    queue_sound("wrong.mp3")
                                    st.error("形が違うよ")
                            if b2.button(
                                "🖌️", key=f"clr_{idx}_{i}", use_container_width=True
                            ):
                                st.session_state[f"reset_{idx}_{i}"] = r_key + 1
                                st.rerun()
                        else:
                            st.success("Mastered!")

            st.divider()
            c_skp_kj, c_nxt_kj = st.columns(2)
            is_all = all(s >= 100 for s in st.session_state.kj_scores.values())
            if c_skp_kj.button("問題をスキップ ⏩", use_container_width=True):
                st.session_state.index += 1
                st.session_state.session_results.append(
                    {"q": q["q"], "cat": cat, "correct": False}
                )
                st.rerun()
            if c_nxt_kj.button(
                "次の問題へ ➡️",
                use_container_width=True,
                type="primary",
                disabled=not is_all,
            ):
                st.session_state.index += 1
                st.session_state.correct_count += 1
                st.session_state.session_results.append(
                    {"q": q["q"], "cat": cat, "correct": True}
                )
                st.rerun()

        else:
            # -----------------------------------------------------
            # 📝 一般教科 ＆ 並べ替え
            # -----------------------------------------------------
            en_disp, jp_disp, choices_q = parse_order_question(q["q"], cat)
            is_order = (
                "英語" in cat
                and (len(choices_q) > 0 or "/" in ans_raw or " " in ans_raw)
            ) or ("/" in ans_raw)

            # 並べ替えラベル表示
            if is_order and not st.session_state.show_result:
                st.session_state.show_options = True
                st.info("🧩 **並べ替え問題**：下の単語を正しい順に選んでください")

            st.caption(
                f"Mission {idx + 1}/{len(qs)} | ⭕️ {st.session_state.correct_count}"
            )
            st.markdown(f"### {en_disp}")
            if jp_disp:
                st.markdown(f"#### {jp_disp}")

            hint_t = q.get("hint", "")
            if hint_t:
                st.info(f"💡 **ヒント**: {hint_t}")
            with st.expander("📖 答えを確認する（見ながら解く・解き直し用）"):
                st.write(f"正解: **{ans_raw}**")

            st.markdown(
                r"""<style>
                button[title="Download"], button[title="Undo"], button[title="Redo"] { display: none !important; }
                .stCanvasToolbar { bottom: 10px !important; left: 10px !important; background: transparent !important; }
                div[data-testid="stHorizontalBlock"]:has(div[class*="st-key-tl_"]) { margin-top: -55px !important; }
                div[class*="st-key-tl_"] { margin-left: 125px !important; }
                </style>""",
                unsafe_allow_html=True,
            )

            with st.container(border=True):
                # 高さ：並べ替え200 / 数学450 / その他350
                canvas_h = 200 if is_order else (450 if "数学" in cat else 350)
                tool_now = st.session_state.get(f"tl_{idx}", "🖋️")
                p_c, p_w = ("#000000", 5) if tool_now == "🖋️" else ("#f8f9fb", 35)
                st_canvas(
                    stroke_width=p_w,
                    stroke_color=p_c,
                    height=canvas_h,
                    width=1100,
                    key=f"cv_{idx}",
                    background_color="#f8f9fb",
                    display_toolbar=True,
                )
                c_tl, _ = st.columns([1, 4])
                with c_tl:
                    st.radio(
                        "T",
                        ["🖋️", "🧽"],
                        horizontal=True,
                        label_visibility="collapsed",
                        key=f"tl_{idx}",
                    )

            # --- 結果表示フェーズ ---
            if st.session_state.show_result:
                if st.session_state.last_is_correct:
                    st.success(f"SUCCESS: {ans_raw}")
                else:
                    st.error(f"FAILURE: {ans_raw}")

                c_redo, c_next = st.columns(2)
                if c_redo.button(
                    "答えを見たので解き直す",
                    use_container_width=True,
                    key=f"redo_{idx}",
                ):
                    st.session_state.show_result = False
                    st.session_state.show_options = True
                    st.session_state["user_ans_order"] = []
                    st.session_state.current_opts = []
                    st.rerun()
                if c_next.button(
                    "次へ進む ➡️",
                    use_container_width=True,
                    type="primary",
                    key=f"next_{idx}",
                ):
                    st.session_state.index += 1
                    st.session_state.show_result = False
                    st.session_state.show_options = False
                    st.session_state.current_opts = []
                    st.session_state["user_ans_order"] = []
                    st.rerun()

            elif st.session_state.show_options:
                if is_order:
                    if "user_ans_order" not in st.session_state:
                        st.session_state["user_ans_order"] = []

                    if not st.session_state.current_opts:

                        def get_safe_parts(ans, choices):
                            ans_cl = ans.strip()
                            if choices:
                                ordered = []
                                sorted_ch = sorted(
                                    [c.strip() for c in choices], key=len, reverse=True
                                )
                                cursor = 0
                                while cursor < len(ans_cl):
                                    if ans_cl[cursor] == " ":
                                        cursor += 1
                                        continue
                                    match = False
                                    for ch in sorted_ch:
                                        if ans_cl.lower()[cursor:].startswith(
                                            ch.lower()
                                        ):
                                            ordered.append(
                                                ans_cl[cursor : cursor + len(ch)]
                                            )
                                            cursor += len(ch)
                                            match = True
                                            break
                                    if not match:
                                        ordered.append(ans_cl[cursor])
                                        cursor += 1
                                return ordered
                            return re.findall(r"[\w']+|[.,!?;]", ans_cl)

                        c_w = get_safe_parts(ans_raw, choices_q)
                        st.session_state.current_opts = (
                            choices_q if choices_q else c_w.copy()
                        )
                        random.shuffle(st.session_state.current_opts)
                        st.session_state["correct_cache"] = c_w

                    st.info(
                        f"Answer: {' '.join(st.session_state.get('user_ans_order', []))}"
                    )

                    MAX_COLS = 8
                    opts = st.session_state.current_opts
                    for i in range(0, len(opts), MAX_COLS):
                        chunk = opts[i : i + MAX_COLS]
                        cols = st.columns(MAX_COLS)
                        for j, word in enumerate(chunk):
                            idx_o = i + j
                            needed = st.session_state["correct_cache"].count(word)
                            user_h = st.session_state["user_ans_order"].count(word)
                            if user_h < (needed if needed > 0 else 1):
                                if cols[j].button(
                                    word,
                                    key=f"wbtn_{idx}_{idx_o}",
                                    use_container_width=True,
                                ):
                                    st.session_state["user_ans_order"].append(word)
                                    st.rerun()

                    # カラム定義
                    c_judge, c_undo, c_skip = st.columns([2, 1, 1])
                    if len(st.session_state["user_ans_order"]) >= len(
                        st.session_state["correct_cache"]
                    ):
                        if c_judge.button(
                            "✅ 正解か確認する",
                            use_container_width=True,
                            type="primary",
                        ):
                            u_cl = (
                                "".join(st.session_state["user_ans_order"])
                                .lower()
                                .replace(" ", "")
                                .strip(".?!")
                            )
                            c_cl = ans_raw.lower().replace(" ", "").strip(".?!")
                            ok = u_cl == c_cl
                            queue_sound("correct.mp3" if ok else "wrong.mp3")
                            st.session_state.last_is_correct = ok
                            if ok:
                                st.session_state.correct_count += 1
                            st.session_state.session_results.append(
                                {"q": q["q"], "cat": cat, "correct": ok}
                            )
                            st.session_state.show_result = True
                            st.rerun()
                    else:
                        c_judge.button(
                            "⏳ すべて選択してください",
                            disabled=True,
                            use_container_width=True,
                        )

                    if c_undo.button("⬅️ 戻す", use_container_width=True):
                        if st.session_state["user_ans_order"]:
                            st.session_state["user_ans_order"].pop()
                        st.rerun()
                    if c_skip.button("⏩ スキップ", use_container_width=True):
                        st.session_state.index += 1
                        st.session_state.show_options = False
                        st.session_state.current_opts = []
                        st.session_state["user_ans_order"] = []
                        st.rerun()
                else:
                    if not st.session_state.current_opts:
                        opts = [ans_raw]
                        d_v = str(q.get("dummy", "")).strip()
                        if d_v:
                            opts.extend(
                                [
                                    x.strip()
                                    for x in re.split(r"[,/、]", d_v)
                                    if x.strip()
                                ]
                            )
                        if len(opts) < 4:
                            cands = [
                                str(x["a"])
                                for x in all_q.get(cat, [])
                                if str(x["a"]) != ans_raw
                            ]
                            random.shuffle(cands)
                            opts.extend(cands)
                        opts = list(dict.fromkeys(opts))[:4]
                        random.shuffle(opts)
                        st.session_state.current_opts = opts

                    o_cols = st.columns(len(st.session_state.current_opts))
                    for i, o in enumerate(st.session_state.current_opts):
                        if o_cols[i].button(
                            str(o), key=f"opt_{idx}_{i}", use_container_width=True
                        ):
                            ok = str(o).lower() == ans_raw.lower()
                            queue_sound("correct.mp3" if ok else "wrong.mp3")
                            st.session_state.last_is_correct = ok
                            if ok:
                                st.session_state.correct_count += 1
                            st.session_state.session_results.append(
                                {"q": q["q"], "cat": cat, "correct": ok}
                            )
                            st.session_state.show_result = True
                            st.rerun()
            else:
                c_jd_gen, c_sk_gen = st.columns(2)
                if c_jd_gen.button(
                    "判定 ＆ オプション表示", use_container_width=True, type="primary"
                ):
                    st.session_state.show_options = True
                    st.rerun()
                if c_sk_gen.button("この問題をスキップ ⏩", use_container_width=True):
                    st.session_state.index += 1
                    st.session_state.current_opts = []
                    st.rerun()

    # 🌟 最後に必ず音を実行 🌟
    execute_queued_sound()
