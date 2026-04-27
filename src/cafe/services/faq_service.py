from cafe.core.validator import ValidationError


_FAQ = {
    "hours": "We are open daily 7 AM to 11 PM.",
    "wifi": "Free Wi-Fi: 'CafeGuest', password 'welcome123'.",
    "vegan": "Vegan options available — ask for oat or soy milk.",
    "allergens": "All items may contain traces of nuts, dairy, gluten.",
    "payment": "We accept UPI, cards, and cash. Card minimum ₹100.",
    "location": "Find us at Brigade Road, Bengaluru — 1st floor.",
    "loyalty": "Earn 1 star per ₹100 spent. 10 stars = free coffee.",
}

_FAQ_KEYWORDS = {
    "hours": ("hours", "open", "close", "closing", "time", "timing", "daily"),
    "wifi": ("wifi", "wi-fi", "internet", "password"),
    "vegan": ("vegan", "oat", "soy", "plant"),
    "allergens": ("allergen", "allergens", "nuts", "dairy", "gluten"),
    "payment": ("payment", "pay", "upi", "card", "cash"),
    "location": ("location", "address", "where", "brigade", "road"),
    "loyalty": ("loyalty", "stars", "reward", "rewards", "free coffee"),
}


def lookup_faq(question: str) -> tuple[str, str]:
    """Returns (topic, answer). Raises ValidationError on no match."""
    normalized_question = question.casefold()

    for topic, keywords in _FAQ_KEYWORDS.items():
        if any(keyword in normalized_question for keyword in keywords):
            return topic, _FAQ[topic]

    raise ValidationError("I could not find an FAQ answer for that question.")
