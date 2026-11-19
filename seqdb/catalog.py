"""Sequences of the database and the order-number helpers built on them."""
from .sequence import Sequence, SequenceError


class Catalog:
    def __init__(self):
        self.sequences = {}
        self.last_values = {}
        self.last_sequence = None

    def create(self, name, **options):
        if name in self.sequences:
            raise SequenceError(f'relation "{name}" already exists')
        self.sequences[name] = Sequence(name, **options)
        return self.sequences[name]

    def sequence(self, name):
        if name not in self.sequences:
            raise SequenceError(f'relation "{name}" does not exist')
        return self.sequences[name]


def order_number(session, prefix="ORD"):
    """ORD-0000123 style numbers for the shop frontend."""
    return f"{prefix}-{session.nextval('order_seq'):07d}"
