from django.contrib.auth.models import Group
from django.core.management.base import BaseCommand

from second_project.presentation.permissions import HR_GROUP, TEAM_MANAGER_GROUP


class Command(BaseCommand):
    help = "인사 요청 검토 가이드의 기본 Django 역할 그룹을 준비합니다."

    def handle(self, *args, **options) -> None:
        for group_name in (HR_GROUP, TEAM_MANAGER_GROUP):
            _, created = Group.objects.get_or_create(name=group_name)
            state = "생성" if created else "기존 유지"
            self.stdout.write(f"- {group_name}: {state}")
        self.stdout.write(
            self.style.SUCCESS(
                "역할 그룹 준비 완료. 사용자 배정은 관리자 페이지에서 수행하세요."
            )
        )
