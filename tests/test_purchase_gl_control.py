from decimal import Decimal as D
from unittest.mock import AsyncMock
import pytest
from app.services.accounting_account_roles import AccountingAccountRole as R
from app.services.purchase_gl_control_service import dynamic_plan
from app.models.purchase_value_correction_fifo_impact_event import PurchaseValueCorrectionFifoImpactEvent as Fifo
from app.models.purchase_value_correction_moving_average_replay_event import PurchaseValueCorrectionMovingAverageReplayEvent as Average
from app.models.purchase_value_correction_vat_adjustment_event import PurchaseValueCorrectionVatAdjustmentEvent as Vat

IDS={R.INVENTORY_GOODS:1,R.SUPPLIER_PAYABLES:2,R.VAT_INPUT:3}


@pytest.mark.asyncio
@pytest.mark.parametrize('sign',[1,-1])
async def test_value_and_vat_corrections_preserve_direction_and_precision(sign):
    for event,asset,amount in (
        (Fifo(original_base_amount=D(10),corrected_base_amount=D(10+sign),destination_kind='on_hand'),1,D(1)),
        (Average(original_valuation_amount=D(10),corrected_valuation_amount=D(10)+sign*D('1.005'),effect_kind='on_hand'),1,D('1.01')),
        (Vat(adjusted_tax_amount=D(1),adjustment_kind='increase' if sign>0 else 'decrease'),3,D(1)),
    ):
        plan=await dynamic_plan(AsyncMock(),event,IDS)
        assert plan[asset]==([amount,D(0)] if sign>0 else [D(0),amount])
        assert plan[2]==([D(0),amount] if sign>0 else [amount,D(0)])
