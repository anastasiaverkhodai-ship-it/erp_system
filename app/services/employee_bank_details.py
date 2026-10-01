"""Ukrainian IBAN syntax/checksum only; this does not prove account ownership.
NBU: https://bank.gov.ua/ua/iban
"""
import re


def normalize_employee_iban(value: str | None) -> str | None:
    if value is None:
        return None
    value = ''.join(value.split()).upper()
    if not value:
        return None
    if not re.fullmatch(r'UA[0-9]{27}', value):
        raise ValueError('Employee IBAN must be UA followed by 27 digits')
    if not 2 <= int(value[2:4]) <= 98 or int(value[4:] + '3010' + value[2:4]) % 97 != 1:
        raise ValueError('Employee IBAN checksum is invalid')
    return value
