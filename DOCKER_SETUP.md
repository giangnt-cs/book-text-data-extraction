# Docker Setup

Hướng dẫn triển khai hệ thống OCR sách bằng Docker Compose trên server GPU.

## Kiến trúc

```text
Admin (web) ──upload PDF──► PostgreSQL + media volume
       │
       └── Celery task ──► worker-gpu
                              │
                              ├─ doclayout-yolo  → phát hiện bảng / công thức
                              └─ Google Document AI
                                   ├─ Enterprise OCR  (trang text thường)
                                   └─ Layout Parser   (trang có bảng/công thức)
                              │
                              └── file Markdown → /media/books/markdown/
```

| Service | Vai trò |
|---------|---------|
| `web` | Django Admin (Unfold), upload PDF, tải Markdown |
| `worker-gpu` | Celery – detect layout + gọi Document AI |
| `postgres` | Database |
| `redis` | Hàng đợi Celery |

Task OCR **chạy nền** trên worker. Tắt trình duyệt không làm dừng job.

## Yêu cầu

- Docker Engine + Docker Compose v2
- **Server GPU**: NVIDIA Container Toolkit (`nvidia-container-toolkit`)
- Google Cloud:
  - Bật **Cloud Document AI API**
  - Service account JSON (quyền Document AI)
  - Processors `OCR_PROCESSOR` và `LAYOUT_PARSER_PROCESSOR` (tự tạo lần đầu nếu để trống ID)

## Chuẩn bị

```bash
git clone <repo> && cd book-text-data-extraction
cp .env.example .env
```

Đặt file credentials vào thư mục `credentials/` (thư mục này không commit lên git), ví dụ:

```text
credentials/google-vision.json
```

### Biến môi trường quan trọng (`.env`)

```env
DJANGO_SECRET_KEY=<chuỗi-bí-mật-mạnh>
DJANGO_ALLOWED_HOSTS=<ip-server>,localhost
DJANGO_DEBUG=0

DATABASE_URL=postgres://ocr_platform:ocr_platform@postgres:5432/ocr_platform
CELERY_BROKER_URL=redis://redis:6379/0
CELERY_RESULT_BACKEND=redis://redis:6379/1
CELERY_FORCE_CPU=0

# Google credentials (mount vào /app/credentials trong container)
GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/google-vision.json

# Document AI
DOCUMENT_AI_PROJECT_ID=
DOCUMENT_AI_LOCATION=us
DOCUMENT_AI_OCR_PROCESSOR_ID=          # để trống = tự tạo/tìm lần đầu
DOCUMENT_AI_LAYOUT_PROCESSOR_ID=
```

Tùy chọn (chỉ khi dùng backend Ollama thay Document AI):

```env
OLLAMA_BASE_URL=http://<ip-ollama>:11434
OLLAMA_MODEL=qwen2.5vl:32b
```

## Khởi động (server GPU – khuyến nghị)

Stack dùng **network riêng** `ocr-platform-net`, tách khỏi container khác trên cùng server.
Container/volume có prefix `ocr-platform-*`.

```bash
docker compose up -d --build
```

**Quan trọng:** Trong `.env`, `DATABASE_URL` phải trỏ `@postgres:5432`, **không** dùng `127.0.0.1`.
Trong container, `localhost` là chính container đó, không phải PostgreSQL.
`docker-compose.yml` đã ghi đè URL đúng cho `web` và `worker-gpu` — pull code mới rồi recreate:

```bash
docker compose down
docker compose up -d --build
```

Tạo tài khoản admin (lần đầu):

```bash
docker compose exec web python backend/manage.py createsuperuser
```

Kiểm tra:

```bash
docker compose ps
curl http://localhost:8000/healthz/
docker compose logs -f worker-gpu
```

Truy cập: **http://&lt;ip-server&gt;:8000/admin/**

## Máy dev local không GPU

Chỉ dùng profile `cpu-only` khi **cố ý** test trên máy không có GPU (chậm hơn nhiều):

```bash
# docker-compose.yml: bật profile cpu-only, tắt worker-gpu nếu cần
CELERY_FORCE_CPU=1 docker compose --profile cpu-only up -d --build postgres redis web worker-cpu
```

Deploy production vẫn nên dùng `worker-gpu` trên server có NVIDIA GPU.

## Cấu hình OCR trong Admin

Sau `migrate`, vào **Admin → Cấu hình OCR**:

| Trường | Giá trị mặc định khuyến nghị |
|--------|-------------------------------|
| Backend | **Google Document AI** |
| api_key_env | `GOOGLE_APPLICATION_CREDENTIALS` |
| is_default | ✓ |
| params (JSON) | `{"project_id": "...", "location": "us", "ocr_processor_id": "...", "layout_processor_id": "..."}` |

Nếu DB đã có provider Ollama cũ từ lần cài trước, tạo provider Document AI mới và đặt **Mặc định**.

**Admin → Cấu hình doclayout-yolo**: giữ mặc định (`models/doclayout_yolo.pt`, device `cuda:0`, DPI `150`).

### Chọn GPU (server 2 card)

Trong `.env`:

```env
GPU_DEVICE_IDS=0   # GPU đầu tiên (nvidia-smi)
# GPU_DEVICE_IDS=1   # GPU thứ hai
```

Worker gán **đúng 1 GPU vật lý** qua `deploy.resources.devices.device_ids` (không dùng `gpus: all`).
Trong container card đó luôn là `cuda:0` — Admin giữ device **cuda:0**.

Đổi GPU → sửa `.env` rồi **bắt buộc recreate** container:

```bash
docker compose up -d --force-recreate worker-gpu
docker compose logs worker-gpu 2>&1 | head -20
```

Log khởi động phải thấy:
```text
Worker GPU: GPU_DEVICE_IDS=1 NVIDIA_VISIBLE_DEVICES=1 CUDA_VISIBLE_DEVICES=0
```

Kiểm tra trong container:
```bash
docker compose exec worker-gpu nvidia-smi
# Chỉ hiện 1 GPU (index 0 trong container = GPU 1 trên host)
```

Kiểm tra GPU đang dùng:

```bash
docker compose exec worker-gpu nvidia-smi
```

## Luồng sử dụng

1. **Admin → Sách → Thêm sách**: nhập tên, upload PDF, chọn ngôn ngữ (`vi`).
2. Lưu sách.
3. Tick **một hoặc nhiều** sách trong danh sách.
4. Action **「Chạy OCR (detect → đánh dấu → Document AI → Markdown)」** → Apply.
5. Refresh trang – theo dõi cột **Status**:
   - `Đang xử lý` → worker đang chạy
   - `Hoàn thành` → xong
   - `Lỗi` → xem `error_message` trong chi tiết sách
6. Khi hoàn thành: cột **Markdown** → **「Tải Markdown」** hoặc vào chi tiết sách → link tải file `.md`.

### Nhiều sách cùng lúc

Có. Tick nhiều dòng → cùng một admin action. Mỗi sách là một Celery task riêng; worker xử lý theo hàng đợi (2 process mặc định).

### Chạy ngầm

Có. OCR chạy trên `worker-gpu` qua Redis. Đóng tab/trình duyệt **không** dừng task. Chỉ dừng khi container worker bị stop hoặc task lỗi.

## Volume & dữ liệu

| Volume | Nội dung |
|--------|----------|
| `pgdata` | Database PostgreSQL |
| `redisdata` | Redis persistence |
| `media_data` | PDF upload, ảnh trang, file Markdown |

File Markdown: `/app/media/books/markdown/` trong container. Web serve qua `/media/` (single-node, không dùng S3).

## Rebuild sau khi cập nhật code

```bash
git pull
docker compose up -d --build web worker-gpu
docker compose exec web python backend/manage.py migrate
```

## Xử lý sự cố

| Triệu chứng | Nguyên nhân thường gặp |
|-------------|------------------------|
| `SERVICE_DISABLED` Document AI | Chưa bật API trên GCP Console |
| Worker không nhận task | `worker-gpu` chưa chạy hoặc thiếu GPU runtime |
| `connection to 127.0.0.1:5432 refused` | `.env` dùng localhost — sửa thành `@postgres:5432` hoặc pull compose mới (đã ghi đè) |
| Link Markdown 404 | Kiểm tra volume `media_data`, rebuild `web` sau cập nhật urls |
| Detect chậm / lỗi CUDA | Xác nhận `nvidia-smi` trên host và `gpus: all` trong compose |
| OCR provider lỗi | Admin → Cấu hình OCR: backend = `google_document_ai`, credentials đúng path trong container |

Xem log worker:

```bash
docker compose logs -f worker-gpu
```

Test Document AI thủ công (ngoài Docker):

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/credentials/<file>.json"
python3 scripts/test_document_ai.py "samples/<file>.pdf" --pages "1,6"
```

## Services trong docker-compose.yml

```bash
docker compose up -d postgres redis web worker-gpu   # production GPU
docker compose --profile cpu-only up -d ...          # dev không GPU (tùy chọn)
```

Không có service Ollama trong compose – Ollama chạy riêng nếu dùng backend đó.
