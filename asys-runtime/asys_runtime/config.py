from pathlib import Path

from .files import read_json, validate_name


def load_config(path, *, environment=False):
    path = Path(path).expanduser().resolve()
    return parse_config(read_json(path), directory=path.parent, environment=environment)


def parse_config(config, *, directory, environment=False):
    fields = {"version", "types", "name", "description", "egress"} if environment else {"version", "types"}
    if not isinstance(config, dict) or set(config) - fields or type(config.get("version")) is not int or config["version"] != 1:
        raise ValueError("runtime configuration must contain version: 1 and types")
    if environment and type(config.get("egress", False)) is not bool:
        raise ValueError("environment egress must be true or false")
    types = config.get("types")
    if not isinstance(types, dict) or not types:
        raise ValueError("runtime configuration needs at least one job type")
    return validate_types(types, directory=directory)


def validate_types(types, *, directory=None):
    result = {}
    for name, raw in types.items():
        validate_name("job type", name)
        if not isinstance(raw, dict) or set(raw) - {"command", "env", "timeout"}:
            raise ValueError(f"{name}: supported settings are command, env, timeout")
        command = raw.get("command")
        if not isinstance(command, list) or not command or any(not isinstance(v, str) or "\0" in v for v in command) or not command[0]:
            raise ValueError(f"{name}: command must be a nonempty argument list")
        command = list(command)
        if directory is not None and "/" in command[0] and not Path(command[0]).is_absolute():
            command[0] = str(directory / command[0])
        env = raw.get("env", {})
        if not isinstance(env, dict) or any(not isinstance(k, str) or not k or "=" in k or "\0" in k or not isinstance(v, str) or "\0" in v for k, v in env.items()):
            raise ValueError(f"{name}: env must map variable names to strings")
        timeout = raw.get("timeout")
        if timeout is not None and (type(timeout) not in (float, int) or not 0 < timeout <= 31536000):
            raise ValueError(f"{name}: timeout must be a positive number of seconds")
        result[name] = {"command": command, "env": dict(env), "timeout": timeout}
    return result
