"""Small shared helpers: atomic IO, locking, hashing, time, slugs."""
from __future__ import annotations

import contextlib
import datetime as _dt
import hashlib
import json
import os
import re
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Iterator, Optional

try:  # POSIX only (macOS/Linux). Windows is not a target.
    import fcntl  # type: ignore
except Exception:  # pragma: no cover
    fcntl = None  # type: ignore


def now_iso() -> str:
    return _dt.datetime.now().astimezone().replace(microsecond=0).isoformat()


def today() -> str:
    return _dt.date.today().isoformat()


def read_text(path: Path, default: str = "") -> str:
    try:
        return Path(path).read_text(encoding="utf-8")
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError):
        return default


def write_text_atomic(path: Path, text: str) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def read_json(path: Path, default: Any = None) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except (FileNotFoundError, NotADirectoryError):
        return default
    except ValueError:              # not JSON, or not even text (UnicodeDecodeError): the same as "nothing usable"
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    write_text_atomic(Path(path), json.dumps(data, indent=2, ensure_ascii=False, sort_keys=False) + "\n")


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    """Exclusive advisory lock on <path>.lock (no-op where fcntl is unavailable)."""
    lock_path = Path(str(path) + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = open(lock_path, "a+")
    try:
        if fcntl is not None:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            with contextlib.suppress(Exception):
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        fh.close()


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path, limit: Optional[int] = None) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with open(path, "rb") as fh:
            read = 0
            while True:
                chunk = fh.read(1 << 16)
                if not chunk:
                    break
                h.update(chunk)
                read += len(chunk)
                if limit is not None and read > limit:
                    return None
        return h.hexdigest()
    except (FileNotFoundError, IsADirectoryError, PermissionError, NotADirectoryError):
        return None


_TR_MAP = str.maketrans({"ı": "i", "İ": "i", "ş": "s", "Ş": "s", "ğ": "g", "Ğ": "g",
                         "ü": "u", "Ü": "u", "ö": "o", "Ö": "o", "ç": "c", "Ç": "c"})


def slugify(text: str, max_len: int = 40) -> str:
    text = (text or "").translate(_TR_MAP)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    if len(text) > max_len:
        text = text[:max_len].rstrip("-")
    return text or "feature"


def norm_lower(text: str) -> str:
    """Lower-case that behaves for Turkish input (drops the combining dot of İ)."""
    return (text or "").lower().replace("̇", "")


def tail_lines(text: str, n: int = 40, max_chars: int = 4000) -> str:
    lines = text.splitlines()[-n:]
    out = "\n".join(lines)
    if len(out) > max_chars:
        out = out[-max_chars:]
    return out


def is_relative_to(path: Path, base: Path) -> bool:
    try:
        Path(path).relative_to(base)
        return True
    except ValueError:
        return False


def resolve_path(p: str, cwd: Optional[str] = None) -> Path:
    path = Path(os.path.expanduser(p))
    if not path.is_absolute():
        path = Path(cwd or os.getcwd()) / path
    # resolve symlinks where possible, but never fail on non-existent files
    try:
        return path.resolve(strict=False)
    except Exception:  # pragma: no cover
        return Path(os.path.abspath(str(path)))
