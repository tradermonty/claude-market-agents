#!/usr/bin/env python3
"""
Earnings Trade HTML Report Parser

Parses earnings trade analysis HTML reports (94+ files, 7+ format variants)
and extracts trade candidates with ticker, score, grade, and price.
"""

import os
import re
import logging
from dataclasses import dataclass, field, replace
from typing import List, Optional
from pathlib import Path

from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)


@dataclass
class TradeCandidate:
    ticker: str
    report_date: str       # YYYY-MM-DD from filename
    grade: str             # A/B/C/D
    grade_source: str      # "html" | "inferred"
    score: Optional[float] = None  # 0-100, None if only grade available
    price: Optional[float] = None
    gap_size: Optional[float] = None
    company_name: Optional[str] = None


class EarningsReportParser:
    """Multi-format HTML report parser with fallback chains."""

    TICKER_RE = re.compile(r'^\$?([A-Z][A-Z0-9./-]{0,9})$')
    TICKER_FROM_TEXT_RE = re.compile(r'\$?([A-Z][A-Z0-9./-]{0,6})')
    SCORE_PTS_RE = re.compile(r'(\d+\.?\d*)\s*(?:pts|points|/\s*100)', re.I)
    SCORE_SLASH100_RE = re.compile(r'(\d+\.?\d*)\s*/\s*100')
    SCORE_ELEMENT_RE = re.compile(r'\d+/5')
    GRADE_CLASS_RE = re.compile(r'grade-([abcd])(?![a-z])', re.I)
    GRADE_TEXT_RE = re.compile(r'([ABCD])-(?:Grade|GRADE)', re.I)
    GRADE_LETTER_RE = re.compile(r'^([ABCD])$')
    DATE_FROM_FILENAME_RE = re.compile(r'(\d{4}-\d{2}-\d{2})')
    PRICE_RE = re.compile(r'\$(\d+\.?\d*)')

    def parse_all_reports(self, reports_dir: str) -> List[TradeCandidate]:
        """Parse all earnings trade HTML reports in a directory."""
        reports_path = Path(reports_dir)
        html_files = sorted(reports_path.glob("earnings_trade_analysis_*.html"))

        logger.info(f"Found {len(html_files)} earnings trade HTML files in {reports_dir}")

        all_candidates = []
        for html_file in html_files:
            try:
                candidates = self.parse_single_report(str(html_file))
                all_candidates.extend(candidates)
            except Exception as e:
                logger.error(f"Failed to parse {html_file.name}: {e}")

        deduped = self._deduplicate(all_candidates)
        logger.info(f"Parsed {len(deduped)} unique trade candidates from {len(html_files)} files")
        return deduped

    def parse_single_report(self, filepath: str) -> List[TradeCandidate]:
        """Parse a single HTML report file."""
        filename = os.path.basename(filepath)
        date_match = self.DATE_FROM_FILENAME_RE.search(filename)
        if not date_match:
            logger.warning(f"Cannot extract date from filename: {filename}")
            return []
        report_date = date_match.group(1)

        with open(filepath, 'r', encoding='utf-8') as f:
            html = f.read()

        soup = BeautifulSoup(html, 'html.parser')

        # Check for no-stocks page
        if self._is_no_stocks_page(soup):
            logger.debug(f"{filename}: no-stocks page, skipping")
            return []

        # Remove upcoming/summary sections to avoid extracting non-target tickers
        self._remove_excluded_sections(soup)

        # Find stock cards
        cards = self._find_stock_cards(soup)
        if not cards:
            logger.debug(f"{filename}: no stock cards found")
            return []

        candidates = []
        for card in cards:
            candidate = self._extract_candidate(card, report_date, soup)
            if candidate:
                if '/' in candidate.ticker:
                    # Split compound ticker (e.g., "UA/UAA") into separate candidates
                    for part in candidate.ticker.split('/'):
                        clean = self._clean_ticker(part)
                        if clean:
                            candidates.append(replace(candidate, ticker=clean))
                else:
                    candidates.append(candidate)

        logger.debug(f"{filename}: extracted {len(candidates)} candidates")
        return candidates

    def _is_no_stocks_page(self, soup: BeautifulSoup) -> bool:
        """Detect no-stocks pages."""
        if soup.find(class_='no-stocks-card'):
            return True
        if soup.find(class_='no-stocks-title'):
            return True
        if soup.find(string=re.compile(r'No.*(?:Qualifying|Earnings).*Stocks.*(?:Found|Available)', re.I)):
            return True
        return False

    def _remove_excluded_sections(self, soup: BeautifulSoup) -> None:
        """Remove upcoming and summary sections from the DOM."""
        for tag_name in ['section', 'div']:
            for el in soup.find_all(tag_name, class_=re.compile(r'upcoming|summary-section')):
                el.decompose()

    def _find_stock_cards(self, soup: BeautifulSoup) -> list:
        """Find all stock card elements."""
        cards = soup.find_all(['div', 'article'], class_=re.compile(r'stock-card'))
        if cards:
            return cards

        # Fallback: look for grade-section containers with individual items
        grade_sections = soup.find_all(['section', 'div'], class_=re.compile(r'grade-section'))
        if grade_sections:
            cards = []
            for section in grade_sections:
                items = section.find_all(['div', 'article'], class_=re.compile(r'stock-card|stock-item'))
                cards.extend(items)
            if cards:
                return cards

        return []

    def _extract_candidate(self, card, report_date: str, soup: BeautifulSoup) -> Optional[TradeCandidate]:
        """Extract a TradeCandidate from a stock card element."""
        ticker = self._extract_ticker(card)
        if not ticker:
            return None

        # Find parent grade section for grade context
        parent_section = card.find_parent(['section', 'div'], class_=re.compile(r'grade-section|grade-[abcd]'))

        score = self._extract_score(card)
        grade, grade_source = self._extract_grade(card, parent_section)

        if score is None and grade is None:
            logger.debug(f"Skipping {ticker} on {report_date}: no score or grade")
            return None

        # If no grade from HTML, infer from score
        if grade is None and score is not None:
            grade = self._infer_grade(score)
            grade_source = "inferred"

        # score may remain None if only grade is available (no default assignment)

        price = self._extract_price(card)
        gap_size = self._extract_gap_size(card)
        company_name = self._extract_company_name(card)

        return TradeCandidate(
            ticker=ticker,
            report_date=report_date,
            grade=grade,
            grade_source=grade_source,
            score=score,
            price=price,
            gap_size=gap_size,
            company_name=company_name,
        )

    def _extract_ticker(self, card) -> Optional[str]:
        """Extract ticker with fallback chain."""
        # 1. div.stock-ticker — prefer nested ticker-symbol span to avoid
        #    picking up adjacent text like "+11.22%"
        el = card.find(class_='stock-ticker')
        if el:
            symbol_span = el.find(class_='ticker-symbol')
            text = symbol_span.get_text(strip=True) if symbol_span else el.get_text(strip=True)
            ticker = self._clean_ticker(text)
            if ticker:
                return ticker

        # 2. span.ticker
        el = card.find('span', class_='ticker')
        if el:
            ticker = self._clean_ticker(el.get_text(strip=True))
            if ticker:
                return ticker

        # 3. div.ticker — may contain "AEO - $17.28" or "NYSE: USB"
        el = card.find('div', class_='ticker')
        if el:
            text = el.get_text(strip=True)
            if ':' in text:
                text = text.split(':')[-1].strip()
            else:
                text = text.split(' - ')[0].split('(')[0]
            ticker = self._clean_ticker(text)
            if ticker:
                return ticker

        # 4. h2 heading (may contain "$FBK" or "$CRDO ⭐⭐⭐")
        el = card.find('h2')
        if el:
            text = el.get_text(strip=True)
            m = self.TICKER_FROM_TEXT_RE.search(text)
            if m:
                ticker = self._clean_ticker(m.group(1))
                if ticker:
                    return ticker

        # 5. h3 heading (e.g. "$CIFR - Cipher Mining Inc")
        el = card.find('h3')
        if el:
            text = el.get_text(strip=True)
            m = self.TICKER_FROM_TEXT_RE.search(text)
            if m:
                ticker = self._clean_ticker(m.group(1))
                if ticker:
                    return ticker

        return None

    def _clean_ticker(self, raw: str) -> Optional[str]:
        """Normalize and validate ticker string."""
        if not raw:
            return None
        ticker = raw.strip().lstrip('$').strip()
        ticker = re.sub(r'\s+', '', ticker)
        if self.TICKER_RE.match(ticker):
            return ticker
        return None

    def _extract_score(self, card) -> Optional[float]:
        """Extract total score with fallback chain. Ignores element scores (x/5)."""
        # 1. score-breakdown "Total Score" row -> x/100
        breakdown = card.find(class_='score-breakdown')
        if breakdown:
            # Check h3 heading for total score (e.g., "94/100 Points")
            for h3 in breakdown.find_all('h3'):
                text = h3.get_text(strip=True)
                m = self.SCORE_SLASH100_RE.search(text)
                if m:
                    val = float(m.group(1))
                    if val > 5:
                        return self._validate_score(val)
                m = self.SCORE_PTS_RE.search(text)
                if m:
                    val = float(m.group(1))
                    if val > 5:
                        return self._validate_score(val)

            # Check score-item with "Total" label (e.g., "Weighted Total Score: 88/100")
            for item in breakdown.find_all(class_='score-item'):
                name_el = item.find(class_='score-item-name')
                if name_el and 'Total' in name_el.get_text():
                    val_el = item.find(class_='score-item-value')
                    if val_el:
                        text = val_el.get_text(strip=True)
                        m = self.SCORE_SLASH100_RE.search(text)
                        if m:
                            val = float(m.group(1))
                            if val > 5:
                                return self._validate_score(val)

            rows = breakdown.find_all(class_='score-row') or breakdown.find_all('div')
            for row in rows:
                label = row.find(class_='score-label')
                if label and 'Total Score' in label.get_text():
                    value_el = row.find(class_='score-value')
                    if value_el:
                        text = value_el.get_text(strip=True)
                        m = self.SCORE_SLASH100_RE.search(text)
                        if m:
                            return self._validate_score(float(m.group(1)))
                        # Try plain number
                        m = re.search(r'(\d+\.?\d*)', text)
                        if m:
                            val = float(m.group(1))
                            if val > 5:
                                return self._validate_score(val)

        # 2. div.score-value (skip if x/5 element score)
        for el in card.find_all(class_='score-value'):
            # Skip elements inside score-breakdown (already handled above)
            if el.find_parent(class_='score-breakdown'):
                continue
            text = el.get_text(strip=True)
            if self.SCORE_ELEMENT_RE.search(text):
                continue
            m = self.SCORE_SLASH100_RE.search(text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)
            m = self.SCORE_PTS_RE.search(text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)
            try:
                val = float(re.sub(r'[^\d.]', '', text))
                if val > 5:
                    return self._validate_score(val)
            except ValueError:
                continue

        # 3. div.stock-score-value
        el = card.find(class_='stock-score-value')
        if el:
            text = el.get_text(strip=True)
            m = self.SCORE_SLASH100_RE.search(text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)
            m = self.SCORE_PTS_RE.search(text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)
            try:
                val = float(re.sub(r'[^\d.]', '', text))
                if val > 5:
                    return self._validate_score(val)
            except ValueError:
                pass

        # 4. div.score -> "88 pts" or "67.0 / 100" or bare "89.5"
        el = card.find(class_='score')
        if el:
            text = el.get_text(strip=True)
            if not self.SCORE_ELEMENT_RE.search(text):
                m = self.SCORE_PTS_RE.search(text)
                if m:
                    val = float(m.group(1))
                    if val > 5:
                        return self._validate_score(val)
                # Bare number fallback (e.g. "89.5" with no pts/100 suffix)
                try:
                    val = float(text)
                    if val > 5:
                        return self._validate_score(val)
                except ValueError:
                    pass

        # 4b. span.score-number -> "91.5"
        el = card.find(class_='score-number')
        if el:
            try:
                val = float(el.get_text(strip=True))
                if val > 5:
                    return self._validate_score(val)
            except ValueError:
                pass

        # 5. h3/h4 containing "Score" -> "Score: 69 pts" or "Score: 88/100"
        for heading in card.find_all(['h3', 'h4']):
            if heading.find_parent(class_='score-breakdown'):
                continue  # Already handled in step 1
            text = heading.get_text(strip=True)
            if 'Score' in text or 'score' in text:
                m = self.SCORE_SLASH100_RE.search(text)
                if m:
                    val = float(m.group(1))
                    if val > 5:
                        return self._validate_score(val)
                m = self.SCORE_PTS_RE.search(text)
                if m:
                    val = float(m.group(1))
                    if val > 5:
                        return self._validate_score(val)

        # 6. grade-badge / rating-badge text -> "88.5 pts" or "A-Grade 78 pts"
        for badge in card.find_all(class_=re.compile(r'grade-badge|rating-badge')):
            text = badge.get_text(strip=True)
            m = self.SCORE_PTS_RE.search(text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)
            m = self.SCORE_SLASH100_RE.search(text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)

        # 6b. grade-score class -> "74.0 points"
        el = card.find(class_='grade-score')
        if el:
            text = el.get_text(strip=True)
            m = self.SCORE_PTS_RE.search(text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)

        # 6c. score-badge -> "86.0A-Grade..." or "74.0Points"
        for badge in card.find_all(class_='score-badge'):
            text = badge.get_text(strip=True)
            m = self.SCORE_PTS_RE.search(text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)
            m = self.SCORE_SLASH100_RE.search(text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)
            # Leading number fallback (e.g., "86.0A-Grade")
            m = re.match(r'(\d+\.?\d*)', text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)

        # 7. total-score-value span
        el = card.find(class_='total-score-value')
        if el:
            text = el.get_text(strip=True)
            m = re.search(r'(\d+\.?\d*)', text)
            if m:
                val = float(m.group(1))
                if val > 5:
                    return self._validate_score(val)

        # 8. total-score section with total-value
        total_score_el = card.find(class_='total-score')
        if total_score_el:
            value_el = total_score_el.find(class_='total-value')
            if value_el:
                text = value_el.get_text(strip=True)
                # Try pts/points/100 format first (e.g., "76.0 pts")
                m = self.SCORE_PTS_RE.search(text)
                if m:
                    val = float(m.group(1))
                    if val > 5:
                        return self._validate_score(val)
                # Try percentage: "4.10 / 5.00 (82%)"
                m = re.search(r'\((\d+)%\)', text)
                if m:
                    val = float(m.group(1))
                    if val > 5:
                        return self._validate_score(val)

        return None

    def _validate_score(self, score: float) -> Optional[float]:
        """Validate score is in expected range."""
        if 5 < score <= 100:
            return score
        return None

    def _extract_grade(self, card, parent_section=None) -> tuple:
        """Extract grade with fallback chain. Returns (grade, source)."""
        # 1. Card CSS class -> a-grade, b-grade, etc.
        card_classes = ' '.join(card.get('class', []))
        m = re.search(r'([abcd])-grade', card_classes, re.I)
        if m:
            return m.group(1).upper(), "html"
        # 1b. grade-[abcd]-card pattern (e.g., "stock-card grade-a-card")
        m = self.GRADE_CLASS_RE.search(card_classes)
        if m:
            return m.group(1).upper(), "html"

        # 2. stock-grade element (span or div) - check class and text
        # Note: design doc step 6 (single-letter class) is merged here in step 2
        for el in card.find_all(class_='stock-grade'):
            cls_list = el.get('class', [])
            cls = ' '.join(cls_list)
            # Check grade-a/b/c/d pattern in class
            m = self.GRADE_CLASS_RE.search(cls)
            if m:
                return m.group(1).upper(), "html"
            # Check single-letter class: stock-grade a/b/c/d
            for c in cls_list:
                if c in ('a', 'b', 'c', 'd'):
                    return c.upper(), "html"
            # Check text content
            text = el.get_text(strip=True)
            if self.GRADE_LETTER_RE.match(text):
                return text.upper(), "html"

        # 3. div.grade-badge / rating-badge / large-grade / score-badge class
        for badge in card.find_all(class_=re.compile(
                r'grade-badge|rating-badge|large-grade|score-badge')):
            cls_list = badge.get('class', [])
            cls = ' '.join(cls_list)
            m = self.GRADE_CLASS_RE.search(cls)
            if m:
                return m.group(1).upper(), "html"
            # [abcd]-grade pattern (e.g., "grade-badge b-grade")
            m = re.search(r'([abcd])-grade', cls, re.I)
            if m:
                return m.group(1).upper(), "html"
            # Single-letter class (e.g., "grade-badge a")
            for c in cls_list:
                if c in ('a', 'b', 'c', 'd'):
                    return c.upper(), "html"
            text = badge.get_text(strip=True)
            m = self.GRADE_TEXT_RE.search(text)
            if m:
                return m.group(1).upper(), "html"
            # Single-letter text (e.g., bare "B" or "D")
            if self.GRADE_LETTER_RE.match(text):
                return text.upper(), "html"

        # 4. div.stock-score-label -> "B-GRADE"
        el = card.find(class_='stock-score-label')
        if el:
            text = el.get_text(strip=True)
            m = self.GRADE_TEXT_RE.search(text)
            if m:
                return m.group(1).upper(), "html"

        # 5. div.grade / span.grade text
        for tag in ['div', 'span']:
            el = card.find(tag, class_='grade')
            if el:
                text = el.get_text(strip=True)
                if self.GRADE_LETTER_RE.match(text):
                    return text.upper(), "html"
                m = self.GRADE_TEXT_RE.search(text)
                if m:
                    return m.group(1).upper(), "html"

        # 6. Parent section grade-header class (with extended fallbacks)
        if parent_section:
            header = parent_section.find(class_=re.compile(r'grade-header'))
            if header:
                cls = ' '.join(header.get('class', []))
                # 6a. grade-[abcd] in header class
                m = self.GRADE_CLASS_RE.search(cls)
                if m:
                    return m.group(1).upper(), "html"
                # 6b. [abcd]-header pattern (e.g., "grade-header a-header")
                m = re.search(r'([abcd])-header', cls, re.I)
                if m:
                    return m.group(1).upper(), "html"
                # 6c. Single-letter class (e.g., "grade-header a")
                for c in header.get('class', []):
                    if c in ('a', 'b', 'c', 'd'):
                        return c.upper(), "html"
                # 6d. Child badge elements
                for badge in header.find_all(class_=re.compile(
                        r'grade-badge|rating-badge|score-badge')):
                    badge_cls = ' '.join(badge.get('class', []))
                    m = self.GRADE_CLASS_RE.search(badge_cls)
                    if m:
                        return m.group(1).upper(), "html"
                    badge_text = badge.get_text(strip=True)
                    m = self.GRADE_TEXT_RE.search(badge_text)
                    if m:
                        return m.group(1).upper(), "html"
                # 6e. Header text content (e.g., "A-GRADE ...")
                header_text = header.get_text(strip=True)
                m = self.GRADE_TEXT_RE.search(header_text)
                if m:
                    return m.group(1).upper(), "html"
            # Also check parent section class itself
            sec_cls = ' '.join(parent_section.get('class', []))
            m = self.GRADE_CLASS_RE.search(sec_cls)
            if m:
                return m.group(1).upper(), "html"

        return None, None

    def _infer_grade(self, score: float) -> str:
        """Infer grade from score."""
        if score >= 85:
            return 'A'
        elif score >= 70:
            return 'B'
        elif score >= 55:
            return 'C'
        else:
            return 'D'

    def _extract_price(self, card) -> Optional[float]:
        """Extract stock price with fallback chain."""
        # 1. metric-value where metric-label = "Price" or "Current Price"
        for metric in card.find_all(class_=re.compile(r'metric-box|metric')):
            label_el = metric.find(class_=re.compile(r'metric-label|label'))
            if label_el:
                label = label_el.get_text(strip=True).lower()
                if 'price' in label and 'target' not in label:
                    value_el = metric.find(class_=re.compile(r'metric-value|value'))
                    if value_el:
                        price = self._parse_price(value_el.get_text(strip=True))
                        if price and price > 0:
                            return price

        # 2. span.price-current
        el = card.find(class_='price-current')
        if el:
            price = self._parse_price(el.get_text(strip=True))
            if price and price > 0:
                return price

        # 3. div.price-value
        el = card.find(class_='price-value')
        if el:
            price = self._parse_price(el.get_text(strip=True))
            if price and price > 0:
                return price

        # 4. div.ticker text with price (e.g. "AEO - $17.28")
        el = card.find(class_='ticker')
        if el:
            m = self.PRICE_RE.search(el.get_text())
            if m:
                price = float(m.group(1))
                if price > 0:
                    return price

        # 5. General metric-value with "Price" context
        for el in card.find_all(class_='metric-value'):
            prev = el.find_previous_sibling()
            if prev and 'price' in prev.get_text(strip=True).lower():
                price = self._parse_price(el.get_text(strip=True))
                if price and price > 0:
                    return price

        return None

    def _parse_price(self, text: str) -> Optional[float]:
        """Parse price from text like '$56.31' or '56.31'."""
        m = re.search(r'\$?(\d+\.?\d*)', text)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
        return None

    def _extract_gap_size(self, card) -> Optional[float]:
        """Extract gap-up size percentage."""
        for metric in card.find_all(class_=re.compile(r'metric-box|metric')):
            label_el = metric.find(class_=re.compile(r'metric-label|label'))
            if label_el:
                label = label_el.get_text(strip=True).lower()
                if 'gap' in label:
                    value_el = metric.find(class_=re.compile(r'metric-value|value'))
                    if value_el:
                        text = value_el.get_text(strip=True)
                        m = re.search(r'(\d+\.?\d*)%', text)
                        if m:
                            return float(m.group(1))

        # Look for gap in tech badges or other locations
        for el in card.find_all(class_=re.compile(r'tech-badge|badge')):
            text = el.get_text(strip=True).lower()
            if 'gap' in text:
                m = re.search(r'(\d+\.?\d*)%', text)
                if m:
                    return float(m.group(1))

        return None

    def _extract_company_name(self, card) -> Optional[str]:
        """Extract company name."""
        # Try stock-company or company-name class
        for cls in ['stock-company', 'company-name', 'company', 'stock-name']:
            el = card.find(class_=cls)
            if el:
                name = el.get_text(strip=True)
                if name and len(name) > 1:
                    return name

        # Try subtitle or description
        el = card.find(class_=re.compile(r'subtitle|stock-sector'))
        if el:
            return el.get_text(strip=True)

        return None

    def _deduplicate(self, candidates: List[TradeCandidate]) -> List[TradeCandidate]:
        """Deduplicate by (report_date, ticker). First seen wins; score=None replaced by scored entry."""
        seen = {}
        for c in candidates:
            key = (c.report_date, c.ticker)
            if key not in seen:
                seen[key] = c
            else:
                # Only replace if existing has no score and new one does
                if seen[key].score is None and c.score is not None:
                    seen[key] = c

        return list(seen.values())
