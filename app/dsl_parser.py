# app/dsl_parser.py
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

class DSLEntry:
    """Represents a DSL dictionary entry with all its variants."""

    def __init__(self, headword: str):
        self.headword = headword.strip()
        self.main_translation: str = ""
        self.variants: List[Tuple[str, str]] = []  # [(variant_source, variant_target), ...]

    def add_main_translation(self, translation: str):
        """Add the main translation for the headword."""
        self.main_translation = translation.strip()

    def add_variant(self, source: str, target: str):
        """Add a variant with its translation."""
        source = source.strip()
        target = target.strip()
        if source and target:
            self.variants.append((source, target))

    def get_all_source_terms(self) -> List[str]:
        """Get all source terms (headword + all variant sources)."""
        terms = [self.headword]
        terms.extend([v[0] for v in self.variants])
        return terms

    def get_all_target_terms(self) -> List[str]:
        """Get all target terms (main translation + all variant targets)."""
        terms = []
        if self.main_translation:
            terms.append(self.main_translation)
        # Split comma-separated translations and flatten
        for _, target in self.variants:
            # Handle comma-separated translations
            for t in target.split(','):
                t = t.strip()
                if t:
                    terms.append(t)
        return terms


class DSLParser:
    """Parser for DSL dictionary files."""

    def __init__(self):
        self.entries: Dict[str, DSLEntry] = {}  # headword -> DSLEntry
        self.index: Dict[str, str] = {}  # normalized_term -> headword

    def parse_file(self, filepath: str | Path) -> None:
        """Parse a DSL file and build the index."""
        filepath = Path(filepath)
        if not filepath.exists():
            return

        current_entry: Optional[DSLEntry] = None

        with open(filepath, 'r', encoding='utf-8') as f:
            for line in f:
                # Skip metadata lines
                if line.startswith('#'):
                    continue

                # Check if this is a headword (starts at column 0)
                if line and line[0] not in (' ', '\t', '\n', '\r'):
                    headword = line.strip()
                    if headword:
                        current_entry = DSLEntry(headword)
                        self.entries[headword.lower()] = current_entry
                        self._add_to_index(headword, headword)
                    continue

                if not current_entry:
                    continue

                # This is an indented line (translation or variant)
                stripped = line.strip()
                if not stripped:
                    continue

                # Check if this line contains a variant (marked with [b]...[/b])
                variant_match = re.match(r'\[b\]([^\]]+)\[/b]\s+(.+)', stripped)
                if variant_match:
                    variant_source = variant_match.group(1).strip()
                    variant_target = variant_match.group(2).strip()
                    current_entry.add_variant(variant_source, variant_target)
                    # Add variant to index
                    self._add_to_index(variant_source, current_entry.headword)
                elif not current_entry.main_translation:
                    # This is the main translation (first indented line without [b] tags)
                    current_entry.add_main_translation(stripped)
                else:
                    # After main translation is set, remaining lines are variants
                    # Try to split into source and target parts
                    # Pattern: "abbreviated_source rest_of_source target_translation"
                    # E.g., "ch. Burkitta Burkitt's lymphoma"

                    words = stripped.split()
                    if len(words) >= 2:
                        # Heuristics to find the split point:
                        split_idx = None

                        # 1. Look for apostrophe (English possessive like "Burkitt's")
                        for i, word in enumerate(words):
                            if "'" in word or "'" in word:  # Regular and curly apostrophes
                                split_idx = i
                                break

                        # 2. Look for Polish adjective endings followed by English
                        if split_idx is None:
                            polish_endings = ('owy', 'ny', 'iczny', 'yczny', 'niczy', 'czy', 'ski', 'cki', 'tyczny')
                            for i in range(len(words) - 1):
                                if any(words[i].endswith(ending) for ending in polish_endings):
                                    # Next word is likely English
                                    split_idx = i + 1
                                    break

                        # 3. Look for hyphenated English prefixes
                        if split_idx is None:
                            english_prefixes = ('non-', 'anti-', 'pre-', 'post-', 'sub-', 'super-', 'semi-')
                            for i, word in enumerate(words):
                                if any(word.lower().startswith(prefix) for prefix in english_prefixes):
                                    split_idx = i
                                    break

                        # 4. Look for common English medical words
                        if split_idx is None:
                            english_words = {'lymphoma', 'disease', 'syndrome', 'cell', 'follicular',
                                           'benign', 'malignant', 'giant', 'pulmonary', 'cutaneous'}
                            for i, word in enumerate(words):
                                if word.lower() in english_words:
                                    split_idx = i
                                    break

                        # 5. Fallback: if starts with abbreviation like "ch.", split after 2nd or 3rd word
                        if split_idx is None and words[0].endswith('.'):
                            # Assume: "abbr. descriptor English translation"
                            split_idx = min(2, len(words) - 1)

                        if split_idx and split_idx < len(words):
                            variant_source = ' '.join(words[:split_idx])
                            variant_target = ' '.join(words[split_idx:])
                            current_entry.add_variant(variant_source, variant_target)
                            self._add_to_index(variant_source, current_entry.headword)

    def _add_to_index(self, term: str, headword: str) -> None:
        """Add a term to the search index."""
        normalized = term.lower().strip()
        if normalized:
            self.index[normalized] = headword.lower()

    def lookup(self, term: str) -> Optional[DSLEntry]:
        """Look up a term and return its entry if found."""
        normalized = term.lower().strip()
        headword = self.index.get(normalized)
        if headword:
            return self.entries.get(headword)
        return None


# Global parsers for EN-PL and PL-EN dictionaries
_en_pl_parser: Optional[DSLParser] = None
_pl_en_parser: Optional[DSLParser] = None


def get_en_pl_parser() -> DSLParser:
    """Get or initialize the EN-PL parser."""
    global _en_pl_parser
    if _en_pl_parser is None:
        _en_pl_parser = DSLParser()
        _en_pl_parser.parse_file("EN-PL.dsl")
    return _en_pl_parser


def get_pl_en_parser() -> DSLParser:
    """Get or initialize the PL-EN parser."""
    global _pl_en_parser
    if _pl_en_parser is None:
        _pl_en_parser = DSLParser()
        _pl_en_parser.parse_file("PL-ENG.dsl")
    return _pl_en_parser


def dsl_lookup(term: str, direction: str = "en-pl") -> Optional[Dict[str, any]]:
    """
    Look up a term in the DSL dictionaries.

    Args:
        term: The term to look up
        direction: "en-pl" or "pl-en"

    Returns:
        Dict with 'source_terms', 'target_terms', and 'pairs' lists, or None if not found
    """
    if direction == "en-pl":
        parser = get_en_pl_parser()
    else:
        parser = get_pl_en_parser()

    entry = parser.lookup(term)
    if not entry:
        return None

    source_terms = entry.get_all_source_terms()
    target_terms = entry.get_all_target_terms()

    # Build pairs: start with (headword, main_translation) then add all variants
    pairs = []
    if entry.headword and entry.main_translation:
        pairs.append((entry.headword, entry.main_translation))

    # Add all variants as pairs
    for source, target in entry.variants:
        pairs.append((source, target))

    return {
        "headword": entry.headword,
        "source_terms": source_terms,
        "target_terms": target_terms,
        "source_text": ", ".join(source_terms),
        "target_text": ", ".join(target_terms),
        "pairs": pairs,
    }
