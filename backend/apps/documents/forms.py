from __future__ import annotations

from django import forms

from .classification import BOOK_CLASS_FIELDS
from .markdown import page_display_text
from .models import Book, ClassificationTerm, Page


def _term_label(term: ClassificationTerm) -> str:
    if term.description:
        return f"{term.code} — {term.name} ({term.description})"
    return f"{term.code} — {term.name}"


class BookAdminForm(forms.ModelForm):
    class Meta:
        model = Book
        fields = "__all__"

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
        pdf_field = self.fields.get("pdf_file")
        if pdf_field is not None:
            pdf_field.help_text = (
                "PDF lớn sẽ được tải từng phần 512KB. "
                "File phải đã có trên máy (OneDrive cần tải về trước)."
            )
            if (self.data.get("pdf_upload_token") or "").strip():
                pdf_field.required = False

    def clean(self):
        cleaned = super().clean()
        token = (self.data.get("pdf_upload_token") or "").strip()
        pdf = cleaned.get("pdf_file")
        if not self.instance.pk and not pdf and not token:
            self.add_error("pdf_file", "Chọn file PDF.")
        return cleaned


class PageReviewForm(forms.ModelForm):
    class Meta:
        model = Page
        fields = ("text_md",)
        widgets = {
            "text_md": forms.Textarea(
                attrs={
                    "id": "page-text-md-editor",
                    "rows": 1,
                    "class": (
                        "page-text-md-field w-full rounded-default border border-base-300 bg-white px-3 py-2 "
                        "font-mono text-sm leading-relaxed dark:border-base-700 dark:bg-base-900"
                    ),
                    "spellcheck": "false",
                }
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and not self.is_bound:
            self.initial["text_md"] = page_display_text(self.instance)
