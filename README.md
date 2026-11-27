# embege

Backend REST API untuk katalog menu dan rekomendasi berdasarkan konteks daerah.
Menggunakan FastAPI, PostgreSQL, Redis, dan provider LLM yang kompatibel dengan OpenAI.

## Minimum Environment Requirements

Salin `.env.example` menjadi `.env`, lalu isi:

| Variabel | Kebutuhan |
| --- | --- |
| `DB_PASSWORD` | Password PostgreSQL |
| `JWT_SECRET_KEY` | Rahasia acak minimal 32 byte |
| `LLM_API_KEY` | API key provider LLM |
| `LLM_MODEL` | Nama model yang digunakan |
| `LLM_BASE_URL` | URL API provider yang kompatibel dengan OpenAI |

Pertahankan pengaturan lainnya dari `.env.example`. Jangan commit `.env`.

## Docker Compose

Jalankan setelah `.env` terisi:

```bash
docker compose up -d --build --wait
```

| Service | Fungsi | Akses |
| --- | --- | --- |
| `api` | REST API embege | `http://127.0.0.1:8000`, mengikuti `API_PORT` |
| `db` | PostgreSQL 17 untuk pengguna, sesi, dan rekomendasi | Internal Compose |
| `redis` | Rate limit dan lock rekomendasi | Internal Compose |

Perintah pengelolaan service:

```bash
docker compose ps
docker compose logs -f api
docker compose stop
```

`docker compose stop` menghentikan seluruh service proyek embege dan mempertahankan
container serta volume datanya. Untuk menjalankan kembali dengan kode atau
konfigurasi terbaru, gunakan perintah `up` di atas.

- OpenAPI: <http://127.0.0.1:8000/docs>
- Liveness: <http://127.0.0.1:8000/health>

## Endpoint REST API

Base URL lokal adalah `http://127.0.0.1:8000` sesuai `API_PORT` pada `.env`.
Dokumentasi interaktif tersedia di `/docs`, dan spesifikasi OpenAPI di `/openapi.json`.

| Method | Endpoint | Akses | Status sukses | Kegunaan |
| --- | --- | --- | --- | --- |
| `GET` | `/health` | Publik | `200 OK` | Memeriksa liveness API |
| `POST` | `/auth/register` | Publik | `201 Created` | Mendaftarkan pengguna |
| `POST` | `/auth/login` | Publik | `200 OK` | Mendapatkan access token |
| `GET` | `/auth/me` | Bearer token | `200 OK` | Melihat profil pengguna aktif |
| `POST` | `/auth/change-password` | Bearer token | `200 OK` | Mengubah password dan mencabut seluruh sesi |
| `POST` | `/auth/logout` | Bearer token | `200 OK` | Mencabut sesi token aktif |
| `GET` | `/menus` | Publik | `200 OK` | Mencari dan menampilkan menu aktif |
| `GET` | `/menus/{menu_id}` | Publik | `200 OK` | Melihat detail menu aktif |
| `POST` | `/recommendations` | Bearer token | `201 Created` | Membuat rekomendasi |
| `GET` | `/recommendations` | Bearer token | `200 OK` | Melihat riwayat milik pengguna aktif |
| `GET` | `/recommendations/{recommendation_id}` | Bearer token | `200 OK` | Melihat detail rekomendasi milik pengguna aktif |

## Contoh request dan response

Siapkan base URL untuk contoh `curl` berikut:

```bash
BASE_URL='http://127.0.0.1:8000'
```

Kirim body JSON dengan header `Content-Type: application/json`. Setelah login,
ganti `ACCESS_TOKEN` dengan nilai `access_token` dari respons login:

```bash
ACCESS_TOKEN='TOKEN_DARI_RESPONS_LOGIN'
```

Nilai UUID, waktu, token, dan alasan pemilihan di bawah adalah ilustrasi. Gunakan
ID dari respons API Anda untuk request berikutnya. Isi menu pada contoh hanya
ilustrasi struktur; gunakan ID dan isi dari katalog aktif. Hasil rekomendasi
bergantung pada pilihan LLM dan katalog aktif.

### GET /health

Request:

```bash
curl -sS "$BASE_URL/health"
```

Response `200 OK`:

```json
{
  "status": "ok"
}
```

### POST /auth/register

Email wajib valid dan maksimal 255 karakter. Password pendaftaran harus berisi
12–128 karakter. Email dinormalisasi menjadi huruf kecil tanpa spasi di awal/akhir.

Request:

```bash
curl -sS -X POST "$BASE_URL/auth/register" \
  -H 'Content-Type: application/json' \
  -d '{"email":"pengguna@example.com","password":"kata-sandi-rahasia"}'
```

Response `201 Created`:

```json
{
  "id": "4fdb1497-f1b6-4407-b765-2dfe56a765f1",
  "email": "pengguna@example.com",
  "created_at": "2026-11-27T03:00:00Z",
  "updated_at": "2026-11-27T03:00:00Z"
}
```

Pendaftaran tidak membuat sesi login. Email yang sudah terdaftar mengembalikan
`409 Conflict` dengan kode `EMAIL_ALREADY_EXISTS`.

### POST /auth/login

Request:

```bash
curl -sS -X POST "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d '{"email":"pengguna@example.com","password":"kata-sandi-rahasia"}'
```

Response `200 OK`:

```json
{
  "access_token": "TOKEN_JWT_DARI_SERVER",
  "token_type": "bearer",
  "expires_in": 1800
}
```

`expires_in` adalah masa berlaku token dalam detik, mengikuti
`ACCESS_TOKEN_EXPIRE_MINUTES` (contoh konfigurasi: 30 menit). Email atau password
yang salah mengembalikan `401 Unauthorized` dengan kode `INVALID_CREDENTIALS`.

### GET /auth/me

Request:

```bash
curl -sS "$BASE_URL/auth/me" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

Response `200 OK`:

```json
{
  "id": "4fdb1497-f1b6-4407-b765-2dfe56a765f1",
  "email": "pengguna@example.com",
  "created_at": "2026-11-27T03:00:00Z",
  "updated_at": "2026-11-27T03:00:00Z"
}
```

### POST /auth/change-password

`new_password` harus berisi 12–128 karakter dan berbeda dari `current_password`.

Request:

```bash
curl -i -X POST "$BASE_URL/auth/change-password" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -d '{"current_password":"kata-sandi-rahasia","new_password":"kata-sandi-baru-rahasia"}'
```

Response `200 OK`:

```json
{
  "message": "Password changed successfully."
}
```

Seluruh sesi pengguna dicabut setelah password berubah. Login kembali dengan
password baru untuk memperoleh token. Password saat ini yang salah mengembalikan
`400 Bad Request` dengan kode `INVALID_CURRENT_PASSWORD`.

### POST /auth/logout

Request:

```bash
curl -i -X POST "$BASE_URL/auth/logout" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

Response `200 OK`:

```json
{
  "message": "Logout successful."
}
```

Endpoint ini mencabut sesi token yang dikirim. Token tersebut tidak dapat dipakai
lagi untuk mengakses endpoint yang membutuhkan autentikasi.

### GET /menus

Hanya menu aktif yang ditampilkan. Endpoint ini dapat diakses tanpa token.

| Query parameter | Wajib | Nilai bawaan | Keterangan |
| --- | --- | --- | --- |
| `search` | Tidak | Tanpa filter | Maksimal 1.500 karakter; mencari sebagian teks pada ID, nama, atau deskripsi tanpa membedakan huruf besar/kecil |
| `limit` | Tidak | `20` | Jumlah item per halaman, dari `1` sampai `READ_MAX_PAGE_SIZE` (maksimal `1000`) |
| `after` | Tidak | Halaman pertama | Cursor `next_cursor` dari respons sebelumnya, maksimal 20.480 karakter |

Request:

```bash
curl -sS --get "$BASE_URL/menus" \
  --data-urlencode 'search=ikan' \
  --data-urlencode 'limit=20'
```

Response `200 OK`:

```json
{
  "items": [
    {
      "id": "menu-ikan",
      "name": "Paket Ikan dan Nasi",
      "description": "Paket katalog dengan nasi, hidangan ikan, sayur, dan buah sebagai contoh pilihan menu.",
      "your_food": [
        "+-------+",
        "| IKAN  |",
        "| NASI  |",
        "+-------+"
      ]
    }
  ],
  "next_cursor": null,
  "has_next_page": false
}
```

Menu diurutkan menurut ID menaik. Jika pencarian tidak menemukan menu,
`items` berupa array kosong.

Pencarian tidak memerlukan ID lengkap. Contohnya, `GET /menus?search=001`
mencocokkan bagian `001` dalam ID, sedangkan `GET /menus?search=ayam` menemukan
menu yang ID, nama, atau deskripsinya mengandung `ayam`. Pencocokan berupa
potongan teks berurutan, tanpa wildcard atau koreksi salah ketik. Spasi di awal
dan akhir kata pencarian diabaikan. Request GET ini tidak membutuhkan body.

### GET /menus/{menu_id}

Request:

```bash
curl -sS "$BASE_URL/menus/menu-ikan"
```

Response `200 OK`:

```json
{
  "id": "menu-ikan",
  "name": "Paket Ikan dan Nasi",
  "description": "Paket katalog dengan nasi, hidangan ikan, sayur, dan buah sebagai contoh pilihan menu.",
  "your_food": [
    "+-------+",
    "| IKAN  |",
    "| NASI  |",
    "+-------+"
  ]
}
```

Menu tidak aktif atau ID yang tidak ditemukan mengembalikan `404 Not Found`.

### POST /recommendations

| Body field | Wajib | Keterangan |
| --- | --- | --- |
| `region_name` | Ya | Nama daerah, minimal 2 karakter dan maksimal `MAX_REGION_NAME_LENGTH` (contoh konfigurasi: `1500`) |
| `geographic_description` | Ya | Deskripsi geografis, minimal 10 karakter dan maksimal `MAX_GEOGRAPHIC_DESCRIPTION_LENGTH` (contoh konfigurasi: `20000`) |

Spasi di awal/akhir kedua field dibuang sebelum validasi panjang.

Request:

```bash
curl -sS -X POST "$BASE_URL/recommendations" \
  -H 'Content-Type: application/json' \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  -d '{"region_name":"Pesisir Utara","geographic_description":"Wilayah pesisir dengan kegiatan perikanan dan cuaca hangat."}'
```

Response `201 Created`:

```json
{
  "id": "50ddae73-f674-4c4a-890b-dc31d64347de",
  "region_name": "Pesisir Utara",
  "geographic_description": "Wilayah pesisir dengan kegiatan perikanan dan cuaca hangat.",
  "selection_reason": "Paket ikan sesuai dengan potensi bahan pangan dari kegiatan perikanan setempat.",
  "menu": {
    "id": "menu-ikan",
    "name": "Paket Ikan dan Nasi",
    "description": "Paket katalog dengan nasi, hidangan ikan, sayur, dan buah sebagai contoh pilihan menu.",
    "your_food": [
      "+-------+",
      "| IKAN  |",
      "| NASI  |",
      "+-------+"
    ]
  },
  "created_at": "2026-11-27T03:05:00Z"
}
```

Rekomendasi disimpan sebagai riwayat pengguna. `menu` merupakan snapshot katalog
pada saat rekomendasi dibuat, sehingga tetap tersedia walaupun katalog berubah.
Jika tidak ada menu yang sesuai, API mengembalikan `422 Unprocessable Entity`
dengan kode `NO_SUITABLE_MENU`.

### GET /recommendations

Endpoint ini hanya menampilkan riwayat milik pengguna yang terautentikasi.

| Query parameter | Wajib | Nilai bawaan | Keterangan |
| --- | --- | --- | --- |
| `limit` | Tidak | `20` | Jumlah item per halaman, dari `1` sampai `READ_MAX_PAGE_SIZE` (maksimal `1000`) |
| `after` | Tidak | Halaman pertama | Cursor `next_cursor` dari respons sebelumnya, maksimal 20.480 karakter |

Request:

```bash
curl -sS --get "$BASE_URL/recommendations" \
  -H "Authorization: Bearer $ACCESS_TOKEN" \
  --data-urlencode 'limit=20'
```

Response `200 OK`:

```json
{
  "items": [
    {
      "id": "50ddae73-f674-4c4a-890b-dc31d64347de",
      "region_name": "Pesisir Utara",
      "geographic_description": "Wilayah pesisir dengan kegiatan perikanan dan cuaca hangat.",
      "selection_reason": "Paket ikan sesuai dengan potensi bahan pangan dari kegiatan perikanan setempat.",
      "menu": {
        "id": "menu-ikan",
        "name": "Paket Ikan dan Nasi",
        "description": "Paket katalog dengan nasi, hidangan ikan, sayur, dan buah sebagai contoh pilihan menu.",
        "your_food": [
          "+-------+",
          "| IKAN  |",
          "| NASI  |",
          "+-------+"
        ]
      },
      "created_at": "2026-11-27T03:05:00Z"
    }
  ],
  "next_cursor": null,
  "has_next_page": false
}
```

Riwayat diurutkan menurut waktu pembuatan terbaru, lalu ID menurun untuk waktu
yang sama. Pengguna yang belum memiliki rekomendasi menerima `items: []`.

### GET /recommendations/{recommendation_id}

Request:

```bash
curl -sS "$BASE_URL/recommendations/50ddae73-f674-4c4a-890b-dc31d64347de" \
  -H "Authorization: Bearer $ACCESS_TOKEN"
```

Response `200 OK`:

```json
{
  "id": "50ddae73-f674-4c4a-890b-dc31d64347de",
  "region_name": "Pesisir Utara",
  "geographic_description": "Wilayah pesisir dengan kegiatan perikanan dan cuaca hangat.",
  "selection_reason": "Paket ikan sesuai dengan potensi bahan pangan dari kegiatan perikanan setempat.",
  "menu": {
    "id": "menu-ikan",
    "name": "Paket Ikan dan Nasi",
    "description": "Paket katalog dengan nasi, hidangan ikan, sayur, dan buah sebagai contoh pilihan menu.",
    "your_food": [
      "+-------+",
      "| IKAN  |",
      "| NASI  |",
      "+-------+"
    ]
  },
  "created_at": "2026-11-27T03:05:00Z"
}
```

ID yang tidak berbentuk UUID mengembalikan `422 Unprocessable Entity`.
Rekomendasi yang tidak ditemukan atau milik pengguna lain mengembalikan
`404 Not Found`.

### Mengambil halaman berikutnya

Ketika `has_next_page` bernilai `true`, salin nilai `next_cursor` dari respons
ke parameter `after`. Contoh untuk daftar menu:

```bash
NEXT_CURSOR='NILAI_NEXT_CURSOR_DARI_RESPONS'
curl -sS --get "$BASE_URL/menus" \
  --data-urlencode 'search=ikan' \
  --data-urlencode 'limit=20' \
  --data-urlencode "after=$NEXT_CURSOR"
```

Pertahankan `search` yang sama dengan halaman sebelumnya. Untuk riwayat,
gunakan `GET /recommendations` dengan header Bearer token pengguna yang sama.
Pada halaman terakhir, `next_cursor` bernilai `null` dan `has_next_page` bernilai
`false`. Cursor yang rusak, berbeda jenis resource, berbeda konteks pencarian,
atau berbeda pengguna mengembalikan HTTP 422.

## Contoh response error

Error menggunakan object `error` dengan `code` dan `message`. Field `details`
hanya muncul bila tersedia; `retry_after` muncul pada error pembatasan permintaan.

### Autentikasi diperlukan

Request tanpa token:

```bash
curl -i "$BASE_URL/recommendations"
```

Response `401 Unauthorized`, dengan header `WWW-Authenticate: Bearer`:

```json
{
  "error": {
    "code": "UNAUTHORIZED",
    "message": "Autentikasi diperlukan."
  }
}
```

Token yang tidak valid, kedaluwarsa, atau sudah dicabut juga ditolak. Header
Authorization yang dikirim pada endpoint katalog publik tetap harus valid.

### Parameter tidak valid

Request:

```bash
curl -i --get "$BASE_URL/menus" --data-urlencode 'limit=0'
```

Response `422 Unprocessable Entity`:

```json
{
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Input tidak valid.",
    "details": [
      {
        "field": "limit",
        "message": "Nilai tidak valid."
      }
    ]
  }
}
```

### Menu tidak ditemukan

Request:

```bash
curl -i "$BASE_URL/menus/tidak-ada"
```

Response `404 Not Found`:

```json
{
  "error": {
    "code": "NOT_FOUND",
    "message": "Menu tidak ditemukan."
  }
}
```

### Batas permintaan terlampaui

Seluruh pembacaan katalog dan riwayat berbagi batas `READ_LIMIT` per
`READ_WINDOW_SECONDS`, dihitung menurut pengguna untuk token valid atau IP
untuk anonim/token tidak valid. Contoh konfigurasi membatasi 600 request per 60 detik.

Jika batas terlampaui, contoh response `429 Too Many Requests` dengan header
`Retry-After: 7`:

```json
{
  "error": {
    "code": "RATE_LIMIT_EXCEEDED",
    "message": "Batas permintaan terlampaui.",
    "retry_after": 7
  }
}
```

Tunggu sesuai `Retry-After` sebelum mencoba lagi. Permintaan rekomendasi yang
masih diproses untuk pengguna yang sama memakai kode `RECOMMENDATION_IN_PROGRESS`.
Jika kapasitas rekomendasi seluruh aplikasi penuh, API mengembalikan
`429` dengan kode `LLM_CAPACITY_EXCEEDED` dan header `Retry-After: 1`.
Permintaan ini tidak memanggil LLM dan tidak mengurangi kuota rekomendasi pengguna.

Token yang ditolak memakai kuota kegagalan autentikasi bersama. Pada endpoint
yang memakai autentikasi wajib sebelum menjalankan handler (profil, ganti
password, logout, dan pembuatan rekomendasi), permintaan tanpa token juga
memakai kuota ini. Pembacaan anonim tetap memakai kuota baca per IP.
Logout dengan sesi valid tetap tersedia saat Redis bermasalah.

### Ringkasan status error

| Status | Contoh kode | Arti |
| --- | --- | --- |
| `400` | `INVALID_CURRENT_PASSWORD` | Password saat ini salah |
| `401` | `UNAUTHORIZED`, `INVALID_CREDENTIALS` | Token atau kredensial tidak valid |
| `404` | `NOT_FOUND` | Resource tidak ditemukan atau tidak dapat diakses pengguna |
| `409` | `EMAIL_ALREADY_EXISTS` | Email sudah terdaftar |
| `422` | `VALIDATION_ERROR`, `NO_SUITABLE_MENU` | Input tidak valid atau tidak ada menu yang sesuai |
| `429` | `RATE_LIMIT_EXCEEDED`, `RECOMMENDATION_IN_PROGRESS`, `LLM_CAPACITY_EXCEEDED` | Batas penggunaan, proses aktif, atau kapasitas LLM penuh |
| `500` | `INTERNAL_ERROR` | Kesalahan internal |
| `502` | `LLM_INVALID_RESPONSE` | Respons provider tidak valid |
| `503` | `DATABASE_UNAVAILABLE`, `CATALOG_UNAVAILABLE`, `RATE_LIMIT_UNAVAILABLE`, `LLM_UNAVAILABLE` | Layanan pendukung tidak tersedia |
| `504` | `LLM_TIMEOUT` | Pemilihan menu melewati batas waktu |

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
      "your_food": ["+-------+", "| paket |", "+-------+"]
    }
  ]
}
```

ID harus unik. `your_food` berupa array string yang disalin apa adanya ke respons
API dan snapshot rekomendasi. Tidak ada batas panjang string atau jumlah elemen
khusus untuk field ini. Unicode, baris dengan lebar berbeda, string kosong, tab,
dan newline diperbolehkan; array kosong juga diperbolehkan. String tidak dipangkas
atau diubah. Jika isinya gambar teks, tampilkan dengan font monospace dan
gabungkan elemen `your_food` dengan newline.

Snapshot rekomendasi lama dengan field `ascii_art` tetap dapat dibaca. API selalu
mengembalikan `your_food`, dan rekomendasi baru menyimpan nama field tersebut.
Katalog baru memakai `your_food`.

Katalog hanya dibaca sekali saat startup. Setelah mengedit file katalog pada
deployment Docker, jalankan `docker compose up -d --build api`.

## LLM dan batas penggunaan

`LLM_OUTPUT_MODE` disesuaikan dengan kemampuan provider:

- `text`: kompatibilitas terluas; prompt meminta JSON dan backend tetap memvalidasinya secara ketat.
- `json_object`: gunakan bila provider mendukung JSON object mode.
- `json_schema`: gunakan bila provider mendukung structured output berbasis JSON Schema.

Satu pengguna dapat memiliki satu proses rekomendasi aktif. Pada konfigurasi contoh, rekomendasi dibatasi 100 permintaan per rolling 60 detik dan 1.000 permintaan per rolling 10.800 detik. Batas endpoint lain dapat diubah melalui variabel pada `.env.example`.

`LLM_MAX_CONCURRENT_REQUESTS=5` membatasi seluruh aplikasi menjadi maksimal lima
proses rekomendasi bersamaan, termasuk saat API memakai beberapa worker atau
instance yang terhubung ke Redis yang sama. Slot ditahan sampai proses selesai
atau gagal, termasuk penyimpanan hasil, sehingga jumlah panggilan LLM bersamaan
juga dibatasi. Permintaan saat penuh langsung menerima `429`; tidak ada antrean.
Slot dilepas setelah proses berakhir dan memiliki masa berlaku sesuai
`RECOMMENDATION_LOCK_TTL_SECONDS` untuk pemulihan jika worker berhenti mendadak.
Nilai maksimum harus berupa bilangan bulat lebih besar dari nol.

Nilai batas penggunaan pada `.env.example`:

| Pengaturan | Nilai | Jendela / satuan |
| --- | --- | --- |
| `RECOMMENDATION_MINUTE_LIMIT` | 100 | Per 60 detik, per pengguna |
| `RECOMMENDATION_LONG_WINDOW_LIMIT` | 1.000 | Per 10.800 detik, per pengguna |
| `REGISTER_LIMIT` | 50 | Per 3.600 detik, per IP |
| `LOGIN_LIMIT` | 100 | Per 900 detik, per IP dan email |
| `CHANGE_PASSWORD_LIMIT` | 50 | Per 900 detik, per pengguna |
| `INVALID_AUTH_LIMIT` | 30 | Batas kegagalan autentikasi Bearer per IP, lintas endpoint |
| `INVALID_AUTH_WINDOW_SECONDS` | 60 | Durasi jendela kegagalan autentikasi, dalam detik |
| `ME_LIMIT` | 600 | Per 60 detik, per pengguna |
| `READ_LIMIT` | 600 | Per 60 detik, per pengguna atau IP |
| `READ_MAX_PAGE_SIZE` | 1.000 | Item per halaman; default request tetap 20 |
| `MAX_REGION_NAME_LENGTH` | 1.500 | Karakter nama daerah |
| `MAX_GEOGRAPHIC_DESCRIPTION_LENGTH` | 20.000 | Karakter deskripsi geografis |
| `MAX_SELECTION_REASON_LENGTH` | 10.000 | Karakter alasan pemilihan |
| `LLM_MAX_OUTPUT_TOKENS` | 5.120 | Token keluaran provider |
| `LLM_MAX_RESPONSE_BYTES` | 163.840 | Byte respons provider |
| `LLM_MAX_CONCURRENT_REQUESTS` | 5 | Maksimum proses rekomendasi bersamaan untuk seluruh aplikasi |
| `LLM_TIMEOUT_SECONDS` | 600 | Detik timeout pemilihan menu |
| `RECOMMENDATION_LOCK_TTL_SECONDS` | 1.200 | Detik masa berlaku lock rekomendasi |

Pencarian dibatasi 1.500 karakter dan cursor 20.480 karakter. Konten katalog
mendukung nama menu hingga 1.500 karakter dan deskripsi hingga 20.000 karakter.
`your_food` tidak memiliki batas panjang atau jumlah elemen khusus di schema
dan tidak dibatasi pada karakter ASCII. Field ini diambil dari katalog, tanpa
dikirim ke LLM. Batas lainnya tetap dipakai bersama oleh schema API, katalog,
dan input/output LLM.

Lock membatasi satu proses rekomendasi aktif per pengguna.
`RECOMMENDATION_LOCK_TTL_SECONDS` harus lebih besar dari `LLM_TIMEOUT_SECONDS`.
Batas provider LLM mengikuti layanan yang digunakan.

Untuk deployment lain, sesuaikan nilai pada `.env` dengan tabel di atas dan
buat ulang container API agar environment baru terbaca, misalnya
`docker compose up -d --build api`. Jangan menimpa kredensial yang sudah ada.

Hasil pilihan menu dan teks katalog tidak menjamin kecukupan gizi, verifikasi ketersediaan bahan, atau pemenuhan standar gizi resmi.

## Struktur modul

```text
app/
├── main.py                  # lifecycle, health, pemasangan router
├── db.py                    # koneksi dan inisialisasi tabel
├── models.py                # model SQLAlchemy terpusat
├── api/                     # endpoint HTTP dan dependency request
│   ├── auth.py
│   ├── menus.py
│   ├── recommendations.py
│   └── dependencies.py
├── services/                # logika bisnis dan integrasi provider
│   ├── auth.py
│   ├── authentication.py    # validasi bearer token dan sesi
│   ├── recommendations.py
│   ├── catalog.py
│   └── llm.py
├── repositories/            # query dan penyimpanan database
│   ├── auth.py
│   └── recommendations.py
├── schemas/                 # validasi input/output per fitur
│   ├── __init__.py          # ekspor schema publik
│   ├── auth.py
│   ├── catalog.py
│   ├── recommendations.py
│   ├── llm.py
│   └── pagination.py
├── core/                    # kebutuhan bersama
│   ├── config.py
│   ├── security.py
│   ├── errors.py
│   ├── pagination.py
│   ├── limits.py
│   ├── rate_limit.py
│   └── rate_limit_scripts.py
└── data/
    ├── menus.json
    └── select_menu.txt
```

### Pembagian tanggung jawab

| Modul | Tanggung jawab |
| --- | --- |
| `api/` | Endpoint HTTP, parameter, status response, dan dependency FastAPI |
| `services/` | Proses bisnis, validasi sesi, pemilihan menu, dan integrasi LLM |
| `repositories/` | Query SQLAlchemy serta penyimpanan data |
| `schemas/` | Validasi request, response, katalog, dan payload LLM |
| `models.py` | Definisi tabel SQLAlchemy |
| `db.py` | Koneksi, inisialisasi tabel, dan penyesuaian kolom nama daerah lama |
| `core/` | Konfigurasi, keamanan, error, pagination, dan rate limit |
| `data/` | Katalog menu dan prompt pemilihan menu |

### Lokasi kode per fitur

| Fitur | Endpoint | Service | Repository | Schema |
| --- | --- | --- | --- | --- |
| Autentikasi | [api/auth.py](app/api/auth.py) | [services/auth.py](app/services/auth.py), [services/authentication.py](app/services/authentication.py) | [repositories/auth.py](app/repositories/auth.py) | [schemas/auth.py](app/schemas/auth.py) |
| Rekomendasi | [api/recommendations.py](app/api/recommendations.py) | [services/recommendations.py](app/services/recommendations.py) | [repositories/recommendations.py](app/repositories/recommendations.py) | [schemas/recommendations.py](app/schemas/recommendations.py) |
| Katalog | [api/menus.py](app/api/menus.py) | [services/catalog.py](app/services/catalog.py) | — | [schemas/catalog.py](app/schemas/catalog.py) |
| Provider LLM | — | [services/llm.py](app/services/llm.py) | — | [schemas/llm.py](app/schemas/llm.py) |

Dependency request bersama berada di [api/dependencies.py](app/api/dependencies.py).
Implementasi rate limiter berada di [core/rate_limit.py](app/core/rate_limit.py),
sedangkan operasi Redis atomik disimpan di
[core/rate_limit_scripts.py](app/core/rate_limit_scripts.py).

Saat menambahkan fitur, tempatkan endpoint di `api/`, proses bisnis di
`services/`, query di `repositories/`, dan validasi di `schemas/`.
Import schema per fitur tersedia melalui `app.schemas.auth`,
`app.schemas.recommendations`, dan modul sejenis; ekspor publik melalui
`app.schemas` juga tersedia.

## Pengujian

Gunakan Python 3.12 atau lebih baru:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python -m pytest -q
```

| File | Cakupan |
| --- | --- |
| [tests/test_rest_api.py](tests/test_rest_api.py) | Endpoint, autentikasi, privasi riwayat, pagination, dan penanganan error |
| [tests/test_limits.py](tests/test_limits.py) | Batas input/output, kapasitas halaman, kuota rekomendasi, dan pelebaran kolom nama daerah |
| [tests/test_your_food.py](tests/test_your_food.py) | Konten Unicode tanpa batas format khusus, respons katalog/rekomendasi, snapshot lama, dan schema OpenAPI |
| [tests/conftest.py](tests/conftest.py) | Katalog uji, database SQLite, identitas pengguna, dan pengganti rate limiter |

Tes tidak membutuhkan PostgreSQL, Redis, atau LLM yang berjalan. Konfigurasi
uji diambil dari `.env.example` dengan kredensial khusus pengujian.

Pengujian kapasitas LLM berada di `tests/test_llm_capacity.py`. Untuk menjalankan
uji integrasi konkurensi, pelepasan slot, dan pemulihan slot kedaluwarsa dengan
Redis nyata, set `TEST_REDIS_URL`, lalu jalankan `python -m pytest -q`.
Tanpa variabel tersebut, dua uji integrasi Redis dilewati. Kunci pengujian memakai
namespace acak dan dibersihkan setelah pengujian.
