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
import pydantic
import inspect
import functools
import dataclasses
import textwrap
import enum
import re


# ---- core



@dataclasses.dataclass
class PGHint:
    """Additional information helping to generate corresponding 
    to Pydantic constructs appropriate sql commands.

    Attributes:
        type: base sql type for sql cosntruct (eg DOMAIN)
    """

    type: str


type Predicate[T] = typing.Callable[[T], bool]
type PredicateList[T] = list[tuple[Predicate[T], str]]

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


class BaxModel(pydantic.BaseModel):
    
    predicates: typing.ClassVar[list[Predicate[typing.Any]]] = []
    
    @classmethod
    def with_predicate[T](cls, errmsg: str):

        def decorated(predicate: Predicate[T]):
            cls.predicates.append(predicate)
        
            def pydantic_after_validator(obj: typing.Any) -> typing.Any:
                if not predicate(obj):
                    raise ValueError(errmsg)
                return obj
        
            return pydantic_after_validator
        
        return decorated


# ---- discoverers

def is_bax_composite(obj: typing.Any):
    return (
        inspect.isclass(obj) and
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


def get_schema_members(
        sch: type,
        predicate: typing.Any
) -> list[tuple[str, typing.Any]]:
    return [(name, obj) for name, obj in sch.__dict__.items() if predicate(obj)]


@dataclasses.dataclass(frozen=True)
class ScalarPredicateInfo:
    """Intermediate metadata of scalar predicate.

    Predicate is function from base type to boolean.
    It should be immutable and pure.
    
    Attributes:
        name: name of function (in python and postgres)
        pgtype: postgres base type of corresponding DOMAIN
        doc: function docstring
        body: function body 
    """

    schema: str
    name: str
    pgtype: str
    doc: str
    body: str

    @staticmethod
    def of(schema: str, f: Predicate[typing.Any], pgtype: str) -> ScalarPredicateInfo:
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
        
        body: str = textwrap.indent(textwrap.dedent(
            inspect.getsource(typing.cast(typing.Any, f)).split(f'"""{doc}"""\n')[-1].strip('\n')
        ), '    ')
        if not body:
            raise ValueError(f'No body of predicate found [{f.__name__}]')
        
        return ScalarPredicateInfo(schema=schema, name=f.__name__, pgtype=pgtype, doc=doc, body=body)

    @functools.cached_property
    def sql_create_cmd(self) -> str:
        """Returns sql command creating plpython3u stored function for predicate"""

        return '\n'.join([
            f"CREATE OR REPLACE FUNCTION {self.schema}.{self.name}(value {self.pgtype})",
            f"RETURNS BOOLEAN AS $plpython$",
            f"{textwrap.indent(self.doc, '    # ')}",
            f"{self.body}",
            f"$plpython$ LANGUAGE plpython3u IMMUTABLE STRICT; ",
        ])


@dataclasses.dataclass(frozen=True)
class CompositePredicateInfo:
    """Intermediate metadata of composite predicate.

    Predicate is function from base type to boolean.
    It should be immutable and pure.
    
    Attributes:
        name: name of function (in python and postgres)
        pgtype: postgres base type of corresponding DOMAIN
        doc: function docstring
        body: function body 
    """

    schema: str
    name: str
    pgtype: str
    doc: str
    body: str

    @staticmethod
    def of(schema: str, f: Predicate[typing.Any], pgtype: str) -> CompositePredicateInfo:
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

        def dictionarize(body: str) -> str:
            def repl(match: typing.Match[str]) -> str:
                parts = match.group(0).split('.')[1:]   # omit self
                return "self" + "".join(f"['{p}']" for p in parts)
            return re.sub(r'\bself(?:\.[A-Za-z_][A-Za-z0-9_]*)+', repl, body)

        doc: str | None = inspect.getdoc(f)
        if not doc:
            raise ValueError(f'Predicate must have docstring in [{f.__name__}]')
        
        body: str = textwrap.indent(textwrap.dedent(
            inspect.getsource(typing.cast(typing.Any, f)).split(f'"""{doc}"""\n')[-1].strip('\n')
        ), '    ')
        if not body:
            raise ValueError(f'No body of predicate found [{f.__name__}]')
        
        return CompositePredicateInfo(schema=schema, name=f.__name__, pgtype=pgtype, doc=doc, body=dictionarize(body))

    @functools.cached_property
    def sql_create_cmd(self) -> str:
        """Returns sql command creating plpython3u stored function for predicate"""

        return '\n'.join([
            f"CREATE OR REPLACE FUNCTION {self.schema}.{self.name}(self {self.schema}.{self.pgtype}_t)",
            f"RETURNS BOOLEAN AS $plpython$",
            f"{textwrap.indent(self.doc, '    # ')}",
            f"{self.body}",
            f"$plpython$ LANGUAGE plpython3u IMMUTABLE STRICT; ",
        ])


type Property[R] = property | functools.cached_property[R]

@dataclasses.dataclass(frozen=True)
class PropertyInfo[R]:

    schame: str
    name: str
    pgtype: str
    rettype: str
    doc: str
    body: str

    @staticmethod
    def of(schema: str, cfi: pydantic.fields.ComputedFieldInfo, name: str, pgtype: str) -> PropertyInfo[R]:
        return PropertyInfo[R](schema, name, pgtype, cfi.return_type.__metadata__[-1], 'doc', 'body')


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
    
    schema: str
    name: str
    pgtype: str
    predicates: list[ScalarPredicateInfo]

    @staticmethod
    def of(schema: str, name: str, annotated: typing.Any) -> ScalarInfo:
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

        return ScalarInfo(schema=schema, name=name, pgtype=pghint.type, predicates=[
            ScalarPredicateInfo.of(schema, f, pghint.type)
            for f, __ in typing.cast(functools.partial[typing.Any], validator.func).args[0]
        ])
    
    @functools.cached_property
    def sql_create_cmd(self) -> str:
        """Returns sql command creating DOMAIN for scalar."""
        return f'CREATE DOMAIN {self.schema}.{self.name} AS {self.pgtype};'
    
    @functools.cached_property
    def sql_constraints_cmd(self) -> str:
        """Returns sql commands creating check contraints of scalar"""
        return '\n\n'.join([
            "\n".join([
                f"{p.sql_create_cmd}",
                f"",
                f"ALTER DOMAIN {self.schema}.{self.name} ADD CONSTRAINT ck__{p.schema}__{p.name}",
                f"    CHECK ({p.schema}.{p.name}(VALUE));",
            ])
            for p in self.predicates])


@dataclasses.dataclass(frozen=True)
class CompositeInfo:

    schema: str
    name: str
    fields: list[tuple[str, str]]
    predicates: list[CompositePredicateInfo]
    properties: list[PropertyInfo[typing.Any]]

    @staticmethod
    def of(schema: str, name: str, cls: typing.Type[BaxModel]):
        
        def resolve_attr_type_name(t: typing.Any):
            match t:
                case annotated if is_bax_scalar(annotated):
                    return annotated.__metadata__[-1]
                case cls if is_bax_composite(cls):
                    return cls.__name__
                case _:
                    raise ValueError('Only Annotetd or BaseModel-derived types allowed')
        
        return CompositeInfo(
            schema=schema,
            name=name, 
            fields=[
                (name, resolve_attr_type_name(cls.__annotations__[name]))
                for name in cls.model_fields],
            predicates=[
                 CompositePredicateInfo.of(schema, func, name)
                 for func in cls.predicates 
                 if func.__qualname__.split('.')[-2] == cls.__name__],
            properties=[])
            #     PropertyInfo[typing.Any].of(cfi=cfi, name=name, pgtype=cls.__name__)
            #     for name, cfi in cls.model_computed_fields.items()
            # ])
    
    
    @functools.cached_property
    def sql_create_cmd(self) -> str:
        attrs: str = ',\n'.join([f"    {a} {self.schema}.{t}" for a, t in self.fields])
        args: str = ', '.join([a for a, __ in self.fields])
        return '\n'.join([
            f"CREATE TYPE {self.schema}.{self.name}_t AS (",
            f"{attrs}",
            f");",
            f"",
            f"CREATE DOMAIN {self.schema}.{self.name} AS {self.schema}.{self.name}_t;",
            f"",
            f"CREATE OR REPLACE FUNCTION {self.schema}.{self.name}(",
            f"{attrs}",
            f") RETURNS {self.schema}.{self.name} AS $SQL$",
            f"    SELECT ROW({args});",
            f"$SQL$ LANGUAGE SQL IMMUTABLE;"
        ])
    
    @functools.cached_property
    def sql_constraints_cmd(self) -> str:
        """Returns sql commands creating check contraints of composite"""
        return '\n\n'.join([
            "\n".join([
                f"{p.sql_create_cmd}",
                f"",
                f"ALTER DOMAIN {p.schema}.{self.name} ADD CONSTRAINT ck__{p.schema}__{p.name}",
                f"    CHECK ({p.schema}.{p.name}(VALUE));",
            ])
            for p in self.predicates])

@dataclasses.dataclass
class SchemaInfo:
    scalars: list[ScalarInfo]
    composites: list[CompositeInfo]

    @staticmethod
    def of(sch: type) -> SchemaInfo:

        scalars = [
            ScalarInfo.of(sch.__name__, name, obj) 
            for name, obj in get_schema_members(sch, is_bax_scalar)]

        composites = [
            CompositeInfo.of(sch.__name__, name, cls)
            for name, cls in get_schema_members(sch, is_bax_composite)]
        
        return SchemaInfo(
            scalars=scalars, 
            composites=composites
        )
        
    
# predicates: exactly one arg named value, must have unique (in function text) one-line(for now) docstring in """ brackets
# scalars must be indpendent each other and use only distributed with underlaying python features

class sch:
    GenericText = typing.Annotated[
        str,
        pydantic.Field(),
        pydantic.AfterValidator(functools.partial[str](field_validator, [])),
        PGHint(type='TEXT'),
    ]


    @staticmethod
    def valid_customer_symbol_format(value: str) -> bool:
        """only digits letters and - [ ]"""
        from re import fullmatch
        return fullmatch(r'[0-9a-zA-z\-\[\]]{10,20}', value) is not None

    @staticmethod
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


    @staticmethod
    def valid_street_name(value: str) -> bool:
        """without special characters"""
        from re import fullmatch
        return fullmatch(r'[ \S]{3,200}', value) is not None

    StreetName = typing.Annotated[
        str,
        pydantic.Field(min_length=3, max_length=200),
        pydantic.AfterValidator(functools.partial[str](field_validator, [
            (valid_street_name, 'no special characters allowed in street name')
        ])),
        PGHint(type='VARCHAR(200)'),
    ]


    @staticmethod
    def valid_building_no(value: str) -> bool:
        """without special characters"""
        from re import fullmatch
        return fullmatch(r'[ \S]{,20}', value) is not None

    BuildingNo = typing.Annotated[
        str,
        pydantic.Field(max_length=20),
        pydantic.AfterValidator(functools.partial[str](field_validator, [
            (valid_building_no, 'no special characters allowed in building number')
        ])),
        PGHint(type='VARCHAR(20)'),
    ]


    @staticmethod
    def valid_apartment_no(value: str) -> bool:
        """without special characters"""
        from re import fullmatch
        return fullmatch(r'[ \S]{,20}', value) is not None

    ApartmentNo = typing.Annotated[
        str,
        pydantic.Field(max_length=20),
        pydantic.AfterValidator(functools.partial[str](field_validator, [
            (valid_apartment_no, 'no special characters allowed in apartment number')
        ])),
        PGHint(type='VARCHAR(20)'),
    ]


    @staticmethod
    def valid_zip_code(value: str) -> bool:
        """without special characters"""
        from re import fullmatch
        return fullmatch(r'[ \S]{2,15}', value) is not None

    ZipCode = typing.Annotated[
        str,
        pydantic.Field(min_length=2, max_length=15),
        pydantic.AfterValidator(functools.partial[str](field_validator, [
            (valid_zip_code, 'no special characters allowed in zip code')
        ])),
        PGHint(type='VARCHAR(15)'),
    ]


    @staticmethod
    def valid_city_name(value: str) -> bool:
        """without special characters"""
        from re import fullmatch
        return fullmatch(r'[ \S]{3,100}', value) is not None

    CityName = typing.Annotated[
        str,
        pydantic.Field(min_length=3, max_length=100),
        pydantic.AfterValidator(functools.partial[str](field_validator, [
            (valid_city_name, 'no special characters allowed in city name')
        ])),
        PGHint(type='VARCHAR(100)'),
    ]


    @staticmethod
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


    @staticmethod
    def valid_country_name(value: str) -> bool:
        """without special characters"""
        from re import fullmatch
        return fullmatch(r'[ \S]{2,100}', value) is not None

    CountryName = typing.Annotated[
        str,
        pydantic.Field(min_length=2, max_length=100),
        pydantic.AfterValidator(functools.partial[str](field_validator, [
            (valid_country_name, 'no special characters in country name')
        ])),
        PGHint(type='VARCHAR(100)'),
    ]


    class Country(BaxModel):

        bax_model_kind: typing.ClassVar[BaxModelKind] = BaxModelKind.COMPOSITE

        code: sch.CountryCode
        name: sch.CountryName

        # @pydantic.model_validator(mode='after')
        # @BaxModel.with_predicate(errmsg='test predicate')
        # def valid_test_pred(self) -> bool:
        #     """test predicate"""
        #     return True


    class Address(BaxModel):

        bax_model_kind: typing.ClassVar[BaxModelKind] = BaxModelKind.COMPOSITE

        street_name: sch.StreetName
        building_no: sch.BuildingNo
        apartment_no: sch.ApartmentNo
        zip_code: sch.ZipCode
        city_name: sch.CityName
        country: sch.Country

        @pydantic.model_validator(mode='after')
        @BaxModel.with_predicate(errmsg='wrong format of zip code')
        def valid_zip_code(self) -> bool:
            """zip code proper format"""
            import re
            return (
                re.fullmatch(r'[0-9]{2}-[0-9]{3}', self.zip_code) is not None
                if self.country.code == 'PL' else True
            )

        @pydantic.computed_field
        @functools.cached_property
        def street_line(self) -> sch.GenericText:
            """street_line"""
            return ' '.join([self.street_name, self.building_no, self.apartment_no])

        @pydantic.computed_field
        @functools.cached_property
        def city_line(self) -> sch.GenericText:
            """city line"""
            return ' '.join([self.zip_code, self.city_name])


# ---- sql generator

si: SchemaInfo = SchemaInfo.of(sch)

# zwykłe property
# print(inspect.getsource(Address.model_computed_fields['street_line'].wrapped_property.fget))
# print(inspect.getsource(Address.model_computed_fields['street_line'].wrapped_property.func))
# print(Address.model_computed_fields.items())

sql = f"""--** generated by customers.py **--

CREATE EXTENSION IF NOT EXISTS plpython3u;

DROP SCHEMA IF EXISTS {sch.__name__} CASCADE;

{'\n'.join([f"DROP DOMAIN IF EXISTS {s.schema}.{s.name} CASCADE;" for s in si.scalars])}
{'\n'.join([f"DROP TYPE IF EXISTS {c.schema}.{c.name}_t CASCADE;" for c in si.composites])}

CREATE SCHEMA IF NOT EXISTS {sch.__name__};

{'\n\n\n'.join([s.sql_create_cmd for s in si.scalars])}


{'\n\n\n'.join([s.sql_constraints_cmd for s in si.scalars if s.sql_constraints_cmd])}


{'\n\n\n'.join([c.sql_create_cmd for c in si.composites])}


{'\n\n\n'.join([c.sql_constraints_cmd for c in si.composites if c.sql_constraints_cmd])}


SELECT sch.Country('PL', 'POLAND');
SELECT sch.Address('Dąb Rozwadowskiego', '6', '5', '00-902', 'Warszawa', sch.Country('PL', 'Polska'));

"""

with open(__file__.replace('.py', '_generated.sql'), 'w') as f:
    f.write(sql)

# ---- mini happy test

c = sch.Country(code='PL', name='Poland')
a = sch.Address(
        street_name='Dąb Rozwadowskiego',
        building_no='6',
        apartment_no='5',
        zip_code='00-999',
        city_name='Warsaw',
        country=c)