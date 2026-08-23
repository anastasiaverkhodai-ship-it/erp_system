from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.ukrainian_chart_source_definition import (
    UkrainianSyntheticAccountSource,
)


TEMPLATE_TYPE = (
    ChartOfAccountsTemplateType.GENERAL_291
)


GENERAL_291_SYNTHETIC_ACCOUNTS: tuple[
    UkrainianSyntheticAccountSource,
    ...,
] = (
    # =============================================
    # Class 1. Необоротні активи
    # =============================================
    UkrainianSyntheticAccountSource(
        code="10",
        name="Основні засоби",
    ),
    UkrainianSyntheticAccountSource(
        code="11",
        name="Інші необоротні матеріальні активи",
    ),
    UkrainianSyntheticAccountSource(
        code="12",
        name="Нематеріальні активи",
    ),
    UkrainianSyntheticAccountSource(
        code="13",
        name="Знос (амортизація) необоротних активів",
    ),
    UkrainianSyntheticAccountSource(
        code="14",
        name="Довгострокові фінансові інвестиції",
    ),
    UkrainianSyntheticAccountSource(
        code="15",
        name="Капітальні інвестиції",
    ),
    UkrainianSyntheticAccountSource(
        code="16",
        name="Довгострокові біологічні активи",
    ),
    UkrainianSyntheticAccountSource(
        code="17",
        name="Відстрочені податкові активи",
    ),
    UkrainianSyntheticAccountSource(
        code="18",
        name=(
            "Довгострокова дебіторська заборгованість "
            "та інші необоротні активи"
        ),
    ),
    UkrainianSyntheticAccountSource(
        code="19",
        name="Гудвіл",
    ),
    # =============================================
    # Class 2. Запаси
    # =============================================
    UkrainianSyntheticAccountSource(
        code="20",
        name="Виробничі запаси",
    ),
    UkrainianSyntheticAccountSource(
        code="21",
        name="Поточні біологічні активи",
    ),
    UkrainianSyntheticAccountSource(
        code="22",
        name="Малоцінні та швидкозношувані предмети",
    ),
    UkrainianSyntheticAccountSource(
        code="23",
        name="Виробництво",
    ),
    UkrainianSyntheticAccountSource(
        code="24",
        name="Брак у виробництві",
    ),
    UkrainianSyntheticAccountSource(
        code="25",
        name="Напівфабрикати",
    ),
    UkrainianSyntheticAccountSource(
        code="26",
        name="Готова продукція",
    ),
    UkrainianSyntheticAccountSource(
        code="27",
        name="Продукція сільськогосподарського виробництва",
    ),
    UkrainianSyntheticAccountSource(
        code="28",
        name="Товари",
    ),
    # =============================================
    # Class 3. Кошти, розрахунки та інші активи
    # =============================================
    UkrainianSyntheticAccountSource(
        code="30",
        name="Готівка",
    ),
    UkrainianSyntheticAccountSource(
        code="31",
        name="Рахунки в банках",
    ),
    UkrainianSyntheticAccountSource(
        code="33",
        name="Інші кошти",
    ),
    UkrainianSyntheticAccountSource(
        code="34",
        name="Короткострокові векселі одержані",
    ),
    UkrainianSyntheticAccountSource(
        code="35",
        name="Поточні фінансові інвестиції",
    ),
    UkrainianSyntheticAccountSource(
        code="36",
        name="Розрахунки з покупцями та замовниками",
    ),
    UkrainianSyntheticAccountSource(
        code="37",
        name="Розрахунки з різними дебіторами",
    ),
    UkrainianSyntheticAccountSource(
        code="38",
        name="Резерв сумнівних боргів",
    ),
    UkrainianSyntheticAccountSource(
        code="39",
        name="Витрати майбутніх періодів",
    ),
    # =============================================
    # Class 4. Власний капітал та забезпечення
    # зобов'язань
    # =============================================
    UkrainianSyntheticAccountSource(
        code="40",
        name="Зареєстрований (пайовий) капітал",
    ),
    UkrainianSyntheticAccountSource(
        code="41",
        name="Капітал у дооцінках",
    ),
    UkrainianSyntheticAccountSource(
        code="42",
        name="Додатковий капітал",
    ),
    UkrainianSyntheticAccountSource(
        code="43",
        name="Резервний капітал",
    ),
    UkrainianSyntheticAccountSource(
        code="44",
        name="Нерозподілені прибутки (непокриті збитки)",
    ),
    UkrainianSyntheticAccountSource(
        code="45",
        name="Вилучений капітал",
    ),
    UkrainianSyntheticAccountSource(
        code="46",
        name="Неоплачений капітал",
    ),
    UkrainianSyntheticAccountSource(
        code="47",
        name="Забезпечення майбутніх витрат і платежів",
    ),
    UkrainianSyntheticAccountSource(
        code="48",
        name="Цільове фінансування і цільові надходження",
    ),
    UkrainianSyntheticAccountSource(
        code="49",
        name="Страхові резерви",
    ),
    # =============================================
    # Class 5. Довгострокові зобов'язання
    # =============================================
    UkrainianSyntheticAccountSource(
        code="50",
        name="Довгострокові позики",
    ),
    UkrainianSyntheticAccountSource(
        code="51",
        name="Довгострокові векселі видані",
    ),
    UkrainianSyntheticAccountSource(
        code="52",
        name="Довгострокові зобов'язання за облігаціями",
    ),
    UkrainianSyntheticAccountSource(
        code="53",
        name="Довгострокові зобов'язання з оренди",
    ),
    UkrainianSyntheticAccountSource(
        code="54",
        name="Відстрочені податкові зобов'язання",
    ),
    UkrainianSyntheticAccountSource(
        code="55",
        name="Інші довгострокові зобов'язання",
    ),
    # =============================================
    # Class 6. Поточні зобов'язання
    # =============================================
    UkrainianSyntheticAccountSource(
        code="60",
        name="Короткострокові позики",
    ),
    UkrainianSyntheticAccountSource(
        code="61",
        name=(
            "Поточна заборгованість за "
            "довгостроковими зобов'язаннями"
        ),
    ),
    UkrainianSyntheticAccountSource(
        code="62",
        name="Короткострокові векселі видані",
    ),
    UkrainianSyntheticAccountSource(
        code="63",
        name="Розрахунки з постачальниками та підрядниками",
    ),
    UkrainianSyntheticAccountSource(
        code="64",
        name="Розрахунки за податками й платежами",
    ),
    UkrainianSyntheticAccountSource(
        code="65",
        name="Розрахунки за страхуванням",
    ),
    UkrainianSyntheticAccountSource(
        code="66",
        name="Розрахунки за виплатами працівникам",
    ),
    UkrainianSyntheticAccountSource(
        code="67",
        name="Розрахунки з учасниками та кошти клієнтів",
    ),
    UkrainianSyntheticAccountSource(
        code="68",
        name="Розрахунки за іншими операціями",
    ),
    UkrainianSyntheticAccountSource(
        code="69",
        name="Доходи майбутніх періодів",
    ),
    # =============================================
    # Class 7. Доходи і результати діяльності
    # =============================================
    UkrainianSyntheticAccountSource(
        code="70",
        name="Доходи від реалізації",
    ),
    UkrainianSyntheticAccountSource(
        code="71",
        name="Інший операційний дохід",
    ),
    UkrainianSyntheticAccountSource(
        code="72",
        name="Дохід від участі в капіталі",
    ),
    UkrainianSyntheticAccountSource(
        code="73",
        name="Інші фінансові доходи",
    ),
    UkrainianSyntheticAccountSource(
        code="74",
        name="Інші доходи",
    ),
    UkrainianSyntheticAccountSource(
        code="76",
        name="Страхові платежі",
    ),
    UkrainianSyntheticAccountSource(
        code="79",
        name="Фінансові результати",
    ),

    # =============================================
    # Class 8. Витрати за елементами
    # =============================================
    UkrainianSyntheticAccountSource(
        code="80",
        name="Матеріальні витрати",
    ),
    UkrainianSyntheticAccountSource(
        code="81",
        name="Витрати на оплату праці",
    ),
    UkrainianSyntheticAccountSource(
        code="82",
        name="Відрахування на соціальні заходи",
    ),
    UkrainianSyntheticAccountSource(
        code="83",
        name="Амортизація",
    ),
    UkrainianSyntheticAccountSource(
        code="84",
        name="Інші операційні витрати",
    ),


    # =============================================
    # Class 9. Витрати діяльності
    # =============================================
    UkrainianSyntheticAccountSource(
        code="90",
        name="Собівартість реалізації",
    ),
    UkrainianSyntheticAccountSource(
        code="91",
        name="Загальновиробничі витрати",
    ),
    UkrainianSyntheticAccountSource(
        code="92",
        name="Адміністративні витрати",
    ),
    UkrainianSyntheticAccountSource(
        code="93",
        name="Витрати на збут",
    ),
    UkrainianSyntheticAccountSource(
        code="94",
        name="Інші витрати операційної діяльності",
    ),
    UkrainianSyntheticAccountSource(
        code="95",
        name="Фінансові витрати",
    ),
    UkrainianSyntheticAccountSource(
        code="96",
        name="Втрати від участі в капіталі",
    ),
    UkrainianSyntheticAccountSource(
        code="97",
        name="Інші витрати",
    ),
    UkrainianSyntheticAccountSource(
        code="98",
        name="Податок на прибуток",
    ),


    # =============================================
    # Class 0. Позабалансові рахунки
    # =============================================
    UkrainianSyntheticAccountSource(
        code="01",
        name="Орендовані необоротні активи",
    ),
    UkrainianSyntheticAccountSource(
        code="02",
        name="Активи на відповідальному зберіганні",
    ),
    UkrainianSyntheticAccountSource(
        code="03",
        name="Контрактні зобов'язання",
    ),
    UkrainianSyntheticAccountSource(
        code="04",
        name="Непередбачені активи й зобов'язання",
    ),
    UkrainianSyntheticAccountSource(
        code="05",
        name="Гарантії та забезпечення надані",
    ),
    UkrainianSyntheticAccountSource(
        code="06",
        name="Гарантії та забезпечення отримані",
    ),
    UkrainianSyntheticAccountSource(
        code="07",
        name="Списані активи",
    ),
    UkrainianSyntheticAccountSource(
        code="08",
        name="Бланки суворого обліку",
    ),
    UkrainianSyntheticAccountSource(
        code="09",
        name="Амортизаційні відрахування",
    ),

)


def validate_general_291_synthetic_accounts() -> None:
    codes = tuple(
        account.code
        for account
        in GENERAL_291_SYNTHETIC_ACCOUNTS
    )

    if len(codes) != len(set(codes)):
        raise RuntimeError(
            "General 291 synthetic account "
            "codes must be unique"
        )


validate_general_291_synthetic_accounts()
