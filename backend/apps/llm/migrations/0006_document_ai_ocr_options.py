# Generated manually

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("llm", "0005_text_cleaning"),
    ]

    operations = [
        migrations.AlterField(
            model_name="ocrprovider",
            name="backend",
            field=models.CharField(
                choices=[
                    ("google_vision", "Google Cloud Vision — trang thường (chữ)"),
                    (
                        "google_document_ai_ocr",
                        "Document AI · Enterprise OCR — trang thường (chữ)",
                    ),
                    (
                        "google_document_ai_layout",
                        "Document AI · Layout Parser + OCR — trang có bảng",
                    ),
                    (
                        "google_document_ai",
                        "Document AI · Tự theo loại trang (chữ → OCR, bảng → Layout Parser)",
                    ),
                    ("vlm_gemini", "Gemini (VLM) — trang bảng / công thức / hình"),
                    (
                        "vlm_ollama",
                        "Ollama (VLM) — trang bảng / công thức / hình (local)",
                    ),
                    ("vlm_openai", "ChatGPT (VLM) — trang bảng / công thức / hình"),
                    ("vlm_deepseek", "DeepSeek (VLM) — trang bảng / công thức / hình"),
                    ("paddle_ocr", "PaddleOCR — trang thường (chữ)"),
                    ("mineru", "MinerU — PDF phức tạp"),
                    ("ollama", "Ollama (cũ)"),
                ],
                max_length=40,
                verbose_name="Loại công cụ",
            ),
        ),
    ]
