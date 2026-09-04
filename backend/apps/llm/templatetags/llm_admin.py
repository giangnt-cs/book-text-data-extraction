from django import template

from apps.llm.constants import OCR_TOOL_CATALOG

register = template.Library()


@register.inclusion_tag("admin/llm/ocr_tool_catalog_table.html")
def ocr_tool_catalog_table():
    return {"ocr_tool_catalog": OCR_TOOL_CATALOG}
