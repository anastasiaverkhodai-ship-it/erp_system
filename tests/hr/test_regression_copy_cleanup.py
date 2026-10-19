import pytest
from sqlalchemy.exc import DBAPIError
from scripts.run_postgresql_regression_copy import remove_isolated_database


class Connection:
    def __init__(self, error=None):
        self.statements=[]
        self.error=error

    async def execute(self, statement):
        self.statements.append(str(statement))
        if self.error is not None:
            error,self.error=self.error,None
            raise error


def database_error(code):
    original=Exception('PostgreSQL test error')
    original.sqlstate=code
    return DBAPIError(None,None,original)


@pytest.mark.asyncio
async def test_cleanup_uses_ordinary_drop_before_forcing_clients():
    conn=Connection()
    await remove_isolated_database(conn,'erp_r3_copy_0123456789abcdef')
    assert len(conn.statements)==1 and 'FORCE' not in conn.statements[0]
    conn=Connection(database_error('55006'))
    await remove_isolated_database(conn,'erp_r3_copy_0123456789abcdef')
    assert len(conn.statements)==2 and 'WITH (FORCE)' in conn.statements[1]


@pytest.mark.asyncio
async def test_cleanup_rejects_working_database_and_does_not_mask_privilege_errors():
    conn=Connection()
    with pytest.raises(RuntimeError,match='Unsafe'):
        await remove_isolated_database(conn,'erp_db')
    assert conn.statements==[]
    conn=Connection(database_error('42501'))
    with pytest.raises(DBAPIError):
        await remove_isolated_database(conn,'erp_r3_copy_0123456789abcdef')
    assert len(conn.statements)==1
