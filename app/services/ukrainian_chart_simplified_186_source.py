from app.services.chart_of_accounts_template_types import (
    ChartOfAccountsTemplateType,
)
from app.services.ukrainian_chart_source_definition import (
    UkrainianSyntheticAccountSource,
)


TEMPLATE_TYPE = (
    ChartOfAccountsTemplateType.SIMPLIFIED_186
)


SIMPLIFIED_186_SYNTHETIC_ACCOUNTS: tuple[
    UkrainianSyntheticAccountSource,
    ...,
] = (
    # =============================================
    # Необоротні активи
    # =============================================
    UkrainianSyntheticAccountSource(
        code="10",
        name="Основні засоби",
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
        code="18",
        name="Інші необоротні активи",
    ),

    # =============================================
    # Запаси та виробництво
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
        code="23",
        name="Виробництво",
    ),
    UkrainianSyntheticAccountSource(
        code="26",
        name="Готова продукція",
    ),

    # =============================================
    # Кошти, розрахунки та інші активи
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
        code="35",
        name="Поточні фінансові інвестиції",
    ),
    UkrainianSyntheticAccountSource(
        code="37",
        name="Розрахунки з різними дебіторами",
    ),
    UkrainianSyntheticAccountSource(
        code="39",
        name="Витрати майбутніх періодів",
    ),

    # =============================================
    # Власний капітал і забезпечення
    # =============================================
    UkrainianSyntheticAccountSource(
        code="40",
        name="Власний капітал",
    ),
    UkrainianSyntheticAccountSource(
        code="44",
        name="Нерозподілені прибутки (непокриті збитки)",
    ),
    UkrainianSyntheticAccountSource(
        code="47",
        name="Забезпечення майбутніх витрат і платежів",
    ),
    UkrainianSyntheticAccountSource(
        code="48",
        name="Цільове фінансування і цільові надходження",
    ),

    # =============================================
    # Довгострокові зобов'язання
    # =============================================
    UkrainianSyntheticAccountSource(
        code="55",
        name="Інші довгострокові зобов'язання",
    ),

    # =============================================
    # Поточні зобов'язання
    # =============================================
    UkrainianSyntheticAccountSource(
        code="64",
        name="Розрахунки за податками й платежами",
    ),
    UkrainianSyntheticAccountSource(
        code="66",
        name="Розрахунки з оплати праці",
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
    # Доходи та фінансові результати
    # =============================================
    UkrainianSyntheticAccountSource(
        code="70",
        name="Доходи від реалізації",
    ),
    UkrainianSyntheticAccountSource(
        code="74",
        name="Інші доходи",
    ),
    UkrainianSyntheticAccountSource(
        code="79",
        name="Фінансові результати",
    ),

    # =============================================
    # Витрати
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
        code="97",
        name="Інші витрати",
    ),
)


def validate_simplified_186_synthetic_accounts() -> None:
    codes = tuple(
        account.code
        for account
        in SIMPLIFIED_186_SYNTHETIC_ACCOUNTS
    )

    if len(codes) != len(set(codes)):
        raise RuntimeError(
            "Simplified 186 synthetic account "
            "codes must be unique"
        )


validate_simplified_186_synthetic_accounts()
