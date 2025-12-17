import streamlit as st
import easyocr
import pandas as pd
import numpy as np
import cv2
from PIL import Image
import gspread
import time
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
    # 1. Load Gambar
    pil_image = Image.open(image_file).convert("RGB")
    img_array = np.array(pil_image)
    
    # Buat salinan gambar untuk digambar kotak-kotak (Debug Visual)
    debug_image = img_array.copy()
    
    # 2. Baca SEMUA teks dan posisinya
    # Format result: [ [[x1,y1],[x2,y2]..], "teks", confidence ]
    results = reader.readtext(img_array)
    
    active_columns = []
    captured_data = []

    # --- TAHAP A: CARI HEADER (JANGKAR) ---
    for (bbox, text, prob) in results:
        clean_text = text.lower().strip()
        
        # Cek apakah teks ini adalah Header yang dicari?
        if any(h in clean_text for h in TARGET_HEADERS):
            # Ambil koordinat
            (tl, tr, br, bl) = bbox
            x_min = min(tl[0], bl[0])
            x_max = max(tr[0], br[0])
            y_max = max(tr[1], br[1]) # Bagian bawah header
            
            # Simpan area kolom ini
            active_columns.append({
                'x_min': x_min,
                'x_max': x_max,
                'y_start': y_max,
                'text': text
            })
            
            # GAMBAR KOTAK HIJAU DI HEADER (Visualisasi)
            cv2.rectangle(debug_image, (int(tl[0]), int(tl[1])), (int(br[0]), int(br[1])), (0, 255, 0), 3)
            cv2.putText(debug_image, "HEADER", (int(tl[0]), int(tl[1]-10)), 
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    # Jika tidak ada header, stop
    if not active_columns:
        return [], debug_image, "Tidak ditemukan tulisan Lot/Roll/Batch sebagai patokan."

    # --- TAHAP B: CARI DATA DI BAWAH HEADER ---
    for (bbox, text, prob) in results:
        # Hitung titik tengah teks ini
        (tl, tr, br, bl) = bbox
        data_x_center = (tl[0] + tr[0]) / 2
        data_y_top = min(tl[1], tr[1])
        
        # Cek ke setiap kolom yang aktif
        for col in active_columns:
            # Syarat 1: Posisi HARUS di bawah header
            if data_y_top > col['y_start']:
                
                # Syarat 2: Posisi HARUS sejajar vertikal (dengan toleransi margin melebar dikit)
                margin = 100 # Pixel toleransi kiri-kanan
                if (col['x_min'] - margin) < data_x_center < (col['x_max'] + margin):
                    
                    # Syarat 3: Filter Sampah
                    # Bukan header itu sendiri & panjang karakter masuk akal
                    if text not in [c['text'] for c in active_columns] and len(text) > 3:
                        
                        # Filter Kata Terlarang (Blacklist) agar 'Total'/'Weight' tidak masuk
                        blacklist = ["TOTAL", "WEIGHT", "KG", "MM", "DATE", "QTY", "NET"]
                        if not any(b in text.upper() for b in blacklist):
                            
                            captured_data.append(text)
                            
                            # GAMBAR KOTAK BIRU DI DATA (Visualisasi)
                            cv2.rectangle(debug_image, (int(tl[0]), int(tl[1])), (int(br[0]), int(br[1])), (255, 0, 0), 2)

    return captured_data, debug_image, "OK"

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
