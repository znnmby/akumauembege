# Embege


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

```bash
docker compose up --build
```

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
| `POST` | `/auth/change-password` | Bearer token | `204 No Content` | Mengubah password dan mencabut seluruh sesi |
| `POST` | `/auth/logout` | Bearer token | `204 No Content` | Mencabut sesi token aktif |
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
ID dari respons API Anda untuk request berikutnya. Contoh menu memakai katalog
bawaan; hasil rekomendasi bergantung pada pilihan LLM dan katalog aktif.

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

Response `204 No Content`, tanpa body:

```http
HTTP/1.1 204 No Content
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

Response `204 No Content`, tanpa body:

```http
HTTP/1.1 204 No Content
```

Endpoint ini mencabut sesi token yang dikirim. Token tersebut tidak dapat dipakai
lagi untuk mengakses endpoint yang membutuhkan autentikasi.

### GET /menus

Hanya menu aktif yang ditampilkan. Endpoint ini dapat diakses tanpa token.

| Query parameter | Wajib | Nilai bawaan | Keterangan |
| --- | --- | --- | --- |
| `search` | Tidak | Tanpa filter | Maksimal 1.500 karakter; mencari nama dan deskripsi tanpa membedakan huruf besar/kecil |
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
      "ascii_art": [
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
  "ascii_art": [
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
    "ascii_art": [
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
        "ascii_art": [
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
    "ascii_art": [
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

### Ringkasan status error

| Status | Contoh kode | Arti |
| --- | --- | --- |
| `400` | `INVALID_CURRENT_PASSWORD` | Password saat ini salah |
| `401` | `UNAUTHORIZED`, `INVALID_CREDENTIALS` | Token atau kredensial tidak valid |
| `404` | `NOT_FOUND` | Resource tidak ditemukan atau tidak dapat diakses pengguna |
| `409` | `EMAIL_ALREADY_EXISTS` | Email sudah terdaftar |
| `422` | `VALIDATION_ERROR`, `NO_SUITABLE_MENU` | Input tidak valid atau tidak ada menu yang sesuai |
| `429` | `RATE_LIMIT_EXCEEDED`, `RECOMMENDATION_IN_PROGRESS` | Batas penggunaan atau proses aktif |
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

Satu pengguna dapat memiliki satu proses rekomendasi aktif. Pada konfigurasi contoh, rekomendasi dibatasi 100 permintaan per rolling 60 detik dan 1.000 permintaan per rolling 10.800 detik. Batas endpoint lain dapat diubah melalui variabel pada `.env.example`.

Batas penggunaan pada `.env.example` sudah dilonggarkan 10 kali lipat:

| Pengaturan | Sebelumnya | Sekarang | Jendela / satuan |
| --- | --- | --- | --- |
| `RECOMMENDATION_MINUTE_LIMIT` | 10 | 100 | Per 60 detik, per pengguna |
| `RECOMMENDATION_LONG_WINDOW_LIMIT` | 100 | 1.000 | Per 10.800 detik, per pengguna |
| `REGISTER_LIMIT` | 5 | 50 | Per 3.600 detik, per IP |
| `LOGIN_LIMIT` | 10 | 100 | Per 900 detik, per IP dan email |
| `CHANGE_PASSWORD_LIMIT` | 5 | 50 | Per 900 detik, per pengguna |
| `ME_LIMIT` | 60 | 600 | Per 60 detik, per pengguna |
| `READ_LIMIT` | 60 | 600 | Per 60 detik, per pengguna atau IP |
| `READ_MAX_PAGE_SIZE` | 100 | 1.000 | Item per halaman; default request tetap 20 |
| `MAX_REGION_NAME_LENGTH` | 150 | 1.500 | Karakter nama daerah |
| `MAX_GEOGRAPHIC_DESCRIPTION_LENGTH` | 2.000 | 20.000 | Karakter deskripsi geografis |
| `MAX_SELECTION_REASON_LENGTH` | 1.000 | 10.000 | Karakter alasan pemilihan |
| `LLM_MAX_OUTPUT_TOKENS` | 512 | 5.120 | Token keluaran provider |
| `LLM_MAX_RESPONSE_BYTES` | 16.384 | 163.840 | Byte respons provider |
| `LLM_TIMEOUT_SECONDS` | 60 | 600 | Detik timeout pemilihan menu |
| `RECOMMENDATION_LOCK_TTL_SECONDS` | 120 | 1.200 | Detik masa berlaku lock rekomendasi |

Batas pencarian menjadi 1.500 karakter dan cursor 20.480 karakter. Konten katalog
mendukung nama menu hingga 1.500 karakter, deskripsi hingga 20.000 karakter, serta
ASCII art hingga 1.000 baris dengan lebar maksimal 2.000 karakter per baris.
Batas ini dipakai bersama oleh schema API, katalog, dan input/output LLM.

Jumlah request dinaikkan dengan jendela rate limit yang sama. Syarat format ID,
email, password, token, dan autentikasi tetap berlaku. Lock tetap membatasi satu
proses rekomendasi aktif per pengguna; TTL diperpanjang agar selalu melebihi
timeout LLM. Batas provider LLM sendiri tetap mengikuti layanan yang digunakan.

Untuk deployment lain, sesuaikan nilai pada `.env` dengan tabel di atas dan
buat ulang container API agar environment baru terbaca, misalnya
`docker compose up -d --build api`. Jangan menimpa kredensial yang sudah ada.

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
│   ├── limits.py
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
