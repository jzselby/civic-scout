"""Registry of sources. To add one, write a class following base.Source and list it here."""

from .pmn import PublicNotices
from .slc_licenses import SlcBusinessLicenses
from .slc_permits import SlcPermits
from .warn import WarnNotices

ALL = [PublicNotices(), WarnNotices(), SlcBusinessLicenses(), SlcPermits()]
BY_NAME = {s.name: s for s in ALL}


def selected(names: tuple[str, ...] | list[str]) -> list:
    if not names:
        return [s for s in ALL if s.default_enabled]
    unknown = [n for n in names if n not in BY_NAME]
    if unknown:
        raise SystemExit(f"Unknown source(s): {', '.join(unknown)}. Known: {', '.join(BY_NAME)}")
    return [BY_NAME[n] for n in names]
