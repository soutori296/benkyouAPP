import streamlit as st
from streamlit_drawable_canvas import st_canvas
import json, os, random, time, base64, re
from datetime import datetime
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

@st.cache_data(ttl=300)
def load_questions_from_gsheet():
    client = get_gsheet_client()
    if not client: return {}
    try:
        sh = client.open("study_stats_db").worksheet("questions")
        data = sh.get_all_records()
        organized = {}
        for row in data:
            cat = row.get('category', '共通')
            if not cat or ("数学" in str(cat) and "/" in str(row.get('q', ''))): continue
            if cat not in organized: organized[cat] = []
            organized[cat].append({"rank": row.get('rank', 'A'), "q": str(row.get('q', '')), "a": str(row.get('a', '')), "h": str(row.get('h', ''))})
        return organized
    except: return {}

def load_all_stats_with_records():
    client = get_gsheet_client()
    if not client: return {"history": {}, "streaks": {}, "last_sub": ""}
    try:
        ss = client.open("study_stats_db")
        hist_data = ss.sheet1.get_all_records()
        history = {str(row['q']): {"correct": int(row['correct'] or 0), "wrong": int(row['wrong'] or 0), "subject": str(row.get('subject', '不明'))} for row in hist_data if row.get('q')}
        streaks = {}; last_sub = ""
        try:
            sum_sh = ss.worksheet("summary")
            sum_data = sum_sh.get_all_records()
            for row in sum_data:
                streaks[row['key']] = row['value']
                if row['key'] == "last_selected_subject": last_sub = str(row['value'])
        except: pass
        return {"history": history, "streaks": streaks, "last_sub": last_sub}
    except: return {"history": {}, "streaks": {}, "last_sub": ""}

def save_last_subject(sub_name):
    client = get_gsheet_client()
    if client:
        try:
            sh = client.open("study_stats_db").worksheet("summary")
            cell = sh.find("last_selected_subject")
            if cell: sh.update_cell(cell.row, 2, sub_name)
            else: sh.append_row(["last_selected_subject", sub_name])
        except: pass

def sync_results_to_gsheet():
    if not st.session_state.pending_results: return
    client = get_gsheet_client()
    if not client: return
    with st.spinner('記録を保存中...'):
        try:
            ss = client.open("study_stats_db")
            sheet = ss.sheet1
            rows = sheet.get_all_records()
            current_data = {str(r['q']): r for r in rows}
            for res in st.session_state.pending_results:
                q_t = res['q']
                if q_t in current_data:
                    if res['is_ok']: current_data[q_t]['correct'] = int(current_data[q_t]['correct']) + 1
                    else:
                        if int(current_data[q_t]['correct']) >= 5: current_data[q_t]['correct'] = 4
                        current_data[q_t]['wrong'] = int(current_data[q_t]['wrong']) + 1
                else:
                    current_data[q_t] = {'q': q_t, 'correct': 1 if res['is_ok'] else 0, 'wrong': 0 if res['is_ok'] else 1, 'rank': res['rank'], 'subject': res['subject']}
            final_rows = [['q', 'correct', 'wrong', 'rank', 'subject']]
            for v in current_data.values(): final_rows.append([v['q'], v['correct'], v['wrong'], v['rank'], v['subject']])
            sheet.update('A1', final_rows)
            sum_sh = ss.worksheet("summary")
            key_name = f"max_streak_{st.session_state.mode}"
            new_val = st.session_state.session_max_streak
            found = False
            for i, row in enumerate(sum_sh.get_all_values()):
                if row[0] == key_name:
                    if new_val > int(row[1] or 0): sum_sh.update_cell(i+1, 2, new_val)
                    found = True; break
            if not found: sum_sh.append_row([key_name, new_val])
            st.session_state.pending_results = []
            st.cache_data.clear()
        except: st.error("保存失敗")

def generate_clever_distractors(correct_ans, subject_mode, all_answers):
    distractors = set(); correct_ans = str(correct_ans)
    if "数学" in subject_mode:
        try:
            val = float(correct_ans)
            distractors.add(str(int(val + 1) if val.is_integer() else val + 1))
            distractors.add(str(int(val - 1) if val.is_integer() else val - 1))
            distractors.add(str(int(-val) if val.is_integer() else -val))
        except:
            if "-" in correct_ans: distractors.add(correct_ans.replace("-", ""))
            else: distractors.add("-" + correct_ans)
    pool = [str(a) for a in all_answers if abs(len(str(a)) - len(correct_ans)) <= 2 and str(a).lower() != correct_ans.lower()]
    m2 = [a for a in pool if a.lower().startswith(correct_ans[:2].lower())]
    m1 = [a for a in pool if a.lower().startswith(correct_ans[:1].lower()) and a not in m2]
    candidates = m2 + m1 + [a for a in pool if a not in m2 and a not in m1]
    for c in candidates:
        if len(distractors) >= 3: break
        distractors.add(c)
    defaults = ["is", "the", "was", "not", "to", "it"]
    for d in defaults:
        if len(distractors) >= 3: break
        if d.lower() != correct_ans.lower(): distractors.add(d)
    return list(distractors)[:3]

def setup_audio_engine():
    base_dir = os.path.dirname(os.path.abspath(__file__))
    c_p, w_p = os.path.join(base_dir, "correct.mp3"), os.path.join(base_dir, "wrong.mp3")
    c_b64 = base64.b64encode(open(c_p, "rb").read()).decode() if os.path.exists(c_p) else ""
    w_b64 = base64.b64encode(open(w_p, "rb").read()).decode() if os.path.exists(w_p) else ""
    st.components.v1.html(f"""<script>window.parent.playJudge = function(isCorrect) {{
        var audio = new Audio(isCorrect ? "data:audio/mp3;base64,{c_b64}" : "data:audio/mp3;base64,{w_b64}");
        audio.play(); }};</script>""", height=0)

def parse_q_display(text):
    match = re.search(r'\s+([ぁ-んァ-ヶ一-龠].*)$', text)
    if match:
        main_p = text[:match.start()].strip(); hint_p = match.group(1).strip()
        if main_p: return main_p, hint_p
    return text, ""

# セッション初期化
for k, v in {"user_ans_list": [], "show_options": False, "show_result": False, "index": 0, "session_streak": 0, "session_max_streak": 0, "pending_results": []}.items():
    if k not in st.session_state: st.session_state[k] = v

st.set_page_config(page_title="高校受験対策", layout="wide")
setup_audio_engine()

# 余白削りCSS
st.markdown("""<style>.block-container {padding-top: 1rem !important; padding-bottom: 0rem !important;} header {visibility: hidden;}</style>""", unsafe_allow_html=True)

# --- 2. サイドバー ---
with st.sidebar:
    st.title("📊 学習記録")
    if st.button("🔄 データを更新", use_container_width=True):
        st.cache_data.clear(); st.session_state.clear(); st.rerun()
    stats_res = load_all_stats_with_records()
    hist = stats_res["history"]; streaks = stats_res["streaks"]; last_sub = stats_res["last_sub"]
    if 'mode' in st.session_state:
        st.success(f"🔥 今日の連勝: {st.session_state.session_streak}")
        rec = int(streaks.get(f"max_streak_{st.session_state.mode}", 0))
        st.warning(f"👑 歴代最高: {max(rec, st.session_state.session_max_streak)}")
    st.write("**教科別の歴代記録**")
    for k, v in streaks.items():
        if "max_streak_" in k: st.caption(f"{k.replace('max_streak_', '')}: {v}連勝")
    st.divider(); st.write("<br>" * 30, unsafe_allow_html=True)
    if st.checkbox("⚙️ メンテナンス", value=False): pass

# --- 3. メイン画面 ---
if 'mode' not in st.session_state:
    st.title("🛡️ 高校受験対策")
    all_q = load_questions_from_gsheet()
    if all_q:
        q_keys = list(all_q.keys())
        default_idx = q_keys.index(last_sub) if last_sub in q_keys else 0
        sub = st.selectbox("特訓セットを選択", q_keys, index=default_idx)
        diff_opts = ["ミックス", "🔥 苦手克服"]
        if "数学" not in sub: diff_opts.insert(1, "🧩 並べ替え特訓")
        diff = st.radio("モード選択", diff_opts, horizontal=True)
        if st.button("特訓開始！", use_container_width=True):
            save_last_subject(sub); st.session_state.mode = sub; data = all_q.get(sub, [])
            filtered = [q for q in data if int(hist.get(q['q'], {}).get('correct', 0)) < 5 or random.random() < 0.2]
            if "並べ替え" in diff: filtered = [q for q in filtered if "/" in q['q']]
            elif "苦手克服" in diff:
                wrong_list = [q_t for q_t, v in hist.items() if v.get("wrong", 0) > 0]
                filtered = [q for q in filtered if q['q'] in wrong_list]
            if not filtered: filtered = data
            random.shuffle(filtered); st.session_state.questions = filtered[:50]
            st.session_state.all_ans_in_set = [q['a'] for q in data]
            st.session_state.index, st.session_state.session_streak, st.session_state.session_max_streak, st.session_state.pending_results = 0, 0, 0, []
            st.session_state.show_result = False; st.rerun()
else:
    total_q = len(st.session_state.questions)
    if st.session_state.index >= total_q:
        sync_results_to_gsheet(); st.balloons(); st.success("特訓終了！")
        if st.button("TOPへ戻る"): st.session_state.clear(); st.rerun()
    else:
        q = st.session_state.questions[st.session_state.index]; is_order_q = "/" in q['q']
        col_left, col_right = st.columns([1.5, 1])

        with col_left:
            st.write(f"残り {total_q - st.session_state.index} 問")
            main_p, hint_p = parse_q_display(q['q']); st.subheader(main_p)
            if hint_p: st.info(f"💡 {hint_p}")
            
            # ★ 改良：数学なら高さを500px、それ以外は250pxに自動調整
            canvas_height = 500 if "数学" in st.session_state.mode else 250
            canvas_res = st_canvas(
                stroke_width=9, height=canvas_height, width=800, 
                key=f"c_{st.session_state.index}", background_color="#f0f2f6"
            )

        with col_right:
            st.write("---")
            if st.session_state.show_result:
                if st.session_state.last_is_correct:
                    st.success("⭕ 正解！")
                    if st.button("次へ進む ➡️", use_container_width=True):
                        st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
                else:
                    st.error(f"❌ 正解は:\n\n**{q['a']}**")
                    if st.button("次へ進む ➡️", use_container_width=True):
                        st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
            else:
                c1, c2 = st.columns(2)
                with c1:
                    if not st.session_state.show_options:
                        if st.button("🔍 判定へ", use_container_width=True):
                            if not is_order_q and (not canvas_res.json_data or len(canvas_res.json_data.get("objects", [])) < 2):
                                st.error("☝️ 2画以上書いてね！")
                            else: st.session_state.show_options = True; st.rerun()
                with c2:
                    if st.button("🏳️ 中止保存", use_container_width=True):
                        sync_results_to_gsheet(); st.session_state.clear(); st.rerun()
                
                if st.session_state.show_options:
                    st.write("**答えを選択：**")
                    if is_order_q:
                        words = [w.strip() for w in main_p.replace("(","").replace(")","").replace("?","").replace(".","").split("/") if w.strip()]
                        current = st.session_state.user_ans_list; disp = [w for w in words if w not in current]
                        for i, w in enumerate(disp):
                            if st.button(w, key=f"w_{i}", use_container_width=True):
                                st.session_state.user_ans_list.append(w); st.rerun()
                        st.markdown(f"**解答:** {' '.join(current)}")
                        bc1, bc2 = st.columns(2)
                        with bc1:
                            if st.button("⬅️ 戻す", use_container_width=True):
                                if st.session_state.user_ans_list: st.session_state.user_ans_list.pop(); st.rerun()
                        with bc2:
                            if st.button("🗑️ クリア", use_container_width=True):
                                st.session_state.user_ans_list = []; st.rerun()
                        if len(current) == len(words):
                            is_ok = " ".join(current).lower() == q['a'].lower()
                            st.session_state.pending_results.append({'q': q['q'], 'is_ok': is_ok, 'rank': q.get('rank','A'), 'subject': st.session_state.mode})
                            st.components.v1.html(f"<script>window.parent.playJudge({str(is_ok).lower()});</script>", height=0)
                            if is_ok:
                                st.session_state.session_streak += 1; st.session_state.session_max_streak = max(st.session_state.session_max_streak, st.session_state.session_streak)
                            else: st.session_state.session_streak = 0
                            st.session_state.last_is_correct = is_ok; time.sleep(0.5); st.session_state.show_result = True; st.rerun()
                    else:
                        if 'cur_opts' not in st.session_state or st.session_state.get('last_q_id') != st.session_state.index:
                            opts = [q['a']] + generate_clever_distractors(q['a'], st.session_state.mode, st.session_state.all_ans_in_set)
                            st.session_state.cur_opts = random.sample(list(set(opts)), len(list(set(opts)))); st.session_state.last_q_id = st.session_state.index
                        for i, opt in enumerate(st.session_state.cur_opts):
                            if st.button(opt, key=f"o_{i}", use_container_width=True):
                                is_ok = (opt.lower() == str(q['a']).lower())
                                st.session_state.pending_results.append({'q': q['q'], 'is_ok': is_ok, 'rank': q.get('rank','A'), 'subject': st.session_state.mode})
                                st.components.v1.html(f"<script>window.parent.playJudge({str(is_ok).lower()});</script>", height=0)
                                if is_ok:
                                    st.session_state.session_streak += 1; st.session_state.session_max_streak = max(st.session_state.session_max_streak, st.session_state.session_streak)
                                else: st.session_state.session_streak = 0
                                st.session_state.last_is_correct = is_ok; time.sleep(0.5); st.session_state.show_result = True; st.rerun()
