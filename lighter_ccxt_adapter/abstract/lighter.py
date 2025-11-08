from ccxt.base.types import Entry


class ImplicitAPI:
    # Public GET endpoints
    public_get_funding = publicGetApiFunding = Entry('api/v1/funding-rates', 'public', 'GET', {'cost': 1})




