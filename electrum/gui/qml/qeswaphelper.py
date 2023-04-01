import asyncio
import threading
import math
from typing import Union

from PyQt5.QtCore import pyqtProperty, pyqtSignal, pyqtSlot, QObject, QTimer

from electrum.i18n import _
from electrum.lnutil import ln_dummy_address
from electrum.logging import get_logger
from electrum.transaction import PartialTxOutput
from electrum.util import NotEnoughFunds, NoDynamicFeeEstimates, profiler

from .auth import AuthMixin, auth_protect
from .qetypes import QEAmount
from .qewallet import QEWallet

class QESwapHelper(AuthMixin, QObject):
    _logger = get_logger(__name__)

    confirm = pyqtSignal([str], arguments=['message'])
    error = pyqtSignal([str], arguments=['message'])
    swapStarted = pyqtSignal()
    swapSuccess = pyqtSignal()
    swapFailed = pyqtSignal([str], arguments=['message'])

    def __init__(self, parent=None):
        super().__init__(parent)

        self._wallet = None
        self._sliderPos = 0
        self._rangeMin = 0
        self._rangeMax = 0
        self._tx = None
        self._valid = False
        self._userinfo = ' '.join([
            _('Move the slider to set the amount and direction of the swap.'),
            _('Swapping lightning funds for onchain funds will increase your capacity to receive lightning payments.'),
        ])
        self._tosend = QEAmount()
        self._toreceive = QEAmount()
        self._serverfeeperc = ''
        self._server_miningfee = QEAmount()
        self._miningfee = QEAmount()
        self._isReverse = False

        self._service_available = False
        self._send_amount = 0
        self._receive_amount = 0

        self._leftVoid = 0
        self._rightVoid = 0

        self._fwd_swap_updatetx_timer = QTimer(self)
        self._fwd_swap_updatetx_timer.setSingleShot(True)
        # self._fwd_swap_updatetx_timer.setInterval(500)
        self._fwd_swap_updatetx_timer.timeout.connect(self.fwd_swap_updatetx)


    walletChanged = pyqtSignal()
    @pyqtProperty(QEWallet, notify=walletChanged)
    def wallet(self):
        return self._wallet

    @wallet.setter
    def wallet(self, wallet: QEWallet):
        if self._wallet != wallet:
            self._wallet = wallet
            self.init_swap_slider_range()
            self.walletChanged.emit()

    sliderPosChanged = pyqtSignal()
    @pyqtProperty(float, notify=sliderPosChanged)
    def sliderPos(self):
        return self._sliderPos

    @sliderPos.setter
    def sliderPos(self, sliderPos):
        if self._sliderPos != sliderPos:
            self._sliderPos = sliderPos
            self.swap_slider_moved()
            self.sliderPosChanged.emit()

    rangeMinChanged = pyqtSignal()
    @pyqtProperty(float, notify=rangeMinChanged)
    def rangeMin(self):
        return self._rangeMin

    @rangeMin.setter
    def rangeMin(self, rangeMin):
        if self._rangeMin != rangeMin:
            self._rangeMin = rangeMin
            self.rangeMinChanged.emit()

    rangeMaxChanged = pyqtSignal()
    @pyqtProperty(float, notify=rangeMaxChanged)
    def rangeMax(self):
        return self._rangeMax

    @rangeMax.setter
    def rangeMax(self, rangeMax):
        if self._rangeMax != rangeMax:
            self._rangeMax = rangeMax
            self.rangeMaxChanged.emit()

    leftVoidChanged = pyqtSignal()
    @pyqtProperty(float, notify=leftVoidChanged)
    def leftVoid(self):
        return self._leftVoid

    rightVoidChanged = pyqtSignal()
    @pyqtProperty(float, notify=rightVoidChanged)
    def rightVoid(self):
        return self._rightVoid

    validChanged = pyqtSignal()
    @pyqtProperty(bool, notify=validChanged)
    def valid(self):
        return self._valid

    @valid.setter
    def valid(self, valid):
        if self._valid != valid:
            self._valid = valid
            self.validChanged.emit()

    userinfoChanged = pyqtSignal()
    @pyqtProperty(str, notify=userinfoChanged)
    def userinfo(self):
        return self._userinfo

    @userinfo.setter
    def userinfo(self, userinfo):
        if self._userinfo != userinfo:
            self._userinfo = userinfo
            self.userinfoChanged.emit()

    tosendChanged = pyqtSignal()
    @pyqtProperty(QEAmount, notify=tosendChanged)
    def tosend(self):
        return self._tosend

    @tosend.setter
    def tosend(self, tosend):
        if self._tosend != tosend:
            self._tosend = tosend
            self.tosendChanged.emit()

    toreceiveChanged = pyqtSignal()
    @pyqtProperty(QEAmount, notify=toreceiveChanged)
    def toreceive(self):
        return self._toreceive

    @toreceive.setter
    def toreceive(self, toreceive):
        if self._toreceive != toreceive:
            self._toreceive = toreceive
            self.toreceiveChanged.emit()

    server_miningfeeChanged = pyqtSignal()
    @pyqtProperty(QEAmount, notify=server_miningfeeChanged)
    def server_miningfee(self):
        return self._server_miningfee

    @server_miningfee.setter
    def server_miningfee(self, server_miningfee):
        if self._server_miningfee != server_miningfee:
            self._server_miningfee = server_miningfee
            self.server_miningfeeChanged.emit()

    serverfeepercChanged = pyqtSignal()
    @pyqtProperty(str, notify=serverfeepercChanged)
    def serverfeeperc(self):
        return self._serverfeeperc

    @serverfeeperc.setter
    def serverfeeperc(self, serverfeeperc):
        if self._serverfeeperc != serverfeeperc:
            self._serverfeeperc = serverfeeperc
            self.serverfeepercChanged.emit()

    miningfeeChanged = pyqtSignal()
    @pyqtProperty(QEAmount, notify=miningfeeChanged)
    def miningfee(self):
        return self._miningfee

    @miningfee.setter
    def miningfee(self, miningfee):
        if self._miningfee != miningfee:
            self._miningfee = miningfee
            self.miningfeeChanged.emit()

    isReverseChanged = pyqtSignal()
    @pyqtProperty(bool, notify=isReverseChanged)
    def isReverse(self):
        return self._isReverse

    @isReverse.setter
    def isReverse(self, isReverse):
        if self._isReverse != isReverse:
            self._isReverse = isReverse
            self.isReverseChanged.emit()


    def init_swap_slider_range(self):
        lnworker = self._wallet.wallet.lnworker
        if not lnworker:
            return
        swap_manager = lnworker.swap_manager
        try:
            asyncio.run(swap_manager.get_pairs())
            self._service_available = True
        except Exception as e:
            self.error.emit(_('Swap service unavailable'))
            self._logger.error(f'could not get pairs for swap: {repr(e)}')
            return

        """Sets the minimal and maximal amount that can be swapped for the swap
        slider."""
        # tx is updated again afterwards with send_amount in case of normal swap
        # this is just to estimate the maximal spendable onchain amount for HTLC
        self.update_tx('!')
        try:
            max_onchain_spend = self._tx.output_value_for_address(ln_dummy_address())
        except AttributeError:  # happens if there are no utxos
            max_onchain_spend = 0
        reverse = int(min(lnworker.num_sats_can_send(),
                          swap_manager.get_max_amount()))
        max_recv_amt_ln = int(lnworker.num_sats_can_receive())
        max_recv_amt_oc = swap_manager.get_send_amount(max_recv_amt_ln, is_reverse=False) or 0
        forward = int(min(max_recv_amt_oc,
                          # maximally supported swap amount by provider
                          swap_manager.get_max_amount(),
                          max_onchain_spend))
        # we expect range to adjust the value of the swap slider to be in the
        # correct range, i.e., to correct an overflow when reducing the limits
        self._logger.debug(f'Slider range {-reverse} - {forward}')
        self.rangeMin = -reverse
        self.rangeMax = forward
        # percentage of void, right or left
        if reverse < forward:
            self._leftVoid = 0.5 * (forward - reverse) / forward
            self._rightVoid = 0
        elif reverse > forward:
            self._leftVoid = 0
            self._rightVoid = - 0.5 * (forward - reverse) / reverse
        else:
            self._leftVoid = 0
            self._rightVoid = 0
        self.leftVoidChanged.emit()
        self.rightVoidChanged.emit()

        self.swap_slider_moved()

    @profiler
    def update_tx(self, onchain_amount: Union[int, str]):
        """Updates the transaction associated with a forward swap."""
        if onchain_amount is None:
            self._tx = None
            self.valid = False
            return
        outputs = [PartialTxOutput.from_address_and_value(ln_dummy_address(), onchain_amount)]
        coins = self._wallet.wallet.get_spendable_coins(None)
        try:
            self._tx = self._wallet.wallet.make_unsigned_transaction(
                coins=coins,
                outputs=outputs)
        except (NotEnoughFunds, NoDynamicFeeEstimates):
            self._tx = None
            self.valid = False

    def swap_slider_moved(self):
        if not self._service_available:
            return

        position = int(self._sliderPos)

        swap_manager = self._wallet.wallet.lnworker.swap_manager

        # pay_amount and receive_amounts are always with fees already included
        # so they reflect the net balance change after the swap
        if position < 0:  # reverse swap
            self.isReverse = True

            self._send_amount = abs(position)
            self.tosend = QEAmount(amount_sat=self._send_amount)

            self._receive_amount = swap_manager.get_recv_amount(
                send_amount=self._send_amount, is_reverse=True)
            self.toreceive = QEAmount(amount_sat=self._receive_amount)

            # fee breakdown
            self.serverfeeperc = f'{swap_manager.percentage:0.1f}%'
            server_miningfee = swap_manager.lockup_fee
            self.server_miningfee = QEAmount(amount_sat=server_miningfee)
            self.miningfee = QEAmount(amount_sat=swap_manager.get_claim_fee())

            self.check_valid(self._send_amount, self._receive_amount)
        else:  # forward (normal) swap
            self.isReverse = False
            self._send_amount = position
            self.tosend = QEAmount(amount_sat=self._send_amount)

            self._receive_amount = swap_manager.get_recv_amount(send_amount=position, is_reverse=False)
            self.toreceive = QEAmount(amount_sat=self._receive_amount)

            # fee breakdown
            self.serverfeeperc = f'{swap_manager.percentage:0.1f}%'
            server_miningfee = swap_manager.normal_fee
            self.server_miningfee = QEAmount(amount_sat=server_miningfee)

            # the slow stuff we delegate to a delay timer which triggers after slider
            # doesn't update for a while
            self.valid = False # wait for timer
            self._fwd_swap_updatetx_timer.start(250)

    def check_valid(self, send_amount, receive_amount):
        if send_amount and receive_amount:
            self.valid = True
        else:
            # add more nuanced error reporting?
            self.valid = False

    def fwd_swap_updatetx(self):
        self.update_tx(self._send_amount)
        # add lockup fees, but the swap amount is position
        pay_amount = self._send_amount + self._tx.get_fee() if self._tx else 0
        self.miningfee = QEAmount(amount_sat=self._tx.get_fee()) if self._tx else QEAmount()
        self.check_valid(pay_amount, self._receive_amount)

    def do_normal_swap(self, lightning_amount, onchain_amount):
        assert self._tx
        if lightning_amount is None or onchain_amount is None:
            return
        loop = self._wallet.wallet.network.asyncio_loop
        coro = self._wallet.wallet.lnworker.swap_manager.normal_swap(
            lightning_amount_sat=lightning_amount,
            expected_onchain_amount_sat=onchain_amount,
            password=self._wallet.password,
            tx=self._tx,
        )

        def swap_task():
            try:
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
                self.swapStarted.emit()
                txid = fut.result()
                self.swapSuccess.emit()
            except Exception as e:
                self._logger.error(str(e))
                self.swapFailed.emit(str(e))

        threading.Thread(target=swap_task).start()

    def do_reverse_swap(self, lightning_amount, onchain_amount):
        if lightning_amount is None or onchain_amount is None:
            return
        swap_manager = self._wallet.wallet.lnworker.swap_manager
        loop = self._wallet.wallet.network.asyncio_loop
        coro = swap_manager.reverse_swap(
            lightning_amount_sat=lightning_amount,
            expected_onchain_amount_sat=onchain_amount + swap_manager.get_claim_fee(),
        )

        def swap_task():
            try:
                fut = asyncio.run_coroutine_threadsafe(coro, loop)
                self.swapStarted.emit()
                success = fut.result()
                if success:
                    self.swapSuccess.emit()
                else:
                    self.swapFailed.emit('')
            except Exception as e:
                self._logger.error(str(e))
                self.swapFailed.emit(str(e))

        threading.Thread(target=swap_task).start()

    @pyqtSlot()
    @pyqtSlot(bool)
    def executeSwap(self, confirm=False):
        if not self._wallet.wallet.network:
            self.error.emit(_("You are offline."))
            return
        if confirm:
            self._do_execute_swap()
            return

        if self.isReverse:
            self.confirm.emit(_('Do you want to do a reverse submarine swap?'))
        else:
            self.confirm.emit(_('Do you want to do a submarine swap? '
                'You will need to wait for the swap transaction to confirm.'
            ))

    @auth_protect
    def _do_execute_swap(self):
        if self.isReverse:
            lightning_amount = self._send_amount
            onchain_amount = self._receive_amount
            self.do_reverse_swap(lightning_amount, onchain_amount)
        else:
            lightning_amount = self._receive_amount
            onchain_amount = self._send_amount
            self.do_normal_swap(lightning_amount, onchain_amount)
