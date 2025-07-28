from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import csv
import io
import json
import os
from datetime import datetime
from werkzeug.utils import secure_filename
import tempfile
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, origins=[
    "https://umittasdemir1.github.io",
    "http://localhost:3000",
    "*"
])

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
UPLOAD_FOLDER = tempfile.gettempdir()
ALLOWED_EXTENSIONS = {'csv'}

class MagazaTransferSistemi:
    def __init__(self):
        self.data = []
        self.magazalar = []
        self.mevcut_analiz = None

    def csv_oku(self, file_content):
        """CSV içeriğini oku ve işle"""
        try:
            # CSV'yi satır satır oku
            # Hem virgül hem noktalı virgül desteği
if ';' in file_content.split('\n')[0]:
    csv_reader = csv.DictReader(io.StringIO(file_content), delimiter=';')
else:
    csv_reader = csv.DictReader(io.StringIO(file_content), delimiter=',')
            data = []
            
            for row in csv_reader:
                # Sütun isimlerini temizle
                clean_row = {}
                for key, value in row.items():
                    clean_key = key.strip()
                    clean_row[clean_key] = value
                data.append(clean_row)
            
            logger.info(f"CSV okundu: {len(data)} satır")
            
            # Gerekli sütunları kontrol et
            if not data:
                return False, "CSV dosyası boş!"
            
            first_row = data[0]
            gerekli_sutunlar = ['Depo Adı', 'Ürün Kodu', 'Ürün Adı', 'Satis', 'Envanter']
            eksik_sutunlar = [s for s in gerekli_sutunlar if s not in first_row]
            
            if eksik_sutunlar:
                return False, f"Eksik sütunlar: {', '.join(eksik_sutunlar)}"
            
            # Verileri temizle ve dönüştür
            temiz_data = []
            magazalar_set = set()
            
            for row in data:
                if not row.get('Depo Adı'):
                    continue
                    
                try:
                    satis = float(row.get('Satis', 0) or 0)
                    envanter = float(row.get('Envanter', 0) or 0)
                    
                    # Negatif değerleri sıfırla
                    satis = max(0, satis)
                    envanter = max(0, envanter)
                    
                    temiz_row = {
                        'Depo Adı': row['Depo Adı'].strip(),
                        'Ürün Kodu': row.get('Ürün Kodu', '').strip(),
                        'Ürün Adı': row.get('Ürün Adı', '').strip(),
                        'Renk Açıklaması': row.get('Renk Açıklaması', '').strip(),
                        'Beden': row.get('Beden', '').strip(),
                        'Satis': int(satis),
                        'Envanter': int(envanter)
                    }
                    
                    temiz_data.append(temiz_row)
                    magazalar_set.add(temiz_row['Depo Adı'])
                    
                except ValueError as e:
                    logger.warning(f"Satır atlandı: {row}, Hata: {e}")
                    continue
            
            self.data = temiz_data
            self.magazalar = list(magazalar_set)
            
            logger.info(f"Veri işlendi: {len(temiz_data)} satır, {len(self.magazalar)} mağaza")
            
            return True, {
                'message': f"Başarılı! {len(temiz_data):,} ürün, {len(self.magazalar)} mağaza yüklendi.",
                'satir_sayisi': len(temiz_data),
                'magaza_sayisi': len(self.magazalar),
                'magazalar': self.magazalar,
                'sutunlar': list(first_row.keys()) if data else []
            }
            
        except Exception as e:
            logger.error(f"CSV okuma hatası: {str(e)}")
            return False, f"Hata: {str(e)}"

    def magaza_metrikleri_hesapla(self):
        """Her mağaza için metrikleri hesapla"""
        if not self.data:
            return {}

        metrikler = {}
        for magaza in self.magazalar:
            magaza_data = [row for row in self.data if row['Depo Adı'] == magaza]
            toplam_satis = sum(row['Satis'] for row in magaza_data)
            toplam_envanter = sum(row['Envanter'] for row in magaza_data)

            metrikler[magaza] = {
                'toplam_satis': int(toplam_satis),
                'toplam_envanter': int(toplam_envanter),
                'satis_orani': float(toplam_satis / (toplam_satis + toplam_envanter)) if (toplam_satis + toplam_envanter) > 0 else 0,
                'envanter_fazlasi': int(toplam_envanter - toplam_satis),
                'urun_sayisi': len(magaza_data)
            }
        return metrikler

    def str_hesapla(self, satis, envanter):
        """Sell-Through Rate hesapla"""
        toplam = satis + envanter
        if toplam == 0:
            return 0
        return satis / toplam

    def basit_transfer_analizi(self):
        """Basit transfer analizi"""
        if not self.data:
            return None

        logger.info("Basit transfer analizi başlatılıyor...")
        
        metrikler = self.magaza_metrikleri_hesapla()
        transferler = []
        
        # Ürün bazında analiz
        urun_gruplari = {}
        
        # Ürünleri grupla
        for row in self.data:
            urun_key = f"{row['Ürün Adı']} {row.get('Renk Açıklaması', '')} {row.get('Beden', '')}".strip()
            
            if urun_key not in urun_gruplari:
                urun_gruplari[urun_key] = {}
            
            magaza = row['Depo Adı']
            if magaza not in urun_gruplari[urun_key]:
                urun_gruplari[urun_key][magaza] = {
                    'satis': 0,
                    'envanter': 0,
                    'urun_adi': row['Ürün Adı'],
                    'renk': row.get('Renk Açıklaması', ''),
                    'beden': row.get('Beden', ''),
                    'urun_kodu': row.get('Ürün Kodu', '')
                }
            
            urun_gruplari[urun_key][magaza]['satis'] += row['Satis']
            urun_gruplari[urun_key][magaza]['envanter'] += row['Envanter']
        
        # Her ürün için transfer analizi
        for urun_key, magazalar_data in urun_gruplari.items():
            if len(magazalar_data) < 2:  # En az 2 mağazada olmalı
                continue
            
            # STR hesapla ve sırala
            magaza_str_listesi = []
            for magaza, data in magazalar_data.items():
                str_value = self.str_hesapla(data['satis'], data['envanter'])
                magaza_str_listesi.append({
                    'magaza': magaza,
                    'str': str_value,
                    'satis': data['satis'],
                    'envanter': data['envanter'],
                    'urun_adi': data['urun_adi'],
                    'renk': data['renk'],
                    'beden': data['beden'],
                    'urun_kodu': data['urun_kodu']
                })
            
            # STR'a göre sırala
            magaza_str_listesi.sort(key=lambda x: x['str'])
            
            en_dusuk = magaza_str_listesi[0]
            en_yuksek = magaza_str_listesi[-1]
            
            # Basit transfer koşulları
            str_farki = (en_yuksek['str'] - en_dusuk['str']) * 100
            
            if (en_yuksek['satis'] > en_dusuk['satis'] and 
                en_dusuk['envanter'] >= 3 and 
                str_farki >= 15):
                
                # Transfer miktarı hesapla (basit)
                transfer_miktari = min(
                    int(str_farki / 100 * en_dusuk['envanter']),
                    int(en_dusuk['envanter'] * 0.4),  # Max %40
                    en_dusuk['envanter'] - 2,  # Min 2 kalsın
                    5  # Max 5 adet
                )
                
                transfer_miktari = max(1, transfer_miktari)
                
                transferler.append({
                    'urun_adi': en_dusuk['urun_adi'],
                    'renk': en_dusuk['renk'],
                    'beden': en_dusuk['beden'],
                    'urun_kodu': en_dusuk['urun_kodu'],
                    'gonderen_magaza': en_dusuk['magaza'],
                    'alan_magaza': en_yuksek['magaza'],
                    'transfer_miktari': transfer_miktari,
                    'gonderen_satis': en_dusuk['satis'],
                    'gonderen_envanter': en_dusuk['envanter'],
                    'alan_satis': en_yuksek['satis'],
                    'alan_envanter': en_yuksek['envanter'],
                    'gonderen_str': round(en_dusuk['str'] * 100, 1),
                    'alan_str': round(en_yuksek['str'] * 100, 1),
                    'str_farki': round(str_farki, 1)
                })
        
        # STR farkına göre sırala
        transferler.sort(key=lambda x: x['str_farki'], reverse=True)
        
        logger.info(f"Analiz tamamlandı: {len(transferler)} transfer önerisi")
        
        return {
            'analiz_tipi': 'basit',
            'magaza_metrikleri': metrikler,
            'transferler': transferler
        }

# Global sistem instance
sistem = MagazaTransferSistemi()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        'status': 'healthy',
        'service': 'RetailFlow Transfer API',
        'version': '1.0.0',
        'timestamp': datetime.now().isoformat()
    })

@app.route('/upload', methods=['POST'])
def upload_file():
    """Dosya yükleme endpoint'i"""
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'Dosya seçilmedi'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'error': 'Dosya seçilmedi'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({'error': 'Sadece CSV dosyaları desteklenir'}), 400
        
        # Dosyayı oku
        file_content = file.read().decode('utf-8')
        
        # Sisteme yükle
        success, result = sistem.csv_oku(file_content)
        
        if success:
            return jsonify({
                'success': True,
                'filename': secure_filename(file.filename),
                'data': result
            })
        else:
            return jsonify({'error': result}), 400
            
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': f'Dosya yükleme hatası: {str(e)}'}), 500

@app.route('/analyze', methods=['POST'])
def analyze_data():
    """Transfer analizi endpoint'i"""
    try:
        if not sistem.data:
            return jsonify({'error': 'Önce bir dosya yükleyin'}), 400
        
        logger.info("Transfer analizi başlatılıyor...")
        
        # Analizi çalıştır
        results = sistem.basit_transfer_analizi()
        
        if results:
            sistem.mevcut_analiz = results
            return jsonify({
                'success': True,
                'results': results
            })
        else:
            return jsonify({'error': 'Analiz başarısız'}), 500
            
    except Exception as e:
        logger.error(f"Analysis error: {str(e)}")
        return jsonify({'error': f'Analiz hatası: {str(e)}'}), 500

@app.route('/stores', methods=['GET'])
def get_stores():
    """Mağaza listesi endpoint'i"""
    try:
        if not sistem.magazalar:
            return jsonify({'error': 'Mağaza verisi bulunamadı'}), 400
        
        metrikler = sistem.magaza_metrikleri_hesapla()
        
        stores = []
        for magaza in sistem.magazalar:
            if magaza in metrikler:
                m = metrikler[magaza]
                str_oran = m['satis_orani'] * 100
                stores.append({
                    'name': magaza,
                    'sales': m['toplam_satis'],
                    'inventory': m['toplam_envanter'],
                    'str_rate': round(str_oran, 1),
                    'product_count': m['urun_sayisi'],
                    'excess_inventory': m['envanter_fazlasi']
                })
        
        return jsonify({
            'success': True,
            'stores': stores
        })
        
    except Exception as e:
        logger.error(f"Stores error: {str(e)}")
        return jsonify({'error': f'Mağaza verisi hatası: {str(e)}'}), 500

@app.route('/template', methods=['GET'])
def download_template():
    """CSV template indirme endpoint'i"""
    try:
        # Örnek CSV template oluştur
        template_data = [
            ['Depo Adı', 'Ürün Kodu', 'Ürün Adı', 'Renk Açıklaması', 'Beden', 'Satis', 'Envanter'],
            ['İstanbul AVM', 'P001', 'Gömlek', 'Beyaz', 'M', '15', '25'],
            ['Ankara Merkez', 'P001', 'Gömlek', 'Beyaz', 'M', '8', '40'],
            ['İzmir Plaza', 'P002', 'Pantolon', 'Siyah', 'L', '12', '18']
        ]
        
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerows(template_data)
        
        output.seek(0)
        
        return send_file(
            io.BytesIO(output.getvalue().encode('utf-8')),
            as_attachment=True,
            download_name='magaza_transfer_template.csv',
            mimetype='text/csv'
        )
        
    except Exception as e:
        logger.error(f"Template error: {str(e)}")
        return jsonify({'error': f'Template hatası: {str(e)}'}), 500

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'Dosya boyutu çok büyük (Max: 50MB)'}), 413

@app.errorhandler(500)
def internal_error(e):
    return jsonify({'error': 'İç sunucu hatası'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
