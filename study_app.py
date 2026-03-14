import streamlit as st
from streamlit_drawable_canvas import st_canvas
import json, os, random, time, base64, re
from datetime import datetime, timedelta
import gspread
from google.oauth2.service_account import Credentials

# --- 1. 基本設定・API連携 ---
def get_gsheet_client():
    if "gcp_service_account" in st.secrets:
        try:
            scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
            creds = Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
            return gspread.authorize(creds)
        except: return None
    return None

@st.cache_data(ttl=60)
def load_questions_from_gsheet():
    client = get_gsheet_client()
    if not client: return {}
    try:
        sh = client.open("study_stats_db").worksheet("questions")
        data = sh.get_all_records()
        organized = {}
        for row in data:
            cat = row.get('category', '共通')
            # ★ 改良：数学の場合は「/」が含まれる問題（並び替え）を最初から除外
            if "数学" in cat and "/" in str(row.get('q', '')):
                continue
            if cat not in organized: organized[cat] = []
            organized[cat].append({"rank": row.get('rank', 'A'), "q": str(row.get('q', '')), "a": str(row.get('a', '')), "h": str(row.get('h', ''))})
        return organized
    except: return {}

@st.cache_data(ttl=300)
def load_all_stats_cached():
    client = get_gsheet_client()
    if not client: return {"history": {}, "status": "no_client"}
    try:
        sh = client.open("study_stats_db").sheet1
        data = sh.get_all_records()
        history = {str(row['q']): {"correct": int(row['correct'] or 0), "wrong": int(row['wrong'] or 0), "subject": str(row.get('subject', '不明'))} for row in data if row.get('q')}
        return {"history": history, "status": "ok"}
    except: return {"history": {}, "status": "error"}

def update_question_in_gsheet(old_q, new_q, new_a):
    client = get_gsheet_client()
    if client:
        try:
            sh = client.open("study_stats_db").worksheet("questions")
            cell = sh.find(old_q)
            if cell:
                sh.update_cell(cell.row, 3, new_q); sh.update_cell(cell.row, 4, new_a)
                return True
        except: pass
    return False

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
                sh.update_cell(row, 2, c_val); sh.update_cell(row, 3, w_val)
            else:
                sh.append_row([q_text, 1 if is_correct else 0, 0 if is_correct else 1, rank, subject])
        except: pass

def generate_clever_distractors(correct_ans, subject_mode, all_answers):
    distractors = set()
    if "数学" in subject_mode:
        try:
            val = float(correct_ans)
            distractors.add(str(int(val + 1) if val.is_integer() else val + 1))
            distractors.add(str(int(val - 1) if val.is_integer() else val - 1))
            distractors.add(str(int(-val) if val.is_integer() else -val))
            distractors.add("0")
        except:
            if "-" in correct_ans: distractors.add(correct_ans.replace("-", ""))
            else: distractors.add("-" + correct_ans)
            distractors.add(re.sub(r'\d+', lambda x: str(int(x.group())+1), correct_ans))
    other_ans = [a for a in all_answers if a.lower() != correct_ans.lower()]
    if other_ans:
        random.shuffle(other_ans)
        for a in other_ans:
            if len(distractors) >= 3: break
            distractors.add(a)
    return list(distractors)[:3]

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

for k, v in {"user_ans_list": [], "show_options": False, "show_result": False, "score": 0, "index": 0, "session_streak": 0}.items():
    if k not in st.session_state: st.session_state[k] = v

st.set_page_config(page_title="70点マスター", layout="centered")
setup_audio_engine()

# --- 2. サイドバー ---
with st.sidebar:
    st.title("📊 学習管理")
    if st.button("🔄 データを更新", use_container_width=True): st.cache_data.clear(); st.rerun()
    
    # ★ 改良：表示中の問題を優先修正
    if 'mode' in st.session_state and st.session_state.index < len(st.session_state.questions):
        cur_q = st.session_state.questions[st.session_state.index]
        with st.expander("🛠️ 今表示中の問題を修正", expanded=True):
            new_q = st.text_input("問題文を修正", value=cur_q['q'], key="instant_q")
            new_a = st.text_input("答えを修正", value=cur_q['a'], key="instant_a")
            if st.button("この問題をスプレッドシートに保存"):
                if update_question_in_gsheet(cur_q['q'], new_q, new_a):
                    st.success("保存しました！更新ボタンを押すと反映されます。")
                    st.cache_data.clear()
                else: st.error("保存失敗")
    
    with st.expander("🔍 過去の問題を検索して修正"):
        search_txt = st.text_input("検索ワード")
        if search_txt:
            all_qs = load_questions_from_gsheet()
            for cat, q_list in all_qs.items():
                for q_item in q_list:
                    if search_txt in q_item['q']:
                        st.write(f"--- ({cat}) ---")
                        n_q = st.text_input("問題文", value=q_item['q'], key=f"srch_q_{q_item['q']}")
                        n_a = st.text_input("正解", value=q_item['a'], key=f"srch_a_{q_item['q']}")
                        if st.button("保存", key=f"btn_srch_{q_item['q']}"):
                            if update_question_in_gsheet(q_item['q'], n_q, n_a):
                                st.success("完了！"); st.cache_data.clear()

    st.divider()
    res = load_all_stats_cached(); hist = res.get("history", {})
    if hist:
        subjects = sorted(list(set(v.get("subject", "不明") for v in hist.values())))
        for sub in subjects:
            sub_qs = [v for v in hist.values() if v.get("subject") == sub]
            total_t = sum(v["correct"] + v["wrong"] for v in sub_qs); total_c = sum(v["correct"] for v in sub_qs)
            rate = int(total_c / total_t * 100) if total_t > 0 else 0
            st.write(f"**{sub}**: {rate}点 ({total_t}回)"); st.progress(rate / 100)

# --- 3. メイン画面 ---
if 'mode' not in st.session_state:
    st.title("🛡️ 70点奪取特訓")
    all_q = load_questions_from_gsheet()
    if all_q:
        sub = st.selectbox("特訓セットを選択", list(all_q.keys()))
        diff_options = ["ミックス", "🔥 苦手克服"]
        # ★ 改良：数学以外の場合のみ並べ替えを表示
        if "数学" not in sub: diff_options.insert(1, "🧩 並べ替え特訓")
        
        diff = st.radio("モード選択", diff_options, horizontal=True)
        if st.button("特訓開始！", use_container_width=True):
            st.session_state.mode = sub; data = all_q.get(sub, [])
            if "並べ替え" in diff: filtered = [q for q in data if "/" in q['q']]
            elif "苦手克服" in diff:
                wrong_list = [q_t for q_t, v in hist.items() if v.get("wrong", 0) > 0]
                filtered = [q for q in data if q['q'] in wrong_list]
                if not filtered: st.warning("苦手なし。"); filtered = data
            else: filtered = data
            random.shuffle(filtered); st.session_state.questions = filtered[:50]
            st.session_state.all_ans_in_set = [q['a'] for q in data]
            st.session_state.index, st.session_state.score, st.session_state.session_streak = 0, 0, 0
            st.session_state.show_result = False; st.rerun()
else:
    # クイズ進行ロジック（Ver.143と同様）
    total_q = len(st.session_state.questions)
    if st.session_state.index >= total_q:
        st.balloons(); st.success("特訓終了！")
        if st.button("TOPへ戻る"): st.session_state.clear(); st.rerun()
    else:
        q = st.session_state.questions[st.session_state.index]; is_order_q = "/" in q['q']
        if st.session_state.show_result:
            if st.session_state.last_is_correct:
                st.success("⭕ 正解！"); time.sleep(1.0)
                st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
            else:
                st.error(f"❌ 正解は: {q['a']}")
                if st.button("次へ ➡️"):
                    st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
        else:
            st.write(f"残り {total_q - st.session_state.index} 問 / **{st.session_state.session_streak}** 連勝中")
            main_p, hint_p = parse_q_display(q['q']); st.subheader(main_p)
            if hint_p: st.info(f"💡 {hint_p}")
            canvas_res = st_canvas(stroke_width=9, height=180, width=700, key=f"c_{st.session_state.index}")
            
            c1, c2 = st.columns(2)
            with c1:
                if not st.session_state.show_options:
                    if st.button("🔍 判定して選択肢を表示", use_container_width=True):
                        if not is_order_q and (not canvas_res.json_data or len(canvas_res.json_data.get("objects", [])) == 0):
                            st.warning("⚠️ 手書きして！")
                        else: st.session_state.show_options = True; st.rerun()
            with c2:
                if st.button("🏳️ 降参（中止）", use_container_width=True): st.session_state.clear(); st.rerun()

            if st.session_state.show_options:
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
                        dummies = generate_clever_distractors(q['a'], st.session_state.mode, st.session_state.all_ans_in_set)
                        opts = list(set([q['a']] + dummies))
                        st.session_state.cur_opts = random.sample(opts, len(opts))
                        st.session_state.last_q_id = st.session_state.index
                    cols = st.columns(len(st.session_state.cur_opts))
                    for i, opt in enumerate(st.session_state.cur_opts):
                        if cols[i].button(opt, key=f"o_{i}", use_container_width=True):
                            is_ok = (opt.lower() == str(q['a']).lower())
                            save_stat(q['q'], is_ok, q.get("rank", "A"), st.session_state.mode)
                            st.components.v1.html(f"<script>window.parent.playJudge({str(is_ok).lower()});</script>", height=0)
                            st.session_state.last_is_correct = is_ok
                            if is_ok: st.session_state.score += 1; st.session_state.session_streak += 1
                            else: st.session_state.session_streak = 0
                            time.sleep(0.5); st.session_state.show_result = True; st.rerun()
