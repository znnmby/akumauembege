# API testing embege dengan Postman

Folder ini berisi koleksi Postman dan panduan menjalankan pengujian REST API
embege. Koleksi memakai format Postman Collection v2.1 dan berisi 20 request.
Isi JSON dipertahankan dari ekspor asli. Penomorannya mengikuti file tersebut:
setelah request 09, urutan berikutnya adalah 11.

| File | Isi |
| --- | --- |
| [embege.postman_collection.json](embege.postman_collection.json) | Request, variabel, dan script pengujian Postman |
| [README.md](README.md) | Persiapan, urutan pengujian, dan penjelasan hasil |

Panduan instalasi backend dan dokumentasi endpoint lengkap ada di
[README utama](../README.md).

## 1. Siapkan backend

Untuk pengujian lokal, siapkan `.env` mengikuti README utama, lalu jalankan dari
folder utama proyek:

```bash
docker compose up -d --build --wait
```

Base URL lokal bawaan adalah `http://127.0.0.1:8000`, mengikuti `API_PORT`.
Backend membutuhkan PostgreSQL, Redis, dan konfigurasi provider LLM yang valid.
Untuk menguji deployment, gunakan URL HTTPS backend yang sudah aktif.

Gunakan akun khusus pengujian. Alur koleksi membuat akun, menyimpan rekomendasi,
mengganti password, dan mencabut sesi. Request 16 memanggil provider LLM sungguhan
dan dapat memakai kuota atau biaya layanan. Panduan ini tidak memuat kredensial
database, JWT secret, atau API key provider.

## 2. Impor koleksi

1. Buka workspace Postman.
2. Pilih **Import** atau **Use resources or import → Import**, sesuai versi
   Postman. Kamu juga bisa menyeret file ke jendela Postman.
3. Pilih `docs/embege.postman_collection.json`.
4. Pastikan koleksi **embege** muncul pada daftar Collections.

Langkah impor juga dijelaskan pada
[dokumentasi resmi Postman](https://learning.postman.com/docs/getting-started/importing-and-exporting/importing-data/).

## 3. Isi Collection Variables

Buka koleksi **embege**, lalu tab **Variables**. Isi nilai lokal yang digunakan
Postman saat request dijalankan. Ekspor terbaru sudah mengisi `baseUrl` dengan
`https://akumauembege.vercel.app`; variabel akun dan token masih kosong.

| Variabel | Contoh nilai | Keterangan |
| --- | --- | --- |
| `baseUrl` | `https://akumauembege.vercel.app` | Nilai bawaan ekspor; ganti menjadi `http://127.0.0.1:8000` untuk Docker lokal, tanpa slash di akhir |
| `emailA` | `postman-a-001@example.com` | Gunakan email baru untuk setiap pengujian penuh |
| `passA` | `Uji-Awal-123456!` | Password awal akun A, 12–128 karakter |
| `newPassA` | `Uji-Baru-123456!` | Password pengganti, 12–128 karakter dan berbeda dari `passA` |

Jangan mengganti `passA` menjadi password baru di tengah pengujian. Request 19
memang memakai password lama untuk membuktikan bahwa login ditolak, sedangkan
request 20 memakai `newPassA`.

`tokenA` diisi otomatis oleh script login pada request 07. `freshA` diisi oleh
script request 20. Variabel menu dan cursor diisi oleh request katalog.

Koleksi juga memuat variabel akun B, tetapi belum memiliki rangkaian request
untuk akun B. Variabel tersebut tidak wajib diisi untuk menjalankan 20 request
yang tersedia.

Kamu boleh memilih **No Environment** jika semua nilai diisi pada Collection
Variables. Jika environment memiliki variabel bernama sama, nilainya dapat
menimpa nilai koleksi. Periksa scope tersebut ketika token atau `baseUrl` yang
dipakai tidak sesuai. Penjelasan scope ada pada
[panduan variabel Postman](https://learning.postman.com/docs/use/send-requests/variables/variables/).

## 4. Jalankan berurutan dengan Collection Runner

1. Buka menu koleksi **embege**, lalu pilih **Run collection** atau **Run**.
2. Pilih seluruh request dan pertahankan urutan aslinya.
3. Gunakan **1 iteration** untuk pengujian pertama.
4. Jalankan koleksi dan periksa hasil setiap request.

Urutan ini penting karena request berikutnya membutuhkan akun, token, atau
password yang dihasilkan request sebelumnya. Collection Runner juga menjaga
variabel lokal antar-request dalam satu run. Lihat
[panduan Collection Runner](https://learning.postman.com/docs/tests-and-scripts/running-collections/intro-to-collection-runs/intro-to-collection-runs-overview/).

| Nomor | Pengujian | Status yang diharapkan | Hal yang diperiksa |
| --- | --- | --- | --- |
| 01 | Identitas API dan dokumentasi Bearer | `200` | Judul OpenAPI dan skema autentikasi |
| 02 | Health | `200` | `status: "ok"` |
| 03 | Daftar akun A | `201` | Akun dibuat tanpa membocorkan password hash |
| 04 | Email duplikat | `409` | `EMAIL_ALREADY_EXISTS` |
| 05 | Format email salah | `422` | `VALIDATION_ERROR` |
| 06 | Profil tanpa login | `401` | `UNAUTHORIZED` |
| 07 | Login akun A | `200` | Access token tersimpan sebagai `tokenA` |
| 08 | Baca profil akun A | `200` | ID dan email sesuai akun yang didaftarkan |
| 09 | Katalog menu per halaman | `200` | Daftar menu dan cursor pagination |
| 11 | Cari menu tertentu | `200` | Daftar hasil pencarian dan cursor |
| 12 | Riwayat tanpa login | `401` | `UNAUTHORIZED` |
| 13 | Riwayat akun A | `200` | Struktur daftar riwayat REST |
| 14 | Rekomendasi tanpa login | `401` | `UNAUTHORIZED` |
| 15 | Deskripsi daerah terlalu pendek | `422` | `VALIDATION_ERROR` |
| 16 | Buat rekomendasi | `201` | ID rekomendasi, alasan, dan `menu.your_food` |
| 17 | Ganti password akun A | `200` | Password berubah dan seluruh sesi akun dicabut |
| 18 | Profil memakai token lama | `401` | Token lama ditolak |
| 19 | Login memakai password lama | `401` | `INVALID_CREDENTIALS` |
| 20 | Login memakai password baru | `200` | Token baru tersedia sebagai `freshA` |
| 21 | Logout akun A | `200` | Sesi `freshA` dicabut |

Status `401`, `409`, dan `422` pada skenario negatif adalah hasil yang benar.
Test dianggap berhasil jika status dan isi respons sesuai dengan skenario,
bukan jika semua request mendapat `200`.

Untuk mengulang seluruh alur, ganti `emailA`, misalnya menjadi
`postman-a-002@example.com`, dan kosongkan token lama. Menjalankan ulang dengan
email yang sama akan membuat request 03 mendapat `409`. Password akun lama juga
sudah berubah setelah request 17.

## 5. Jika menjalankan request satu per satu

Ikuti urutan tabel di atas dan periksa tab **Test Results** setelah menekan
**Send**. Untuk GET, gunakan query parameter; Body tidak diperlukan. Untuk POST,
gunakan Body **raw → JSON** dengan `Content-Type: application/json`.

Pada ekspor ini, beberapa script memakai `pm.variables.set()`. Nilainya bersifat
lokal untuk request atau satu collection run, sehingga tidak selalu tersedia
ketika kamu menekan Send pada request lain secara terpisah.

| Nilai yang dibutuhkan | Jika menjalankan secara manual |
| --- | --- |
| `userIdA` | Setelah request 03, tambahkan Collection Variable bernama persis `userIdA` dari field `id` respons |
| `tokenA` | Request 07 sudah memakai `pm.collectionVariables.set()` sehingga token tersimpan pada koleksi |
| `recId` | Salin `id` respons request 16 ke Collection Variable `recId` jika ingin membuka detail rekomendasi |
| `freshA` | Setelah request 20, salin `access_token` ke Collection Variable `freshA` sebelum logout pada request 21 |

Perhatikan ejaan: ekspor memiliki `userldA` dan `recld` dengan huruf kecil `l`,
sedangkan script memakai `userIdA` dan `recId` dengan huruf besar `I`. Nama itu
berbeda. Collection Runner memperoleh nama yang benar dari script lokal;
pengujian manual perlu memakai nama yang dibaca oleh script atau request.

Request tanpa login sudah menggunakan **No Auth**. Request akun A menggunakan
Bearer `{{tokenA}}`; logout terakhir menggunakan Bearer `{{freshA}}`. Jangan
mengganti token request 18, karena request itu sengaja menguji token lama.

## 6. Mencari menu dan memakai cursor

Ganti parameter `search` pada request 11 untuk mencari sebagian ID, nama, atau
deskripsi tanpa membedakan huruf besar/kecil:

```http
GET {{baseUrl}}/menus?search=ayam
GET {{baseUrl}}/menus?search=suwir
GET {{baseUrl}}/menus?search=001
```

Untuk pagination, jalankan request 09 terlebih dahulu. Pada tab **Params**,
aktifkan `after={{menuNextCursor}}` untuk halaman berikutnya. Script memperbarui
cursor dari respons terakhir. Untuk kembali, gunakan
`before={{menuPreviousCursor}}`. Aktifkan hanya salah satu dari `after` atau
`before`, dan gunakan hanya cursor yang tersedia.

Pada request 11, parameter `before` yang masih dinonaktifkan pada ekspor
menunjuk ke `{{menuNextCursor}}`. Jika ingin menguji halaman sebelumnya,
ganti nilainya menjadi `{{menuPreviousCursor}}` sebelum mengaktifkannya.
Pertahankan `search` yang sama selama berpindah halaman. Jika kata pencarian
berubah, nonaktifkan cursor dan mulai dari halaman pertama.

## 7. Membaca riwayat dan menambah pemeriksaan manual

Riwayat memakai format REST berikut:

```json
{
  "items": [],
  "next_cursor": null,
  "has_next_page": false
}
```

`items: []` berarti belum ada rekomendasi tersimpan untuk akun yang tokennya
dipakai. Rekomendasi baru masuk riwayat setelah request pembuatan berhasil
disimpan. Respons tidak memakai `data.myHistory.edges` atau `errors[].extensions`.
Error aplikasi dibaca melalui `body.error.code`.

Setelah request 16 berhasil dan sebelum ganti password, ulangi request 13 untuk
memastikan ID rekomendasi tersebut muncul di riwayat. Pengujian tambahan yang
bisa dibuat sebagai request baru:

| Request | Autentikasi | Hasil yang diharapkan |
| --- | --- | --- |
| `GET {{baseUrl}}/menus/{{menu1}}` | No Auth | `200`, detail menu dengan `your_food` |
| `GET {{baseUrl}}/recommendations/{{recId}}` | Bearer `{{tokenA}}` sebelum ganti password | `200`, ID dan menu sesuai hasil pembuatan |
| `GET {{baseUrl}}/auth/me` setelah request 21 | Bearer `{{freshA}}` | `401 UNAUTHORIZED`, sesi logout sudah dicabut |

Untuk menguji privasi antar-akun, daftarkan akun B dengan email berbeda, login,
dan simpan tokennya sebagai `tokenB`. Akses ID rekomendasi akun A dengan token B
harus mendapat `404 NOT_FOUND`, dan daftar riwayat B tidak boleh memuat data A.
Skenario akun B ini belum disertakan sebagai request di file koleksi.

## 8. Menangani hasil gagal

| Hasil | Pemeriksaan |
| --- | --- |
| `baseUrl` tidak terisi atau koneksi gagal | Isi Collection Variables, cek backend, dan jalankan request Health |
| Request 03 mendapat `409` | Pakai email baru untuk pengujian penuh berikutnya |
| Request akun A mendapat `401` | Jalankan login, periksa token, masa berlaku, serta apakah password sudah diganti atau sesi sudah logout |
| Test profil gagal pada `userIdA` | Gunakan Runner atau simpan ID registrasi ke Collection Variable dengan ejaan yang benar |
| `429 RECOMMENDATION_IN_PROGRESS` | Akun yang sama masih memiliki lock proses; tunggu request pertama selesai dan jangan menekan Send berulang |
| `429 LLM_CAPACITY_EXCEEDED` | Slot rekomendasi seluruh aplikasi sedang penuh; ikuti header `Retry-After` |
| `429 RATE_LIMIT_EXCEEDED` | Kuota terlampaui; ikuti `Retry-After` sebelum mencoba lagi |
| `422 NO_SUITABLE_MENU` pada request 16 | Provider tidak memilih kandidat yang sesuai; periksa konteks daerah dan katalog aktif |
| `502`, `503`, atau `504` | Baca `error.code`, lalu periksa layanan database, Redis, atau provider LLM sesuai kode error |
| Request 20 berhasil tetapi logout mendapat `401` | Jika memakai Send satu per satu, pastikan `freshA` tersimpan di Collection Variables |

Lock adalah penanda proses sementara. Nilai `retry_after` untuk proses aktif
berasal dari sisa masa berlaku lock, bukan perkiraan pasti waktu LLM selesai.
Sesuaikan timeout klien dengan batas backend; memperpanjang timeout Postman
tidak memperpanjang batas waktu deployment.

Jika request pembuatan rekomendasi gagal, periksa penyebabnya sebelum melanjutkan
alur. Jangan mengubah ekspektasi test sukses menjadi error hanya agar hasilnya
PASS. Bandingkan respons dengan dokumentasi API dan skenario yang sedang diuji.
