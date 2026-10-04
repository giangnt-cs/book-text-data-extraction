# Google Cloud credentials

Thư mục này chứa **file JSON Service Account** để gọi Google Vision / Document AI.

Đây **không** phải API key dạng chuỗi ngắn. File JSON Google cấp gồm `project_id`, `client_email` và `private_key`. **Không commit file JSON** — git chỉ theo dõi `README.md` này.

## 1. Tạo Service Account

1. Mở [Google Cloud Console](https://console.cloud.google.com/).
2. Chọn project (hoặc tạo project mới).
3. **IAM & Admin → Service Accounts → Create service account**.
4. Gán quyền tối thiểu, ví dụ:
   - **Cloud Vision API User** — nếu dùng Vision API
   - **Document AI API User** — nếu dùng Document AI
5. **Keys → Add key → Create new key → JSON** → tải file về.
6. **APIs & Services → Enable APIs** và bật:
   - Cloud Vision API
   - Cloud Document AI API (nếu dùng)

## 2. Đặt file vào repo

Đổi tên file tải về cho dễ nhớ, rồi copy vào thư mục này:

```bash
cp ~/Downloads/<file-key>.json credentials/google-vision.json
```

Docker Compose đã mount `./credentials` vào `/app/credentials` trong container.

## 3. Khai báo biến môi trường

Trong `.env` (copy từ `.env.example`):

```env
GOOGLE_APPLICATION_CREDENTIALS=/app/credentials/google-vision.json

# Document AI — điền project ID trên Google Cloud
DOCUMENT_AI_PROJECT_ID=<your-gcp-project-id>
DOCUMENT_AI_LOCATION=us
# Để trống = tự tạo/tìm processor lần đầu
DOCUMENT_AI_OCR_PROCESSOR_ID=
DOCUMENT_AI_LAYOUT_PROCESSOR_ID=
```

Chạy script ngoài Docker (máy local):

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/credentials/google-vision.json"
```

Trong Django Admin → **OCR**, chọn backend Google Vision / Document AI và trỏ API key / path tới file JSON trong container (`/app/credentials/google-vision.json`).

## 4. Kiểm tra nhanh (tùy chọn)

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/credentials/google-vision.json"
pip install google-cloud-vision
python scripts/test_google_vision.py /path/to/page.png
```
