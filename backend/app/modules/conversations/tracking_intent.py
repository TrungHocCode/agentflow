"""Code-owned wording and keyword check for the chat -> competitor-setup flow.

The Supervisor chooses ``setup_tracking`` for a request to find or track competitors, but a small model can
still plan competitor research from memory (measured on qwen3:8b), so code checks the routing with a keyword
match: a match plus ``propose_plan`` becomes ``setup_tracking``, and when the model call fails the match alone
decides. The lines that report the setup result are written here, in the language of the request, because they
state facts from the search that the model must not paraphrase.
"""

import re
from typing import Any

_TRACKING = re.compile(
    r"\b(?:competitors?|rivals?|competition)\b|đối thủ|"
    r"\b(?:track|monitor|watch)\b[^.?!\n]{0,60}\b(?:pric\w*|plans?|features?|changelog|releases?)\b|"
    r"theo dõi[^.?!\n]{0,60}(?:giá|tính năng|gói|cập nhật)",
    re.IGNORECASE,
)
_VIETNAMESE = re.compile(r"[ăâđêôơưạảấầẩẫậắằẳẵặẹẻẽếềểễệỉịọỏốồổỗộớờởỡợụủứừửữựỳỵỷỹ]", re.IGNORECASE)
MAX_LISTED_COMPETITORS = 6

_SOURCE_LABELS = {
    "default": ("default", "mặc định"),
    "saved": ("saved product", "sản phẩm đã lưu"),
    "message": ("from your message", "từ tin nhắn của bạn"),
}


def is_tracking_request(text: str) -> bool:
    return bool(_TRACKING.search(text or ""))


def is_vietnamese(text: str) -> bool:
    return bool(_VIETNAMESE.search(text or ""))


def setup_acknowledgement(text: str) -> str:
    """Reply text when no Supervisor text exists (model failure, or the user answered a follow-up question)."""
    return "Mình sẽ tìm đối thủ ngay." if is_vietnamese(text) else "I'll look for competitors now."


def setup_summary(text: str, setup: dict[str, Any] | None) -> str:
    """Code-written lead sentence, used when the model cannot write one from the search result."""
    vietnamese = is_vietnamese(text)
    recommendation = (setup or {}).get("recommendation") or {}
    if setup is None or setup.get("failed"):
        return "Mình chưa tìm được đối thủ lần này." if vietnamese else "I could not finish the competitor search."
    if recommendation.get("follow_up_question"):
        return "Mình cần thêm một thông tin trước khi tìm." if vietnamese else "I need one more detail before I search."
    if not recommendation.get("products"):
        return "Mình không tìm thấy đối thủ nào." if vietnamese else "I did not find any competitors."
    return ("Mình đã tìm được vài đối thủ; hãy chọn đối thủ muốn theo dõi rồi bấm “Find source URLs” ở thẻ bên dưới."
            if vietnamese else
            "I found some competitors; pick the ones to track and press “Find source URLs” in the card below.")


def setup_report(text: str, setup: dict[str, Any] | None) -> str:
    """The code-written lines that follow the Supervisor's text; ``setup`` is None when the search failed."""
    vietnamese = is_vietnamese(text)
    if setup is None:
        return ("Tìm kiếm đối thủ bị lỗi. Bấm “Search again” ở thẻ bên dưới để thử lại." if vietnamese
                else "The competitor search failed. Use “Search again” in the card below to retry.")
    product = setup.get("product") or {}
    recommendation = setup.get("recommendation") or {}
    label = _SOURCE_LABELS.get(product.get("source"), _SOURCE_LABELS["message"])[1 if vietnamese else 0]
    name = product.get("name")
    using = (f"Đang dùng: {name} ({label})" if vietnamese else f"Using: {name} ({label})") if name else (
        "Đang dùng: tin nhắn của bạn" if vietnamese else "Using: your message")
    question = recommendation.get("follow_up_question")
    if question:
        return f"{using}\n\n{question}" if name else question
    names = [item["name"] for item in recommendation.get("products") or [] if item.get("kind") == "competitor"]
    if not names:
        return f"{using}\n\n" + ("Chưa tìm thấy đối thủ nào." if vietnamese else "No competitors were found.")
    listed = ", ".join(names[:MAX_LISTED_COMPETITORS])
    return f"{using}\n\n" + (f"Đối thủ gợi ý: {listed}." if vietnamese else f"Suggested competitors: {listed}.")
