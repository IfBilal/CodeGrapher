"""Registry of framework-specific naming conventions the parser pattern-matches
against, so recognizing a new ORM is "register a new profile," not "rewrite
the detection logic."

Each profile describes one ORM's conventions: what its model base class is
usually named, and what its field-declaration calls look like. Field
extraction tries every registered profile's field/relationship call names
against each class attribute independently - so even if two frameworks share
an ambiguous base class name (both Flask-SQLAlchemy and Django commonly use
something resolving to "Model"), the actual field calls (SQLAlchemy's
`Column(...)` vs Django's `CharField(...)`) naturally disambiguate which
profile's parsing rules actually apply, without us having to pick one
profile per class up front.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ORMProfile:
    name: str
    base_class_names: set[str]
    # Call names whose result is a plain field (e.g. SQLAlchemy's Column(),
    # Django's CharField()/IntegerField()/ForeignKey()).
    field_call_names: set[str]
    # Call names that represent a relationship to another model, extracted
    # as a graph edge (RELATES_TO) rather than a Field node.
    relationship_call_names: set[str] = field(default_factory=set)


SQLALCHEMY = ORMProfile(
    name="sqlalchemy",
    base_class_names={"Base", "Model"},  # "Model" covers Flask-SQLAlchemy's db.Model
    field_call_names={"Column"},
    relationship_call_names={"relationship"},
)

DJANGO = ORMProfile(
    name="django",
    base_class_names={"Model"},  # django.db.models.Model
    field_call_names={
        "CharField",
        "IntegerField",
        "BooleanField",
        "DateTimeField",
        "TextField",
        "EmailField",
        "FloatField",
        "DecimalField",
        "AutoField",
        "ForeignKey",
        "OneToOneField",
    },
    relationship_call_names={"ManyToManyField"},
)

ORM_PROFILES: list[ORMProfile] = [SQLALCHEMY, DJANGO]
