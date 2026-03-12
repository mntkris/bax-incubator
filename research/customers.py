"""Research for optimal usage od Pydantic constructs allowing
as simple as possible sharing validations, properties, dependencies
or dictionaries etc between data and rest api layers.

Bax is research project of erp like
document-resource-evidence paradigm.
The goal is to refine optimal set of building blocks
taking usability, simplicity and DRY into account.

Module contains sketches of scalars, composites and resources.
It is the inital POC of bax resource design thus some functionality
(ie schemas) are omitted, some are overspimplified (ie predicate
text rules) and some redundant (for demo purposes 1st char in CustomerSymbol).

Scalar is Annotated Pydantic Field and corresponds
to postgres DOMAIN.
"""


import typing
import types
import pydantic
import inspect
import functools
import dataclasses
import textwrap
import enum


# ---- core


@dataclasses.dataclass
class PGHint:
    """Additional information helping to generate corresponding 
    to Pydantic constructs appropriate sql commands.

    Attributes:
        type: base sql type for sql cosntruct (eg DOMAIN)
    """

    type: str


type PredicateList[T] = list[tuple[typing.Callable[[T], bool], str]]

def field_validator[T](predicates: PredicateList[T], value: T):
    """Utility that cummulates predicates and raises aggregated errors (if any)

    Thanks to functools.partial can be applieed to pydantic validator in place

    Arguments:
        T: type variable describing annotated origin type
        predicates: list of pairs with predicate function and error message
        value: validated value

    Returns:
        value argument

    Raises:
        ValueError: thrown when one or more predicates are unsuccessfull
    """
    errors: list[str] = [msg for fun, msg in predicates if not fun(value)]
    if errors:
        raise ValueError('\n'.join(errors))
    return value


class BaxModelKind(enum.Enum):
    COMPOSITE = 1


def is_bax_composite(obj: typing.Any):
    return (
        issubclass(obj, pydantic.BaseModel) and 
        getattr(obj, 'bax_model_kind', None) == BaxModelKind.COMPOSITE
    )
            

def is_bax_scalar(obj: typing.Any):
    return (
        typing.get_origin(obj) is typing.Annotated and
        len(obj.__metadata__) >= 3 and
        isinstance(obj.__metadata__[0], pydantic.fields.FieldInfo) and
        isinstance(obj.__metadata__[1], pydantic.AfterValidator) and
        isinstance(obj.__metadata__[2], PGHint)
    )


# ---- discoverers

@dataclasses.dataclass(frozen=True)
class PredicateInfo:
    """Intermediate metadata of predicate.

    Predicate is function from base type to boolean.
    It should be immutable and pure.
    
    Attributes:
        name: name of function (in python and postgres)
        pgtype: postgres base type of corresponding DOMAIN
        doc: function docstring
        body: function body 
    """

    name: str
    pgtype: str
    doc: str
    body: str

    @staticmethod
    def of(f: function, pgtype: str) -> PredicateInfo:
        """Creates PredicateInfo for predicate.

        Arguments:
            f: predicate function
            pgtype: postgres base type of validated value

        Returns:
            new PredicateInfo

        Raises:
            ValueError: raised if no docstring or body
                cannot be discovered from function's source code
        """
        
        doc: str | None = inspect.getdoc(f)
        if not doc:
            raise ValueError(f'Predicate must have docstring in [{f.__name__}]')
        
        body: str = inspect.getsource(typing.cast(typing.Any, f)).split(f'"""{doc}"""\n')[-1].strip('\n')
        if not body:
            raise ValueError(f'No body of predicate found [{f.__name__}]')
        
        return PredicateInfo(name=f.__name__, pgtype=pgtype, doc=doc, body=body)

    @functools.cached_property
    def sql_create_cmd(self) -> str:
        """Returns sql command creating plpython3u stored function for predicate"""

        return '\n'.join([
            f"CREATE OR REPLACE FUNCTION {self.name}(self {self.pgtype})",
            f"RETURNS BOOLEAN AS $plpython$",
            f"{textwrap.indent(self.doc, '    # ')}",
            f"{self.body}",
            f"$plpython$ LANGUAGE plpython3u IMMUTABLE STRICT; ",
            f"--SECURITY DEFINER SET search_path = {'!TODO!'}, pg_temp;",
            f"--REVOKE ALL ON FUNCTION {self.name} FROM public;",
        ])


@dataclasses.dataclass(frozen=True)
class ScalarInfo:
    """Intermediate metadata of scalar.

    Scalar is implemented as Annotated type 
    DOMAIN is corresponding postgres construct.

    Annotation items are positional and must be:
        first  - `pydantic.Field`
        second - `pydantic.AfterValidator`
        third  - `PGHint`

    Attributes:
        name: name of the scalar
        pgtype: postgres base type of DOMAIN
        predicates: list of attached PredicateInfo(s)
    """
    
    name: str
    pgtype: str
    predicates: list[PredicateInfo]

    @staticmethod
    def of(name: str, annotated: typing.Any) -> ScalarInfo:
        """Creates ScalarInfo from annotated type.
        
        Arguments:
            name: the name of the scalar
            annotated: annotated type built according to above rules

        Returns:
            new ScalarInfo    
        """

        validator: pydantic.AfterValidator = annotated.__metadata__[1]
        pghint: PGHint = annotated.__metadata__[2]

        annotated.__metadata__ += (name,)

        return ScalarInfo(name=name, pgtype=pghint.type, predicates=[
            PredicateInfo.of(f, pghint.type)
            for f, __ in typing.cast(functools.partial[typing.Any], validator.func).args[0]
        ])
    
    @functools.cached_property
    def sql_create_cmd(self) -> str:
        """Returns sql command creating DOMAIN for scalar."""
        return f'CREATE DOMAIN {self.name} AS {self.pgtype};'
    
    @functools.cached_property
    def sql_constraints_cmd(self) -> str:
        """Returns sql commands creating check contraints of scalar"""
        return '\n\n'.join([
            "\n".join([
                f"{p.sql_create_cmd}",
                f"",
                f"ALTER DOMAIN {self.name} ADD CONSTRAINT ck__{p.name}",
                f"    CHECK ({p.name}(VALUE));",
            ])
            for p in self.predicates])


@dataclasses.dataclass(frozen=True)
class CompositeInfo:

    name: str
    fields: list[tuple[str, str]]

    @staticmethod
    def of(name: str, cls: typing.Type[pydantic.BaseModel]):
        
        def resolve_attr_type_name(t: typing.Any):
            match t:
                case annotated if typing.get_origin(annotated) is typing.Annotated:
                    return annotated.__metadata__[-1]
                case cls if is_bax_composite(cls):
                    return cls.__name__
                case _:
                    raise ValueError('Only Annotetd or BaseModel-derived types allowed')
        
        return CompositeInfo(name=name, fields=[
            (name, resolve_attr_type_name(cls.__annotations__[name]))
            for name in cls.model_fields
        ])


@dataclasses.dataclass
class ModuleInfo:
    scalars: list[ScalarInfo]
    composites: list[CompositeInfo]

    @staticmethod
    def of(module: types.ModuleType) -> ModuleInfo:

        scalars = [
            ScalarInfo.of(name, obj) 
            for name, obj in inspect.getmembers_static(module)
            if is_bax_scalar(obj)]

        composites = [
            CompositeInfo.of(name, cls)
            for name, cls in inspect.getmembers_static(module, inspect.isclass)
            if is_bax_composite(cls)]
        
        return ModuleInfo(scalars=scalars, composites=composites)
        

    
# predicates: exactly one arg named value, must have unique (in function text) one-line(for now) docstring in """ brackets
# scalars must be indpendent each other and use only distributed with underlaying python features

def valid_customer_symbol_format(value: str) -> bool:
    """only digits letters and - [ ]"""
    from re import fullmatch
    return fullmatch(r'[0-9a-zA-z\-\[\]]{10,20}', value) is not None

def valid_customer_symbol_first_char(value: str) -> bool:
    """first char letter or cipher"""
    return 'a' <= value[0] <= 'z' or 'A' <= value[0] <= 'Z' or '0' <= value[0] <= '9'

CustomerSymbol = typing.Annotated[
    str, 
    pydantic.Field(min_length=10, max_length=20, strict=True),
    pydantic.AfterValidator(functools.partial[str](field_validator, [
        (valid_customer_symbol_format, 'wrong input for customer symbol'), 
        (valid_customer_symbol_first_char, 'customer symbol must start with letter or cipher'),
    ])),
    PGHint(type='VARCHAR(20)'),
]


def valid_street_name(value: str) -> bool:
    """without special characters"""
    from re import fullmatch
    return fullmatch(r'\S{3,200}', value) is not None

StreetName = typing.Annotated[
    str,
    pydantic.Field(min_length=3, max_length=200),
    pydantic.AfterValidator(functools.partial[str](field_validator, [
        (valid_street_name, 'no special characters allowed in street name')
    ])),
    PGHint(type='VARCHAR(200)'),
]


def valid_building_no(value: str) -> bool:
    """without special characters"""
    from re import fullmatch
    return fullmatch(r'\S{,20}', value) is not None

BuildingNo = typing.Annotated[
    str,
    pydantic.Field(max_length=20),
    pydantic.AfterValidator(functools.partial[str](field_validator, [
        (valid_building_no, 'no special characters allowed in building number')
    ])),
    PGHint(type='VARCHAR(20)'),
]


def valid_apartment_no(value: str) -> bool:
    """without special characters"""
    from re import fullmatch
    return fullmatch(r'\S{,20}', value) is not None

ApartmentNo = typing.Annotated[
    str,
    pydantic.Field(max_length=20),
    pydantic.AfterValidator(functools.partial[str](field_validator, [
        (valid_apartment_no, 'no special characters allowed in apartment number')
    ])),
    PGHint(type='VARCHAR(20)'),
]


def valid_zip_code(value: str) -> bool:
    """without special characters"""
    from re import fullmatch
    return fullmatch(r'\S{2,15}', value) is not None

ZipCode = typing.Annotated[
    str,
    pydantic.Field(min_length=2, max_length=15),
    pydantic.AfterValidator(functools.partial[str](field_validator, [
        (valid_zip_code, 'no special characters allowed in zip code')
    ])),
    PGHint(type='VARCHAR(15)'),
]


def valid_city_name(value: str) -> bool:
    """without special characters"""
    from re import fullmatch
    return fullmatch(r'\S{3,100}', value) is not None

CityName = typing.Annotated[
    str,
    pydantic.Field(min_length=3, max_length=100),
    pydantic.AfterValidator(functools.partial[str](field_validator, [
        (valid_city_name, 'no special characters allowed in city name')
    ])),
    PGHint(type='VARCHAR(100)'),
]


def valid_country_code(value: str) -> bool:
    """two uppercase ascci letters"""
    from re import fullmatch
    return fullmatch(r'[A-Z]{2}', value) is not None

CountryCode = typing.Annotated[
    str,
    pydantic.Field(min_length=2, max_length=2),
    pydantic.AfterValidator(functools.partial[str](field_validator, [
        (valid_country_code, 'country code must consists from two uppercase ascii letters')
    ])),
    PGHint(type='CHAR(2)'),
]


def valid_country_name(value: str) -> bool:
    """without special characters"""
    from re import fullmatch
    return fullmatch(r'\S{2,100}', value) is not None

CountryName = typing.Annotated[
    str,
    pydantic.Field(min_length=2, max_length=100),
    pydantic.AfterValidator(functools.partial[str](field_validator, [
        (valid_country_name, 'no special characters in country name')
    ])),
    PGHint(type='CHAR(2)'),
]

class Country(pydantic.BaseModel):

    bax_model_kind: typing.ClassVar[BaxModelKind] = BaxModelKind.COMPOSITE

    code: CountryCode
    name: CountryName


class Address(pydantic.BaseModel):

    bax_model_kind: typing.ClassVar[BaxModelKind] = BaxModelKind.COMPOSITE

    street_name: StreetName
    building_no: BuildingNo
    apartment_no: ApartmentNo
    zip_code: ZipCode
    city_name: CityName
    country: Country


# ---- sql generator

import sys


mi: ModuleInfo = ModuleInfo.of(sys.modules[__name__])


sql = f"""--** generated by customers.py **--

CREATE EXTENSION IF NOT EXISTS plpython3u;

{'\n'.join([f"DROP DOMAIN IF EXISTS {s.name} CASCADE;" for s in mi.scalars])}

{'\n\n'.join([s.sql_create_cmd for s in mi.scalars])}


{'\n\n'.join([s.sql_constraints_cmd for s in mi.scalars if s.sql_constraints_cmd])}
"""

with open(__file__.replace('.py', '-generated.sql'), 'w') as f:
    f.write(sql)
