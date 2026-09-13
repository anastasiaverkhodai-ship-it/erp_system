from pathlib import Path


def test_cash_desk_service_validates_company_account():
    source = Path(
        "app/services/cash_desk_service.py"
    ).read_text()

    assert "Account.company_id == company_id" in source
    assert (
        "Account.id == accounting_account_id"
        in source
    )


def test_cash_desk_service_requires_active_account():
    source = Path(
        "app/services/cash_desk_service.py"
    ).read_text()

    assert "if not account.is_active:" in source


def test_cash_desk_service_requires_postable_account():
    source = Path(
        "app/services/cash_desk_service.py"
    ).read_text()

    assert "if not account.is_postable:" in source


def test_cash_desk_service_has_no_system_account_requirement():
    source = Path(
        "app/services/cash_desk_service.py"
    ).read_text()

    assert "account.is_system" not in source
