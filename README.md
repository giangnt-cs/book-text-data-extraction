# OCR Sách – PDF → Layout Detect → Document AI → Markdown

Hệ thống OCR sách từ PDF sang Markdown.

## Pipeline

1. Upload PDF sách qua Django Admin
2. **doclayout-yolo** phát hiện bbox, đánh dấu trang có **bảng** / **công thức** (GPU)
3. **Google Document AI** OCR từng trang:
   - Trang thường → **Enterprise OCR**
   - Trang bảng/công thức → **Layout Parser**
4. Tổng hợp file Markdown — tải qua Admin

Chi tiết deploy: xem [DOCKER_SETUP.md](DOCKER_SETUP.md).

## Deploy trên server GPU (docker compose)

```bash
git clone <repo> && cd book-text-data-extraction
cp .env.example .env
# Chỉnh .env: DJANGO_ALLOWED_HOSTS, GOOGLE_APPLICATION_CREDENTIALS, DOCUMENT_AI_*
docker compose up -d --build postgres redis web worker-gpu
docker compose exec web python backend/manage.py createsuperuser
```

Truy cập: `http://<ip-server>:8000/admin/`

## Cấu hình trong Admin

| Menu | Mô tả |
|------|-------|
| **Sách** | Upload PDF, chạy OCR (nhiều sách), tải Markdown |
| **Cấu hình doclayout-yolo** | Model path, confidence, DPI, device |
| **Cấu hình OCR** | Google Document AI (mặc định), Vision hoặc Ollama |

## Xử lý sách

1. Admin → **Sách** → thêm sách + upload PDF
2. Tick một hoặc nhiều sách → Action **「Chạy OCR (detect → đánh dấu → Document AI → Markdown)」**
3. Status = **Hoàn thành** → **「Tải Markdown」**

OCR chạy nền (Celery) — tắt trình duyệt không ảnh hưởng.

## Services

| Service | Mô tả |
|---------|-------|
| `web` | Django Admin (Unfold) |
| `worker-gpu` | Celery – detect + Document AI OCR |
| `postgres` | Database |
| `redis` | Celery broker |
