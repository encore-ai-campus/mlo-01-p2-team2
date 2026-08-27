from __future__ import annotations

from pathlib import Path
from typing import Any


class YamlLoadError(ValueError):
    """YAML 파일이 안전하게 읽히지 않거나 구조가 잘못됐을 때 발생한다."""


def load_yaml_file(
    path: str | Path,
    *,
    max_bytes: int = 50 * 1024 * 1024,
) -> Any:
    """중복 키를 거부하는 SafeLoader로 YAML 파일을 읽는다."""

    yaml_path = Path(path)
    try:
        size = yaml_path.stat().st_size
    except OSError as error:
        raise YamlLoadError(f"YAML 파일을 읽을 수 없습니다: {yaml_path}: {error}") from error

    if size > max_bytes:
        raise YamlLoadError(
            f"YAML 파일이 허용 크기 {max_bytes}바이트를 초과했습니다: {size}바이트"
        )

    try:
        import yaml
        from yaml.constructor import ConstructorError
        from yaml.resolver import BaseResolver
    except ImportError as error:
        raise RuntimeError(
            "YAML 처리를 위해 PyYAML이 필요합니다. `pip install -e .`로 설치해 주세요."
        ) from error

    class UniqueKeySafeLoader(yaml.SafeLoader):
        """같은 mapping 안의 중복 키를 조용히 덮어쓰지 않는 로더다."""

    def construct_unique_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
        loader.flatten_mapping(node)
        mapping: dict[Any, Any] = {}
        for key_node, value_node in node.value:
            key = loader.construct_object(key_node, deep=deep)
            try:
                duplicate = key in mapping
            except TypeError as error:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    "YAML mapping 키는 해시 가능한 스칼라 값이어야 합니다.",
                    key_node.start_mark,
                ) from error
            if duplicate:
                raise ConstructorError(
                    "while constructing a mapping",
                    node.start_mark,
                    f"중복 YAML 키를 사용할 수 없습니다: {key!r}",
                    key_node.start_mark,
                )
            mapping[key] = loader.construct_object(value_node, deep=deep)
        return mapping

    UniqueKeySafeLoader.add_constructor(
        BaseResolver.DEFAULT_MAPPING_TAG,
        construct_unique_mapping,
    )

    try:
        with yaml_path.open("r", encoding="utf-8") as file:
            return yaml.load(file, Loader=UniqueKeySafeLoader)
    except (OSError, yaml.YAMLError) as error:
        raise YamlLoadError(f"YAML 파일 형식이 올바르지 않습니다: {yaml_path}: {error}") from error
