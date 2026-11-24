# Cafe_MerahPutih

Aplikasi FastAPI minimal. Membutuhkan Python 3.11 atau lebih baru.

## Instalasi

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

## Menjalankan aplikasi

```bash
python -m uvicorn app.main:app --reload
```

Dokumentasi interaktif: <http://127.0.0.1:8000/docs>.

## Endpoint

| Method | Path | Respons |
|---|---|---|
| GET | `/` | `{"message": "Hello World"}` |
