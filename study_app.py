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

# ★ 改良：データを一括同期するメインエンジン
def sync_results_to_gsheet():
    if not st.session_state.pending_results: return
    
    client = get_gsheet_client()
    if not client: return

    with st.spinner('学習記録をまとめて保存中...'):
        try:
            ss = client.open("study_stats_db")
            # 1. 成績シートの一括更新
            sheet = ss.sheet1
            rows = sheet.get_all_records()
            
            # 現在のシート内容を辞書化して高速化
            current_data = {str(r['q']): r for r in rows}
            
            for res in st.session_state.pending_results:
                q_text = res['q']
                if q_text in current_data:
                    current_data[q_text]['correct'] = int(current_data[q_text].get('correct', 0)) + (1 if res['is_ok'] else 0)
                    current_data[q_text]['wrong'] = int(current_data[q_text].get('wrong', 0)) + (0 if res['is_ok'] else 1)
                else:
                    current_data[q_text] = {
                        'q': q_text, 
                        'correct': 1 if res['is_ok'] else 0, 
                        'wrong': 0 if res['is_ok'] else 1, 
                        'rank': res['rank'], 
                        'subject': res['subject']
                    }
            
            # 一括書き戻し（ヘッダー + データ）
            final_rows = [['q', 'correct', 'wrong', 'rank', 'subject']]
            for k, v in current_data.items():
                final_rows.append([v['q'], v['correct'], v['wrong'], v['rank'], v['subject']])
            
            sheet.update('A1', final_rows)
            
            # 2. 歴代記録の更新
            if st.session_state.session_max_streak > st.session_state.all_time_max:
                sum_sh = ss.worksheet("summary")
                sum_sh.update_cell(2, 2, st.session_state.session_max_streak)
            
            # 送信完了したらリストを空にする
            st.session_state.pending_results = []
            st.cache_data.clear() # 成績表示を更新するためにキャッシュクリア
        except Exception as e:
            st.error(f"保存に失敗しました: {e}")

def load_stats_only():
    client = get_gsheet_client()
    if not client: return {"history": {}, "all_time_max": 0}
    try:
        ss = client.open("study_stats_db")
        hist_data = ss.sheet1.get_all_records()
        history = {str(row['q']): {"correct": int(row['correct'] or 0), "wrong": int(row['wrong'] or 0), "subject": str(row.get('subject', '不明'))} for row in hist_data if row.get('q')}
        
        all_time_max = 0
        try:
            val = ss.worksheet("summary").cell(2, 2).value
            all_time_max = int(val) if val else 0
        except: pass
        return {"history": history, "all_time_max": all_time_max}
    except: return {"history": {}, "all_time_max": 0}

# (中略: update_question_in_gsheet, setup_audio_engine, parse_q_display などは維持)

def update_question_in_gsheet(old_q, new_q, new_a):
    client = get_gsheet_client()
    if client:
        try:
            sh = client.open("study_stats_db").worksheet("questions")
            cell = sh.find(old_q)
            if cell:
                sh.update_cell(cell.row, 3, new_q); sh.update_cell(cell.row, 4, new_a); return True
        except: pass
    return False

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

# セッション管理
for k, v in {"user_ans_list": [], "show_options": False, "show_result": False, "index": 0, "session_streak": 0, "session_max_streak": 0, "pending_results": []}.items():
    if k not in st.session_state: st.session_state[k] = v

st.set_page_config(page_title="70点マスター", layout="centered")
setup_audio_engine()

# --- 2. サイドバー ---
with st.sidebar:
    st.title("📊 学習状況")
    if st.button("🔄 最新データに更新", use_container_width=True):
        st.cache_data.clear(); st.session_state.clear(); st.rerun()

    stats = load_stats_only()
    st.session_state.all_time_max = stats["all_time_max"]

    if 'mode' in st.session_state:
        st.success(f"🔥 現在: {st.session_state.session_streak} 連勝")
        st.warning(f"👑 歴代最高: {max(st.session_state.all_time_max, st.session_state.session_max_streak)} 連勝")
        st.divider()

    hist = stats["history"]
    if hist:
        subjects = sorted(list(set(v.get("subject", "不明") for v in hist.values())))
        for sub in subjects:
            sub_qs = [v for v in hist.values() if v.get("subject") == sub]
            total_t = sum(v["correct"] + v["wrong"] for v in sub_qs); total_c = sum(v["correct"] for v in sub_qs)
            rate = int(total_c / total_t * 100) if total_t > 0 else 0
            st.write(f"**{sub}**: {rate}点 ({total_t}回)"); st.progress(rate / 100)
    
    st.write("<br>" * 15, unsafe_allow_html=True)
    st.divider()
    if st.checkbox("⚙️ メンテナンスモード", value=False):
        if 'mode' in st.session_state and st.session_state.index < len(st.session_state.questions):
            idx = st.session_state.index; cur_q = st.session_state.questions[idx]
            with st.expander("🛠️ 今の問題を修正", expanded=True):
                new_q = st.text_input("問題修正", value=cur_q['q'], key=f"inst_q_{idx}")
                new_a = st.text_input("正解修正", value=cur_q['a'], key=f"inst_a_{idx}")
                if st.button("保存して反映", key=f"btn_inst_{idx}"):
                    if update_question_in_gsheet(cur_q['q'], new_q, new_a):
                        st.session_state.questions[idx]['q'] = new_q; st.session_state.questions[idx]['a'] = new_a
                        st.cache_data.clear(); st.success("反映完了！"); time.sleep(0.5); st.rerun()

# --- 3. メイン画面 ---
if 'mode' not in st.session_state:
    st.title("🛡️ 70点奪取特訓")
    all_q = load_questions_from_gsheet()
    if all_q:
        sub = st.selectbox("特訓セットを選択", list(all_q.keys()))
        diff_opts = ["ミックス", "🔥 苦手克服"]; 
        if "数学" not in sub: diff_opts.insert(1, "🧩 並べ替え特訓")
        diff = st.radio("モード選択", diff_opts, horizontal=True)
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
            st.session_state.index, st.session_state.session_streak, st.session_state.session_max_streak, st.session_state.pending_results = 0, 0, 0, []
            st.session_state.show_result = False; st.rerun()
else:
    total_q = len(st.session_state.questions)
    if st.session_state.index >= total_q:
        # ★ 特訓終了時にまとめて保存
        sync_results_to_gsheet()
        st.balloons(); st.success("特訓終了！全ての記録を保存しました。")
        if st.button("TOPへ戻る"): st.session_state.clear(); st.rerun()
    else:
        q = st.session_state.questions[st.session_state.index]; is_order_q = "/" in q['q']
        if st.session_state.show_result:
            if st.session_state.last_is_correct:
                st.success("⭕ 正解！"); time.sleep(0.8)
                st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
            else:
                st.error(f"❌ 正解は: {q['a']}")
                if st.button("次へ ➡️"):
                    st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.rerun()
        else:
            st.write(f"残り {total_q - st.session_state.index} 問")
            main_p, hint_p = parse_q_display(q['q']); st.subheader(main_p)
            if hint_p: st.info(f"💡 {hint_p}")
            canvas_res = st_canvas(stroke_width=9, height=180, width=700, key=f"c_{st.session_state.index}")
            c1, c2 = st.columns(2)
            with c1:
                if not st.session_state.show_options:
                    if st.button("🔍 判定して選択肢を表示", use_container_width=True):
                        if not is_order_q and (not canvas_res.json_data or len(canvas_res.json_data.get("objects", [])) == 0): st.warning("手書きして！")
                        else: st.session_state.show_options = True; st.rerun()
            with c2:
                if st.button("🏳️ 中止して保存", use_container_width=True):
                    sync_results_to_gsheet() # 中止のときもそこまでの分を保存
                    st.session_state.clear(); st.rerun()

            if st.session_state.show_options:
                st.divider()
                # 判定ロジック（中身はVer.150と同じですが、save_statの代わりにリストへ追加）
                if is_order_q:
                    # (並べ替え判定)
                    words = [w.strip() for w in main_p.replace("(","").replace(")","").replace("?","").replace(".","").split("/") if w.strip()]
                    current = st.session_state.user_ans_list; disp = [w for w in words if w not in current]
                    if disp:
                        cols = st.columns(min(len(disp), 5))
                        for i, w in enumerate(disp):
                            if cols[i % 5].button(w, key=f"w_{i}", use_container_width=True): st.session_state.user_ans_list.append(w); st.rerun()
                    if len(current) == len(words):
                        is_ok = " ".join(current).lower() == q['a'].lower()
                        st.session_state.pending_results.append({'q': q['q'], 'is_ok': is_ok, 'rank': q.get('rank','A'), 'subject': st.session_state.mode})
                        if is_ok:
                            st.session_state.session_streak += 1
                            st.session_state.session_max_streak = max(st.session_state.session_max_streak, st.session_state.session_streak)
                        else: st.session_state.session_streak = 0
                        st.session_state.last_is_correct = is_ok; time.sleep(0.5); st.session_state.show_result = True; st.rerun()
                else:
                    # (選択肢判定)
                    if 'cur_opts' not in st.session_state or st.session_state.get('last_q_id') != st.session_state.index:
                        opts = [q['a']] + random.sample([a for a in st.session_state.all_ans_in_set if a != q['a']], min(3, len(st.session_state.all_ans_in_set)-1))
                        st.session_state.cur_opts = random.sample(opts, len(opts)); st.session_state.last_q_id = st.session_state.index
                    cols = st.columns(len(st.session_state.cur_opts))
                    for i, opt in enumerate(st.session_state.cur_opts):
                        if cols[i].button(opt, key=f"o_{i}", use_container_width=True):
                            is_ok = (opt.lower() == str(q['a']).lower())
                            st.session_state.pending_results.append({'q': q['q'], 'is_ok': is_ok, 'rank': q.get('rank','A'), 'subject': st.session_state.mode})
                            if is_ok:
                                st.session_state.session_streak += 1
                                st.session_state.session_max_streak = max(st.session_state.session_max_streak, st.session_state.session_streak)
                            else: st.session_state.session_streak = 0
                            st.session_state.last_is_correct = is_ok; time.sleep(0.5); st.session_state.show_result = True; st.rerun()
