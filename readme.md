# Embege

Embege adalah backend API-only yang memilih satu paket menu MBG dari katalog JSON berdasarkan nama dan deskripsi geografis suatu daerah. LLM hanya memilih `menu_id` dari kandidat aktif dan memberikan alasan; nama, deskripsi, dan ASCII art selalu berasal dari katalog yang tervalidasi.

## Menjalankan dengan Docker Compose

Salin `.env.example` menjadi `.env`, lalu isi sekurangnya `DB_PASSWORD`, `JWT_SECRET_KEY`, `LLM_API_KEY`, dan `LLM_MODEL`. `JWT_SECRET_KEY` harus berupa rahasia acak minimal 32 byte. Jangan commit `.env`.

`app/core/config.py` tidak menyimpan nilai bawaan: seluruh pengaturan wajib
berasal dari environment atau `.env`, dan aplikasi gagal start dengan daftar
field yang hilang bila ada yang belum diisi. `.env.example` memuat semua kunci
yang dibutuhkan.

Nama environment yang dipakai aplikasi adalah `DB_USER`, `DB_PASSWORD`, dan
`DB_NAME`; Compose meneruskannya sebagai `POSTGRES_USER`, `POSTGRES_PASSWORD`,
dan `POSTGRES_DB` pada container database. API memakai nilai yang sama.
Dalam Compose, alamat database adalah `db:5432`; port PostgreSQL lama di host
tidak dipakai untuk koneksi antar-container.

Konfigurasi contoh menggunakan Gemini melalui endpoint OpenAI-compatible:

```dotenv
LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai/
LLM_MODEL=gemini-3.8-flash
LLM_API_KEY=
```

Isi `LLM_API_KEY` dengan Gemini API key Anda sebelum startup. Jangan menyalin
ulang `.env.example` di atas `.env` yang sudah berisi kredensial.
Periksa konfigurasi Compose tanpa menjalankan container dengan
`docker compose config --quiet`; pemeriksaan ini tidak memvalidasi API key LLM.

Perintah berikut disediakan untuk Anda jalankan sendiri:

```bash
docker compose up --build
```

API hanya dipublikasikan pada `127.0.0.1:8000`. PostgreSQL dan Redis tidak dipublikasikan ke host.

Perintah uvicorn untuk service `api` berada di `compose.yaml` (bukan di
`Dockerfile`), sehingga perubahan berikut cukup diatur lewat `.env` tanpa
mengubah image: `API_PORT` (port uvicorn sekaligus port publish dan
healthcheck), `UVICORN_WORKERS`, dan `FORWARDED_ALLOW_IPS` (isi dengan IP proxy
tepercaya bila ada reverse proxy di depan API).

- OpenAPI: <http://127.0.0.1:8000/docs>
- Liveness: <http://127.0.0.1:8000/health>

Seluruh definisi tabel PostgreSQL (`User`, `AuthSession`, dan `Recommendation`) dipusatkan di `app/models.py`. Repository pada setiap modul mengimpor model dari file tersebut. Pada startup, `app/db.py` mendaftarkan seluruh model dan membuat tabel yang belum ada dengan SQLAlchemy `create_all`. Mekanisme ini tidak memigrasikan tabel lama; perubahan struktur tabel harus ditangani manual sebelum deployment berikutnya.

## Contoh REST

Daftarkan pengguna melalui `POST /auth/register`, lalu login:

```bash
curl -sS http://127.0.0.1:8000/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"email":"pengguna@example.com","password":"kata-sandi-rahasia"}'
```

Gunakan `access_token` dari respons untuk meminta rekomendasi:

```bash
curl -sS http://127.0.0.1:8000/recommendations \
  -H 'Content-Type: application/json' \
  -H 'Authorization: Bearer ACCESS_TOKEN' \
  -d '{"region_name":"Pesisir Utara","geographic_description":"Wilayah pesisir dengan kegiatan perikanan dan cuaca hangat."}'
```

`POST /auth/logout` mencabut sesi token aktif. `POST /auth/change-password` mencabut seluruh sesi pengguna, sehingga pengguna perlu login kembali.

## Katalog dan riwayat melalui REST

| Method | Endpoint | Akses | Kegunaan |
| --- | --- | --- | --- |
| `GET` | `/menus` | Publik | Daftar menu aktif dan pencarian |
| `GET` | `/menus/{menu_id}` | Publik | Detail menu aktif |
| `GET` | `/recommendations` | Bearer token | Riwayat rekomendasi pengguna aktif |
| `GET` | `/recommendations/{recommendation_id}` | Bearer token | Detail rekomendasi milik pengguna aktif |
| `POST` | `/recommendations` | Bearer token | Membuat rekomendasi |

Daftar menu menerima `search` (maksimal 150 karakter). Kedua endpoint daftar
menerima `limit` (default 20, maksimal `READ_MAX_PAGE_SIZE`) dan `after` untuk
cursor halaman berikutnya:

```bash
curl -sS --get http://127.0.0.1:8000/menus \
  --data-urlencode 'search=ikan' --data-urlencode 'limit=20'

curl -sS --get http://127.0.0.1:8000/recommendations \
  -H 'Authorization: Bearer ACCESS_TOKEN' --data-urlencode 'limit=20'
```

Respons daftar berbentuk `{"items": [...], "next_cursor": "...", "has_next_page": true}`.
Kirim nilai `next_cursor` sebagai `after` untuk melanjutkan; pertahankan `search`
yang sama untuk katalog. Pada halaman terakhir, `next_cursor` bernilai `null`
dan `has_next_page` bernilai `false`. Katalog diurutkan menurut ID menaik;
riwayat menurut waktu pembuatan lalu ID menurun. Cursor riwayat hanya berlaku
untuk pengguna yang sama.

Detail menu dan elemen `items` katalog memakai field `id`, `name`, `description`,
dan `ascii_art`. Detail rekomendasi dan elemen riwayat memakai bentuk yang sama
dengan hasil `POST /recommendations`, termasuk snapshot menu ketika rekomendasi
dibuat. Menu tidak aktif tidak ditampilkan. Resource yang tidak ditemukan atau
rekomendasi milik pengguna lain mengembalikan HTTP 404; cursor dan parameter
tidak valid mengembalikan HTTP 422. Riwayat tanpa token valid mengembalikan HTTP 401.

Seluruh pembacaan katalog dan riwayat berbagi batas `READ_LIMIT` per
`READ_WINDOW_SECONDS`, dihitung menurut pengguna untuk token valid atau IP
untuk anonim/token tidak valid. Header Authorization yang dikirim pada endpoint
publik juga harus valid. HTTP 429 menyertakan header `Retry-After`.

## Katalog

Katalog berada di `app/data/menus.json` dan berbentuk:

```json
{
  "version": 1,
  "menus": [
    {
      "id": "menu-001",
      "name": "Nama paket",
      "description": "Deskripsi katalog",
      "is_active": true,
      "ascii_art": ["+-------+", "| paket |", "+-------+"]
    }
  ]
}
```

ID harus unik. ASCII art wajib memakai karakter ASCII printable, tanpa tab/newline dalam satu baris, dan semua baris harus sama lebar. Restart aplikasi setelah mengubah katalog karena file hanya dibaca sekali saat startup. Untuk menampilkan gambar, gunakan font monospace dan gabungkan `ascii_art` dengan newline.

## LLM dan batas penggunaan

`LLM_OUTPUT_MODE` disesuaikan dengan kemampuan provider:

- `text`: kompatibilitas terluas; prompt meminta JSON dan backend tetap memvalidasinya secara ketat.
- `json_object`: gunakan bila provider mendukung JSON object mode.
- `json_schema`: gunakan bila provider mendukung structured output berbasis JSON Schema.

Satu pengguna dapat memiliki satu proses rekomendasi aktif. Secara default, rekomendasi dibatasi 10 permintaan per rolling 60 detik dan 100 permintaan per rolling 10.800 detik. Batas endpoint lain dapat diubah melalui variabel pada `.env.example`.

Hasil pilihan menu dan teks katalog tidak menjamin kecukupan gizi, verifikasi ketersediaan bahan, atau pemenuhan standar gizi resmi.

## Struktur ringkas

```text
app/
├── main.py             # lifecycle, health, pemasangan router
├── db.py               # koneksi dan inisialisasi tabel
├── models.py           # model SQLAlchemy terpusat
├── schemas.py          # schema input/output
├── api/
│   ├── auth.py
│   ├── recommendations.py
│   └── menus.py
├── core/
│   ├── config.py
│   ├── security.py
│   ├── errors.py
│   ├── pagination.py
│   └── rate_limit.py
├── services/
│   ├── catalog.py
│   └── llm.py
└── data/
    ├── menus.json
    └── select_menu.txt
```

Setiap fitur API menyatukan endpoint, logika bisnis, dan akses datanya dalam
satu file. `core/` berisi kebutuhan bersama, sedangkan `services/` menangani
katalog dan provider LLM. Tidak ada pemecahan router/service/repository per fitur.
Katalog dan prompt tetap berada di `app/data/`.

Jika memakai `.env` dari struktur sebelumnya, ubah `CATALOG_PATH` dari
`app/db/data/data.json` menjadi `app/data/menus.json`.

## Pengujian

```bash
python -m pip install -e '.[test]'
python -m pytest -q
```

Tes endpoint menggunakan katalog uji, SQLite untuk pembacaan riwayat, dan
pengganti layanan autentikasi serta rate limiter; PostgreSQL, Redis, dan LLM
tidak diperlukan untuk menjalankannya.
