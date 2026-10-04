from __future__ import annotations

from django.contrib.auth.models import Group, Permission
from django.db.models import Q
from django.db.models.signals import post_migrate
from django.dispatch import receiver

ROLE_ADMIN = "admin"
ROLE_OPERATOR = "operator"


def _ensure_group(name: str) -> Group:
    g, _ = Group.objects.get_or_create(name=name)
    return g


@receiver(post_migrate)
def ensure_default_roles(sender, **kwargs):
    admin = _ensure_group(ROLE_ADMIN)
    operator = _ensure_group(ROLE_OPERATOR)

    admin.permissions.set(Permission.objects.all())

    project_perms = Permission.objects.filter(
        content_type__app_label__in={"documents", "dataset", "layout", "llm", "sharing"}
    )
    operator.permissions.set(
        project_perms.filter(Q(codename__startswith="add_") | Q(codename__startswith="change_") | Q(codename__startswith="view_"))
    )
