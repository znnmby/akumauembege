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