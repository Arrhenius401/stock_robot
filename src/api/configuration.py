"""受控配置读取与更新接口。"""
import copy
import math
from collections.abc import Callable
from json import JSONDecodeError
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from api.runtime import RuntimeManager
from utils.config import Config

_CREDENTIAL_PATHS = {
    "llm.api_key": ("llm", "api_key"),
    "push.email.smtp_password": ("push", "email", "smtp_password"),
    "push.wecom.secret": ("push", "wecom", "secret"),
}

_EDITABLE_FIELDS: dict[str, Any] = {
    "llm": {
        "provider": None,
        "model": None,
        "enabled": None,
        "api_key": None,
        "base_url": None,
        "temperature": None,
        "max_tokens": None,
        "retry_times": None,
        "timeout_seconds": None,
    },
    "data": {
        "cache_ttl": {"daily": None, "quarterly": None, "news": None},
        "disclaimer_accepted": None,
    },
    "api": {"host": None, "port": None},
    "push": {
        "enabled": None,
        "max_symbols_per_subscription": None,
        "email": {
            "smtp_host": None,
            "smtp_port": None,
            "smtp_user": None,
            "smtp_password": None,
            "to_addr": None,
        },
        "wecom": {"corp_id": None, "agent_id": None, "secret": None, "to_user": None},
    },
    "signal": {
        "thresholds": {"attack": None, "watch": None},
        "actions": {
            "attack": {"action": None, "position": None},
            "watch": {"action": None, "position": None},
            "defend": {"action": None, "position": None},
        },
    },
}

_NONEMPTY_STRING_PATHS = {
    ("llm", "model"),
    ("api", "host"),
    ("push", "email", "smtp_host"),
    ("push", "email", "smtp_user"),
    ("push", "email", "to_addr"),
    ("push", "wecom", "corp_id"),
    ("push", "wecom", "agent_id"),
    ("push", "wecom", "to_user"),
    ("signal", "actions", "attack", "action"),
    ("signal", "actions", "attack", "position"),
    ("signal", "actions", "watch", "action"),
    ("signal", "actions", "watch", "position"),
    ("signal", "actions", "defend", "action"),
    ("signal", "actions", "defend", "position"),
}

_CREDENTIAL_PATH_SET = set(_CREDENTIAL_PATHS.values())


def mask_secret(value: str) -> str:
    """按长度掩码密钥，避免公开响应泄露内容。"""
    length = len(value)
    if length <= 8:
        return "*" * length
    visible = 4 if length <= 11 else 5
    return f"{value[:visible]}{'*' * (length - visible * 2)}{value[-visible:]}"


def _get_value(data: dict[str, Any], path: tuple[str, ...]) -> Any:
    value: Any = data
    for key in path:
        value = value[key]
    return value


def _set_value(data: dict[str, Any], path: tuple[str, ...], value: Any) -> None:
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value


def _pick_editable(data: dict[str, Any], fields: dict[str, Any]) -> dict[str, Any]:
    picked: dict[str, Any] = {}
    for key, nested_fields in fields.items():
        value = data[key]
        picked[key] = (
            _pick_editable(value, nested_fields)
            if isinstance(nested_fields, dict)
            else copy.deepcopy(value)
        )
    return picked


def safe_config(config: Config) -> dict[str, Any]:
    """生成不含明文凭据的可编辑配置快照。"""
    result = _pick_editable(config.data, _EDITABLE_FIELDS)
    for path in _CREDENTIAL_PATH_SET:
        value = str(_get_value(config.data, path))
        _set_value(result, path, {"configured": bool(value), "masked": mask_secret(value)})
    return result


def _validation_error(path: tuple[str, ...], message: str) -> None:
    label = ".".join(path)
    raise HTTPException(status_code=422, detail=f"{label}: {message}")


def _require_string(value: Any, path: tuple[str, ...], *, nonempty: bool = False) -> str:
    if not isinstance(value, str):
        _validation_error(path, "必须是字符串")
    normalized = value.strip()
    if nonempty and not normalized:
        _validation_error(path, "不能为空")
    return normalized


def _require_integer(
    value: Any,
    path: tuple[str, ...],
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    if type(value) is not int:
        _validation_error(path, "必须是整数")
    if value < minimum or (maximum is not None and value > maximum):
        _validation_error(path, "数值超出允许范围")
    return value


def _require_number(
    value: Any,
    path: tuple[str, ...],
    *,
    minimum: float,
    maximum: float,
) -> float | int:
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        _validation_error(path, "必须是数字")
    if not math.isfinite(float(value)):
        _validation_error(path, "必须是有限数字")
    if value < minimum or value > maximum:
        _validation_error(path, "数值超出允许范围")
    return value


def _validate_leaf(value: Any, path: tuple[str, ...]) -> Any:
    if path in _CREDENTIAL_PATH_SET:
        if not isinstance(value, str):
            _validation_error(path, "必须是字符串")
        return value
    if path == ("llm", "provider"):
        provider = _require_string(value, path, nonempty=True)
        if provider not in {"openai", "claude"}:
            _validation_error(path, "仅支持 openai 或 claude")
        return provider
    if path in _NONEMPTY_STRING_PATHS:
        return _require_string(value, path, nonempty=True)
    if path == ("llm", "base_url"):
        return _require_string(value, path)
    if path in {
        ("llm", "enabled"),
        ("data", "disclaimer_accepted"),
        ("push", "enabled"),
    }:
        if type(value) is not bool:
            _validation_error(path, "必须是布尔值")
        return value
    if path == ("llm", "temperature"):
        return _require_number(value, path, minimum=0, maximum=2)
    integer_ranges: dict[tuple[str, ...], tuple[int, int | None]] = {
        ("llm", "max_tokens"): (1, 128000),
        ("llm", "retry_times"): (0, 10),
        ("llm", "timeout_seconds"): (1, 600),
        ("data", "cache_ttl", "daily"): (1, None),
        ("data", "cache_ttl", "quarterly"): (1, None),
        ("data", "cache_ttl", "news"): (1, None),
        ("api", "port"): (1, 65535),
        ("push", "max_symbols_per_subscription"): (1, None),
        ("push", "email", "smtp_port"): (1, None),
    }
    if path in integer_ranges:
        minimum, maximum = integer_ranges[path]
        return _require_integer(value, path, minimum=minimum, maximum=maximum)
    if path in {("signal", "thresholds", "attack"), ("signal", "thresholds", "watch")}:
        return _require_number(value, path, minimum=0, maximum=10)
    _validation_error(path, "不支持的配置字段")


def _validate_update(
    values: Any,
    fields: dict[str, Any],
    path: tuple[str, ...] = (),
) -> dict[str, Any]:
    if not isinstance(values, dict):
        _validation_error(path or ("config",), "必须是对象")
    result: dict[str, Any] = {}
    for key, value in values.items():
        current_path = (*path, key)
        if key not in fields:
            _validation_error(current_path, "不允许修改")
        nested_fields = fields[key]
        if isinstance(nested_fields, dict):
            result[key] = _validate_update(value, nested_fields, current_path)
            continue
        normalized = _validate_leaf(value, current_path)
        if current_path in _CREDENTIAL_PATH_SET and normalized == "":
            continue
        result[key] = normalized
    return result


def _deep_merge(base: dict[str, Any], values: dict[str, Any]) -> None:
    for key, value in values.items():
        if isinstance(base.get(key), dict) and isinstance(value, dict):
            _deep_merge(base[key], value)
        else:
            base[key] = value


def _validate_threshold_relation(current: dict[str, Any], update: dict[str, Any]) -> None:
    candidate = copy.deepcopy(current)
    _deep_merge(candidate, update)
    thresholds = candidate["signal"]["thresholds"]
    watch = thresholds["watch"]
    attack = thresholds["attack"]
    if not 0 < watch < attack <= 10:
        _validation_error(("signal", "thresholds"), "必须满足 0 < watch < attack <= 10")


def _restart_required(update: dict[str, Any]) -> bool:
    return bool(update.get("api", {}).keys() & {"host", "port"})


def _has_hot_update(update: dict[str, Any]) -> bool:
    """判断更新中是否包含可在当前进程生效的字段。"""
    return any(key != "api" for key in update)


def _config_for_runtime(
    config: Config,
    before_update: dict[str, Any],
    update: dict[str, Any],
) -> Config:
    """为热重载恢复监听字段，避免运行时快照误用待重启的绑定配置。"""
    runtime_config = Config(config_dir=config.config_dir)
    runtime_config.data = copy.deepcopy(config.data)
    for key in update.get("api", {}):
        runtime_config.data["api"][key] = before_update["api"][key]
    return runtime_config


def _safe_reload_error(error: str | None, config: Config) -> str:
    """清理重载错误中的已配置凭据，防止错误边界泄露密钥。"""
    message = error or "运行时热更新失败"
    for path in _CREDENTIAL_PATH_SET:
        secret = str(_get_value(config.data, path))
        if secret:
            message = message.replace(secret, "***")
    return message


def _is_loopback_client(request: Request) -> bool:
    """完整凭据只交给本机回环请求，防止远程页面读取。"""
    return request.client is not None and request.client.host in {"127.0.0.1", "::1"}


def create_configuration_router(
    config_factory: Callable[[], Config] | None = None,
    runtime: RuntimeManager | None = None,
) -> APIRouter:
    """创建使用同一份项目配置文件的受控配置路由。"""
    router = APIRouter()

    def get_config() -> Config:
        return config_factory() if config_factory is not None else Config()

    @router.get("/api/v1/config")
    async def get_public_config():
        config = get_config()
        return JSONResponse({
            "config": safe_config(config),
            "paths": {
                "state_dir": str(config.config_dir),
                "config_file": str(config.config_dir / "config.yaml"),
            },
        })

    @router.get("/api/v1/config/credentials/{key}")
    async def get_credential(key: str, request: Request):
        path = _CREDENTIAL_PATHS.get(key)
        if path is None:
            raise HTTPException(status_code=404, detail="凭据不存在")
        if not _is_loopback_client(request):
            raise HTTPException(status_code=403, detail="仅允许本机读取完整凭据")
        return JSONResponse({"value": _get_value(get_config().data, path)})

    @router.put("/api/v1/config")
    async def update_config(request: Request):
        try:
            body = await request.json()
        except JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="body: JSON 格式无效") from exc
        if not isinstance(body, dict) or set(body) != {"config"}:
            _validation_error(("body",), "只能包含 config")
        config = get_config()
        update = _validate_update(body["config"], _EDITABLE_FIELDS)
        _validate_threshold_relation(config.data, update)
        before_update = copy.deepcopy(config.data)
        config.update(update)
        restart_required = _restart_required(update)
        applied = False
        reload_error: str | None = None
        if _has_hot_update(update):
            if runtime is None:
                reload_error = "运行时未连接，配置将在下次启动时生效"
            else:
                result = runtime.reload(_config_for_runtime(config, before_update, update))
                applied = result.applied
                if not result.applied:
                    reload_error = _safe_reload_error(result.error, config)

        response: dict[str, Any] = {
            "config": safe_config(config),
            "paths": {
                "state_dir": str(config.config_dir),
                "config_file": str(config.config_dir / "config.yaml"),
            },
            "persisted": True,
            "applied": applied,
            "restart_required": restart_required,
        }
        if reload_error is not None:
            response["reload_error"] = reload_error
        return JSONResponse(response)

    return router
