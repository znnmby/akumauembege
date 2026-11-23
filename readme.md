# Cafe_MerahPutih

API pemesanan kafe menggunakan FastAPI, SQLAlchemy, dan PostgreSQL. Membutuhkan Python 3.11 atau lebih baru.

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
`database-init` untuk membuat/memperbarui tabel dan memuat data role, kategori,
menu, dan user dari JSON sebelum API dimulai. Folder JSON dipasang read-only.

Hentikan layanan dengan `docker compose down`.

Setelah menambah data JSON, jalankan seed ulang tanpa menghapus volume PostgreSQL:

```bash
docker compose run --rm database-init
```

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

## Kontrak API

Seluruh route memakai path langsung (`/auth`, `/users`, `/categories`,
`/menu-items`, `/orders`), tanpa prefix `/api`. Schema lengkap dan tombol
Authorize tersedia di `/docs`. Header autentikasi: `Authorization: Bearer <access_token>`.

Registrasi `POST /auth/register` menerima `user_name`, `email`, `password`
(8–128 karakter), dan `role` (`CUSTOMER` atau `CASHIER`, default `CUSTOMER`).
Registrasi cashier memang publik sesuai kontrak; batasi route ini sebelum
dipakai pada layanan yang hanya memperbolehkan staf internal menjadi cashier.
Hanya admin yang dapat membuat akun `ADMIN` melalui `POST /users`.

Login:

```json
{"identifier":"admin","password":"password-dari-INITIAL_ADMIN_PASSWORD"}
```

`identifier` menerima username atau email. Respons login berisi `access_token`,
`refresh_token`, `token_type`, dan `expires_in` (detik).
`POST /auth/refresh` dan `POST /auth/logout` menerima
`{"refresh_token":"..."}`. Refresh token berlaku satu kali: ditukar dengan pasangan
token baru dan token lama dicabut. Logout memerlukan access token dan mencabut
refresh token milik pengguna; access token yang sudah terbit tetap berlaku sampai
kedaluwarsa. `ACCESS_TOKEN_EXPIRE_MINUTES` dan `REFRESH_TOKEN_EXPIRE_DAYS`
mengatur masa berlaku. Hash refresh token disimpan di database.

List pengguna, menu, dan pesanan memakai `page=1`, `limit=20` (maksimal 100).
Respons pagination berisi `items`, `total`, `page`, `limit`, dan `pages`.
Menu mendukung `category_id` dan pencarian substring literal `q`. Menu unavailable
tetap terlihat, sedangkan menu soft-deleted tidak muncul. Kategori yang masih
direferensikan menu tidak dapat dihapus.

Pembuatan pesanan:

```json
{"items":[{"menu_item_id":1,"quantity":2}]}
```

Konfirmasi oleh customer pemilik memberikan nomor antrean unik per tanggal UTC.
Menu wajib tersedia dan belum dihapus, baik saat pembuatan maupun konfirmasi.

```mermaid
stateDiagram-v2
    [*] --> PENDING: customer creates
    PENDING --> QUEUED: owner confirms
    PENDING --> CANCELLED: owner or cashier
    QUEUED --> CANCELLED: owner or cashier
    QUEUED --> COMPLETED: cashier completes
```

Completion menerima `{"payment_method":"CASH"}`; pilihan `CASH`, `QRIS`, atau
`CARD`, default `CASH`. Completion mencatat cashier, `completed_at`, dan `paid_at`;
tidak memproses gateway pembayaran atau menghitung nominal karena schema menu
belum memiliki harga. Admin dapat melihat semua pesanan, tetapi tidak mengambil
aksi khusus cashier. Customer hanya dapat melihat pesanannya sendiri.
Filter `date=YYYY-MM-DD` memakai tanggal pembuatan UTC, termasuk pesanan pending.

Error JSON memuat `message`, `code`, dan `status_code`. Input invalid menggunakan
400; token invalid 401; akses ditolak 403; data hilang 404; konflik/state invalid
409. `/health` memeriksa koneksi database dan memberikan 503 bila tidak tersedia.

## WebSocket

Hubungkan `/ws?token=<access_token>` atau gunakan header Bearer. Token invalid
ditolak dan koneksi ditutup saat token kedaluwarsa. Pesan awal `connected`;
kirim teks `ping` untuk menerima event `pong`.

Event pesanan: `order.created`, `order.queued`, `order.completed`, `order.cancelled`.
Customer hanya menerima event pesanannya sendiri; cashier/admin menerima semua.
Event katalog (`category.*`, `menu.created`, `menu.updated`,
`menu.availability_changed`, `menu.deleted`) dikirim ke semua pengguna terhubung.
Event perubahan memuat `event`, `id`, `timestamp`, dan `data`, serta dikirim
setelah transaksi commit.

Broker realtime berada di memori satu proses; jalankan satu worker. Event bukan
antrean persisten dan tidak diputar ulang setelah reconnect; ambil kondisi terkini
melalui HTTP. Hindari mencatat query string WebSocket di reverse proxy karena
dapat mengandung token. Tidak ada middleware CORS.

## Pengujian

```bash
python -m pip install -e '.[test]'
TEST_DATABASE_URL=postgresql+psycopg://user:password@localhost:55432/test_db python -m pytest -q
```

Gunakan PostgreSQL khusus pengujian. Setiap test membuat schema terisolasi dan
menghapus schema tersebut setelah selesai; akun database perlu izin membuat
schema. Pengujian mencakup izin role, token rotation, queue concurrency,
soft deletion, state machine, WebSocket, seed JSON, dan upgrade schema lama.