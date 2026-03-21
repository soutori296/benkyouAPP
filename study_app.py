import streamlit as st
import base64  # noqa: F401  # 173行目等で使用するため維持
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


def queue_sound(file_path):
    # ファイルが存在するかチェック
    if os.path.exists(file_path):
        with open(file_path, "rb") as f:
            data = f.read()
            # 音源をテキストデータ(Base64)に変換して予約
            b64 = base64.b64encode(data).decode()
            st.session_state["sound_queue_b64"] = b64
    else:
        print(f"警告: {file_path} が見つかりません")


def execute_queued_sound():
    if "sound_queue_b64" in st.session_state and st.session_state["sound_queue_b64"]:
        b64_data = st.session_state["sound_queue_b64"]
        # 💡 HTMLに音源データを直接埋め込んで、強制的に再生させる
        st.components.v1.html(
            f"""
            <audio autoplay style="display:none;">
                <source src="data:audio/mp3;base64,{b64_data}" type="audio/mp3">
            </audio>
            <script>
                // ブラウザの制限を回避するための予備命令
                var audio = document.querySelector('audio');
                audio.play().catch(e => console.log('再生失敗:', e));
            </script>
            """,
            height=0,
        )
        # 鳴らした後は空にする
        st.session_state["sound_queue_b64"] = None


def to_pretty_display(text):
    """ボタン・ヒント用：LaTeXを記号・変数イタリック・エスケープ掃除込みで変換する"""
    if not isinstance(text, str):
        return text

    # 1. $ を消去
    t = text.replace("$", "")

    # 2. 【新規追加】LaTeXのエスケープ記号（\% など）を普通の記号に戻す
    # % や $ や _ などの前にある \ を取り除きます
    escape_chars = ["%", "$", "_", "{", "}", "&", "#"]
    for char in escape_chars:
        t = t.replace(f"\\{char}", char)

    # 3. 算数・数学の特殊記号（\times など）
    replacements = {
        "\\times": "×",
        "\\div": "÷",
        "\\pm": "±",
        "\\leqq": "≦",
        "\\geqq": "≧",
        "\\le": "≦",
        "\\ge": "≧",
        "\\pi": "π",
        "\\approx": "≒",
        "\\therefore": "∴",
        "\\because": "∵",
        "\\triangle": "△",
        "\\angle": "∠",
        "\\infty": "∞",
    }
    for old, new in replacements.items():
        t = t.replace(old, new)

    # 3. \text{...} の中身だけを取り出す（化学式などはここに含まれる）
    t = re.sub(r"\\text\{([^}]*)\}", r"\1", t)

    # 4. 数学の変数を数式用イタリック文字に一括変換
    # 教科書でよく使う文字を網羅（a-z）
    var_map = {
        "a": "𝑎",
        "b": "𝑏",
        "c": "𝑐",
        "d": "𝑑",
        "e": "𝑒",
        "f": "𝑓",
        "g": "𝑔",
        "h": "ℎ",
        "i": "𝑖",
        "j": "𝑗",
        "k": "𝑘",
        "l": "𝑙",
        "m": "𝑚",
        "n": "𝑛",
        "o": "𝑜",
        "p": "𝑝",
        "q": "𝑞",
        "r": "𝑟",
        "s": "𝑠",
        "t": "𝑡",
        "u": "𝑢",
        "v": "𝑣",
        "w": "𝑤",
        "x": "𝑥",
        "y": "𝑦",
        "z": "𝑧",
    }

    # 独立したアルファベット1文字のみを変換（化学式の H や O を避けるため）
    for eng, math in var_map.items():
        # 前後に他のアルファベットがない場合のみ置換する
        t = re.sub(rf"(^|[^a-zA-Z]){eng}([^a-zA-Z]|$)", rf"\1{math}\2", t)

    # 5. 下付き・上付き文字の変換
    sub_map = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
    t = re.sub(r"_\{?(\d+)\}?", lambda m: m.group(1).translate(sub_map), t)

    sup_map = str.maketrans("0123456789", "⁰¹²³⁴⁵⁶⁷⁸⁹")
    t = re.sub(r"\^\{?(\d+)\}?", lambda m: m.group(1).translate(sup_map), t)

    # 6. 残った不要な中括弧を消去
    t = t.replace("{", "").replace("}", "")

    return t


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
        /* 🌟 【追加：縦の隙間を詰める】 🌟 */
        /* ステータス行（Mission...）の下の余白を削る */
        [data-testid="stHorizontalBlock"] {
            margin-bottom: -10px !important; 
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


def assign_missing_ids():
    """
    O列(15列目)が空の行に、一括でUUIDを付与する（API制限回避版）
    """
    try:
        creds = get_creds()
        client = gspread.authorize(creds)
        sh = client.open("study_stats_db").worksheet("questions")

        # 全データを取得
        records = sh.get_all_values()
        if not records:
            return

        # 1. 既存の全IDを取得してダブりチェック用セットを作成
        existing_ids = {row[14] for row in records if len(row) >= 15 and row[14]}

        # 更新用リストを準備
        cells_to_update = []
        updated_count = 0

        with st.spinner("一括更新データを準備中..."):
            for i, row in enumerate(records[1:], start=2):  # 2行目から
                current_id = row[14] if len(row) >= 15 else ""

                if not current_id or str(current_id).strip() == "":
                    # 重複しないIDを生成
                    new_uuid = str(uuid.uuid4())
                    while new_uuid in existing_ids:
                        new_uuid = str(uuid.uuid4())

                    # 💡 書き込み予約（セルオブジェクトを作成）
                    from gspread.cell import Cell

                    cells_to_update.append(Cell(row=i, col=15, value=new_uuid))
                    existing_ids.add(new_uuid)
                    updated_count += 1

        # 2. 💡 まとめて一括書き込み（ここがAPI節約のポイント）
        if cells_to_update:
            with st.spinner(f"{updated_count}件をスプレッドシートに一括保存中..."):
                sh.update_cells(cells_to_update)
            st.success(f"✅ {updated_count}件の問題に新しいIDを付与しました。")
        else:
            st.info("ℹ️ すべての問題にIDが設定済みです。")

    except Exception as e:
        st.error(f"ID付与エラー: {e}")


def archive_and_delete_question(q_data):
    """
    指定された問題を 'deleted_questions' シートへ移動し、元から削除する
    """
    try:
        creds = get_creds()
        client = gspread.authorize(creds)
        ss = client.open("study_stats_db")
        main_sh = ss.worksheet("questions")

        target_id = q_data.get("id")
        if not target_id:
            st.error(
                "この問題にはIDがないため削除できません。管理者にID付与を依頼してください。"
            )
            return

        # 1. 元シートからIDを探して行番号を特定
        id_col = main_sh.col_values(15)
        try:
            row_idx = id_col.index(str(target_id)) + 1
        except ValueError:
            st.error("スプレッドシート上で対象のIDが見つかりませんでした。")
            return

        # 2. 削除用シートへ移動
        try:
            del_sh = ss.worksheet("deleted_questions")
        except Exception:
            del_sh = ss.add_worksheet(title="deleted_questions", rows="100", cols="20")
            del_sh.append_row(list(q_data.keys()) + ["deleted_at"])

        archive_row = list(q_data.values()) + [
            datetime.now(timezone(timedelta(hours=9))).isoformat()
        ]
        del_sh.append_row(archive_row)

        # 3. 物理削除
        main_sh.delete_rows(row_idx)
        st.toast("問題をアーカイブへ移動しました。")
        time.sleep(1)
        st.rerun()
    except Exception as e:
        st.error(f"削除失敗: {e}")


# -----------------------------------------------------------------------------
# 🛠️ データベース保守用：特定IDの問題を削除＆退避する関数
# -----------------------------------------------------------------------------
def delete_question_by_id(target_id):
    """
    指定されたIDの問題を questions シートから削除し、deleted_questions シートへ移動
    """
    try:
        # スプレッドシートへの接続
        gc = gspread.authorize(get_creds())
        doc = gc.open("study_stats_db")  # ←ここ、実際のシート名と合っているか確認！
        sh_q = doc.worksheet("questions")

        # 退避用シート（deleted_questions）を開く。なければ作成。
        try:
            sh_del = doc.worksheet("deleted_questions")
        except Exception:
            sh_del = doc.add_worksheet(title="deleted_questions", rows="100", cols="20")
            # 1行目にヘッダーを入れる
            sh_del.append_row(sh_q.row_values(1))

        # 1. シートの全データを取得（API節約のため一括取得）
        all_rows = sh_q.get_all_values()
        id_column_idx = 14  # O列は0から数えて14番目

        found_row_idx = -1
        row_content = []

        for i, row in enumerate(all_rows):
            # 1行目（ヘッダー）は飛ばす
            if i == 0:
                continue

            # IDが一致するか確認
            if len(row) > id_column_idx and str(row[id_column_idx]) == str(target_id):
                found_row_idx = i + 1  # Googleシートは1行目から始まるので+1
                row_content = row
                break

        if found_row_idx > 1:
            # 2. 退避用シートにデータを追加
            sh_del.append_row(row_content)
            # 3. 元のシートからその行を削除
            sh_q.delete_rows(found_row_idx)
            return True
        else:
            return False

    except Exception as e:
        st.error(f"削除エラー発生: {e}")
        return False


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
    """
    230サイズで判定を行います。
    補助線なし、位置補正なしのストイックな記憶判定ロジックです。
    """
    if canvas_result is None or canvas_result.json_data is None:
        return 0, "まずは一画書いてみよう！"

    # 1. 画数チェック (±2画)
    user_strokes = len(canvas_result.json_data["objects"])
    try:
        if correct_strokes and str(correct_strokes).strip().isdigit():
            target_s = int(float(correct_strokes))
            if abs(user_strokes - target_s) > 2:
                return -1, f"画数が違います（現在 {user_strokes} 画）。"
    except Exception:
        pass

    # 2. マスク準備 (230x230サイズ)
    size = 230
    user_mask_raw = canvas_result.image_data[:, :, 3] > 0
    if user_mask_raw.sum() == 0:
        return 0, "形をイメージしてから書いてみよう。"

    # 230x230としてそのまま判定（位置のズレを許容しない）
    user_mask = np.array(Image.fromarray(user_mask_raw).resize((size, size)))

    # 3. お手本描画
    target_img = Image.new("L", (size, size), 0)
    font = None
    # 230サイズに合わせてフォントサイズを 165 程度に調整
    fps = [
        os.path.join("fonts", "ipaexg.ttf"),
        "/usr/share/fonts/truetype/fonts-japanese-gothic.ttf",
        r"C:\Windows\Fonts\msgothic.ttc",
    ]
    for fp in fps:
        if os.path.exists(fp):
            try:
                font = ImageFont.truetype(fp, 165)
                break
            except Exception:
                continue
    if not font:
        font = ImageFont.load_default()
    draw = ImageDraw.Draw(target_img)
    draw.text((size // 2, size // 2), char, font=font, fill=255, anchor="mm")
    target_mask = np.array(target_img) > 0

    # 4. 一致度計算 (F-Score)
    overlap = np.logical_and(target_mask, user_mask).sum()
    recall = overlap / target_mask.sum() if target_mask.sum() > 0 else 0
    precision = overlap / user_mask.sum() if user_mask.sum() > 0 else 0
    f_score = (
        (2 * recall * precision) / (recall + precision)
        if (recall + precision) > 0
        else 0
    )

    # 5. スコア決定（階段式 34/66/100）
    if f_score > 0.65:
        return 100, "バッチリ！完璧に思い出せましたね💮"
    elif f_score > 0.35:
        return 66, "だいたい合っています！一度クリアして書き直してみよう。"
    elif f_score > 0.15:
        return 34, "場所は捉えられています！次は形（パーツ）を思い出して。"

    return 0, "位置がずれているかもしれません。お手本をよく見て思い出そう。"


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


def get_cooldown_questions(history, cooldown=3):
    """直近n回分の履歴から問題テキストを抽出する"""
    recent_texts = set()
    # 履歴の最後から指定回数分をループ
    for record in history[-cooldown:]:
        q_list_str = record.get("問題リスト(JSON)", "[]")
        try:
            # 保存されている問題リストを読み込む
            q_list = json.loads(q_list_str)
            for q_item in q_list:
                # 辞書形式なら 'q' キー、文字列ならそのまま追加
                if isinstance(q_item, dict):
                    recent_texts.add(q_item.get("q"))
                else:
                    recent_texts.add(q_item)
        except Exception:
            continue
    return recent_texts


@st.cache_data(ttl=3600)
def load_db():
    """
    スプレッドシートから全問題をロードし、統計情報を動的に生成します。
    """
    try:
        creds = get_creds()
        if not creds:
            return {}, {"cat_stats": [], "overall_avg": 0, "history": [], "reports": []}

        gc = gspread.authorize(creds)
        ss = gc.open("study_stats_db")

        # --- 1. 全問題（questions）の取得と名寄せ ---
        q_rows = ss.worksheet("questions").get_all_records()

        # 🌟 pandasを使用して物理的に重複を排除し、正解を一対一に固定する
        import pandas as pd

        df_raw = pd.DataFrame(q_rows)

        def normalize_q(text):
            if not isinstance(text, str):
                return text
            text = re.sub(r"^(単語：|英単語：|【英単語】\s*)", "", text)
            text = re.sub(r"[\s　、。！？!?,.()（）]", "", text)
            return text

        if not df_raw.empty and "q" in df_raw.columns:
            # 比較用キーで重複を削り、最初の1行を「絶対の正解」として採用
            df_raw["q_comparison"] = df_raw["q"].apply(normalize_q)
            df_raw = df_raw.drop_duplicates(subset=["q_comparison"], keep="first")
            q_rows = df_raw.to_dict("records")

        # O列(15列目)の実際の名前を取得
        q_sheet = ss.worksheet("questions")
        q_headers = q_sheet.row_values(1)
        id_col_name = q_headers[14] if len(q_headers) >= 15 else "id"

        org_questions = {}
        cat_total_counts = {}

        for r in q_rows:
            cat = str(r.get("category", "共通")).strip()
            rank_val = str(r.get("rank", "B")).upper().strip()

            # ダミー案(p_dummy)を優先し、正解(a)と重複していれば除去
            correct_ans = str(r.get("a", "")).strip()
            raw_dummy = str(
                r.get("p_dummy") if r.get("p_dummy") else r.get("dummy", "")
            )
            clean_dummies = [
                d.strip()
                for d in re.split(r"[,、]", raw_dummy)
                if d.strip() and d.strip() != correct_ans
            ]

            question_data = {
                "id": str(r.get(id_col_name, "")).strip(),
                "q": str(r.get("q", "")),
                "a": correct_ans,
                "h": str(r.get("h", "")),
                "rank": rank_val,
                "orig_cat": cat,
                "dummy": ", ".join(clean_dummies),
                "unit": str(r.get("unit", r.get("sub_category", ""))),
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
                score = int(m.get("score", 0))
                q_text = str(m.get("q", "")).strip()
                cat_m = str(m.get("category", "共通")).strip()
                if score >= 1 and q_text:
                    conquered_sets.setdefault(cat_m, set()).add(q_text)
        except Exception:
            pass

        # --- 🚩 進捗テーブルの作成 ---
        st_list = []
        total_opened_count = 0
        for cat in cat_total_counts.keys():
            total_in_db = cat_total_counts[cat]
            done = len(conquered_sets.get(cat, set()))
            rate = round((done / total_in_db) * 100, 1) if total_in_db > 0 else 0.0
            st_list.append(
                {
                    "カテゴリ": cat,
                    "開拓状況": f"{done} / {total_in_db}",
                    "🚩 開拓率": rate,
                }
            )
            total_opened_count += done

        st_list.sort(key=lambda x: x["🚩 開拓率"])
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


# 🌟 この代入行が、サイドバーやメインパネルの UI コードより「上」にあることを確認してください
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
        "show_help_persistence": False,  # 💡 ヘルプの状態を保持する変数
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

# タイマー：リアルタイム加算（240秒以内の活動を記録）
now_ts = time.time()
elapsed_t = now_ts - st.session_state.last_action_time
st.session_state.last_action_time = now_ts

# --- 活動判定（ローカル）：240秒（4分）以上の放置は加算しない ---
if 0 < elapsed_t < 240:
    st.session_state.unsynced_seconds += int(elapsed_t)
    st.session_state.daily_seconds += int(elapsed_t)
    if "total_seconds" in st.session_state:
        st.session_state.total_seconds += int(elapsed_t)

# --- 同期頻度（通信）：未保存が900秒（15分）溜まったらスプレッドシートへ ---
if st.session_state.unsynced_seconds >= 900:
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
        c3.metric("🚩 開拓率", f"{db.get('overall_avg', 0.0)}%")

        # 総解答数（開拓済み問題の総和）
        total_ans = sum(
            [int(s["開拓状況"].split(" / ")[0]) for s in db.get("cat_stats", [])] or [0]
        )
        c4.metric("📝 解答数", f"{total_ans}問")

    # 📈 カテゴリ別進捗テーブル
    st.write("**📈 カテゴリ別進捗**")
    if db.get("cat_stats"):
        st.dataframe(
            db["cat_stats"],
            width="stretch",
            height=250,
            hide_index=True,
            column_config={
                "🚩 開拓率": st.column_config.NumberColumn(
                    "🚩 開拓率",
                    format="%.1f%%",
                )
            },
        )

    # 🛠️ 操作パネル
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

        # --- ホーム戻り・中断 ---
        nav1, nav2 = st.columns(2, gap="small")
        if st.session_state.get("print_data"):
            if nav1.button("⬅️ ホーム", width="stretch"):
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

    # 🚨 不備報告・削除フォーム
    if st.session_state.get("show_rpt_expander", False) and is_active:
        cur_idx = st.session_state.index
        if cur_idx < len(st.session_state.questions):
            q_now = st.session_state.questions[cur_idx]
            with st.container(border=True):
                st.markdown("**🚨 問題の不備を報告・削除**")
                rpt_msg = st.text_input("誤植・内容の不備など", key=f"rpt_in_{cur_idx}")

                # ボタンを横並びに配置
                c_rpt_send, c_rpt_del = st.columns(2, gap="small")

                if c_rpt_send.button("送信する", type="primary", width="stretch"):
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

                # 🗑️ 削除ボタン（archive_and_delete_questionを呼び出し）
                if c_rpt_del.button(
                    "🗑️ 削除",
                    type="secondary",
                    width="stretch",
                    help="問題をアーカイブへ移動して完全に削除します",
                ):
                    archive_and_delete_question(q_now)

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
# 11. メイン画面：ホーム（未攻略優先生成・履歴管理機能・メモ復元）
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

        with st.expander("🛠️ データベース保守 ＆ 監査ツール", expanded=False):
            # --- 1. ID一括管理セクション (新設) ---
            st.markdown("#### 🆔 問題ID一括管理")
            st.caption(
                "スプレッドシートのO列(15列目)をスキャンし、IDがない問題にのみUUIDを付与します。"
            )
            if st.button(
                "🆔 未採番行にIDを一括付与", type="primary", use_container_width=True
            ):
                assign_missing_ids()  # API制限回避版の一括書き込み関数
            st.divider()

            # --- 2. 同期・監査セクション ---
            col_ad1, col_ad2 = st.columns(2)

            with col_ad1:
                st.markdown("**🔄 Mastery全同期（最終診断版）**")
                if st.button(
                    "全同期を実行する", use_container_width=True, type="primary"
                ):
                    try:
                        gc_ad = gspread.authorize(get_creds())
                        sh_m_ad = gc_ad.open("study_stats_db").worksheet("mastery")
                        m_all = sh_m_ad.get_all_values()
                        m_headers = m_all[0]
                        m_q_idx = m_headers.index("q")

                        mastery_map = {
                            r[m_q_idx].strip(): r for r in m_all[1:] if len(r) > m_q_idx
                        }

                        sh_q_current = gc_ad.open("study_stats_db").worksheet(
                            "questions"
                        )
                        q_all = sh_q_current.get_all_values()
                        q_headers = q_all[0]

                        q_q_idx = q_headers.index("q") if "q" in q_headers else 1
                        q_a_idx = q_headers.index("a") if "a" in q_headers else 5
                        q_cat_idx, q_unit_idx = 0, 1

                        new_mastery_list = []
                        for q_row in q_all[1:]:
                            if len(q_row) <= max(q_q_idx, q_a_idx):
                                continue
                            q_text, q_ans, q_cat = (
                                q_row[q_q_idx].strip(),
                                q_row[q_a_idx].strip(),
                                q_row[q_cat_idx].strip(),
                            )
                            q_unit = (
                                q_row[q_unit_idx].strip()
                                if len(q_row) > q_unit_idx
                                else ""
                            )

                            if q_text in mastery_map:
                                row = mastery_map[q_text]
                                while len(row) < 7:
                                    row.append("")
                                row[5], row[6], row[0] = q_ans, q_unit, q_cat
                                new_mastery_list.append(row)
                            else:
                                new_mastery_list.append(
                                    [q_cat, q_text, "0", "0", "", q_ans, q_unit]
                                )

                        if new_mastery_list:
                            last_row_old = len(m_all) + 100
                            sh_m_ad.batch_clear([f"A2:G{last_row_old}"])
                            sh_m_ad.update(
                                range_name=f"A2:G{len(new_mastery_list) + 1}",
                                values=new_mastery_list,
                            )

                        st.success(
                            f"✨ 同期成功！ {len(new_mastery_list)} 件を更新しました。"
                        )
                    except Exception as e:
                        st.error(f"同期エラー: {e}")

            with col_ad2:
                st.markdown("**🔎 データ監査（英語・超精密）**")
                if st.button(
                    "超精密・整合性監査を実行", use_container_width=True, type="primary"
                ):
                    error_details = []
                    for cat_name, q_list in all_q.items():
                        if "英語" in cat_name:
                            for q_ad in q_list:
                                q_txt, ans_txt = (
                                    str(q_ad.get("q", "")),
                                    str(q_ad.get("a", "")),
                                )
                                m = re.search(r"[\(（](.*?)[\)）]", q_txt)
                                if m and re.search(r"[/／]", m.group(1)):
                                    opts = [
                                        w.strip().lower().rstrip("?!.,")
                                        for w in re.split(r"[/／]", m.group(1))
                                        if w.strip()
                                    ]
                                    temp_ans, ans_words_found = (
                                        ans_txt.lower().rstrip("?!.,"),
                                        [],
                                    )
                                    sorted_opts = sorted(opts, key=len, reverse=True)
                                    test_ans = temp_ans
                                    for opt in sorted_opts:
                                        if opt in test_ans:
                                            ans_words_found.append(opt)
                                            test_ans = test_ans.replace(opt, "", 1)
                                    missing = [
                                        o
                                        for o in opts
                                        if opts.count(o) > ans_words_found.count(o)
                                    ]
                                    if missing:
                                        error_details.append(
                                            f"❌ {q_txt[:25]}... \n ➡ 【不足: {set(missing)}】"
                                        )

                    if error_details:
                        st.error(f"{len(error_details)}件の不備を発見しました。")
                        for i, err in enumerate(error_details):
                            st.code(f"No.{i} | {err}")
                    else:
                        st.success("すべての整合性が確認されました！")

            st.divider()

            # --- 3. バックアップ ＆ 削除データ抽出 ---
            st.markdown("#### 💾 データエクスポート")
            c_ex1, c_ex2 = st.columns(2)

            with c_ex1:
                st.markdown("**📝 学習履歴**")
                df_hist = pd.DataFrame(db.get("history", []))
                if not df_hist.empty:
                    csv_hist = df_hist.to_csv(index=False).encode("utf-8-sig")
                    st.download_button(
                        "📥 History (履歴) CSV",
                        data=csv_hist,
                        file_name=f"history_backup_{datetime.now(JST).strftime('%Y%m%d')}.csv",
                        mime="text/csv",
                        use_container_width=True,
                    )
                else:
                    st.button("履歴データなし", disabled=True, use_container_width=True)

            with c_ex2:
                st.markdown("**📊 削除ログ**")
                if st.button("📈 削除データのExcel準備", use_container_width=True):
                    try:
                        gc = gspread.authorize(get_creds())
                        try:
                            del_sh = gc.open("study_stats_db").worksheet(
                                "deleted_questions"
                            )
                            del_df = pd.DataFrame(del_sh.get_all_records())
                        except Exception:
                            del_df = pd.DataFrame()

                        if not del_df.empty:
                            import io

                            output = io.BytesIO()
                            with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
                                del_df.to_excel(
                                    writer, index=False, sheet_name="Deleted_Log"
                                )
                            st.download_button(
                                "📥 Excelをダウンロード",
                                data=output.getvalue(),
                                file_name=f"deleted_log_{datetime.now(JST).strftime('%Y%m%d')}.xlsx",
                                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                use_container_width=True,
                            )
                        else:
                            st.warning("アーカイブされた削除データはありません。")
                    except Exception as e:
                        st.error(f"抽出失敗: {e}")

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

            if st.button(
                "ミッションを起動する", use_container_width=True, type="primary"
            ):
                st.session_state.show_help_persistence = False  # 💡 追加

                # --- 1. 除外リストの作成 (卒業済み & 直近3回) ---
                graduated = set()
                recent_q_texts = set()

                try:
                    # ① 卒業名簿（スコア5以上）の取得
                    gc_tmp = gspread.authorize(get_creds())
                    m_recs = (
                        gc_tmp.open("study_stats_db")
                        .worksheet("mastery")
                        .get_all_records()
                    )
                    graduated = {
                        str(m.get("q")).strip()
                        for m in m_recs
                        if int(m.get("score", 0)) >= 5
                    }

                    # ② 直近3回分の履歴（クールダウン）の取得
                    # セクション7で定義した関数をここで呼び出します
                    recent_q_texts = get_cooldown_questions(
                        db.get("history", []), cooldown=3
                    )
                except Exception as e:
                    st.warning(
                        f"除外リストの作成中にエラーが発生しました（続行します）: {e}"
                    )

                # --- 2. 抽選プールの準備 ---
                pool_A, pool_B, pool_C = [], [], []
                prefix = "_" if "漢字" in subj else ""

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
                        q_text = str(q_item.get("q", "")).strip()
                        q_rank = str(q_item.get("rank", "B")).upper()

                        # 難易度フィルタ
                        if diff != "🌟 総合" and q_rank not in diff:
                            continue

                        # 形式フィルタ（並べ替え特化）
                        if fmt == "🧩 並べ替え特化":
                            # 括弧内にスラッシュが含まれるものを並べ替え問題と判定
                            if not re.search(r"[\(（].*?[/／].*?[\)）]", q_text):
                                continue

                        # 🚩 【重要】卒業済みの除外
                        if q_text in graduated:
                            continue

                        # 🚩 【重要】直近3回に出た問題の除外（クールダウン）
                        if q_text in recent_q_texts:
                            continue

                        # ランク別にプールへ振り分け
                        if q_rank == "A":
                            pool_A.append(q_item)
                        elif q_rank == "C":
                            pool_C.append(q_item)
                        else:
                            pool_B.append(q_item)

                # --- 3. 黄金比率による抽出 (A:15, B:12, C:3) ---
                target_A, target_B, target_C = 15, 12, 3

                # 安全にサンプリング（足りない場合はあるだけ取る）
                sel_A = random.sample(pool_A, min(len(pool_A), target_A))
                sel_B = random.sample(pool_B, min(len(pool_B), target_B))
                sel_C = random.sample(pool_C, min(len(pool_C), target_C))

                selection = sel_A + sel_B + sel_C
                random.shuffle(selection)  # 出題順をバラバラにする

                # --- 4. ミッション起動と保存 ---
                if selection:
                    # 保存用のモード名を決定
                    if year == "総合":
                        mode_name = subj if subj != "すべて" else "総合ミックス"
                    else:
                        mode_name = (
                            f"{prefix}{year}{subj}"
                            if subj != "すべて"
                            else f"{year}全教科"
                        )

                    # DBへ保存して画面をリロード（ミッション開始！）
                    batch_save_to_db(custom_mode=mode_name, custom_qs=selection)
                    st.rerun()
                else:
                    st.error(
                        "条件に合う問題（未習得かつ最近出ていない問題）が見つかりませんでした。範囲を広げるか、クールダウン期間が終わるのを待ってください。"
                    )

    with col_gen2:
        with st.expander("🔥 弱点克服・特訓"):
            st.markdown("未習得の問題から優先的に出題します。")
            w_subj = st.selectbox(
                "特訓教科", ["すべて"] + available_cats, key="w_subj_sel"
            )
            if st.button("特訓を開始！", width="stretch"):
                st.session_state.show_help_persistence = False  # 💡 追加
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

    # --- 🔍 自由検索・カスタムミッション（AND/ORハイブリッド版） ---
    with st.expander("🔍 検索カスタム抽出ミッション", expanded=False):
        # 1. 入力を受け取る（全角・半角スペース、カンマに対応）
        search_raw = st.text_input(
            "キーワード検索",
            placeholder="例: 「2年 プレスタ 古文」(AND) / 「漢字, 語句」(OR)",
            key="custom_search_input",
        )

        if search_raw:
            # 1. カンマでOR分割、さらにスペースでAND分割
            or_groups = [g.strip() for g in re.split(r"[,、]", search_raw) if g.strip()]

            found_pool = []
            for cat_name, q_list in all_q.items():
                for q_item in q_list:
                    # 🌟 A列(大) + B列(問) + G列(中) をすべて結合（小文字化して検索しやすく）
                    q_val = str(q_item.get("q", ""))
                    u_val = str(q_item.get("unit", ""))
                    target_text = (cat_name + q_val + u_val).lower()

                    match_found = False
                    for group in or_groups:
                        # 2. 【強化ポイント】全角・半角スペースをすべて分割し、空文字を除去
                        and_keywords = [
                            k.strip().lower()
                            for k in re.split(r"[\s　]+", group)
                            if k.strip()
                        ]

                        # 3. あいまいAND判定：すべてのキーワードが「どこか」に含まれているか
                        if and_keywords and all(
                            kw in target_text for kw in and_keywords
                        ):
                            match_found = True
                            break

                    if match_found:
                        found_pool.append(q_item)

            # --- 結果の表示と起動 ---
            hit_count = len(found_pool)
            if hit_count > 0:
                # ⭐ 30問を上限にするセーフティ
                max_display = 30
                num_to_draw = min(hit_count, max_display)

                st.metric("ヒット件数", f"{hit_count} 件")
                st.info(
                    f"💡 {hit_count}件の中から、ランダムに **{num_to_draw}問** を選んで出題します。"
                )

                if st.button(
                    f"{num_to_draw}問でミッションを開始！",
                    type="primary",
                    width="stretch",
                    key="start_and_or_mission",
                ):
                    st.session_state.show_help_persistence = False  # 💡 追加
                    # ランダム抽出
                    selection = random.sample(found_pool, num_to_draw)

                    # モード名に検索ワードを反映
                    mode_label = f"検索:{search_raw[:10]}"
                    batch_save_to_db(custom_mode=mode_label, custom_qs=selection)
                    st.rerun()
            else:
                st.warning(
                    "一致する問題がありません。キーワードを減らすか、別の言葉を試してください。"
                )
        else:
            st.write("キーワードを入れてください（スペースで絞り込み、カンマで追加）")

    # =============================================================================
    # 11. メイン画面：MISSION LOG（一括非表示 ＆ 2026年最新UI仕様）
    # =============================================================================
    st.subheader("📅 MISSION LOG")

    # 🌟 1. 選択中がある時だけ出現する「一括非表示バー」
    if st.session_state.get("delete_list"):
        with st.container(border=True):
            c_msg, c_btn = st.columns([3, 1])
            c_msg.info(
                f"ℹ️ {len(st.session_state.delete_list)}件を選択中。画面から非表示にします（記憶は保持されます）。"
            )

            if c_btn.button("🙈 選択中を一括非表示", type="primary", width="stretch"):
                try:
                    gc = gspread.authorize(get_creds())
                    sh_h = gc.open("study_stats_db").worksheet("history")
                    all_ids = [
                        str(val).strip() for val in sh_h.col_values(7)
                    ]  # G列(ID)

                    rows_to_hide = [
                        all_ids.index(str(tid).strip()) + 1
                        for tid in st.session_state.delete_list
                        if str(tid).strip() in all_ids
                    ]

                    if rows_to_hide:
                        for r_idx in rows_to_hide:
                            sh_h.update_cell(r_idx, 10, "1")  # J列(削除フラグ)を1に

                        st.session_state.delete_list = []
                        st.cache_data.clear()
                        st.toast("画面から除外しました", icon="🧹")
                        st.rerun()
                except Exception as e:
                    st.error(f"非表示エラー: {e}")

    # 🌟 2. 履歴データの読み込みとグループ分け
    h_list = db.get("history", [])
    if h_list:
        now_d = datetime.now(JST).date()
        start_w = now_d - timedelta(days=now_d.weekday())
        gps = {"📌 今週": [], "📌 先週": [], "📌 アーカイブ": []}

        for h in h_list[::-1]:
            # 🚩 削除フラグ(J列/10番目)が "1" なら表示対象から外す
            if str(h.get("削除フラグ", "")) == "1":
                continue

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

        # 🌟 3. 各カテゴリの展開表示
        for lbl, items in gps.items():
            if not items:
                continue

            with st.expander(f"{lbl} ({len(items)}件)", expanded=(lbl == "📌 今週")):
                for h in items:
                    tid = h.get("ID")

                    # --- 🎨 得点に基づいたカラー・メッセージ判定 ---
                    score_raw = h.get("得点")
                    # 🌟 判定：得点がない、空、または「未実施」という文字が含まれる場合
                    is_new = (
                        score_raw is None
                        or score_raw == ""
                        or "未実施" in str(score_raw)
                    )

                    try:
                        if not is_new:
                            # 数値だけを取り出す（例: "85.2点" -> 85.2）
                            score_num = float(str(score_raw).split("点")[0])
                        else:
                            score_num = 0
                    except Exception:
                        score_num = 0

                    # 🌟 崩れない標準の枠（コンテナ）
                    with st.container(border=True):
                        # 👑 状態に応じて「一番上の帯」の色とアイコンを完全分離
                        if is_new:
                            # ✨ 作った直後：青色（Info）で「未実施」を表現
                            st.info(
                                "🆕 NEW MISSION：未実施の新しい課題です。挑戦しよう！"
                            )

                        elif score_num == 100:
                            # 🥇 1位：濃い緑
                            st.success(f"🥇【極】 完璧な満点！ 1位合格 🎖️ ({score_raw})")

                        elif score_num >= 90:
                            # 🥈 2位：薄い緑
                            st.success(
                                f"🥈【秀】 素晴らしい！ あと一歩で満点 🏆 ({score_raw})"
                            )

                        elif score_num >= 80:
                            # 🥉 3位：黄色（合格）
                            st.warning(
                                f"🥉【優】 合格！ 記述テストの資格あり 🎉 ({score_raw})"
                            )

                        else:
                            # 80点未満：灰色
                            st.write(f"📝 実施済み ({score_raw})")

                        # --- 上段：情報とメインボタン（ここから中身） ---
                        # 🛠️ 6列設定 [チェック, 情報, 特訓, 余白, 題, 答]
                        c_sel, c_info, c_go, c_sp, c_pq, c_pa = st.columns(
                            [0.4, 3.1, 1.2, 0.1, 0.8, 0.8]
                        )

                        # 1. チェックボックス
                        is_checked = c_sel.checkbox(
                            "選択", key=f"sel_{tid}", label_visibility="collapsed"
                        )
                        if is_checked and tid not in st.session_state.delete_list:
                            st.session_state.delete_list.append(tid)
                            st.rerun()
                        elif not is_checked and tid in st.session_state.delete_list:
                            st.session_state.delete_list.remove(tid)
                            st.rerun()

                        # 2. 情報表示
                        # 🌟 重複した単語を確実に1つにまとめる処理
                        raw_subject = str(h.get("教科", ""))

                        # ① まず不要な「検索」や「：」「:」を消去
                        # ※ ここで「temp_text」という名前で定義します
                        temp_text = (
                            raw_subject.replace("検索：", "")
                            .replace("検索:", "")
                            .replace("検索", "")
                            .replace("：", "")
                            .replace(":", "")
                        )

                        # ② 全角スペースを半角に統一して分割
                        words = temp_text.replace("　", " ").split()

                        # ③ 重複を除去（順番を維持したまま1つにする）
                        unique_words = []
                        for w in words:
                            if w not in unique_words:
                                unique_words.append(w)

                        clean_subject = " ".join(unique_words).strip()

                        c_info.markdown(
                            f"<small style='color:#888;'>{h.get('日付')} | `{tid}`</small><br>"
                            f"<strong style='font-size:18px;'>{clean_subject}</strong>",
                            unsafe_allow_html=True,
                        )

                        # 3. 🔄 特訓ボタン（全ロジック保持）
                        if c_go.button(
                            "🔄 特訓",
                            key=f"go_{tid}",
                            type="primary",
                            use_container_width=True,
                        ):
                            st.session_state.show_help_persistence = False
                            keys_to_reset = [
                                "questions",
                                "index",
                                "correct_count",
                                "show_result",
                                "kj_scores",
                                "user_answers",
                                "session_results",
                                "correct_cache",
                                "show_options",
                            ]
                            for k in keys_to_reset:
                                if k in st.session_state:
                                    del st.session_state[k]
                            st.session_state.show_options = False
                            st.session_state.correct_cache = []
                            st.session_state.index = 0
                            st.session_state.correct_count = 0
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

                        # 4. 📄 題 / 🔑 答
                        with c_sp:
                            st.write("")  # スペース用

                        if c_pq.button(
                            "📄 題", key=f"pq_{tid}", use_container_width=True
                        ):
                            q_json = json.loads(h.get("問題リスト(JSON)", "[]"))
                            target_qs = [
                                next((q for q in flat_pool if q["q"] == t), None)
                                for t in q_json
                            ]
                            st.session_state.print_type = "q"
                            st.session_state.print_data = {
                                "mode": h.get("教科"),
                                "id": tid,
                                "qs": [q for q in target_qs if q],
                            }
                            st.rerun()

                        if c_pa.button(
                            "🔑 答", key=f"pa_{tid}", use_container_width=True
                        ):
                            if st.session_state.get("parent_unlock_key") == "7777":
                                q_json = json.loads(h.get("問題リスト(JSON)", "[]"))
                                target_qs = [
                                    next((q for q in flat_pool if q["q"] == t), None)
                                    for t in q_json
                                ]
                                st.session_state.print_type = "a"
                                st.session_state.print_data = {
                                    "mode": h.get("教科"),
                                    "id": tid,
                                    "qs": [q for q in target_qs if q],
                                }
                                st.rerun()
                            else:
                                st.toast("キーが必要です", icon="🔒")

                        # --- 下段：メモ・除外・保存 ---
                        st.write("")
                        c_m1, c_m2, c_m3 = st.columns([3, 2, 1])
                        memo_val = c_m1.text_input(
                            "📝 メモ",
                            value=str(h.get("メモ", "")),
                            key=f"memo_{tid}",
                            label_visibility="collapsed",
                            placeholder="メモを入力...",
                        )
                        skip_val = c_m2.text_input(
                            "✂️ 除外",
                            value=str(h.get("除外", "")),
                            key=f"skip_{tid}",
                            label_visibility="collapsed",
                            placeholder="除外番号...",
                        )
                        if c_m3.button("💾", key=f"sv_{tid}", use_container_width=True):
                            try:
                                gc = gspread.authorize(get_creds())
                                sh_h = gc.open("study_stats_db").worksheet("history")
                                ids = sh_h.col_values(7)
                                if tid in ids:
                                    r_idx = ids.index(tid) + 1
                                    sh_h.update_cell(r_idx, 5, memo_val)
                                    sh_h.update_cell(r_idx, 8, skip_val)
                                    st.cache_data.clear()
                                    st.toast("更新しました", icon="✅")
                            except Exception:
                                st.error("保存失敗")

                    # 🌟 カード枠の終了（ここで div を閉じます）
                    st.markdown("</div>", unsafe_allow_html=True)

else:  # --- 特訓モード：1行集約・点滅ゼロ・デバッグ対応版 ---
    idx = st.session_state.index
    qs = st.session_state.questions

    if idx >= len(qs):
        # =========================================================
        # 🏁 MISSION COMPLETE 画面（変更なし）
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
            "💾 保存してホームへ戻る", type="primary", use_container_width=True
        ):
            batch_save_to_db()
            st.session_state.mode = None
            st.rerun()

    else:
        # =========================================================
        # 📖 問題実行中
        # =========================================================
        q = qs[idx]
        # 現在の問題データを削除等のためにセッションに保持
        st.session_state["current_question"] = q

        cat = q.get("orig_cat", "")
        ans_raw = str(q.get("a", "")).strip()
        is_kanji = "漢字" in cat

        # 並べ替え判定
        en_disp, jp_disp, choices_q = parse_order_question(q.get("q", ""), cat)
        is_order = (
            "英語" in cat and (len(choices_q) > 0 or "/" in ans_raw or " " in ans_raw)
        ) or ("/" in ans_raw)

        # 💡 判定ボタンを表示するか（4択モードのみTrue）
        show_judge_button = not (is_kanji or is_order)

        # 2. 問題メインコンテンツの表示
        if is_kanji:
            # キャンバスの枠線設定（230px / 補助線なし）
            st.markdown(
                r"""
                <style>
                canvas.stCanvas {
                    background-color: #ffffff !important;
                    border: 1px solid #ddd !important;
                    border-radius: 4px;
                    width: 230px !important;
                    height: 230px !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )

            # 漢字モードのタイトル表示
            display_title = str(q.get("q", "")).replace("検索", "").strip()
            st.markdown(
                f"<div style='text-align:center; font-size:20px; font-weight:bold; margin-bottom:10px;'>🛡️ 漢字特訓：{display_title}</div>",
                unsafe_allow_html=True,
            )

            chars = list(ans_raw)
            # セッション状態の初期化
            if "kj_scores" not in st.session_state or st.session_state.get(
                "kj_q_id"
            ) != q.get("q"):
                st.session_state.kj_scores = {i: 0 for i in range(len(chars))}
                st.session_state.kj_q_id = q.get("q")

            cols_kj = st.columns(len(chars))
            for i, char in enumerate(chars):
                with cols_kj[i]:
                    stroke_setting = q.get(f"strokes{i + 1}")
                    is_target_kanji = (
                        stroke_setting and str(stroke_setting).strip().isdigit()
                    )

                    if not is_target_kanji:
                        # 画数設定がない文字（ひらがな等）は自動合格
                        st.session_state.kj_scores[i] = 100
                        st.markdown(
                            f"<div style='text-align:center; font-weight:bold; color:#999;'>{char}</div>",
                            unsafe_allow_html=True,
                        )
                        st.markdown(
                            f"<div style='text-align:center; background:#f8f9fa; border:1px solid #eee; border-radius:10px; font-size:60px; font-family:serif; color:#ddd; height:230px; display:flex; align-items:center; justify-content:center;'>{char}</div>",
                            unsafe_allow_html=True,
                        )
                        continue

                    # 現在のスコアを取得
                    score_val = st.session_state.kj_scores[i]

                    opacity = 1.0
                    if score_val == 34:
                        opacity = 0.15
                    elif score_val == 66:
                        opacity = 0.0
                    elif score_val == 100:
                        opacity = 1.0

                    hint_key, lock_key = f"kj_hint_{idx}_{i}", f"kj_locked_{idx}_{i}"
                    if hint_key not in st.session_state:
                        st.session_state[hint_key] = (
                            "まずはお手本を見て、形を脳に写そう！"
                        )
                    if lock_key not in st.session_state:
                        st.session_state[lock_key] = False

                    st.markdown(
                        f"<div style='text-align:center; font-weight:bold; opacity: {opacity}; transition: opacity 0.5s;'>{char} ({min(100, score_val)}%)</div>",
                        unsafe_allow_html=True,
                    )

                    with st.container(border=True):
                        st.markdown(
                            f"<div style='text-align:center;'><div style='font-size:55px; font-family:serif; opacity: {opacity}; transition: opacity 0.5s;'>{char}</div><div style='font-size:10px;'>{stroke_setting}画</div></div>",
                            unsafe_allow_html=True,
                        )
                        st.progress(min(100, score_val) / 100)

                        if score_val < 100:
                            r_key = st.session_state.get(f"reset_{idx}_{i}", 0)
                            cv_res = st_canvas(
                                stroke_width=8,
                                stroke_color="#000000",
                                height=230,
                                width=230,
                                key=f"kj_cv_{idx}_{i}_{r_key}",
                                display_toolbar=False,
                                background_color="#ffffff",
                            )

                            b1, b2 = st.columns(2)
                            if b1.button(
                                "📮 判定",
                                key=f"score_{idx}_{i}",
                                use_container_width=True,
                            ):
                                if st.session_state[lock_key]:
                                    st.warning("一度『クリア』してから書き直してね！")
                                else:
                                    s_p, msg = get_kanji_score(
                                        cv_res, char, stroke_setting
                                    )
                                    st.session_state[lock_key] = True
                                    if s_p == 100:
                                        st.session_state.kj_scores[i] = 100
                                        st.session_state[hint_key] = "完璧！記憶完了💮"
                                        queue_sound("correct.mp3")
                                        st.rerun()
                                    elif s_p > 0:
                                        queue_sound("correct.mp3")
                                        if score_val == 0:
                                            st.session_state.kj_scores[i] = 34
                                        elif score_val == 34:
                                            st.session_state.kj_scores[i] = 66
                                        elif score_val == 66:
                                            st.session_state.kj_scores[i] = 100
                                        st.rerun()
                                    else:
                                        st.session_state[hint_key] = msg
                                        queue_sound("wrong.mp3")
                                        st.rerun()

                            if b2.button(
                                "🧽 クリア",
                                key=f"clr_{idx}_{i}",
                                use_container_width=True,
                            ):
                                st.session_state[f"reset_{idx}_{i}"] = r_key + 1
                                st.session_state[lock_key] = False
                                st.rerun()

                            st.markdown(
                                f"<div style='background-color: #f0f7ff; border-left: 5px solid #007bff; padding: 10px; margin-top: 10px; border-radius: 4px; font-size: 13px; color: #333; min-height: 55px;'><strong>💡 ヒント:</strong><br>{st.session_state[hint_key]}</div>",
                                unsafe_allow_html=True,
                            )
                        else:
                            st.success("OK! 記憶完了")

            # --- 修正版：ごみ箱の下にIDを「改行なし」で表示 ---
            st.write("")
            target_id = q.get("id", "N/A")

            # 全文字クリア判定
            all_clear = all(v == 100 for v in st.session_state.kj_scores.values())

            # カラム比率：[管理(0.8に拡大), 前へ, スキップ, 次へ]
            # 左端を0.8に広げることでIDの横幅を確保します
            nav_c0, nav_c1, nav_c2, nav_c3 = st.columns([0.8, 1, 1, 1.4])

            with nav_c0:
                # 1. 上段にごみ箱（幅を少し絞って小さく見せる）
                sub_del_c1, _ = st.columns([1, 0.5])
                with sub_del_c1:
                    if st.button(
                        "🗑️",
                        key=f"kj_del_{idx}",
                        use_container_width=True,
                        help="この問題を削除",
                    ):
                        st.session_state.confirm_delete = True
                        st.rerun()

                # 2. 下段にID表示（white-space: nowrap で絶対改行させない）
                st.markdown(
                    f"""
                    <div style='
                        color: gray; 
                        font-size: 0.75rem; 
                        margin-top: -5px; 
                        white-space: nowrap; 
                        overflow: visible;
                        text-align: left;
                    '>🆔{target_id}</div>
                    """,
                    unsafe_allow_html=True,
                )

            with nav_c1:
                if st.button("⬅️ 前へ", key=f"kj_prev_{idx}", use_container_width=True):
                    if st.session_state.index > 0:
                        st.session_state.index -= 1
                        st.rerun()

            with nav_c2:
                if st.button(
                    "⏩ スキップ", key=f"kj_skip_{idx}", use_container_width=True
                ):
                    if st.session_state.index + 1 < len(qs):
                        st.session_state.index += 1
                        st.rerun()

            with nav_c3:
                # 100%達成時のみ有効
                if st.button(
                    "✅ 完了！次へ",
                    key=f"kj_next_{idx}",
                    use_container_width=True,
                    type="primary" if all_clear else "secondary",
                    disabled=not all_clear,
                    help="全文字100%になると進めます",
                ):
                    if st.session_state.index + 1 < len(qs):
                        st.session_state.index += 1
                        st.rerun()
                    else:
                        st.success("全問達成です！")

            # --- 削除確認 ---
            if st.session_state.get("confirm_delete", False):
                st.error(f"🆔 {target_id} を削除しますか？")
                d_c1, d_c2 = st.columns(2)
                with d_c1:
                    if st.button(
                        "はい、削除",
                        key=f"kj_del_y_{idx}",
                        type="primary",
                        use_container_width=True,
                    ):
                        if delete_question_by_id(target_id):
                            st.session_state.questions.pop(st.session_state.index)
                            st.cache_data.clear()
                            st.session_state.confirm_delete = False
                            st.rerun()
                with d_c2:
                    if st.button(
                        "いいえ", key=f"kj_del_n_{idx}", use_container_width=True
                    ):
                        st.session_state.confirm_delete = False
                        st.rerun()

        else:
            # --- 🍎 英語・全カテゴリ共通：自動判別＆分割エンジン ---
            import re
            import random

            # 🌟 必要な変数の定義（NameErrorを防ぐためのセット）
            ans_raw_str = str(ans_raw)
            q_text = str(q.get("q", ""))
            cat_name = str(cat)
            help_text = q.get("h", "")

            # クイズ判定用のフラグ（NameError対策）
            is_english = "英語" in cat_name
            is_order = q.get("order", False)  # 並び替え問題かどうかの判定

            # 手書き範囲の動的設定（数学・理科は広く）
            is_math_style = (
                any(kw in cat_name for kw in ["数学", "理科", "計算"]) or "$" in q_text
            )
            c_height = 450 if is_math_style else 250

            # 🌟 0. デザイン設定
            st.markdown(
                """
                <style>
                @import url('https://fonts.googleapis.com/css2?family=Noto+Serif+JP:wght@700&display=swap');
                
                .block-container { 
                    padding-top: 3.5rem !important; 
                    max-width: 1350px !important; 
                    width: 98% !important;  /* 左右にわずかな隙間を確保 */
                    margin: 0 auto !important; 
                    font-family: "Noto Serif JP", "Yu Mincho", "serif" !important; 
                }
                
                /* 枠線が外にはみ出さないように設定 */
                canvas.stCanvas { 
                    box-sizing: border-box !important; /* 枠線を内側に含める */
                    border: 2px solid #ddd !important; /* 1pxから2pxにすると見やすくなります */
                    border-radius: 4px !important; 
                    display: block !important; 
                    margin: 0 auto !important;
                    max-width: 100% !important; /* 親要素（98%）に合わせる */
                    width: 100% !important; 
                    height: auto !important;
                }
                </style>
                """,
                unsafe_allow_html=True,
            )

            # 📊 1. ステータス・トグル・ヒント内容（2列構造で密着）
            st_col_left, st_col_main = st.columns([2.5, 7.5])

            with st_col_left:
                status_line = f"Mission {st.session_state.index + 1}/{len(st.session_state.questions)} | ⭕️ {st.session_state.correct_count} | 🏷️ {cat}"
                st.markdown(
                    f"<div style='padding-top: 12px; font-weight: bold; color: #4b5563; font-size: 15px; white-space: nowrap;'>{status_line}</div>",
                    unsafe_allow_html=True,
                )

            with st_col_main:
                # 🌟 内部カラムでスイッチとヒントを並べる（元の調整済みレイアウト）
                inner1, inner2 = st.columns([0.6, 9.0], gap="small")

                with inner1:
                    is_help_on = st.toggle(
                        "",
                        value=st.session_state.get("show_help_persistence", False),
                        key=f"help_tg_{idx}",
                        label_visibility="collapsed",
                    )
                    st.session_state.show_help_persistence = is_help_on

                with inner2:
                    hint_label = "💡ヒント"

                    # 【ここを書き換え】to_pretty_display を適用して LaTeX 記号を掃除します
                    clean_help = (
                        to_pretty_display(str(help_text).strip()) if help_text else ""
                    )

                    display_content = (
                        f"{hint_label} ➡ {clean_help}"
                        if (is_help_on and clean_help)
                        else hint_label
                    )

                    st.markdown(
                        f"""
                        <span style='display: inline-block; vertical-align: -9px; font-weight: bold; color: #1f2937; font-size: 18px; white-space: nowrap;'>
                            {display_content}
                        </span>
                        """,
                        unsafe_allow_html=True,
                    )

                    # 下記の style ブロック内の 14px も 18px に合わせて変更します
                    st.markdown(
                        "<style>[data-testid='column']:nth-of-type(2) [data-testid='column']:nth-of-type(2) p { margin-top: 8px !important; font-weight: bold !important; color: #1f2937 !important; font-size: 18px !important; white-space: nowrap !important; }</style>",
                        unsafe_allow_html=True,
                    )

            # --- 📖 2. 問題表示 ---
            en_disp, jp_disp, _ = parse_order_question(q_text, cat_name)
            st.markdown(f"### {en_disp}")
            if jp_disp:
                st.markdown(f"**{jp_disp}**")

            # --- 3. キャンバス ---
            curr_w = st.session_state.get("stroke_width", 3)
            curr_c = st.session_state.get("stroke_color", "#000000")
            canvas_result = st_canvas(
                fill_color="rgba(255, 165, 0, 0.3)",
                stroke_width=curr_w,
                stroke_color=curr_c,
                height=c_height,
                width=1050,
                drawing_mode="freedraw",
                key=f"dyn_math_cv_{idx}",
                display_toolbar=True,
                update_streamlit=True,
            )

            # --- 🎨 4. ツール切り替え（ワープCSS） ---
            st.markdown(
                "<style>div[data-testid='stRadio'] { transform: translateY(-68px) !important; margin-left: 140px !important; z-index: 1000 !important; } div[data-testid='stHorizontalBlock']:has(div[data-testid='stRadio']) { height: 0px !important; min-height: 0px !important; margin-bottom: -100px !important; } div[data-testid='stRadio'] > label { display: none !important; } div[data-testid='stRadio'] div[role='radiogroup'] { flex-direction: row !important; gap: 15px !important; }</style>",
                unsafe_allow_html=True,
            )
            r_col, _ = st.columns([0.5, 0.5])
            with r_col:
                mode = st.radio(
                    "Tool",
                    options=["✏️ ペン", "🧽 消ゴム"],
                    index=0
                    if st.session_state.get("stroke_color", "#000000") == "#000000"
                    else 1,
                    horizontal=True,
                    key=f"mode_sel_{idx}",
                )
                new_color = "#000000" if "ペン" in mode else "#ffffff"
                new_width = 3 if "ペン" in mode else 30
                if st.session_state.get("stroke_color") != new_color:
                    st.session_state.stroke_color = new_color
                    st.session_state.stroke_width = new_width
                    st.rerun()

            # --- 📝 5. クイズ表示 & 🚩 6. 操作ボタン (一体型レイアウト) ---

            # 🎨 隙間を極限まで削るCSS
            st.markdown(
                """
                <style>
                /* コンテナ間の上下余白をゼロにする */
                [data-testid="stVerticalBlock"] > div {
                    gap: 0rem !important;
                    padding-bottom: 0rem !important;
                }
                /* 成功・失敗メッセージの余白 */
                div[data-testid="stNotification"] {
                    margin-top: 5px !important;
                    margin-bottom: 5px !important;
                }
                /* ボタンの上下の隙間 */
                .stButton button {
                    margin-top: 2px !important;
                    margin-bottom: 2px !important;
                }
                /* 区切り線の余白 */
                hr {
                    margin-top: 10px !important;
                    margin-bottom: 10px !important;
                }
                /* 単語チップの最小幅 */
                div[data-testid="stHorizontalBlock"] button[data-testid="baseButton-secondary"] {
                    min-width: 90px !important;
                    padding: 0px 5px !important;
                }
                </style>
            """,
                unsafe_allow_html=True,
            )

            # --- 📝 5. クイズ表示 & 🚩 6. 操作ボタン ---
            if st.session_state.get("show_result"):
                # 1. まず to_pretty_display を通して LaTeX を綺麗にする
                # 2. その後で既存の整形（replace）を行う
                display_ans = (
                    to_pretty_display(ans_raw_str)
                    .replace("/", " ")
                    .replace(" ,", ",")
                    .strip()
                )

                if st.session_state.last_is_correct:
                    st.markdown(
                        f"""<div style="background-color: #d4edda; color: #155724; padding: 10px 15px; border-radius: 8px; border-left: 6px solid #28a745; display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 10px;">
                            <span style='font-size: 1.2rem; font-weight: bold; white-space: nowrap;'>⭕️ 正解！</span>
                            <span style='font-size: 1.8rem; font-weight: 800; line-height: 1.1;'>{display_ans}</span>
                        </div>""",
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        f"""<div style="background-color: #f8d7da; color: #721c24; padding: 10px 15px; border-radius: 8px; border-left: 6px solid #dc3545; display: flex; align-items: center; flex-wrap: wrap; gap: 10px; margin-bottom: 10px;">
                            <span style='font-size: 1.2rem; font-weight: bold; white-space: nowrap;'>❌ 残念！正解は：</span>
                            <span style='font-size: 1.8rem; font-weight: 800; line-height: 1.1;'>{display_ans}</span>
                        </div>""",
                        unsafe_allow_html=True,
                    )

            else:
                # クイズ形式の自動判別
                m_inner = re.search(r"[\(（](.*?)[\)）]", q_text)
                raw_inner = m_inner.group(1) if m_inner else ""
                clean_options = [
                    opt.strip() for opt in re.split(r"[/／]", raw_inner) if opt.strip()
                ]
                option_count = len(clean_options)
                empty_parens = re.findall(r"[\(（]\s*[\)）]", q_text)

                is_scramble_render = is_english and (
                    len(empty_parens) >= 2 or option_count >= 3
                )
                is_two_choice = not is_scramble_render and option_count == 2

                # --- クイズ表示ロジック ---
                if is_scramble_render:
                    # --- [A] 並び替えクイズ (1行8つ対応) ---
                    if "user_ans_order" not in st.session_state:
                        st.session_state["user_ans_order"] = []
                    user_ans = st.session_state["user_ans_order"]
                    st.info(f"解答: {' '.join(user_ans) if user_ans else '...'}")

                    parts = (
                        clean_options
                        if option_count >= 3
                        else [
                            w.strip()
                            for w in re.split(r"[/／\s]+", ans_raw_str)
                            if w.strip()
                        ]
                    )
                    if not st.session_state.get("current_opts") or set(
                        st.session_state.get("current_opts", [])
                    ) != set(parts):
                        display_opts = list(parts)
                        random.shuffle(display_opts)
                        st.session_state.current_opts = display_opts

                    opts = st.session_state.current_opts
                    for i in range(0, len(opts), 8):
                        cols = st.columns(8)  # ★ 1行に8つ並べる
                        # --- 修正箇所（1056行目付近） ---
                        for j, word in enumerate(opts[i : i + 8]):
                            if user_ans.count(word) < opts.count(word):
                                # 表示する時だけ $ を消す（replaceを追加）
                                if cols[j].button(
                                    to_pretty_display(
                                        word
                                    ),  # ← ここを replace ではなく関数名にする！
                                    key="...",
                                    use_container_width=True,
                                ):
                                    st.session_state["user_ans_order"].append(word)
                                    st.rerun()

                elif is_two_choice:
                    # --- [B] 2択クイズ ---
                    st.write("▼ 正解を選択")
                    cols = st.columns(8)  # レイアウト維持のため8列枠を使用
                    for j, word in enumerate(clean_options):
                        if cols[j].button(
                            to_pretty_display(word),
                            key=f"ch_{idx}_{i + j}",
                            use_container_width=True,
                        ):
                            correct_val = re.split(r"[/／]", ans_raw_str)[0].strip()
                            ok = word.lower().strip() == correct_val.lower().strip()
                            st.session_state.last_is_correct = ok
                            st.session_state.show_result = True
                            if ok:
                                st.session_state.correct_count += 1
                            st.session_state.session_results.append(
                                {"q": q["q"], "cat": cat, "correct": ok}
                            )
                            queue_sound("correct.mp3" if ok else "wrong.mp3")
                            st.rerun()
                else:
                    # --- [C] 単語4択クイズ (マスク機能付き) ---
                    if not st.session_state.get("show_options"):
                        if st.button(
                            "答えを表示する",
                            key=f"sh_{idx}",
                            use_container_width=True,
                            type="secondary",
                        ):
                            st.session_state.show_options = True
                            st.rerun()
                    else:
                        correct_val = ans_raw_str.split("/")[0].strip()
                        dummy_raw = str(q.get("dummy", ""))
                        all_dummies = [
                            d.strip()
                            for d in re.split(r"[,、]", dummy_raw)
                            if d.strip() and d != correct_val
                        ]
                        selected_dummies = random.sample(
                            all_dummies, min(len(all_dummies), 3)
                        )
                        opts_list = [correct_val] + selected_dummies

                        if not st.session_state.get("current_opts") or set(
                            st.session_state.get("current_opts", [])
                        ) != set(opts_list):
                            display_opts = list(opts_list)
                            random.shuffle(display_opts)
                            st.session_state.current_opts = display_opts

                        cols = st.columns(8)  # ★ 1行8列の枠でボタンを配置
                        for j, word in enumerate(st.session_state.current_opts):
                            # 【ここを書き換え！】
                            # word.replace("$", "") ではなく、to_pretty_display(word) を使います
                            if cols[j].button(
                                to_pretty_display(word),
                                key=f"f4_{idx}_{j}",
                                use_container_width=True,
                            ):
                                # 判定ロジックは以前直した「$無視」のままでOKです
                                def clean_s(t):
                                    return re.sub(
                                        r"[\$ ,.\?!\(\)/]", "", str(t)
                                    ).lower()

                                ok = clean_s(word) == clean_s(correct_val)
                                st.session_state.last_is_correct = ok
                                st.session_state.show_result = True
                                if ok:
                                    st.session_state.correct_count += 1
                                st.session_state.session_results.append(
                                    {"q": q["q"], "cat": cat, "correct": ok}
                                )
                                queue_sound("correct.mp3" if ok else "wrong.mp3")
                                st.rerun()

            # --- 下部ナビゲーション ---
            target_id = q.get("id", "不明")
            st.markdown("---")
            n_col = st.columns([0.5, 1.0, 1.0, 0.2, 1.1, 1.1, 0.2, 2.0])

            # 状態をリセットして移動する関数
            def go_to_index(new_idx):
                st.session_state.index = new_idx
                st.session_state.show_result = False
                st.session_state.show_options = False  # 💡 ここでマスクをリセット
                st.session_state["user_ans_order"] = []
                st.session_state.current_opts = []
                st.rerun()

            with n_col[0]:
                if st.button("🗑️", key=f"nv_d_{idx}"):
                    st.session_state.confirm_delete = True
            with n_col[1]:
                if st.button("前へ", key=f"nv_p_{idx}", use_container_width=True):
                    if st.session_state.index > 0:
                        go_to_index(st.session_state.index - 1)
            with n_col[2]:
                if st.button("スキップ", key=f"nv_s_{idx}", use_container_width=True):
                    go_to_index(st.session_state.index + 1)

            with n_col[4]:
                if st.button(
                    "1つ消す",
                    key=f"nv_b_{idx}",
                    use_container_width=True,
                    disabled=st.session_state.get("show_result", False),
                ):
                    if st.session_state.get("user_ans_order"):
                        st.session_state["user_ans_order"].pop()
                        st.rerun()
            with n_col[5]:
                if st.button(
                    "全部消す",
                    key=f"nv_c_{idx}",
                    use_container_width=True,
                    disabled=st.session_state.get("show_result", False),
                ):
                    st.session_state["user_ans_order"] = []
                    st.rerun()

            with n_col[7]:
                if not st.session_state.get("show_result"):
                    if st.button(
                        "✅ 確定する",
                        type="primary",
                        key=f"nv_fix_{idx}",
                        use_container_width=True,
                    ):
                        # --- 修正箇所（1125行目付近） ---
                        def clean_final(v):
                            # 正規表現に \$ を追加してドル記号を無視
                            return re.sub(
                                r"[\$ ,.\?!\(\)/／]",
                                "",
                                "".join(v) if isinstance(v, list) else str(v),
                            ).lower()

                        u_ans = st.session_state.get("user_ans_order", [])
                        ok = clean_final(u_ans) == clean_final(ans_raw_str)
                        st.session_state.last_is_correct = ok
                        st.session_state.show_result = True
                        if ok:
                            st.session_state.correct_count += 1
                        st.session_state.session_results.append(
                            {"q": q["q"], "cat": cat, "correct": ok}
                        )
                        queue_sound("correct.mp3" if ok else "wrong.mp3")
                        st.rerun()
                else:
                    res_btn_col = st.columns(2)
                    with res_btn_col[0]:
                        if st.button(
                            "もう一度", key=f"nv_re_{idx}", use_container_width=True
                        ):
                            st.session_state.show_result = False
                            st.session_state.show_options = False  # リセット
                            st.session_state["user_ans_order"] = []
                            st.session_state.current_opts = []
                            st.rerun()
                    with res_btn_col[1]:
                        if st.button(
                            "次へ ➡️",
                            type="primary",
                            key=f"nv_next_{idx}",
                            use_container_width=True,
                        ):
                            go_to_index(st.session_state.index + 1)

            st.caption(f"ID: {target_id}")

            # --- ⚠️ 削除確認 ---
            if st.session_state.get("confirm_delete", False):
                st.warning("削除しますか？")
                c_y, c_n = st.columns(2)
                with c_y:
                    if st.button(
                        "はい、削除します", key=f"del_y_{idx}", use_container_width=True
                    ):
                        if delete_question_by_id(target_id):
                            st.session_state.questions.pop(idx)
                            st.cache_data.clear()
                            st.session_state.confirm_delete = False
                            st.rerun()
                with c_n:
                    if st.button(
                        "いいえ", key=f"del_n_{idx}", use_container_width=True
                    ):
                        st.session_state.confirm_delete = False
                        st.rerun()

            # --- 🏁 合格判定 ---
            if st.session_state.index + 1 == len(
                st.session_state.questions
            ) and st.session_state.get("show_result"):
                total_q = len(st.session_state.questions)
                correct_q = st.session_state.correct_count
                score_rate = (correct_q / total_q) * 100
                if score_rate >= 80:
                    st.balloons()
                    st.success(f"合格！ スコア：{score_rate:.1f}点")

# 🔊 音声再生
execute_queued_sound()
