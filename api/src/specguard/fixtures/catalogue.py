"""The product catalogue the synthetic spec sheets are built from.

Everything here is invented. Nothing is copied from a real supplier document, and no
retailer is named — the project's knowledge base is public law and its test data is
authored in this repo.
"""

from __future__ import annotations

from specguard.models.common import Language, SpecGuardModel

#: Annex XIV energy conversion factors, kJ and kcal per gram. Compliant fixtures have
#: their energy computed with these, so NUTRITION_ARITHMETIC passing on them means the
#: arithmetic genuinely holds rather than the tolerance being generous.
ENERGY_FACTORS_KJ: dict[str, float] = {
    "carbohydrate": 17.0,
    "polyols": 10.0,
    "protein": 17.0,
    "fat": 37.0,
    "fibre": 8.0,
    "alcohol": 29.0,
    "organic_acid": 13.0,
}
ENERGY_FACTORS_KCAL: dict[str, float] = {
    "carbohydrate": 4.0,
    "polyols": 2.4,
    "protein": 4.0,
    "fat": 9.0,
    "fibre": 2.0,
    "alcohol": 7.0,
    "organic_acid": 3.0,
}


class Nutrients(SpecGuardModel):
    """Macronutrients per 100 g or 100 ml, in grams."""

    fat: float
    saturates: float
    carbohydrate: float
    sugars: float
    fibre: float
    protein: float
    salt: float

    def energy_kj(self) -> float:
        """Energy computed from the macros using the Annex XIV factors."""
        return round(
            self.carbohydrate * ENERGY_FACTORS_KJ["carbohydrate"]
            + self.fat * ENERGY_FACTORS_KJ["fat"]
            + self.protein * ENERGY_FACTORS_KJ["protein"]
            + self.fibre * ENERGY_FACTORS_KJ["fibre"]
        )

    def energy_kcal(self) -> float:
        """Energy in kcal, computed independently rather than divided out of the kJ."""
        return round(
            self.carbohydrate * ENERGY_FACTORS_KCAL["carbohydrate"]
            + self.fat * ENERGY_FACTORS_KCAL["fat"]
            + self.protein * ENERGY_FACTORS_KCAL["protein"]
            + self.fibre * ENERGY_FACTORS_KCAL["fibre"]
        )


class IngredientSpec(SpecGuardModel):
    """One ingredient line. ``allergen`` names the Annex II substance, if any."""

    name: str
    percentage: float | None = None
    allergen: str | None = None


class ProductTemplate(SpecGuardModel):
    """A compliant product. Defects are applied on top of this, never baked in."""

    language: Language = Language.EN
    slug: str
    product_name: str
    legal_name: str
    net_quantity: str
    ingredients: list[IngredientSpec]
    nutrients: Nutrients
    storage: str
    durability_kind: str
    durability: str
    instructions: str | None = None
    origin: str
    primary_ingredient: str | None = None
    primary_ingredient_origin: str | None = None
    nutrition_claim: str | None = None
    health_claim: str | None = None
    supplier: str
    supplier_address: str


CATALOGUE: tuple[ProductTemplate, ...] = (
    ProductTemplate(
        slug="strawberry-yogurt",
        product_name="Strawberry Yogurt",
        legal_name="Strawberry yogurt with live cultures",
        net_quantity="500 g",
        ingredients=[
            IngredientSpec(name="Yogurt (MILK)", percentage=82.0, allergen="MILK"),
            IngredientSpec(name="Strawberries", percentage=12.0),
            IngredientSpec(name="Sugar", percentage=5.0),
            IngredientSpec(name="Pectin"),
        ],
        nutrients=Nutrients(
            fat=3.1,
            saturates=2.0,
            carbohydrate=12.4,
            sugars=11.8,
            fibre=0.4,
            protein=3.6,
            salt=0.12,
        ),
        storage="Keep refrigerated at 0-5 °C.",
        durability_kind="Use by",
        durability="21 days from production",
        origin="Germany",
        primary_ingredient="Yogurt",
        primary_ingredient_origin="Germany",
        supplier="Nordmilch Erzeugnisse GmbH",
        supplier_address="Industriestrasse 12, 28195 Bremen, Germany",
    ),
    ProductTemplate(
        slug="oat-granola",
        product_name="Honey & Almond Oat Granola",
        legal_name="Toasted oat cereal with honey and almonds",
        net_quantity="750 g",
        ingredients=[
            IngredientSpec(name="Wholegrain OAT flakes", percentage=64.0, allergen="OATS"),
            IngredientSpec(name="ALMONDS", percentage=9.0, allergen="ALMONDS"),
            IngredientSpec(name="Honey", percentage=8.0),
            IngredientSpec(name="Sunflower oil", percentage=7.0),
            IngredientSpec(name="Raisins", percentage=6.0),
            IngredientSpec(name="Salt"),
        ],
        nutrients=Nutrients(
            fat=16.2,
            saturates=1.8,
            carbohydrate=58.0,
            sugars=14.5,
            fibre=7.4,
            protein=10.1,
            salt=0.31,
        ),
        storage="Store in a cool, dry place. Reseal after opening.",
        durability_kind="Best before end",
        durability="12 months from production",
        origin="Netherlands",
        primary_ingredient="Oat flakes",
        primary_ingredient_origin="Finland",
        nutrition_claim="High in fibre",
        supplier="De Graanmolen B.V.",
        supplier_address="Havenweg 44, 3011 XA Rotterdam, Netherlands",
    ),
    ProductTemplate(
        slug="tomato-basil-soup",
        product_name="Tomato & Basil Soup",
        legal_name="Tomato soup with basil",
        net_quantity="600 ml",
        ingredients=[
            IngredientSpec(name="Tomatoes", percentage=71.0),
            IngredientSpec(name="Water"),
            IngredientSpec(name="Onions", percentage=6.0),
            IngredientSpec(name="Single CREAM", percentage=4.0, allergen="MILK"),
            IngredientSpec(name="Basil", percentage=1.2),
            IngredientSpec(name="Salt"),
        ],
        nutrients=Nutrients(
            fat=2.1,
            saturates=1.1,
            carbohydrate=5.8,
            sugars=4.9,
            fibre=1.1,
            protein=1.3,
            salt=0.62,
        ),
        storage="Keep refrigerated. Consume within 3 days of opening.",
        durability_kind="Use by",
        durability="14 days from production",
        instructions="Heat gently until piping hot. Do not boil.",
        origin="Italy",
        primary_ingredient="Tomatoes",
        primary_ingredient_origin="Italy",
        supplier="Conserve del Sole S.p.A.",
        supplier_address="Via Garibaldi 88, 84013 Cava de' Tirreni, Italy",
    ),
    ProductTemplate(
        slug="wholemeal-bread",
        product_name="Wholemeal Sliced Bread",
        legal_name="Wholemeal wheat bread, sliced",
        net_quantity="800 g",
        ingredients=[
            IngredientSpec(name="Wholemeal WHEAT flour", percentage=68.0, allergen="WHEAT"),
            IngredientSpec(name="Water"),
            IngredientSpec(name="Yeast"),
            IngredientSpec(name="Salt"),
            IngredientSpec(name="Rapeseed oil", percentage=1.4),
        ],
        nutrients=Nutrients(
            fat=2.4,
            saturates=0.5,
            carbohydrate=38.9,
            sugars=2.6,
            fibre=6.8,
            protein=9.4,
            salt=0.98,
        ),
        storage="Store in a cool, dry place.",
        durability_kind="Best before",
        durability="6 days from production",
        origin="United Kingdom",
        primary_ingredient="Wholemeal wheat flour",
        primary_ingredient_origin="United Kingdom",
        nutrition_claim="Source of fibre",
        supplier="Pennine Bakeries Ltd",
        supplier_address="Mill Lane, Leeds LS10 1AB, United Kingdom",
    ),
    ProductTemplate(
        slug="orange-juice",
        product_name="Orange Juice, Not From Concentrate",
        legal_name="Orange juice",
        net_quantity="1 l",
        ingredients=[IngredientSpec(name="Orange juice", percentage=100.0)],
        nutrients=Nutrients(
            fat=0.2,
            saturates=0.0,
            carbohydrate=9.1,
            sugars=8.9,
            fibre=0.3,
            protein=0.7,
            salt=0.01,
        ),
        storage="Keep refrigerated once opened and consume within 3 days.",
        durability_kind="Best before",
        durability="30 days from production",
        origin="Spain",
        primary_ingredient="Oranges",
        primary_ingredient_origin="Spain",
        health_claim="Vitamin C contributes to the normal function of the immune system",
        supplier="Zumos del Levante S.L.",
        supplier_address="Poligono Industrial 7, 46520 Puerto de Sagunto, Spain",
    ),
    ProductTemplate(
        slug="cheddar-cheese",
        product_name="Mature Cheddar Cheese",
        legal_name="Mature cheddar cheese",
        net_quantity="350 g",
        ingredients=[
            IngredientSpec(name="MILK", percentage=98.0, allergen="MILK"),
            IngredientSpec(name="Salt"),
            IngredientSpec(name="Starter cultures"),
            IngredientSpec(name="Vegetarian rennet"),
        ],
        nutrients=Nutrients(
            fat=34.9,
            saturates=21.7,
            carbohydrate=0.1,
            sugars=0.1,
            fibre=0.0,
            protein=25.4,
            salt=1.8,
        ),
        storage="Keep refrigerated at 0-5 °C.",
        durability_kind="Use by",
        durability="60 days from production",
        origin="Ireland",
        primary_ingredient="Milk",
        primary_ingredient_origin="Ireland",
        supplier="Shannon Creamery Co-operative",
        supplier_address="Coolrain Road, Co. Limerick V94 X2P8, Ireland",
    ),
    ProductTemplate(
        slug="pasta-sauce",
        product_name="Pasta Sauce with Mushrooms",
        legal_name="Tomato sauce with mushrooms for pasta",
        net_quantity="440 g",
        ingredients=[
            IngredientSpec(name="Tomatoes", percentage=62.0),
            IngredientSpec(name="Mushrooms", percentage=14.0),
            IngredientSpec(name="Onions", percentage=8.0),
            IngredientSpec(name="Olive oil", percentage=3.5),
            IngredientSpec(name="Garlic", percentage=1.0),
            IngredientSpec(name="Salt"),
        ],
        nutrients=Nutrients(
            fat=3.9,
            saturates=0.6,
            carbohydrate=6.2,
            sugars=5.1,
            fibre=1.6,
            protein=1.5,
            salt=0.71,
        ),
        storage="Store in a cool, dry place. Refrigerate after opening.",
        durability_kind="Best before",
        durability="18 months from production",
        origin="Italy",
        primary_ingredient="Tomatoes",
        primary_ingredient_origin="Italy",
        supplier="Salsa Bella S.r.l.",
        supplier_address="Strada Provinciale 22, 43122 Parma, Italy",
    ),
    ProductTemplate(
        slug="dark-chocolate",
        product_name="Dark Chocolate 70%",
        legal_name="Dark chocolate",
        net_quantity="100 g",
        ingredients=[
            IngredientSpec(name="Cocoa mass", percentage=58.0),
            IngredientSpec(name="Sugar", percentage=29.0),
            IngredientSpec(name="Cocoa butter", percentage=12.0),
            IngredientSpec(name="SOYA lecithin", allergen="SOYA"),
        ],
        nutrients=Nutrients(
            fat=42.1,
            saturates=25.3,
            carbohydrate=33.8,
            sugars=29.0,
            fibre=10.9,
            protein=7.8,
            salt=0.02,
        ),
        storage="Store below 18 °C in a dry place.",
        durability_kind="Best before end",
        durability="15 months from production",
        origin="Belgium",
        primary_ingredient="Cocoa mass",
        primary_ingredient_origin="Ghana",
        supplier="Chocolaterie Verhoeven N.V.",
        supplier_address="Nijverheidslaan 3, 9000 Ghent, Belgium",
    ),
    ProductTemplate(
        slug="salted-crisps",
        product_name="Lightly Salted Potato Crisps",
        legal_name="Potato crisps, lightly salted",
        net_quantity="150 g",
        ingredients=[
            IngredientSpec(name="Potatoes", percentage=88.0),
            IngredientSpec(name="Sunflower oil", percentage=11.0),
            IngredientSpec(name="Salt", percentage=1.0),
        ],
        nutrients=Nutrients(
            fat=31.2,
            saturates=3.1,
            carbohydrate=49.6,
            sugars=0.6,
            fibre=4.2,
            protein=6.1,
            salt=1.1,
        ),
        storage="Store in a cool, dry place.",
        durability_kind="Best before",
        durability="9 months from production",
        origin="Belgium",
        primary_ingredient="Potatoes",
        primary_ingredient_origin="Belgium",
        supplier="Ardennes Snacks S.A.",
        supplier_address="Rue de l'Industrie 19, 5000 Namur, Belgium",
    ),
    ProductTemplate(
        slug="chicken-soup",
        product_name="Chicken & Vegetable Soup",
        legal_name="Chicken soup with vegetables",
        net_quantity="400 g",
        ingredients=[
            IngredientSpec(name="Chicken stock", percentage=52.0),
            IngredientSpec(name="Chicken", percentage=18.0),
            IngredientSpec(name="Carrots", percentage=11.0),
            IngredientSpec(name="Leeks", percentage=7.0),
            IngredientSpec(name="WHEAT flour", percentage=3.0, allergen="WHEAT"),
            IngredientSpec(name="CELERY", percentage=2.0, allergen="CELERY"),
            IngredientSpec(name="Salt"),
        ],
        nutrients=Nutrients(
            fat=2.8,
            saturates=0.9,
            carbohydrate=4.4,
            sugars=1.7,
            fibre=0.9,
            protein=4.9,
            salt=0.79,
        ),
        storage="Keep refrigerated. Use within 2 days of opening.",
        durability_kind="Use by",
        durability="10 days from production",
        instructions="Empty into a saucepan and heat until piping hot.",
        origin="France",
        primary_ingredient="Chicken stock",
        primary_ingredient_origin="France",
        supplier="Potages Duval SAS",
        supplier_address="12 Rue des Halles, 35000 Rennes, France",
    ),
    ProductTemplate(
        slug="digestive-biscuits",
        product_name="Wholemeal Digestive Biscuits",
        legal_name="Wholemeal biscuits",
        net_quantity="400 g",
        ingredients=[
            IngredientSpec(name="Wholemeal WHEAT flour", percentage=51.0, allergen="WHEAT"),
            IngredientSpec(name="Palm oil", percentage=19.0),
            IngredientSpec(name="Sugar", percentage=16.0),
            IngredientSpec(name="Partially inverted sugar syrup"),
            IngredientSpec(name="Raising agents"),
            IngredientSpec(name="Salt"),
        ],
        nutrients=Nutrients(
            fat=20.9,
            saturates=9.8,
            carbohydrate=62.1,
            sugars=16.4,
            fibre=4.1,
            protein=6.9,
            salt=1.02,
        ),
        storage="Store in a cool, dry place.",
        durability_kind="Best before end",
        durability="10 months from production",
        origin="United Kingdom",
        primary_ingredient="Wholemeal wheat flour",
        primary_ingredient_origin="United Kingdom",
        supplier="Ashford Biscuit Company Ltd",
        supplier_address="Unit 4, Chartham Estate, Kent CT4 7HT, United Kingdom",
    ),
    ProductTemplate(
        slug="salmon-fillets",
        product_name="Atlantic Salmon Fillets",
        legal_name="Skinless Atlantic salmon fillets",
        net_quantity="240 g",
        ingredients=[IngredientSpec(name="Atlantic SALMON", percentage=100.0, allergen="FISH")],
        nutrients=Nutrients(
            fat=13.6,
            saturates=2.9,
            carbohydrate=0.0,
            sugars=0.0,
            fibre=0.0,
            protein=20.4,
            salt=0.14,
        ),
        storage="Keep refrigerated at 0-4 °C. Do not refreeze.",
        durability_kind="Use by",
        durability="7 days from production",
        instructions="Cook thoroughly until the flesh is opaque throughout.",
        origin="Norway",
        primary_ingredient="Salmon",
        primary_ingredient_origin="Norway",
        supplier="Fjordfisk AS",
        supplier_address="Havnegata 9, 6002 Alesund, Norway",
    ),
    ProductTemplate(
        slug="hummus",
        product_name="Classic Hummus",
        legal_name="Chickpea dip with sesame paste",
        net_quantity="200 g",
        ingredients=[
            IngredientSpec(name="Chickpeas", percentage=54.0),
            IngredientSpec(name="Water"),
            IngredientSpec(name="Rapeseed oil", percentage=9.0),
            IngredientSpec(name="SESAME paste", percentage=7.0, allergen="SESAME"),
            IngredientSpec(name="Lemon juice", percentage=3.0),
            IngredientSpec(name="Garlic", percentage=1.0),
            IngredientSpec(name="Salt"),
        ],
        nutrients=Nutrients(
            fat=15.1,
            saturates=1.6,
            carbohydrate=10.2,
            sugars=0.8,
            fibre=5.3,
            protein=6.4,
            salt=0.86,
        ),
        storage="Keep refrigerated. Consume within 3 days of opening.",
        durability_kind="Use by",
        durability="16 days from production",
        origin="Greece",
        primary_ingredient="Chickpeas",
        primary_ingredient_origin="Turkey",
        supplier="Meze Foods A.E.",
        supplier_address="Odos Athinon 210, 104 42 Athens, Greece",
    ),
    ProductTemplate(
        slug="apple-juice",
        product_name="Cloudy Apple Juice",
        legal_name="Cloudy apple juice from concentrate",
        net_quantity="750 ml",
        ingredients=[
            IngredientSpec(name="Water"),
            IngredientSpec(name="Apple juice concentrate", percentage=12.0),
            IngredientSpec(name="Ascorbic acid"),
        ],
        nutrients=Nutrients(
            fat=0.1,
            saturates=0.0,
            carbohydrate=10.4,
            sugars=10.1,
            fibre=0.2,
            protein=0.2,
            salt=0.01,
        ),
        storage="Keep refrigerated once opened.",
        durability_kind="Best before",
        durability="12 months from production",
        origin="Poland",
        primary_ingredient="Apple juice concentrate",
        primary_ingredient_origin="Poland",
        supplier="Sady Wisly Sp. z o.o.",
        supplier_address="ul. Owocowa 5, 24-100 Pulawy, Poland",
    ),
    ProductTemplate(
        slug="mixed-nuts",
        product_name="Roasted Mixed Nuts",
        legal_name="Roasted mixed nuts, salted",
        net_quantity="200 g",
        ingredients=[
            IngredientSpec(name="PEANUTS", percentage=42.0, allergen="PEANUTS"),
            IngredientSpec(name="CASHEW NUTS", percentage=26.0, allergen="CASHEWS"),
            IngredientSpec(name="ALMONDS", percentage=18.0, allergen="ALMONDS"),
            IngredientSpec(name="HAZELNUTS", percentage=12.0, allergen="HAZELNUTS"),
            IngredientSpec(name="Sunflower oil"),
            IngredientSpec(name="Salt"),
        ],
        nutrients=Nutrients(
            fat=49.8,
            saturates=7.2,
            carbohydrate=12.1,
            sugars=4.3,
            fibre=7.9,
            protein=21.6,
            salt=0.94,
        ),
        storage="Store in a cool, dry place away from direct sunlight.",
        durability_kind="Best before end",
        durability="8 months from production",
        origin="Germany",
        primary_ingredient="Peanuts",
        primary_ingredient_origin="Argentina",
        nutrition_claim="High in protein",
        supplier="Nussmeister Handels GmbH",
        supplier_address="Am Hafen 23, 20457 Hamburg, Germany",
    ),
    # --- German spec sheets ---------------------------------------------------
    # The corpus is indexed in English and German, so some of the test data has to be
    # German or the multilingual index is never actually exercised. Allergens are
    # emphasised in capitals here too — Art. 21(1)(b) is about typeset, not language.
    ProductTemplate(
        language=Language.DE,
        slug="de-erdbeerjoghurt",
        product_name="Erdbeerjoghurt",
        legal_name="Erdbeerjoghurt mit lebenden Kulturen",
        net_quantity="500 g",
        ingredients=[
            IngredientSpec(name="Joghurt (MILCH)", percentage=82.0, allergen="MILCH"),
            IngredientSpec(name="Erdbeeren", percentage=12.0),
            IngredientSpec(name="Zucker", percentage=5.0),
            IngredientSpec(name="Pektin"),
        ],
        nutrients=Nutrients(
            fat=3.1,
            saturates=2.0,
            carbohydrate=12.4,
            sugars=11.8,
            fibre=0.4,
            protein=3.6,
            salt=0.12,
        ),
        storage="Bei 0-5 °C gekühlt aufbewahren.",
        durability_kind="Verbrauchen bis",
        durability="21 Tage ab Herstellung",
        origin="Deutschland",
        primary_ingredient="Joghurt",
        primary_ingredient_origin="Deutschland",
        supplier="Nordmilch Erzeugnisse GmbH",
        supplier_address="Industriestrasse 12, 28195 Bremen, Deutschland",
    ),
    ProductTemplate(
        language=Language.DE,
        slug="de-vollkornbrot",
        product_name="Vollkornbrot, geschnitten",
        legal_name="Weizenvollkornbrot, geschnitten",
        net_quantity="750 g",
        ingredients=[
            IngredientSpec(name="WEIZEN-Vollkornmehl", percentage=68.0, allergen="WEIZEN"),
            IngredientSpec(name="Wasser"),
            IngredientSpec(name="Hefe"),
            IngredientSpec(name="Speisesalz"),
            IngredientSpec(name="Rapsoel", percentage=1.4),
        ],
        nutrients=Nutrients(
            fat=2.4,
            saturates=0.5,
            carbohydrate=38.9,
            sugars=2.6,
            fibre=6.8,
            protein=9.4,
            salt=0.98,
        ),
        storage="Kuehl und trocken lagern.",
        durability_kind="Mindestens haltbar bis",
        durability="6 Tage ab Herstellung",
        origin="Deutschland",
        primary_ingredient="Weizenvollkornmehl",
        primary_ingredient_origin="Deutschland",
        nutrition_claim="Ballaststoffquelle",
        supplier="Backhaus Westfalen GmbH",
        supplier_address="Muehlenweg 8, 48143 Muenster, Deutschland",
    ),
    ProductTemplate(
        language=Language.DE,
        slug="de-haferknusper",
        product_name="Hafer-Knuspermuesli mit Honig und Mandeln",
        legal_name="Geroestetes Hafercerealien-Erzeugnis mit Honig und Mandeln",
        net_quantity="750 g",
        ingredients=[
            IngredientSpec(name="HAFER-Vollkornflocken", percentage=64.0, allergen="HAFER"),
            IngredientSpec(name="MANDELN", percentage=9.0, allergen="MANDELN"),
            IngredientSpec(name="Honig", percentage=8.0),
            IngredientSpec(name="Sonnenblumenoel", percentage=7.0),
            IngredientSpec(name="Rosinen", percentage=6.0),
            IngredientSpec(name="Speisesalz"),
        ],
        nutrients=Nutrients(
            fat=16.2,
            saturates=1.8,
            carbohydrate=58.0,
            sugars=14.5,
            fibre=7.4,
            protein=10.1,
            salt=0.31,
        ),
        storage="Kuehl und trocken lagern. Nach dem Oeffnen wieder verschliessen.",
        durability_kind="Mindestens haltbar bis Ende",
        durability="12 Monate ab Herstellung",
        origin="Niederlande",
        primary_ingredient="Haferflocken",
        primary_ingredient_origin="Finnland",
        nutrition_claim="Hoher Ballaststoffgehalt",
        supplier="De Graanmolen B.V.",
        supplier_address="Havenweg 44, 3011 XA Rotterdam, Niederlande",
    ),
    ProductTemplate(
        language=Language.DE,
        slug="de-zartbitterschokolade",
        product_name="Zartbitterschokolade 70%",
        legal_name="Zartbitterschokolade",
        net_quantity="100 g",
        ingredients=[
            IngredientSpec(name="Kakaomasse", percentage=58.0),
            IngredientSpec(name="Zucker", percentage=29.0),
            IngredientSpec(name="Kakaobutter", percentage=12.0),
            IngredientSpec(name="SOJA-Lecithin", allergen="SOJA"),
        ],
        nutrients=Nutrients(
            fat=42.1,
            saturates=25.3,
            carbohydrate=33.8,
            sugars=29.0,
            fibre=10.9,
            protein=7.8,
            salt=0.02,
        ),
        storage="Unter 18 °C trocken lagern.",
        durability_kind="Mindestens haltbar bis Ende",
        durability="15 Monate ab Herstellung",
        origin="Belgien",
        primary_ingredient="Kakaomasse",
        primary_ingredient_origin="Ghana",
        supplier="Chocolaterie Verhoeven N.V.",
        supplier_address="Nijverheidslaan 3, 9000 Gent, Belgien",
    ),
    ProductTemplate(
        language=Language.DE,
        slug="de-tomatensuppe",
        product_name="Tomatensuppe mit Basilikum",
        legal_name="Tomatensuppe mit Basilikum",
        net_quantity="600 ml",
        ingredients=[
            IngredientSpec(name="Tomaten", percentage=71.0),
            IngredientSpec(name="Wasser"),
            IngredientSpec(name="Zwiebeln", percentage=6.0),
            IngredientSpec(name="SAHNE", percentage=4.0, allergen="MILCH"),
            IngredientSpec(name="Basilikum", percentage=1.2),
            IngredientSpec(name="Speisesalz"),
        ],
        nutrients=Nutrients(
            fat=2.1,
            saturates=1.1,
            carbohydrate=5.8,
            sugars=4.9,
            fibre=1.1,
            protein=1.3,
            salt=0.62,
        ),
        storage="Gekuehlt aufbewahren. Nach dem Oeffnen innerhalb von 3 Tagen verbrauchen.",
        durability_kind="Verbrauchen bis",
        durability="14 Tage ab Herstellung",
        instructions="Vorsichtig erhitzen, nicht kochen lassen.",
        origin="Italien",
        primary_ingredient="Tomaten",
        primary_ingredient_origin="Italien",
        supplier="Conserve del Sole S.p.A.",
        supplier_address="Via Garibaldi 88, 84013 Cava de' Tirreni, Italien",
    ),
    ProductTemplate(
        language=Language.DE,
        slug="de-apfelsaft",
        product_name="Naturtrueber Apfelsaft",
        legal_name="Apfelsaft aus Apfelsaftkonzentrat, naturtrueb",
        net_quantity="750 ml",
        ingredients=[
            IngredientSpec(name="Wasser"),
            IngredientSpec(name="Apfelsaftkonzentrat", percentage=12.0),
            IngredientSpec(name="Ascorbinsaeure"),
        ],
        nutrients=Nutrients(
            fat=0.1,
            saturates=0.0,
            carbohydrate=10.4,
            sugars=10.1,
            fibre=0.2,
            protein=0.2,
            salt=0.01,
        ),
        storage="Nach dem Oeffnen gekuehlt aufbewahren.",
        durability_kind="Mindestens haltbar bis",
        durability="12 Monate ab Herstellung",
        origin="Polen",
        primary_ingredient="Apfelsaftkonzentrat",
        primary_ingredient_origin="Polen",
        supplier="Sady Wisly Sp. z o.o.",
        supplier_address="ul. Owocowa 5, 24-100 Pulawy, Polen",
    ),
)
