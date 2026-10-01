
import json
import os

CONFIG = "config.json"

def home() -> str:
    e = os.environ.get("NIECHE_HOME") or os.environ.get("NICAI_HOME")
    root = e if e else os.path.expanduser("~/.nieche-emu")
    if not e:
        _migrate(os.path.expanduser("~/.nicai-emu"), root)
    os.makedirs(root, exist_ok=True)
    return root

def _migrate(old: str, new: str) -> None:

    if not os.path.isdir(old) or os.path.exists(new):
        return
    try:
        os.rename(old, new)
    except OSError:
        pass

def _sub(*parts) -> str:
    p = os.path.join(home(), *parts)
    os.makedirs(p, exist_ok=True)
    return p

def saves_dir(module: str) -> str:

    return _sub("saves", safe(module))

def fs_dir(module: str) -> str:

    return _sub("fs", safe(module))

def audio_dir(module: str) -> str:

    return _sub("audio", safe(module))

def safe(name: str) -> str:

    out = "".join("_" if c in '/\\:*?"<>|' else c for c in (name or "unnamed"))
    return out.strip() or "unnamed"

def config_path() -> str:
    return os.path.join(home(), CONFIG)

def load_config() -> dict:
    try:
        with open(config_path(), encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}

def save_config(d: dict) -> None:
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
        f.write("\n")

def describe() -> str:
    return (f"数据根目录：{home()}\n"
            f"  存档  {os.path.join(home(), 'saves')}/<模块名>/\n"
            f"  文件  {os.path.join(home(), 'fs')}/<模块名>/\n"
            f"  音频  {os.path.join(home(), 'audio')}/<模块名>/\n"
            f"  配置  {config_path()}")

if __name__ == "__main__":
    print(describe())
