from __future__ import annotations

from django import forms

from apps.documents.classification import BOOK_CLASS_FIELDS
from apps.documents.models import ClassificationTerm

from .models import RAGFlowConfig, SourceChunk, SourceDocument


def _term_label(term: ClassificationTerm) -> str:
    if term.description:
        return f"{term.code} — {term.name} ({term.description})"
    return f"{term.code} — {term.name}"


class SourceDocumentAdminForm(forms.ModelForm):
    class Meta:
        model = SourceDocument
        exclude = (
            "ragflow_document_id",
            "ragflow_status",
            "ragflow_error",
            "ragflow_synced_at",
        )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name, slug in BOOK_CLASS_FIELDS:
            field = self.fields.get(field_name)
            if not field:
                continue
            field.queryset = (
                ClassificationTerm.objects.filter(group__slug=slug)
                .select_related("group")
                .order_by("sort_order", "code")
            )
            field.label_from_instance = _term_label
            field.required = False
            field.empty_label = "— Chưa phân loại —"


_TEXT_WIDGET = forms.Textarea(
    attrs={
        "rows": 24,
        "class": (
            "w-full rounded-default border border-base-300 bg-white px-3 py-2 "
            "font-mono text-sm leading-relaxed dark:border-base-700 dark:bg-base-900"
        ),
        "spellcheck": "false",
        "style": "min-height: 28rem;",
    }
)


class SourceChunkAdminForm(forms.ModelForm):
    class Meta:
        model = SourceChunk
        fields = ("text_md",)
        widgets = {"text_md": _TEXT_WIDGET}

    def clean_text_md(self):
        from apps.dataset.markdown import strip_break_markers

        text = strip_break_markers(self.cleaned_data.get("text_md") or "")
        if not text:
            raise forms.ValidationError("Nội dung không được trống.")
        return text


def _chunk_option_label(chunk: SourceChunk) -> str:
    code = (chunk.chunk_code or "").strip() or f"Đoạn {chunk.position}"
    return f"{code} — trang {chunk.page_number}"


class SourceChunkAddForm(SourceChunkAdminForm):
    page_number = forms.IntegerField(
        min_value=1,
        label="Trang",
        help_text="Số trang (ccc). Hệ thống sẽ chèn `<!-- Trang N -->` vào đầu nội dung.",
    )
    after_chunk = forms.ModelChoiceField(
        queryset=SourceChunk.objects.none(),
        required=False,
        label="Phân đoạn liền trước",
        empty_label="— Đầu trang —",
        help_text="Đoạn cùng trang đứng ngay trước đoạn mới. Để trống nếu đây là đoạn đầu của trang.",
    )

    class Meta(SourceChunkAdminForm.Meta):
        fields = ("text_md",)

    def __init__(self, *args, document=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.document = document
        field = self.fields["after_chunk"]
        if document is not None:
            field.queryset = document.chunks.order_by("page_number", "page_seq", "position")
        else:
            field.queryset = SourceChunk.objects.none()
        field.label_from_instance = _chunk_option_label

    def clean_after_chunk(self):
        after = self.cleaned_data.get("after_chunk")
        if after and self.document and after.document_id != self.document.pk:
            raise forms.ValidationError("Phân đoạn liền trước không thuộc văn bản này.")
        return after

    def clean(self):
        cleaned = super().clean()
        after = cleaned.get("after_chunk")
        page = cleaned.get("page_number")
        if after and page and int(after.page_number) != int(page):
            self.add_error(
                "after_chunk",
                "Phân đoạn liền trước phải thuộc cùng trang đã chọn.",
            )
        return cleaned


class RAGFlowConfigForm(forms.ModelForm):
    class Meta:
        model = RAGFlowConfig
        fields = (
            "base_url",
            "api_key",
            "dataset_id",
            "meta_timeout",
            "chunk_timeout",
            "embed_prefix",
        )
        widgets = {
            "api_key": forms.PasswordInput(render_value=True, attrs={"autocomplete": "new-password"}),
            "base_url": forms.TextInput(attrs={"placeholder": "http://host:80"}),
            "dataset_id": forms.TextInput(attrs={"placeholder": "ID knowledge base trên RAGFlow"}),
            "meta_timeout": forms.NumberInput(attrs={"min": "1"}),
            "chunk_timeout": forms.NumberInput(attrs={"min": "1"}),
        }

    def clean_base_url(self):
        url = (self.cleaned_data.get("base_url") or "").strip().rstrip("/")
        if url.endswith("/api/v1"):
            url = url[: -len("/api/v1")].rstrip("/")
        return url

    def clean_dataset_id(self):
        return (self.cleaned_data.get("dataset_id") or "").strip()

    def clean_embed_prefix(self):
        prefix = self.cleaned_data.get("embed_prefix")
        if prefix is None:
            return "Passage: "
        return prefix
