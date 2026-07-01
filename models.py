from dataclasses import dataclass


@dataclass
class KotListing:
    """One kot/studio/apartment listing.

    Every field is a string. Empty string means "not found on the site" —
    we never guess a value.
    """
    source: str = ""
    url: str = ""
    title: str = ""
    address: str = ""
    neighborhood: str = ""
    price_eur_month: str = ""
    size_m2: str = ""
    price_per_m2: str = ""
    listing_type: str = ""          # Kamer / Studio / Appartement / ...
    furnished: str = ""             # Yes / No / ""
    private_bathroom: str = ""
    shared_bathroom: str = ""
    private_kitchen: str = ""
    shared_kitchen: str = ""
    internet_included: str = ""
    utilities_included: str = ""
    washing_machine: str = ""
    elevator: str = ""
    pets_allowed: str = ""
    available_from: str = ""
    scraped_on: str = ""

    def compute_price_per_m2(self) -> None:
        """Fill price_per_m2 when both price and size are usable numbers."""
        try:
            price = float(self.price_eur_month)
            size = float(self.size_m2)
        except (ValueError, TypeError):
            return
        if price > 0 and size > 0:
            self.price_per_m2 = f"{price / size:.1f}"
