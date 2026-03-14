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
        s = re.sub(r'[\$\{\}\\]', '', s)
        s = re.sub(r'[\s\u3000\t\n\r\xa0]', '', s)
        s = re.sub(r'^[a-z]\s*=\s*', '', s)
        s = re.sub(r'[.,\?\!。？！\'\"、，]', '', s)
        return s
    return normalize(u) == normalize(c)

def parse_order_question(text, category):
    en_part = text
    jp_part = ""
    if '英語' in str(category):
        match_jp_en = re.search(r'([。？！、，])\s*([A-Za-z\(])', text)
        if match_jp_en:
            split_idx = match_jp_en.start(1) + 1
            jp_part = text[:split_idx].strip()
            en_part = text[split_idx:].strip()

    words = []
    display_text = en_part
    match = re.search(r'\((.*?/.*?)\)', en_part)
    if match:
        words = [w.strip() for w in match.group(1).split('/') if w.strip()]
        display_text = en_part.replace(f"({match.group(1)})", "{ANS}").strip()
    
    return display_text, jp_part, words

# --- 5. API・データ連携 ---
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
        h_rows = ss.sheet1.get_all_records()
        cat_agg = {}; t_c = t_a = 0; q_history = {}
        for r in h_rows:
            q_text = str(r.get('q', ''))
            cat = str(r.get('subject', 'その他'))
            c, w = int(r.get('correct', 0)), int(r.get('wrong', 0))
            q_history[q_text] = {"c": c, "w": w}
            cat_agg.setdefault(cat, {"c": 0, "t": 0})
            cat_agg[cat]["c"] += c; cat_agg[cat]["t"] += (c + w); t_c += c; t_a += (c + w)
            
        q_rows = ss.worksheet("questions").get_all_records()
        organized = {}
        cat_ans_pool = {}
        for r in q_rows:
            cat = str(r.get('category', '共通'))
            q_text = str(r['q']); a_text = str(r['a'])
            c_count = q_history.get(q_text, {}).get("c", 0)
            w_count = q_history.get(q_text, {}).get("w", 0)
            cat_ans_pool.setdefault(cat, []).append(a_text)
            organized.setdefault(cat, []).append({
                "q": q_text, "a": a_text, "h": str(r.get('h','')), "rank": str(r.get('rank','-')), 
                "orig_cat": cat, "c_count": c_count, "w_count": w_count, "dummy": str(r.get('dummy', ''))
            })
        cat_stats = [{"教科": k, "解答数": f"{v['t']}問", "得点率": f"{int(v['c']/v['t']*100) if v['t']>0 else 0}点"} for k,v in cat_agg.items()]
        last_sub = next((r.get('value') for r in ss.worksheet("summary").get_all_records() if r.get('key')=="last_selected_subject"), "")
        reports = []
        try: reports = ss.worksheet("reports").get_all_records()
        except: pass
        return organized, {"all_ans": [str(r['a']) for r in q_rows], "cat_ans_pool": cat_ans_pool, "cat_stats": cat_stats, "avg": int((t_c/t_a*100)) if t_a>0 else 0, "last_sub": last_sub, "reports": reports}
    except: return {}, {"cat_stats": [], "avg": 0}

def update_db_question_master(old_cat, old_q, new_rank, new_q, new_a, new_dummy):
    try:
        sh = gspread.authorize(get_creds()).open("study_stats_db").worksheet("questions")
        records = sh.get_all_records()
        for i, row in enumerate(records):
            if str(row.get('category')) == str(old_cat) and str(row.get('q')) == str(old_q):
                row_num = i + 2
                sh.update_cell(row_num, 2, new_rank)
                sh.update_cell(row_num, 3, new_q)
                sh.update_cell(row_num, 4, new_a)
                sh.update_cell(row_num, 6, new_dummy) # F列(6列目)を更新
                return True
        return False
    except: return False

def delete_db_question(target_cat, target_q):
    try:
        sh = gspread.authorize(get_creds()).open("study_stats_db").worksheet("questions")
        records = sh.get_all_records()
        for i, row in enumerate(records):
            if str(row.get('category')) == str(target_cat) and str(row.get('q')) == str(target_q):
                row_num = i + 2; sh.delete_rows(row_num); return True, "成功"
        return False, "一致なし"
    except Exception as e: return False, str(e)

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
                curr = int(sh.cell(f_row, col).value or 0); sh.update_cell(f_row, col, curr + 1)
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
                m = st.text_input("内容", key=f"rpt_in_{idx}")
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
            idx = st.session_state.index; cur = st.session_state.questions[idx]
            nq = st.text_area("問題修正", value=cur['q'], key=f"fix_q_{idx}")
            na = st.text_input("正解修正", value=cur['a'], key=f"fix_a_{idx}")
            nr = st.text_input("Rank修正", value=cur['rank'], key=f"fix_r_{idx}")
            nd = st.text_input("ダミー選択肢(カンマ区切り)", value=cur.get('dummy',''), key=f"fix_d_{idx}")
            
            c1, c2 = st.columns(2)
            with c1:
                if st.button("✅ 上書き", width='stretch'):
                    if update_db_question_master(cur['orig_cat'], cur['q'], nr, nq, na, nd):
                        # ★メモリ上のデータも同時に書き換え（これで即適用されます）
                        st.session_state.questions[idx]['q'] = nq
                        st.session_state.questions[idx]['a'] = na
                        st.session_state.questions[idx]['rank'] = nr
                        st.session_state.questions[idx]['dummy'] = nd
                        st.session_state.current_opts = [] # 選択肢を再生成させるためにリセット
                        st.cache_data.clear(); st.success("完了！"); time.sleep(0.5); st.rerun()
            with c2:
                if st.button("🗑️ 削除", width='stretch'):
                    success, msg = delete_db_question(cur['orig_cat'], cur['q'])
                    if success:
                        st.cache_data.clear(); st.success("削除成功！"); time.sleep(0.5)
                        st.session_state.questions.pop(st.session_state.index)
                        st.session_state.show_result = False; st.session_state.show_options = False; st.session_state.user_ans_list = []; st.session_state.current_opts = []; st.rerun()
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
        qs_candidates = []
        for c in target: qs_candidates.extend(all_q.get(c, []))
        if diff == "🧩 並べ替え特訓": qs_candidates = [x for x in qs_candidates if "/" in str(x['q'])]
        active_qs = [q for q in qs_candidates if (q.get('c_count', 0) - q.get('w_count', 0)) < 5]
        if not active_qs: st.error("対象の問題がありません"); st.stop()
        rank_ab = [q for q in active_qs if str(q.get('rank', '')).upper() in ['A', 'B']]
        rank_c  = [q for q in active_qs if str(q.get('rank', '')).upper() == 'C']
        rank_others = [q for q in active_qs if str(q.get('rank', '')).upper() not in ['A', 'B', 'C']]
        random.shuffle(rank_ab); random.shuffle(rank_c); random.shuffle(rank_others)
        total_to_select = min(30, len(active_qs))
        count_c = min(len(rank_c), int(total_to_select * 0.1))
        selected_qs = rank_c[:count_c]; rem_pool = rank_ab + rank_others + rank_c[count_c:]; random.shuffle(rem_pool)
        selected_qs.extend(rem_pool[:(total_to_select - count_c)]); random.shuffle(selected_qs)
        st.session_state.questions = selected_qs; st.session_state.all_ans_pool = db.get("all_ans", [])
        st.session_state.mode = sub; st.session_state.index = 0; st.session_state.correct_count = 0; st.session_state.session_streak = 0; st.rerun()
else:
    idx = st.session_state.index; qs = st.session_state.questions
    if idx >= len(qs):
        if st.session_state.results_buffer: batch_save_to_db()
        st.balloons(); st.markdown(f'<div style="font-size:3rem; text-align:center;">スコア: {int((st.session_state.correct_count/len(qs))*100)}点</div>', unsafe_allow_html=True)
        if st.button("TOPへ", width='stretch', type="primary"): st.session_state.clear(); st.rerun()
    else:
        q = qs[idx]; en_display, jp_part, order_w = parse_order_question(q['q'], q['orig_cat'])
        c_cnt = q.get('c_count', 0); w_cnt = q.get('w_count', 0); score = c_cnt - w_cnt; total_ans = c_cnt + w_cnt
        rank_str = str(q['rank']).upper()
        if rank_str == 'A': disp_rank = "⭐ 基本"
        elif rank_str == 'B': disp_rank = "⭐⭐ 標準"
        elif rank_str == 'C': disp_rank = "⭐⭐⭐ 発展"
        else: disp_rank = f"未設定 ({q['rank']})"
        st.caption(f"残り {len(qs)-idx} 問 / {len(qs)}問中　🔥 {st.session_state.session_streak}連勝　|　難易度: {disp_rank}　|　挑戦: {total_ans}回 (⭕️ {c_cnt} / ❌ {w_cnt} / スコア: {score})")
        if order_w: 
            st.markdown(f'<div style="font-size: 1.5rem; color: #4a4a4a; padding: 10px 0; margin-bottom: 10px;">( {" / ".join(order_w)} )</div>', unsafe_allow_html=True)
            if jp_part: st.markdown(f'<h5 style="color: #666;">{jp_part}</h5>', unsafe_allow_html=True)
        else: 
            if jp_part: st.markdown(f'### {en_display}'); st.markdown(f'<h5 style="color: #666;">{jp_part}</h5>', unsafe_allow_html=True)
            else: st.markdown(f'### {en_display}')
        st_canvas(stroke_width=9, height=450, width=1200, key=f"cv_{idx}_{st.session_state.retry_count}", background_color="#f8f9fb", update_streamlit=False)
        if st.session_state.show_result:
            if st.session_state.last_is_correct: 
                if st.session_state.retry_count == 0: st.success(f"✨ 正解！ : {q['a']}")
                else: st.success(f"✅ 復習クリア！ : {q['a']} （※成績には反映されません）")
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
                ans_text = " ".join(curr) if curr else "＿＿＿＿＿"
                full_ans_text = en_display.replace("{ANS}", f"<span style='color: #ff4b4b; text-decoration: underline;'>{ans_text}</span>") if "{ANS}" in en_display else ans_text
                st.markdown(f'<div style="font-size: 2.2rem; font-weight: bold; padding: 15px; background-color: #f0f8ff; color: #0056b3; border-radius: 8px; text-align: center; margin-bottom: 15px; border: 2px dashed #b8daff;">{full_ans_text}</div>', unsafe_allow_html=True)
                cols = st.columns(min(len(disp)+1, 8))
                for i, w in enumerate(disp):
                    if cols[i % 8].button(w, key=f"w_{idx}_{i}"): st.session_state.user_ans_list.append(w); st.rerun()
                bc1, bc2 = st.columns(2)
                with bc1:
                    if st.button("判定する", width='stretch', type="primary") and curr:
                        ok = compare_answers("".join(curr), cor_a); queue_sound("correct.mp3" if ok else "wrong.mp3")
                        if st.session_state.retry_count == 0: st.session_state.results_buffer.append({"q":q['q'], "is_correct":ok, "rank":q['rank'], "subject":q['orig_cat']})
                        st.session_state.last_is_correct = ok
                        if ok and st.session_state.retry_count == 0: st.session_state.correct_count += 1; st.session_state.session_streak += 1
                        elif not ok: st.session_state.session_streak = 0
                        st.session_state.show_result = True; st.rerun()
                with bc2:
                    if st.button("🗑️ クリア", width='stretch'): st.session_state.user_ans_list = []; st.rerun()
            else:
                if not st.session_state.current_opts:
                    dummy_str = str(q.get('dummy', '')).strip(); opts = [cor_a]
                    if dummy_str and dummy_str != 'nan':
                        dummies = [d.strip() for d in dummy_str.split(',') if d.strip()]; dummies = [d for d in dummies if d != cor_a]; opts.extend(dummies[:3])
                    if len(opts) < 4:
                        same_cat_ans = db.get("cat_ans_pool", {}).get(q['orig_cat'], []); others = [a for a in same_cat_ans if str(a).strip() not in opts]
                        if len(others) < (4 - len(opts)): all_ans = db.get("all_ans", []); others.extend([a for a in all_ans if str(a).strip() not in opts and a not in others])
                        opts.extend(random.sample(others, min(len(others), 4 - len(opts))))
                    random.shuffle(opts); st.session_state.current_opts = opts
                cols = st.columns(len(st.session_state.current_opts))
                for i, o in enumerate(st.session_state.current_opts):
                    if cols[i].button(o, key=f"opt_{idx}_{i}", width='stretch'):
                        ok = compare_answers(o, cor_a); queue_sound("correct.mp3" if ok else "wrong.mp3")
                        if st.session_state.retry_count == 0: st.session_state.results_buffer.append({"q":q['q'], "is_correct":ok, "rank":q['rank'], "subject":q['orig_cat']})
                        st.session_state.last_is_correct = ok
                        if ok and st.session_state.retry_count == 0: st.session_state.correct_count += 1; st.session_state.session_streak += 1
                        elif not ok: st.session_state.session_streak = 0
                        st.session_state.show_result = True; st.rerun()
        else:
            if st.button("判定・選択肢表示", width='stretch', type="primary"): st.session_state.show_options = True; st.rerun()

# --- 8. 音声予約実行 ---
execute_queued_sound()
