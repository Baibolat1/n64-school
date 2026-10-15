"""Класс ученика: какие классы есть в школе и как ученик выбирает свой.

Список классов -- не справочник в базе, а расписание: класс существует, пока
по нему стоит сетка уроков. Свободного ввода нет нарочно -- «9б» вместо «9Б»
это расписание из нуля строк без единого слова о том, почему оно пустое, а
ключом тут работает строка целиком.

Кулдауна на смену нет, в отличие от псевдонима: класс -- не имя, за которое
можно себя выдать, ошибиться в нём можно сколько угодно, а видеть чужой класс
-- это чужая сетка уроков и список псевдонимов, то есть не тайна (см.
``docs/PLAN.md``, «Мой класс»).

Транзакцией владеет сервис, как и у соседних сервисов ученика: :func:`set_class`
коммитит.
"""

from dataclasses import dataclass
from enum import Enum

from sqlalchemy.orm import Session

from app.models.student import Student
from app.repositories import schedule as schedule_repo
from app.repositories import students as students_repo


# «ё» стоит в Unicode далеко за «я» (U+0451), а в русском алфавите -- сразу за
# «е»: без этой замены «Ёж» встал бы в самый конец росписи класса. Регистр
# снимает ``casefold``, так что остаётся только строчная «ё».
_ALPHABET = str.maketrans({"ё": "е"})


class ClassError(str, Enum):
    """Машинная причина, по которой класс не принят."""

    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ClassChangeResult:
    """Итог попытки поставить класс.

    ``class_name`` -- нормализованное значение из формы: его показывает поле,
    чтобы ученик видел, что именно не приняли.
    """

    ok: bool
    class_name: str | None = None
    reason: ClassError | None = None


def _school_order(name: str) -> tuple[int, str]:
    """Ключ школьного порядка: 8А, 8Б, 9А, 10А, 10Б.

    Обычная сортировка строк поставила бы десятый класс перед восьмым: у
    «10А» первая цифра меньше. Поэтому сначала номер класса, потом буква.
    """
    digits = ""
    rest = name
    while rest[:1].isdigit():
        digits += rest[0]
        rest = rest[1:]
    return (int(digits) if digits else 0, rest.casefold())


def available_classes(db: Session, *, current: str | None = None) -> list[str]:
    """Что можно выбрать: классы из сетки школы плюс уже стоящий класс.

    Свой класс добавляется к списку, даже если сетки по нему сейчас нет: иначе
    поле показывало бы «не указан» там, где класс есть, и ученик терял бы его,
    сохранив соседнюю форму.
    """
    names = list(schedule_repo.list_class_names(db))
    if current and current not in names:
        names.append(current)
    return sorted(names, key=_school_order)


def set_class(db: Session, student: Student, raw_class_name: str) -> ClassChangeResult:
    """Поставить ученику класс из списка школы; пустое значение снимает его.

    В базу идёт строка из списка, а не то, что пришло из формы: класс -- ключ,
    по которому ищутся расписание, домашнее задание и одноклассники, и
    отличаться от ключа на пробел ему нельзя.
    """
    submitted = " ".join(raw_class_name.split())

    if not submitted:
        # Пустой выбор -- не ошибка, а честное «класса нет»: ученик может уйти
        # из школы или ошибиться классом и захотеть вернуться к пустому полю.
        student.class_name = None
        db.commit()
        return ClassChangeResult(ok=True, class_name=None)

    if submitted not in available_classes(db, current=student.class_name):
        # Значение пришло не из нашего списка: поле выбирают, а не набирают,
        # так что это либо правка запроса руками, либо список успел измениться.
        return ClassChangeResult(
            ok=False, class_name=submitted, reason=ClassError.UNKNOWN
        )

    student.class_name = submitted
    db.commit()
    return ClassChangeResult(ok=True, class_name=submitted)


def refusal_message(result: ClassChangeResult) -> str:
    """Одна фраза на оба входа -- страницу настроек и её JSON-двойник."""
    if result.reason is ClassError.UNKNOWN:
        return "Такого класса в школе нет — выберите из списка"
    return "Не удалось сохранить класс"


def classmates(db: Session, class_name: str) -> list:
    """Роспись класса по алфавиту псевдонимов, вместе с самим учеником.

    Свой класс -- это и он сам тоже: список, из которого себя вычеркнули,
    читается как «вот они, а где я».

    Сортировка здесь, а не в SQL: SQLite складывает кириллицу по коду символа,
    и заглавные буквы у него идут отдельной страницей перед строчными («Ёж»
    раньше «Абрикос», потому что Ё -- U+0401, а А -- U+0410). ``casefold``
    сравнивает по буквам, поэтому регистр на место в списке не влияет, а
    :data:`_ALPHABET` возвращает на место «ё».
    """
    roster = students_repo.list_classmates(db, class_name)
    return sorted(
        roster, key=lambda row: row.alias.casefold().translate(_ALPHABET)
    )
