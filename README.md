# embege

Backend REST API untuk katalog menu dan rekomendasi berdasarkan konteks daerah.

Base URL: [https://akumauembege.vercel.app](https://akumauembege.vercel.app/).

## Dokumentasi dan pengujian

Dokumentasi lengkap seluruh endpoint, parameter, autentikasi, status error,
dan contoh respons tersedia di **[Swagger UI — /docs](https://akumauembege.vercel.app/docs)**.
Endpoint lain dapat langsung diuji melalui tombol **Try it out → Execute**.
Untuk endpoint terlindungi, login terlebih dahulu, klik **Authorize**, lalu
masukkan nilai `access_token` dari respons login.

API juga dapat diuji melalui Postman dengan mengimpor
[koleksi embege](docs/embege.postman_collection.json). Variabel `baseUrl` pada koleksi
sudah diarahkan ke `https://akumauembege.vercel.app`. Isi variabel akun uji yang
dibutuhkan dan jalankan request sesuai urutan.

## Contoh autentikasi

Gunakan base URL berikut untuk contoh `curl`:

```bash
BASE_URL='https://akumauembege.vercel.app'
```

Nilai UUID, waktu, dan token pada contoh adalah ilustrasi. Gunakan nilai yang
benar-benar dikembalikan API saat menjalankan request berikutnya.

### Register — `POST /auth/register`

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

### Login — `POST /auth/login`

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

### Logout — `POST /auth/logout`

Gunakan `access_token` yang diperoleh dari login:

```bash
ACCESS_TOKEN='TOKEN_DARI_RESPONS_LOGIN'
```

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

## Contoh respons rekomendasi

Contoh respons `201 Created` dari `POST /recommendations`:

Objek `menu` di bawah diambil langsung dari
[katalog JSON](app/data/menus.json), termasuk seluruh isi `your_food`.
UUID rekomendasi, waktu, konteks daerah, dan `selection_reason` merupakan
ilustrasi sesuai format respons API, bukan hasil panggilan LLM langsung.

```json
{
  "id": "50ddae73-f674-4c4a-890b-dc31d64347de",
  "region_name": "Sumatra Barat",
  "geographic_description": "Daerah Sumatra Barat dengan tradisi masakan rendang dan hasil pertanian.",
  "selection_reason": "Paket rendang suwir dipilih karena sesuai dengan konteks kuliner daerah Sumatra Barat.",
  "menu": {
    "id": "menu-001-nasi-rendang-suwir-serat-halus-sumatra-barat",
    "name": "Nasi Rendang Suwir Serat Halus (Sumatra Barat)",
    "description": "Daging sapi disuwir sangat halus menyerupai abon basah, disajikan bersama nasi putih cetakan bulat, perkedel kentang pipih, dan daun singkong gulung.",
    "your_food": [
      "                                                                      ",
      "  ▛▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▀▜  ",
      "  ▌                                ██                              ▐  ",
      "  ▌                                ██                              ▐  ",
      "  ▌          ▒░░░▒░▒▒░▒░░          ██    ▒▒▒      ▒▒▒      ▒▒▒     ▐  ",
      "  ▌       ░▒░░░░░▒░░▒░░░░░▒▒       ██   ▓░▓▒▓    ▒▒░░░    ▒░▒▒░    ▐  ",
      "  ▌     ░▒░░░░░▒▒▒▒▒▒░░░░░░▒░░     ██  ▒▓▒▓▒▓▒  ▒░░▓▒▒▒  ▒▓▓▓▒▒▒   ▐  ",
      "  ▌    ░░░░░░▒▒▒░░░▒░▒░░░░░░░▒░    ██  ▒▒▓▒░▒▒  ▒▓▒▒▓▓▒  ▒▒░▒░▓▒   ▐  ",
      "  ▌    ▒░░▒▒░░░▒▒▒░░▒░░░░░░░░░▒    ██   ░░░▒░    ▓░▓▒▒    ░▓▒░▓    ▐  ",
      "  ▌   ░░░▒░░░░░░▒▒░░▒░▒░░░░▒▒░░░   ██    ▒▒▒      ▒▒▒      ▒▒▒     ▐  ",
      "  ▌   ░▒░░▒░░░▒▒░░░▒▒░▒░░░▒░░░░░   ████████████████████████████████▐  ",
      "  ▌    ░▒░▒░░░░░▒░░▒░░▒░▒░░░░░░    ██                              ▐  ",
      "  ▌    ░▒░░░░▒▒▒░░░▒░░▒░░░░░░▒░    ██                 ▒▒▒ ▒▒▒ ▒▒▒  ▐  ",
      "  ▌     ░░░░▒▒░▒▒░░▒▒▒░░░░░░▒░     ██ ▓▒ ▓▒▒ ▒░▓ ▓    ░░▒ ░░▒ ░░▒  ▐  ",
      "  ▌       ░░░▒░░░▒▒▒░░▒░▒░░░       ██ ▓▓ ░▓▓ ▒░░ ▓    ▒▒▒ ▒▒▒ ▒▒▒  ▐  ",
      "  ▌          ░░▒▒▒░░▒▒▒▒▒          ██ ▓▓ ░▒▓ ▒▓▒ ░    ░░▒ ░░▒ ░░▒  ▐  ",
      "  ▌                                ██                              ▐  ",
      "  ▌                                ██                              ▐  ",
      "  ▙▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▟  ",
      "                                                                      "
    ]
  },
  "created_at": "2026-11-27T03:05:00Z"
}
```
