from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


DOCS_DIR = Path(__file__).resolve().parents[1] / "Docs"
DEFAULT_MENU_DOC_PATH = DOCS_DIR / "BTB_Menu_Enhanced.md"


@dataclass(frozen=True)
class MenuSection:
    top_level: str
    name: str
    path: tuple[str, ...]
    items: tuple[str, ...]

    @property
    def item_count(self) -> int:
        return len(self.items)

    def as_dict(self, *, include_items: bool = True) -> dict[str, object]:
        data: dict[str, object] = {
            "top_level": self.top_level,
            "name": self.name,
            "path": list(self.path),
            "item_count": self.item_count,
        }
        if include_items:
            data["items"] = list(self.items)
        return data


@dataclass(frozen=True)
class MenuIndex:
    sections: tuple[MenuSection, ...]
    aliases: dict[str, tuple[str, ...]]

    @property
    def top_level_categories(self) -> tuple[str, ...]:
        seen: list[str] = []
        for section in self.sections:
            if section.top_level not in seen:
                seen.append(section.top_level)
        return tuple(seen)

    @property
    def flat_category_names(self) -> tuple[str, ...]:
        return tuple(section.name for section in self.sections)

    def sections_for_top_level(self, top_level: str) -> tuple[MenuSection, ...]:
        return tuple(
            section for section in self.sections
            if section.top_level.casefold() == top_level.casefold()
        )


@dataclass(frozen=True)
class MenuPriceItem:
    name: str
    category: str
    price: int
    top_level: str
    serving: str | None = None
    dietary: str | None = None

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "name": self.name,
            "category": self.category,
            "price": self.price,
            "top_level": self.top_level,
        }
        if self.serving:
            data["serving"] = self.serving
        if self.dietary:
            data["dietary"] = self.dietary
        return data


@dataclass(frozen=True)
class MenuBrowseResult:
    display_text: str
    response_kind: str
    passthrough: bool
    requested_section: str | None = None

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "display_text": self.display_text,
            "response_kind": self.response_kind,
            "passthrough": self.passthrough,
        }
        if self.requested_section:
            data["requested_section"] = self.requested_section
        return data


def _normalize(value: str) -> str:
    normalized = value.casefold().strip()
    for suffix in (" section", " category", " items", " list", " options"):
        normalized = normalized.removesuffix(suffix)
    return " ".join(normalized.split())


def _phrase_normalize(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9]+", " ", value.casefold())
    return " ".join(normalized.split())


def _contains_phrase(text: str, phrase: str) -> bool:
    if not phrase:
        return False
    return f" {phrase} " in f" {text} "


def _parse_markdown_table_row(line: str) -> list[str]:
    if not line.startswith("|") or "---" in line:
        return []
    return [part.strip() for part in line.strip("|").split("|")]


def _parse_price(value: str) -> int | None:
    match = re.search(r"\d+", value)
    return int(match.group(0)) if match else None


def _next_content_line(lines: list[str], start_index: int) -> str:
    for raw_line in lines[start_index:]:
        line = raw_line.strip()
        if line and line != "---":
            return line
    return ""


def _parse_alias_line(line: str) -> tuple[str, tuple[str, ...]] | None:
    match = re.match(r"- \*\*(.+?):\*\*\s*(.+)$", line)
    if not match:
        return None
    alias = _phrase_normalize(match.group(1))
    targets = tuple(
        target.strip()
        for target in match.group(2).split(",")
        if target.strip()
    )
    if not alias or not targets:
        return None
    return alias, targets


@lru_cache
def build_menu_index(menu_doc_path: str | None = None) -> MenuIndex:
    path = Path(menu_doc_path) if menu_doc_path else DEFAULT_MENU_DOC_PATH
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    sections: list[MenuSection] = []
    aliases: dict[str, tuple[str, ...]] = {}
    current_path: tuple[str, ...] | None = None
    current_items: list[str] = []
    in_aliases = False

    def flush_current() -> None:
        nonlocal current_path, current_items
        if current_path is None:
            return
        sections.append(
            MenuSection(
                top_level=current_path[0],
                name=" > ".join(current_path[1:]),
                path=current_path,
                items=tuple(current_items),
            )
        )
        current_path = None
        current_items = []

    for index, raw_line in enumerate(lines):
        line = raw_line.strip()

        if line == "### Browse Aliases":
            flush_current()
            in_aliases = True
            continue

        if in_aliases:
            if line.startswith("### ") or line.startswith("## "):
                in_aliases = False
            else:
                parsed_alias = _parse_alias_line(line)
                if parsed_alias:
                    alias, targets = parsed_alias
                    aliases[alias] = targets
                continue

        if line.startswith("## ") and " > " in line[3:]:
            flush_current()
            current_path = tuple(part.strip() for part in line[3:].split(">"))
            current_items = []
            continue

        if current_path is None:
            continue

        if line.startswith("## "):
            flush_current()
            continue

        if line.startswith("### "):
            next_line = _next_content_line(lines, index + 1)
            if not next_line.startswith("#### "):
                current_items.append(line[4:].strip())
            continue

        if line.startswith("#### "):
            current_items.append(line[5:].strip())

    flush_current()
    return MenuIndex(sections=tuple(sections), aliases=aliases)


@lru_cache
def build_menu_price_index(menu_doc_path: str | None = None) -> tuple[MenuPriceItem, ...]:
    path = Path(menu_doc_path) if menu_doc_path else DEFAULT_MENU_DOC_PATH
    lines = path.read_text(encoding="utf-8").splitlines()
    items: list[MenuPriceItem] = []
    section: str | None = None

    for raw_line in lines:
        line = raw_line.strip()

        if line.startswith("### ALL BEVERAGES"):
            section = "beverages"
            continue
        if line.startswith("### ALL FOOD ITEMS"):
            section = "food"
            continue
        if line.startswith("### ") or line.startswith("## "):
            if section in {"beverages", "food"}:
                section = None
            continue

        if section is None:
            continue

        cells = _parse_markdown_table_row(line)
        if not cells or cells[0] == "#":
            continue

        if section == "beverages" and len(cells) >= 5:
            price = _parse_price(cells[3])
            if price is None:
                continue
            items.append(
                MenuPriceItem(
                    name=cells[1],
                    category=cells[2],
                    price=price,
                    top_level="Beverages",
                    serving=cells[4],
                )
            )
            continue

        if section == "food" and len(cells) >= 5:
            price = _parse_price(cells[3])
            if price is None:
                continue
            items.append(
                MenuPriceItem(
                    name=cells[1],
                    category=cells[2],
                    price=price,
                    top_level="Food",
                    dietary=cells[4],
                )
            )

    return tuple(items)


def get_menu_categories(*, include_items: bool = True) -> dict[str, object]:
    index = build_menu_index()
    return {
        "top_level_categories": list(index.top_level_categories),
        "categories": [
            section.as_dict(include_items=include_items)
            for section in index.sections
        ],
        "flat_category_names": list(index.flat_category_names),
        "aliases": {alias: list(targets) for alias, targets in index.aliases.items()},
    }


def _resolve_alias(index: MenuIndex, alias: str) -> tuple[MenuSection, ...]:
    targets = index.aliases.get(_phrase_normalize(alias), ())
    matches: list[MenuSection] = []
    for target in targets:
        if any(target.casefold() == top_level.casefold() for top_level in index.top_level_categories):
            matches.extend(index.sections_for_top_level(target))
            continue

        matches.extend(
            section for section in index.sections
            if _normalize(section.name) == _normalize(target)
        )

    return tuple(matches)


def resolve_sections(section_name: str) -> tuple[MenuSection, ...]:
    index = build_menu_index()
    normalized = _normalize(section_name)

    exact = tuple(
        section for section in index.sections
        if _normalize(section.name) == normalized
    )
    if exact:
        return exact

    alias_matches = _resolve_alias(index, normalized)
    if alias_matches:
        return alias_matches

    singular = normalized.removesuffix("s")
    singular_matches = tuple(
        section for section in index.sections
        if _normalize(section.name).removesuffix("s") == singular
    )
    if singular_matches:
        return singular_matches

    return tuple(
        section for section in index.sections
        if normalized in _normalize(section.name)
    )


def _requested_section_from_query(query: str) -> str | None:
    index = build_menu_index()
    text = _phrase_normalize(query)

    for alias in sorted(index.aliases, key=len, reverse=True):
        normalized_alias = _phrase_normalize(alias)
        if " " in normalized_alias and _contains_phrase(text, normalized_alias):
            return alias

    for section in sorted(index.sections, key=lambda item: len(item.name), reverse=True):
        variants = {
            _phrase_normalize(section.name),
            _phrase_normalize(section.path[-1]),
            _phrase_normalize(" ".join(section.path[1:])),
        }
        variants.update(
            variant.removesuffix("s")
            for variant in list(variants)
            if variant.endswith("s")
        )
        if any(_contains_phrase(text, variant) for variant in variants):
            return section.name

    for alias in sorted(index.aliases, key=len, reverse=True):
        if _contains_phrase(text, _phrase_normalize(alias)):
            if len(resolve_sections(alias)) > 1 and any(
                _contains_phrase(text, word)
                for word in ("categories", "category", "sections", "section")
            ):
                continue
            return alias

    for top_level in index.top_level_categories:
        if _contains_phrase(text, _phrase_normalize(top_level)):
            if not any(
                _contains_phrase(text, word)
                for word in ("categories", "category", "sections", "section")
            ):
                return top_level

    return None


def _query_wants_complete_items(query: str) -> bool:
    text = _phrase_normalize(query)
    item_words = ("items", "item list", "detailed", "with items", "complete")
    whole_menu_words = ("menu", "whole menu", "entire menu", "full menu")
    return any(_contains_phrase(text, word) for word in item_words) and any(
        _contains_phrase(text, word) for word in whole_menu_words
    )


def _query_requests_menu_overview(query: str) -> bool:
    text = _phrase_normalize(query)
    return any(
        _contains_phrase(text, phrase)
        for phrase in (
            "menu",
            "categories",
            "category",
            "sections",
            "section",
            "what do you have",
        )
    )


def format_menu_categories(*, include_items: bool = False) -> str:
    data = get_menu_categories(include_items=include_items)
    if include_items:
        lines = ["Here is the complete menu, grouped by section:"]
    else:
        lines = [
            "Of course. Here are the menu sections:",
            "",
            "Pick any section you like, and I can show you the items inside it.",
        ]

    for top_level in data["top_level_categories"]:
        lines.extend(["", f"{top_level}:"])
        for category in data["categories"]:
            if category["top_level"] != top_level:
                continue
            if include_items:
                items = ", ".join(category["items"])
                lines.append(f"- {category['name']}: {items}")
            else:
                lines.append(f"- {category['name']}")

    return "\n".join(lines)


def format_menu_section_items(section_name: str) -> str:
    matches = resolve_sections(section_name)
    if not matches:
        sections = ", ".join(build_menu_index().flat_category_names)
        return (
            f"I could not find a menu section named '{section_name}'. "
            f"Available sections are: {sections}."
        )

    if len(matches) == 1:
        section = matches[0]
        lines = [f"Absolutely. Here are the items under {section.name}:"]
        lines.extend(f"- {item}" for item in section.items)
        lines.extend(["", "I can show prices or details for any one you like."])
        return "\n".join(lines)

    lines = [f"Absolutely. Here are the matching sections for {section_name}:"]
    for section in matches:
        lines.extend(["", f"{section.name}:"])
        lines.extend(f"- {item}" for item in section.items)
    lines.extend(["", "I can show prices or details for any one you like."])
    return "\n".join(lines)


def format_menu_browse_query(query: str, *, include_items: bool | None = None) -> str:
    """Return the right menu browsing display text for a natural query."""
    return browse_menu_query(query, include_items=include_items).display_text


def browse_menu_query(query: str, *, include_items: bool | None = None) -> MenuBrowseResult:
    """Return menu browsing text plus whether it is safe to pass through."""
    requested_section = _requested_section_from_query(query)
    if requested_section:
        return MenuBrowseResult(
            display_text=format_menu_section_items(requested_section),
            response_kind="section_items",
            passthrough=True,
            requested_section=requested_section,
        )

    should_include_items = (
        _query_wants_complete_items(query)
        if include_items is None
        else include_items
    )
    is_menu_overview = _query_requests_menu_overview(query)
    return MenuBrowseResult(
        display_text=format_menu_categories(include_items=should_include_items),
        response_kind="menu_items" if should_include_items else "menu_sections",
        passthrough=is_menu_overview,
    )


def extract_price_limit(query: str) -> int | None:
    text = _phrase_normalize(query)
    patterns = (
        r"(?:under|below|less than|within|max|maximum|upto|up to)\s*(?:rs|rupees|inr)?\s*(\d+)",
        r"(?:rs|rupees|inr)\s*(\d+)\s*(?:or less|and below|and under)",
        r"(\d+)\s*(?:rs|rupees|inr)\s*(?:or less|and below|and under)",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def _price_scope_from_query(query: str) -> str | None:
    text = _phrase_normalize(query)
    requested_section = _requested_section_from_query(query)
    if requested_section:
        normalized_section = _phrase_normalize(requested_section)
        if normalized_section in {"drink", "drinks", "beverage", "beverages"}:
            return "Beverages"
        if normalized_section == "food":
            return "Food"

        matches = resolve_sections(requested_section)
        if not matches:
            return requested_section
        if len({section.top_level for section in matches}) == 1:
            return requested_section

    if any(_contains_phrase(text, word) for word in ("drink", "drinks", "beverage", "beverages")):
        return "Beverages"
    if _contains_phrase(text, "food"):
        return "Food"
    return None


def requested_section_from_query(query: str) -> str | None:
    return _requested_section_from_query(query)


def is_price_list_request(query: str) -> bool:
    text = _phrase_normalize(query)
    return any(
        _contains_phrase(text, phrase)
        for phrase in (
            "price",
            "prices",
            "with price",
            "with prices",
            "how much",
            "cost",
            "costs",
        )
    ) and extract_price_limit(query) is None


def is_context_dependent_price_request(query: str) -> bool:
    if not is_price_list_request(query):
        return False
    text = _phrase_normalize(query)
    return not any(
        _contains_phrase(text, phrase)
        for phrase in (
            "coffee",
            "coffees",
            "coffee options",
            "pizza",
            "pizzas",
            "mocktail",
            "mocktails",
            "drink",
            "drinks",
            "beverage",
            "beverages",
            "food",
        )
    )


def filter_price_items(
    *,
    max_price: int,
    query: str = "",
    scope: str | None = None,
) -> tuple[MenuPriceItem, ...]:
    price_items = build_menu_price_index()
    resolved_scope = scope or _price_scope_from_query(query)

    if resolved_scope:
        section_matches = resolve_sections(resolved_scope)
        section_names = {_normalize(section.name) for section in section_matches}
        section_singulars = {name.removesuffix("s") for name in section_names}
        top_levels = {
            top_level
            for top_level in build_menu_index().top_level_categories
            if top_level.casefold() == resolved_scope.casefold()
        }
    else:
        section_names = set()
        section_singulars = set()
        top_levels = set()

    matches: list[MenuPriceItem] = []
    for item in price_items:
        if item.price > max_price:
            continue
        if top_levels and item.top_level not in top_levels:
            continue
        if section_names and (
            _normalize(item.category) not in section_names
            and _normalize(item.category).removesuffix("s") not in section_singulars
        ):
            continue
        matches.append(item)

    return tuple(matches)


def _price_items_for_scope(query: str, scope: str | None = None) -> tuple[MenuPriceItem, ...]:
    price_items = build_menu_price_index()
    resolved_scope = scope or _price_scope_from_query(query)

    if not resolved_scope:
        return price_items

    section_matches = resolve_sections(resolved_scope)
    section_names = {_normalize(section.name) for section in section_matches}
    section_singulars = {name.removesuffix("s") for name in section_names}
    top_levels = {
        top_level
        for top_level in build_menu_index().top_level_categories
        if top_level.casefold() == resolved_scope.casefold()
    }

    if top_levels:
        return tuple(item for item in price_items if item.top_level in top_levels)

    if section_names:
        return tuple(
            item for item in price_items
            if _normalize(item.category) in section_names
            or _normalize(item.category).removesuffix("s") in section_singulars
        )

    return price_items


def price_items_for_query(query: str) -> tuple[MenuPriceItem, ...]:
    return tuple(sorted(_price_items_for_scope(query), key=lambda item: (item.price, item.name)))


def format_price_list_query(query: str) -> str:
    scope = _price_scope_from_query(query)
    items = price_items_for_query(query)
    if not items:
        return "I could not find matching menu prices for that request."

    if scope:
        heading = f"Here are the prices for {scope}:"
    else:
        heading = "Here are the menu prices:"

    lines = [heading]
    for item in items:
        detail = item.serving or item.dietary
        suffix = f" ({detail})" if detail else ""
        lines.append(f"- {item.name} - ₹{item.price} [{item.category}]{suffix}")
    lines.extend(["", "Tell me any item name if you want the full details."])
    return "\n".join(lines)


def format_price_filter_query(query: str, *, max_price: int | None = None) -> str:
    limit = max_price if max_price is not None else extract_price_limit(query)
    if limit is None:
        return "I need a price limit to filter the menu, like 'items under 200'."

    matches = filter_price_items(max_price=limit, query=query)
    scope = _price_scope_from_query(query)
    scope_text = f" {scope}" if scope else ""

    if not matches:
        scoped_items = sorted(_price_items_for_scope(query), key=lambda item: item.price)
        if scoped_items:
            cheapest = scoped_items[0]
            return (
                f"I could not find any{scope_text} items under ₹{limit}. "
                f"The lowest {scope or 'menu'} option I found is "
                f"{cheapest.name} at ₹{cheapest.price}."
            )
        return (
            f"I could not find any{scope_text} items under ₹{limit}. "
            "I can show the lowest-priced menu options if you like."
        )

    if len(matches) == 1:
        heading = (
            f"Here is one item in {scope} under ₹{limit}:"
            if scope
            else f"Here is one item under ₹{limit}:"
        )
    else:
        heading = (
            f"Here are the items in {scope} under ₹{limit}:"
            if scope
            else f"Here are the items under ₹{limit}:"
        )
    lines = [heading]
    for item in matches:
        detail = item.serving or item.dietary
        suffix = f" ({detail})" if detail else ""
        lines.append(f"- {item.name} - ₹{item.price} [{item.category}]{suffix}")
    lines.extend(["", "Want me to narrow these by drinks, food, or a category?"])
    return "\n".join(lines)
