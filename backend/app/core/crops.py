"""
Справочник культур заказчика: цвета для карты + таксономия.

Источник — backend/data/crop_colors.csv (выгрузка из 1С): 193 культуры с
полями name / shortName / description / category / subCategory / color /
cropCode1c. Один цвет приходится на группу культур (например, все люцерны —
#00B050), поэтому справочник задаёт не только палитру, но и группировку.

Задача модуля — сопоставить «грязные» названия культур из истории севооборота
(в данных встречается 1060 различных написаний: разный регистр, годы жизни,
подсевы, комбинации через «;») с 193 каноническими записями справочника.
Сопоставление детерминированное и объяснимое: каждый шаг возвращает признак
того, каким правилом культура была опознана.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from app.config import settings

CROP_COLORS_FILE = "crop_colors.csv"

# Цвет для культур, которые не удалось опознать в справочнике. Совпадает с
# «нет данных» на фронтенде.
FALLBACK_COLOR = "#94A3B8"

# В выгрузке 1С у части строк цвет не заполнен и стоит белый — на карте такое
# поле сливается с подложкой. Такие строки перекрашиваются в модальный цвет
# своей группы shortName (см. CropTaxonomy._repair_placeholder_colors).
PLACEHOLDER_COLOR = "#FFFFFF"

# Категории справочника, означающие «без производства» — не культура.
NON_PRODUCTION_CATEGORIES = frozenset({"Пар", "Пашня без производства"})

# Азотфиксаторы. Категория «Зернобобовые и крупяные» для этого не годится —
# в неё попадают гречиха и просо, которые азот не фиксируют, а бобовые
# многолетние травы (люцерна, клевер, эспарцет) лежат в «Кормовых».
# Поэтому опознаём по основе названия.
LEGUME_STEMS: tuple[str, ...] = (
    "горох", "соя", "нут", "чечевиц", "люпин", "вика", "фасол", "бобы",
    "клевер", "кл.кр", "кл.бел", "люцерн", "люц.", "эспарцет", "эсп.",
    "донник", "козлятник", "лядвенец", "галега", "зернобоб",
)

# Объёмные культуры, для которых логистическое плечо до элеватора реально
# бьёт по экономике (см. описание фактора «Логистическое плечо»).
BULK_CROP_STEMS: tuple[str, ...] = (
    "свекл", "силос", "корнаж", "картофел", "зелён.корм", "зелен.корм",
    "сенаж", "подстилк",
)

# Культуры прямого действия навоза КРС (1-й год) — из описания фактора
# «Культуры прямого действия навоза КРС».
MANURE_RESPONSIVE_STEMS: tuple[str, ...] = ("кукуруз", "свекл", "подсолнеч")

# Культуры с высокой уборочной влажностью, требующие сушки/особых мощностей
# хранения (фактор «Мощности хранения и сушки»).
HIGH_MOISTURE_STEMS: tuple[str, ...] = ("кукуруза зерно", "кукуруза гибрид", "подсолнеч")

# «1 г.ж.», «2г.ж», «пр.лет», «прошлых лет» — всё это указание на год жизни
# многолетней культуры. Для цвета и таксономии год жизни не важен (справочник
# даёт один цвет на всю группу), поэтому он схлопывается в плейсхолдер.
_YEAR_OF_LIFE_RE = re.compile(r"(?:\d+\s*г\.?\s*ж\.?|пр\.?\s*лет|прошлых\s+лет)")
_UNDERSOWING_RE = re.compile(r"\s*с\s*подс\.?\s*\([^)]*\)\s*")
_WS_RE = re.compile(r"\s+")
_LOOSE_RE = re.compile(r"[^0-9a-zа-я]+")
_TRAILING_PUNCT_RE = re.compile(r"[\s.,;:+\-]+$")


@dataclass(frozen=True)
class CropInfo:
    """Запись справочника + то, каким правилом она была найдена."""
    name: str
    short_name: str
    description: str
    category: str
    sub_category: str
    color: str
    code_1c: str
    matched_by: str = "exact"


def _norm(text: str) -> str:
    """Каноническая форма названия для сравнения."""
    s = str(text).replace(" ", " ").replace("ё", "е").replace("Ё", "Е")
    s = s.replace("_", " ").strip().lower()
    s = _WS_RE.sub(" ", s)
    # «мн.тр. корм» и «мн.тр.корм» должны совпасть
    s = s.replace(" .", ".").replace(". ", ".")
    return s


def _family_key(normalized: str) -> str:
    """Ключ «семейства» — год жизни заменён плейсхолдером.

    «люцерна 2 г.ж.корм», «люцерна 4 г.ж.корм» и «люцерна пр.лет корм» дают
    один ключ, а «люцерна 1 г.ж.сем.» — другой (корм ≠ семена).
    """
    return _YEAR_OF_LIFE_RE.sub("<гж>", normalized)


def _loose(normalized: str) -> str:
    """
    «Свободный» ключ — только буквы и цифры. Гасит расхождения в точках,
    пробелах, скобках и дефисах: «Фестулолиум пр.лет. сем.» и
    «Фестулолиум пр.лет сем.» дают одно и то же.
    """
    return _LOOSE_RE.sub("", normalized)


def _strip_undersowing(normalized: str) -> str:
    return _WS_RE.sub(" ", _UNDERSOWING_RE.sub(" ", normalized)).strip()


def _split_parts(normalized: str) -> list[str]:
    """Разбить составное название по «;» и «,» на непустые части."""
    return [p.strip() for p in re.split(r"[;,]", normalized) if p.strip()]


def _collapse_unambiguous(groups: dict[str, list[CropInfo]]) -> dict[str, CropInfo]:
    """
    Свернуть индекс «ключ → список записей» в «ключ → запись», выбросив ключи,
    под которыми лежат записи разного цвета: для них сопоставление было бы
    произвольным, и лучше пропустить культуру дальше по цепочке правил.
    """
    out: dict[str, CropInfo] = {}
    for key, items in groups.items():
        if len({i.color for i in items}) == 1:
            out[key] = items[0]
    return out


@lru_cache(maxsize=1)
def load_crop_reference(path: str | None = None) -> tuple[CropInfo, ...]:
    """Прочитать crop_colors.csv (кодировка utf-8-sig — файл с BOM)."""
    csv_path = Path(path) if path else settings.data_dir / CROP_COLORS_FILE
    if not csv_path.exists():
        return ()
    with csv_path.open(encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    return tuple(
        CropInfo(
            name=(r.get("name") or "").strip(),
            short_name=(r.get("shortName") or "").strip(),
            description=(r.get("description") or "").strip(),
            category=(r.get("category") or "").strip(),
            sub_category=(r.get("subCategory") or "").strip(),
            color=(r.get("color") or "").strip().upper(),
            code_1c=(r.get("cropCode1c") or "").strip(),
        )
        for r in rows
        if (r.get("name") or "").strip()
    )


class CropTaxonomy:
    """
    Индекс справочника культур с многоступенчатым сопоставлением имён.

    Порядок правил (первое сработавшее выигрывает):
      1. exact          — совпадение имени как есть;
      2. normalized     — регистр, пробелы, ё/е, точки, подчёркивания;
      3. year_of_life   — «Люцерна 2 г.ж.корм» → «Люцерна пр.лет корм»;
      4. loose          — сравнение по буквам и цифрам без пунктуации;
      5. undersowing    — отбрасывается «с подс.(...)»;
      6. combination    — «Кукуруза силос; Пар», «Залеж, лес» → первая часть,
                          которая нашлась;
      7. prefix         — имя является префиксом группы справочника, и все
                          кандидаты одного цвета;
      8. short_name     — совпадение с колонкой shortName.
    Не опознанные культуры возвращают None; цвет для них берётся из
    детерминированной запасной палитры.
    """

    def __init__(self, reference: tuple[CropInfo, ...] | None = None):
        source = reference if reference is not None else load_crop_reference()
        self.reference = self._repair_placeholder_colors(source)
        self._by_exact: dict[str, CropInfo] = {}
        self._by_norm: dict[str, CropInfo] = {}
        # Ключи, под которыми сгруппировалось несколько записей: годятся, только
        # если у всех кандидатов один цвет — иначе выбор был бы произвольным.
        short: dict[str, list[CropInfo]] = {}
        family: dict[str, list[CropInfo]] = {}
        loose: dict[str, list[CropInfo]] = {}
        loose_family: dict[str, list[CropInfo]] = {}
        for info in self.reference:
            self._by_exact.setdefault(info.name, info)
            normalized = _norm(info.name)
            self._by_norm.setdefault(normalized, info)
            family.setdefault(_family_key(normalized), []).append(info)
            loose.setdefault(_loose(normalized), []).append(info)
            loose_family.setdefault(_loose(_family_key(normalized)), []).append(info)
            if info.short_name:
                short.setdefault(_norm(info.short_name), []).append(info)
        self._by_short = _collapse_unambiguous(short)
        self._by_family = _collapse_unambiguous(family)
        self._by_loose = _collapse_unambiguous(loose)
        self._by_loose_family = _collapse_unambiguous(loose_family)
        # Отсортированный список нормализованных имён — для префиксного поиска.
        self._norm_names = sorted(self._by_norm)

    @staticmethod
    def _repair_placeholder_colors(
        reference: tuple[CropInfo, ...]
    ) -> tuple[CropInfo, ...]:
        """
        Заменить белый цвет-заглушку на модальный цвет группы shortName.

        В crop_colors.csv 10 строк имеют #FFFFFF и пустые category/subCategory —
        по сути незаполненные записи. Отрисованные белым, они невидимы на карте,
        поэтому берём цвет их группы («Кострец» → цвет остальных костров).
        Если у группы цвета нет, строка остаётся белой и на карте её перекроет
        общий фолбэк.
        """
        by_group: dict[str, list[str]] = {}
        for info in reference:
            if info.short_name and info.color and info.color != PLACEHOLDER_COLOR:
                by_group.setdefault(_norm(info.short_name), []).append(info.color)

        repaired: list[CropInfo] = []
        for info in reference:
            if info.color and info.color != PLACEHOLDER_COLOR:
                repaired.append(info)
                continue
            group = by_group.get(_norm(info.short_name or ""))
            if not group:
                repaired.append(info)
                continue
            modal = max(set(group), key=group.count)
            repaired.append(CropInfo(
                name=info.name, short_name=info.short_name,
                description=info.description, category=info.category,
                sub_category=info.sub_category, color=modal,
                code_1c=info.code_1c, matched_by=info.matched_by,
            ))
        return tuple(repaired)

    # ------------------------------------------------------------------
    # Сопоставление
    # ------------------------------------------------------------------
    def resolve(self, culture) -> CropInfo | None:
        if culture is None:
            return None
        raw = str(culture).strip()
        if not raw:
            return None
        return self._resolve_cached(raw)

    @lru_cache(maxsize=4096)  # noqa: B019 — экземпляр живёт всё время процесса
    def _resolve_cached(self, raw: str) -> CropInfo | None:
        hit = self._by_exact.get(raw)
        if hit:
            return hit

        normalized = _norm(raw)
        hit = self._by_norm.get(normalized)
        if hit:
            return self._with(hit, "normalized")

        hit = self._by_family.get(_family_key(normalized))
        if hit:
            return self._with(hit, "year_of_life")

        hit = (self._by_loose.get(_loose(normalized))
               or self._by_loose_family.get(_loose(_family_key(normalized))))
        if hit:
            return self._with(hit, "loose")

        without_undersowing = _strip_undersowing(normalized)
        if without_undersowing != normalized:
            hit = self._lookup_simple(without_undersowing)
            if hit:
                return self._with(hit, "undersowing")

        # «Кукуруза силос; Пар» и «Залеж, лес»: разбираем по частям. Запятая
        # встречается и внутри канонических имён («Залежь, лес»), поэтому
        # правило запускается только после того, как прямые совпадения не
        # сработали.
        if ";" in normalized or "," in normalized:
            for part in _split_parts(normalized):
                hit = (self._lookup_simple(part)
                       or self._lookup_simple(_strip_undersowing(part)))
                if hit:
                    return self._with(hit, "combination")
            for part in _split_parts(normalized):
                hit = self._by_prefix(part)
                if hit:
                    return self._with(hit, "combination")

        hit = self._by_prefix(without_undersowing or normalized)
        if hit:
            return self._with(hit, "prefix")

        hit = self._by_short.get(without_undersowing or normalized)
        if hit:
            return self._with(hit, "short_name")

        return None

    def _lookup_simple(self, normalized: str) -> CropInfo | None:
        """Точное/семейное/свободное совпадение без запуска остальных правил."""
        if not normalized:
            return None
        return (self._by_norm.get(normalized)
                or self._by_family.get(_family_key(normalized))
                or self._by_loose.get(_loose(normalized))
                or self._by_loose_family.get(_loose(_family_key(normalized))))

    def _by_prefix(self, normalized: str) -> CropInfo | None:
        """
        Имя — префикс записей справочника. Годится, только если все кандидаты
        одного цвета: иначе выбор был бы произвольным.
        «оз.пшеница мягк» → «оз.пшеница мягкая» (одна запись);
        «пшеница» → и озимая, и яровая, но обе #F5B413 — тоже годится.
        """
        stem = _TRAILING_PUNCT_RE.sub("", normalized)
        if len(stem) < 4:
            return None
        candidates = [self._by_norm[n] for n in self._norm_names
                      if n.startswith(stem)]
        if not candidates:
            return None
        if len({c.color for c in candidates}) > 1:
            return None
        return candidates[0]

    @staticmethod
    def _with(info: CropInfo, matched_by: str) -> CropInfo:
        return CropInfo(
            name=info.name, short_name=info.short_name,
            description=info.description, category=info.category,
            sub_category=info.sub_category, color=info.color,
            code_1c=info.code_1c, matched_by=matched_by,
        )

    # ------------------------------------------------------------------
    # Производные свойства
    # ------------------------------------------------------------------
    def color(self, culture) -> str:
        return self._effective_color(self.resolve(culture), culture)

    @staticmethod
    def _effective_color(info: CropInfo | None, culture) -> str:
        """Цвет справочника; белая заглушка и пустое значение → запасная палитра."""
        if info and info.color and info.color != PLACEHOLDER_COLOR:
            return info.color
        return fallback_color(culture)

    def category(self, culture) -> str | None:
        info = self.resolve(culture)
        return info.category or None if info else None

    def short_name(self, culture) -> str | None:
        info = self.resolve(culture)
        return info.short_name or None if info else None

    def code_1c(self, culture) -> str | None:
        info = self.resolve(culture)
        return info.code_1c or None if info else None

    def is_legume(self, culture) -> bool:
        """Азотфиксатор — по основе названия культуры и её каноничного имени."""
        return self._match_stems(culture, LEGUME_STEMS)

    def is_bulk(self, culture) -> bool:
        """Объёмная культура (логистическое плечо бьёт по экономике)."""
        return self._match_stems(culture, BULK_CROP_STEMS)

    def is_manure_responsive(self, culture) -> bool:
        """Культура прямого действия навоза КРС (1-й год)."""
        return self._match_stems(culture, MANURE_RESPONSIVE_STEMS)

    def is_high_moisture(self, culture) -> bool:
        """Требует сушки/мощностей хранения при уборке."""
        return self._match_stems(culture, HIGH_MOISTURE_STEMS)

    def is_non_production(self, culture) -> bool:
        """Пар, залежь, стройка и прочее «без производства»."""
        info = self.resolve(culture)
        return bool(info and info.category in NON_PRODUCTION_CATEGORIES)

    def _match_stems(self, culture, stems: tuple[str, ...]) -> bool:
        if culture is None:
            return False
        haystacks = [_norm(culture)]
        info = self.resolve(culture)
        if info:
            haystacks.append(_norm(info.name))
            if info.short_name:
                haystacks.append(_norm(info.short_name))
        return any(stem in hay for stem in stems for hay in haystacks)

    def palette(self, cultures) -> dict[str, dict]:
        """
        Палитра и таксономия для набора культур — то, что уходит на фронтенд.
        Ключ — исходное имя культуры, как оно приходит в плане.
        """
        out: dict[str, dict] = {}
        for culture in cultures:
            if culture is None:
                continue
            key = str(culture)
            if key in out:
                continue
            info = self.resolve(key)
            out[key] = {
                "color": self._effective_color(info, key),
                "matched": info is not None,
                "matched_by": info.matched_by if info else None,
                "canonical": info.name if info else None,
                "short_name": info.short_name if info else None,
                "category": info.category if info else None,
                "sub_category": info.sub_category if info else None,
                "code_1c": info.code_1c if info else None,
            }
        return out


# Запасная палитра для культур вне справочника: детерминированная (тот же цвет
# при каждом расчёте), заметно отличается от «сельскохозяйственных» цветов 1С,
# чтобы пропуски в справочнике были видны глазом.
_FALLBACK_PALETTE = (
    "#94A3B8", "#64748B", "#A1A1AA", "#78716C", "#6B7280",
    "#9CA3AF", "#71717A", "#57534E",
)


def fallback_color(culture) -> str:
    if culture is None:
        return FALLBACK_COLOR
    h = 0
    for ch in str(culture):
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return _FALLBACK_PALETTE[h % len(_FALLBACK_PALETTE)]


@lru_cache(maxsize=1)
def get_taxonomy() -> CropTaxonomy:
    """Справочник культур (кэшируется на процесс)."""
    return CropTaxonomy()
