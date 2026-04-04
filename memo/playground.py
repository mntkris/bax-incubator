import pydantic
import typing
import re


# -------------------------------------------------------------
# on field itself only first validator raises, all are executed
# -------------------------------------------------------------

def scalar_validator_1(value: typing.Any) -> typing.Any:
    print('scalar_validator_1')
    if value == 'error':
        raise ValueError('error_1')
    return value

def scalar_validator_2(value: typing.Any) -> typing.Any:
    print('scalar_validator_2')
    if value == 'error':
        raise ValueError('error_2')
    return value

Scalar_1 = typing.Annotated[str, pydantic.AfterValidator(scalar_validator_1), pydantic.AfterValidator(scalar_validator_2)]

class Composite_1(pydantic.BaseModel):
    f_1: Scalar_1

# x = Composite_1(f_1='222') 
# x = Composite_1(f_1='error') 



# ---------------------------
# predicate as AfterValidator
# ---------------------------

type Predicate[T] = typing.Callable[[T], bool]

class BaxModel(pydantic.BaseModel):
    predicates: typing.ClassVar[list[function]] = []
    
    @classmethod
    def with_predicate(cls, errmsg: str):
        def decorated(predicate: typing.Any):
            cls.predicates.append(predicate)
            def pydantic_after_validator(obj: typing.Any):
                if not predicate(obj):
                    raise ValueError(errmsg)
                return obj
            return pydantic_after_validator
        return decorated

class Composite_2(BaxModel):
    f_1: Scalar_1

    @pydantic.model_validator(mode='after')
    @BaxModel.with_predicate(errmsg='error error')
    def model_predicate_2(self) -> bool:
        """f_1 neq error_2"""
        return self.f_1 != 'error_2'

class Composite_3(BaxModel):
    f_1: Scalar_1

    @pydantic.model_validator(mode='after')
    @BaxModel.with_predicate(errmsg='error error')
    def model_predicate_3(self) -> bool:
        """f_1 neq error_3"""
        return self.f_1 != 'error_3'   


def model_predicates_of(cls: typing.Any) -> list[function]:
    return [
        f for f in cls.predicates
        if f.__qualname__.split('.')[-2] == cls.__name__
    ]

# print(model_predicates_of(Composite_2))
# print(model_predicates_of(Composite_3))


# ----------------------------
# change self.xxxx.{}.zzzz to self['xxxx']{}['zzzz']

def dictionarize(body: str) -> str:
    def repl(match: typing.Match[str]) -> str:
        text = match.group(0)
        parts = text.split('.')[1:]
        return "self" + "".join(f"['{p}']" for p in parts)

    return re.sub(r'\bself(?:\.[A-Za-z_][A-Za-z0-9_]*)+', repl, body)


