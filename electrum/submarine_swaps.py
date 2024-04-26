import asyncio
import json
import os
from typing import TYPE_CHECKING, Optional, Dict, Union
from decimal import Decimal
import math

import attr
import aiohttp

from .crypto import sha256, hash_160
from .ecc import ECPrivkey
from .bitcoin import (script_to_p2wsh, opcodes, p2wsh_nested_script, push_script,
                      is_segwit_address, construct_witness)
from .transaction import PartialTxInput, PartialTxOutput, PartialTransaction, Transaction, TxInput, TxOutpoint
from .transaction import script_GetOp, match_script_against_template, OPPushDataGeneric, OPPushDataPubkey
from .util import log_exceptions, BelowDustLimit
from .lnutil import REDEEM_AFTER_DOUBLE_SPENT_DELAY, ln_dummy_address
from .bitcoin import dust_threshold
from .logging import Logger
from .lnutil import hex_to_bytes
from .json_db import StoredObject
from . import constants
from .address_synchronizer import TX_HEIGHT_LOCAL
from .i18n import _

if TYPE_CHECKING:
    from .network import Network
    from .wallet import Abstract_Wallet
    from .lnwatcher import LNWalletWatcher
    from .lnworker import LNWallet
    from .simple_config import SimpleConfig


API_URL_MAINNET = 'https://swaps.electrum.org/api'
API_URL_TESTNET = 'https://swaps.electrum.org/testnet'
API_URL_REGTEST = 'https://localhost/api'



WITNESS_TEMPLATE_SWAP = [
    opcodes.OP_HASH160,
    OPPushDataGeneric(lambda x: x == 20),
    opcodes.OP_EQUAL,
    opcodes.OP_IF,
    OPPushDataPubkey,
    opcodes.OP_ELSE,
    OPPushDataGeneric(None),
    opcodes.OP_CHECKLOCKTIMEVERIFY,
    opcodes.OP_DROP,
    OPPushDataPubkey,
    opcodes.OP_ENDIF,
    opcodes.OP_CHECKSIG
]


# The script of the reverse swaps has one extra check in it to verify
# that the length of the preimage is 32. This is required because in
# the reverse swaps the preimage is generated by the user and to
# settle the hold invoice, you need a preimage with 32 bytes . If that
# check wasn't there the user could generate a preimage with a
# different length which would still allow for claiming the onchain
# coins but the invoice couldn't be settled

WITNESS_TEMPLATE_REVERSE_SWAP = [
    opcodes.OP_SIZE,
    OPPushDataGeneric(None),
    opcodes.OP_EQUAL,
    opcodes.OP_IF,
    opcodes.OP_HASH160,
    OPPushDataGeneric(lambda x: x == 20),
    opcodes.OP_EQUALVERIFY,
    OPPushDataPubkey,
    opcodes.OP_ELSE,
    opcodes.OP_DROP,
    OPPushDataGeneric(None),
    opcodes.OP_CHECKLOCKTIMEVERIFY,
    opcodes.OP_DROP,
    OPPushDataPubkey,
    opcodes.OP_ENDIF,
    opcodes.OP_CHECKSIG
]


class SwapServerError(Exception):
    def __str__(self):
        return _("The swap server errored or is unreachable.")


@attr.s
class SwapData(StoredObject):
    is_reverse = attr.ib(type=bool)
    locktime = attr.ib(type=int)
    onchain_amount = attr.ib(type=int)  # in sats
    lightning_amount = attr.ib(type=int)  # in sats
    redeem_script = attr.ib(type=bytes, converter=hex_to_bytes)
    preimage = attr.ib(type=bytes, converter=hex_to_bytes)
    prepay_hash = attr.ib(type=Optional[bytes], converter=hex_to_bytes)
    privkey = attr.ib(type=bytes, converter=hex_to_bytes)
    lockup_address = attr.ib(type=str)
    receive_address = attr.ib(type=str)
    funding_txid = attr.ib(type=Optional[str])
    spending_txid = attr.ib(type=Optional[str])
    is_redeemed = attr.ib(type=bool)

    _funding_prevout = None  # type: Optional[TxOutpoint]  # for RBF
    __payment_hash = None

    @property
    def payment_hash(self) -> bytes:
        if self.__payment_hash is None:
            self.__payment_hash = sha256(self.preimage)
        return self.__payment_hash


def create_claim_tx(
        *,
        txin: PartialTxInput,
        witness_script: bytes,
        address: str,
        amount_sat: int,
        locktime: int,
) -> PartialTransaction:
    """Create tx to either claim successful reverse-swap,
    or to get refunded for timed-out forward-swap.
    """
    txin.script_sig = b''
    txin.witness_script = witness_script
    txout = PartialTxOutput.from_address_and_value(address, amount_sat)
    tx = PartialTransaction.from_io([txin], [txout], version=2, locktime=locktime)
    tx.set_rbf(True)
    return tx


class SwapManager(Logger):

    network: Optional['Network'] = None
    lnwatcher: Optional['LNWalletWatcher'] = None

    def __init__(self, *, wallet: 'Abstract_Wallet', lnworker: 'LNWallet'):
        Logger.__init__(self)
        self.normal_fee = 0
        self.lockup_fee = 0
        self.percentage = 0
        self._min_amount = None
        self._max_amount = None
        self.wallet = wallet
        self.lnworker = lnworker

        self.swaps = self.wallet.db.get_dict('submarine_swaps')  # type: Dict[str, SwapData]
        self._swaps_by_funding_outpoint = {}  # type: Dict[TxOutpoint, SwapData]
        self._swaps_by_lockup_address = {}  # type: Dict[str, SwapData]
        for payment_hash, swap in self.swaps.items():
            self._add_or_reindex_swap(swap)

        self.prepayments = {}  # type: Dict[bytes, bytes] # fee_rhash -> rhash
        for k, swap in self.swaps.items():
            if swap.is_reverse and swap.prepay_hash is not None:
                self.prepayments[swap.prepay_hash] = bytes.fromhex(k)
        # api url
        if constants.net == constants.BitcoinMainnet:
            self.api_url = API_URL_MAINNET
        elif constants.net == constants.BitcoinTestnet:
            self.api_url = API_URL_TESTNET
        else:
            self.api_url = API_URL_REGTEST
        # init default min & max
        self.init_min_max_values()

    def start_network(self, *, network: 'Network', lnwatcher: 'LNWalletWatcher'):
        assert network
        assert lnwatcher
        assert self.network is None, "already started"
        self.network = network
        self.lnwatcher = lnwatcher
        for k, swap in self.swaps.items():
            if swap.is_redeemed:
                continue
            self.add_lnwatcher_callback(swap)

    @log_exceptions
    async def _claim_swap(self, swap: SwapData) -> None:
        assert self.network
        assert self.lnwatcher
        if not self.lnwatcher.adb.is_up_to_date():
            return
        current_height = self.network.get_local_height()
        delta = current_height - swap.locktime
        txos = self.lnwatcher.adb.get_addr_outputs(swap.lockup_address)
        for txin in txos.values():
            if swap.is_reverse and txin.value_sats() < swap.onchain_amount:
                self.logger.info('amount too low, we should not reveal the preimage')
                continue
            swap.funding_txid = txin.prevout.txid.hex()
            swap._funding_prevout = txin.prevout
            self._add_or_reindex_swap(swap)  # to update _swaps_by_funding_outpoint
            spent_height = txin.spent_height
            if spent_height is not None:
                swap.spending_txid = txin.spent_txid
                if spent_height > 0:
                    if current_height - spent_height > REDEEM_AFTER_DOUBLE_SPENT_DELAY:
                        self.logger.info(f'stop watching swap {swap.lockup_address}')
                        self.lnwatcher.remove_callback(swap.lockup_address)
                        swap.is_redeemed = True
                elif spent_height == TX_HEIGHT_LOCAL:
                    if txin.block_height > 0 or self.wallet.config.get('allow_instant_swaps', False):
                        tx = self.lnwatcher.adb.get_transaction(txin.spent_txid)
                        self.logger.info(f'broadcasting tx {txin.spent_txid}')
                        await self.network.broadcast_transaction(tx)
                # already in mempool
                continue
            if not swap.is_reverse and delta < 0:
                # too early for refund
                return
            try:
                tx = self._create_and_sign_claim_tx(txin=txin, swap=swap, config=self.wallet.config)
            except BelowDustLimit:
                self.logger.info('utxo value below dust threshold')
                continue
            self.logger.info(f'adding claim tx {tx.txid()}')
            self.wallet.adb.add_transaction(tx)
            swap.spending_txid = tx.txid()

    def get_claim_fee(self):
        return self._get_claim_fee(config=self.wallet.config)

    @classmethod
    def _get_claim_fee(cls, *, config: 'SimpleConfig'):
        return config.estimate_fee(136, allow_fallback_to_static_rates=True)

    def get_swap(self, payment_hash: bytes) -> Optional[SwapData]:
        # for history
        swap = self.swaps.get(payment_hash.hex())
        if swap:
            return swap
        payment_hash = self.prepayments.get(payment_hash)
        if payment_hash:
            return self.swaps.get(payment_hash.hex())

    def add_lnwatcher_callback(self, swap: SwapData) -> None:
        callback = lambda: self._claim_swap(swap)
        self.lnwatcher.add_callback(swap.lockup_address, callback)

    async def normal_swap(
            self,
            *,
            lightning_amount_sat: int,
            expected_onchain_amount_sat: int,
            password,
            tx: PartialTransaction = None,
            channels = None,
    ) -> str:
        """send on-chain BTC, receive on Lightning

        - User generates an LN invoice with RHASH, and knows preimage.
        - User creates on-chain output locked to RHASH.
        - Server pays LN invoice. User reveals preimage.
        - Server spends the on-chain output using preimage.
        """
        assert self.network
        assert self.lnwatcher
        privkey = os.urandom(32)
        pubkey = ECPrivkey(privkey).get_public_key_bytes(compressed=True)
        amount_msat = lightning_amount_sat * 1000
        payment_hash = self.lnworker.create_payment_info(amount_msat=amount_msat)
        lnaddr, invoice = self.lnworker.get_bolt11_invoice(
            payment_hash=payment_hash,
            amount_msat=amount_msat,
            message='swap',
            expiry=3600 * 24,
            fallback_address=None,
            channels=channels,
        )
        preimage = self.lnworker.get_preimage(payment_hash)
        request_data = {
            "type": "submarine",
            "pairId": "FTC/FTC",
            "orderSide": "sell",
            "invoice": invoice,
            "refundPublicKey": pubkey.hex()
        }
        response = await self.network.async_send_http_on_proxy(
            'post',
            self.api_url + '/createswap',
            json=request_data,
            timeout=30)
        data = json.loads(response)
        response_id = data["id"]
        zeroconf = data["acceptZeroConf"]
        onchain_amount = data["expectedAmount"]
        locktime = data["timeoutBlockHeight"]
        lockup_address = data["address"]
        redeem_script = data["redeemScript"]
        # verify redeem_script is built with our pubkey and preimage
        redeem_script = bytes.fromhex(redeem_script)
        parsed_script = [x for x in script_GetOp(redeem_script)]
        if not match_script_against_template(redeem_script, WITNESS_TEMPLATE_SWAP):
            raise Exception("fswap check failed: scriptcode does not match template")
        if script_to_p2wsh(redeem_script.hex()) != lockup_address:
            raise Exception("fswap check failed: inconsistent scriptcode and address")
        if hash_160(preimage) != parsed_script[1][1]:
            raise Exception("fswap check failed: our preimage not in script")
        if pubkey != parsed_script[9][1]:
            raise Exception("fswap check failed: our pubkey not in script")
        if locktime != int.from_bytes(parsed_script[6][1], byteorder='little'):
            raise Exception("fswap check failed: inconsistent locktime and script")
        # check that onchain_amount is not more than what we estimated
        if onchain_amount > expected_onchain_amount_sat:
            raise Exception(f"fswap check failed: onchain_amount is more than what we estimated: "
                            f"{onchain_amount} > {expected_onchain_amount_sat}")
        # verify that they are not locking up funds for more than a day
        if locktime - self.network.get_local_height() >= 144:
            raise Exception("fswap check failed: locktime too far in future")
        # create funding tx
        # note: rbf must not decrease payment
        # this is taken care of in wallet._is_rbf_allowed_to_touch_tx_output
        funding_output = PartialTxOutput.from_address_and_value(lockup_address, onchain_amount)
        if tx is None:
            tx = self.wallet.create_transaction(outputs=[funding_output], rbf=True, password=password)
        else:
            dummy_output = PartialTxOutput.from_address_and_value(ln_dummy_address(), expected_onchain_amount_sat)
            tx.outputs().remove(dummy_output)
            tx.add_outputs([funding_output])
            tx.set_rbf(True)
            self.wallet.sign_transaction(tx, password)
        # save swap data in wallet in case we need a refund
        receive_address = self.wallet.get_receiving_address()
        swap = SwapData(
            redeem_script = redeem_script,
            locktime = locktime,
            privkey = privkey,
            preimage = preimage,
            prepay_hash = None,
            lockup_address = lockup_address,
            onchain_amount = expected_onchain_amount_sat,
            receive_address = receive_address,
            lightning_amount = lightning_amount_sat,
            is_reverse = False,
            is_redeemed = False,
            funding_txid = None,
            spending_txid = None,
        )
        self._add_or_reindex_swap(swap)
        self.add_lnwatcher_callback(swap)
        await self.network.broadcast_transaction(tx)
        return tx.txid()

    async def reverse_swap(
            self,
            *,
            lightning_amount_sat: int,
            expected_onchain_amount_sat: int,
            channels = None,
    ) -> bool:
        """send on Lightning, receive on-chain

        - User generates preimage, RHASH. Sends RHASH to server.
        - Server creates an LN invoice for RHASH.
        - User pays LN invoice - except server needs to hold the HTLC as preimage is unknown.
        - Server creates on-chain output locked to RHASH.
        - User spends on-chain output, revealing preimage.
        - Server fulfills HTLC using preimage.

        Note: expected_onchain_amount_sat is BEFORE deducting the on-chain claim tx fee.
        """
        assert self.network
        assert self.lnwatcher
        privkey = os.urandom(32)
        pubkey = ECPrivkey(privkey).get_public_key_bytes(compressed=True)
        preimage = os.urandom(32)
        preimage_hash = sha256(preimage)
        request_data = {
            "type": "reversesubmarine",
            "pairId": "FTC/FTC",
            "orderSide": "buy",
            "invoiceAmount": lightning_amount_sat,
            "preimageHash": preimage_hash.hex(),
            "claimPublicKey": pubkey.hex()
        }
        response = await self.network.async_send_http_on_proxy(
            'post',
            self.api_url + '/createswap',
            json=request_data,
            timeout=30)
        data = json.loads(response)
        invoice = data['invoice']
        fee_invoice = data.get('minerFeeInvoice')
        lockup_address = data['lockupAddress']
        redeem_script = data['redeemScript']
        locktime = data['timeoutBlockHeight']
        onchain_amount = data["onchainAmount"]
        response_id = data['id']
        # verify redeem_script is built with our pubkey and preimage
        redeem_script = bytes.fromhex(redeem_script)
        parsed_script = [x for x in script_GetOp(redeem_script)]
        if not match_script_against_template(redeem_script, WITNESS_TEMPLATE_REVERSE_SWAP):
            raise Exception("rswap check failed: scriptcode does not match template")
        if script_to_p2wsh(redeem_script.hex()) != lockup_address:
            raise Exception("rswap check failed: inconsistent scriptcode and address")
        if hash_160(preimage) != parsed_script[5][1]:
            raise Exception("rswap check failed: our preimage not in script")
        if pubkey != parsed_script[7][1]:
            raise Exception("rswap check failed: our pubkey not in script")
        if locktime != int.from_bytes(parsed_script[10][1], byteorder='little'):
            raise Exception("rswap check failed: inconsistent locktime and script")
        # check that the onchain amount is what we expected
        if onchain_amount < expected_onchain_amount_sat:
            raise Exception(f"rswap check failed: onchain_amount is less than what we expected: "
                            f"{onchain_amount} < {expected_onchain_amount_sat}")
        # verify that we will have enough time to get our tx confirmed
        if locktime - self.network.get_local_height() <= 60:
            raise Exception("rswap check failed: locktime too close")
        # verify invoice preimage_hash
        lnaddr = self.lnworker._check_invoice(invoice)
        invoice_amount = int(lnaddr.get_amount_sat())
        if lnaddr.paymenthash != preimage_hash:
            raise Exception("rswap check failed: inconsistent RHASH and invoice")
        # check that the lightning amount is what we requested
        if fee_invoice:
            fee_lnaddr = self.lnworker._check_invoice(fee_invoice)
            invoice_amount += fee_lnaddr.get_amount_sat()
            prepay_hash = fee_lnaddr.paymenthash
        else:
            prepay_hash = None
        if int(invoice_amount) != lightning_amount_sat:
            raise Exception(f"rswap check failed: invoice_amount ({invoice_amount}) "
                            f"not what we requested ({lightning_amount_sat})")
        # save swap data to wallet file
        receive_address = self.wallet.get_receiving_address()
        swap = SwapData(
            redeem_script = redeem_script,
            locktime = locktime,
            privkey = privkey,
            preimage = preimage,
            prepay_hash = prepay_hash,
            lockup_address = lockup_address,
            onchain_amount = onchain_amount,
            receive_address = receive_address,
            lightning_amount = lightning_amount_sat,
            is_reverse = True,
            is_redeemed = False,
            funding_txid = None,
            spending_txid = None,
        )
        self._add_or_reindex_swap(swap)
        # add callback to lnwatcher
        self.add_lnwatcher_callback(swap)
        # initiate fee payment.
        if fee_invoice:
            self.prepayments[prepay_hash] = preimage_hash
            asyncio.ensure_future(self.lnworker.pay_invoice(fee_invoice, attempts=10))
        # we return if we detect funding
        async def wait_for_funding(swap):
            while swap.spending_txid is None:
                await asyncio.sleep(1)
        # initiate main payment
        tasks = [asyncio.create_task(self.lnworker.pay_invoice(invoice, attempts=10, channels=channels)), asyncio.create_task(wait_for_funding(swap))]
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        success = swap.spending_txid is not None
        return success

    def _add_or_reindex_swap(self, swap: SwapData) -> None:
        if swap.payment_hash.hex() not in self.swaps:
            self.swaps[swap.payment_hash.hex()] = swap
        if swap._funding_prevout:
            self._swaps_by_funding_outpoint[swap._funding_prevout] = swap
        self._swaps_by_lockup_address[swap.lockup_address] = swap

    async def get_pairs(self) -> None:
        """Might raise SwapServerError."""
        from .network import Network
        try:
            response = await Network.async_send_http_on_proxy(
                'get',
                self.api_url + '/getpairs',
                timeout=30)
        except aiohttp.ClientError as e:
            self.logger.error(f"Swap server errored: {e!r}")
            raise SwapServerError() from e
        # we assume server response is well-formed; otherwise let an exception propagate to the crash reporter
        pairs = json.loads(response)
        # cache data to disk
        with open(self.pairs_filename(), 'w', encoding='utf-8') as f:
            f.write(json.dumps(pairs))
        fees = pairs['pairs']['FTC/FTC']['fees']
        self.percentage = fees['percentage']
        self.normal_fee = fees['minerFees']['baseAsset']['normal']
        self.lockup_fee = fees['minerFees']['baseAsset']['reverse']['lockup']
        limits = pairs['pairs']['FTC/FTC']['limits']
        self._min_amount = limits['minimal']
        self._max_amount = limits['maximal']

    def pairs_filename(self):
        return os.path.join(self.wallet.config.path, 'swap_pairs')

    def init_min_max_values(self):
        # use default values if we never requested pairs
        try:
            with open(self.pairs_filename(), 'r', encoding='utf-8') as f:
                pairs = json.loads(f.read())
            limits = pairs['pairs']['FTC/FTC']['limits']
            self._min_amount = limits['minimal']
            self._max_amount = limits['maximal']
        except Exception:
            self._min_amount = 10000
            self._max_amount = 10000000

    def get_max_amount(self):
        return self._max_amount

    def get_min_amount(self):
        return self._min_amount

    def check_invoice_amount(self, x):
        return x >= self.get_min_amount() and x <= self.get_max_amount()

    def _get_recv_amount(self, send_amount: Optional[int], *, is_reverse: bool) -> Optional[int]:
        """For a given swap direction and amount we send, returns how much we will receive.

        Note: in the reverse direction, the mining fee for the on-chain claim tx is NOT accounted for.
        In the reverse direction, the result matches what the swap server returns as response["onchainAmount"].
        """
        if send_amount is None:
            return
        x = Decimal(send_amount)
        percentage = Decimal(self.percentage)
        if is_reverse:
            if not self.check_invoice_amount(x):
                return
            # see/ref:
            # https://github.com/BoltzExchange/boltz-backend/blob/e7e2d30f42a5bea3665b164feb85f84c64d86658/lib/service/Service.ts#L948
            percentage_fee = math.ceil(percentage * x / 100)
            base_fee = self.lockup_fee
            x -= percentage_fee + base_fee
            x = math.floor(x)
            if x < dust_threshold():
                return
        else:
            x -= self.normal_fee
            percentage_fee = math.ceil(x * percentage / (100 + percentage))
            x -= percentage_fee
            if not self.check_invoice_amount(x):
                return
        x = int(x)
        return x

    def _get_send_amount(self, recv_amount: Optional[int], *, is_reverse: bool) -> Optional[int]:
        """For a given swap direction and amount we want to receive, returns how much we will need to send.

        Note: in the reverse direction, the mining fee for the on-chain claim tx is NOT accounted for.
        In the forward direction, the result matches what the swap server returns as response["expectedAmount"].
        """
        if not recv_amount:
            return
        x = Decimal(recv_amount)
        percentage = Decimal(self.percentage)
        if is_reverse:
            # see/ref:
            # https://github.com/BoltzExchange/boltz-backend/blob/e7e2d30f42a5bea3665b164feb85f84c64d86658/lib/service/Service.ts#L928
            # https://github.com/BoltzExchange/boltz-backend/blob/e7e2d30f42a5bea3665b164feb85f84c64d86658/lib/service/Service.ts#L958
            base_fee = self.lockup_fee
            x += base_fee
            x = math.ceil(x / ((100 - percentage) / 100))
            if not self.check_invoice_amount(x):
                return
        else:
            if not self.check_invoice_amount(x):
                return
            # see/ref:
            # https://github.com/BoltzExchange/boltz-backend/blob/e7e2d30f42a5bea3665b164feb85f84c64d86658/lib/service/Service.ts#L708
            # https://github.com/BoltzExchange/boltz-backend/blob/e7e2d30f42a5bea3665b164feb85f84c64d86658/lib/rates/FeeProvider.ts#L90
            percentage_fee = math.ceil(percentage * x / 100)
            x += percentage_fee + self.normal_fee
        x = int(x)
        return x

    def get_recv_amount(self, send_amount: Optional[int], *, is_reverse: bool) -> Optional[int]:
        # first, add percentage fee
        recv_amount = self._get_recv_amount(send_amount, is_reverse=is_reverse)
        # sanity check calculation can be inverted
        if recv_amount is not None:
            inverted_send_amount = self._get_send_amount(recv_amount, is_reverse=is_reverse)
            # accept off-by ones as amt_rcv = recv_amt(send_amt(amt_rcv)) only up to +-1
            if abs(send_amount - inverted_send_amount) > 1:
                raise Exception(f"calc-invert-sanity-check failed. is_reverse={is_reverse}. "
                                f"send_amount={send_amount} -> recv_amount={recv_amount} -> inverted_send_amount={inverted_send_amount}")
        # second, add on-chain claim tx fee
        if is_reverse and recv_amount is not None:
            recv_amount -= self.get_claim_fee()
        return recv_amount

    def get_send_amount(self, recv_amount: Optional[int], *, is_reverse: bool) -> Optional[int]:
        # first, add on-chain claim tx fee
        if is_reverse and recv_amount is not None:
            recv_amount += self.get_claim_fee()
        # second, add percentage fee
        send_amount = self._get_send_amount(recv_amount, is_reverse=is_reverse)
        # sanity check calculation can be inverted
        if send_amount is not None:
            inverted_recv_amount = self._get_recv_amount(send_amount, is_reverse=is_reverse)
            if recv_amount != inverted_recv_amount:
                raise Exception(f"calc-invert-sanity-check failed. is_reverse={is_reverse}. "
                                f"recv_amount={recv_amount} -> send_amount={send_amount} -> inverted_recv_amount={inverted_recv_amount}")
        return send_amount

    def get_swap_by_funding_tx(self, tx: Transaction) -> Optional[SwapData]:
        if len(tx.outputs()) != 1:
            return False
        prevout = TxOutpoint(txid=bytes.fromhex(tx.txid()), out_idx=0)
        return self._swaps_by_funding_outpoint.get(prevout)

    def get_swap_by_claim_tx(self, tx: Transaction) -> Optional[SwapData]:
        txin = tx.inputs()[0]
        return self.get_swap_by_claim_txin(txin)

    def get_swap_by_claim_txin(self, txin: TxInput) -> Optional[SwapData]:
        return self._swaps_by_funding_outpoint.get(txin.prevout)

    def is_lockup_address_for_a_swap(self, addr: str) -> bool:
        return bool(self._swaps_by_lockup_address.get(addr))

    def add_txin_info(self, txin: PartialTxInput) -> None:
        """Add some info to a claim txin.
        note: even without signing, this is useful for tx size estimation.
        """
        swap = self.get_swap_by_claim_txin(txin)
        if not swap:
            return
        preimage = swap.preimage if swap.is_reverse else 0
        witness_script = swap.redeem_script
        txin.script_sig = b''
        txin.witness_script = witness_script
        sig_dummy = b'\x00' * 71  # DER-encoded ECDSA sig, with low S and low R
        witness = [sig_dummy, preimage, witness_script]
        txin.witness_sizehint = len(bytes.fromhex(construct_witness(witness)))

    @classmethod
    def sign_tx(cls, tx: PartialTransaction, swap: SwapData) -> None:
        preimage = swap.preimage if swap.is_reverse else 0
        witness_script = swap.redeem_script
        txin = tx.inputs()[0]
        assert len(tx.inputs()) == 1, f"expected 1 input for swap claim tx. found {len(tx.inputs())}"
        assert txin.prevout.txid.hex() == swap.funding_txid
        txin.script_sig = b''
        txin.witness_script = witness_script
        sig = bytes.fromhex(tx.sign_txin(0, swap.privkey))
        witness = [sig, preimage, witness_script]
        txin.witness = bytes.fromhex(construct_witness(witness))

    @classmethod
    def _create_and_sign_claim_tx(
        cls,
        *,
        txin: PartialTxInput,
        swap: SwapData,
        config: 'SimpleConfig',
    ) -> PartialTransaction:
        # FIXME the mining fee should depend on swap.is_reverse.
        #       the txs are not the same size...
        amount_sat = txin.value_sats() - cls._get_claim_fee(config=config)
        if amount_sat < dust_threshold():
            raise BelowDustLimit()
        if swap.is_reverse:  # successful reverse swap
            locktime = 0
            # preimage will be set in sign_tx
        else:  # timing out forward swap
            locktime = swap.locktime
        tx = create_claim_tx(
            txin=txin,
            witness_script=swap.redeem_script,
            address=swap.receive_address,
            amount_sat=amount_sat,
            locktime=locktime,
        )
        cls.sign_tx(tx, swap)
        return tx

    def max_amount_forward_swap(self) -> Optional[int]:
        """ returns None if we cannot swap """
        max_swap_amt_ln = self.get_max_amount()
        max_recv_amt_ln = int(self.lnworker.num_sats_can_receive())
        max_amt_ln = int(min(max_swap_amt_ln, max_recv_amt_ln))
        max_amt_oc = self.get_send_amount(max_amt_ln, is_reverse=False) or 0
        min_amt_oc = self.get_send_amount(self.get_min_amount(), is_reverse=False) or 0
        return max_amt_oc if max_amt_oc >= min_amt_oc else None
