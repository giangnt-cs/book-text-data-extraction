# Google Cloud Vision – Service Account Key (file JSON)

Đây KHÔNG phải API key dạng chuỗi ngắn, mà là file JSON do Google cấp cho Service Account.

File JSON chứa:
- project_id: ID project trên Google Cloud
- client_email: email service account (vd: ocr-bot@my-project.iam.gserviceaccount.com)
- private_key: private key để xác thực API

Cách lấy file:
1. Vào https://console.cloud.google.com/
2. IAM & Admin → Service Accounts → Create service account
3. Gán quyền (role) có thể gọi Vision API, vd: "Cloud Vision API User"
4. Keys → Add key → Create new key → JSON → tải file về
5. APIs & Services → Enable APIs → bật "Cloud Vision API"

Cách dùng với script thử:
  cp ~/Downloads/my-key.json credentials/google-vision.json
  export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/credentials/google-vision.json"
  pip install google-cloud-vision
  python scripts/test_google_vision.py /path/to/page.png

Lưu ý: KHÔNG commit file JSON lên git (đã gitignore *.json).
