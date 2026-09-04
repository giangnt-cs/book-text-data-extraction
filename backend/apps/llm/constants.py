"""Metadata hiển thị Admin: icon & màu cho công cụ OCR và loại trang."""

from __future__ import annotations

TOOL_META: dict[str, dict[str, str]] = {
    "google_vision": {
        "label": "Google Cloud Vision",
        "icon": "🔍",
        "color": "#4285F4",
        "bg": "#E8F0FE",
    },
    "google_document_ai": {
        "label": "Document AI (tự theo trang)",
        "icon": "📄",
        "color": "#0F9D58",
        "bg": "#E6F4EA",
    },
    "google_document_ai_ocr": {
        "label": "Document AI · Enterprise OCR",
        "icon": "📄",
        "color": "#0F9D58",
        "bg": "#E6F4EA",
    },
    "google_document_ai_layout": {
        "label": "Document AI · Layout Parser",
        "icon": "▦",
        "color": "#0B8043",
        "bg": "#E6F4EA",
    },
    "vlm_gemini": {
        "label": "Gemini",
        "icon": "✦",
        "color": "#8E75B2",
        "bg": "#F3EEFC",
    },
    "vlm_openai": {
        "label": "ChatGPT",
        "icon": "💬",
        "color": "#10A37F",
        "bg": "#E7F7F1",
    },
    "vlm_ollama": {
        "label": "Ollama",
        "icon": "🦙",
        "color": "#1A1A1A",
        "bg": "#F5F5F5",
    },
    "vlm_vllm": {
        "label": "vLLM",
        "icon": "⚡",
        "color": "#F97316",
        "bg": "#FFF7ED",
    },
    "vlm_deepseek": {
        "label": "DeepSeek",
        "icon": "🐋",
        "color": "#0066FF",
        "bg": "#E8F1FF",
    },
    "llm_gemini": {
        "label": "Gemini",
        "icon": "✦",
        "color": "#8E75B2",
        "bg": "#F3EEFC",
    },
    "llm_openai": {
        "label": "ChatGPT",
        "icon": "💬",
        "color": "#10A37F",
        "bg": "#E7F7F1",
    },
    "llm_ollama": {
        "label": "Ollama",
        "icon": "🦙",
        "color": "#1A1A1A",
        "bg": "#F5F5F5",
    },
    "llm_vllm": {
        "label": "vLLM",
        "icon": "⚡",
        "color": "#F97316",
        "bg": "#FFF7ED",
    },
    "llm_deepseek": {
        "label": "DeepSeek",
        "icon": "🐋",
        "color": "#0066FF",
        "bg": "#E8F1FF",
    },
    "mineru": {
        "label": "MinerU",
        "icon": "⛏️",
        "color": "#D97706",
        "bg": "#FEF3C7",
    },
    "paddle_ocr": {
        "label": "PaddleOCR",
        "icon": "🛶",
        "color": "#2563EB",
        "bg": "#DBEAFE",
    },
    # legacy
    "ollama": {
        "label": "Ollama",
        "icon": "🦙",
        "color": "#1A1A1A",
        "bg": "#F5F5F5",
    },
}

PAGE_MODE_META: dict[str, dict[str, str]] = {
    "standard": {
        "label": "Trang thường",
        "icon": "📝",
        "color": "#22c55e",
        "hint": "Không bảng/công thức → Cloud Vision hoặc Document AI Enterprise OCR",
    },
    "layout": {
        "label": "Trang có bảng/công thức",
        "icon": "📊",
        "color": "#a855f7",
        "hint": "Có bảng → Document AI Layout Parser + OCR; có công thức/hình → Gemini hoặc Ollama",
    },
}

# Danh sách đầy đủ công cụ + option — hiển thị Admin «Công cụ OCR»
OCR_TOOL_CATALOG: list[dict[str, str]] = [
    {
        "backend": "google_vision",
        "tool": "Google Cloud Vision",
        "option": "—",
        "page_types": "Trang thường (chữ, đoạn văn)",
        "avoid": "Trang có bảng — không giữ hàng/cột",
        "notes": "Nhanh, rẻ. Nên gán cho «OCR trang thường».",
    },
    {
        "backend": "google_document_ai_ocr",
        "tool": "Google Document AI",
        "option": "Enterprise OCR",
        "page_types": "Trang thường (chữ, đoạn văn)",
        "avoid": "Trang có bảng — chữ từng ô bị tách dòng",
        "notes": "OCR chữ tốt hơn Vision trên sách scan. Nên gán cho «OCR trang thường».",
    },
    {
        "backend": "google_document_ai_layout",
        "tool": "Google Document AI",
        "option": "Layout Parser + OCR",
        "page_types": "Trang có bảng",
        "avoid": "Trang chỉ có chữ (tốn 2 lần gọi API)",
        "notes": "Bảng lấy từ Layout Parser, chữ lấy từ Enterprise OCR rồi ghép. Nên gán cho «OCR trang bảng/công thức».",
    },
    {
        "backend": "google_document_ai",
        "tool": "Google Document AI",
        "option": "Tự theo loại trang",
        "page_types": "Cả hai loại trang (một cấu hình)",
        "avoid": "Khi muốn tách rõ chữ / bảng thành 2 công cụ khác nhau",
        "notes": "Trang thường → Enterprise OCR; trang bảng → Layout Parser + OCR. Có thể gán cho cả hai loại trang.",
    },
    {
        "backend": "vlm_gemini",
        "tool": "Gemini (VLM)",
        "option": "Model vision (chọn khi tải danh sách)",
        "page_types": "Trang có bảng, công thức, hình/sơ đồ",
        "avoid": "Sách nhiều trang chữ — chậm và đắt hơn OCR",
        "notes": "Hiểu layout bằng mô hình ảnh. Nên gán cho «OCR trang bảng/công thức».",
    },
    {
        "backend": "vlm_ollama",
        "tool": "Ollama (VLM)",
        "option": "Model vision local (qwen2.5vl, …)",
        "page_types": "Trang có bảng, công thức, hình/sơ đồ",
        "avoid": "Máy không GPU / model chưa kéo",
        "notes": "Chạy local, không gửi sách ra ngoài. Nên gán cho «OCR trang bảng/công thức».",
    },
    {
        "backend": "vlm_vllm",
        "tool": "vLLM (VLM)",
        "option": "Model vision trên server vLLM (OpenAI /v1)",
        "page_types": "Trang có bảng, công thức, hình/sơ đồ",
        "avoid": "Server vLLM chưa bật vision / thiếu GPU",
        "notes": "Nhanh hơn Ollama. Base URL dạng http://host:8000/v1. Nên gán cho «OCR trang bảng/công thức».",
    },
    {
        "backend": "vlm_openai",
        "tool": "ChatGPT (VLM)",
        "option": "GPT-4o / GPT-4.1, …",
        "page_types": "Trang có bảng, công thức, hình/sơ đồ",
        "avoid": "Sách nhiều trang chữ — chi phí cao",
        "notes": "Nên gán cho «OCR trang bảng/công thức».",
    },
    {
        "backend": "vlm_deepseek",
        "tool": "DeepSeek (VLM)",
        "option": "Model vision trên API DeepSeek",
        "page_types": "Trang có bảng, công thức, hình/sơ đồ",
        "avoid": "Sách nhiều trang chữ",
        "notes": "Nên gán cho «OCR trang bảng/công thức».",
    },
    {
        "backend": "paddle_ocr",
        "tool": "PaddleOCR",
        "option": "—",
        "page_types": "Trang thường (chữ)",
        "avoid": "Trang có bảng phức tạp",
        "notes": "OCR local, không cần API Google.",
    },
    {
        "backend": "mineru",
        "tool": "MinerU",
        "option": "—",
        "page_types": "PDF phức tạp (cột, bảng native)",
        "avoid": "Sách scan chất lượng thấp nếu chưa cấu hình endpoint",
        "notes": "Cần Base URL service MinerU.",
    },
]


def catalog_entry(backend: str) -> dict[str, str]:
    for row in OCR_TOOL_CATALOG:
        if row["backend"] == backend:
            return row
    return {
        "backend": backend or "",
        "tool": backend or "",
        "option": "—",
        "page_types": "",
        "avoid": "",
        "notes": "",
    }

# Giữ cho template/admin cũ nếu còn tham chiếu bbox legend
BOX_META: dict[str, dict[str, str]] = {
    "text": {"label": "Text", "icon": "📝", "color": "#22c55e"},
    "table": {"label": "Bảng", "icon": "📊", "color": "#ef4444"},
    "formula": {"label": "Công thức", "icon": "∑", "color": "#a855f7"},
    "image": {"label": "Hình ảnh", "icon": "🖼️", "color": "#3b82f6"},
    "columnar": {"label": "Cột", "icon": "📰", "color": "#f59e0b"},
    "unknown": {"label": "Không xác định", "icon": "❔", "color": "#94a3b8"},
}

DEFAULT_BASE_URL: dict[str, str] = {
    "vlm_openai": "https://api.openai.com/v1",
    "vlm_deepseek": "https://api.deepseek.com",
    "vlm_ollama": "http://localhost:11434",
    "vlm_vllm": "http://localhost:8000/v1",
    "llm_openai": "https://api.openai.com/v1",
    "llm_deepseek": "https://api.deepseek.com",
    "llm_ollama": "http://localhost:11434",
    "llm_vllm": "http://localhost:8000/v1",
    "ollama": "http://localhost:11434",
    "mineru": "",
    "google_vision": "",
    "google_document_ai": "",
    "google_document_ai_ocr": "",
    "google_document_ai_layout": "",
    "paddle_ocr": "",
    "vlm_gemini": "",
}

DEFAULT_MODELS: dict[str, str] = {
    "vlm_gemini": "gemini-2.0-flash",
    "vlm_openai": "gpt-4o",
    "vlm_ollama": "qwen2.5vl:32b",
    "vlm_vllm": "",
    "vlm_deepseek": "deepseek-chat",
    "llm_gemini": "gemini-2.0-flash",
    "llm_openai": "gpt-4o",
    "llm_ollama": "qwen2.5:32b",
    "llm_vllm": "",
    "llm_deepseek": "deepseek-chat",
}

DEFAULT_API_KEY_ENV: dict[str, str] = {
    "google_vision": "GOOGLE_APPLICATION_CREDENTIALS",
    "google_document_ai": "GOOGLE_APPLICATION_CREDENTIALS",
    "google_document_ai_ocr": "GOOGLE_APPLICATION_CREDENTIALS",
    "google_document_ai_layout": "GOOGLE_APPLICATION_CREDENTIALS",
    "vlm_gemini": "GEMINI_API_KEY",
    "vlm_openai": "OPENAI_API_KEY",
    "vlm_deepseek": "DEEPSEEK_API_KEY",
    "llm_gemini": "GEMINI_API_KEY",
    "llm_openai": "OPENAI_API_KEY",
    "llm_deepseek": "DEEPSEEK_API_KEY",
    "vlm_ollama": "",
    "vlm_vllm": "",
    "llm_ollama": "",
    "llm_vllm": "",
    "mineru": "",
    "paddle_ocr": "",
}
