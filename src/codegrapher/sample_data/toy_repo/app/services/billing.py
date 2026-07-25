import stripe


def charge_card(amount_cents):
    stripe_client = stripe.Client()
    return stripe_client.charges.create(amount=amount_cents)


def refund_card(amount_cents):
    stripe_client = stripe.Client()
    return stripe_client.refunds.create(amount=amount_cents)
