import streamlit as st
import easyocr
import pandas as pd
import numpy as np
import cv2
from PIL import Image
import gspread
import time
import re
from oauth2client.service_account import ServiceAccountCredentials

# ==========================================
# 1. SETUP & KONFIGURASI
# ==========================================

st.set_page_config(page_title="Auto Lot Scanner (Spatial)", page_icon="📐")

# Load EasyOCR (Cache agar cepat, tidak load berulang-ulang)
@st.cache_resource
def load_reader():
    return easyocr.Reader(['en'])

reader = load_reader()

# Daftar kata kunci Header yang menandakan kolom Lot
# Jika di surat jalan tulisannya "Batch No", "Roll ID", "Lot Number", ini akan tertangkap.
TARGET_HEADERS = ["lot", "roll", "batch", "no.", "number", "id", "code", "rolls"]

# --- MASUKKAN ID SPREADSHEET ANDA DI SINI ---
# Ganti string di bawah ini dengan ID yang Anda copy dari URL
SPREADSHEET_ID = "16jhVRIPt_hWMqgtXbH_7DwjDKYEY0WIwXBgTz9-qrPg" 

# --- FUNGSI KONEKSI (DENGAN CACHE) ---
# @st.cache_resource artinya: "Simpan hasil koneksi di memori"
# Jadi aplikasi tidak perlu login ulang setiap kali tombol ditekan.
@st.cache_resource
def get_google_sheet_connection():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    #creds = ServiceAccountCredentials.from_json_keyfile_name("credentials.json", scope)
    creds_dict = dict(st.secrets["gcp_service_account"])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    
    # BUKA LANGSUNG PAKAI ID (JALUR VIP)
    # Ini jauh lebih cepat daripada mencari nama file
    sheet = client.open_by_key(SPREADSHEET_ID).sheet1
    return sheet

# --- FUNGSI KIRIM (VERSI SUPER CEPAT) ---
def save_to_google_sheets(dataframe, sheet_name=None):
    # Parameter sheet_name tidak lagi dipakai karena kita pakai ID
    try:
        # Panggil koneksi yang sudah dicache
        sheet = get_google_sheet_connection()
        
        # Konversi Data
        data_to_send = dataframe.values.tolist()
        
        # Kirim (Bulk Upload)
        sheet.append_rows(data_to_send)
        
        return True, "Sukses"
        
    except FileNotFoundError:
        return False, "File 'credentials.json' hilang."
    except Exception as e:
        return False, str(e)
        
# ==========================================
# 2. CORE LOGIC: SPATIAL OCR (ANCHOR & DROP)
# ==========================================

def process_image_spatial(image_file):
    # 1. Load & Resize Gambar (Agar Cepat)
    pil_image = Image.open(image_file).convert("RGB")
    img_array = np.array(pil_image)
    
    height, width = img_array.shape[:2]
    max_width = 1200
    if width > max_width:
        scale_ratio = max_width / width
        new_height = int(height * scale_ratio)
        img_array = cv2.resize(img_array, (max_width, new_height), interpolation=cv2.INTER_AREA)
    
    debug_image = img_array.copy()
    
    # 2. Baca Teks
    results = reader.readtext(img_array)
    
    active_columns = []
    captured_data = []
    
    # --- STRATEGI 1: SPATIAL (Cari Header Kolom) ---
    # Cocok untuk supplier yang punya tabel rapi (Lot No, Batch, dll)
    target_headers = ["lot", "roll", "batch", "no.", "number", "id", "code"]
    
    for (bbox, text, prob) in results:
        clean_text = text.lower().strip()
        if any(h in clean_text for h in target_headers):
            (tl, tr, br, bl) = bbox
            active_columns.append({
                'x_min': min(tl[0], bl[0]),
                'x_max': max(tr[0], br[0]),
                'y_start': max(tr[1], br[1]), # Bagian bawah header
                'text': text
            })
            # Visualisasi Header (Hijau)
            cv2.rectangle(debug_image, (int(tl[0]), int(tl[1])), (int(br[0]), int(br[1])), (0, 255, 0), 3)

    # --- STRATEGI 2: REGEX SPESIFIK (Untuk SP8N & Format Bandel) ---
    # Format SP8N: 13 digit angka, kadang ada huruf A di belakang (3082504120078A)
    # Kita cari ini DI MANA SAJA (tidak peduli kolom)
    regex_sp8n = re.compile(r'\b\d{13}[A-Z]?\b')

    for (bbox, text, prob) in results:
        (tl, tr, br, bl) = bbox
        
        # Bersihkan teks untuk pengecekan
        clean_val = text.replace(" ", "").strip().upper()
        is_captured = False
        
        # CEK 1: Apakah ini format SP8N (13 digit)?
        if regex_sp8n.search(clean_val):
            captured_data.append(text)
            is_captured = True
            # Visualisasi Data SP8N (Ungu - Biar beda)
            cv2.rectangle(debug_image, (int(tl[0]), int(tl[1])), (int(br[0]), int(br[1])), (255, 0, 255), 3)
            
        # CEK 2: Jika bukan SP8N, apakah dia ada di bawah kolom "Lot"?
        elif active_columns: 
            data_x_center = (tl[0] + tr[0]) / 2
            data_y_top = min(tl[1], tr[1])
            
            for col in active_columns:
                if data_y_top > col['y_start']:
                    # Margin toleransi
                    margin = 60 
                    if (col['x_min'] - margin) < data_x_center < (col['x_max'] + margin):
                        # Filter sampah
                        if text not in [c['text'] for c in active_columns] and len(text) > 2:
                            blacklist = ["TOTAL", "WEIGHT", "KG", "MM", "DATE", "QTY", "NET", "ROLLS", "MIC"]
                            if not any(b in clean_val for b in blacklist):
                                captured_data.append(text)
                                is_captured = True
                                # Visualisasi Data Kolom (Biru)
                                cv2.rectangle(debug_image, (int(tl[0]), int(tl[1])), (int(br[0]), int(br[1])), (255, 0, 0), 2)
                                break # Sudah ketemu kolomnya, lanjut teks berikutnya

    # --- FINAL CHECK ---
    # Hapus duplikat jika ada data yang terbaca double
    captured_data = list(dict.fromkeys(captured_data))

    if captured_data:
        return captured_data, debug_image, "OK"
    else:
        return [], debug_image, "Tidak ditemukan data Lot (Kolom) maupun 13 Digit."
        
# ==========================================
# 3. USER INTERFACE (STREAMLIT)
# ==========================================

st.title("⚡ Scan Lot: One Click")
st.markdown("Mode Cepat: Sekali klik, foto discan dan data langsung masuk Google Sheet.")

# --- FUNGSI RESET ---
def reset_state():
    # Hapus memori lama saat ganti file
    if 'hasil_scan' in st.session_state:
        del st.session_state['hasil_scan']
    if 'img_debug' in st.session_state:
        del st.session_state['img_debug']

# Layout
col_upload, col_preview = st.columns([1, 1.5])

with col_upload:
    st.subheader("1. Upload & Aksi")
    
    uploaded_file = st.file_uploader(
        "Pilih Foto Surat Jalan", 
        type=['jpg', 'png', 'jpeg'],
        on_change=reset_state 
    )
    
    # Input nama sheet (bisa disembunyikan/dihardcode jika mau lebih simpel)
    # sheet_name = "Data Lot Scan" 
    
    if uploaded_file is not None:
        st.markdown("---")
        
        # --- TOMBOL AJAIB (ONE CLICK ACTION) ---
        if st.button("🚀 Scan & Kirim Otomatis", type="primary", width='stretch'):
            
            # 1. PROSES SCAN
            with st.status("Sedang memproses...", expanded=True) as status:
                st.write("🔍 Membaca gambar...")
                data_hasil, img_debug, msg_scan = process_image_spatial(uploaded_file)
                
                if msg_scan == "OK" and data_hasil:
                    st.write("✅ Scan berhasil! Ditemukan " + str(len(data_hasil)) + " data.")
                    
                    # Simpan ke memori untuk preview
                    st.session_state['hasil_scan'] = data_hasil
                    st.session_state['img_debug'] = img_debug
                    
                    # 2. PROSES KIRIM LANGSUNG
                    st.write("☁️ Mengirim ke Google Sheets...")
                    
                    # Ubah list ke dataframe dulu
                    df_to_send = pd.DataFrame(data_hasil, columns=["Nomor Lot"])
                    
                    # Kirim!
                    ok, msg_kirim = save_to_google_sheets(df_to_send)
                    
                    if ok:
                        status.update(label="Selesai! Data tersimpan.", state="complete", expanded=False)
                        st.success(f"Sukses! {len(data_hasil)} lot telah masuk ke Google Sheets.")
                        st.balloons()
                    else:
                        status.update(label="Gagal Kirim", state="error")
                        st.error(f"Scan OK, tapi Gagal Kirim: {msg_kirim}")
                else:
                    status.update(label="Gagal Scan", state="error")
                    st.error(f"Gagal Scan: {msg_scan}")

with col_preview:
    st.subheader("2. Preview")
    
    # Logika Preview
    if 'img_debug' in st.session_state and uploaded_file is not None:
        st.image(st.session_state['img_debug'], caption="Visualisasi Bukti Scan", width='stretch')
    elif uploaded_file is not None:
        st.image(uploaded_file, caption="Foto Asli", width='stretch')
    else:
        st.info("Upload foto dulu.")

# Tampilkan Tabel History di Bawah (Hanya untuk memastikan)
if 'hasil_scan' in st.session_state and uploaded_file is not None:
    st.divider()
    with st.expander("Lihat Data yang Baru Saja Dikirim (History)"):
        st.dataframe(pd.DataFrame(st.session_state['hasil_scan'], columns=["Nomor Lot"]), width='stretch')
