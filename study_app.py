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

def format_math_text(text):
    if not isinstance(text, str): return text
    text = text.replace('*', '×')
    text = text.replace('^2', '²').replace('^3', '³')
    return text
    
def is_too_easy_math(category, q_text):
    if "数学" not in category: return False
    if re.search(r'[xyabπ\^²³\(\)＝=]', q_text): return False
    if re.match(r'^\d+\s*[\+\-\*\/×÷]\s*\d+\s*=$', q_text.strip()): return True
    return False

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
            cat = str(row.get('category', '共通'))
            q_raw = str(row.get('q', ''))
            if is_too_easy_math(cat, q_raw) or not q_raw or not row.get('a'): continue
            if cat not in organized: organized[cat] = []
            organized[cat].append({
                "rank": row.get('rank', 'A'), 
                "q": q_raw, 
                "a": str(row.get('a', '')), 
                "h": str(row.get('h', '')),
                "orig_cat": cat
            })
        return organized
    except: return {}

def update_question_in_gsheet(category, old_q, new_q, new_a):
    creds = get_creds()
    if not creds: return False
    try:
        client = gspread.authorize(creds)
        sh = client.open("study_stats_db").worksheet("questions")
        records = sh.get_all_records()
        for i, row in enumerate(records):
            if str(row.get('category')) == category and str(row.get('q')) == old_q:
                sh.update_cell(i + 2, 2, new_q); sh.update_cell(i + 2, 3, new_a); return True
    except: return False

def load_all_stats_with_records():
    creds = get_creds()
    if not creds: return {"history": {}, "streaks": {}, "last_sub": "", "raw_data": []}
    try:
        ss = gspread.authorize(creds).open("study_stats_db")
        hist_raw = ss.sheet1.get_all_records()
        hist = {str(r['q']): {"correct": int(r['correct'] or 0), "wrong": int(r['wrong'] or 0), "subject": str(r.get('subject', '不明'))} for r in hist_raw if r.get('q')}
        streaks = {}; last_sub = ""
        try:
            for r in ss.worksheet("summary").get_all_records():
                streaks[r['key']] = r['value']
                if r['key'] == "last_selected_subject": last_sub = str(r['value'])
        except: pass
        return {"history": hist, "streaks": streaks, "last_sub": last_sub, "raw_data": hist_raw}
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
    try:
        ss = gspread.authorize(creds).open("study_stats_db"); sheet = ss.sheet1; rows = sheet.get_all_records()
        cur_data = {str(r['q']): r for r in rows}
        for res in st.session_state.pending_results:
            q_t = res['q']
            if q_t in cur_data:
                if res['is_ok']: cur_data[q_t]['correct'] = int(cur_data[q_t]['correct']) + 1
                else:
                    if int(cur_data[q_t]['correct']) >= 5: cur_data[q_t]['correct'] = 4
                    cur_data[q_t]['wrong'] = int(cur_data[q_t]['wrong']) + 1
            else: cur_data[q_t] = {'q': q_t, 'correct': 1 if res['is_ok'] else 0, 'wrong': 0 if res['is_ok'] else 1, 'rank': res['rank'], 'subject': res['subject']}
        final = [['q', 'correct', 'wrong', 'rank', 'subject']]
        for v in cur_data.values(): final.append([v['q'], v['correct'], v['wrong'], v['rank'], v['subject']])
        sheet.update('A1', final)
        
        sum_sh = ss.worksheet("summary"); key = f"max_streak_{st.session_state.mode}"; val = st.session_state.session_max_streak; found = False
        for i, r in enumerate(sum_sh.get_all_values()):
            if r[0] == key:
                if val > int(r[1] or 0): sum_sh.update_cell(i+1, 2, val)
                found = True; break
        if not found: sum_sh.append_row([key, val])
        st.session_state.pending_results = []; st.cache_data.clear()
    except: st.error("保存失敗")

def generate_clever_distractors(correct_ans, mode, all_ans):
    dists = set(); c_ans = str(correct_ans)
    if "数学" in mode:
        try:
            v = float(c_ans)
            dists.add(str(int(v+1) if v.is_integer() else v+1))
            dists.add(str(int(v-1) if v.is_integer() else v-1))
            dists.add(str(int(-v) if v.is_integer() else -v))
        except:
            if "=" in c_ans:
                var, val = c_ans.split("=")
                try:
                    v = int(val.strip())
                    dists.add(f"{var}= {v+1}"); dists.add(f"{var}= {v-1}"); dists.add(f"{var}= {-v}")
                except: pass
    pool = [str(a) for a in all_ans if abs(len(str(a)) - len(c_ans)) <= 5 and str(a).lower() != c_ans.lower()]
    random.shuffle(pool)
    for c in pool:
        if len(dists) >= 3: break
        dists.add(c)
    return list(dists)[:3]

def setup_audio():
    base = os.path.dirname(os.path.abspath(__file__)); c_p, w_p = os.path.join(base, "correct.mp3"), os.path.join(base, "wrong.mp3")
    c_b64 = base64.b64encode(open(c_p, "rb").read()).decode() if os.path.exists(c_p) else ""
    w_b64 = base64.b64encode(open(w_p, "rb").read()).decode() if os.path.exists(w_p) else ""
    st.components.v1.html(f"<script>window.parent.playJudge=function(isOk){{new Audio(isOk?'data:audio/mp3;base64,{c_b64}':'data:audio/mp3;base64,{w_b64}').play();}};</script>", height=0)

def parse_q_display(text):
    if re.match(r'^[A-Za-z\s\(\)\.\,\?\!]+', text):
        m = re.search(r'\s+([ぁ-んァ-ヶ一-龠].*)$', text)
        if m: return text[:m.start()].strip(), m.group(1).strip()
    return text, ""

# セッション初期化
for k, v in {"user_ans_list": [], "show_options": False, "show_result": False, "index": 0, "session_streak": 0, "session_max_streak": 0, "pending_results": [], "p_edit_obj": None}.items():
    if k not in st.session_state: st.session_state[k] = v

st.set_page_config(
    page_title="高校受験対策", 
    layout="wide", 
    initial_sidebar_state="expanded"  # ← これを追加（強制展開）
)
setup_audio()

# CSS修正：サイドバーを出すボタン(header内のボタン)を消さないように設定
st.markdown("""
    <style>
    .block-container {padding-top: 1rem;}
    [data-testid="stToolbar"] {visibility: hidden;}
    </style>
    """, unsafe_allow_html=True)

# --- 2. サイドバー ---
with st.sidebar:
    st.title("📊 学習記録")
    if st.button("🔄 最新データに更新", use_container_width=True): st.cache_data.clear(); st.session_state.clear(); st.rerun()
    stats_data = load_all_stats_with_records()
    if 'mode' in st.session_state:
        st.success(f"🔥 今日の連勝: {st.session_state.session_streak}")
        st.warning(f"👑 歴代最高: {max(int(stats_data['streaks'].get(f'max_streak_{st.session_state.mode}', 0)), st.session_state.session_max_streak)}")
        
        # 中止保存ボタンをこちらに配置
        if st.button("🏳️ 中止保存", use_container_width=True):
            sync_results_to_gsheet()
            st.session_state.clear()
            st.rerun()
    
    st.divider()
    with st.expander("👨‍👩‍👧 保護者メニュー"):
        if st.checkbox("保護者モードを有効にする"):
            p_tabs = st.tabs(["📈 統計", "🛠️ 問題修正"])
            with p_tabs[0]:
                df = pd.DataFrame(stats_data["raw_data"])
                if not df.empty:
                    for c in ['correct', 'wrong']: df[c] = pd.to_numeric(df[c], errors='coerce').fillna(0)
                    df['正答率'] = (df['correct'] / (df['correct'] + df['wrong']) * 100).fillna(0).round(1)
                    st.dataframe(df[['subject', 'q', 'correct', 'wrong', '正答率']], use_container_width=True)
            
            with p_tabs[1]:
                all_q_edit = load_questions_from_gsheet()
                src = st.text_input("🔍 問題を検索", placeholder="例: I have a pen")
                match = None
                if src:
                    for c, items in all_q_edit.items():
                        for i in items:
                            if src.lower() in i['q'].lower():
                                match = {"cat": c, "q": i['q'], "a": i['a']}; break
                        if match: break
                elif 'mode' in st.session_state and st.session_state.index < len(st.session_state.questions):
                    cur = st.session_state.questions[st.session_state.index]
                    match = {"cat": st.session_state.mode, "q": cur['q'], "a": cur['a']}
                
                st.session_state.p_edit_obj = match
                target = st.session_state.p_edit_obj
                if target: st.caption(f"対象教科: {target['cat']}")
                new_q = st.text_input("問題文 (修正)", value=target['q'] if target else "")
                new_a = st.text_input("正解 (修正)", value=target['a'] if target else "")
                
                if st.button("✅ 修正を保存", use_container_width=True) and target:
                    if update_question_in_gsheet(target['cat'], target['q'], new_q, new_a):
                        st.success("保存完了！"); st.cache_data.clear(); time.sleep(1); st.rerun()

    st.divider(); st.write("<br>" * 10, unsafe_allow_html=True)

# --- 3. メイン画面 ---
if 'mode' not in st.session_state:
    st.title("🛡️ 高校受験対策"); all_q = load_questions_from_gsheet()
    if all_q:
        raw_keys = list(all_q.keys())
        special_options = ["英語 (1・2年合同)", "数学 (1・2年合同)"]
        q_keys = special_options + raw_keys
        
        last_sub = stats_data['last_sub']
        sub = st.selectbox("特訓セットを選択", q_keys, index=q_keys.index(last_sub) if last_sub in q_keys else 0)
        diff = st.radio("モード選択", ["ミックス", "🧩 並べ替え特訓", "🔥 苦手克服"], horizontal=True)
        
        if st.button("特訓開始！", use_container_width=True):
            save_last_subject(sub); st.session_state.mode = sub
            target_cats = []
            if "英語" in sub and "合同" in sub: target_cats = [k for k in raw_keys if "英語" in k]
            elif "数学" in sub and "合同" in sub: target_cats = [k for k in raw_keys if "数学" in k]
            else: target_cats = [sub]
            
            cat_groups = {}
            total_ans_pool = []
            for cat in target_cats:
                data = all_q.get(cat, [])
                total_ans_pool.extend([q['a'] for q in data])
                f_data = [q for q in data if int(stats_data['history'].get(q['q'], {}).get('correct', 0)) < 5 or random.random() < 0.2]
                if "数学" in cat:
                    f_data = [q for q in f_data if not ("/" in q['q'] and " " in str(q['a']).strip())]
                if "並べ替え" in diff:
                    f_data = [q for q in f_data if "/" in q['q']]
                elif "苦手克服" in diff:
                    f_data = [q for q in f_data if stats_data['history'].get(q['q'], {}).get('wrong', 0) > 0]
                
                if not f_data: f_data = data
                random.shuffle(f_data); cat_groups[cat] = f_data
            
            interleaved = []
            if cat_groups:
                max_q = max(len(v) for v in cat_groups.values())
                for i in range(0, max_q, 3):
                    for cat in sorted(cat_groups.keys()):
                        chunk = cat_groups[cat][i : i+3]
                        interleaved.extend(chunk)
            
            st.session_state.questions = interleaved[:80]; st.session_state.all_ans_in_set = list(set(total_ans_pool))
            st.session_state.index = 0; st.session_state.session_streak = 0; st.session_state.session_max_streak = 0; st.session_state.pending_results = []; st.session_state.show_result = False; st.rerun()
else:
    total = len(st.session_state.questions)
    if st.session_state.index >= total:
        sync_results_to_gsheet(); st.balloons(); st.success("特訓終了！")
        if st.button("TOPへ戻る"): st.session_state.clear(); st.rerun()
    else:
        q = st.session_state.questions[st.session_state.index]; is_math = "数学" in q['orig_cat']
        is_order_q = ("/" in q['q']) and (" " in str(q['a']).strip())
        main_p, hint_p = parse_q_display(q['q']); display_q = format_math_text(main_p)
        
        st.caption(f"カテゴリー: {q['orig_cat']} | 残り {total - st.session_state.index} 問")
        
        if is_math:
            col_l, col_r = st.columns([1.5, 1])
            with col_l:
                st.markdown(f"### {display_q}", unsafe_allow_html=True)
                if hint_p: st.info(f"💡 {hint_p}")
                canvas_res = st_canvas(stroke_width=9, height=500, width=800, key=f"c_{st.session_state.index}", background_color="#f8f9fb")
            target_col = col_r
        else:
            st.subheader(display_q)
            if hint_p: st.info(f"💡 {hint_p}")
            canvas_res = st_canvas(stroke_width=9, height=250, width=1200, key=f"c_{st.session_state.index}", background_color="#f8f9fb")
            st.write("---"); target_col = st.container()

        with target_col:
            if is_math: st.write("---")
            if st.session_state.show_result:
                if st.session_state.last_is_correct:
                    st.success(f"### ✨ 正解！ : {format_math_text(q['a'])}")
                    if st.button("次へ進む ➡️", use_container_width=True, type="primary"): st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
                else:
                    st.error(f"### ❌ ざんねん！ 正解は **{format_math_text(q['a'])}**")
                    if st.button("理解した！次へ ➡️", use_container_width=True): st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
            else:
                if not st.session_state.show_options:
                    if st.button("🔍 判定へ", use_container_width=True): st.session_state.show_options = True; st.rerun()
                
                if st.session_state.show_options:
                    st.write("**答えを選択：**")
                    if is_order_q:
                        words = [w.strip() for w in main_p.replace("(","").replace(")","").replace("?","").replace(".","").split("/") if w.strip()]
                        current = st.session_state.user_ans_list; disp = [w for w in words if w not in current]
                        if current: st.info(" ".join(current))
                        if disp:
                            # 並べ替えの単語ボタンを1行にする
                            cols = st.columns(len(disp))
                            for i, w in enumerate(disp):
                                if cols[i].button(w, key=f"w_{i}_{len(current)}", use_container_width=True):
                                    st.session_state.user_ans_list.append(w); st.rerun()
                        
                        bc1, bc2 = st.columns(2)
                        with bc1:
                            if st.button("⬅️ 戻す", use_container_width=True):
                                if st.session_state.user_ans_list: st.session_state.user_ans_list.pop(); st.rerun()
                        with bc2:
                            if st.button("🗑️ クリア", use_container_width=True): st.session_state.user_ans_list = []; st.rerun()
                        
                        if len(current) == len(words):
                            is_ok = " ".join(current).lower() == q['a'].lower()
                            st.session_state.pending_results.append({'q': q['q'], 'is_ok': is_ok, 'rank': q.get('rank','A'), 'subject': q['orig_cat']})
                            st.components.v1.html(f"<script>window.parent.playJudge({str(is_ok).lower()});</script>", height=0)
                            if is_ok: st.session_state.session_streak += 1; st.session_state.session_max_streak = max(st.session_state.session_max_streak, st.session_state.session_streak)
                            else: st.session_state.session_streak = 0
                            st.session_state.last_is_correct = is_ok; time.sleep(0.5); st.session_state.show_result = True; st.rerun()
                    else:
                        if 'cur_opts' not in st.session_state or st.session_state.get('last_q_id') != st.session_state.index:
                            opts = [q['a']] + generate_clever_distractors(q['a'], q['orig_cat'], st.session_state.all_ans_in_set)
                            st.session_state.cur_opts = random.sample(list(set(opts)), len(list(set(opts)))); st.session_state.last_q_id = st.session_state.index
                        
                        # 4択ボタンを1行にする
                        o_cols = st.columns(len(st.session_state.cur_opts))
                        for i, opt in enumerate(st.session_state.cur_opts):
                            with o_cols[i]:
                                if st.button(format_math_text(opt), key=f"o_{i}", use_container_width=True):
                                    is_ok = (str(opt).lower() == str(q['a']).lower())
                                    st.session_state.pending_results.append({'q': q['q'], 'is_ok': is_ok, 'rank': q.get('rank','A'), 'subject': q['orig_cat']})
                                    st.components.v1.html(f"<script>window.parent.playJudge({str(is_ok).lower()});</script>", height=0)
                                    if is_ok: st.session_state.session_streak += 1; st.session_state.session_max_streak = max(st.session_state.session_max_streak, st.session_state.session_streak)
                                    else: st.session_state.session_streak = 0
                                    st.session_state.last_is_correct = is_ok; time.sleep(0.5); st.session_state.show_result = True; st.rerun()
