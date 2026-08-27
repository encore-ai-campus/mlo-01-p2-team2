from __future__ import annotations

import base64
import math
from collections import Counter
from collections.abc import Callable, Mapping
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Protocol
from uuid import UUID


class StandardizationError(ValueError):
    """값을 안전한 JSON 형태로 바꿀 수 없을 때 발생한다."""


class Standardizer(Protocol):
    """표준화 구현체가 따라야 하는 최소 규약이다."""

    def standardize(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """원본 문서를 변환하며 처리 불가 데이터는 StandardizationError를 낸다."""
        ...


class CommonStandardizer:
    """필드 구조를 유지하며 Python/BSON 값을 JSON 호환 값으로 바꾼다."""

    def __init__(
        self,
        custom_converters: Mapping[type[Any], Callable[[Any], Any]] | None = None,
    ) -> None:
        """필요하면 특정 타입의 사용자 정의 변환 함수를 등록한다."""

        self._custom_converters = dict(custom_converters or {})
        self.metrics: Counter[str] = Counter(
            column_renamed=0,
            type_converted=0,
        )

    def standardize(self, document: Mapping[str, Any]) -> dict[str, Any]:
        """문서 전체를 재귀적으로 순회해 JSON 호환 형태로 반환한다."""

        if not isinstance(document, Mapping):
            raise StandardizationError("문서가 object 형태가 아닙니다.")
        return self._convert_mapping(document, path="$", depth=0)

    def _convert(self, value: Any, *, path: str, depth: int) -> Any:
        """값의 타입에 맞는 변환을 선택하고 중첩 값까지 처리한다."""

        if depth > 100:
            raise StandardizationError(f"{path}: 중첩 깊이가 100을 초과했습니다.")

        for value_type, converter in self._custom_converters.items():
            if isinstance(value, value_type):
                self.metrics["type_converted"] += 1
                return self._convert(converter(value), path=path, depth=depth + 1)

        bson_value = self._convert_known_bson(value)
        if bson_value is not _UNHANDLED:
            self.metrics["type_converted"] += 1
            return bson_value

        if value is None or isinstance(value, (str, bool, int)):
            return value

        if isinstance(value, float):
            if not math.isfinite(value):
                raise StandardizationError(f"{path}: NaN 또는 Infinity는 지원하지 않습니다.")
            return value

        if isinstance(value, datetime):
            self.metrics["type_converted"] += 1
            normalized = value
            if normalized.tzinfo is None:
                normalized = normalized.replace(tzinfo=timezone.utc)
            normalized = normalized.astimezone(timezone.utc)
            return normalized.isoformat().replace("+00:00", "Z")

        if isinstance(value, date):
            self.metrics["type_converted"] += 1
            return value.isoformat()

        if isinstance(value, Decimal):
            self.metrics["type_converted"] += 1
            return str(value)

        if isinstance(value, UUID):
            self.metrics["type_converted"] += 1
            return str(value)

        if isinstance(value, (bytes, bytearray, memoryview)):
            self.metrics["type_converted"] += 1
            return {
                "$binary": base64.b64encode(bytes(value)).decode("ascii"),
                "subtype": "00",
            }

        if isinstance(value, Mapping):
            return self._convert_mapping(value, path=path, depth=depth + 1)

        if isinstance(value, (list, tuple)):
            return [
                self._convert(item, path=f"{path}[{index}]", depth=depth + 1)
                for index, item in enumerate(value)
            ]

        raise StandardizationError(
            f"{path}: 지원하지 않는 타입입니다: {type(value).__module__}.{type(value).__name__}"
        )

    def _convert_mapping(
        self,
        value: Mapping[Any, Any],
        *,
        path: str,
        depth: int,
    ) -> dict[str, Any]:
        """객체의 필드명을 문자열로 통일하고 각 값을 재귀 변환한다."""

        converted: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key in converted:
                raise StandardizationError(f"{path}: 문자열 변환 후 필드명이 중복됩니다: {key}")
            if raw_key != key:
                self.metrics["column_renamed"] += 1
            child_path = f"{path}.{key}"
            converted[key] = self._convert(raw_value, path=child_path, depth=depth + 1)
        return converted

    def _convert_known_bson(self, value: Any) -> Any:
        """ObjectId, Decimal128 등 MongoDB 고유 타입을 변환한다."""

        # pymongo 없이도 데모와 코어 테스트를 실행하기 위해 타입 이름을 확인한다.
        module = type(value).__module__
        name = type(value).__name__
        if not module.startswith("bson"):
            return _UNHANDLED

        if name == "ObjectId":
            return str(value)
        if name == "Decimal128" and hasattr(value, "to_decimal"):
            return str(value.to_decimal())
        if name == "Binary":
            subtype = getattr(value, "subtype", 0)
            return {
                "$binary": base64.b64encode(bytes(value)).decode("ascii"),
                "subtype": f"{subtype:02x}",
            }
        if name == "Timestamp":
            return {
                "$timestamp": {
                    "time": int(getattr(value, "time")),
                    "increment": int(getattr(value, "inc")),
                }
            }
        if name == "Regex":
            return {
                "$regex": str(getattr(value, "pattern", "")),
                "options": str(getattr(value, "flags", "")),
            }
        if name == "Code":
            return str(value)
        if name == "MinKey":
            return {"$minKey": 1}
        if name == "MaxKey":
            return {"$maxKey": 1}

        return _UNHANDLED


_UNHANDLED = object()
