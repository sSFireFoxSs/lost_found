"""접수 스키마. 필수 여부·형식·추가 질문을 여기에서 함께 정의한다."""
from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, create_model, field_validator

from agents.categories import CATEGORIES

UNKNOWN_ANSWERS = {"모름", "몰라요", "기억 안 나요", "없음", "없어요", "건너뛰기", "확인 불가"}
Category = Literal[tuple(CATEGORIES)]


class LostReport(BaseModel):
    """확정할 신고의 기준. 기본값 없는 항목은 필수 응답, None 기본값은 선택이다."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    item_name: str = Field(min_length=1, max_length=100, title="물품",
        description="잃어버린 물건의 종류 또는 이름",
        json_schema_extra={"question": "어떤 물건을 잃어버리셨나요? 예: 지갑, 아이폰"})
    lost_at: str = Field(min_length=1, max_length=150, title="분실 시점",
        description="분실 날짜와 시간 또는 대략적인 범위. 모른다고 답하면 확인 불가",
        json_schema_extra={"question": "언제 잃어버리셨나요? 대략적인 날짜와 시간도 괜찮습니다. 모르면 '모름'이라고 해주세요."})
    lost_place: str = Field(min_length=1, max_length=200, title="분실 장소",
        description="분실 장소, 마지막으로 본 곳 또는 이동 경로",
        json_schema_extra={"question": "어디에서 잃어버리셨나요? 마지막으로 본 장소나 이동 경로도 좋습니다. 모르면 '모름'이라고 해주세요."})
    color: str = Field(min_length=1, max_length=100, title="색상",
        description="물품의 색상",
        json_schema_extra={"question": "물건의 색상은 무엇인가요? 기억나지 않으면 '모름'이라고 해주세요."})
    public_features: str = Field(min_length=1, max_length=400, title="외관 특징",
        description="브랜드·모양·재질·크기 등 외관 특징. 개인 식별정보나 내용물은 제외",
        json_schema_extra={"question": "브랜드·모양·크기처럼 겉으로 구별할 특징이 있나요? 없으면 '없음'이라고 해주세요."})
    private_features: str = Field(min_length=1, max_length=400, title="본인확인용 특징",
        description="내용물·각인처럼 본인만 아는 비공개 특징",
        json_schema_extra={"question": "내용물·각인처럼 본인만 아는 특징이 있나요? 비공개로 저장합니다. 전체 카드번호·비밀번호는 쓰지 마세요. 없으면 '없음'이라고 해주세요."})
    category: Category | None = Field(default=None, title="분류", description="물품 대분류",
        json_schema_extra={"question": "물품 분류를 확인할 수 있도록 어떤 종류의 물건인지 다시 알려주세요."})
    brand: str | None = Field(default=None, min_length=1, max_length=100, title="브랜드",
        description="언급한 경우에만 브랜드 입력",
        json_schema_extra={"question": "브랜드를 다시 알려주세요. 모르면 '모름'이라고 해주세요."})

    @field_validator("item_name")
    @classmethod
    def require_item_type(cls, value):
        if value in UNKNOWN_ANSWERS or value == "미상":
            raise ValueError("물건의 종류는 필요합니다.")
        return value


def make_extraction_schema():
    """한 메시지에 모든 값이 없을 수 있으므로 추출용 모델은 전부 nullable로 만든다."""
    fields = {}
    for name, definition in LostReport.model_fields.items():
        field = deepcopy(definition)
        field.default = None
        field.json_schema_extra = None
        fields[name] = (definition.annotation | None, field)
    return create_model("ExtractedFields", __config__=LostReport.model_config, **fields)


ExtractedFields = make_extraction_schema()
QUESTIONS = {name: field.json_schema_extra["question"] for name, field in LostReport.model_fields.items()}
