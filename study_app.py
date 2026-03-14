import streamlit as st
import base64
import os
import time
import re
import random
from datetime import datetime
import pandas as pd
import gspread
from google.oauth2.service_account import Credentials

# --- 1. st.set_page_config ---
st.set_page_config(
    page_title="高校受験対策 🛡️ 数式対応・完全統合版", 
    layout="wide", 
    initial_sidebar_state="expanded"
)

from streamlit_drawable_canvas import st_canvas

# --- 2. セッション状態の初期化 ---
def init_session():
    defaults = {
        "questions": [], "index": 0, "mode": None, "diff": "ミックス",
        "show_options": False, "show_result": False, "last_is_correct": False,
        "user_ans_list": [], "retry_count": 0, "session_streak": 0, "correct_count": 0,
        "all_ans_pool": [], "current_opts": [], "results_buffer": [],
        "sound_enabled": True, "play_this": None
    }
    for k, v in defaults.items():
        if k not in st.session_state: st.session_state[k] = v

init_session()

# --- 3. 音声予約システム (安定再生用) ---
def queue_sound(file_name):
    if st.session_state.sound_enabled:
        st.session_state.play_this = file_name

def execute_queued_sound():
    file_name = st.session_state.play_this
    if file_name and os.path.exists(file_name):
        with open(file_name, "rb") as f:
            data = f.read()
            b64 = base64.b64encode(data).decode()
            unique_id = int(time.time() * 1000)
            sound_html = f"""<script>/* {unique_id} */ setTimeout(function() {{ var a = new Audio("data:audio/mp3;base64,{b64}"); a.play(); }}, 800);</script>"""
            st.components.v1.html(sound_html, height=0)
        st.session_state.play_this = None

# --- 4. 判定・解析 ---
def compare_answers(u, c):
    if not u or not c: return False
    def normalize(s):
        s = str(s).lower()
        # LaTeX記号を除去して比較 (判定を甘くするため)
        s = re.sub(r'[\$\{\}\\]', '', s)
        s = re.sub(r'[\s\u3000\t\n\r\xa0]', '', s)
        s = re.sub(r'^[a-z]\s*=\s*', '', s)
        s = re.sub(r'[.\?\!。？！]+$', '', s)
        return s
    return normalize(u) == normalize(c)

def parse_order_question(text):
    match = re.search(r'\((.*?/.*?)\)', str(text))
    if match:
        words = [w.strip() for w in match.group(1).split('/') if w.strip()]
        jp = text.replace(f"({match.group(1)})", "").strip()
        return jp, words
    return text, []

# --- 5. API・データ連携 (A:category, B:rank, C:q, D:a, E:h) ---
def get_creds():
    if "gcp_service_account" in st.secrets:
        return Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"])
    return None

@st.cache_data(ttl=60)
def load_db():
    creds = get_creds()
    if not creds: return {}, {"cat_stats": [], "avg": 0, "reports": []}
    try:
        gc = gspread.authorize(creds); ss = gc.open("study_stats_db")
        q_rows = ss.worksheet("questions").get_all_records()
        organized = {}
        for r in q_rows:
            cat = str(r.get('category', '共通'))
            organized.setdefault(cat, []).append({"q": str(r['q']), "a": str(r['a']), "h": str(r.get('h','')), "rank": str(r.get('rank','-')), "orig_cat": cat})
        h_rows = ss.sheet1.get_all_records()
        cat_agg = {}; t_c = t_a = 0
        for r in h_rows:
            cat = str(r.get('subject', 'その他'))
            c, w = int(r.get('correct', 0)), int(r.get('wrong', 0))
            cat_agg.setdefault(cat, {"c": 0, "t": 0})
            cat_agg[cat]["c"] += c; cat_agg[cat]["t"] += (c + w); t_c += c; t_a += (c + w)
        cat_stats = [{"教科": k, "解答数": f"{v['t']}問", "得点率": f"{int(v['c']/v['t']*100) if v['t']>0 else 0}点"} for k,v in cat_agg.items()]
        last_sub = next((r.get('value') for r in ss.worksheet("summary").get_all_records() if r.get('key')=="last_selected_subject"), "")
        reports = []
        try: reports = ss.worksheet("reports").get_all_records()
        except: pass
        return organized, {"all_ans": [str(r['a']) for r in q_rows], "cat_stats": cat_stats, "avg": int((t_c/t_a*100)) if t_a>0 else 0, "last_sub": last_sub, "reports": reports}
    except: return {}, {"cat_stats": [], "avg": 0}

def update_db_question_master(old_cat, old_q, new_rank, new_q, new_a):
    try:
        sh = gspread.authorize(get_creds()).open("study_stats_db").worksheet("questions")
        records = sh.get_all_records()
        for i, row in enumerate(records):
            if str(row.get('category')) == str(old_cat) and str(row.get('q')) == str(old_q):
                row_num = i + 2
                sh.update_cell(row_num, 2, new_rank); sh.update_cell(row_num, 3, new_q); sh.update_cell(row_num, 4, new_a)
                return True
        return False
    except: return False

def batch_save_to_db():
    if not st.session_state.results_buffer: return
    try:
        sh = gspread.authorize(get_creds()).open("study_stats_db").sheet1
        all_r = sh.get_all_records()
        for res in st.session_state.results_buffer:
            q_t, is_ok, r_v, s_v = res["q"], res["is_correct"], res.get("rank","-"), res.get("subject","共通")
            f_row = -1
            for i, row in enumerate(all_r):
                if str(row.get('q')) == str(q_t): f_row = i + 2; break
            if f_row != -1:
                col = 2 if is_ok else 3
                curr = int(sh.cell(f_row, col).value or 0)
                sh.update_cell(f_row, col, curr + 1)
                sh.update_cell(f_row, 4, r_v); sh.update_cell(f_row, 5, s_v)
            else: sh.append_row([str(q_t), 1 if is_ok else 0, 0 if is_ok else 1, r_v, s_v])
        st.session_state.results_buffer = []; st.cache_data.clear(); st.toast("同期完了！")
    except: st.error("保存失敗")

all_q, db = load_db()

# --- 6. サイドバー ---
with st.sidebar:
    st.title("📊 学習成績表")
    if st.button("🔄 最新に更新", width='stretch'): st.cache_data.clear(); st.rerun()
    st.metric("🎓 総合平均点", f"{db.get('avg', 0)}点")
    if db.get('cat_stats'): st.dataframe(pd.DataFrame(db['cat_stats']), width='stretch', hide_index=True)
    st.divider()
    st.session_state.sound_enabled = st.toggle("🔊 効果音", value=st.session_state.sound_enabled)
    
    if st.session_state.mode:
        if st.button("🏳️ 特訓中止", width='stretch'): st.session_state.clear(); st.rerun()
        idx = st.session_state.index
        if idx < len(st.session_state.questions):
            cur = st.session_state.questions[idx]
            with st.expander("🚨 ミス報告"):
                m = st.text_input("内容", key="rpt_in")
                if st.button("送信", width='stretch'):
                    try:
                        sh_rpt = gspread.authorize(get_creds()).open("study_stats_db").worksheet("reports")
                        sh_rpt.append_row([datetime.now().strftime("%Y/%m/%d %H:%M"), cur['orig_cat'], cur['q'], cur['a'], m])
                        st.toast("報告完了")
                    except: st.error("失敗")

    if st.session_state.results_buffer:
        if st.button("💾 データを保存", width='stretch', type="primary"): batch_save_to_db(); st.rerun()

    for _ in range(10): st.write("")
    st.divider()
    if st.checkbox("👨‍👩‍👧 保護者メニュー", value=False):
        if st.session_state.mode and st.session_state.index < len(st.session_state.questions):
            cur = st.session_state.questions[st.session_state.index]
            nq = st.text_area("問題修正", value=cur['q'], key="fix_q")
            na = st.text_input("正解修正", value=cur['a'], key="fix_a")
            nr = st.text_input("Rank修正", value=cur['rank'], key="fix_r")
            if st.button("✅ DBを書き換える", width='stretch'):
                if update_db_question_master(cur['orig_cat'], cur['q'], nr, nq, na):
                    st.cache_data.clear(); st.success("完了！"); time.sleep(1); st.rerun()
        else: st.info("特訓中に直せます")

# --- 7. メイン画面 ---
if not st.session_state.mode:
    st.title("🛡️ 高校受験対策")
    q_keys = ["数学総合", "英語総合"] + sorted(list(all_q.keys()))
    sub = st.selectbox("セット選択", q_keys, index=q_keys.index(db.get('last_sub')) if db.get('last_sub') in q_keys else 0)
    diff = st.radio("モード", ["ミックス", "🧩 並べ替え特訓", "🔥 苦手克服"], horizontal=True)
    if st.button("🚀 スタート！", width='stretch', type="primary"):
        target = [sub]
        if sub == "数学総合": target = [k for k in all_q.keys() if "数学" in k]
        elif sub == "英語総合": target = [k for k in all_q.keys() if "英語" in k]
        qs = []
        for c in target: qs.extend(all_q.get(c, []))
        if diff == "🧩 並べ替え特訓": qs = [x for x in qs if "/" in str(x['q'])]
        if not qs: st.error("対象なし"); st.stop()
        random.shuffle(qs)
        st.session_state.questions = qs[:30]; st.session_state.all_ans_pool = db.get("all_ans", [])
        st.session_state.mode = sub; st.session_state.index = 0; st.session_state.correct_count = 0; st.session_state.session_streak = 0; st.rerun()
else:
    idx = st.session_state.index; qs = st.session_state.questions
    if idx >= len(qs):
        if st.session_state.results_buffer: batch_save_to_db()
        st.balloons(); st.markdown(f'<div style="font-size:3rem; text-align:center;">スコア: {int((st.session_state.correct_count/len(qs))*100)}点</div>', unsafe_allow_html=True)
        if st.button("TOPへ", width='stretch', type="primary"): st.session_state.clear(); st.rerun()
    else:
        q = qs[idx]; jp_p, order_w = parse_order_question(q['q'])
        st.caption(f"残り {len(qs)-idx} 問 / 30問中　🔥 {st.session_state.session_streak}連勝　Rank: {q['rank']}")
        # ★【数式対応】st.markdownを使うことでLaTeXをレンダリング
        if order_w: st.markdown(f'### ( {" / ".join(order_w)} ) {jp_p}')
        else: st.markdown(f'### {q["q"]}')
        
        st_canvas(stroke_width=9, height=450, width=1200, key=f"cv_{idx}_{st.session_state.retry_count}", background_color="#f8f9fb", update_streamlit=False)
        
        if st.session_state.show_result:
            # ★【数式対応】正解表示もLaTeX対応
            if st.session_state.last_is_correct: st.success(f"✨ 正解！ : {q['a']}")
            else: st.error(f"❌ 正解は **{q['a']}**")
            c1, c2 = st.columns(2)
            with c1:
                if st.button("次へ進む ➡️", width='stretch', type="primary"):
                    st.session_state.index += 1; st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.session_state.current_opts = []; st.session_state.retry_count = 0; st.rerun()
            with c2:
                if st.button("🔄 もう一度", width='stretch'):
                    st.session_state.retry_count += 1; st.session_state.show_result = False; st.session_state.user_ans_list = []; st.rerun()
        elif st.session_state.show_options:
            cor_a = str(q['a'])
            if order_w:
                if not st.session_state.current_opts:
                    o = list(order_w); random.shuffle(o); st.session_state.current_opts = o
                curr = st.session_state.user_ans_list; disp = list(st.session_state.current_opts)
                for w in curr: 
                    if w in disp: disp.remove(w)
                if curr: st.info(" ".join(curr))
                cols = st.columns(min(len(disp)+1, 8))
                for i, w in enumerate(disp):
                    if cols[i % 8].button(w, key=f"w_{idx}_{i}"):
                        st.session_state.user_ans_list.append(w); st.rerun()
                bc1, bc2 = st.columns(2)
                with bc1:
                    if st.button("判定する", width='stretch', type="primary") and curr:
                        ok = compare_answers("".join(curr), cor_a)
                        queue_sound("correct.mp3" if ok else "wrong.mp3")
                        st.session_state.results_buffer.append({"q":q['q'], "is_correct":ok, "rank":q['rank'], "subject":q['orig_cat']})
                        st.session_state.last_is_correct = ok
                        if ok and st.session_state.retry_count == 0: st.session_state.correct_count += 1; st.session_state.session_streak += 1
                        elif not ok: st.session_state.session_streak = 0
                        st.session_state.show_result = True; st.rerun()
                with bc2:
                    if st.button("🗑️ クリア", width='stretch'): st.session_state.user_ans_list = []; st.rerun()
            else:
                # 4択モード
                if not st.session_state.current_opts:
                    others = [a for a in st.session_state.all_ans_pool if str(a).strip() != cor_a.strip()]
                    opts = [cor_a] + random.sample(others, min(len(others), 3))
                    random.shuffle(opts); st.session_state.current_opts = opts
                cols = st.columns(len(st.session_state.current_opts))
                for i, o in enumerate(st.session_state.current_opts):
                    # ★【数式対応】ボタン上のテキストもLaTeX対応させる場合は st.markdown を使う必要があるが、
                    # ボタン自体にLaTeXを入れるのは制限があるため、ここではテキストのまま表示します。
                    if cols[i].button(o, key=f"opt_{idx}_{i}", width='stretch'):
                        ok = compare_answers(o, cor_a)
                        queue_sound("correct.mp3" if ok else "wrong.mp3")
                        st.session_state.results_buffer.append({"q":q['q'], "is_correct":ok, "rank":q['rank'], "subject":q['orig_cat']})
                        st.session_state.last_is_correct = ok
                        if ok and st.session_state.retry_count == 0: st.session_state.correct_count += 1; st.session_state.session_streak += 1
                        elif not ok: st.session_state.session_streak = 0
                        st.session_state.show_result = True; st.rerun()
        else:
            if st.button("判定・選択肢表示", width='stretch', type="primary"): st.session_state.show_options = True; st.rerun()

# --- 8. 音声予約実行 ---
execute_queued_sound()
