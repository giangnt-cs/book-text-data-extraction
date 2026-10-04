from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone


class CreateShareForm(forms.Form):
    expires_at = forms.DateTimeField(
        label="Hết hạn",
        input_formats=["%Y-%m-%dT%H:%M", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S"],
    )
    allow_pdf = forms.BooleanField(label="PDF", required=False)
    allow_md = forms.BooleanField(label="Markdown", required=False)
    password = forms.CharField(label="Mật khẩu", min_length=8, max_length=64, strip=True)
    note = forms.CharField(label="Ghi chú", max_length=255, required=False, strip=True)

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("allow_pdf") and not cleaned.get("allow_md"):
            raise ValidationError("Chọn ít nhất một loại nội dung: PDF hoặc Markdown.")
        expires = cleaned.get("expires_at")
        if expires is not None and timezone.is_naive(expires):
            expires = timezone.make_aware(expires, timezone.get_current_timezone())
            cleaned["expires_at"] = expires
        if expires and expires <= timezone.now():
            self.add_error("expires_at", "Thời hạn phải ở tương lai.")
        return cleaned


class ShareGateForm(forms.Form):
    name = forms.CharField(label="Họ tên", min_length=2, max_length=120, strip=True)
    email = forms.EmailField(label="Email")
    password = forms.CharField(label="Mật khẩu", max_length=64, widget=forms.PasswordInput(render_value=False))

    def clean_name(self) -> str:
        return " ".join((self.cleaned_data.get("name") or "").split())

    def clean_email(self) -> str:
        return (self.cleaned_data.get("email") or "").strip().lower()
