from django import forms
from django.core.validators import RegexValidator


class ManagerReviewForm(forms.Form):
    manager_id = forms.CharField(
        label="퇴직 대상 관리자 ID",
        max_length=9,
        strip=True,
        validators=[
            RegexValidator(
                regex=r"^EMP[0-9]{6}$",
                message="관리자 ID는 EMP와 숫자 6자리 형식으로 입력해 주세요.",
            )
        ],
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "관리자 ID를 입력하세요",
            }
        ),
    )

    def clean_manager_id(self) -> str:
        return self.cleaned_data["manager_id"].strip()
