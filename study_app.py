import streamlit as st
from streamlit_drawable_canvas import st_canvas
import json, os, random, time, base64, re, io
from datetime import datetime
import gspread
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build # ★ Drive API用に追加
from googleapiclient.http import MediaIoBaseUpload # ★ アップロード用に追加
from PIL import Image

# --- 1. 基本設定・API連携 ---
def get_creds():
    if "gcp_service_account" in st.secrets:
        scope = ["https://www.googleapis.com/auth/spreadsheets", 
                 "https://www.googleapis.com/auth/drive"]
        return Credentials.from_service_account_info(st.secrets["gcp_service_account"], scopes=scope)
    return None

# ★ 改良：Googleドライブへ画像をアップロードする関数
def upload_image_to_drive(img_data, filename):
    creds = get_creds()
    if not creds: return
    try:
        service = build('drive', 'v3', credentials=creds)
        # メモリ上の画像データを準備
        img = Image.fromarray(img_data.astype('uint8'), 'RGBA')
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        buf.seek(0)
        
        file_metadata = {'name': filename}
        media = MediaIoBaseUpload(buf, mimetype='image/png', resumable=True)
        service.files().create(body=file_metadata, media_body=media, fields='id').execute()
    except Exception as e:
        st.error(f"ドライブ保存エラー: {e}")

# (中略：load_questions_from_gsheet, sync_results_to_gsheet 等は Ver.165 を維持)

def sync_results_to_gsheet():
    if not st.session_state.pending_results: return
    client = gspread.authorize(get_creds())
    # ... (成績保存ロジックは Ver.165 と同じ) ...
