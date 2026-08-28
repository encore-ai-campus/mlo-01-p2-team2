from __future__ import annotations

from django.contrib.auth.models import AbstractBaseUser


HR_GROUP = "hr_reviewer"
TEAM_MANAGER_GROUP = "team_manager"


def is_hr_reviewer(user: AbstractBaseUser) -> bool:
    return bool(
        user.is_authenticated
        and (
            user.is_superuser
            or user.groups.filter(name=HR_GROUP).exists()
        )
    )


def is_team_manager(user: AbstractBaseUser) -> bool:
    return bool(
        user.is_authenticated
        and user.groups.filter(name=TEAM_MANAGER_GROUP).exists()
    )


def can_review(user: AbstractBaseUser) -> bool:
    return is_hr_reviewer(user) or is_team_manager(user)
