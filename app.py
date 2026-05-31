from flask import Flask, request, jsonify, send_file
import edge_tts
import asyncio
import os
import re
import requests
from difflib import SequenceMatcher

app = Flask(__name__)

# KOPYALADIĞIN GROQ API ANAHTARINI AŞAĞIDAKİ TIRNAKLARIN İÇİNE YAPIŞTIR
GROQ_API_KEY = os.environ.get("GROQ_API_KEY")
WHISPER_HATALARI = {
    "ألام": "ألم", "الام": "ألم", "إلام": "ألم", "لام": "ألم", 
    "لايلاف": "لإيلاف", "ليلاف": "لإيلاف", "ايلافهم": "إيلافهم", "وامنهم": "وآمنهم",
    "كفواً": "كفوا", "كفؤا": "كفوا", "احد": "أحد", "النفاثات": "النفاثات", "غاسق": "غاسق"
}

def metni_temizle(metin):
    for yanlis, dogru in WHISPER_HATALARI.items():
        metin = metin.replace(yanlis, dogru)
    metin = re.sub(r'[\u0670]', '', metin)
    metin = re.sub(r'[أإآٱ]', 'ا', metin)
    metin = re.sub(r'[\u0617-\u061A\u064B-\u0652\u06D6-\u06ED]', '', metin)
    metin = re.sub(r'[.,!؟،؛"\'_]', '', metin)
    return metin.strip()

def girtlak_toleransi(kelime):
    degisimler = {'ح':'ه', 'خ':'ه', 'ع':'ا', 'ص':'س', 'ض':'د', 'ط':'ت', 'ظ':'ز', 'ث':'س', 'ذ':'ز', 'ق':'ك'}
    for zor, kolay in degisimler.items():
        kelime = kelime.replace(zor, kolay)
    return kelime

def besmele_filtresi(okunan_kelimeler, sure_id):
    if sure_id == "1":
        return okunan_kelimeler
    besmele_kaliplari = [["بسم", "الله", "الرحمن", "الرحيم"], ["بسم", "الله", "الرحمان", "الرحيم"]]
    if len(okunan_kelimeler) >= 4:
        ilk_dort = okunan_kelimeler[:4]
        if ilk_dort in besmele_kaliplari:
            return okunan_kelimeler[4:] 
    return okunan_kelimeler

async def erkek_sesi_olustur(metin, dosya_adi):
    iletisim = edge_tts.Communicate(metin, "ar-SA-HamedNeural")
    await iletisim.save(dosya_adi)

@app.route('/')
def index():
    return "Hafızlık Asistanı Canlı Bulut API Sunucusu Aktif!"

@app.route('/process_audio', methods=['POST'])
def process_audio():
    try:
        audio_file = request.files['audio']
        audio_path = "temp_audio.wav"
        audio_file.save(audio_path)
        
        sure_id = request.form['sure_id']
        sure_adi = request.form['sure_adi']
        ayet_no = request.form['ayet_no']
        
        api_url = f"http://api.alquran.cloud/v1/surah/{sure_id}/quran-simple"
        response = requests.get(api_url).json()
        beklenen_ayet = response['data']['ayahs'][int(ayet_no)-1]['text']
        beklenen_kelimeler = metni_temizle(beklenen_ayet).split()
        
        # --- BULUT YAPAY ZEKASI (GROQ WHISPER LARGE) ---
        with open(audio_path, "rb") as f:
            headers = {"Authorization": f"Bearer {GROQ_API_KEY}"}
            files = {
                "file": (os.path.basename(audio_path), f, "audio/wav"),
                "model": (None, "whisper-large-v3"),
                "language": (None, "ar"),
                "prompt": (None, f"قرآن كريم، سورة {sure_adi}.")
            }
            groq_response = requests.post(
                "https://api.groq.com/openai/v1/audio/transcriptions", 
                headers=headers, 
                files=files
            ).json()
            
        ham_okunan = groq_response.get('text', '')
        okunan_kelimeler = metni_temizle(ham_okunan).split()
        okunan_kelimeler = besmele_filtresi(okunan_kelimeler, sure_id)
        
        # KATI VE KUSURSUZ HAFIZLIK DENETİMİ (%85)
        KELIME_HASSASIYETI = 0.85 
        hata_var = False
        mesaj = ""
        hatali_kelime = ""

        if len(okunan_kelimeler) < (len(beklenen_kelimeler) - 1):
            hata_var = True
            mesaj = "Eksik okudun veya erken sustun!"
            hatali_kelime = beklenen_kelimeler[len(okunan_kelimeler)] if len(okunan_kelimeler) < len(beklenen_kelimeler) else beklenen_kelimeler[-1]
        else:
            for i, b_kelime in enumerate(beklenen_kelimeler):
                if i >= len(okunan_kelimeler):
                    hata_var = True
                    hatali_kelime = b_kelime
                    mesaj = "Şu kelimeyi atladın."
                    break
                
                o_kelime = okunan_kelimeler[i]
                if SequenceMatcher(None, girtlak_toleransi(o_kelime), girtlak_toleransi(b_kelime)).ratio() < KELIME_HASSASIYETI:
                    hata_var = True
                    hatali_kelime = b_kelime
                    mesaj = "Yanlış harf veya kelime!"
                    break

        if not hata_var:
            return jsonify({"durum": "basarili", "okunan": ham_okunan})
        else:
            asyncio.run(erkek_sesi_olustur(hatali_kelime, "duzeltme.mp3"))
            return jsonify({"durum": "hatali", "mesaj": mesaj, "okunan": ham_okunan, "beklenen": hatali_kelime})
            
    except Exception as e:
        return jsonify({"durum": "hatali", "mesaj": f"Sunucu Hatası: {str(e)}", "okunan": "", "beklenen": ""})

@app.route('/get_audio')
def get_audio():
    return send_file("duzeltme.mp3", mimetype="audio/mp3")

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)