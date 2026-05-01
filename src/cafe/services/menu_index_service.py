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
class MenuItemMatch:
    name: str
    section: str
    top_level: str
    price: str | None
    serving: str | None
    dietary_tags: str | None
    tags: tuple[str, ...]
    description: str | None
    matched_terms: tuple[str, ...]
    score: int

    def as_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "name": self.name,
            "section": self.section,
            "top_level": self.top_level,
            "matched_terms": list(self.matched_terms),
            "score": self.score,
        }
        if self.price:
            data["price"] = self.price
        if self.serving:
            data["serving"] = self.serving
        if self.dietary_tags:
            data["dietary_tags"] = self.dietary_tags
        if self.tags:
            data["tags"] = list(self.tags)
        if self.description:
            data["description"] = self.description
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


def _clean_price_text(value: str) -> str:
    return re.sub(r"[^\d/() A-Za-z.+-]+", " ", value).strip()


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
def build_menu_match_aliases(menu_doc_path: str | None = None) -> dict[str, tuple[str, ...]]:
    path = Path(menu_doc_path) if menu_doc_path else DEFAULT_MENU_DOC_PATH
    aliases: dict[str, tuple[str, ...]] = {}
    in_aliases = False

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line == "### Match Aliases":
            in_aliases = True
            continue
        if in_aliases and (line.startswith("### ") or line.startswith("## ")):
            break
        if not in_aliases:
            continue

        parsed_alias = _parse_alias_line(line)
        if parsed_alias:
            alias, targets = parsed_alias
            aliases[alias] = tuple(_phrase_normalize(target) for target in targets)

    return aliases


@lru_cache
def build_menu_item_match_index(menu_doc_path: str | None = None) -> tuple[MenuItemMatch, ...]:
    path = Path(menu_doc_path) if menu_doc_path else DEFAULT_MENU_DOC_PATH
    lines = path.read_text(encoding="utf-8").splitlines()
    section_path: tuple[str, ...] | None = None
    current_name: str | None = None
    current_fields: dict[str, str] = {}
    items: list[MenuItemMatch] = []

    def flush_current() -> None:
        nonlocal current_name, current_fields
        if current_name is None or section_path is None:
            current_name = None
            current_fields = {}
            return

        tags_text = current_fields.get("tags", "")
        tags = tuple(tag.strip() for tag in tags_text.split(",") if tag.strip())
        items.append(
            MenuItemMatch(
                name=current_name,
                section=" > ".join(section_path[1:]),
                top_level=section_path[0],
                price=_clean_price_text(current_fields.get("price", "")) or None,
                serving=current_fields.get("serving"),
                dietary_tags=current_fields.get("dietary tags"),
                tags=tags,
                description=current_fields.get("description"),
                matched_terms=(),
                score=0,
            )
        )
        current_name = None
        current_fields = {}

    for index, raw_line in enumerate(lines):
        line = raw_line.strip()

        if line.startswith("## ") and " > " in line[3:]:
            flush_current()
            section_path = tuple(part.strip() for part in line[3:].split(">"))
            continue

        if section_path is None:
            continue

        if line.startswith("## "):
            flush_current()
            section_path = None
            continue

        if line.startswith("### "):
            next_line = _next_content_line(lines, index + 1)
            if next_line.startswith("#### "):
                continue
            flush_current()
            current_name = line[4:].strip()
            current_fields = {}
            continue

        if line.startswith("#### "):
            flush_current()
            current_name = line[5:].strip()
            current_fields = {}
            continue

        if current_name is None:
            continue

        match = re.match(r"- \*\*(.+?):\*\*\s*(.+)$", line)
        if match:
            current_fields[_phrase_normalize(match.group(1))] = match.group(2).strip()

    flush_current()
    return tuple(items)


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


_MATCH_STOPWORDS = {
    "a",
    "about",
    "all",
    "and",
    "any",
    "are",
    "available",
    "can",
    "current",
    "do",
    "for",
    "have",
    "i",
    "is",
    "list",
    "me",
    "menu",
    "of",
    "on",
    "option",
    "options",
    "please",
    "show",
    "some",
    "something",
    "the",
    "there",
    "to",
    "what",
    "with",
    "you",
}

_MENU_OVERVIEW_TERMS = {
    "categories",
    "category",
    "complete",
    "entire",
    "full",
    "menu",
    "section",
    "sections",
    "whole",
}


def _menu_item_by_name(menu_doc_path: str | None = None) -> dict[str, MenuItemMatch]:
    return {item.name: item for item in build_menu_item_match_index(menu_doc_path)}


def _query_match_terms(query: str) -> tuple[str, ...]:
    words = [
        word for word in _phrase_normalize(query).split()
        if len(word) > 2 and word not in _MATCH_STOPWORDS
    ]
    terms: list[str] = []
    for word in words:
        if word.endswith("ies") and len(word) > 4:
            term = f"{word[:-3]}y"
        elif word.endswith("s") and len(word) > 3:
            term = word[:-1]
        else:
            term = word
        if term and term not in terms:
            terms.append(term)
    return tuple(terms)


def _expanded_query_match_terms(
    query: str,
    *,
    menu_doc_path: str | None = None,
) -> tuple[str, ...]:
    terms = list(_query_match_terms(query))
    text = _phrase_normalize(query)

    for alias, targets in build_menu_match_aliases(menu_doc_path).items():
        if not _contains_phrase(text, alias):
            continue

        alias_terms = set(_query_match_terms(alias))
        terms = [term for term in terms if term not in alias_terms]
        for target in targets:
            for term in _query_match_terms(target):
                if term not in terms:
                    terms.append(term)

    return tuple(terms)


def _terms_for_text(value: str) -> set[str]:
    return set(_query_match_terms(value))


def _section_matches_for_labels(labels: tuple[str, ...]) -> tuple[MenuSection, ...]:
    matches: list[MenuSection] = []
    seen: set[tuple[str, ...]] = set()
    for label in labels:
        for section in resolve_sections(label):
            if section.path in seen:
                continue
            matches.append(section)
            seen.add(section.path)
    return tuple(matches)


def _requested_sections_from_query(query: str) -> tuple[str, ...]:
    index = build_menu_index()
    text = _phrase_normalize(query)
    candidates: list[tuple[str, str, tuple[MenuSection, ...]]] = []

    matched_aliases = [
        alias for alias in sorted(index.aliases, key=len, reverse=True)
        if _contains_phrase(text, _phrase_normalize(alias))
    ]

    for alias in matched_aliases:
        normalized_alias = _phrase_normalize(alias)
        if any(
            alias != other
            and _contains_phrase(_phrase_normalize(other), normalized_alias)
            for other in matched_aliases
        ):
            continue
        sections = resolve_sections(alias)
        if sections:
            exact_singular_sections = tuple(
                section for section in sections
                if _normalize(section.name).removesuffix("s") == normalized_alias
                or _normalize(section.path[-1]).removesuffix("s") == normalized_alias
            )
            broad_alias = any(
                _contains_phrase(text, word)
                for word in ("all", "option", "options")
            )
            if not broad_alias and len(exact_singular_sections) == 1:
                section = exact_singular_sections[0]
                candidates.append((section.name, normalized_alias, (section,)))
            else:
                candidates.append((alias, normalized_alias, sections))

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
        matched_variant = next((variant for variant in variants if _contains_phrase(text, variant)), None)
        if matched_variant:
            candidates.append((section.name, matched_variant, (section,)))

    for top_level in index.top_level_categories:
        normalized = _phrase_normalize(top_level)
        if _contains_phrase(text, normalized):
            if any(
                _contains_phrase(_phrase_normalize(alias), normalized)
                for alias in matched_aliases
            ):
                continue
            candidates.append((top_level, normalized, index.sections_for_top_level(top_level)))

    labels: list[str] = []
    seen_paths: set[tuple[str, ...]] = set()
    for label, _matched_text, sections in sorted(candidates, key=lambda item: len(item[1]), reverse=True):
        new_sections = [section for section in sections if section.path not in seen_paths]
        if not new_sections:
            continue
        labels.append(label)
        seen_paths.update(section.path for section in new_sections)

    return tuple(labels)


def _query_has_item_match_modifier(query: str, labels: tuple[str, ...]) -> bool:
    query_terms = set(_query_match_terms(query))
    if not query_terms:
        return False

    scope_terms: set[str] = set()
    for label in labels:
        scope_terms.update(_terms_for_text(label))

    return bool(query_terms - scope_terms)


def _query_is_pure_menu_overview(query: str) -> bool:
    text = _phrase_normalize(query)
    if not any(
        _contains_phrase(text, phrase)
        for phrase in (
            "menu",
            "categories",
            "category",
            "sections",
            "section",
            "what do you have",
        )
    ):
        return False
    return set(_query_match_terms(query)) <= _MENU_OVERVIEW_TERMS


def _match_search_text(item: MenuItemMatch) -> str:
    return _phrase_normalize(
        " ".join(
            part or ""
            for part in (
                item.name,
                item.section,
                item.top_level,
                item.serving,
                item.dietary_tags,
                " ".join(item.tags),
                item.description,
            )
        )
    )


def search_menu_item_matches(
    query: str,
    *,
    max_results: int = 5,
    menu_doc_path: str | None = None,
) -> tuple[MenuItemMatch, ...]:
    raw_terms = _query_match_terms(query)
    terms = _expanded_query_match_terms(query, menu_doc_path=menu_doc_path)
    if not terms:
        return tuple()

    scope_labels = () if terms != raw_terms else _requested_sections_from_query(query)
    scoped_sections = _section_matches_for_labels(scope_labels)
    scoped_section_names = {_normalize(section.name) for section in scoped_sections}
    scope_terms: set[str] = set()
    for label in scope_labels:
        scope_terms.update(_terms_for_text(label))
    descriptor_terms = tuple(term for term in terms if term not in scope_terms)
    required_terms = descriptor_terms or (() if scoped_sections else terms)

    matches: list[MenuItemMatch] = []
    for item in build_menu_item_match_index(menu_doc_path):
        if scoped_sections and _normalize(item.section) not in scoped_section_names:
            continue

        search_text = _match_search_text(item)
        matched_terms = tuple(term for term in terms if _contains_phrase(search_text, term))
        if not matched_terms:
            continue
        if required_terms and not all(term in matched_terms for term in required_terms):
            continue

        name_text = _phrase_normalize(item.name)
        tag_text = _phrase_normalize(" ".join(item.tags))
        score = len(matched_terms)
        score += sum(3 for term in matched_terms if _contains_phrase(tag_text, term))
        score += sum(2 for term in matched_terms if _contains_phrase(name_text, term))
        matches.append(
            MenuItemMatch(
                name=item.name,
                section=item.section,
                top_level=item.top_level,
                price=item.price,
                serving=item.serving,
                dietary_tags=item.dietary_tags,
                tags=item.tags,
                description=item.description,
                matched_terms=matched_terms,
                score=score,
            )
        )

    return tuple(
        sorted(matches, key=lambda item: (-item.score, item.name))[:max_results]
    )


def _copy_menu_item(
    item: MenuItemMatch,
    *,
    matched_terms: tuple[str, ...] = (),
    score: int = 0,
) -> MenuItemMatch:
    return MenuItemMatch(
        name=item.name,
        section=item.section,
        top_level=item.top_level,
        price=item.price,
        serving=item.serving,
        dietary_tags=item.dietary_tags,
        tags=item.tags,
        description=item.description,
        matched_terms=matched_terms,
        score=score,
    )


def recommend_menu_items(
    *,
    max_results: int = 5,
    menu_doc_path: str | None = None,
) -> tuple[MenuItemMatch, ...]:
    """Return a stable, data-derived diverse sample from the menu.

    The selector uses only the parsed menu document: top-level order, section
    order, and item order. It alternates across top-level groups, then walks
    each group's sections in document order. No item or category names are
    encoded in code.
    """
    item_by_name = _menu_item_by_name(menu_doc_path)
    index = build_menu_index(menu_doc_path)
    section_positions = {top_level: 0 for top_level in index.top_level_categories}
    sections_by_top_level = {
        top_level: index.sections_for_top_level(top_level)
        for top_level in index.top_level_categories
    }
    picks: list[MenuItemMatch] = []
    seen_items: set[str] = set()

    while len(picks) < max_results:
        added_in_round = False
        for top_level in index.top_level_categories:
            sections = sections_by_top_level[top_level]
            position = section_positions[top_level]
            while position < len(sections):
                section = sections[position]
                section_positions[top_level] = position + 1
                position += 1
                item = next(
                    (
                        item_by_name[item_name]
                        for item_name in section.items
                        if item_name in item_by_name and item_name not in seen_items
                    ),
                    None,
                )
                if item is None:
                    continue
                picks.append(_copy_menu_item(item, matched_terms=("menu_data",)))
                seen_items.add(item.name)
                added_in_round = True
                break
            if len(picks) >= max_results:
                break
        if not added_in_round:
            break

    return tuple(picks)


def _format_item_lines(items: tuple[MenuItemMatch, ...]) -> list[str]:
    lines: list[str] = []
    for item in items:
        details = []
        if item.price:
            details.append(f"INR {item.price}")
        details.append(item.section)
        if item.serving:
            details.append(item.serving)
        suffix = f" ({'; '.join(details)})" if details else ""
        lines.append(f"- {item.name}{suffix}")
        if item.description:
            lines.append(f"  {item.description}")
    return lines


def format_menu_recommendations(
    *,
    max_results: int = 5,
    menu_doc_path: str | None = None,
) -> str:
    items = recommend_menu_items(max_results=max_results, menu_doc_path=menu_doc_path)
    if not items:
        return "No menu recommendations are available from the current menu data."

    lines = ["Representative picks from the current menu:"]
    lines.extend(_format_item_lines(items))
    return "\n".join(lines)


def format_menu_item_matches(
    query: str,
    *,
    max_results: int = 5,
    menu_doc_path: str | None = None,
) -> str:
    matches = search_menu_item_matches(
        query,
        max_results=max_results,
        menu_doc_path=menu_doc_path,
    )
    raw_terms = _query_match_terms(query)
    expanded_terms = _expanded_query_match_terms(query, menu_doc_path=menu_doc_path)
    requested_sections = (
        ()
        if expanded_terms != raw_terms
        else _requested_sections_from_query(query)
    )
    requested_section_text = ", ".join(requested_sections)
    if not matches:
        return "No matching menu items are available for that request."

    text = _phrase_normalize(query)
    if requested_section_text:
        heading = f"Here are the matching menu items for {requested_section_text}:"
    elif _contains_phrase(text, "dessert") or _contains_phrase(text, "desserts"):
        heading = (
            "I did not find a dedicated Desserts section, but I found these "
            "dessert-style menu items:"
        )
    else:
        heading = "Here are the matching menu items I found:"

    lines = [heading]
    lines.extend(_format_item_lines(matches))
    return "\n".join(lines)


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
    sections = _requested_sections_from_query(query)
    return sections[0] if sections else None


def _query_wants_complete_items(query: str) -> bool:
    text = _phrase_normalize(query)
    item_words = ("items", "item list", "detailed", "with items", "complete")
    whole_menu_words = ("menu", "whole menu", "entire menu", "full menu")
    return any(_contains_phrase(text, word) for word in item_words) and any(
        _contains_phrase(text, word) for word in whole_menu_words
    )


def _query_requests_menu_overview(query: str) -> bool:
    return _query_is_pure_menu_overview(query)


def format_menu_categories(*, include_items: bool = False) -> str:
    data = get_menu_categories(include_items=include_items)
    if include_items:
        lines = ["Here is the complete menu, grouped by section:"]
    else:
        lines = [
            "Of course. Here are the menu sections:",
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
        return "\n".join(lines)

    lines = [f"Absolutely. Here are the matching sections for {section_name}:"]
    for section in matches:
        lines.extend(["", f"{section.name}:"])
        lines.extend(f"- {item}" for item in section.items)
    return "\n".join(lines)


def format_menu_multi_section_items(section_names: tuple[str, ...], query: str) -> str:
    matches = _section_matches_for_labels(section_names)
    if not matches:
        sections = ", ".join(build_menu_index().flat_category_names)
        return (
            f"I could not find menu sections matching '{query}'. "
            f"Available sections are: {sections}."
        )

    lines = [f"Absolutely. Here are the matching sections for {query}:"]
    for section in matches:
        lines.extend(["", f"{section.name}:"])
        lines.extend(f"- {item}" for item in section.items)
    return "\n".join(lines)


def format_menu_browse_query(query: str, *, include_items: bool | None = None) -> str:
    """Return the right menu browsing display text for a natural query."""
    return browse_menu_query(query, include_items=include_items).display_text


def browse_menu_query(query: str, *, include_items: bool | None = None) -> MenuBrowseResult:
    """Return menu browsing text plus whether it is safe to pass through."""
    requested_sections = _requested_sections_from_query(query)
    requested_section = ", ".join(requested_sections) if requested_sections else None
    if requested_sections:
        display_text = (
            format_menu_section_items(requested_sections[0])
            if len(requested_sections) == 1
            else format_menu_multi_section_items(requested_sections, query)
        )
        return MenuBrowseResult(
            display_text=display_text,
            response_kind="section_items",
            passthrough=not _query_has_item_match_modifier(query, requested_sections),
            requested_section=requested_section,
        )

    wants_complete_items = _query_wants_complete_items(query)
    should_include_items = wants_complete_items if include_items is None else include_items and wants_complete_items
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
    requested_section = _requested_section_from_query(query)
    if requested_section:
        matches = resolve_sections(requested_section)
        if not matches:
            return requested_section
        top_levels = {section.top_level for section in matches}
        if len(top_levels) == 1:
            top_level = next(iter(top_levels))
            if _normalize(requested_section) == _normalize(top_level):
                return top_level
            return requested_section

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


def _query_mentions_menu_scope(query: str) -> bool:
    text = _phrase_normalize(query)
    index = build_menu_index()
    candidates: set[str] = set(index.top_level_categories)
    candidates.update(index.flat_category_names)
    candidates.update(index.aliases)
    candidates.update(item.name for item in build_menu_item_match_index())

    for candidate in candidates:
        normalized = _phrase_normalize(candidate)
        variants = {normalized, normalized.removesuffix("s")}
        if any(_contains_phrase(text, variant) for variant in variants):
            return True
    return False


def is_context_dependent_price_request(query: str) -> bool:
    if not is_price_list_request(query):
        return False
    return not _query_mentions_menu_scope(query)


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
            "No matching menu items are available for that request."
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
    return "\n".join(lines)
