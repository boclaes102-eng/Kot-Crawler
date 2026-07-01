from dataclasses import dataclass, field


@dataclass
class KotListing:
    source: str = "missing"
    url: str = "missing"
    title: str = "missing"
    address: str = "missing"
    neighborhood: str = "missing"
    price_eur_month: str = "missing"
    size_m2: str = "missing"
    price_per_m2: str = "missing"
    listing_type: str = "missing"       # kamer / studio / appartement
    furnished: str = "missing"
    private_bathroom: str = "missing"
    shared_bathroom: str = "missing"
    private_kitchen: str = "missing"
    shared_kitchen: str = "missing"
    internet_included: str = "missing"
    utilities_included: str = "missing"
    washing_machine: str = "missing"
    elevator: str = "missing"
    pets_allowed: str = "missing"
    available_from: str = "missing"
    scraped_on: str = "missing"

    def compute_price_per_m2(self) -> None:
        """Fill price_per_m2 when both price and size are known numbers."""
        try:
            price = float(self.price_eur_month.replace("€", "").replace(",", ".").strip())
            size = float(self.size_m2.replace("m²", "").replace("m2", "").strip())
            if price > 0 and size > 0:
                self.price_per_m2 = f"{price / size:.1f}"
        except (ValueError, AttributeError):
            pass
