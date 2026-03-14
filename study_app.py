import streamlit as st
from streamlit_drawable_canvas import st_canvas
import json, os, random, time, base64, re
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

# --- 1. 基本設定 ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
QUESTIONS_FILE = os.path.join(BASE_DIR, "questions.json")

def get_gsheet_client():
    if "gcp_service_account" in st.secrets:
        try:
            scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
            return gspread.authorize(creds)
        except: return None
    return None

@st.cache_data(ttl=300, show_spinner="読込中...")
def load_all_stats_cached():
    client = get_gsheet_client()
    if not client: return {"history": {}, "status": "no_client"}
    try:
        sh = client.open("study_stats_db").sheet1
        if not sh.cell(1, 1).value:
            sh.insert_row(["q", "correct", "wrong", "rank", "subject"], 1)
            return {"history": {}, "status": "ok"}
        data = sh.get_all_records()
        history = {str(row['q']): {"correct": int(row['correct'] or 0), "wrong": int(row['wrong'] or 0), 
                                  "rank": str(row.get('rank', 'A')), "subject": str(row.get('subject', '不明'))} for row in data if row.get('q')}
        return {"history": history, "status": "ok"}
    except Exception as e:
        return {"history": {}, "status": "error", "message": str(e)}

def save_stat(q_text, is_correct, rank, subject):
    client = get_gsheet_client()
    if client:
        try:
            sh = client.open("study_stats_db").sheet1
            cell = sh.find(q_text)
            if cell:
                row = cell.row
                c_val = int(sh.cell(row, 2).value or 0) + (1 if is_correct else 0)
                w_val = int(sh.cell(row, 3).value or 0) + (0 if is_correct else 1)
                sh.update_cell(row, 2, c_val)
                sh.update_cell(row, 3, w_val)
            else:
                sh.append_row([q_text, 1 if is_correct else 0, 0 if is_correct else 1, rank, subject])
        except: pass

def setup_audio_engine():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    c_p, w_p = os.path.join(base_dir, "correct.mp3"), os.path.join(base_dir, "wrong.mp3")
    c_b64 = base64.b64encode(open(c_p, "rb").read()).decode() if os.path.exists(c_p) else ""
    w_b64 = base64.b64encode(open(w_p, "rb").read()).decode() if os.path.exists(w_p) else ""
    st.components.v1.html(f"""<script>window.parent.playJudge = function(isCorrect) {{
        var s = isCorrect ? "data:audio/mp3;base64,{c_b64}" : "data:audio/mp3;base64,{w_b64}";
        new Audio(s).play().catch(e => console.log(e)); }};</script>""", height=0)

def parse_q_display(text):
    match = re.search(r'\s+([ぁ-んァ-ヶ一-龠].*)$', text)
    if match:
        main_p = text[:match.start()].strip(); hint_p = match.group(1).strip()
        if main_p: return main_p, hint_p
    return text, ""

# セッション初期化
if "questions_data" not in st.session_state and os.path.exists(QUESTIONS_FILE):
    with open(QUESTIONS_FILE, "r", encoding="utf-8") as f:
        st.session_state.questions_data = json.load(f)

for k, v in {"user_ans_list": [], "show_options": False, "show_result": False, 
             "last_is_correct": False, "score": 0, "index": 0, "session_streak": 0}.items():
    if k not in st.session_state: st.session_state[k] = v

st.set_page_config(page_title="70点マスター", layout="centered")
setup_audio_engine()

# --- 2. サイドバー：メンテナンス機能 ---
with st.sidebar:
    st.title("📊 学習管理")
    if st.button("🔄 データを更新", use_container_width=True):
        st.cache_data.clear(); st.rerun()

    res = load_all_stats_cached()
    hist = res.get("history", {})
    
    # 教科別表示
    if hist:
        subjects = sorted(list(set(v.get("subject", "不明") for v in hist.values())))
        for sub in subjects:
            sub_qs = [v for v in hist.values() if v.get("subject") == sub]
            total_t = sum(v["correct"] + v["wrong"] for v in sub_qs)
            total_c = sum(v["correct"] for v in sub_qs)
            rate = int(total_c / total_t * 100) if total_t > 0 else 0
            st.write(f"**{sub}**: {rate}点 ({total_t}回)")
            st.progress(rate / 100)
    
    st.divider()
    
    # ★ 苦手問題ダウンロード
    with st.expander("📥 苦手問題の出力"):
        weaks = [f"【{v['subject']}】{q} (ミス:{v['wrong']}回)" for q, v in hist.items() if v['wrong'] > 0]
        if weaks:
            weak_text = "\n".join(weaks)
            st.download_button("苦手リストをダウンロード(.txt)", weak_text, file_name="weak_list.txt")
        else:
            st.write("苦手な問題はまだありません。")

    # ★ 問題文の修正
    with st.expander("📝 問題文を修正する"):
        st.warning("修正後は下部のJSONをダウンロードしてGitHubへ反映させてください。")
        search_q = st.text_input("修正したい問題（キーワード）")
        if search_q and "questions_data" in st.session_state:
            found = False
            for cat, q_list in st.session_state.questions_data.items():
                for i, q_item in enumerate(q_list):
                    if search_q in q_item['q']:
                        new_q = st.text_input(f"問題文の変更 ({cat})", value=q_item['q'], key=f"edit_q_{i}")
                        new_a = st.text_input(f"答えの変更", value=q_item['a'], key=f"edit_a_{i}")
                        if st.button("この修正を保存", key=f"btn_{i}"):
                            st.session_state.questions_data[cat][i]['q'] = new_q
                            st.session_state.questions_data[cat][i]['a'] = new_a
                            st.success("修正しました！下のJSONを保存してください。")
                        found = True; break
            if not found: st.write("見つかりません。")

    # ★ 最新JSONの書き出し
    if "questions_data" in st.session_state:
        st.divider()
        json_str = json.dumps(st.session_state.questions_data, ensure_ascii=False, indent=2)
        st.download_button("✅ 修正済みのquestions.jsonを保存", json_str, file_name="questions.json", use_container_width=True)

# --- 3. メイン画面 ---
if 'mode' not in st.session_state:
    st.title("🛡️ yoshi式・70点奪取特訓")
    if "questions_data" in st.session_state:
        all_q = st.session_state.questions_data
        sub = st.selectbox("特訓セットを選択", list(all_q.keys()))
        diff = st.radio("モード選択", ["ミックス", "🧩 並べ替え特訓", "基礎(Rank A)", "標準(Rank B)", "難問(Rank C)", "🔥 苦手克服"], horizontal=True)
        if st.button("特訓開始！", use_container_width=True):
            st.session_state.mode = sub; st.session_state.diff_label = diff; data = all_q.get(sub, [])
            if "並べ替え" in diff: filtered = [q for q in data if "/" in q['q']]
            elif "Rank" in diff:
                t = diff.split("Rank ")[1][0]; filtered = [q for q in data if q.get("rank") == t]
            elif "苦手克服" in diff:
                wrong_list = [q_t for q_t, v in hist.items() if v.get("wrong", 0) > 0]
                filtered = [q for q in data if q['q'] in wrong_list]
                if not filtered: st.warning("まだ苦手な問題がありません。ミックスで開始。"); filtered = data
            else: filtered = data
            random.shuffle(filtered); st.session_state.questions = filtered[:50]
            st.session_state.index, st.session_state.score, st.session_state.session_streak = 0, 0, 0
            st.session_state.show_result = False; st.rerun()
else:
    total_q = len(st.session_state.questions)
    if st.session_state.index >= total_q:
        st.balloons(); st.success("特訓終了！"); 
        if st.button("TOPへ戻る"): st.session_state.clear(); st.rerun()
    else:
        q = st.session_state.questions[st.session_state.index]; is_order_q = "/" in q['q']
        if st.session_state.show_result:
            if st.session_state.last_is_correct:
                st.success(f"⭕ 正解！")
                time.sleep(1.2); st.session_state.index += 1; st.session_state.show_result = False
                st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
            else:
                st.error(f"❌ 正解は: {q['a']}")
                if st.button("次へ ➡️"):
                    st.session_state.index += 1; st.session_state.show_result = False
                    st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
        else:
            st.write(f"残り {total_q - st.session_state.index} 問")
            main_p, hint_p = parse_q_display(q['q'])
            st.subheader(main_p)
            if hint_p: st.info(f"💡 {hint_p}")
            canvas_res = st_canvas(stroke_width=9, height=180, width=700, key=f"c_{st.session_state.index}")
            if not st.session_state.show_options:
                if st.button("🔍 判定して選択肢を表示", use_container_width=True):
                    if not is_order_q and (not canvas_res.json_data or len(canvas_res.json_data.get("objects", [])) == 0):
                        st.warning("⚠️ 手書きで解答を書いてください！")
                    else: st.session_state.show_options = True; st.rerun()
            else:
                st.divider()
                if is_order_q:
                    words = [w.strip() for w in main_p.replace("(","").replace(")","").replace("?","").replace(".","").split("/") if w.strip()]
                    current = st.session_state.user_ans_list; disp = [w for w in words if w not in current]
                    if disp:
                        cols = st.columns(min(len(disp), 5))
                        for i, w in enumerate(disp):
                            if cols[i % 5].button(w, key=f"w_{i}", use_container_width=True): st.session_state.user_ans_list.append(w); st.rerun()
                    if current:
                        st.write(f"解答: {' '.join(current)}")
                        if st.button("🗑️ やり直す"): st.session_state.user_ans_list = []; st.session_state.show_options = False; st.rerun()
                        if len(current) == len(words):
                            is_ok = " ".join(current).lower() == q['a'].lower()
                            save_stat(q['q'], is_ok, q.get("rank", "A"), st.session_state.mode)
                            st.components.v1.html(f"<script>window.parent.playJudge({str(is_ok).lower()});</script>", height=0)
                            st.session_state.last_is_correct = is_ok
                            if is_ok: st.session_state.score += 1; st.session_state.session_streak += 1
                            else: st.session_state.session_streak = 0
                            time.sleep(0.5); st.session_state.show_result = True; st.rerun()
                else:
                    if 'cur_opts' not in st.session_state or st.session_state.get('last_q_id') != st.session_state.index:
                        correct_ans = str(q['a']); opts_set = {correct_ans}; pool = []
                        if "数学" in st.session_state.mode:
                            match = re.search(r"(-?\d*)([xyabπ]+.*)", correct_ans)
                            if match:
                                c_s, var = match.groups(); coef = 1 if c_s == "" else (-1 if c_s == "-" else int(c_s))
                                pool = [f"{coef+1}{var}", f"{coef-1}{var}", f"{-coef}{var}", "0"]
                            else:
                                try: v = int(correct_ans); pool = [str(-v), str(v+1), str(v-1), "0"]
                                except: pool = ["x", "y", "a", "b", "0"]
                        else:
                            pool = ["is", "am", "are", "was", "were", "the", "it", "to", "in"]
                        random.shuffle(pool)
                        for p in pool:
                            if len(opts_set) >= 4: break
                            if p != correct_ans: opts_set.add(p)
                        st.session_state.cur_opts = random.sample(list(opts_set), len(opts_set)); st.session_state.last_q_id = st.session_state.index
                    cols = st.columns(4)
                    for i, opt in enumerate(st.session_state.cur_opts):
                        if cols[i].button(opt, key=f"o_{i}", use_container_width=True):
                            is_ok = (opt.lower() == str(q['a']).lower())
                            save_stat(q['q'], is_ok, q.get("rank", "A"), st.session_state.mode)
                            st.components.v1.html(f"<script>window.parent.playJudge({str(is_ok).lower()});</script>", height=0)
                            st.session_state.last_is_correct = is_ok
                            if is_ok: st.session_state.score += 1; st.session_state.session_streak += 1
                            else: st.session_state.session_streak = 0
                            time.sleep(0.5); st.session_state.show_result = True; st.rerun()
                    if st.button("🗑️ やり直す"): st.session_state.show_options = False; st.rerun()
