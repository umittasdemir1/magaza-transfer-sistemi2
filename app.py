from flask import Flask, request, jsonify, send_file
from flask_cors import CORS
import pandas as pd
import os
import io
import json
from datetime import datetime
from werkzeug.utils import secure_filename
import tempfile
import logging

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# ===== TEK CORS KONFIGÜRASYONU (ÇİFTE HEADER ÖNLEMİ) =====
CORS(app, 
     origins=["*"],
     methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
     allow_headers=["Content-Type", "Authorization"],
     supports_credentials=False
)

# Configuration
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB max file size
ALLOWED_EXTENSIONS = {'xlsx', 'xls', 'csv'}

class MagazaTransferSistemi:
    def __init__(self):
        self.data = None
        self.magazalar = []
        self.mevcut_analiz = None

    def dosya_yukle_df(self, df):
        """DataFrame'i yükle ve işle - ORIJINAL KOD"""
        try:
            # Sütun isimlerini temizle
            df.columns = df.columns.str.strip()
            
            logger.info(f"Bulunan sütunlar: {list(df.columns)}")
            
            gerekli_sutunlar = ['Depo Adı', 'Ürün Kodu', 'Ürün Adı', 'Satis', 'Envanter']
            eksik_sutunlar = [s for s in gerekli_sutunlar if s not in df.columns]
            
            if eksik_sutunlar:
                return False, f"Eksik sütunlar: {', '.join(eksik_sutunlar)}"
            
            df = df.dropna(subset=['Depo Adı'])
            df['Satis'] = pd.to_numeric(df['Satis'], errors='coerce').fillna(0)
            df['Envanter'] = pd.to_numeric(df['Envanter'], errors='coerce').fillna(0)
            
            # Negatif değerleri sıfırla
            df['Satis'] = df['Satis'].clip(lower=0)
            df['Envanter'] = df['Envanter'].clip(lower=0)
            
            self.data = df
            self.magazalar = df['Depo Adı'].unique().tolist()
            
            logger.info(f"Veri yüklendi: {len(df)} satır, {len(self.magazalar)} mağaza")
            
            return True, {
                'message': f"Başarılı! {len(df):,} ürün, {len(self.magazalar)} mağaza yüklendi.",
                'satir_sayisi': len(df),
                'magaza_sayisi': len(self.magazalar),
                'magazalar': self.magazalar,
                'sutunlar': list(df.columns)
            }
            
        except Exception as e:
            logger.error(f"Dosya yükleme hatası: {str(e)}")
            return False, f"Hata: {str(e)}"

    def magaza_metrikleri_hesapla(self):
        """Her mağaza için metrikleri hesapla - ORIJINAL KOD"""
        if self.data is None:
            return {}

        metrikler = {}
        for magaza in self.magazalar:
            magaza_data = self.data[self.data['Depo Adı'] == magaza]
            toplam_satis = magaza_data['Satis'].sum()
            toplam_envanter = magaza_data['Envanter'].sum()

            metrikler[magaza] = {
                'toplam_satis': int(toplam_satis),
                'toplam_envanter': int(toplam_envanter),
                'satis_orani': float(toplam_satis / (toplam_satis + toplam_envanter)) if (toplam_satis + toplam_envanter) > 0 else 0,
                'envanter_fazlasi': int(toplam_envanter - toplam_satis),
                'urun_sayisi': len(magaza_data)
            }
        return metrikler

    def urun_anahtari_olustur(self, urun_adi, renk, beden):
        """Ürün adı + renk + beden kombinasyonu ile benzersiz anahtar oluştur - ORIJINAL KOD"""
        urun_adi = str(urun_adi).strip().upper() if pd.notna(urun_adi) else ""
        renk = str(renk).strip().upper() if pd.notna(renk) else ""
        beden = str(beden).strip().upper() if pd.notna(beden) else ""
        return f"{urun_adi} {renk} {beden}".strip()

    def str_hesapla(self, satis, envanter):
        """Sell-Through Rate hesapla - ORIJINAL KOD"""
        toplam = satis + envanter
        if toplam == 0:
            return 0
        return satis / toplam

    def str_bazli_transfer_hesapla(self, gonderen_satis, gonderen_envanter, alan_satis, alan_envanter):
        """STR bazlı transfer miktarı hesapla - ORIJINAL KOD"""
        gonderen_str = self.str_hesapla(gonderen_satis, gonderen_envanter)
        alan_str = self.str_hesapla(alan_satis, alan_envanter)
        str_farki = alan_str - gonderen_str
        teorik_transfer = str_farki * gonderen_envanter
        
        # Koruma filtreleri
        max_transfer_40 = gonderen_envanter * 0.40
        min_kalan_2 = gonderen_envanter - 2
        max_5_adet = 5
        
        transfer_miktari = min(teorik_transfer, max_transfer_40, min_kalan_2, max_5_adet)
        transfer_miktari = max(1, min(transfer_miktari, gonderen_envanter))
        
        return int(transfer_miktari), {
            'gonderen_str': round(gonderen_str * 100, 1),
            'alan_str': round(alan_str * 100, 1),
            'str_farki': round(str_farki * 100, 1),
            'teorik_transfer': round(teorik_transfer, 1),
            'uygulanan_filtre': 'Max %40' if transfer_miktari == max_transfer_40 else 
                               'Min 2 kalsın' if transfer_miktari == min_kalan_2 else
                               'Max 5 adet' if transfer_miktari == max_5_adet else 'Teorik'
        }

    def transfer_kosulları_kontrol(self, gonderen_satis, gonderen_envanter, alan_satis, alan_envanter):
        """STR bazlı transfer koşulları kontrol - ORIJINAL KOD"""
        if alan_satis <= gonderen_satis:
            return False, f"Alan satış ({alan_satis}) ≤ Gönderen satış ({gonderen_satis})"
        
        if gonderen_envanter < 3:
            return False, f"Gönderen envanter yetersiz ({gonderen_envanter} < 3)"
        
        gonderen_str = self.str_hesapla(gonderen_satis, gonderen_envanter)
        alan_str = self.str_hesapla(alan_satis, alan_envanter)
        str_farki = alan_str - gonderen_str
        
        if str_farki < 0.15:
            return False, f"STR farkı yetersiz ({str_farki*100:.1f}% < 15%)"
        
        transfer_miktari, detaylar = self.str_bazli_transfer_hesapla(
            gonderen_satis, gonderen_envanter, alan_satis, alan_envanter
        )
        
        if transfer_miktari <= 0:
            return False, "Transfer miktarı hesaplanamadı"
        
        return True, f"STR: A{detaylar['alan_str']}%>G{detaylar['gonderen_str']}%, T:{transfer_miktari}"

    def global_transfer_analizi_yap(self):
        """Global ürün bazlı transfer analizi - ORIJINAL KOD"""
        if self.data is None:
            return None

        logger.info("Global ürün bazlı STR transfer analizi başlatılıyor...")
        
        metrikler = self.magaza_metrikleri_hesapla()
        transferler = []
        transfer_gereksiz = []

        # TÜM mağazaların ürünlerini grupla (ürün adı + renk + beden)
        tum_data = self.data.copy()
        tum_data['urun_anahtari'] = tum_data.apply(
            lambda x: self.urun_anahtari_olustur(
                x['Ürün Adı'], 
                x.get('Renk Açıklaması', ''), 
                x.get('Beden', '')
            ), axis=1
        )
        
        # Tüm benzersiz ürün anahtarlarını al
        tum_urun_anahtarlari = tum_data['urun_anahtari'].unique()
        
        logger.info(f"Toplam {len(tum_urun_anahtarlari)} benzersiz ürün grubu analiz ediliyor...")

        # Her ürün anahtarı için global optimizasyon
        for index, urun_anahtari in enumerate(tum_urun_anahtarlari):
            if (index + 1) % 100 == 0:
                logger.info(f"İşlenen: {index + 1}/{len(tum_urun_anahtarlari)}")

            # Bu ürünün tüm mağazalardaki durumunu analiz et
            urun_data = tum_data[tum_data['urun_anahtari'] == urun_anahtari]
            
            # Mağaza bazında grupla
            magaza_gruplari = urun_data.groupby('Depo Adı').agg({
                'Satis': 'sum',
                'Envanter': 'sum',
                'Ürün Adı': 'first',
                'Renk Açıklaması': 'first',
                'Beden': 'first',
                'Ürün Kodu': 'first'
            }).reset_index()

            # En az 2 mağazada olmalı transfer için
            if len(magaza_gruplari) < 2:
                continue

            # Her mağaza için STR hesapla
            magaza_str_listesi = []
            for _, magaza_grup in magaza_gruplari.iterrows():
                magaza = magaza_grup['Depo Adı']
                satis = magaza_grup['Satis']
                envanter = magaza_grup['Envanter']
                str_value = self.str_hesapla(satis, envanter)
                
                magaza_str_listesi.append({
                    'magaza': magaza,
                    'satis': satis,
                    'envanter': envanter,
                    'str': str_value,
                    'urun_adi': magaza_grup['Ürün Adı'],
                    'renk': magaza_grup.get('Renk Açıklaması', ''),
                    'beden': magaza_grup.get('Beden', ''),
                    'urun_kodu': magaza_grup['Ürün Kodu']
                })

            # STR'a göre sırala (düşükten yükseğe)
            magaza_str_listesi.sort(key=lambda x: x['str'])
            
            # En düşük ve en yüksek STR'ı al
            en_dusuk_str = magaza_str_listesi[0]
            en_yuksek_str = magaza_str_listesi[-1]

            # Transfer koşullarını kontrol et
            kosul_sonuc, kosul_mesaj = self.transfer_kosulları_kontrol(
                en_dusuk_str['satis'], en_dusuk_str['envanter'], 
                en_yuksek_str['satis'], en_yuksek_str['envanter']
            )
            
            if kosul_sonuc:
                # STR bazlı transfer miktarını hesapla
                transfer_miktari, str_detaylar = self.str_bazli_transfer_hesapla(
                    en_dusuk_str['satis'], en_dusuk_str['envanter'],
                    en_yuksek_str['satis'], en_yuksek_str['envanter']
                )
                
                if transfer_miktari > 0:
                    # Stok durumu STR bazında
                    alan_str_val = str_detaylar['alan_str']
                    if alan_str_val >= 80:
                        stok_durumu = 'YÜKSEK'
                    elif alan_str_val >= 50:
                        stok_durumu = 'NORMAL'
                    elif alan_str_val >= 20:
                        stok_durumu = 'DÜŞÜK'
                    else:
                        stok_durumu = 'KRİTİK'
                    
                    transferler.append({
                        'urun_anahtari': urun_anahtari,
                        'urun_kodu': en_dusuk_str['urun_kodu'],
                        'urun_adi': en_dusuk_str['urun_adi'],
                        'renk': en_dusuk_str['renk'],
                        'beden': en_dusuk_str['beden'],
                        'gonderen_magaza': en_dusuk_str['magaza'],
                        'alan_magaza': en_yuksek_str['magaza'],
                        'transfer_miktari': int(transfer_miktari),
                        'gonderen_satis': int(en_dusuk_str['satis']),
                        'gonderen_envanter': int(en_dusuk_str['envanter']),
                        'alan_satis': int(en_yuksek_str['satis']),
                        'alan_envanter': int(en_yuksek_str['envanter']),
                        'gonderen_str': str_detaylar['gonderen_str'],
                        'alan_str': str_detaylar['alan_str'],
                        'str_farki': str_detaylar['str_farki'],
                        'teorik_transfer': str_detaylar['teorik_transfer'],
                        'uygulanan_filtre': str_detaylar['uygulanan_filtre'],
                        'alan_stok_durumu': stok_durumu,
                        'magaza_sayisi': len(magaza_str_listesi),
                        'min_str': round(en_dusuk_str['str'] * 100, 1),
                        'max_str': round(en_yuksek_str['str'] * 100, 1),
                        'satis_farki': int(en_yuksek_str['satis'] - en_dusuk_str['satis']),
                        'envanter_farki': int(en_dusuk_str['envanter'] - en_yuksek_str['envanter'])
                    })
            else:
                # Transfer gerekmeyen ürünleri kaydet
                str_ortalama = sum(m['str'] for m in magaza_str_listesi) / len(magaza_str_listesi)
                str_fark = max(m['str'] for m in magaza_str_listesi) - min(m['str'] for m in magaza_str_listesi)
                
                transfer_gereksiz.append({
                    'urun_anahtari': urun_anahtari,
                    'urun_adi': magaza_str_listesi[0]['urun_adi'],
                    'renk': magaza_str_listesi[0]['renk'],
                    'beden': magaza_str_listesi[0]['beden'],
                    'magaza_sayisi': len(magaza_str_listesi),
                    'ortalama_str': round(str_ortalama * 100, 1),
                    'str_fark': round(str_fark * 100, 1),
                    'red_nedeni': kosul_mesaj
                })

        # STR farkına göre sırala (yüksek fark = daha öncelikli)
        transferler.sort(key=lambda x: x['str_farki'], reverse=True)

        logger.info(f"Global analiz tamamlandı: {len(transferler)} transfer, {len(transfer_gereksiz)} red")

        return {
            'analiz_tipi': 'global',
            'magaza_metrikleri': metrikler,
            'transferler': transferler,
            'transfer_gereksiz': transfer_gereksiz
        }

# Global sistem instance
sistem = MagazaTransferSistemi()

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@app.route('/', methods=['GET'])
def health_check():
    """Health check endpoint"""
    try:
        return jsonify({
            'status': 'healthy',
            'service': 'RetailFlow Transfer API - MINIMAL CORS',
            'version': '4.2.0',
            'timestamp': datetime.now().isoformat()
        })
    except Exception as e:
        logger.error(f"Health check error: {str(e)}")
        return jsonify({'error': str(e)}), 500

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
            return jsonify({'error': 'Geçersiz dosya formatı! Excel (.xlsx, .xls) veya CSV dosyası yükleyin.'}), 400
        
        filename = secure_filename(file.filename)
        
        # Excel veya CSV oku
        if filename.lower().endswith('.csv'):
            try:
                df = pd.read_csv(file, encoding='utf-8')
            except UnicodeDecodeError:
                df = pd.read_csv(file, encoding='cp1254')
        else:
            df = pd.read_excel(file, engine='openpyxl' if filename.endswith('.xlsx') else None)
        
        # Sisteme yükle
        success, result = sistem.dosya_yukle_df(df)
        
        if success:
            return jsonify({
                'success': True,
                'filename': filename,
                'data': result
            })
        else:
            return jsonify({'error': result}), 400
            
    except Exception as e:
        logger.error(f"Upload error: {str(e)}")
        return jsonify({'error': f'Dosya yükleme hatası: {str(e)}'}), 500

@app.route('/analyze', methods=['POST'])
def analyze_data():
    """Global STR transfer analizi"""
    try:
        if sistem.data is None:
            return jsonify({'error': 'Önce bir dosya yükleyin'}), 400
        
        logger.info("Global STR transfer analizi başlatılıyor...")
        
        results = sistem.global_transfer_analizi_yap()
        
        if results:
            sistem.mevcut_analiz = results
            
            # İlk 50 transfer önerisi
            limited_results = {
                'analiz_tipi': results['analiz_tipi'],
                'magaza_metrikleri': results['magaza_metrikleri'],
                'transferler': results['transferler'][:50],
                'transfer_gereksiz': results['transfer_gereksiz'][:20],
                'toplam_transfer_sayisi': len(results['transferler']),
                'toplam_gereksiz_sayisi': len(results['transfer_gereksiz'])
            }
            
            return jsonify({
                'success': True,
                'results': limited_results
            })
        else:
            return jsonify({'error': 'Analiz başarısız'}), 500
            
    except Exception as e:
        logger.error(f"Analysis error: {str(e)}")
        return jsonify({'error': f'Analiz hatası: {str(e)}'}), 500

@app.route('/export/excel', methods=['POST'])
def export_excel():
    """Excel export"""
    try:
        # Request body'den results al
        try:
            request_data = request.get_json()
            if request_data and 'results' in request_data:
                sistem.mevcut_analiz = request_data['results']
        except:
            pass
        
        if not sistem.mevcut_analiz:
            return jsonify({'error': 'Analiz sonucu bulunamadı'}), 400
        
        transferler = sistem.mevcut_analiz['transferler']
        
        # Excel dosyası oluştur
        output = io.BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            if transferler:
                df_transfer = pd.DataFrame(transferler)
                df_transfer.to_excel(writer, index=False, sheet_name='Transfer Önerileri')
        
        output.seek(0)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        filename = f'transfer_analizi_{timestamp}.xlsx'
        
        return send_file(
            output,
            as_attachment=True,
            download_name=filename,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        logger.error(f"Export error: {str(e)}")
        return jsonify({'error': f'Export hatası: {str(e)}'}), 500

@app.route('/template', methods=['GET'])
def download_template():
    """Excel template download"""
    try:
        template_data = {
            'Depo Adı': ['Mağaza A', 'Mağaza A', 'Mağaza B', 'Mağaza B'],
            'Ürün Kodu': ['UR001', 'UR002', 'UR001', 'UR002'],
            'Ürün Adı': ['T-Shirt', 'Pantolon', 'T-Shirt', 'Pantolon'],
            'Satis': [10, 15, 20, 5],
            'Envanter': [30, 25, 10, 40],
            'Renk Açıklaması': ['Kırmızı', 'Mavi', 'Kırmızı', 'Mavi'],
            'Beden': ['M', 'L', 'M', 'L']
        }
        
        df = pd.DataFrame(template_data)
        output = io.BytesIO()
        
        with pd.ExcelWriter(output, engine='openpyxl') as writer:
            df.to_excel(writer, index=False, sheet_name='Veri')
        
        output.seek(0)
        
        return send_file(
            output,
            as_attachment=True,
            download_name='retailflow_template.xlsx',
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
        )
        
    except Exception as e:
        logger.error(f"Template error: {str(e)}")
        return jsonify({'error': f'Template hatası: {str(e)}'}), 500

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    logger.info(f"Starting Flask app on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
