"""
bot/nav.py
Shared navigation state (folder breadcrumb stack) per user.
Isolated here so both commands.py and callbacks.py can import it
without circular dependency issues.

Security: Stack depth is capped, and old inactive users are evicted.
"""

# Per-user breadcrumb stack: list of (folder_id, folder_name)
# Root is represented as ("root", "🏠 Home")
_folder_stack: dict[int, list[tuple[str, str]]] = {}

MAX_STACK_DEPTH = 50   # max folder nesting depth per user
MAX_USERS       = 5000 # evict oldest users if exceeded


def get_stack(uid: int) -> list[tuple[str, str]]:
    if uid not in _folder_stack:
        # Evict oldest user if we're at capacity
        if len(_folder_stack) >= MAX_USERS:
            oldest = next(iter(_folder_stack))
            del _folder_stack[oldest]
        _folder_stack[uid] = [("root", "🏠 Home")]
    return _folder_stack[uid]


def current_folder_id(uid: int) -> str:
    return get_stack(uid)[-1][0]


def current_folder_name(uid: int) -> str:
    return get_stack(uid)[-1][1]


def breadcrumb(uid: int) -> str:
    return " > ".join(name for _, name in get_stack(uid))


def push_folder(uid: int, folder_id: str, folder_name: str) -> None:
    stack = get_stack(uid)
    if len(stack) >= MAX_STACK_DEPTH:
        # Trim from the middle to keep root and recent path
        _folder_stack[uid] = [stack[0]] + stack[-(MAX_STACK_DEPTH - 2):]
        stack = _folder_stack[uid]
    stack.append((folder_id, folder_name))


def pop_folder(uid: int) -> bool:
    """Go back one level. Returns False if already at root."""
    stack = get_stack(uid)
    if len(stack) <= 1:
        return False
    stack.pop()
    return True


def go_home(uid: int) -> None:
    _folder_stack[uid] = [("root", "🏠 Home")]


def clear_user(uid: int) -> None:
    _folder_stack.pop(uid, None)
