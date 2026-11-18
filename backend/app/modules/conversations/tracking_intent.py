"""Chat messages that ask to track competitors belong to the setup card, not a Supervisor plan.

Measured on qwen3:8b: the Supervisor answered every such message with its own research plan, which
hid the setup card, and once planned "identify and list competitors" from model memory. Scout finds
competitors with web search instead, so these turns get a fixed reply that points to the card.
"""

import re

_TRACKING = re.compile(
    r"\b(?:competitors?|rivals?|competition)\b|đối thủ|"
    r"\b(?:track|monitor|watch)\b[^.?!\n]{0,60}\b(?:pric\w*|plans?|features?|changelog|releases?)\b|"
    r"theo dõi[^.?!\n]{0,60}(?:giá|tính năng|gói|cập nhật)",
    re.IGNORECASE,
)
_VIETNAMESE = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.IGNORECASE)


def is_tracking_request(text: str) -> bool:
    return bool(_TRACKING.search(text or ""))


def tracking_reply(text: str) -> str:
    if _VIETNAMESE.search(text or ""):
        return ("Mình sẽ tìm đối thủ bằng web search từ mô tả của bạn. Bấm “Find competitors” ở thẻ bên dưới; "
                "nếu còn thiếu thông tin, mình sẽ hỏi thêm ở đó.")
    return ("I'll find competitors with web search from your description. Click “Find competitors” in the "
            "card below; if something is missing, I'll ask there.")
