"""Display coordinates owned by the desktop absolute mouse backend."""


def absolute_entry(edge: int, fraction: float, width: int, height: int) -> tuple[int, int]:
    """One pixel inside the destination edge with full-range proportional mapping."""
    f = max(0.0, min(1.0, fraction))
    if edge == 0:
        return 1, round(f * (height - 1))
    if edge == 1:
        return width - 2, round(f * (height - 1))
    if edge == 2:
        return round(f * (width - 1)), 1
    return round(f * (width - 1)), height - 2
