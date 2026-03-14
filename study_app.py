import streamlit as st
from streamlit_drawable_canvas import st_canvas
import json, os, random, time, base64, re, io
import pandas as pd
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials

# --- 1. 基本設定・API連携 ---
def get_creds():
    if "gcp_service_account" in st.secrets:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        return Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    return None

@st.cache_data(ttl=300)
def load_questions_from_gsheet():
    creds = get_creds()
    if not creds: return {}
    try:
        client = gspread.authorize(creds)
        sh = client.open("study_stats_db").worksheet("questions")
        data = sh.get_all_records()
        organized = {}
        for row in data:
            cat = row.get('category', '共通')
            if not row.get('q') or not row.get('a'): continue
            if cat not in organized: organized[cat] = []
            organized[cat].append({"rank": row.get('rank', 'A'), "q": str(row.get('q', '')), "a": str(row.get('a', '')), "h": str(row.get('h', ''))})
        return organized
    except Exception as e:
        st.error(f"データ読み込み失敗: {e}"); return {}

def update_question_in_gsheet(category, old_q, new_q, new_a):
    creds = get_creds()
    if not creds: return False
    try:
        client = gspread.authorize(creds)
        sh = client.open("study_stats_db").worksheet("questions")
        records = sh.get_all_records()
        for i, row in enumerate(records):
            if str(row.get('category')) == category and str(row.get('q')) == old_q:
                row_idx = i + 2
                sh.update_cell(row_idx, 2, new_q)
                sh.update_cell(row_idx, 3, new_a)
                return True
    except Exception as e:
        st.error(f"修正保存エラー: {e}"); return False

def load_all_stats_with_records():
    creds = get_creds()
    if not creds: return {"history": {}, "streaks": {}, "last_sub": "", "raw_data": []}
    try:
        ss = gspread.authorize(creds).open("study_stats_db")
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
        return {"history": history, "streaks": streaks, "last_sub": last_sub, "raw_data": hist_data}
    except: return {"history": {}, "streaks": {}, "last_sub": "", "raw_data": []}

def save_last_subject(sub_name):
    creds = get_creds()
    if creds:
        try:
            sh = gspread.authorize(creds).open("study_stats_db").worksheet("summary")
            cell = sh.find("last_selected_subject")
            if cell: sh.update_cell(cell.row, 2, sub_name)
            else: sh.append_row(["last_selected_subject", sub_name])
        except: pass

def sync_results_to_gsheet():
    if not st.session_state.pending_results: return
    creds = get_creds()
    if not creds: return
    with st.spinner('記録を保存中...'):
        try:
            ss = gspread.authorize(creds).open("study_stats_db")
            sheet = ss.sheet1; rows = sheet.get_all_records()
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
            sum_sh = ss.worksheet("summary"); key_name = f"max_streak_{st.session_state.mode}"; new_val = st.session_state.session_max_streak; found = False
            for i, row in enumerate(sum_sh.get_all_values()):
                if row[0] == key_name:
                    if new_val > int(row[1] or 0): sum_sh.update_cell(i+1, 2, new_val)
                    found = True; break
            if not found: sum_sh.append_row([key_name, new_val])
            st.session_state.pending_results = []; st.cache_data.clear()
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
for k, v in {"user_ans_list": [], "show_options": False, "show_result": False, "index": 0, "session_streak": 0, "session_max_streak": 0, "pending_results": [], "p_edit_q_obj": None}.items():
    if k not in st.session_state: st.session_state[k] = v

st.set_page_config(page_title="高校受験対策", layout="wide")
setup_audio_engine()
st.markdown("""<style>.block-container {padding-top: 1rem !important;} header {visibility: hidden;}</style>""", unsafe_allow_html=True)

# --- 2. サイドバー ---
with st.sidebar:
    st.title("📊 学習記録")
    if st.button("🔄 最新データに更新", use_container_width=True):
        st.cache_data.clear(); st.session_state.clear(); st.rerun()
    
    stats_res = load_all_stats_with_records()
    hist = stats_res["history"]; streaks = stats_res["streaks"]; last_sub = stats_res["last_sub"]
    
    if 'mode' in st.session_state:
        st.success(f"🔥 今日の連勝: {st.session_state.session_streak}")
        rec = int(streaks.get(f"max_streak_{st.session_state.mode}", 0))
        st.warning(f"👑 歴代最高: {max(rec, st.session_state.session_max_streak)}")
    
    st.divider()
    # --- 保護者メニュー（Ver.180：最短UI版） ---
    with st.expander("👨‍👩‍👧 保護者メニュー"):
        p_mode = st.checkbox("保護者モードを有効にする")
        if p_mode:
            p_tabs = st.tabs(["📈 統計", "🛠️ 問題修正"])
            with p_tabs[0]:
                df = pd.DataFrame(stats_res["raw_data"])
                if not df.empty:
                    df['correct'] = pd.to_numeric(df['correct'], errors='coerce').fillna(0)
                    df['wrong'] = pd.to_numeric(df['wrong'], errors='coerce').fillna(0)
                    df['Total'] = df['correct'] + df['wrong']
                    df['正答率'] = (df['correct'] / df['Total'] * 100).fillna(0).round(1)
                    st.dataframe(df[['subject', 'q', 'correct', 'wrong', '正答率']], use_container_width=True)
            
            with p_tabs[1]:
                all_q_edit = load_questions_from_gsheet()
                if all_q_edit:
                    # 1. 今の問題を即セット
                    if 'mode' in st.session_state and st.session_state.index < len(st.session_state.questions):
                        cur_q = st.session_state.questions[st.session_state.index]
                        if st.button(f"📢 今の問題を修正：{cur_q['q'][:10]}...", use_container_width=True):
                            st.session_state["p_edit_q_obj"] = {"cat": st.session_state.mode, "q": cur_q['q'], "a": cur_q['a']}
                            st.rerun()
                    
                    st.write("---")
                    # 2. 検索欄
                    search_txt = st.text_input("🔍 問題を検索（クリックで修正対象を選択）", placeholder="キーワードを入力")
                    
                    if search_txt:
                        flat_list = []
                        for cat, items in all_q_edit.items():
                            for item in items:
                                if search_txt.lower() in item['q'].lower():
                                    flat_list.append({"cat": cat, "q": item['q'], "a": item['a']})
                        
                        if flat_list:
                            st.caption("検索結果（クリックしてセット）:")
                            for i, f in enumerate(flat_list[:10]): # 上位10件をボタン表示
                                if st.button(f"[{f['cat']}] {f['q'][:20]}...", key=f"src_{i}", use_container_width=True):
                                    st.session_state["p_edit_q_obj"] = f
                                    st.rerun()
                        else: st.info("見つかりません。")

                    st.write("---")
                    # 3. 修正フォーム（値がある時だけ表示、ないなら空白）
                    target = st.session_state["p_edit_q_obj"]
                    val_q = target['q'] if target else ""
                    val_a = target['a'] if target else ""
                    
                    if target: st.caption(f"対象教科: {target['cat']}")
                    new_q = st.text_input("問題文 (修正)", value=val_q)
                    new_a = st.text_input("正解 (修正)", value=val_a)
                    
                    if st.button("✅ 修正内容を保存", use_container_width=True):
                        if target and update_question_in_gsheet(target['cat'], target['q'], new_q, new_a):
                            st.success("保存完了！")
                            st.cache_data.clear()
                            st.session_state["p_edit_q_obj"] = None
                            time.sleep(1); st.rerun()
                        elif not target:
                            st.error("修正する問題を選択してください。")

    st.divider(); st.write("<br>" * 10, unsafe_allow_html=True)

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
        q = st.session_state.questions[st.session_state.index]; is_order_q = "/" in q['q']; is_math = "数学" in st.session_state.mode
        main_p, hint_p = parse_q_display(q['q'])
        if is_math:
            col_l, col_r = st.columns([1.5, 1])
            with col_l:
                st.write(f"残り {total_q - st.session_state.index} 問"); st.subheader(main_p)
                if hint_p: st.info(f"💡 {hint_p}")
                canvas_res = st_canvas(stroke_width=9, height=500, width=800, key=f"c_{st.session_state.index}", background_color="#f0f2f6")
            target_col = col_r
        else:
            st.write(f"残り {total_q - st.session_state.index} 問"); st.subheader(main_p)
            if hint_p: st.info(f"💡 {hint_p}")
            canvas_res = st_canvas(stroke_width=9, height=250, width=1200, key=f"c_{st.session_state.index}", background_color="#f0f2f6")
            st.write("---"); target_col = st.container()

        with target_col:
            if is_math: st.write("---")
            if st.session_state.show_result:
                if st.session_state.last_is_correct:
                    st.success(f"## ✨ 正解！ : {q['a']}")
                    if st.button("次へ進む ➡️", use_container_width=True, type="primary"):
                        st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
                else:
                    st.error(f"## ❌ ざんねん！ 正解は {q['a']}")
                    st.markdown(f"""<div style="background-color:#ffe9e9; padding:15px; border-radius:10px; border:2px solid #ff4b4b; text-align:center;"><span style="color:#31333f; font-weight:bold; font-size:2rem;">{q['a']}</span></div>""", unsafe_allow_html=True)
                    if st.button("理解した！次へ ➡️", use_container_width=True):
                        st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
            else:
                b_cols = st.columns(2)
                with b_cols[0]:
                    if not st.session_state.show_options:
                        if st.button("🔍 判定へ", use_container_width=True):
                            if not is_order_q and (not canvas_res.json_data or len(canvas_res.json_data.get("objects", [])) < 2):
                                st.error("☝️ 2画以上書いてね！")
                            else: st.session_state.show_options = True; st.rerun()
                with b_cols[1]:
                    if st.button("🏳️ 中止保存", use_container_width=True):
                        sync_results_to_gsheet(); st.session_state.clear(); st.rerun()
                
                if st.session_state.show_options:
                    st.write("**答えを選択：**")
                    if is_order_q:
                        words = [w.strip() for w in main_p.replace("(","").replace(")","").replace("?","").replace(".","").split("/") if w.strip()]
                        current = st.session_state.user_ans_list; disp = [w for w in words if w not in current]
                        if current:
                            st.markdown(f"""<div style="background-color:#e1f5fe; padding:15px; border-radius:10px; border-left:5px solid #03a9f4; margin-bottom:20px;"><span style="color:#0277bd; font-size:2.0rem; font-weight:bold; letter-spacing:1px;">{" ".join(current)}</span></div>""", unsafe_allow_html=True)
                        if len(disp) > 0:
                            cols = st.columns(5)
                            for i, w in enumerate(disp):
                                if cols[i % 5].button(w, key=f"w_{i}_{len(current)}"):
                                    st.session_state.user_ans_list.append(w); st.rerun()
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
                            if is_ok: st.session_state.session_streak += 1; st.session_state.session_max_streak = max(st.session_state.session_max_streak, st.session_state.session_streak)
                            else: st.session_state.session_streak = 0
                            st.session_state.last_is_correct = is_ok; time.sleep(0.5); st.session_state.show_result = True; st.rerun()
                    else:
                        if 'cur_opts' not in st.session_state or st.session_state.get('last_q_id') != st.session_state.index:
                            opts = [q['a']] + generate_clever_distractors(q['a'], st.session_state.mode, st.session_state.all_ans_in_set)
                            st.session_state.cur_opts = random.sample(list(set(opts)), len(list(set(opts)))); st.session_state.last_q_id = st.session_state.index
                        if not is_math:
                            opt_cols = st.columns(len(st.session_state.cur_opts))
                            for i, opt in enumerate(st.session_state.cur_opts):
                                if opt_cols[i].button(opt, key=f"o_{i}", use_container_width=True):
                                    is_ok = (opt.lower() == str(q['a']).lower())
                                    st.session_state.pending_results.append({'q': q['q'], 'is_ok': is_ok, 'rank': q.get('rank','A'), 'subject': st.session_state.mode})
                                    st.components.v1.html(f"<script>window.parent.playJudge({str(is_ok).lower()});</script>", height=0)
                                    if is_ok: st.session_state.session_streak += 1; st.session_state.session_max_streak = max(st.session_state.session_max_streak, st.session_state.session_streak)
                                    else: st.session_state.session_streak = 0
                                    st.session_state.last_is_correct = is_ok; time.sleep(0.5); st.session_state.show_result = True; st.rerun()
                        else:
                            for i, opt in enumerate(st.session_state.cur_opts):
                                if st.button(opt, key=f"o_{i}", use_container_width=True):
                                    is_ok = (opt.lower() == str(q['a']).lower())
                                    st.session_state.pending_results.append({'q': q['q'], 'is_ok': is_ok, 'rank': q.get('rank','A'), 'subject': st.session_state.mode})
                                    st.components.v1.html(f"<script>window.parent.playJudge({str(is_ok).lower()});</script>", height=0)
                                    if is_ok: st.session_state.session_streak += 1; st.session_state.session_max_streak = max(st.session_state.session_max_streak, st.session_state.session_streak)
                                    else: st.session_state.session_streak = 0
                                    st.session_state.last_is_correct = is_ok; time.sleep(0.5); st.session_state.show_result = True; st.rerun()
