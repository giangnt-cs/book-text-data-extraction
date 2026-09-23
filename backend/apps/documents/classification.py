"""Danh mục nhóm phân loại sách — seed Admin, không ghi đè bản đã sửa."""

from __future__ import annotations

GROUP_SOURCE = "source"
GROUP_FIELD = "field"
GROUP_LANGUAGE = "language"
GROUP_DOC_TYPE = "doc_type"
GROUP_ERA = "era"

SEEDED_GROUP_SLUGS = (
    GROUP_SOURCE,
    GROUP_FIELD,
    GROUP_LANGUAGE,
    GROUP_DOC_TYPE,
    GROUP_ERA,
)

# (slug, tên, kind, [(mã, tên, mô tả), ...])
CLASSIFICATION_CATALOG: list[tuple[str, str, str, list[tuple[str, str, str]]]] = [
    (
        GROUP_SOURCE,
        "Nguồn y học",
        "main",
        [
            ("M", "Y học cổ truyền Việt Nam", ""),
            ("N", "Y học cổ truyền Trung Quốc", ""),
        ],
    ),
    (
        GROUP_FIELD,
        "Lĩnh vực",
        "main",
        [
            ("A", "Y lý cơ sở", ""),
            ("B", "Chẩn đoán và biện chứng", ""),
            ("C", "Lâm sàng - Nội khoa", ""),
            ("D", "Lâm sàng - Ngoại khoa", ""),
            ("E", "Lâm sàng - Sản phụ khoa", ""),
            ("F", "Lâm sàng - Nhi khoa", ""),
            ("G", "Lâm sàng - Tai, mũi, họng", ""),
            ("H", "Lâm sàng - Mắt", ""),
            ("I", "Lâm sàng - Da liễu", ""),
            ("K", "Lâm sàng - Cơ xương khớp", ""),
            ("L", "Lâm sàng - Thần kinh, tâm thần", ""),
            ("M", "Lâm sàng - Truyền nhiễm", ""),
            ("N", "Lâm sàng - Cấp cứu", ""),
            ("O", "Lâm sàng - Phục hồi chức năng", ""),
            ("P", "Dược liệu - bản thảo", ""),
            ("Q", "Dược tính và chế biến", ""),
            ("R", "Phương tễ - bài thuốc", ""),
            ("S", "Châm cứu - kinh huyệt", ""),
            ("U", "Xoa bóp - bấm huyệt", ""),
            ("V", "Dưỡng sinh", ""),
            ("W", "Thực dưỡng", ""),
            ("X", "Phòng bệnh - sức khoẻ cộng đồng", ""),
            ("Y", "Kết hợp Đông - Tây y", ""),
            ("Z", "Tổng hợp", ""),
        ],
    ),
    (
        GROUP_LANGUAGE,
        "Ngôn ngữ",
        "main",
        [
            ("V", "Vietnamese — Tiếng Việt", ""),
            ("S", "Sino — Tiếng Hán", ""),
            ("E", "English — Tiếng Anh", ""),
            ("N", "Nom — Chữ Nôm", ""),
            ("B", "Bilingual — chữ Hán và chữ Việt", ""),
        ],
    ),
    (
        GROUP_DOC_TYPE,
        "Loại tài liệu",
        "aux",
        [
            ("KD", "Kinh điển – cổ thư", "Y thư kinh điển, tác phẩm nền tảng, nguyên tác cổ"),
            ("CK", "Chuyên khảo", "Nghiên cứu hệ thống về một chủ đề cụ thể"),
            ("GT", "Giáo trình", "Dùng cho giảng dạy, đào tạo"),
            ("ST", "Sổ tay – cẩm nang", "Tra cứu nhanh, hướng dẫn thực hành"),
            ("HD", "Hướng dẫn – phác đồ", "Quy trình, hướng dẫn chẩn đoán hoặc điều trị"),
            ("NC", "Nghiên cứu khoa học", "Luận văn, luận án, đề tài, báo cáo hoặc tuyển tập công trình"),
            ("TA", "Từ điển – tra cứu", "Từ điển thuật ngữ, danh mục, thư mục, bảng tra"),
            ("TT", "Tuyển tập", "Tập hợp bài viết, phương thuốc, ca bệnh hoặc tư liệu"),
            ("NP", "Nghiệm phương", "Tập hợp phương thuốc và kinh nghiệm điều trị"),
            ("CB", "Ca bệnh – bệnh án", "Y án, hồ sơ ca bệnh, phân tích trường hợp"),
            ("LS", "Lịch sử – sử liệu", "Lịch sử y học, tổ chức y tế, quá trình phát triển học thuật"),
            ("TS", "Tiểu sử – danh y", "Cuộc đời, hành trạng và học thuật của thầy thuốc"),
            ("DC", "Dịch thuật – chú giải", "Bản dịch, hiệu đính, khảo dị hoặc chú giải cổ thư"),
            ("PT", "Phổ thông", "Sách phổ biến kiến thức cho công chúng"),
            ("TL", "Tư liệu gốc", "Bản chép tay, bản khắc, tài liệu lưu trữ, bản sao cổ"),
            ("KL", "Kỷ yếu – tuyển luận", "Kỷ yếu hội nghị, tập bài nghiên cứu"),
            ("KH", "Khác", "Chưa phù hợp với các nhóm trên"),
        ],
    ),
    (
        GROUP_ERA,
        "Thời đại",
        "aux",
        [
            ("CD", "Cổ đại", "Trước năm 1000"),
            ("TG", "Trung đại", "1000–1801"),
            ("CDI", "Cận đại", "1802–1945"),
            ("HD", "Hiện đại", "1945–1986"),
            ("DD", "Đương đại", "Từ 1986 đến nay"),
            ("UN", "Không rõ", ""),
        ],
    ),
]

BOOK_CLASS_FIELDS: tuple[tuple[str, str], ...] = (
    ("class_source", GROUP_SOURCE),
    ("class_field", GROUP_FIELD),
    ("class_language", GROUP_LANGUAGE),
    ("class_doc_type", GROUP_DOC_TYPE),
    ("class_era", GROUP_ERA),
)


def _format_stat(value) -> str:
    if value is None:
        return "0"
    number = float(value)
    if abs(number - round(number)) < 1e-9:
        return f"{int(round(number)):,}".replace(",", ".")
    return f"{number:,.1f}".replace(",", "X").replace(".", ",").replace("X", ".")


TOKEN_BIN_SIZE = 50


def _token_histogram(bin_size: int = TOKEN_BIN_SIZE) -> list[dict]:
    from django.db.models import Count, F, IntegerField, Value
    from django.db.models.functions import Cast, Floor

    from apps.dataset.models import SourceChunk

    rows = (
        SourceChunk.objects.annotate(
            bucket=Cast(
                Floor(F("token_count") / Value(float(bin_size))) * Value(bin_size),
                IntegerField(),
            )
        )
        .values("bucket")
        .annotate(n=Count("id"))
        .order_by("bucket")
    )
    counts = {int(row["bucket"] or 0): int(row["n"] or 0) for row in rows}
    if not counts:
        return []
    hi = max(counts)
    bin_count = hi // bin_size + 1
    starts = range(0, hi + bin_size, bin_size) if bin_count <= 60 else sorted(counts)
    bins = []
    for start in starts:
        end = start + bin_size - 1
        n = counts.get(start, 0)
        bins.append(
            {
                "start": start,
                "end": end,
                "label": f"{start}–{end}",
                "count": n,
                "count_display": _format_stat(n),
            }
        )
    return bins


def _chunk_token_stats() -> dict:
    from django.db.models import Aggregate, Avg, Count, FloatField, Sum

    from apps.dataset.models import SourceChunk

    class Median(Aggregate):
        function = "PERCENTILE_CONT"
        name = "Median"
        template = "%(function)s(0.5) WITHIN GROUP (ORDER BY %(expressions)s)"
        output_field = FloatField()

    agg = SourceChunk.objects.aggregate(
        chunk_count=Count("id"),
        token_total=Sum("token_count"),
        token_avg=Avg("token_count"),
        token_median=Median("token_count"),
    )
    return {
        "chunk_count": int(agg["chunk_count"] or 0),
        "chunk_count_display": _format_stat(agg["chunk_count"] or 0),
        "token_total": int(agg["token_total"] or 0),
        "token_total_display": _format_stat(agg["token_total"] or 0),
        "token_avg_display": _format_stat(agg["token_avg"] or 0),
        "token_median_display": _format_stat(agg["token_median"] or 0),
        "token_bin_size": TOKEN_BIN_SIZE,
        "token_bins": _token_histogram(),
    }


def build_book_classification_stats() -> dict:
    """Số sách theo từng thuật ngữ, tách riêng từng nhóm phân loại."""
    from django.db.models import Count

    from apps.dataset.models import ChunkingBatchStatus as DatasetChunkingBatchStatus
    from apps.dataset.models import SourceChunkingBatch
    from apps.documents.models import (
        Book,
        BookStatus,
        ClassificationGroup,
        ClassificationTerm,
        CleaningCoverage,
        OCR_READY_STATUSES,
        Page,
    )

    total = Book.objects.count()
    total_pages = Page.objects.count()
    ocr_count = Book.objects.filter(status__in=OCR_READY_STATUSES).count()
    cleaned_count = Book.objects.filter(
        cleaning_coverage__in=[CleaningCoverage.PARTIAL, CleaningCoverage.FULL]
    ).count()
    approved_count = Book.objects.filter(status=BookStatus.APPROVED).count()
    chunked_count = (
        SourceChunkingBatch.objects.filter(status=DatasetChunkingBatchStatus.DONE)
        .values("document_id")
        .distinct()
        .count()
    )
    tokens = _chunk_token_stats()
    groups: list[dict] = []

    for field_name, slug in BOOK_CLASS_FIELDS:
        group = ClassificationGroup.objects.filter(slug=slug).first()
        if not group:
            continue

        raw_counts: dict = {}
        for row in Book.objects.values(field_name).annotate(n=Count("id")):
            raw_counts[row[field_name]] = int(row["n"] or 0)

        bars: list[dict] = []
        classified = 0
        for term in ClassificationTerm.objects.filter(group=group).order_by("sort_order", "code"):
            n = int(raw_counts.get(term.pk, 0) or 0)
            classified += n
            bars.append(
                {
                    "id": str(term.pk),
                    "code": term.code,
                    "name": term.name,
                    "count": n,
                    "unclassified": False,
                }
            )

        unclassified = int(raw_counts.get(None, 0) or 0)
        bars.append(
            {
                "id": "empty",
                "code": "—",
                "name": "Chưa phân loại",
                "count": unclassified,
                "unclassified": True,
            }
        )

        groups.append(
            {
                "name": group.name,
                "slug": slug,
                "kind": group.get_kind_display(),
                "field": field_name,
                "bars": bars,
                "classified": classified,
                "unclassified": unclassified,
                "horizontal": len(bars) > 8,
            }
        )

    return {
        "total": total,
        "total_pages": total_pages,
        "ocr_count": ocr_count,
        "cleaned_count": cleaned_count,
        "approved_count": approved_count,
        "chunked_count": chunked_count,
        **tokens,
        "groups": groups,
    }
