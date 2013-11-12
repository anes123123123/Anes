from wallet import Wallet


class WalletFactory(object):
    def __new__(cls, config):
        if config.get('bitkey', False):
            # if user requested support for Bitkey device,
            # import Bitkey driver
            from wallet_bitkey import WalletBitkey
            return WalletBitkey(config)

        # Load standard wallet
        return Wallet(config)
