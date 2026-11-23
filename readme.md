# Cafe_MerahPutih

API minimal menggunakan FastAPI. Membutuhkan Python 3.11 atau lebih baru.

## Instalasi

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
cp .env.example .env
```

Jika `.env` sudah ada, pertahankan konfigurasi lokalnya. `APP_NAME` mengatur judul
aplikasi dan pesan endpoint utama. Jangan commit `.env` atau kredensial.

## Menjalankan aplikasi

```bash
python -m uvicorn app.main:app --reload --env-file .env
```

Opsi `--env-file` memuat konfigurasi `.env` sebelum aplikasi diimpor.
`--reload` hanya untuk pengembangan lokal.

### Docker Compose

```bash
docker compose up
```

Compose memuat variabel dari `.env`, menjalankan PostgreSQL, lalu menjalankan
`database-init` untuk membuat tabel dan memuat data JSON sebelum API dimulai.

Hentikan layanan dengan `docker compose down`.

### Inisialisasi database

Untuk menjalankan inisialisasi secara manual setelah PostgreSQL aktif:

```bash
python -m app.databases.script
```

Script aman dijalankan ulang; hanya data yang belum ada yang ditambahkan. Data awal
dibaca dari `category.json`, `menu.json`, `role.json`, dan `user.json` dalam
`app/databases/data/`.

`role.json` mengisi tabel `roles`; `users.role` mereferensikan `roles.name`.
Role baru cukup ditambahkan ke JSON, tanpa mengubah enum Python. `models.py` hanya
mendefinisikan struktur dan relasi; impor model tidak menjalankan seed.

Urutan pengisian: role dan kategori, lalu menu dan user. Nilai yang sudah ada tidak
ditimpa atau dihapus; perubahan role user yang bertentangan menghasilkan error.
`is_available` wajib diisi di `menu.json`.

Pada PostgreSQL, inisialisasi juga mengubah kolom role versi lama dari CHECK enum
menjadi foreign key tanpa menghapus user. `OrderStatus` tetap enum aturan pesanan,
bukan data dummy.

User admin awal memakai password dari `INITIAL_ADMIN_PASSWORD` di `.env`. Password
tersebut di-hash dengan Argon2 dan tidak disimpan dalam file JSON. Ganti nilai contoh
sebelum menjalankan inisialisasi.

- API: http://127.0.0.1:8000/
- Swagger UI: http://127.0.0.1:8000/docs
- ReDoc: http://127.0.0.1:8000/redoc
- OpenAPI: http://127.0.0.1:8000/openapi.json

## Cek endpoint

```bash
curl --fail http://127.0.0.1:8000/
```

Respons:

```json
{"message":"Cafe_MerahPutih API"}
```